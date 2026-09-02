"""
system_log_view.py — Modül 6
Kullanıcı Yönetimi, Log Yöneticisi, Uçuş Log Analizi.
"""
import logging
import math
import time
import collections
from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QTimer, QPointF, QObject
from PyQt6.QtGui import QFont, QFontMetrics, QPainter, QPen, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTabWidget, QPushButton, QTableWidget, QTableWidgetItem,
    QGroupBox, QFormLayout, QLineEdit, QComboBox,
    QProgressBar, QTextEdit, QHeaderView, QMessageBox,
    QSizePolicy, QSplitter,
)
from ui.auth_manager import hash_password
from core import local_storage, theme
from core import batarya
from core.global_state import state

logger = logging.getLogger(__name__)

# NEDEN (C4 — KRİTİK): "Kullanıcı Yönetimi [Yalnızca ADMIN]" başlığı bir söz
# veriyordu ama kod hiçbir yerde rol kontrolü yapmıyordu. Sekme yanlışlıkla
# açık kalırsa (ya da ileride başka bir yerden çağrılırsa) VIEWER/OPERATOR
# kendine ADMIN hesabı açıp tüm yetkileri ele geçirebiliyordu. Rol kontrolü
# artık işlemin KENDİSİNDE — sekme görünürlüğüne güvenilmez (defense in depth).
ROLE_ADMIN = 3

