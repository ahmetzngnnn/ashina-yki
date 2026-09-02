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

from core import local_storage, theme
from core import batarya

logger = logging.getLogger(__name__)

# ── Stil ──────────────────────────────────────────────────────────────────────
# NOT: QGroupBox / buton stilleri artık global style.qss'ten gelir (v2 şartname);
# yerel override yok.

# Test durumları
_ST_WAITING = "BEKLIYOR"
_ST_RUNNING = "ÇALIŞIYOR"
_ST_PASS    = "BAŞARILI"
_ST_FAIL    = "BAŞARISIZ"
_ST_SKIP    = "ATLANDI"
# NEDEN (H5): YKİ'nin hiçbir komut göndermediği, tamamen operatör gözlemine
# dayanan testler için ayrı durum. "ÇALIŞIYOR" göstermek testin otomatik
# yürütüldüğü yanılgısı yaratıp sahte BAŞARILI sonucuna yol açıyordu.
_ST_MANUAL  = "MANUEL"

# Test durumu → tema token'ı. Renkler çalışma anında değiştiği için inline
# kalır, ama ham hex yerine token üzerinden okunur (tema değişimini izler).
# NOT: ÇALIŞIYOR "warning" (devam eden işlem), MANUEL "info" tonundadır —
# H5 ayrımı (otomatik yürüyen test / operatör gözlemi) renkte de korunmalı.
_STATE_TOKENS = {
    _ST_WAITING: "text_muted",
    _ST_RUNNING: "warning",
    _ST_PASS:    "success",
    _ST_FAIL:    "danger",
    _ST_SKIP:    "text_muted",
    _ST_MANUAL:  "info",
}

# Otomatik komutu olmayan testler (idx) ve operatörün uygulaması gereken adımlar.
_MANUAL_TESTS = (3, 4)
_MANUAL_STEPS = {
    3: (
        "1) Drone'u güvenli irtifada AUTO/GUIDED modda uçurun.",
        "2) YKİ telemetri antenini/USB'sini fiziksel olarak ayırın.",
        "3) FS_GCS_ENABLE süresi dolunca aracın RTL'e geçtiğini GÖZLEYİN.",
        "4) Telemetriyi geri takın, aracın eve döndüğünü doğrulayın.",
    ),
    4: (
        "1) Drone'u sabit irtifada tutarak YKİ'den uzaklaştırın.",
        "2) RSSI / paket kaybı / video donma mesafelerini not edin.",
        "3) Bağlantı bozulmaya başladığı mesafeyi kaydedin.",
        "4) Aracı menzil içine geri getirip bağlantıyı doğrulayın.",
    ),
}

# Önceden uçuş kontrol listesi öğeleri
_CHECKLIST = [
    ("GPS Fix",            "3D GPS kilidi gerekli (en az 6 uydu)",     "gps_fix"),
    ("EKF Durumu",         "EKF hatası yok",                           "ekf_ok"),
    # NEDEN başlık f-string: eşik core/batarya.py'de tanımlı. Metne 50'yi
    # elle yazmak, eşik değişince listenin YALAN söylemesi demekti.
    (f"Batarya > %{batarya.UCUS_ONCESI_ASGARI_YUZDE}",
     f"Pil gerilimi yeterli (≥ {batarya.yuzdeden_gerilim(batarya.UCUS_ONCESI_ASGARI_YUZDE):.1f} V)",
     "battery_ok"),
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
        self.setMinimumSize(120, 84)
        self.setMaximumHeight(92)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setObjectName("cardFrame")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)

        # Son bilinen durum — tema değişince renkler bundan yeniden üretilir.
        self._ok: bool | None = None

        self._led = QLabel("●")
        self._led.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._led.setFont(QFont("Segoe UI", 18))

        self._title = QLabel(title)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._value = QLabel("—")
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay.addWidget(self._led)
        lay.addWidget(self._title)
        lay.addWidget(self._value)

        self._apply_theme_colors()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def set_status(self, ok: bool | None, value_text: str = ""):
        self._ok = ok
        if value_text:
            self._value.setText(value_text)
        self._apply_theme_colors()

    def _apply_theme_colors(self):
        """Durum + tema renklerini (yeniden) yazar.

        Durum yalnız kenarlık + LED rengiyle gösterilir; arka plan boyanmaz
        (kart zemini QSS'teki cardFrame'den gelir)."""
        if self._ok is True:
            border_color = led_color = theme.c("success")
        elif self._ok is False:
            border_color = led_color = theme.c("danger")
        else:
            border_color, led_color = theme.c("border"), theme.c("text_muted")
        # Kapsayıcıya ID seçicili stil — alt widget'lara yayılmaz.
        card_sheet = f"QFrame#cardFrame{{border-color:{border_color};}}"
        self.setStyleSheet(card_sheet)
        self._led.setStyleSheet(f"color:{led_color};")
        # 10px + bold kombinasyonu hazır class'la karşılanmıyor; renk token'dan.
        self._title.setStyleSheet(
            f"color:{theme.c('text_muted')};font-size:10px;font-weight:bold;"
        )
        self._value.setStyleSheet(f"color:{led_color};font-size:10px;")


