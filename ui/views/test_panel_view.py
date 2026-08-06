"""
test_panel_view.py — Uçuş Öncesi Test Paneli

İçerik:
  • Aviyonik sağlık göstergeleri (GPS, EKF, Batarya, RC, Silahlama)
  • Uçuş öncesi otomatik kontrol listesi
  • 6 test süreci (Kalkış, İniş, RC Failsafe, Telemetri Failsafe, Menzil, Hover)
  • Test logu + rapor kaydı (SQLite & dosya)
"""

import json
import time
import logging

from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QTimer, QElapsedTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QPushButton, QLabel,
    QGroupBox, QTextEdit, QScrollArea, QFrame, QMessageBox,
    QFileDialog, QSizePolicy,
)

from core import local_storage

logger = logging.getLogger(__name__)

# ── Stil ──────────────────────────────────────────────────────────────────────
_SS = ("QGroupBox{border:1px solid #0f3460;color:#aaa;font-weight:bold;"
       "padding-top:18px;margin-top:6px;border-radius:4px;}")
_SB = "QPushButton{color:#00e5ff;border-radius:4px;padding:6px 10px;font-weight:bold;}"
_SBG = ("QPushButton{color:#4ade80;border-radius:4px;padding:7px 12px;font-weight:bold;}"
        "QPushButton:hover{}"
        "QPushButton:disabled{color:#555;}")
_SBR = "QPushButton{color:#ff6666;border-radius:4px;padding:7px 12px;font-weight:bold;}"
_SBW = "QPushButton{color:#f0a500;border-radius:4px;padding:7px 12px;font-weight:bold;}"

# Test durumları
_ST_WAITING = "BEKLIYOR"
_ST_RUNNING = "ÇALIŞIYOR"
_ST_PASS    = "BAŞARILI"
_ST_FAIL    = "BAŞARISIZ"
_ST_SKIP    = "ATLANDI"

# Önceden uçuş kontrol listesi öğeleri
_CHECKLIST = [
    ("GPS Fix",            "3D GPS kilidi gerekli (en az 6 uydu)",     "gps_fix"),
    ("EKF Durumu",         "EKF hatası yok",                           "ekf_ok"),
    ("Batarya > %50",      "Batarya seviyesi yeterli",                  "battery_ok"),
    ("RC Bağlantısı",      "RC alıcı sinyali aktif",                   "rc_ok"),
    ("Pusula Kalibrasyonu","Pusula kalibre edilmiş",                   "compass_ok"),
    ("İvmeölçer Kalibr.",  "İvmeölçer kalibre edilmiş",               "accel_ok"),
    ("Telemetri Bağlı",    "MAVLink bağlantısı aktif",                 "telem_ok"),
    ("Arming Engeli Yok",  "Kritik hata veya engel yok",              "arming_ok"),
]


