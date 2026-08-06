"""
system_log_view.py — Modül 6
Kullanıcı Yönetimi, Log Yöneticisi, Uçuş Log Analizi.
"""
import logging
import math
import time
import collections
from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QTimer, QPointF, QObject
from PyQt6.QtGui import QFont, QPainter, QPen, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTabWidget, QPushButton, QTableWidget, QTableWidgetItem,
    QGroupBox, QFormLayout, QLineEdit, QComboBox,
    QProgressBar, QTextEdit, QHeaderView, QMessageBox,
    QSizePolicy, QSplitter,
)
from ui.auth_manager import hash_password
from core import local_storage

logger = logging.getLogger(__name__)

# ── Basit gerçek zamanlı grafik widget ───────────────────────────────────────
class _MiniChart(QWidget):
    """Son N örneğin çizgi grafiğini çizen hafif widget."""
    def __init__(self, label: str, color: str = "#4ade80",
                 y_min: float = -180, y_max: float = 180,
                 samples: int = 300, parent=None):
        super().__init__(parent)
        self._label  = label
        self._color  = QColor(color)
        self._y_min  = y_min
        self._y_max  = y_max
        self._data: collections.deque = collections.deque(maxlen=samples)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("border-radius:4px;")

    def push(self, value: float) -> None:
        self._data.append(value)
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # Arka plan
        p.fillRect(0, 0, w, h, QColor("#0a0a18"))
        # Grid
        p.setPen(QPen(QColor("#1a1a3e"), 1, Qt.PenStyle.DotLine))
        for yi in range(4):
            y = int(h * yi / 3)
            p.drawLine(0, y, w, y)
        # Sıfır çizgisi (eğer aralıkta)
        if self._y_min < 0 < self._y_max:
            y0 = int(h - h * (0 - self._y_min) / (self._y_max - self._y_min))
            p.setPen(QPen(QColor("#333"), 1))
            p.drawLine(0, y0, w, y0)
        # Veri çizgisi
        if len(self._data) >= 2:
            pts = []
            for i, v in enumerate(self._data):
                x = w * i / (len(self._data) - 1)
                y_norm = (v - self._y_min) / max(self._y_max - self._y_min, 1e-6)
                y_px = h - y_norm * h
                pts.append(QPointF(x, max(0, min(h, y_px))))
            p.setPen(QPen(self._color, 1.5))
            for i in range(len(pts) - 1):
                p.drawLine(pts[i], pts[i + 1])
        # Min/Max/Now gösterimi
        p.setPen(QColor("#888"))
        p.setFont(QFont("Courier New", 7))
        p.drawText(2, 10, self._label)
        if self._data:
            last = self._data[-1]
            p.setPen(self._color)
            p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            p.drawText(w - 60, 10, f"{last:+.2f}")
        p.end()