# ── Basit gerçek zamanlı grafik widget ───────────────────────────────────────
class _MiniChart(QWidget):
    """Son N örneğin çizgi grafiğini çizen hafif widget.

    `color` parametresi artık HAM HEX DEĞİL bir TEMA TOKEN ADI'dır
    (ör. "danger", "info", "accent"). Renk paint sırasında theme.c(...) ile
    okunur — __init__'te cachelenmez — böylece tema değişince grafik de
    doğru renklerle yeniden çizilir.
    """
    def __init__(self, label: str, color: str = "accent",
                 y_min: float = -180, y_max: float = 180,
                 samples: int = 300, parent=None):
        super().__init__(parent)
        self._label  = label
        self._color  = color          # tema token adı
        self._y_min  = y_min
        self._y_max  = y_max
        self._data: collections.deque = collections.deque(maxlen=samples)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _apply_theme_colors(self) -> None:
        """Tema değişimi: renkler paint anında okunduğu için yeniden çizmek yeter."""
        self.update()

    def push(self, value: float) -> None:
        self._data.append(value)
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # Renkler paint ANINDA okunur (tema değişimine duyarlı)
        series_col = QColor(theme.c(self._color))
        # Arka plan
        p.fillRect(0, 0, w, h, QColor(theme.c("chart_bg")))
        # Izgara
        p.setPen(QPen(QColor(theme.c("grid")), 1, Qt.PenStyle.DotLine))
        for yi in range(4):
            y = int(h * yi / 3)
            p.drawLine(0, y, w, y)
        # Sıfır çizgisi (eğer aralıkta)
        if self._y_min < 0 < self._y_max:
            y0 = int(h - h * (0 - self._y_min) / (self._y_max - self._y_min))
            p.setPen(QPen(QColor(theme.c("border_strong")), 1))
            p.drawLine(0, y0, w, y0)
        # Veri çizgisi
        if len(self._data) >= 2:
            pts = []
            for i, v in enumerate(self._data):
                x = w * i / (len(self._data) - 1)
                y_norm = (v - self._y_min) / max(self._y_max - self._y_min, 1e-6)
                y_px = h - y_norm * h
                pts.append(QPointF(x, max(0, min(h, y_px))))
            p.setPen(QPen(series_col, 1.5))
            for i in range(len(pts) - 1):
                p.drawLine(pts[i], pts[i + 1])
        # Etiket ve son değer
        p.setPen(QColor(theme.c("chart_fg")))
        p.setFont(QFont("Courier New", 8))
        p.drawText(2, 11, self._label)
        if self._data:
            last = self._data[-1]
            p.setPen(series_col)
            val_font = QFont("Courier New", 9, QFont.Weight.Bold)
            p.setFont(val_font)
            # Sabit x offset yerine ÖLÇÜLEN genişlik — uzun değer kırpılmaz.
            txt = f"{last:+.2f}"
            tw = QFontMetrics(val_font).horizontalAdvance(txt)
            p.drawText(max(2, w - tw - 4), 11, txt)
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
        # NEDEN (M9): Olaylar EDGE-TRIGGERED üretilir — durum değiştiğinde BİR
        # KEZ. Eskiden batarya/roll/pitch eşiği aşıldığı sürece her telemetri
        # karesinde (~15 Hz) satır ekleniyordu; gerçek olaylar bu tekrarların
        # arasında kayboluyor ve log sınırsız büyüyordu.
        self._bat_level: str = ""        # "" | "dusuk" | "kritik"
        self._roll_exceeded: bool = False
        self._pitch_exceeded: bool = False
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setInterval(200)
        self._analysis_timer.timeout.connect(self._flush_events)
        self._build_ui()
        # Tema değişince kurulumda yazılan renkler bayat kalmasın (C kuralı).
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _apply_theme_colors(self) -> None:
        """Tema değişiminde çalışma-anı renklerini yeniden uygular.

        NOT: Log kutularındaki GEÇMİŞ satırlar yazıldıkları anın renklerini
        korur (kayıt niteliğindedir, geriye dönük boyanmaz); yeni satırlar
        aktif temanın token'larıyla yazılır.
        """
        # Kayıt butonu (durum → QSS class'ı) ve grafikler tazelenir.
        self._set_analysis_btn_state(self._analysis_active)
        for ch in (self._chart_roll, self._chart_pitch, self._chart_yaw,
                   self._chart_alt, self._chart_spd, self._chart_bat):
            ch.update()

    def _build_ui(self) -> None:
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
        lbl_yki.setObjectName("sectionTitle")
        self._yki_log = QTextEdit()
        self._yki_log.setReadOnly(True)
        # Sınırsız büyümeyi engelle — uzun oturumda bellek/UI şişmesi (en fazla 2000 satır).
        self._yki_log.document().setMaximumBlockCount(2000)
        self._yki_log.setStyleSheet("QTextEdit{font-family:'Courier New'; font-size:11px;}")
        left_l.addWidget(lbl_yki)
        left_l.addWidget(self._yki_log)

        # Sağ: Drone Logları
        right_w = QWidget()
        right_l = QVBoxLayout(right_w)
        right_l.setContentsMargins(0, 0, 0, 0)
        lbl_drone = QLabel("Drone / Otopilot Logları")
        lbl_drone.setObjectName("sectionTitle")
        self._drone_log = QTextEdit()
        self._drone_log.setReadOnly(True)
        self._drone_log.document().setMaximumBlockCount(2000)
        self._drone_log.setStyleSheet("QTextEdit{font-family:'Courier New'; font-size:11px;}")
        right_l.addWidget(lbl_drone)
        right_l.addWidget(self._drone_log)

        splitter.addWidget(left_w)
        splitter.addWidget(right_w)
        splitter.setSizes([600, 600])
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
                    # Renk aktif temadan okunur (ham hex YOK).
                    color = theme.c("text_muted")
                    if record.levelno >= logging.ERROR: color = theme.c("danger")
                    elif record.levelno >= logging.WARNING: color = theme.c("warning")
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
        lbl.setObjectName("dangerText")
        layout.addWidget(lbl)

        # Kullanıcı listesi — dikey stretch tabloda kalır, formda değil.
        self._user_table = QTableWidget(0, 3)
        self._user_table.setHorizontalHeaderLabels(["ID", "Kullanıcı", "Rol"])
        _uh = self._user_table.horizontalHeader()
        if _uh is not None:
            _uh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._user_table, stretch=1)

        # Yeni kullanıcı formu
        grp = QGroupBox("Yeni Kullanıcı Ekle")
        grp.setMaximumWidth(520)
        form = QFormLayout(grp)
        form.setVerticalSpacing(6)
        self._new_username = QLineEdit()
        self._new_username.setMaximumWidth(300)
        self._new_password = QLineEdit()
        self._new_password.setMaximumWidth(300)
        self._new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._new_role_cb = QComboBox()
        self._new_role_cb.setMaximumWidth(200)
        self._new_role_cb.addItems(["VIEWER (1)", "OPERATOR (2)", "ADMIN (3)"])

        form.addRow("Kullanıcı adı:", self._new_username)
        form.addRow("Şifre:", self._new_password)
        form.addRow("Rol:", self._new_role_cb)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Ekle")
        btn_add.setObjectName("primaryButton")
        btn_add.clicked.connect(self._add_user)

        btn_del = QPushButton("Seçili Kullanıcıyı Sil")
        btn_del.setObjectName("dangerButton")
        btn_del.clicked.connect(self._delete_user)

        btn_refresh = QPushButton("Yenile")
        btn_refresh.clicked.connect(self._refresh_users)
        for b in (btn_add, btn_del, btn_refresh):
            b.setMinimumHeight(28)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch()

        layout.addWidget(grp, alignment=Qt.AlignmentFlag.AlignLeft)
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

    def _require_admin(self, islem: str) -> bool:
        """Kullanıcı yönetimi işlemleri için ADMIN rolü zorunlu (C4).

        NEDEN: Rol kontrolü UI görünürlüğünde değil, işlemin kendisinde
        yapılmalıdır; aksi halde tek bir sekme/RBAC hatası yetki yükseltmeye
        (privilege escalation) dönüşür.
        """
        role = state.get_rbac_role()
        if role < ROLE_ADMIN:
            QMessageBox.warning(
                self, "Yetki Yok",
                f"{islem} işlemi yalnızca ADMIN yetkisine sahip kullanıcılar "
                f"tarafından yapılabilir.\n\nMevcut rol seviyeniz: {role}",
            )
            logger.warning("Yetkisiz kullanıcı yönetimi denemesi: %s (rol=%s)", islem, role)
            try:
                local_storage.write_audit(
                    self._db_conn, "WARNING",
                    f"Yetkisiz kullanıcı yönetimi denemesi: {islem} (rol={role})",
                )
            except Exception:
                pass
            return False
        return True

    def _add_user(self) -> None:
        # C4: ADMIN değilse hiçbir şey yapma.
        if not self._require_admin("Kullanıcı ekleme"):
            return
        username = self._new_username.text().strip()
        password = self._new_password.text()
        role = self._new_role_cb.currentIndex() + 1
        if not username or not password:
            QMessageBox.warning(self, "Uyarı", "Kullanıcı adı ve şifre gerekli.")
            return
        if len(password) < 8:
            QMessageBox.warning(self, "Uyarı", "Şifre en az 8 karakter olmalı.")
            return
        try:
            local_storage.add_user(self._db_conn, username, hash_password(password), role)
            self._new_username.clear()
            self._new_password.clear()
            self._refresh_users()
        except Exception as exc:
            QMessageBox.critical(self, "Hata", str(exc))

    def _delete_user(self) -> None:
        # C4: ADMIN değilse hiçbir şey yapma.
        if not self._require_admin("Kullanıcı silme"):
            return
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
        lbl.setObjectName("sectionTitle")
        layout.addWidget(lbl)

        self._log_table = QTableWidget(0, 4)
        self._log_table.setHorizontalHeaderLabels(["ID", "Boyut (B)", "Zaman", "Durum"])
        _lh = self._log_table.horizontalHeader()
        if _lh is not None:
            _lh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._log_table)

        self._log_progress = QProgressBar()
        self._log_progress.setRange(0, 100)
        self._log_progress.setValue(0)
        layout.addWidget(self._log_progress)

        btn_row = QHBoxLayout()

        btn_refresh_logs = QPushButton("Log Listesini Yenile")
        btn_refresh_logs.clicked.connect(self._on_refresh_log_list)

        btn_download = QPushButton("Seçili Logu İndir")
        btn_download.setObjectName("primaryButton")   # stil QSS'ten
        btn_download.clicked.connect(self._on_download_log)
        for b in (btn_refresh_logs, btn_download):
            b.setMinimumHeight(28)
        btn_row.addWidget(btn_refresh_logs)
        btn_row.addWidget(btn_download)
        btn_row.addStretch()

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
        self._analysis_btn.setMinimumHeight(28)
        self._set_analysis_btn_state(False)   # renk QSS class'ından
        self._analysis_btn.clicked.connect(self._toggle_analysis)
        self._analysis_status_lbl = QLabel("Kayıt yok")
        self._analysis_status_lbl.setObjectName("mutedLabel")
        btn_clear = QPushButton("Temizle")
        btn_clear.setMinimumHeight(28)
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

        # Seri renkleri TOKEN adıyla verilir; her grafik ayrı widget olduğu için
        # aynı token'ın tekrarı bir soruna yol açmaz, önemli olan her serinin
        # iki temada da okunur bir renkle çizilmesi.
        self._chart_roll  = _MiniChart("Roll (°)",    "danger",     -60,  60)
        self._chart_pitch = _MiniChart("Pitch (°)",   "warning",    -60,  60)
        self._chart_yaw   = _MiniChart("Yaw (°)",     "info",       -180, 180)
        self._chart_alt   = _MiniChart("İrtifa (m)",  "success",      0,  200)
        self._chart_spd   = _MiniChart("Hız (m/s)",   "accent",       0,   30)
        self._chart_bat   = _MiniChart("Batarya (%)", "warning_fg",   0,  100)

        for c in [self._chart_roll, self._chart_pitch, self._chart_yaw,
                  self._chart_alt, self._chart_spd, self._chart_bat]:
            chart_lay.addWidget(c)
        splitter.addWidget(chart_widget)

        # --- Olay günlüğü alt yarı ---
        evt_widget = QWidget()
        evt_lay = QVBoxLayout(evt_widget)
        evt_lay.setContentsMargins(0, 0, 0, 0)
        evt_hdr = QLabel("Olaylar / Anormallikler")
        evt_hdr.setObjectName("sectionTitle")
        evt_lay.addWidget(evt_hdr)
        self._analysis_log = QTextEdit()
        self._analysis_log.setReadOnly(True)
        # NEDEN (M9): Bu kutuda blok sınırı YOKTU. Telemetri ~15 Hz geldiği için
        # düşük batarya gibi kalıcı bir durumda saniyede ~15 satır ekleniyor,
        # uzun uçuşta on binlerce blok birikip bellek ve UI'yi şişiriyordu.
        # Diğer log kutularıyla aynı sınır (2000 blok) uygulanır.
        self._analysis_log.document().setMaximumBlockCount(2000)
        self._analysis_log.setStyleSheet("QTextEdit{font-family:'Courier New'; font-size:11px;}")
        evt_lay.addWidget(self._analysis_log)
        splitter.addWidget(evt_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        # Özet satırı
        self._summary_lbl = QLabel("Özet: —")
        self._summary_lbl.setObjectName("hintLabel")   # soluk metin + kutu QSS'ten
        root.addWidget(self._summary_lbl)
        return w

    def _set_analysis_btn_state(self, recording: bool) -> None:
        """Kayıt butonunun rengini QSS class'ı ile verir (inline hex YOK).

        Kayıt sürerken 'dangerButton' (durdur), beklerken 'successButton'
        (başlat) görünümü kullanılır; class değişimi unpolish/polish ile
        yeniden uygulanır.
        """
        self._analysis_btn.setObjectName("dangerButton" if recording else "successButton")
        st = self._analysis_btn.style()
        if st is not None:
            st.unpolish(self._analysis_btn)
            st.polish(self._analysis_btn)

    def _toggle_analysis(self) -> None:
        if not self._analysis_active:
            self._analysis_active = True
            self._analysis_start = time.time()
            self._analysis_events.clear()
            self._prev_yaw = None
            self._prev_alt = None
            self._prev_roll = None
            self._prev_pitch = None
            # M9: Yeni kayıtta edge durumları sıfırlanır ki ilk aşım tekrar loglansın.
            self._bat_level = ""
            self._roll_exceeded = False
            self._pitch_exceeded = False
            self._analysis_btn.setText("■ Kaydı Durdur")
            self._set_analysis_btn_state(True)
            self._analysis_status_lbl.setText("Kayıt aktif")
            self._analysis_timer.start()
        else:
            self._analysis_active = False
            self._analysis_btn.setText("▶ Kaydı Başlat")
            self._set_analysis_btn_state(False)
            elapsed = time.time() - self._analysis_start
            self._analysis_status_lbl.setText(f"Son kayıt: {elapsed:.0f}s")
            self._analysis_timer.stop()
            self._update_summary()

    def _clear_analysis(self) -> None:
        self._analysis_log.clear()
        # M9: Log temizlenince edge durumları da sıfırlanır — aksi halde
        # devam eden bir anormallik bir daha hiç loglanmazdı.
        self._bat_level = ""
        self._roll_exceeded = False
        self._pitch_exceeded = False
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
        # ── PİL YÜZDESİ — GERİLİM TABANLI (core/batarya.py, tek kaynak) ────
        # NEDEN değişti: eskiden FC'nin `battery_remaining` (mAh sayımı)
        # alanı okunuyordu ve alan yoksa 0.0 varsayılıyordu — yani veri
        # yokluğu "pil %0" olarak GRAFİĞE ÇİZİLİYOR ve aşağıdaki uyarı
        # mantığına giriyordu. Kullanıcı kararı (2026-09-02) yüzdeyi
        # gerilimden türetir (25.2 V=%100, 21.0 V=%0); bilinmiyorsa None.
        bat = batarya.yuzde_telemetriden(data)

        # Grafiklere veri ekle
        self._chart_roll.push(roll_deg)
        self._chart_pitch.push(pitch_deg)
        self._chart_yaw.push(yaw_deg)
        self._chart_alt.push(alt)
        self._chart_spd.push(spd)
        # NEDEN koşullu: yüzde bilinmiyorsa grafiğe SAHTE 0 basmak, düz bir
        # "pil boş" çizgisi üretir ve sonradan logu okuyan bunu gerçek bir
        # boşalma sanır. Bilinmeyen örnek hiç eklenmez (grafik o an durur).
        if bat is not None:
            self._chart_bat.push(float(bat))

        # Anormallik tespiti — satır renkleri aktif temadan (ham hex YOK)
        _WARN  = f'<span style="color:{theme.c("warning")}">'
        _ERR   = f'<span style="color:{theme.c("danger")}">'
        _INFO  = f'<span style="color:{theme.c("success")}">'
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

        # Aşırı roll/pitch — EDGE-TRIGGERED (M9)
        # NEDEN: Eşik aşıldığı SÜRECE her karede (~15 Hz) satır ekleniyordu.
        # Artık yalnızca eşiğe GİRİŞTE ve eşikten ÇIKIŞTA birer satır yazılır;
        # 5° histerezis, tam sınırda titreşen değerin log'u doldurmasını önler.
        if abs(roll_deg) > 45 and not self._roll_exceeded:
            self._roll_exceeded = True
            self._analysis_events.append(
                f"{_ERR}[{t:.1f}s] AŞIRI ROLL: {roll_deg:.1f}°{_CLOSE}"
            )
        elif abs(roll_deg) < 40 and self._roll_exceeded:
            self._roll_exceeded = False
            self._analysis_events.append(
                f"{_INFO}[{t:.1f}s] Roll normale döndü: {roll_deg:.1f}°{_CLOSE}"
            )

        if abs(pitch_deg) > 45 and not self._pitch_exceeded:
            self._pitch_exceeded = True
            self._analysis_events.append(
                f"{_ERR}[{t:.1f}s] AŞIRI PİTCH: {pitch_deg:.1f}°{_CLOSE}"
            )
        elif abs(pitch_deg) < 40 and self._pitch_exceeded:
            self._pitch_exceeded = False
            self._analysis_events.append(
                f"{_INFO}[{t:.1f}s] Pitch normale döndü: {pitch_deg:.1f}°{_CLOSE}"
            )

        # Düşük batarya — EDGE-TRIGGERED (M9)
        # NEDEN: Asıl kaçak buradaydı — batarya %30'un altına indiğinde uçuşun
        # KALANI boyunca her telemetri karesinde satır ekleniyordu. Artık seviye
        # DEĞİŞTİĞİNDE tek satır yazılır.
        # NEDEN eşikler core/batarya.py'den: renk/uyarı sınırları ile bu
        # analiz uyarıları AYNI ölçeğe oturmalı. Yeni ölçekte
        # %20 ≈ 21.84 V (3.64 V/hücre) — paketin dik iniş bölgesi;
        # "düşük" için ise kritik eşiğin 10 puan üstü kullanılır (≈22.26 V),
        # eski %30 sabiti gerilim tabanlı ölçekte bir şeye karşılık gelmiyordu.
        _KRITIK = batarya.RENK_KRITIK_YUZDE          # 20
        _DUSUK  = batarya.RENK_KRITIK_YUZDE + 10     # 30
        if bat is None:
            new_level = ""          # veri yok → uyarı ÜRETME (%0 sanma)
        elif bat <= _KRITIK:
            new_level = "kritik"
        elif bat <= _DUSUK:
            new_level = "dusuk"
        else:
            new_level = "normal"

        if new_level and new_level != self._bat_level:
            if new_level == "kritik":
                self._analysis_events.append(
                    f"{_ERR}[{t:.1f}s] KRİTİK BATARYA: %{bat:.0f}{_CLOSE}"
                )
            elif new_level == "dusuk":
                self._analysis_events.append(
                    f"{_WARN}[{t:.1f}s] Düşük batarya: %{bat:.0f}{_CLOSE}"
                )
            elif self._bat_level in ("dusuk", "kritik"):
                self._analysis_events.append(
                    f"{_INFO}[{t:.1f}s] Batarya normale döndü: %{bat:.0f}{_CLOSE}"
                )
            self._bat_level = new_level

    # ── Drone STATUSTEXT mesajları ──────────────────────────────────────────────
    # Değerler HAM HEX DEĞİL tema token adıdır; renk yazma anında theme.c ile
    # çözülür, böylece iki temada da okunur kalır.
    _SEV_COLORS = {
        0: "danger",        # EMERGENCY
        1: "danger",        # ALERT
        2: "danger",        # CRITICAL
        3: "danger",        # ERROR
        4: "warning",       # WARNING
        5: "success",       # NOTICE
        6: "text_muted",    # INFO
        7: "text_disabled", # DEBUG
    }

    @pyqtSlot(str, int)
    def on_status_text(self, text: str, severity: int) -> None:
        """Drone'dan gelen STATUSTEXT mesajlarını log paneline yazar."""
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        color = theme.c(self._SEV_COLORS.get(severity, "text_muted"))
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