# ── Sağlık Göstergesi ─────────────────────────────────────────────────────────
class _HealthIndicator(QFrame):
    """Renkli LED + başlık + değer göstergesi."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setMinimumSize(140, 85)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QFrame{border:1px solid #0f3460;border-radius:6px;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        self._led = QLabel("●")
        self._led.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._led.setFont(QFont("Segoe UI", 18))
        self._led.setStyleSheet("color:#555;")

        self._title = QLabel(title)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet("color:#888;font-size:10px;font-weight:bold;")

        self._value = QLabel("—")
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value.setStyleSheet("color:#666;font-size:10px;")

        lay.addWidget(self._led)
        lay.addWidget(self._title)
        lay.addWidget(self._value)

    def set_status(self, ok: bool | None, value_text: str = ""):
        if ok is True:
            color, led_color = "#1a3a1a", "#4ade80"
        elif ok is False:
            color, led_color = "#3a1a1a", "#ff4444"
        else:
            color, led_color = "#111122", "#555"
        self.setStyleSheet(
            f"QFrame{{border:1px solid #0f3460;border-radius:6px;background:{color};}}"
        )
        self._led.setStyleSheet(f"color:{led_color};")
        if value_text:
            self._value.setText(value_text)
            self._value.setStyleSheet(f"color:{led_color};font-size:10px;")


# ── Kontrol Listesi Satırı ────────────────────────────────────────────────────
class _CheckRow(QFrame):
    def __init__(self, name: str, desc: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QFrame{border:none;background:transparent;}"
            "QFrame:hover{}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)

        self._icon = QLabel("⬜")
        self._icon.setFixedWidth(24)
        self._icon.setStyleSheet("font-size:14px;")

        lbl = QLabel(name)
        lbl.setStyleSheet("color:#ccc;font-size:12px;font-weight:bold;")
        lbl.setFixedWidth(180)

        self._desc_lbl = QLabel(desc)
        self._desc_lbl.setStyleSheet("color:#777;font-size:11px;")

        lay.addWidget(self._icon)
        lay.addWidget(lbl)
        lay.addWidget(self._desc_lbl, 1)

    def set_state(self, ok: bool | None):
        if ok is True:
            self._icon.setText("✅")
        elif ok is False:
            self._icon.setText("❌")
        else:
            self._icon.setText("⬜")


# ── Test Satırı ───────────────────────────────────────────────────────────────
class _TestRow(QFrame):
    start_requested = pyqtSignal(int)   # test index

    def __init__(self, idx: int, title: str, description: str, parent=None):
        super().__init__(parent)
        self._idx = idx
        self._state = _ST_WAITING
        self.setStyleSheet(
            "QFrame{border:1px solid #1a1a3e;border-radius:4px;}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)

        self._num = QLabel(f"TEST {idx + 1}")
        self._num.setFixedWidth(55)
        self._num.setStyleSheet("color:#00e5ff;font-weight:bold;font-size:12px;")

        self._title = QLabel(title)
        self._title.setStyleSheet("color:#ddd;font-size:12px;font-weight:bold;")
        self._title.setFixedWidth(220)

        self._desc = QLabel(description)
        self._desc.setStyleSheet("color:#777;font-size:11px;")

        self._timer_lbl = QLabel("—")
        self._timer_lbl.setFixedWidth(60)
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._timer_lbl.setStyleSheet("color:#aaa;font-size:11px;")

        self._status_lbl = QLabel(_ST_WAITING)
        self._status_lbl.setFixedWidth(90)
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setStyleSheet(
            "color:#777;border-radius:3px;padding:2px 4px;font-size:11px;"
        )

        self._start_btn = QPushButton("Başlat")
        self._start_btn.setStyleSheet(_SBG)
        self._start_btn.clicked.connect(lambda: self.start_requested.emit(self._idx))

        lay.addWidget(self._num)
        lay.addWidget(self._title)
        lay.addWidget(self._desc, 1)
        lay.addWidget(self._timer_lbl)
        lay.addWidget(self._status_lbl)
        lay.addWidget(self._start_btn)

    def set_state(self, state: str):
        self._state = state
        color_map = {
            _ST_WAITING: "#777",
            _ST_RUNNING: "#f0a500",
            _ST_PASS:    "#4ade80",
            _ST_FAIL:    "#ff4444",
            _ST_SKIP:    "#888",
        }
        bg_map = {
            _ST_WAITING: "#1a1a2e",
            _ST_RUNNING: "#2a2a0a",
            _ST_PASS:    "#0a2a0a",
            _ST_FAIL:    "#2a0a0a",
            _ST_SKIP:    "#1a1a1a",
        }
        c = color_map.get(state, "#777")
        bg = bg_map.get(state, "#1a1a2e")
        self._status_lbl.setText(state)
        self._status_lbl.setStyleSheet(
            f"background:{bg};color:{c};border-radius:3px;padding:2px 4px;font-size:11px;"
        )
        self._start_btn.setEnabled(state not in (_ST_RUNNING, _ST_PASS))

    def set_timer(self, text: str):
        self._timer_lbl.setText(text)

    def set_running(self, enable: bool):
        self._start_btn.setEnabled(not enable)


# ── Ana Test Paneli ───────────────────────────────────────────────────────────
class TestPanelView(QWidget):
    send_command = pyqtSignal(int, list)

    # Test konfigürasyonları: (başlık, kısa açıklama, beklenen süre_s)
    _TEST_CONFIGS = [
        ("Otonom Kalkış Testi",
         "ARM + TAKEOFF komutu → hedef irtifaya ulaşma",
         30),
        ("Otonom İniş Testi",
         "LAND komutu → yere güvenli iniş",
         30),
        ("RC Failsafe → RTL/LAND",
         "RC sinyal kesilmesi → failsafe tetikleme",
         60),
        ("Telemetri Failsafe → RTL",
         "Telemetri kopması → drone tepkisi izleme",
         60),
        ("Haberleşme Menzil Testi",
         "Video/telemetri menzil kaydı ve değerlendirme",
         120),
        ("Hover / Loiter Kararlılık Testi",
         "LOITER modu → pozisyon tutma kalitesi",
         60),
    ]

    def __init__(self, db_conn, parent=None):
        super().__init__(parent)
        self._db = db_conn
        self._active_test: int = -1
        self._test_start_ts: int = 0
        self._telemetry_snap: dict = {}
        self._test_results: dict[int, dict] = {}
        self._session_ts_start: int = 0

        self._elapsed = QElapsedTimer()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._on_tick)

        self._build_ui()

    # ── UI İnşa ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # --- Üst Satır: Sağlık Göstergeleri ---
        health_grp = QGroupBox("📡  Aviyonik Sağlık Durumu")
        health_grp.setStyleSheet(_SS)
        h_lay = QHBoxLayout(health_grp)
        h_lay.setSpacing(10)

        self._h_gps  = _HealthIndicator("GPS")
        self._h_ekf  = _HealthIndicator("EKF")
        self._h_bat  = _HealthIndicator("Batarya")
        self._h_rc   = _HealthIndicator("RC")
        self._h_arm  = _HealthIndicator("Arming")

        for w in (self._h_gps, self._h_ekf, self._h_bat, self._h_rc, self._h_arm):
            h_lay.addWidget(w)
        h_lay.addStretch(1)

        # Bağlantı durumu
        self._conn_lbl = QLabel("● Bağlantı: Yok")
        self._conn_lbl.setStyleSheet("color:#ff4444;font-weight:bold;font-size:12px;")
        h_lay.addWidget(self._conn_lbl)

        root.addWidget(health_grp)

        # --- Orta Splitter ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Sol: Kontrol Listesi
        cl_grp = QGroupBox("✅  Uçuş Öncesi Kontrol Listesi")
        cl_grp.setStyleSheet(_SS)
        cl_lay = QVBoxLayout(cl_grp)
        cl_lay.setSpacing(2)

        self._check_rows: dict[str, _CheckRow] = {}
        for name, desc, key in _CHECKLIST:
            row = _CheckRow(name, desc)
            self._check_rows[key] = row
            cl_lay.addWidget(row)

        cl_lay.addStretch(1)

        self._chk_summary = QLabel("Kontrol bekleniyor…")
        self._chk_summary.setStyleSheet("color:#aaa;font-size:11px;padding:4px;")
        cl_lay.addWidget(self._chk_summary)

        cl_scroll = QScrollArea()
        cl_scroll.setWidgetResizable(True)
        cl_scroll.setStyleSheet("QScrollArea{border:none;}")
        cl_scroll.setWidget(cl_grp)
        splitter.addWidget(cl_scroll)

        # Sağ: Testler + Log
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        test_grp = QGroupBox("🧪  Test Senaryoları")
        test_grp.setStyleSheet(_SS)
        t_lay = QVBoxLayout(test_grp)
        t_lay.setSpacing(4)

        self._test_rows: list[_TestRow] = []
        for i, (title, desc, _) in enumerate(self._TEST_CONFIGS):
            row = _TestRow(i, title, desc)
            row.start_requested.connect(self._on_start_test)
            self._test_rows.append(row)
            t_lay.addWidget(row)

        right_lay.addWidget(test_grp)

        # Test log
        log_grp = QGroupBox("📋  Test Logu")
        log_grp.setStyleSheet(_SS)
        log_lay = QVBoxLayout(log_grp)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(140)
        self._log.setStyleSheet(
            "QTextEdit{color:#00e5ff;font-family:'Consolas','Courier New';"
            "font-size:11px;border:1px solid #0f3460;}"
        )
        log_lay.addWidget(self._log)

        # Operatör sonuç butonları (test çalışırken görünür)
        self._result_row = QHBoxLayout()
        self._result_lbl = QLabel("Sonuç kaydet:")
        self._result_lbl.setStyleSheet("color:#aaa;font-size:11px;")
        self._pass_btn = QPushButton("✅  Geçti")
        self._pass_btn.setStyleSheet(_SBG)
        self._pass_btn.clicked.connect(lambda: self._on_manual_result(True))
        self._fail_btn = QPushButton("❌  Başarısız")
        self._fail_btn.setStyleSheet(_SBR)
        self._fail_btn.clicked.connect(lambda: self._on_manual_result(False))
        self._result_row.addWidget(self._result_lbl)
        self._result_row.addWidget(self._pass_btn)
        self._result_row.addWidget(self._fail_btn)
        self._result_row.addStretch()
        self._result_widget = QWidget()
        self._result_widget.setLayout(self._result_row)
        self._result_widget.setVisible(False)
        log_lay.addWidget(self._result_widget)

        btn_row = QHBoxLayout()
        self._save_btn = QPushButton("💾  Raporu Kaydet (DB + Dosya)")
        self._save_btn.setStyleSheet(_SB)
        self._save_btn.clicked.connect(self._save_report)
        self._clear_log_btn = QPushButton("🗑  Logu Temizle")
        self._clear_log_btn.setStyleSheet(_SBR)
        self._clear_log_btn.clicked.connect(self._log.clear)
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._clear_log_btn)
        log_lay.addLayout(btn_row)

        right_lay.addWidget(log_grp)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setStyleSheet("QScrollArea{border:none;}")
        right_scroll.setWidget(right_w)
        splitter.addWidget(right_scroll)
        splitter.setSizes([340, 720])

        root.addWidget(splitter, 1)

    # ── Telemetri Güncellemesi ────────────────────────────────────────────────
    @pyqtSlot(dict)
    def update_telemetry(self, data: dict):
        self._telemetry_snap = dict(data)
        self._update_health(data)
        self._update_checklist(data)

    def _update_health(self, d: dict):
        # GPS
        gps_fix = d.get("gps_fix_type", 0)
        gps_sats = d.get("gps_sats", 0)  # mavlink_worker 'gps_sats' olarak gönderiyor
        self._h_gps.set_status(
            gps_fix >= 3,
            f"FIX:{gps_fix} {gps_sats}uydu"
        )

        # EKF (ekf_ok=1 means OK in ArduPilot)
        ekf = d.get("ekf_flags", 0)
        ekf_ok = (ekf & 0x1F) == 0x1F  # tüm temel bitler set
        self._h_ekf.set_status(ekf_ok, f"0x{ekf:04X}")

        # Batarya
        bat_rem = d.get("battery_remaining", -1)
        bat_v   = d.get("battery_voltage", 0)
        bat_ok  = bat_rem >= 50 if bat_rem >= 0 else None
        self._h_bat.set_status(bat_ok, f"%{bat_rem}  {bat_v:.1f}V" if bat_rem >= 0 else "—")

        # RC
        rc_rssi = d.get("rc_rssi", -1)
        rc_ok   = rc_rssi > 50 if rc_rssi >= 0 else None
        self._h_rc.set_status(rc_ok, f"RSSI:{rc_rssi}" if rc_rssi >= 0 else "—")

        # Arming
        armed = d.get("armed", False)
        mode  = d.get("mode", "—")
        self._h_arm.set_status(None, f"{'ARMED' if armed else 'DISARMED'}\n{mode}")

        # Bağlantı
        self._conn_lbl.setText("● Bağlantı: AKTİF")
        self._conn_lbl.setStyleSheet("color:#4ade80;font-weight:bold;font-size:12px;")

    def _update_checklist(self, d: dict):
        states: dict[str, bool] = {
            "gps_fix":    d.get("gps_fix_type", 0) >= 3,
            "ekf_ok":     (d.get("ekf_flags", 0) & 0x1F) == 0x1F,
            "battery_ok": d.get("battery_remaining", -1) >= 50,
            "rc_ok":      d.get("rc_rssi", -1) > 50,
            "compass_ok": d.get("compass_cal_done", False),
            "accel_ok":   d.get("accel_cal_done", False),
            "telem_ok":   True,  # telemetri veri geldiyse bağlı
            "arming_ok":  not d.get("pre_arm_fail", False),
        }
        pass_count = 0
        for key, row in self._check_rows.items():
            ok = states.get(key)
            row.set_state(ok)
            if ok:
                pass_count += 1

        total = len(_CHECKLIST)
        if pass_count == total:
            self._chk_summary.setText(f"✅  Tüm kontroller geçti ({total}/{total}) — Uçuşa hazır!")
            self._chk_summary.setStyleSheet("color:#4ade80;font-size:11px;padding:4px;")
        else:
            self._chk_summary.setText(f"⚠️  {pass_count}/{total} kontrol geçti — Uçuş riski var!")
            self._chk_summary.setStyleSheet("color:#f0a500;font-size:11px;padding:4px;")

    # ── Test Yönetimi ─────────────────────────────────────────────────────────
    @pyqtSlot(int)
    def _on_start_test(self, idx: int):
        if self._active_test >= 0:
            QMessageBox.warning(
                self, "Test Çalışıyor",
                f"TEST {self._active_test + 1} hâlâ çalışıyor. Lütfen bekleyin."
            )
            return

        if self._session_ts_start == 0:
            self._session_ts_start = int(time.time())

        self._active_test = idx
        self._test_start_ts = int(time.time())

        for row in self._test_rows:
            row.set_running(True)

        self._test_rows[idx].set_state(_ST_RUNNING)
        self._elapsed.start()
        self._tick_timer.start()

        title = self._TEST_CONFIGS[idx][0]
        self._log_line(f"▶  TEST {idx + 1}: {title} — BAŞLADI")
        self._log_line(f"   Telemetri: GPS={self._telemetry_snap.get('gps_fix_type', '?')}, "
                       f"Bat=%{self._telemetry_snap.get('battery_remaining', '?')}, "
                       f"Mode={self._telemetry_snap.get('mode', '?')}")

        # MAVLink komutları
        self._execute_test_command(idx)

    def _execute_test_command(self, idx: int):
        """Her test için uygun MAVLink komutunu gönderir."""
        try:
            from pymavlink.dialects.v20 import ardupilotmega as mavlink
        except ImportError:
            from pymavlink.dialects.v10 import ardupilotmega as mavlink

        if idx == 0:  # Otonom Kalkış
            # Adım 1: GUIDED modına geç
            # MAV_CMD_DO_SET_MODE: base_mode=CUSTOM_MODE(1), custom_mode=4(GUIDED)
            self.send_command.emit(
                mavlink.MAV_CMD_DO_SET_MODE,
                [1, 4, 0, 0, 0, 0, 0]
            )
            self._log_line("   → Adım 1/3: GUIDED moduna geçiliyor...")

            # Adım 2: 800 ms sonra ARM
            def _do_arm():
                self.send_command.emit(
                    mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                    [1, 0, 0, 0, 0, 0, 0]
                )
                self._log_line("   → Adım 2/3: ARM komutu gönderildi")

            # Adım 3: 2.5 sn sonra TAKEOFF (20 m)
            def _do_takeoff():
                self.send_command.emit(
                    mavlink.MAV_CMD_NAV_TAKEOFF,
                    [0, 0, 0, 0, 0, 0, 20]
                )
                self._log_line("   → Adım 3/3: TAKEOFF(20m) komutu gönderildi")
                self._log_line(
                    "   ℹ  İrtifa artmaya başladığında kalkış başarılı sayılır.\n"
                    "   Aşağıdaki \"Geçti\" / \"Başarısız\" butonuyla sonucu kaydedin."
                )

            QTimer.singleShot(800,  _do_arm)
            QTimer.singleShot(2500, _do_takeoff)

        elif idx == 1:  # Otonom İniş
            alt = self._telemetry_snap.get("alt", 0)
            armed = self._telemetry_snap.get("armed", False)
            if not armed or alt < 2.0:
                self._log_line(
                    "   ⚠  Dron silahlı değil veya çok alçakta (İrtifa: "
                    f"{alt:.1f} m) — LAND komutu gönderilmedi."
                )
                self._log_line("   ℹ  Önce Test 1 (Kalkış) yapın.")
                QTimer.singleShot(0, lambda: self._finish_test(idx, _ST_FAIL))
                return
            self.send_command.emit(
                mavlink.MAV_CMD_NAV_LAND,
                [0, 0, 0, 0, 0, 0, 0]
            )
            self._log_line("   → LAND komutu gönderildi")
            self._log_line(
                "   ℹ  İrtifa sıfıra indiğinde \"Geçti\" butonuna basın."
            )

        elif idx == 5:  # Hover / Loiter Kararlılık Testi
            armed = self._telemetry_snap.get("armed", False)
            if not armed:
                self._log_line(
                    "   ⚠  Dron silahlı değil — LOITER komutu gönderilmedi."
                )
                self._log_line("   ℹ  Drone havada olmalıdır.")
                QTimer.singleShot(0, lambda: self._finish_test(idx, _ST_FAIL))
                return
            # LOITER modu: ArduCopter custom_mode = 5
            self.send_command.emit(
                mavlink.MAV_CMD_DO_SET_MODE,
                [1, 5, 0, 0, 0, 0, 0]
            )
            self._log_line("   → LOITER modu aktif edildi")
            self._log_line(
                "   ℹ  Drone pozisyonu sabit tutuyorsa \"Geçti\" butonuna basın."
            )

        elif idx == 2:  # RC Failsafe Test
            self.send_command.emit(
                mavlink.MAV_CMD_DO_SET_MODE,
                [1, 11, 0, 0, 0, 0, 0]  # RTL Mode
            )
            self._log_line("   → RTL modu aktif edildi")
            self._log_line(
                "   ℹ  Drone eve dönüyorsa \"Geçti\" butonuna basın."
            )

        else:
            # Testler 3, 4 — manuel tetikleme
            self._log_line(
                f"   ℹ  Bu test manuel tetikleme gerektirir.\n"
                f"   Talimatları uygulayın ve aşağıda sonucu kaydedin."
            )

        # Kalkış (idx=0) dışındaki tüm testler için
        # operator sonucu geçti / başarısız butonlarını göster
        self._show_manual_result_buttons(idx)

    def _show_manual_result_buttons(self, idx: int) -> None:
        """Operatörün geçti/başarısız seçmesi için butonları gösterir."""
        self._result_widget.setVisible(True)
        self._pass_btn.setEnabled(True)
        self._fail_btn.setEnabled(True)

    def _on_manual_result(self, passed: bool) -> None:
        """Operatörün geçti/başarısız butonuna basması."""
        if self._active_test < 0:
            return
        self._result_widget.setVisible(False)
        self._finish_test(self._active_test, _ST_PASS if passed else _ST_FAIL)

    def mark_test_manual(self, idx: int, passed: bool):
        """Dışarıdan çağrı için API (eski uyumluluk)."""
        result = _ST_PASS if passed else _ST_FAIL
        self._result_widget.setVisible(False)
        self._finish_test(idx, result)

    def _on_tick(self):
        if self._active_test < 0:
            self._tick_timer.stop()
            return
        elapsed = self._elapsed.elapsed() // 1000
        self._test_rows[self._active_test].set_timer(f"{elapsed}s")

    def _finish_test(self, idx: int, result: str):
        if self._active_test != idx:
            return
        elapsed_s = int(self._elapsed.elapsed() / 1000)
        self._tick_timer.stop()

        self._test_rows[idx].set_state(result)
        for row in self._test_rows:
            row.set_running(False)

        self._active_test = -1
        title = self._TEST_CONFIGS[idx][0]
        icon = "✅" if result == _ST_PASS else "❌"
        self._log_line(
            f"{icon}  TEST {idx + 1}: {title} — {result}  ({elapsed_s}s)"
        )

        snap = dict(self._telemetry_snap)
        snap.update({
            "duration_s": elapsed_s,
            "result": result,
            "test_title": title,
        })
        self._test_results[idx] = snap

    # ── Rapor Kayıt ──────────────────────────────────────────────────────────
    def _save_report(self):
        if not self._test_results:
            QMessageBox.information(self, "Rapor", "Henüz tamamlanmış test yok.")
            return

        ts_now = int(time.time())
        all_pass = all(r.get("result") == _ST_PASS for r in self._test_results.values())
        overall = _ST_PASS if all_pass else _ST_FAIL

        # Rapor metni oluştur
        lines = [
            "=" * 60,
            "ASHİNA YKİ — UÇUŞ ÖNCESİ TEST RAPORU",
            f"Tarih: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts_now))}",
            f"Genel Sonuç: {overall}",
            "=" * 60,
        ]
        for i, cfg in enumerate(self._TEST_CONFIGS):
            r = self._test_results.get(i)
            if r:
                lines.append(
                    f"TEST {i + 1}: {cfg[0]}\n"
                    f"  Sonuç: {r.get('result','?')}  Süre: {r.get('duration_s','?')}s\n"
                    f"  GPS: {r.get('gps_fix_type','?')}  Bat: %{r.get('battery_remaining','?')}"
                )
            else:
                lines.append(f"TEST {i + 1}: {cfg[0]}\n  Sonuç: YÜRÜTÜLMEDİ")
        lines.append("=" * 60)
        report_text = "\n".join(lines)

        # Dosyaya kaydet
        path, _ = QFileDialog.getSaveFileName(
            self, "Test Raporunu Kaydet",
            f"test_raporu_{time.strftime('%Y%m%d_%H%M%S')}.txt",
            "Metin Dosyası (*.txt)"
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(report_text)
            except OSError as e:
                QMessageBox.warning(self, "Kayıt Hatası", str(e))
                path = ""

        # DB'ye kaydet
        try:
            params_json = json.dumps({
                "tests": self._test_results,
                "telemetry_snapshot": self._telemetry_snap,
            }, ensure_ascii=False)
            local_storage.save_test_session(
                self._db,
                test_type="PREFLIGHT_ALL",
                result=overall,
                params_json=params_json,
                ts_start=self._session_ts_start or ts_now,
                ts_end=ts_now,
                report_path=path,
            )
            local_storage.write_audit(self._db, "INFO", f"Test raporu kaydedildi: {overall}")
            self._log_line(f"💾  Rapor DB'ye kaydedildi. Sonuç: {overall}")
            QMessageBox.information(
                self, "Kayıt Başarılı",
                f"Test raporu veritabanına kaydedildi.\n{f'Dosya: {path}' if path else ''}"
            )
        except Exception as e:
            logger.error("Rapor kayıt hatası: %s", e)
            QMessageBox.warning(self, "DB Hatası", str(e))

        # Oturumu sıfırla
        self._session_ts_start = 0

    # ── Yardımcı ─────────────────────────────────────────────────────────────
    def _log_line(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self._log.append(f"[{ts}]  {text}")
        logger.info("TEST LOG: %s", text)