class SystemLogView(QWidget):
    download_log     = pyqtSignal(int)   # log_id → mavlink_worker
    request_log_list = pyqtSignal()      # log listesini yenile

    def __init__(self, db_conn, parent=None):
        super().__init__(parent)
        self._db_conn = db_conn
        # Analiz tamponu
        self._analysis_active = False
        self._analysis_start: float = 0.0
        self._analysis_events: list = []    # (t, "metin") tüpleri
        self._prev_yaw: float | None = None
        self._prev_alt: float | None = None
        self._prev_roll: float | None = None
        self._prev_pitch: float | None = None
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setInterval(200)
        self._analysis_timer.timeout.connect(self._flush_events)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet("color: #e0e0e0;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        tabs = QTabWidget()
        tabs.addTab(self._user_mgmt_tab(), "Kullanıcı Yönetimi")
        tabs.addTab(self._log_manager_tab(), "MAVLink FTP Log")
        tabs.addTab(self._live_logs_tab(), "Canlı Loglar")
        tabs.addTab(self._flight_analysis_tab(), "Uçuş Analizi")
        layout.addWidget(tabs)

    # ── Canlı Loglar Sekmesi (YKI ve Drone Logları) ───────────────────────────
    def _live_logs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Sol: YKI Logları
        left_w = QWidget()
        left_l = QVBoxLayout(left_w)
        left_l.setContentsMargins(0, 0, 0, 0)
        lbl_yki = QLabel("YKİ Sistem Logları")
        lbl_yki.setStyleSheet("color:#3daee9; font-weight:bold;")
        self._yki_log = QTextEdit()
        self._yki_log.setReadOnly(True)
        # Sınırsız büyümeyi engelle — uzun oturumda bellek/UI şişmesi (en fazla 2000 satır).
        self._yki_log.document().setMaximumBlockCount(2000)
        self._yki_log.setStyleSheet("color:#e0e0e0; font-family:'Courier New'; font-size:11px; border:1px solid #0f3460;")
        left_l.addWidget(lbl_yki)
        left_l.addWidget(self._yki_log)

        # Sağ: Drone Logları
        right_w = QWidget()
        right_l = QVBoxLayout(right_w)
        right_l.setContentsMargins(0, 0, 0, 0)
        lbl_drone = QLabel("Drone / Otopilot Logları")
        lbl_drone.setStyleSheet("color:#4ade80; font-weight:bold;")
        self._drone_log = QTextEdit()
        self._drone_log.setReadOnly(True)
        self._drone_log.document().setMaximumBlockCount(2000)
        self._drone_log.setStyleSheet("color:#e0e0e0; font-family:'Courier New'; font-size:11px; border:1px solid #0f3460;")
        right_l.addWidget(lbl_drone)
        right_l.addWidget(self._drone_log)

        splitter.addWidget(left_w)
        splitter.addWidget(right_w)
        layout.addWidget(splitter)

        # Logging Handler for YKI (C6: THREAD-GÜVENLİ)
        # logging worker thread'lerden (ör. MavlinkWorker QThread) de çağrılır.
        # ÖNCEDEN emit() doğrudan QTextEdit.append yapıyordu → GUI widget'ına
        # GUI-DIŞI thread'den erişim → aralıklı çökme. Artık handler yalnızca bir
        # Qt sinyali yayar; sinyal (thread sınırında otomatik QUEUED bağlantı ile)
        # GUI thread'indeki _append_yki_log slot'unda widget'a yazılır.
        class _LogSignalBridge(QObject):
            message = pyqtSignal(str)

        class QTextEditLogger(logging.Handler):
            def __init__(self, bridge):
                super().__init__()
                self.bridge = bridge
                self.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s', '%H:%M:%S'))

            def emit(self, record):
                try:
                    msg = self.format(record)
                    color = "#aaa"
                    if record.levelno >= logging.ERROR: color = "#ff4444"
                    elif record.levelno >= logging.WARNING: color = "#f0a500"
                    html = f'<span style="color:{color}">{msg}</span>'
                    self.bridge.message.emit(html)   # → GUI thread'e marshal
                except Exception:
                    pass

        self._log_bridge = _LogSignalBridge()
        self._log_bridge.message.connect(self._append_yki_log)
        self._handler = QTextEditLogger(self._log_bridge)
        logging.getLogger().addHandler(self._handler)

        return w

    @pyqtSlot(str)
    def _append_yki_log(self, html: str) -> None:
        """Log satırını GUI thread'inde YKİ log kutusuna ekler (C6)."""
        self._yki_log.append(html)
        sb = self._yki_log.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def closeEvent(self, event):
        """Kapanışta log handler'ı root logger'dan kaldır (sızıntı/çift satır önle)."""
        try:
            if getattr(self, "_handler", None) is not None:
                logging.getLogger().removeHandler(self._handler)
                self._handler = None
        except Exception:
            pass
        super().closeEvent(event)

    # ── Kullanıcı Yönetimi Sekmesi ────────────────────────────────────────────
    def _user_mgmt_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        lbl = QLabel("Kullanıcı Yönetimi  [Yalnızca ADMIN]")
        lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        lbl.setStyleSheet("color: #e94560;")
        layout.addWidget(lbl)

        # Kullanıcı listesi
        self._user_table = QTableWidget(0, 3)
        self._user_table.setHorizontalHeaderLabels(["ID", "Kullanıcı", "Rol"])
        _uh = self._user_table.horizontalHeader()
        if _uh is not None:
            _uh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._user_table.setStyleSheet("""
            QTableWidget {  color: #e0e0e0; gridline-color: #0f3460; }
            QHeaderView::section {  color: #e0e0e0; padding: 4px; }
        """)
        layout.addWidget(self._user_table)

        # Yeni kullanıcı formu
        grp = QGroupBox("Yeni Kullanıcı Ekle")
        grp.setStyleSheet("QGroupBox { border: 1px solid #0f3460; color: #aaa; font-weight: bold; }")
        form = QFormLayout(grp)
        self._new_username = QLineEdit()
        self._new_password = QLineEdit()
        self._new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._new_role_cb = QComboBox()
        self._new_role_cb.addItems(["VIEWER (1)", "OPERATOR (2)", "ADMIN (3)"])

        for w2 in [self._new_username, self._new_password]:
            w2.setStyleSheet(
                " border: 1px solid #0f3460; padding: 5px; border-radius: 3px;"
            )

        form.addRow("Kullanıcı adı:", self._new_username)
        form.addRow("Şifre:", self._new_password)
        form.addRow("Rol:", self._new_role_cb)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Ekle")
        btn_add.setStyleSheet(" color: #000; padding: 7px; border-radius: 3px; font-weight: bold;")
        btn_add.clicked.connect(self._add_user)

        btn_del = QPushButton("Seçili Kullanıcıyı Sil")
        btn_del.setStyleSheet(" color: white; padding: 7px; border-radius: 3px;")
        btn_del.clicked.connect(self._delete_user)

        btn_refresh = QPushButton("Yenile")
        btn_refresh.clicked.connect(self._refresh_users)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        btn_row.addWidget(btn_refresh)

        layout.addWidget(grp)
        layout.addLayout(btn_row)
        self._refresh_users()
        return w

    def _refresh_users(self) -> None:
        users = local_storage.list_users(self._db_conn)
        self._user_table.setRowCount(len(users))
        role_names = {1: "VIEWER", 2: "OPERATOR", 3: "ADMIN"}
        for row, u in enumerate(users):
            self._user_table.setItem(row, 0, QTableWidgetItem(str(u["id"])))
            self._user_table.setItem(row, 1, QTableWidgetItem(u["username"]))
            self._user_table.setItem(row, 2, QTableWidgetItem(role_names.get(u["role"], str(u["role"]))))

    def _add_user(self) -> None:
        username = self._new_username.text().strip()
        password = self._new_password.text()
        role = self._new_role_cb.currentIndex() + 1
        if not username or not password:
            QMessageBox.warning(self, "Uyarı", "Kullanıcı adı ve şifre gerekli.")
            return
        try:
            local_storage.add_user(self._db_conn, username, hash_password(password), role)
            self._new_username.clear()
            self._new_password.clear()
            self._refresh_users()
        except Exception as exc:
            QMessageBox.critical(self, "Hata", str(exc))

    def _delete_user(self) -> None:
        row = self._user_table.currentRow()
        if row < 0:
            return
        _item = self._user_table.item(row, 1)
        username = _item.text() if _item is not None else ""
        if not username:
            return
        reply = QMessageBox.question(
            self, "Onay", f"'{username}' kullanıcısı silinsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            local_storage.delete_user(self._db_conn, username)
            self._refresh_users()

    # ── Log Yöneticisi Sekmesi ─────────────────────────────────────────────────
    def _log_manager_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        lbl = QLabel("MAVLink FTP Log İndirme")
        lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        layout.addWidget(lbl)

        self._log_table = QTableWidget(0, 4)
        self._log_table.setHorizontalHeaderLabels(["ID", "Boyut (B)", "Zaman", "Durum"])
        _lh = self._log_table.horizontalHeader()
        if _lh is not None:
            _lh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._log_table.setStyleSheet("""
            QTableWidget {  color: #e0e0e0; gridline-color: #0f3460; }
            QHeaderView::section {  color: #e0e0e0; padding: 4px; }
        """)
        layout.addWidget(self._log_table)

        self._log_progress = QProgressBar()
        self._log_progress.setRange(0, 100)
        self._log_progress.setValue(0)
        layout.addWidget(self._log_progress)

        btn_row = QHBoxLayout()

        btn_refresh_logs = QPushButton("Log Listesini Yenile")
        btn_refresh_logs.setStyleSheet(" padding: 8px; border-radius: 3px;")
        btn_refresh_logs.clicked.connect(self._on_refresh_log_list)
        btn_row.addWidget(btn_refresh_logs)

        btn_download = QPushButton("Seçili Logu İndir")
        btn_download.setStyleSheet(
            " color: white; padding: 8px; border-radius: 3px; font-weight: bold;"
        )
        btn_download.clicked.connect(self._on_download_log)
        btn_row.addWidget(btn_download)

        layout.addLayout(btn_row)
        layout.addStretch()
        return w

    @pyqtSlot(dict)
    def on_log_entry(self, entry: dict) -> None:
        import datetime
        row = self._log_table.rowCount()
        self._log_table.insertRow(row)
        ts = datetime.datetime.utcfromtimestamp(entry.get("time_utc", 0)).strftime("%Y-%m-%d %H:%M")
        self._log_table.setItem(row, 0, QTableWidgetItem(str(entry.get("id", ""))))
        self._log_table.setItem(row, 1, QTableWidgetItem(str(entry.get("size", ""))))
        self._log_table.setItem(row, 2, QTableWidgetItem(ts))
        self._log_table.setItem(row, 3, QTableWidgetItem("Hazır"))

    @pyqtSlot(int, int)
    def on_download_progress(self, received: int, total: int) -> None:
        pct = int(received * 100 / total) if total > 0 else 0
        self._log_progress.setValue(pct)

    def _on_refresh_log_list(self) -> None:
        self._log_table.setRowCount(0)
        self.request_log_list.emit()

    def _on_download_log(self) -> None:
        row = self._log_table.currentRow()
        if row < 0:
            return
        _id_item = self._log_table.item(row, 0)
        if _id_item is None:
            return
        log_id = int(_id_item.text())
        self.download_log.emit(log_id)
        _st_item = self._log_table.item(row, 3)
        if _st_item is not None:
            _st_item.setText("İndiriliyor...")
        self._log_progress.setValue(0)

    # ── Uçuş Analizi Sekmesi ──────────────────────────────────────────────────
    def _flight_analysis_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)

        # Kontrol çubuğu
        ctrl_row = QHBoxLayout()
        self._analysis_btn = QPushButton("▶ Kaydı Başlat")
        self._analysis_btn.setStyleSheet(
            "color:white;padding:6px 16px;border-radius:4px;font-weight:bold;"
        )
        self._analysis_btn.clicked.connect(self._toggle_analysis)
        self._analysis_status_lbl = QLabel("Kayıt yok")
        self._analysis_status_lbl.setStyleSheet("color:#888;font-size:11px;")
        btn_clear = QPushButton("Temizle")
        btn_clear.setStyleSheet("color:#aaa;padding:6px 12px;border-radius:4px;")
        btn_clear.clicked.connect(self._clear_analysis)
        ctrl_row.addWidget(self._analysis_btn)
        ctrl_row.addWidget(self._analysis_status_lbl)
        ctrl_row.addStretch()
        ctrl_row.addWidget(btn_clear)
        root.addLayout(ctrl_row)

        # Grafikler splitter içinde
        splitter = QSplitter(Qt.Orientation.Vertical)

        # --- Grafikler üst yarı ---
        chart_widget = QWidget()
        chart_lay = QVBoxLayout(chart_widget)
        chart_lay.setSpacing(4)
        chart_lay.setContentsMargins(0, 0, 0, 0)

        self._chart_roll  = _MiniChart("Roll (°)",    "#e94560", -60,  60)
        self._chart_pitch = _MiniChart("Pitch (°)",   "#ffb347", -60,  60)
        self._chart_yaw   = _MiniChart("Yaw (°)",     "#00e5ff", -180, 180)
        self._chart_alt   = _MiniChart("İrtifa (m)",  "#4ade80",   0,  200)
        self._chart_spd   = _MiniChart("Hız (m/s)",   "#bb86fc",   0,   30)
        self._chart_bat   = _MiniChart("Batarya (%)", "#ffff00",   0,  100)

        for c in [self._chart_roll, self._chart_pitch, self._chart_yaw,
                  self._chart_alt, self._chart_spd, self._chart_bat]:
            chart_lay.addWidget(c)
        splitter.addWidget(chart_widget)

        # --- Olay günlüğü alt yarı ---
        evt_widget = QWidget()
        evt_lay = QVBoxLayout(evt_widget)
        evt_lay.setContentsMargins(0, 0, 0, 0)
        evt_hdr = QLabel("Olaylar / Anormallikler")
        evt_hdr.setStyleSheet("color:#4ade80;font-weight:bold;font-size:10px;padding:2px;")
        evt_lay.addWidget(evt_hdr)
        self._analysis_log = QTextEdit()
        self._analysis_log.setReadOnly(True)
        self._analysis_log.setStyleSheet(
            "color:#e0e0e0;font-family:'Courier New';"
            "font-size:11px;border:1px solid #0f3460;"
        )
        evt_lay.addWidget(self._analysis_log)
        splitter.addWidget(evt_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        # Özet satırı
        self._summary_lbl = QLabel("Özet: —")
        self._summary_lbl.setStyleSheet(
            "color:#aaa;padding:4px;font-size:10px;"
            "border:1px solid #0f3460;border-radius:3px;"
        )
        root.addWidget(self._summary_lbl)
        return w

    def _toggle_analysis(self) -> None:
        if not self._analysis_active:
            self._analysis_active = True
            self._analysis_start = time.time()
            self._analysis_events.clear()
            self._prev_yaw = None
            self._prev_alt = None
            self._prev_roll = None
            self._prev_pitch = None
            self._analysis_btn.setText("■ Kaydı Durdur")
            self._analysis_btn.setStyleSheet(
                "color:white;padding:6px 16px;border-radius:4px;font-weight:bold;"
            )
            self._analysis_status_lbl.setText("Kayıt aktif")
            self._analysis_timer.start()
        else:
            self._analysis_active = False
            self._analysis_btn.setText("▶ Kaydı Başlat")
            self._analysis_btn.setStyleSheet(
                "color:white;padding:6px 16px;border-radius:4px;font-weight:bold;"
            )
            elapsed = time.time() - self._analysis_start
            self._analysis_status_lbl.setText(f"Son kayıt: {elapsed:.0f}s")
            self._analysis_timer.stop()
            self._update_summary()

    def _clear_analysis(self) -> None:
        self._analysis_log.clear()
        self._summary_lbl.setText("Özet: —")
        for c in [self._chart_roll, self._chart_pitch, self._chart_yaw,
                  self._chart_alt, self._chart_spd, self._chart_bat]:
            c._data.clear()
            c.update()

    def _flush_events(self) -> None:
        for ev in self._analysis_events:
            self._analysis_log.append(ev)
        self._analysis_events.clear()
        sb = self._analysis_log.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _update_summary(self) -> None:
        # Kısa özet: grafiklerden min/max hesapla
        def stats(dq):
            if not dq:
                return "—"
            return f"min={min(dq):.1f} max={max(dq):.1f} ort={sum(dq)/len(dq):.1f}"
        self._summary_lbl.setText(
            f"Roll: {stats(self._chart_roll._data)}  |  "
            f"Pitch: {stats(self._chart_pitch._data)}  |  "
            f"Yaw: {stats(self._chart_yaw._data)}  |  "
            f"İrtifa: {stats(self._chart_alt._data)}"
        )

    # ── Telemetri slot'u (analiz güncellemesi) ────────────────────────────────
    @pyqtSlot(dict)
    def update_telemetry_for_analysis(self, data: dict) -> None:
        """Sadece analiz sekmesi aktifken çağrılır."""
        if not self._analysis_active:
            return
        t = time.time() - self._analysis_start

        roll_deg  = math.degrees(float(data.get("roll",  0.0)))
        pitch_deg = math.degrees(float(data.get("pitch", 0.0)))
        yaw_deg   = math.degrees(float(data.get("yaw",   0.0)))
        alt       = float(data.get("alt", 0.0))
        spd       = float(data.get("groundspeed", 0.0))
        bat       = float(data.get("battery_remaining", 0))

        # Grafiklere veri ekle
        self._chart_roll.push(roll_deg)
        self._chart_pitch.push(pitch_deg)
        self._chart_yaw.push(yaw_deg)
        self._chart_alt.push(alt)
        self._chart_spd.push(spd)
        self._chart_bat.push(bat)

        # Anormallik tespiti
        _WARN  = '<span style="color:#ff8800">'
        _ERR   = '<span style="color:#e94560">'
        _INFO  = '<span style="color:#4ade80">'
        _CLOSE = '</span>'

        # Yaw hız değişimi
        if self._prev_yaw is not None:
            delta_yaw = abs(yaw_deg - self._prev_yaw)
            if delta_yaw > 180:
                delta_yaw = 360 - delta_yaw
            if delta_yaw > 15:
                self._analysis_events.append(
                    f"{_WARN}[{t:.1f}s] Ani Yaw değişimi: Δ{delta_yaw:.1f}°{_CLOSE}"
                )
        self._prev_yaw = yaw_deg

        # İrtifa ani değişimi
        if self._prev_alt is not None:
            delta_alt = abs(alt - self._prev_alt)
            if delta_alt > 3.0:
                self._analysis_events.append(
                    f"{_WARN}[{t:.1f}s] Ani irtifa değişimi: Δ{delta_alt:.1f}m{_CLOSE}"
                )
        self._prev_alt = alt

        # Aşırı roll/pitch
        if abs(roll_deg) > 45:
            self._analysis_events.append(
                f"{_ERR}[{t:.1f}s] AŞIRI ROLL: {roll_deg:.1f}°{_CLOSE}"
            )
        if abs(pitch_deg) > 45:
            self._analysis_events.append(
                f"{_ERR}[{t:.1f}s] AŞIRI PİTCH: {pitch_deg:.1f}°{_CLOSE}"
            )

        # Düşük batarya
        if 0 < bat <= 20:
            self._analysis_events.append(
                f"{_ERR}[{t:.1f}s] KRİTİK BATARYA: %{bat:.0f}{_CLOSE}"
            )
        elif 20 < bat <= 30:
            self._analysis_events.append(
                f"{_WARN}[{t:.1f}s] Düşük batarya: %{bat:.0f}{_CLOSE}"
            )

    # ── Drone STATUSTEXT mesajları ──────────────────────────────────────────────
    _SEV_COLORS = {
        0: "#e94560",  # EMERGENCY
        1: "#e94560",  # ALERT
        2: "#e94560",  # CRITICAL
        3: "#ff8800",  # ERROR
        4: "#f0a500",  # WARNING
        5: "#4ade80",  # NOTICE
        6: "#aaa",     # INFO
        7: "#888",     # DEBUG
    }

    @pyqtSlot(str, int)
    def on_status_text(self, text: str, severity: int) -> None:
        """Drone'dan gelen STATUSTEXT mesajlarını log paneline yazar."""
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        color = self._SEV_COLORS.get(severity, "#aaa")
        _sev_names = ["EMRG","ALRT","CRIT","ERR","WARN","NOTE","INFO","DBG"]
        sev_name = _sev_names[severity] if 0 <= severity < len(_sev_names) else str(severity)
        msg = f'<span style="color:{color}">[{ts}] [{sev_name}] {text}</span>'
        if hasattr(self, "_drone_log"):
            self._drone_log.append(msg)
            sb = self._drone_log.verticalScrollBar()
            if sb is not None:
                sb.setValue(sb.maximum())
        # Eski uçuş analizi loguna da ekleyebiliriz (opsiyonel)
        if hasattr(self, "_analysis_log"):
            self._analysis_log.append(msg)
            sb2 = self._analysis_log.verticalScrollBar()
            if sb2 is not None:
                sb2.setValue(sb2.maximum())