# ── Kontrol Listesi Satırı ────────────────────────────────────────────────────
class _CheckRow(QFrame):
    def __init__(self, name: str, desc: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(8)

        self._icon = QLabel("⬜")
        self._icon.setFixedWidth(24)
        self._icon.setStyleSheet("font-size:14px;")   # renk yok — emoji

        self._name_lbl = QLabel(name)
        self._name_lbl.setFixedWidth(190)

        self._desc_lbl = QLabel(desc)
        self._desc_lbl.setObjectName("mutedLabel")     # 11px soluk metin (QSS)
        self._desc_lbl.setWordWrap(True)   # dar panelde kırpma yerine satır sar

        lay.addWidget(self._icon)
        lay.addWidget(self._name_lbl)
        lay.addWidget(self._desc_lbl, 1)

        self._apply_theme_colors()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _apply_theme_colors(self):
        """12px + bold metin class'ı yok; renk token'dan verilir."""
        self._name_lbl.setStyleSheet(
            f"color:{theme.c('text')};font-size:12px;font-weight:bold;"
        )

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
        self.setObjectName("cardFrame")
        self.setMaximumHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        self._num = QLabel(f"TEST {idx + 1}")
        self._num.setFixedWidth(55)

        self._title = QLabel(title)
        self._title.setFixedWidth(220)

        self._desc = QLabel(description)
        self._desc.setObjectName("mutedLabel")        # 11px soluk metin (QSS)

        self._timer_lbl = QLabel("—")
        self._timer_lbl.setObjectName("mutedLabel")
        self._timer_lbl.setFixedWidth(60)
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._status_lbl = QLabel(_ST_WAITING)
        self._status_lbl.setObjectName("mutedLabel")
        self._status_lbl.setFixedWidth(90)
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._start_btn = QPushButton("Başlat")
        self._start_btn.setObjectName("successButton")
        self._start_btn.setFixedHeight(28)
        self._start_btn.setFixedWidth(90)
        self._start_btn.clicked.connect(lambda: self.start_requested.emit(self._idx))

        lay.addWidget(self._num)
        lay.addWidget(self._title)
        lay.addWidget(self._desc, 1)
        lay.addWidget(self._timer_lbl)
        lay.addWidget(self._status_lbl)
        lay.addWidget(self._start_btn)

        self._apply_theme_colors()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _apply_theme_colors(self):
        """Kurulumda yazılan inline renkleri tema değişiminde tazeler.

        Buton etkinliği burada DEĞİŞTİRİLMEZ — set_state'i çağırmak, başka bir
        test çalışırken kilitlenmiş Başlat butonlarını yeniden açardı."""
        self._num.setStyleSheet(
            f"color:{theme.c('accent')};font-weight:bold;font-size:12px;"
        )
        self._title.setStyleSheet(
            f"color:{theme.c('text')};font-size:12px;font-weight:bold;"
        )
        # Yalnız metin rengi (arka plan/kenarlık inline YOK).
        self._status_lbl.setStyleSheet(
            f"color:{theme.c(_STATE_TOKENS.get(self._state, 'text_muted'))};"
        )

    def set_state(self, state: str):
        self._state = state
        self._status_lbl.setText(state)
        # Yalnız metin rengi çalışma anında değişir (arka plan/kenarlık inline YOK).
        self._status_lbl.setStyleSheet(
            f"color:{theme.c(_STATE_TOKENS.get(state, 'text_muted'))};"
        )
        # NEDEN (H5): MANUEL durumu da "devam eden test" sayılır — bu satırın
        # Başlat butonu, operatör sonucu kaydedene kadar kapalı kalmalıdır.
        self._start_btn.setEnabled(state not in (_ST_RUNNING, _ST_PASS, _ST_MANUAL))

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
        self._conn_ok: bool = False   # bağlantı etiketinin son bilinen durumu

        self._elapsed = QElapsedTimer()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._on_tick)

        self._build_ui()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ── UI İnşa ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # --- Üst Satır: Sağlık Göstergeleri ---
        health_grp = QGroupBox("📡  Aviyonik Sağlık Durumu")
        h_lay = QHBoxLayout(health_grp)
        h_lay.setSpacing(8)

        self._h_gps  = _HealthIndicator("GPS")
        self._h_ekf  = _HealthIndicator("EKF")
        self._h_bat  = _HealthIndicator("Batarya")
        self._h_rc   = _HealthIndicator("RC")
        self._h_arm  = _HealthIndicator("Arming")

        # Kartlar satırı eşit stretch ile paylaşır; dev orta boşluk yok.
        for w in (self._h_gps, self._h_ekf, self._h_bat, self._h_rc, self._h_arm):
            h_lay.addWidget(w, 1)

        # Bağlantı durumu — satırın sağında sabit genişlikte
        self._conn_lbl = QLabel("● Bağlantı: Yok")
        self._conn_lbl.setFixedWidth(150)
        self._conn_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._apply_conn_style()
        h_lay.addWidget(self._conn_lbl)

        root.addWidget(health_grp)

        # --- Orta Splitter ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Sol: Kontrol Listesi
        cl_grp = QGroupBox("✅  Uçuş Öncesi Kontrol Listesi")
        cl_lay = QVBoxLayout(cl_grp)
        cl_lay.setSpacing(6)

        self._check_rows: dict[str, _CheckRow] = {}
        for name, desc, key in _CHECKLIST:
            row = _CheckRow(name, desc)
            self._check_rows[key] = row
            cl_lay.addWidget(row)

        cl_lay.addStretch(1)

        self._chk_summary = QLabel("Kontrol bekleniyor…")
        self._chk_summary.setObjectName("hintLabel")
        # Sola yaslı — tam satır genişliğinde bar oluşturmaz.
        cl_lay.addWidget(self._chk_summary, 0, Qt.AlignmentFlag.AlignLeft)

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
        t_lay = QVBoxLayout(test_grp)
        t_lay.setSpacing(6)

        self._test_rows: list[_TestRow] = []
        for i, (title, desc, _) in enumerate(self._TEST_CONFIGS):
            row = _TestRow(i, title, desc)
            row.start_requested.connect(self._on_start_test)
            self._test_rows.append(row)
            t_lay.addWidget(row)

        right_lay.addWidget(test_grp)

        # Test log
        log_grp = QGroupBox("📋  Test Logu")
        log_lay = QVBoxLayout(log_grp)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(140)
        self._log.document().setMaximumBlockCount(500)   # sınırsız büyümeyi engelle
        self._apply_log_style()
        log_lay.addWidget(self._log)

        # Operatör sonuç butonları (test çalışırken görünür)
        self._result_row = QHBoxLayout()
        self._result_lbl = QLabel("Sonuç kaydet:")
        self._result_lbl.setObjectName("mutedLabel")   # 11px soluk metin (QSS)
        self._pass_btn = QPushButton("✅  Geçti")
        self._pass_btn.setObjectName("successButton")
        self._pass_btn.clicked.connect(lambda: self._on_manual_result(True))
        self._fail_btn = QPushButton("❌  Başarısız")
        self._fail_btn.setObjectName("dangerButton")
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
        btn_row.setSpacing(8)
        self._save_btn = QPushButton("💾  Raporu Kaydet (DB + Dosya)")
        self._save_btn.setObjectName("primaryButton")
        self._save_btn.setFixedHeight(30)
        self._save_btn.clicked.connect(self._save_report)
        self._clear_log_btn = QPushButton("🗑  Logu Temizle")  # nötr stil
        self._clear_log_btn.setFixedHeight(30)
        self._clear_log_btn.clicked.connect(self._log.clear)
        btn_row.addStretch(1)  # eylemler sağa yaslı, tam satır genişliğinde değil
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

        # ── Batarya — GERİLİM TABANLI YÜZDE ────────────────────────────────
        # NEDEN değişti: burada eskiden FC'nin `battery_remaining` (mAh sayımı)
        # alanı kullanılıyordu. Kullanıcı kararı (2026-09-02): yüzde gerilimden
        # türetilir (25.2 V=%100, 21.0 V=%0). Hesap core/batarya.py'de, TEK yerde.
        # Yüzde bilinmiyorsa durum None ("bilinmiyor") — False (BAŞARISIZ)
        # DEĞİL: veri gelmemesi ile pilin zayıf olması aynı şey değildir ve
        # sahte kırmızı, operatörü sağlam bir paketi değiştirmeye iter.
        bat_pct = batarya.yuzde_telemetriden(d)
        bat_ok  = (bat_pct >= batarya.UCUS_ONCESI_ASGARI_YUZDE
                   if bat_pct is not None else None)
        self._h_bat.set_status(bat_ok, batarya.ozet_metni(d))
        self._h_bat.setToolTip(batarya.ipucu_metni(d))

        # RC
        rc_rssi = d.get("rc_rssi", -1)
        rc_ok   = rc_rssi > 50 if rc_rssi >= 0 else None
        self._h_rc.set_status(rc_ok, f"RSSI:{rc_rssi}" if rc_rssi >= 0 else "—")

        # Arming
        # NEDEN (M8): mavlink_worker telemetriyi "flight_mode" anahtarıyla
        # gönderiyor; eski kod "mode" arıyordu → gösterge her zaman "—" kalıyor,
        # operatör aracın hangi modda olduğunu göremiyordu. Her iki anahtar da
        # desteklenir (sim/eski worker'lar "mode" gönderebilir).
        armed = d.get("armed", False)
        mode  = d.get("flight_mode", d.get("mode", "—"))
        self._h_arm.set_status(None, f"{'ARMED' if armed else 'DISARMED'}\n{mode}")

        # Bağlantı
        self._conn_ok = True
        self._conn_lbl.setText("● Bağlantı: AKTİF")
        self._apply_conn_style()

    def _update_checklist(self, d: dict):
        # NEDEN (M7): "arming_ok" eskiden `not d.get("pre_arm_fail", False)` idi.
        # "pre_arm_fail" alanı telemetride HİÇ üretilmiyor → varsayılan False →
        # kontrol HER ZAMAN ✅ görünüyordu. Bu, hiç doğrulanmamış bir emniyet
        # maddesini "geçti" gibi gösteren SAHTE bir yeşildi. Artık alan yoksa
        # durum "bilinmiyor" (None) → _CheckRow.set_state(None) ⬜ gösterir ve
        # geçti sayısına dahil edilmez.
        _pre_arm = d.get("pre_arm_fail", None)
        # Pil kapısı: gerilim tabanlı yüzde (core/batarya.py) — bilinmiyorsa None.
        _bat_pct = batarya.yuzde_telemetriden(d)
        _battery_ok = (_bat_pct >= batarya.UCUS_ONCESI_ASGARI_YUZDE
                       if _bat_pct is not None else None)
        states: dict[str, bool | None] = {
            "gps_fix":    d.get("gps_fix_type", 0) >= 3,
            "ekf_ok":     (d.get("ekf_flags", 0) & 0x1F) == 0x1F,
            # NEDEN None olabilir: gerilim yoksa yüzde BİLİNMİYOR. Eski kod
            # `battery_remaining` yoksa -1 alıp `-1 >= 50` → False üretiyordu,
            # yani "ölçemedim"i "başarısız" diye sayıyordu. Artık bilinmeyen
            # madde ⬜ gösterilir ve geçti sayısına girmez.
            "battery_ok": _battery_ok,
            "rc_ok":      d.get("rc_rssi", -1) > 50,
            "compass_ok": d.get("compass_cal_done", False),
            "accel_ok":   d.get("accel_cal_done", False),
            "telem_ok":   True,  # telemetri veri geldiyse bağlı
            "arming_ok":  None if _pre_arm is None else (not _pre_arm),
        }
        pass_count = 0
        unknown_count = 0
        for key, row in self._check_rows.items():
            ok = states.get(key)
            row.set_state(ok)
            if ok is True:
                pass_count += 1
            elif ok is None:
                unknown_count += 1

        total = len(_CHECKLIST)
        if pass_count == total:
            self._chk_summary.setText(f"✅  Tüm kontroller geçti ({total}/{total}) — Uçuşa hazır!")
            # hintLabel zemini QSS'ten; yalnız metin rengi duruma göre değişir.
            self._chk_summary.setStyleSheet(f"color:{theme.c('success')};")
        else:
            # NEDEN (M7): Bilinmeyen maddeler ayrıca gösterilir — operatör
            # "geçmedi" ile "veri yok" durumunu ayırt edebilmelidir.
            unk = f"  ({unknown_count} madde BİLİNMİYOR)" if unknown_count else ""
            self._chk_summary.setText(
                f"⚠️  {pass_count}/{total} kontrol geçti{unk} — Uçuş riski var!"
            )
            self._chk_summary.setStyleSheet(f"color:{theme.c('warning')};")

    # ── Tema ─────────────────────────────────────────────────────────────────
    def _apply_conn_style(self):
        """Bağlantı etiketi rengi — son bilinen duruma göre yeniden yazılır."""
        color = theme.c("success") if self._conn_ok else theme.c("danger")
        self._conn_lbl.setStyleSheet(f"color:{color};font-weight:bold;font-size:12px;")

    def _apply_log_style(self):
        """Test logu: mono yazı tipi + info tonu (QSS'te QTextEdit class'ı yok)."""
        self._log.setStyleSheet(
            f"QTextEdit{{color:{theme.c('info')};font-family:'Consolas','Courier New';"
            f"font-size:11px;border:1px solid {theme.c('border')};}}"
        )

    def _apply_theme_colors(self):
        """Tema değişince kurulumda yazılmış inline renkleri yeniden yazar.

        Sağlık kartları ve test satırları kendi aboneliklerini kurar; burada
        yalnız bu view'ın doğrudan boyadığı öğeler tazelenir. Kontrol listesi
        özeti son telemetri anlık görüntüsüyle yeniden üretilir — hiç telemetri
        gelmediyse "Kontrol bekleniyor…" metni bozulmasın diye dokunulmaz."""
        self._apply_conn_style()
        self._apply_log_style()
        if self._telemetry_snap:
            self._update_checklist(self._telemetry_snap)

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
                       # Pil: gerilim tabanlı yüzde + ham gerilim (core/batarya.py).
                       # "veri yok" ile "%0" log'da da ayrışır.
                       f"Bat={batarya.ozet_metni(self._telemetry_snap)}, "
                       # NEDEN (M8): worker "flight_mode" gönderiyor; "mode" eski
                       # anahtar. İkisi de desteklenir, aksi halde log'da hep "?" çıkıyordu.
                       f"Mode={self._telemetry_snap.get('flight_mode', self._telemetry_snap.get('mode', '?'))}")

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

            # GÜVENLİK (C5): _do_arm/_do_takeoff gecikmeli (singleShot) çalışır ve
            # singleShot İPTAL EDİLEMEZ. Test bu aralıkta iptal edilir/bitirilirse
            # (_active_test != 0 olur) drone yine de ARM+TAKEOFF yapıyordu. Artık her
            # adım, testin HÂLÂ aktif kalkış testi (idx=0) olduğunu doğrular; değilse
            # komut gönderilmez.
            _takeoff_idx = idx  # = 0

            # Adım 2: 800 ms sonra ARM
            def _do_arm():
                if self._active_test != _takeoff_idx:
                    self._log_line("   ⚠  Kalkış testi iptal edildi — ARM gönderilmedi.")
                    return
                self.send_command.emit(
                    mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                    [1, 0, 0, 0, 0, 0, 0]
                )
                self._log_line("   → Adım 2/3: ARM komutu gönderildi")

            # Adım 3: 2.5 sn sonra TAKEOFF (20 m)
            def _do_takeoff():
                if self._active_test != _takeoff_idx:
                    self._log_line("   ⚠  Kalkış testi iptal edildi — TAKEOFF gönderilmedi.")
                    return
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
            # NEDEN (C2 — KRİTİK): Eskiden custom_mode=11 gönderiliyordu.
            # ArduCopter'da 11 = DRIFT modudur, RTL DEĞİL. Log "RTL modu aktif
            # edildi" derken araç DRIFT'e geçiyordu (yarı-manuel, pilot girdisi
            # bekleyen mod) → RC failsafe testinde araç eve dönmüyordu.
            # Doğru RTL custom_mode = 6 (bkz. dashboard_view._set_mode(6) ve
            # main_window._on_abort_mission).
            self.send_command.emit(
                mavlink.MAV_CMD_DO_SET_MODE,
                [1, 6, 0, 0, 0, 0, 0]  # RTL Mode (ArduCopter custom_mode = 6)
            )
            self._log_line("   → RTL modu aktif edildi")
            self._log_line(
                "   ℹ  Drone eve dönüyorsa \"Geçti\" butonuna basın."
            )

        else:
            # ── Testler 3, 4 — TAM MANUEL (H5 — YÜKSEK) ────────────────────
            # NEDEN: Bu iki test hiçbir MAVLink komutu göndermiyor. Eski kod
            # yalnızca "manuel tetikleme gerektirir" yazıp testi ÇALIŞIYOR
            # durumunda bırakıyordu; sayaç işliyor ve operatör hiçbir şey
            # yapmadan "Geçti"ye basınca test BAŞARILI sayılıyordu. Artık durum
            # açıkça MANUEL olarak işaretlenir, otomatik PASS verilmez ve
            # operatöre yapılacak adımlar tek tek log'a yazılır.
            self._test_rows[idx].set_state(_ST_MANUAL)
            self._log_line(
                "   ⚠  MANUEL TEST — YKİ hiçbir komut GÖNDERMEZ. "
                "Sonuç yalnızca operatörün fiili gözlemiyle kaydedilir."
            )
            for step in _MANUAL_STEPS.get(idx, ()):
                self._log_line(f"      {step}")
            self._log_line(
                "   ℹ  Adımları uyguladıktan sonra gerçek gözleminize göre "
                "\"Geçti\" / \"Başarısız\" butonuna basın."
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
            # NEDEN (H5): Bu testte YKİ hiçbir komut göndermedi — sonuç yalnızca
            # operatör beyanıdır. Rapor bunu otomatik doğrulanmış testten
            # ayırt edebilsin diye işaretlenir.
            "manual_only": idx in _MANUAL_TESTS,
        })
        self._test_results[idx] = snap

    # ── Rapor Kayıt ──────────────────────────────────────────────────────────
    def _save_report(self):
        if not self._test_results:
            QMessageBox.information(self, "Rapor", "Henüz tamamlanmış test yok.")
            return

        ts_now = int(time.time())
        # ── Genel sonuç hesabı (H6 — YÜKSEK) ────────────────────────────────
        # NEDEN: Eski kod `all(... == PASS)` kullanıyordu. Boş/eksik sonuç
        # kümesinde "hiç başarısız yok" mantığı devreye girip 6 testin 1'i bile
        # yapılmamışken rapora yeşil "BAŞARILI" yazıyordu. Artık toplam sayım
        # yapılır: hiç sonuç yoksa veya testler eksikse ASLA BAŞARILI denmez.
        passed  = sum(1 for r in self._test_results.values() if r.get("result") == _ST_PASS)
        failed  = sum(1 for r in self._test_results.values() if r.get("result") == _ST_FAIL)
        skipped = sum(1 for r in self._test_results.values() if r.get("result") == _ST_SKIP)
        total_tests = len(self._TEST_CONFIGS)
        executed = passed + failed + skipped

        if executed == 0:
            overall = "SONUÇ YOK / HATA"
        elif failed > 0:
            overall = _ST_FAIL
        elif executed < total_tests:
            overall = f"EKSİK ({executed}/{total_tests} test yürütüldü)"
        else:
            overall = _ST_PASS

        # Rapor metni oluştur
        lines = [
            "=" * 60,
            "ASHİNA YKİ — UÇUŞ ÖNCESİ TEST RAPORU",
            f"Tarih: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts_now))}",
            f"Genel Sonuç: {overall}",
            f"Geçti: {passed}  Başarısız: {failed}  Atlandı: {skipped}  "
            f"Yürütülmedi: {total_tests - executed}",
            "=" * 60,
        ]
        for i, cfg in enumerate(self._TEST_CONFIGS):
            r = self._test_results.get(i)
            if r:
                # NEDEN (H5): Manuel testlerde YKİ komut göndermedi — sonucun
                # operatör beyanı olduğu raporda açıkça görünmelidir.
                manual_note = "  [MANUEL — operatör beyanı]" if r.get("manual_only") else ""
                lines.append(
                    f"TEST {i + 1}: {cfg[0]}{manual_note}\n"
                    f"  Sonuç: {r.get('result','?')}  Süre: {r.get('duration_s','?')}s\n"
                    # NEDEN batarya.ozet_metni: rapor, testin çalıştığı ANDAKİ
                    # pil durumunu gerilim tabanlı ölçekle yazar; veri yoksa
                    # "veri yok" der, "%0" ya da "%?" demez.
                    f"  GPS: {r.get('gps_fix_type','?')}  Bat: {batarya.ozet_metni(r)}"
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
