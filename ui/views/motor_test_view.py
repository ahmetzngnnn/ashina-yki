"""
ui/views/motor_test_view.py — Motor Testi (MAV_CMD_DO_MOTOR_TEST, cmd 209)
===========================================================================

⚠ BU PANEL MOTORLARI GERÇEKTEN DÖNDÜRÜR.

Güvenlik kapıları (hepsi ZORUNLU, hiçbiri opsiyonel değil):
  • Üstte göz ardı edilemez uyarı bloğu (PervaneKapisi),
  • "PERVANELER SÖKÜLÜ" onay kutusu işaretlenmeden TÜM test butonları KAPALI,
  • Gaz varsayılanı %5, üst sınır %25 (spin aralığı + gönderim öncesi kelepçe),
  • Süre üst sınırı 3.0 s (spin aralığı + gönderim öncesi kelepçe),
  • Her zaman görünür ve HER ZAMAN etkin ACİL DUR (durdurmak asla riskli
    değildir — bağlantı yokken bile basılabilmeli ki kullanıcı sonucu görsün),
  • Panel gizlenince/terk edilince motorlar DURDURULUR ve onay kutusu düşer,
  • Araç ARM'lıyken test butonları kapalı (ArduPilot zaten reddeder; kullanıcı
    bunu "komut gitti ama motor dönmedi" diye yorumlamasın).

═══════════════════════════════════════════════════════════════════════════
KRİTİK DÜZELTME — param1 ÇIKIŞ KANALI DEĞİL, TEST SIRASIDIR
═══════════════════════════════════════════════════════════════════════════
Eski panel (calibration_view.py `_motor_tab`) param1'i "M1..M4 çıkış kanalı"
sanıyor ve etiketleri buna göre yazıyordu ("M2 = Arka-Sol"). MAVLink ve
ArduPilot'ta param1 MOTOR TEST SIRASIDIR (Mission Planner'daki A/B/C/D).
ArduPilot AP_MotorsMatrix, QUAD X için add_motor(...) çağrılarında her çıkışa
AYRI bir test sırası verir:

    çıkış     açı      dönüş    test sırası
    MOT_1     +45°     CCW      1  (A)   → ÖN-SAĞ
    MOT_2    -135°     CCW      3  (C)   → ARKA-SOL
    MOT_3     -45°     CW       4  (D)   → ÖN-SOL
    MOT_4    +135°     CW       2  (B)   → ARKA-SAĞ

Yani test sırası saat yönünde döner: A=ön-sağ, B=arka-sağ, C=arka-sol,
D=ön-sol. Eski panelde "M2 (Arka-Sol)" düğmesi ARKA-SAĞ motoru döndürüyordu.
Motor testinin TEK amacı kablolama sırasını doğrulamak olduğu için bu, testin
YANLIŞ SONUÇ vermesi demekti: doğru bağlanmış ESC'lerin yerini değiştirmeye
yol açabilirdi. Bu panel motorları TEST SIRASIYLA (A/B/C/D) etiketler, karşılık
gelen çıkış kanalını (M1..M4) ve beklenen fiziksel konumu AYRICA gösterir.

Tablo QUAD X (FRAME_CLASS=1, FRAME_TYPE=1 — IHA/config.json fc_params_expected)
içindir. FC'den gelen FRAME_CLASS/FRAME_TYPE farklıysa panel UYARI gösterir ve
eşleme tablosunu GEÇERSİZ sayar (tahmin etmez).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDoubleSpinBox, QGridLayout, QGroupBox, QLabel, QProgressBar,
    QPushButton, QSizePolicy, QSpinBox, QSplitter, QTextEdit, QVBoxLayout,
    QWidget,
)
from pymavlink import mavutil

from core import theme
from ui.widgets.panel_ortak import MavKanal, PervaneKapisi

logger = logging.getLogger(__name__)


# ── Güvenlik sınırları — hepsi KELEPÇE, sihirli sayı değil ─────────────────
# NEDEN %5 varsayılan: ArduPilot'ta yüzde, MOT_PWM_MIN..MOT_PWM_MAX aralığının
# yüzdesidir; %5 tipik bir multirotorda pervanesiz motoru çeviren ama araca
# itki üretmeyen en düşük değerdir. Dönüş YÖNÜ ve SIRASI bu gazda görülür —
# testin amacı budur, itki ölçmek değil.
GAZ_VARSAYILAN_PCT = 5
# NEDEN %25 tavan: pervanesiz motor testinde daha fazlası gereksizdir; pervane
# YANLIŞLIKLA takılı kaldıysa (kapının atlandığı en olası arıza) %25 üstünde
# tezgâhtaki araç zıplayabilir/devrilebilir. Tavan spin aralığıyla VE gönderim
# öncesi kelepçeyle iki kez uygulanır.
GAZ_TAVAN_PCT = 25
GAZ_TABAN_PCT = 1
# NEDEN 3 s tavan: ArduPilot'un KENDİ üst sınırı 600 s'dir (ArduCopter/
# motor_test.cpp, MOTOR_TEST_TIMEOUT_SEC = 600) — yani FC hiçbir koruma
# sağlamıyor, sınırı GCS koymak zorunda. On dakika yüksüz dönen bir motor/ESC
# ısınır; dönüş yönünü ve sırasını görmek için 1-2 saniye yeter. Ayrıca motor
# testi SÜRDÜĞÜ SÜRECE ArduPilot gaz/GCS/EKF failsafe'lerini KAPATIYOR (aynı
# dosya, mavlink_motor_test_start) ve ancak test bitince geri yüklüyor —
# testin kısa olması bu penceresinin de kısa olması demek.
SURE_TAVAN_S = 3.0
SURE_TABAN_S = 0.5
SURE_VARSAYILAN_S = 1.5

# throttle tipi: 0 = yüzde (MOTOR_TEST_THROTTLE_PERCENT)
_THR_TIP_YUZDE = 0

# NEDEN param6 = 0: MAVLink şemasında param6 "test order" (0 = DEFAULT),
# ArduCopter'ın kendi handler'ında ise param6 "compass learning" (0 = kapalı).
# İki okumada da 0 DOĞRU ve ZARARSIZ değerdir — bu yüzden sabit 0 gönderiyoruz.
_PARAM6 = 0

# Test sırası (param1) → (etiket, çıkış kanalı, konum, dönüş yönü, viz indeksi)
# viz indeksi: DroneVizWidget'in _MOTOR_XY sırası (0=M1 ön-sağ, 1=M2 arka-sol,
# 2=M3 ön-sol, 3=M4 arka-sağ) — o widget ÇIKIŞ KANALI ile çizer, biz TEST
# SIRASINDAN ona çeviriyoruz.
QUAD_X_TEST_SIRASI = [
    (1, "A", "M1", "ÖN-SAĞ",   "CCW", 0),
    (2, "B", "M4", "ARKA-SAĞ", "CW",  3),
    (3, "C", "M2", "ARKA-SOL", "CCW", 1),
    (4, "D", "M3", "ÖN-SOL",   "CW",  2),
]

# Bu tablonun geçerli olduğu gövde (FC'den okunanla karşılaştırılır)
BEKLENEN_FRAME_CLASS = 1    # Quad
BEKLENEN_FRAME_TYPE = 1     # X

# STATUSTEXT severity: 0..4 = EMERGENCY..WARNING → arıza sayılır, 5+ bilgi
_SEV_ARIZA_TAVANI = 4

# ArduCopter'ın motor testi sırasında/sonunda yaydığı BİLGİ mesajları
# (ArduCopter/motor_test.cpp'den birebir doğrulandı — ezberden yazılmadı).
_ST_BASLADI = "starting motor test"
_ST_BITTI = "finished motor test"
_ST_IPTAL = "motor test: cancelled"

# DUR komutunun FC onayı ("finished motor test") bu süre içinde gelmezse tekrarlanır
DUR_ONAY_SURESI_S = 1.0
DUR_MAX_DENEME = 3


class MotorTestView(QWidget):
    """Motor testi paneli. FC'ye giden tek yol `MavKanal`dır.

    Kendi pyqtSignal'i YOKTUR — bkz. panel_ortak.MavKanal NEDEN(2/2): sinyal
    zincirlemek "komut gerçekten bağlı mı" denetimini sessizce işlevsizleştirir.
    """

    def __init__(self, kanal: MavKanal, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kanal = kanal
        self._armed = False
        self._test_aktif = False
        self._test_bitis_ts = 0.0
        self._test_toplam_s = 0.0
        self._test_aciklama = ""
        self._durum_token = "text_muted"
        # FC'den okunan gövde/PWM parametreleri (BİLİNMİYOR = None)
        self._fc_params: dict[str, float] = {}

        # Test süresini sayan ve süre dolunca DUR gönderen zamanlayıcı
        self._sayac = QTimer(self)
        self._sayac.setInterval(100)
        self._sayac.timeout.connect(self._sayac_tik)

        # DUR komutunun FC tarafından onaylandığını izleyen zamanlayıcı
        self._dur_onaylandi = True
        self._dur_deneme = 0
        self._dur_tekrar_timer = QTimer(self)
        self._dur_tekrar_timer.setSingleShot(True)
        self._dur_tekrar_timer.timeout.connect(self._dur_tekrar)

        self._build_ui()
        self._kapilari_uygula()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ══════════════════════════════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════════════════════════════
    def _build_ui(self) -> None:
        kok = QVBoxLayout(self)
        kok.setContentsMargins(8, 8, 8, 8)
        kok.setSpacing(8)

        # ── Güvenlik kapısı (en üstte, göz ardı edilemez) ──
        self._kapi = PervaneKapisi(
            ek_uyari=(
                "  5) Bataryayı, testi bitirir bitirmez AYIRIN.\n"
                "⚠ ArduPilot motor testi SÜRDÜĞÜ SÜRECE gaz (FS_THR), GCS "
                "(FS_GCS) ve EKF arıza emniyetlerini GEÇİCİ OLARAK KAPATIR ve "
                "ancak test bittiğinde geri yükler (ArduCopter/motor_test.cpp). "
                "Bu yüzden test süresi kısa tutulur ve panel süre dolunca "
                "açıkça DUR gönderir."
            )
        )
        self._kapi.durum_degisti.connect(self._on_kapi_degisti)
        kok.addWidget(self._kapi)

        # ── ACİL DUR — HER ZAMAN görünür, HER ZAMAN etkin ──
        # NEDEN her zaman etkin: durdurmak hiçbir koşulda riskli değildir.
        # Kapalı bir ACİL DUR butonu, panik anında operatörün elini bağlar.
        self._btn_acil = QPushButton("⛔  ACİL DUR — TÜM MOTORLARI DURDUR  ⛔")
        self._btn_acil.setObjectName("dangerButton")
        self._btn_acil.setMinimumHeight(52)
        self._btn_acil.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self._btn_acil.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Fixed)
        self._btn_acil.clicked.connect(self._on_acil_dur)
        kok.addWidget(self._btn_acil)

        split = QSplitter(Qt.Orientation.Horizontal)

        # ── SOL: gövde görseli + eşleme tablosu ──
        sol = QWidget()
        sl = QVBoxLayout(sol)
        sl.setContentsMargins(6, 6, 6, 6)
        # NEDEN geç import: DroneVizWidget calibration_view.py'de tanımlı ve o
        # dosya bu paneli kurar → modül seviyesinde import dairesel olurdu.
        self._viz = None
        try:
            from ui.views.calibration_view import DroneVizWidget
            self._viz = DroneVizWidget()
            sl.addWidget(self._viz, 1)
        except Exception:
            logger.exception("Motor görselleştirmesi kurulamadı — panel görselsiz çalışır")

        self._eslesme_lbl = QLabel(self._eslesme_metni())
        self._eslesme_lbl.setObjectName("hintLabel")
        self._eslesme_lbl.setWordWrap(True)
        sl.addWidget(self._eslesme_lbl)

        self._frame_lbl = QLabel("Gövde tipi: FC'den okunmadı")
        self._frame_lbl.setObjectName("mutedLabel")
        self._frame_lbl.setWordWrap(True)
        sl.addWidget(self._frame_lbl)
        split.addWidget(sol)

        # ── SAĞ: kontroller ──
        sag = QWidget()
        rl = QVBoxLayout(sag)
        rl.setContentsMargins(8, 8, 8, 8)
        rl.setSpacing(8)

        # Ayarlar
        ayar_grp = QGroupBox("Test Ayarları (kelepçeli)")
        ag = QGridLayout(ayar_grp)
        ag.addWidget(QLabel("Gaz:"), 0, 0)
        self._spin_gaz = QSpinBox()
        self._spin_gaz.setRange(GAZ_TABAN_PCT, GAZ_TAVAN_PCT)
        self._spin_gaz.setValue(GAZ_VARSAYILAN_PCT)
        self._spin_gaz.setSuffix(" %")
        self._spin_gaz.valueChanged.connect(self._pwm_tahmini_guncelle)
        ag.addWidget(self._spin_gaz, 0, 1)
        ag.addWidget(QLabel(f"(üst sınır %{GAZ_TAVAN_PCT})"), 0, 2)

        ag.addWidget(QLabel("Süre:"), 1, 0)
        self._spin_sure = QDoubleSpinBox()
        self._spin_sure.setRange(SURE_TABAN_S, SURE_TAVAN_S)
        self._spin_sure.setSingleStep(0.5)
        self._spin_sure.setDecimals(1)
        self._spin_sure.setValue(SURE_VARSAYILAN_S)
        self._spin_sure.setSuffix(" s")
        ag.addWidget(self._spin_sure, 1, 1)
        ag.addWidget(QLabel(f"(üst sınır {SURE_TAVAN_S:.1f} s)"), 1, 2)

        self._pwm_lbl = QLabel("Tahmini çıkış PWM: — (MOT_PWM_MIN/MAX okunmadı)")
        self._pwm_lbl.setObjectName("mutedLabel")
        self._pwm_lbl.setWordWrap(True)
        ag.addWidget(self._pwm_lbl, 2, 0, 1, 3)
        rl.addWidget(ayar_grp)

        # Motor butonları — TEST SIRASINA göre
        btn_grp = QGroupBox("Tek Motor Testi (test sırası — Mission Planner A/B/C/D)")
        bg = QGridLayout(btn_grp)
        self._motor_butonlari: list[QPushButton] = []
        for satir, (sira, harf, kanal, konum, yon, _viz) in enumerate(QUAD_X_TEST_SIRASI):
            btn = QPushButton(f"{harf}  ({sira})  →  {konum}   [{kanal}, {yon}]")
            btn.setObjectName("warningButton")
            btn.setMinimumHeight(34)
            btn.clicked.connect(lambda _c=False, s=sira: self._on_tek_motor(s))
            bg.addWidget(btn, satir, 0)
            self._motor_butonlari.append(btn)
        rl.addWidget(btn_grp)

        # Sıralı test
        self._btn_sirali = QPushButton("SIRAYLA HEPSİNİ TEST ET  (A → B → C → D)")
        self._btn_sirali.setObjectName("warningButton")
        self._btn_sirali.setMinimumHeight(36)
        self._btn_sirali.clicked.connect(self._on_sirali)
        rl.addWidget(self._btn_sirali)

        # İlerleme + iptal
        self._ilerleme = QProgressBar()
        self._ilerleme.setRange(0, 100)
        self._ilerleme.setValue(0)
        self._ilerleme.setFormat("Test yok")
        rl.addWidget(self._ilerleme)

        self._durum = QLabel("Durum: Beklemede")
        self._durum.setWordWrap(True)
        rl.addWidget(self._durum)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.document().setMaximumBlockCount(400)
        rl.addWidget(self._log, 1)

        split.addWidget(sag)
        split.setSizes([340, 520])
        kok.addWidget(split, 1)

        self._apply_theme_colors()

    def _eslesme_metni(self) -> str:
        satirlar = [
            "TEST SIRASI ↔ ÇIKIŞ KANALI (QUAD X):",
        ]
        for sira, harf, kanal, konum, yon, _viz in QUAD_X_TEST_SIRASI:
            satirlar.append(f"  {harf} (param1={sira})  →  {konum:<9} = {kanal}, {yon}")
        satirlar.append(
            "Beklenen: her düğmede YALNIZ yazan motor dönmeli ve dönüş yönü "
            "yazanla aynı olmalı. Farklıysa ESC/motor kablolaması yanlıştır."
        )
        return "\n".join(satirlar)

    def _apply_theme_colors(self) -> None:
        self._durum.setStyleSheet(
            f"color:{theme.c(self._durum_token)};font-size:12px;font-weight:bold;"
        )
        self._log.setStyleSheet(
            f"QTextEdit{{color:{theme.c('success')};"
            "font-family:'Courier New';font-size:11px;}"
        )
        if self._viz is not None:
            self._viz.update()

    # ══════════════════════════════════════════════════════════════════════
    # Durum / kapı yönetimi
    # ══════════════════════════════════════════════════════════════════════
    def _durum_yaz(self, metin: str, token: str = "text_muted") -> None:
        self._durum_token = token
        self._durum.setText(metin)
        self._durum.setStyleSheet(
            f"color:{theme.c(token)};font-size:12px;font-weight:bold;"
        )

    def _logla(self, metin: str) -> None:
        self._log.append(f"[{time.strftime('%H:%M:%S')}] {metin}")

    def _kapali_nedeni(self) -> str:
        """Test butonlarının NEDEN kapalı olduğunu döndürür ('' = açık)."""
        neden = self._kanal.neden_kapali()
        if neden:
            return neden
        if not self._kapi.acik():
            return ("Güvenlik kapısı KAPALI — 'PERVANELER SÖKÜLÜ' onay kutusunu "
                    "işaretleyene kadar motor testi yapılamaz")
        if self._armed:
            # NEDEN YKİ engelliyor: ArduPilot motor testini armlı-ve-yerdeyken
            # AÇIKÇA reddetmiyor (ArduCopter/motor_test.cpp yalnızca
            # ap.land_complete ve emniyet düğmesi/E-Stop denetliyor). Ama o
            # durumda test çıkışları tutum kontrolcüsünün çıkışlarıyla
            # ÇAKIŞIR ve test bitince motor_test_stop() aracı DISARM EDER.
            # Bu, uçuşa hazır bir araçta yapılabilecek en kötü şey; kapıyı
            # YKİ koyuyor.
            return ("Araç ARM'lı — motor testi tutum kontrolcüsüyle çakışır ve "
                    "test bitince FC aracı DISARM eder. Önce DISARM edin")
        if self._test_aktif:
            return "Test sürüyor — bitmesini bekleyin ya da ACİL DUR'a basın"
        if not self._gövde_uyumlu():
            return ("Gövde tipi beklenenden farklı (FRAME_CLASS/FRAME_TYPE) — "
                    "A/B/C/D eşleme tablosu bu gövde için GEÇERSİZ")
        return ""

    def _gövde_uyumlu(self) -> bool:
        """FC'den okunan gövde tipi tabloyla uyuşuyor mu.

        NEDEN "okunmadıysa uyumlu say": parametre listesi henüz indirilmemiş
        olabilir; paneli okunmamış bir değer yüzünden kilitlemek, panelin hiç
        kullanılamaması demek olurdu. Uyuşmazlık ANCAK gerçekten okunduğunda
        kapatır — okunmadığında ise etiket "okunmadı" der (tahmin edilmez).
        """
        fc = self._fc_params.get("FRAME_CLASS")
        ft = self._fc_params.get("FRAME_TYPE")
        if fc is None or ft is None:
            return True
        return int(fc) == BEKLENEN_FRAME_CLASS and int(ft) == BEKLENEN_FRAME_TYPE

    def _kapilari_uygula(self, durum_yaz: bool = True) -> None:
        """Butonların açık/kapalı durumunu ve (istenirse) durum metnini yazar.

        NEDEN `durum_yaz` bayrağı: bu metot hem test bitiminde hem de her
        durum değişiminde çağrılıyor. Koşulsuz metin yazsaydı, FC'nin
        "Motor Test: vehicle not landed" reddi ekrana yazıldıktan HEMEN SONRA
        "HAZIR" ile ÜZERİ ÇİZİLİRDİ — operatör reddi hiç görmezdi. (Bu hata
        ilk sürümde gerçekten yaşandı, tezgâh testinde yakalandı.)
        """
        neden = self._kapali_nedeni()
        acik = not neden
        for btn in self._motor_butonlari:
            btn.setEnabled(acik)
            btn.setToolTip(neden)
        self._btn_sirali.setEnabled(acik)
        self._btn_sirali.setToolTip(neden)
        # Ayar kutuları test sırasında kilitli (koşan testin parametresi değişmez)
        self._spin_gaz.setEnabled(not self._test_aktif)
        self._spin_sure.setEnabled(not self._test_aktif)

        if self._test_aktif or not durum_yaz:
            return
        if neden:
            self._durum_yaz(f"KAPALI — {neden}", "warning")
        else:
            self._durum_yaz(
                "HAZIR — pervanesiz test yapılabilir. Emniyet düğmesine basmayı unutmayın.",
                "success",
            )

    def _on_kapi_degisti(self, acik: bool) -> None:
        if not acik and self._test_aktif:
            # Kapı kapandıysa süren testi de durdur — beyan geri çekildi.
            # _durdur_gonder kendi içinde kapıları uygular; burada tekrar
            # çağırmıyoruz, yoksa DUR mesajının üzerine yazardık.
            self._logla("[KAPI] Onay geri çekildi — süren test durduruluyor")
            self._durdur_gonder("güvenlik kapısı kapandı")
            return
        self._kapilari_uygula()

    # ══════════════════════════════════════════════════════════════════════
    # Dışarıdan beslenen bilgiler
    # ══════════════════════════════════════════════════════════════════════
    @pyqtSlot(bool)
    def on_connection_changed(self, bagli: bool) -> None:
        if not bagli and self._test_aktif:
            # Link koptuysa test durumunu artık bilemeyiz — UI'yı temizle ama
            # "durduruldu" DEME: komut karşıya ulaşamaz.
            # _test_bitir kapıları zaten uyguluyor; mesajı ezmemek için dönüyoruz.
            self._test_bitir(
                "⚠ Bağlantı KOPTU — motorun durup durmadığı DOĞRULANAMIYOR. "
                "Bataryayı fiziksel olarak AYIRIN.",
                "danger",
            )
            return
        self._kapilari_uygula()

    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        armed = bool(data.get("armed", False))
        if armed != self._armed:
            self._armed = armed
            self._kapilari_uygula()

    @pyqtSlot(str, float)
    def on_param(self, ad: str, deger: float) -> None:
        if ad in ("FRAME_CLASS", "FRAME_TYPE", "MOT_PWM_MIN", "MOT_PWM_MAX",
                  "RC3_MIN", "RC3_MAX"):
            self._fc_params[ad] = float(deger)
            if ad in ("FRAME_CLASS", "FRAME_TYPE"):
                self._frame_bilgisi_guncelle()
                self._kapilari_uygula()
            else:
                self._pwm_tahmini_guncelle()

    def _frame_bilgisi_guncelle(self) -> None:
        fc = self._fc_params.get("FRAME_CLASS")
        ft = self._fc_params.get("FRAME_TYPE")
        if fc is None or ft is None:
            self._frame_lbl.setText("Gövde tipi: FC'den okunmadı "
                                    "(Uçuş Öncesi Kontrol sekmesinden okutun)")
            self._frame_lbl.setObjectName("mutedLabel")
        elif self._gövde_uyumlu():
            self._frame_lbl.setText(
                f"Gövde tipi: FRAME_CLASS={int(fc)} FRAME_TYPE={int(ft)} → QUAD X ✓ "
                "(A/B/C/D eşlemesi geçerli)"
            )
            self._frame_lbl.setObjectName("successText")
        else:
            self._frame_lbl.setText(
                f"⚠ Gövde tipi FRAME_CLASS={int(fc)} FRAME_TYPE={int(ft)} — bu panelin "
                f"A/B/C/D eşleme tablosu YALNIZCA QUAD X "
                f"(={BEKLENEN_FRAME_CLASS}/{BEKLENEN_FRAME_TYPE}) içindir. "
                "Test SIRASI bu gövdede FARKLI olabilir; panel kilitlendi."
            )
            self._frame_lbl.setObjectName("dangerText")
        # objectName değişince QSS yeniden uygulanmalı
        self._frame_lbl.style().unpolish(self._frame_lbl)
        self._frame_lbl.style().polish(self._frame_lbl)

    def _pwm_tahmini_guncelle(self) -> None:
        """Gaz yüzdesinin PWM karşılığını — ANCAK gerçekten hesaplanabiliyorsa — gösterir.

        NEDEN: ArduPilot yüzdeyi `pwm_min + (pwm_max - pwm_min) * %` ile çevirir.
        pwm_min/max, MOT_PWM_MIN/MOT_PWM_MAX ikisi de >0 ve MAX>MIN ise onlardan,
        aksi hâlde RC3_MIN/RC3_MAX'ten gelir (AP_MotorsMulticopter::
        get_pwm_output_min/max). Bu dört parametre okunmadan verilecek her sayı
        TAHMİN olurdu — o yüzden "—" gösterip nedenini yazıyoruz.
        """
        mn = self._fc_params.get("MOT_PWM_MIN")
        mx = self._fc_params.get("MOT_PWM_MAX")
        if mn is not None and mx is not None and mn > 0 and mx > 0 and mx > mn:
            lo, hi, kaynak = mn, mx, "MOT_PWM_MIN/MAX"
        else:
            lo = self._fc_params.get("RC3_MIN")
            hi = self._fc_params.get("RC3_MAX")
            kaynak = "RC3_MIN/MAX (MOT_PWM_* tanımsız)"
            if lo is None or hi is None or hi <= lo:
                self._pwm_lbl.setText(
                    "Tahmini çıkış PWM: — (MOT_PWM_MIN/MAX ve RC3_MIN/MAX "
                    "okunmadı — ÖLÇÜLMEDİ, tahmin edilmiyor)"
                )
                return
        pct = self._spin_gaz.value()
        pwm = lo + (hi - lo) * (pct / 100.0)
        self._pwm_lbl.setText(
            f"Tahmini çıkış PWM: %{pct} → {pwm:.0f} µs   "
            f"(aralık {lo:.0f}-{hi:.0f}, kaynak: {kaynak})"
        )

    # ══════════════════════════════════════════════════════════════════════
    # Komut gönderimi
    # ══════════════════════════════════════════════════════════════════════
    @staticmethod
    def _ml():
        """mavutil.mavlink dialect'i (yüklü değilse yükler)."""
        if getattr(mavutil, "mavlink", None) is None:
            mavutil.set_dialect("ardupilotmega")
        return mavutil.mavlink

    def _gaz(self) -> int:
        # Gönderim öncesi İKİNCİ kelepçe (spin aralığı ilk kelepçe).
        return max(GAZ_TABAN_PCT, min(GAZ_TAVAN_PCT, int(self._spin_gaz.value())))

    def _sure(self) -> float:
        return max(SURE_TABAN_S, min(SURE_TAVAN_S, float(self._spin_sure.value())))

    def _on_tek_motor(self, sira: int) -> None:
        neden = self._kapali_nedeni()
        if neden:
            self._durum_yaz(f"KAPALI — {neden}", "danger")
            return
        gaz, sure = self._gaz(), self._sure()
        harf, kanal, konum, yon = self._sira_bilgi(sira)
        ok = self._kanal.komut(
            self._ml().MAV_CMD_DO_MOTOR_TEST,
            # [test sırası, gaz tipi, gaz, süre, motor sayısı, param6, 0]
            [sira, _THR_TIP_YUZDE, gaz, sure, 0, _PARAM6, 0],
        )
        self._logla(
            f"[GÖNDER] DO_MOTOR_TEST sıra={sira} ({harf}={konum}/{kanal}) "
            f"gaz=%{gaz} süre={sure:.1f}s"
        )
        if not ok:
            self._durum_yaz(
                "✗ Komut GÖNDERİLEMEDİ — " + self._kanal.neden_kapali(), "danger"
            )
            return
        self._viz_ayarla(sira, gaz)
        self._test_basla(
            sure, f"{harf} ({konum}, {kanal}, {yon}) — %{gaz}"
        )

    def _on_sirali(self) -> None:
        neden = self._kapali_nedeni()
        if neden:
            self._durum_yaz(f"KAPALI — {neden}", "danger")
            return
        gaz, sure = self._gaz(), self._sure()
        adet = len(QUAD_X_TEST_SIRASI)
        # NEDEN tek komut: ArduPilot param5 (motor sayısı) ile sıralamayı KENDİ
        # yürütür (her motor `süre` kadar, aralarında bekleyerek). YKİ'den 4 ayrı
        # komut göndermek, ikinci komutun birinciyi ANINDA iptal etmesi demekti —
        # eski panelin "Hepsini Test Et" düğmesindeki hata tam olarak buydu:
        # 4 komut arka arkaya gidiyor ve pratikte SADECE SONUNCU motor dönüyordu.
        ok = self._kanal.komut(
            self._ml().MAV_CMD_DO_MOTOR_TEST,
            [1, _THR_TIP_YUZDE, gaz, sure, adet, _PARAM6, 0],
        )
        self._logla(
            f"[GÖNDER] DO_MOTOR_TEST SIRALI başlangıç=1 adet={adet} "
            f"gaz=%{gaz} süre={sure:.1f}s/motor"
        )
        if not ok:
            self._durum_yaz(
                "✗ Komut GÖNDERİLEMEDİ — " + self._kanal.neden_kapali(), "danger"
            )
            return
        self._viz_ayarla(None, gaz)
        self._test_basla(
            self._sirali_toplam_sure(sure, adet),
            f"SIRALI A→D — %{gaz}, motor başına {sure:.1f} s",
        )

    @staticmethod
    def _sirali_toplam_sure(sure: float, adet: int) -> float:
        """Sıralı testin GERÇEK toplam süresi.

        NEDEN düz `sure*adet` DEĞİL: ArduPilot (AP_MotorsTest::motor_test_output)
        her motoru `sure` kadar döndürdükten sonra bir sonrakine geçmeden önce
        0.5×`sure` boyunca çıkışı sıfırda tutar; son motorda bu bekleme yoktur.
        Yani toplam = (adet-1)·1.5·sure + sure. Düz çarpımla hesaplasaydık
        ilerleme çubuğu test HÂLÂ SÜRERKEN dolar ve panel erken "DUR" gönderip
        sıralamayı yarıda keserdi.
        """
        if adet <= 1:
            return sure
        return (adet - 1) * 1.5 * sure + sure

    def _on_acil_dur(self) -> None:
        self._durdur_gonder("ACİL DUR")

    def _durdur_gonder(self, sebep: str) -> None:
        """Motor testini durdurur: gaz %0, süre 0.

        NEDEN süre 0: ArduCopter motor_test_timeout_ms'i 0 yapar ve bir sonraki
        motor_test_output() çağrısında testi ANINDA sonlandırır (motor_test_stop),
        aracı DISARM eder ve failsafe'leri geri yükler. Mission Planner'ın "Stop"
        düğmesi de aynı yolu kullanır.

        NEDEN TEK komut (dördü birden DEĞİL): İlk sürüm dört motora ayrı ayrı DUR
        gönderiyordu. ArduCopter kaynağı okununca görüldü ki motor testi FC'de
        TEK bir global durumdur (ap.motor_test): ilk komut testi durdurur, geri
        kalan ÜÇÜ `!ap.motor_test` gördüğü için testi YENİDEN BAŞLATIR
        ("starting motor test" + motorları tekrar arm etme + bir sonraki tikte
        tekrar durdurma). Yani "daha güvenli olsun" diye eklenen tekrarlar
        motorları üç kez daha arm ediyordu. Tek komut gönderiliyor; paket kaybına
        karşı koruma, FC'nin "finished motor test" mesajı GELMEZSE yapılan
        SINIRLI tekrar (_dur_tekrar_timer) ile sağlanıyor.
        """
        ml = self._ml()
        gonderildi = self._kanal.komut(
            ml.MAV_CMD_DO_MOTOR_TEST,
            [1, _THR_TIP_YUZDE, 0, 0, 0, _PARAM6, 0],
        )
        self._viz_temizle()
        self._logla(f"[DUR] ({sebep}) DO_MOTOR_TEST gaz %0 / süre 0 gönderildi")
        if gonderildi:
            # FC'nin "finished motor test" onayını bekle; gelmezse tekrar dene.
            self._dur_onaylandi = False
            self._dur_deneme = 1
            self._dur_tekrar_timer.start(int(DUR_ONAY_SURESI_S * 1000))
            self._test_bitir(
                f"DUR komutu gönderildi ({sebep}) — FC onayı ('finished motor "
                "test') bekleniyor",
                "warning",
            )
        else:
            self._test_bitir(
                "✗ DUR komutu GÖNDERİLEMEDİ — " + self._kanal.neden_kapali()
                + ".  BATARYAYI FİZİKSEL OLARAK AYIRIN.",
                "danger",
            )

    def _dur_tekrar(self) -> None:
        """FC 'finished motor test' demediyse DUR komutunu sınırlı kez tekrarlar."""
        if self._dur_onaylandi:
            return
        if self._dur_deneme >= DUR_MAX_DENEME:
            self._durum_yaz(
                "⚠ DUR komutu gönderildi ama FC ONAYLAMADI "
                f"({DUR_MAX_DENEME} deneme). Motorun durduğu DOĞRULANAMIYOR — "
                "aracı gözle kontrol edin, gerekirse BATARYAYI AYIRIN. "
                f"(FC testi kendi de en geç {SURE_TAVAN_S:.0f} s'de bitirir.)",
                "danger",
            )
            return
        self._dur_deneme += 1
        self._kanal.komut(
            self._ml().MAV_CMD_DO_MOTOR_TEST,
            [1, _THR_TIP_YUZDE, 0, 0, 0, _PARAM6, 0],
        )
        self._logla(f"[DUR] FC onayı yok — tekrar {self._dur_deneme}/{DUR_MAX_DENEME}")
        self._dur_tekrar_timer.start(int(DUR_ONAY_SURESI_S * 1000))

    # ── Süre takibi ──────────────────────────────────────────────────────
    def _test_basla(self, toplam_s: float, aciklama: str) -> None:
        self._test_aktif = True
        self._test_toplam_s = max(0.1, float(toplam_s))
        self._test_bitis_ts = time.monotonic() + self._test_toplam_s
        self._test_aciklama = aciklama
        self._ilerleme.setRange(0, 100)
        self._ilerleme.setValue(0)
        self._sayac.start()
        self._durum_yaz(f"TEST SÜRÜYOR — {aciklama}", "warning")
        self._kapilari_uygula()

    def _sayac_tik(self) -> None:
        kalan = self._test_bitis_ts - time.monotonic()
        if kalan <= 0:
            # NEDEN burada DA DUR gönderiyoruz: ArduPilot süre dolunca testi
            # kendi bitirir, ama bu YKİ'nin doğrulayabildiği bir şey değil
            # (COMMAND_ACK kanalı yok). Açık bir DUR, "süre bitti ama motor
            # dönüyor" senaryosunu ortadan kaldırır.
            self._sayac.stop()
            self._durdur_gonder("süre doldu")
            return
        gecen = self._test_toplam_s - kalan
        self._ilerleme.setValue(int(100 * gecen / self._test_toplam_s))
        self._ilerleme.setFormat(f"{self._test_aciklama} — kalan {kalan:.1f} s")

    def _test_bitir(self, metin: str, token: str) -> None:
        self._sayac.stop()
        self._test_aktif = False
        self._ilerleme.setValue(0)
        self._ilerleme.setFormat("Test yok")
        # Önce kapılar (metin YAZDIRMADAN), sonra asıl mesaj — aksi hâlde
        # kapı metni FC'nin ret/hata mesajının üzerine yazar.
        self._kapilari_uygula(durum_yaz=False)
        self._durum_yaz(metin, token)

    # ── Görselleştirme ───────────────────────────────────────────────────
    def _sira_bilgi(self, sira: int):
        for s, harf, kanal, konum, yon, _viz in QUAD_X_TEST_SIRASI:
            if s == sira:
                return harf, kanal, konum, yon
        return "?", "?", "?", "?"

    def _viz_ayarla(self, sira: Optional[int], gaz: int) -> None:
        if self._viz is None:
            return
        for s, _h, _k, _kon, _y, viz_idx in QUAD_X_TEST_SIRASI:
            aktif = (sira is None) or (s == sira)
            self._viz.set_throttle(viz_idx, gaz if aktif else 0)
        if sira is not None:
            for s, _h, _k, _kon, _y, viz_idx in QUAD_X_TEST_SIRASI:
                if s == sira:
                    self._viz.set_highlight(viz_idx + 1)
                    break
        else:
            self._viz.set_highlight(-1)
        self._viz.start_spin()

    def _viz_temizle(self) -> None:
        if self._viz is None:
            return
        for _s, _h, _k, _kon, _y, viz_idx in QUAD_X_TEST_SIRASI:
            self._viz.set_throttle(viz_idx, 0)
        self._viz.set_highlight(-1)
        self._viz.stop_spin()

    # ══════════════════════════════════════════════════════════════════════
    # FC geri bildirimi
    # ══════════════════════════════════════════════════════════════════════
    @pyqtSlot(str, int)
    def on_status_text(self, text: str, severity: int) -> None:
        """FC STATUSTEXT'inden motor testine ait olanları yakalar.

        NEDEN tek doğrulama yolu bu: mavlink_worker.py COMMAND_ACK'i HİÇ
        işlemiyor (o dosya bu görevin kapsamı dışında — bkz. rapor). ArduPilot
        motor testinin ön koşul hatalarını ("...not landed", "Already armed",
        "Safety switch", "RC not calibrated") STATUSTEXT ile bildirir; burada
        yakalanıp panele YAZILIR. Metinler ezberden EŞLEŞTİRİLMEZ — FC ne
        yazdıysa aynen gösterilir, severity ile arıza/bilgi ayrımı yapılır.
        """
        if not text:
            return
        low = text.strip().lower()
        if "motor test" not in low and "prearm" not in low and "arm" not in low:
            return
        self._logla(f"[FC sev={severity}] {text.strip()}")

        # ── FC testi GERÇEKTEN bitirdi (tek kesin durma kanıtı) ──
        if _ST_BITTI in low:
            self._dur_onaylandi = True
            self._dur_tekrar_timer.stop()
            self._viz_temizle()
            self._test_bitir("✓ FC ONAYLADI: motor testi bitti, motorlar durdu",
                             "success")
            return
        if _ST_BASLADI in low:
            self._durum_yaz(f"FC testi başlattı — {self._test_aciklama}", "warning")
            return
        if _ST_IPTAL in low:
            self._viz_temizle()
            self._test_bitir(
                "FC testi iptal etti (istenen motor sırası bu gövdede yok?) — "
                f"FC: {text.strip()}",
                "warning",
            )
            return
        # ── Ön koşul reddi (severity ERROR/CRITICAL/WARNING) ──
        if "motor test" in low and severity <= _SEV_ARIZA_TAVANI:
            self._viz_temizle()
            self._test_bitir(f"✗ FC REDDETTİ: {text.strip()}", "danger")

    # ══════════════════════════════════════════════════════════════════════
    # Panel terk edilince
    # ══════════════════════════════════════════════════════════════════════
    def panel_terk_edildi(self) -> None:
        """Panelden çıkılınca/sekme değişince motorları durdurur ve kapıyı kapatır.

        Şartname gereği ("panelden çıkılınca motorlar DURDURULUR") ve pratik
        gereklilik: operatör başka sekmeye geçtiğinde bu paneli göremez, dönen
        bir motoru fark edemez.
        """
        if self._test_aktif:
            self._durdur_gonder("panel terk edildi")
        else:
            self._viz_temizle()
        self._sayac.stop()
        self._kapi.sifirla()
        self._kapilari_uygula()

    def hideEvent(self, event):  # noqa: N802 (Qt API)
        # QTabWidget sekme değiştirirken sayfayı setVisible(False) yapar →
        # burası hem iç sekme değişiminde hem ana sekme değişiminde çalışır.
        try:
            self.panel_terk_edildi()
        except Exception:
            logger.exception("Motor paneli terk edilirken hata")
        super().hideEvent(event)
