"""
ui/views/flight_modes_view.py — Uçuş Modları Ayarlama (FLTMODE1..6 + FLTMODE_CH)
================================================================================

Ne yapar:
  • FC'deki FLTMODE1..FLTMODE6 ve FLTMODE_CH değerlerini OKUR ve gösterir,
  • Her slot için açılır listeden yeni mod seçtirir,
  • set_param ile yazar ve PARAM_VALUE ile GERİ OKUYARAK doğrular (bkz.
    panel_ortak.ParamYazici — PARAM_SET'in ACK'i yoktur),
  • Mod anahtarının PWM aralıklarını ve O AN hangi slotun aktif olduğunu
    canlı gösterir.

PWM ARALIKLARI — nereden geliyor:
ArduPilot RC_Channel::get_mode_switch_pos() sabit eşiklerle çalışır; bu eşikler
parametreyle DEĞİŞTİRİLEMEZ, kod içinde sabittir:
      ≤1230 → FLTMODE1     1231-1360 → FLTMODE2   1361-1490 → FLTMODE3
   1491-1620 → FLTMODE4    1621-1749 → FLTMODE5      ≥1750 → FLTMODE6
Bu yüzden burada da SABİT tablo olarak duruyor — config'den gelemez, çünkü
FC tarafında da config'den gelmiyor.

MOD NUMARALARI — nereden geliyor:
pymavlink `ardupilotmega.COPTER_MODE_*` enum'undan DOĞRULANARAK alındı
(ezberden yazılmadı). 8, 10 ve 12 numaralar ArduCopter'da KULLANIM DIŞIDIR
(eski POSITION / OF_LOITER / LAND öncesi yuvalar) — listede yoktur.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from core import theme
from ui.widgets.panel_ortak import (
    MavKanal, ParamDeposu, ParamYazici, TekerlekKorumasi, param_esit,
    tekerlek_korumasi_kur,
)

logger = logging.getLogger(__name__)


# ── Mod numarası → (kısa ad, açıklama) ─────────────────────────────────────
# Kaynak: pymavlink ardupilotmega COPTER_MODE_* + ArduCopter FLTMODE1 parametre
# meta verisi. GPS gerektiren modlar açıklamada işaretli — TEKNOFEST alanında
# GPS kalitesi düşerse bu modlar mod anahtarından SEÇİLSE BİLE reddedilir.
COPTER_MODLARI: list[tuple[int, str, str]] = [
    (0,  "STABILIZE",     "Elle kumanda, açı stabilize. GPS gerekmez."),
    (1,  "ACRO",          "Açısal hız kontrolü (akrobasi). GPS gerekmez."),
    (2,  "ALT_HOLD",      "İrtifa tut, konum elle. Barometre/TOF; GPS gerekmez."),
    (3,  "AUTO",          "Yüklü görev planını uçar. GPS GEREKİR."),
    (4,  "GUIDED",        "Dış bilgisayar/YKİ hedef verir. GPS GEREKİR."),
    (5,  "LOITER",        "Konum + irtifa tut. GPS GEREKİR."),
    (6,  "RTL",           "Eve dön ve in. GPS GEREKİR."),
    (7,  "CIRCLE",        "Bir noktanın etrafında daire. GPS GEREKİR."),
    (9,  "LAND",          "Bulunduğu yerde in. GPS gerekmez."),
    (11, "DRIFT",         "Koordineli dönüşlü elle uçuş. GPS GEREKİR."),
    (13, "SPORT",         "Hız sınırlı açı modu. GPS gerekmez."),
    (14, "FLIP",          "Takla. YARIŞMA ARACINDA KULLANMAYIN."),
    (15, "AUTOTUNE",      "PID otomatik ayar. Geniş açık alan ister."),
    (16, "POSHOLD",       "Konum tut + elle müdahale. GPS GEREKİR."),
    (17, "BRAKE",         "Anında dur ve konumu koru. GPS GEREKİR."),
    (18, "THROW",         "Elle fırlatarak kalkış. GPS GEREKİR."),
    (19, "AVOID_ADSB",    "ADS-B kaçınma (ADS-B alıcısı ister)."),
    (20, "GUIDED_NOGPS",  "GUIDED'ın GPS'siz açı sürümü."),
    (21, "SMART_RTL",     "Gidilen yoldan geri dönerek eve gel. GPS GEREKİR."),
    (22, "FLOWHOLD",      "Optik akış ile konum tut (optik akış sensörü ister)."),
    (23, "FOLLOW",        "Başka bir aracı takip et. GPS GEREKİR."),
    (24, "ZIGZAG",        "İki nokta arası zikzak. GPS GEREKİR."),
    (25, "SYSTEMID",      "Sistem tanımlama (uçuş testi). Uzman kullanımı."),
    (26, "AUTOROTATE",    "Helikopter otorotasyonu — MULTIROTORDA KULLANILMAZ."),
    (27, "AUTO_RTL",      "Görev içi DO_LAND_START'a git. Mod anahtarından "
                          "SEÇİLMESİ ÖNERİLMEZ (görev mantığıyla tetiklenir)."),
    (28, "TURTLE",        "Ters dönmüş aracı çevirir. PERVANE TAKILIYKEN "
                          "TEHLİKELİ."),
]

MOD_ADI = {no: ad for no, ad, _a in COPTER_MODLARI}

# Slot → (parametre adı, PWM aralığı metni, alt sınır, üst sınır)
# NEDEN sabit: bkz. modül başlığı — ArduPilot'ta bu eşikler koda gömülüdür.
FLTMODE_SLOTLARI = [
    ("FLTMODE1", "PWM ≤ 1230",      0,    1230),
    ("FLTMODE2", "PWM 1231 - 1360", 1231, 1360),
    ("FLTMODE3", "PWM 1361 - 1490", 1361, 1490),
    ("FLTMODE4", "PWM 1491 - 1620", 1491, 1620),
    ("FLTMODE5", "PWM 1621 - 1749", 1621, 1749),
    ("FLTMODE6", "PWM ≥ 1750",      1750, 3000),
]

# FLTMODE_CH için geçerli değerler.
# Kaynak: ArduCopter/Parameters.cpp @Values —
#   0:Disabled, 5..15:Channel5..Channel15  (DOĞRULANDI, ezberden yazılmadı;
#   ilk taslakta yalnızca 5-8 yazılmıştı ve bu YANLIŞTI.)
FLTMODE_CH_SECENEKLERI = [(0, "Devre dışı (mod anahtarı yok)")] + [
    (n, f"Kanal {n}") for n in range(5, 16)
]

# Canlı PWM gösterilebilen kanallar.
# NEDEN sınırlı: core/mavlink_worker.py RC_CHANNELS mesajından yalnızca
# chan1..chan8'i telemetriye yazıyor (rc_1..rc_8). 9-15 için canlı PWM YOK —
# panel bunu uydurmaz, "gösterilemiyor" der. Genişletmek mavlink_worker.py
# değişikliği gerektirir (bkz. rapor).
CANLI_PWM_KANALLARI = (5, 6, 7, 8)


class FlightModesView(QWidget):
    """FLTMODE1..6 + FLTMODE_CH okuma/yazma/doğrulama paneli."""

    def __init__(self, kanal: MavKanal, depo: ParamDeposu,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kanal = kanal
        self._depo = depo
        self._yazici = ParamYazici(kanal, self)
        self._yazici.ilerleme.connect(self._on_yazma_ilerleme)
        self._yazici.sonuc.connect(self._on_yazma_sonuc)
        self._yazici.bitti.connect(self._on_yazma_bitti)

        self._combo_slot: dict[str, QComboBox] = {}
        self._lbl_fc: dict[str, QLabel] = {}
        self._lbl_durum: dict[str, QLabel] = {}
        self._lbl_pwm: dict[str, QLabel] = {}
        self._aktif_slot = -1
        self._durum_token = "text_muted"
        self._tekerlek = TekerlekKorumasi(self)
        # Kullanıcının ELLE değiştirdiği slotlar.
        # NEDEN (KRİTİK HATA DÜZELTMESİ): ilk sürümde açılır listeler FC'den
        # parametre geldikçe GÜNCELLENMİYORDU; hepsi indeks 0'da (= mod 0,
        # STABILIZE) kalıyordu. "YAZ" düğmesi de "seçili değer FC'dekinden
        # farklıysa yaz" mantığıyla çalıştığı için, kullanıcı tek bir slotu
        # değiştirse bile DİĞER BEŞ SLOTU ve FLTMODE_CH'yi sessizce 0'a
        # çekiyordu — yani tüm uçuş modlarını STABILIZE yapıp mod anahtarını
        # DEVRE DIŞI bırakıyordu. Tezgâh testinde yakalandı. Artık: FC'den
        # gelen değer, kullanıcının ELLE dokunmadığı her slota yansıtılır ve
        # yazma yalnızca gerçekten farklı olanları kapsar.
        self._kullanici_degistirdi: set[str] = set()

        self._build_ui()
        tekerlek_korumasi_kur(self, self._tekerlek)
        self._depo.degisti.connect(self.on_param)
        self._depo.durum.connect(self._depo_durum)
        self._fc_degerlerini_yansit()
        self._kapilari_uygula()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ══════════════════════════════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════════════════════════════
    def _build_ui(self) -> None:
        kok = QVBoxLayout(self)
        kok.setContentsMargins(8, 8, 8, 8)
        kok.setSpacing(8)

        bilgi = QLabel(
            "Mod anahtarı (FLTMODE_CH) kanalının PWM'i 6 sabit bölgeye düşer; her "
            "bölge FLTMODE1..6 slotlarından birine karşılık gelir. Bu eşikler "
            "ArduPilot'ta KODA GÖMÜLÜDÜR, parametreyle değiştirilemez.\n"
            "Yazılan her değer, FC'den GERİ OKUNARAK doğrulanır — PARAM_SET'in "
            "MAVLink'te ACK'i yoktur, 'gönderildi' 'yazıldı' demek DEĞİLDİR."
        )
        bilgi.setObjectName("hintLabel")
        bilgi.setWordWrap(True)
        kok.addWidget(bilgi)

        # ── Mod anahtarı kanalı ──
        ch_grp = QGroupBox("Mod Anahtarı Kanalı (FLTMODE_CH)")
        cg = QHBoxLayout(ch_grp)
        cg.addWidget(QLabel("Kanal:"))
        self._combo_ch = QComboBox()
        for deger, ad in FLTMODE_CH_SECENEKLERI:
            self._combo_ch.addItem(f"{ad}  [{deger}]", deger)
        # NEDEN `activated`: `currentIndexChanged` programatik değişimde de
        # tetiklenir; kullanıcı düzenlemesini ondan ayırt edemezdik.
        self._combo_ch.activated.connect(
            lambda _i: self._kullanici_secti("FLTMODE_CH")
        )
        cg.addWidget(self._combo_ch)
        self._lbl_ch_fc = QLabel("FC'deki: okunmadı")
        self._lbl_ch_fc.setObjectName("mutedLabel")
        cg.addWidget(self._lbl_ch_fc)
        self._lbl_ch_pwm = QLabel("Canlı PWM: —")
        self._lbl_ch_pwm.setObjectName("monoLabel")
        cg.addWidget(self._lbl_ch_pwm)
        cg.addStretch()
        kok.addWidget(ch_grp)

        # ── Slotlar ──
        slot_grp = QGroupBox("Uçuş Modu Slotları")
        sg = QGridLayout(slot_grp)
        basliklar = ["Slot", "PWM aralığı", "FC'deki değer", "Yeni mod", "Durum"]
        for sutun, metin in enumerate(basliklar):
            lbl = QLabel(metin)
            lbl.setObjectName("sectionTitle")
            sg.addWidget(lbl, 0, sutun)

        for satir, (ad, pwm_metni, _lo, _hi) in enumerate(FLTMODE_SLOTLARI, start=1):
            ad_lbl = QLabel(ad)
            ad_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            sg.addWidget(ad_lbl, satir, 0)

            pwm_lbl = QLabel(pwm_metni)
            pwm_lbl.setObjectName("monoLabel")
            self._lbl_pwm[ad] = pwm_lbl
            sg.addWidget(pwm_lbl, satir, 1)

            fc_lbl = QLabel("okunmadı")
            fc_lbl.setObjectName("mutedLabel")
            self._lbl_fc[ad] = fc_lbl
            sg.addWidget(fc_lbl, satir, 2)

            combo = QComboBox()
            for no, kisa, aciklama in COPTER_MODLARI:
                combo.addItem(f"{no:>2} — {kisa}", no)
                combo.setItemData(combo.count() - 1, aciklama, Qt.ItemDataRole.ToolTipRole)
            combo.setMinimumWidth(190)
            combo.activated.connect(
                lambda _i, _ad=ad: self._kullanici_secti(_ad)
            )
            self._combo_slot[ad] = combo
            sg.addWidget(combo, satir, 3)

            durum_lbl = QLabel("—")
            durum_lbl.setMinimumWidth(230)
            durum_lbl.setWordWrap(True)
            self._lbl_durum[ad] = durum_lbl
            sg.addWidget(durum_lbl, satir, 4)

        sg.setColumnStretch(4, 1)
        kok.addWidget(slot_grp)

        # ── Canlı durum ──
        self._canli_lbl = QLabel("Canlı: uçuş modu —   |   aktif slot —")
        self._canli_lbl.setObjectName("valueLabel")
        kok.addWidget(self._canli_lbl)

        # ── Butonlar ──
        btn_satir = QHBoxLayout()
        self._btn_oku = QPushButton("FC'den OKU (tam parametre listesi)")
        self._btn_oku.setObjectName("primaryButton")
        self._btn_oku.setMinimumHeight(32)
        self._btn_oku.clicked.connect(self._on_oku)
        btn_satir.addWidget(self._btn_oku)

        self._btn_yaz = QPushButton("YAZ ve DOĞRULA")
        self._btn_yaz.setObjectName("successButton")
        self._btn_yaz.setMinimumHeight(32)
        self._btn_yaz.clicked.connect(self._on_yaz)
        btn_satir.addWidget(self._btn_yaz)

        self._btn_iptal = QPushButton("İptal")
        self._btn_iptal.setObjectName("dangerButton")
        self._btn_iptal.setMinimumHeight(32)
        self._btn_iptal.setEnabled(False)
        self._btn_iptal.clicked.connect(self._on_iptal)
        btn_satir.addWidget(self._btn_iptal)

        self._btn_geri = QPushButton("Seçimleri FC'deki değerlere döndür")
        self._btn_geri.setMinimumHeight(32)
        self._btn_geri.clicked.connect(self._fc_degerlerini_yansit)
        btn_satir.addWidget(self._btn_geri)
        kok.addLayout(btn_satir)

        self._ilerleme = QProgressBar()
        self._ilerleme.setRange(0, 100)
        self._ilerleme.setValue(0)
        self._ilerleme.setFormat("İşlem yok")
        kok.addWidget(self._ilerleme)

        self._durum = QLabel("Durum: hazır")
        self._durum.setWordWrap(True)
        kok.addWidget(self._durum)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.document().setMaximumBlockCount(400)
        kok.addWidget(self._log, 1)

        self._apply_theme_colors()

    def _apply_theme_colors(self) -> None:
        self._durum.setStyleSheet(f"color:{theme.c(self._durum_token)};font-size:12px;")
        self._log.setStyleSheet(
            f"QTextEdit{{color:{theme.c('success')};"
            "font-family:'Courier New';font-size:11px;}"
        )
        # Satır durum etiketlerinin rengi tema değişince bayatlamasın diye
        # saklanan token'dan yeniden yazılır.
        for ad, lbl in self._lbl_durum.items():
            token = lbl.property("tema_token") or "text_muted"
            lbl.setStyleSheet(f"color:{theme.c(token)};")

    # ══════════════════════════════════════════════════════════════════════
    # Yardımcılar
    # ══════════════════════════════════════════════════════════════════════
    def _durum_yaz(self, metin: str, token: str = "text_muted") -> None:
        self._durum_token = token
        self._durum.setText(metin)
        self._durum.setStyleSheet(f"color:{theme.c(token)};font-size:12px;")

    def _satir_durum(self, ad: str, metin: str, token: str) -> None:
        lbl = self._lbl_durum.get(ad)
        if lbl is None:
            return
        lbl.setText(metin)
        lbl.setProperty("tema_token", token)
        lbl.setStyleSheet(f"color:{theme.c(token)};")

    def _logla(self, metin: str) -> None:
        self._log.append(metin)

    @staticmethod
    def _mod_metni(deger: Optional[float]) -> str:
        if deger is None:
            return "okunmadı"
        no = int(round(deger))
        ad = MOD_ADI.get(no)
        return f"{no} — {ad}" if ad else f"{no} — BİLİNMEYEN MOD"

    def _kapilari_uygula(self) -> None:
        yaziyor = self._yazici.calisiyor
        neden_komut = self._kanal.neden_kapali()
        neden_param = self._kanal.neden_kapali(param_da_gerek=True)

        self._oku_dugmesini_guncelle(neden_komut)
        self._btn_yaz.setEnabled(not neden_param and not yaziyor)
        self._btn_yaz.setToolTip(neden_param or ("Yazma sürüyor" if yaziyor else ""))
        self._btn_iptal.setEnabled(yaziyor)
        for combo in list(self._combo_slot.values()) + [self._combo_ch]:
            combo.setEnabled(not yaziyor)
        if neden_param and not yaziyor:
            self._durum_yaz(f"KAPALI — {neden_param}", "warning")

    def _oku_dugmesini_guncelle(self, neden_komut: str) -> None:
        """'FC'den OKU' düğmesini indirme durumuna göre oku/bırak moduna alır."""
        if self._depo.indiriyor:
            self._btn_oku.setText("İzlemeyi BIRAK (indirme sürüyor)")
            self._btn_oku.setEnabled(True)
            self._btn_oku.setToolTip(
                "FC'nin gönderimi MAVLink'te iptal edilemez; yalnız YKİ'nin "
                "beklemesi bırakılır"
            )
        else:
            self._btn_oku.setText("FC'den OKU (tam parametre listesi)")
            self._btn_oku.setEnabled(not neden_komut)
            self._btn_oku.setToolTip(neden_komut)

    # ══════════════════════════════════════════════════════════════════════
    # Dışarıdan beslenen bilgiler
    # ══════════════════════════════════════════════════════════════════════
    @pyqtSlot(bool)
    def on_connection_changed(self, _bagli: bool) -> None:
        self._kapilari_uygula()

    @pyqtSlot(str, float)
    def on_param(self, ad: str, deger: float) -> None:
        """ParamDeposu'ndan gelen her parametre. Yazma doğrulamasını da besler."""
        self._yazici.param_geldi(ad, deger)
        if ad in self._lbl_fc:
            self._lbl_fc[ad].setText(self._mod_metni(deger))
            if ad not in self._kullanici_degistirdi:
                self._combo_ayarla(self._combo_slot[ad], int(round(deger)))
            self._satir_uyum_isaretle(ad)
        elif ad == "FLTMODE_CH":
            self._lbl_ch_fc.setText(f"FC'deki: {int(round(deger))}")
            if ad not in self._kullanici_degistirdi:
                self._combo_ayarla(self._combo_ch, int(round(deger)))

    @staticmethod
    def _combo_ayarla(combo: QComboBox, deger: int) -> None:
        """Açılır listeyi verilen değere çeker; değer listede yoksa DOKUNMAZ."""
        idx = combo.findData(deger)
        if idx >= 0 and idx != combo.currentIndex():
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _satir_uyum_isaretle(self, ad: str) -> None:
        """Seçilen mod ile FC'deki mod aynı mı — satır durumunu günceller."""
        fc = self._depo.get(ad)
        combo = self._combo_slot.get(ad)
        if fc is None or combo is None:
            return
        secili = combo.currentData()
        if secili is None:
            return
        if param_esit(fc, float(secili)):
            self._satir_durum(ad, "FC ile aynı", "text_muted")
        else:
            self._satir_durum(ad, "değiştirilecek (yazılmadı)", "warning")

    def _kullanici_secti(self, ad: str) -> None:
        """Kullanıcı bir slotu ELLE değiştirdi — artık FC'den gelen değer
        bu slotun seçimini EZMEZ (kullanıcının işi kaybolmasın)."""
        self._kullanici_degistirdi.add(ad)
        for slot in self._combo_slot:
            self._satir_uyum_isaretle(slot)

    def _fc_degerlerini_yansit(self) -> None:
        """Açılır listeleri FC'den okunan değerlere çeker.

        Kullanıcının elle yaptığı seçim işaretlerini de TEMİZLER — bu düğmenin
        anlamı zaten "benim değişikliklerimi at, FC'de ne varsa onu göster".
        """
        self._kullanici_degistirdi.clear()
        for ad, _pwm, _lo, _hi in FLTMODE_SLOTLARI:
            deger = self._depo.get(ad)
            self._lbl_fc[ad].setText(self._mod_metni(deger))
            if deger is None:
                continue
            idx = self._combo_slot[ad].findData(int(round(deger)))
            if idx >= 0:
                self._combo_ayarla(self._combo_slot[ad], int(round(deger)))
                self._satir_durum(ad, "FC ile aynı", "text_muted")
            else:
                self._satir_durum(
                    ad, f"FC'de tanımadığımız mod: {int(round(deger))}", "danger"
                )
        ch = self._depo.get("FLTMODE_CH")
        if ch is not None:
            self._lbl_ch_fc.setText(f"FC'deki: {int(round(ch))}")
            self._combo_ayarla(self._combo_ch, int(round(ch)))

    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        """Canlı uçuş modu + mod anahtarının hangi slota düştüğü."""
        mod = data.get("flight_mode") or "—"
        ch = self._depo.get("FLTMODE_CH")
        ch_no = int(round(ch)) if ch is not None else None
        pwm = None
        if ch_no in CANLI_PWM_KANALLARI:
            pwm = data.get(f"rc_{ch_no}")
        if pwm is None:
            if ch_no is None:
                neden = "FLTMODE_CH okunmadı"
            elif ch_no == 0:
                neden = "mod anahtarı DEVRE DIŞI (FLTMODE_CH=0)"
            elif ch_no not in CANLI_PWM_KANALLARI:
                neden = (f"kanal {ch_no} için canlı PWM YKİ telemetrisinde yok "
                         "(yalnız 5-8 taşınıyor)")
            else:
                neden = "RC verisi gelmiyor (alıcı bağlı mı?)"
            self._lbl_ch_pwm.setText(f"Canlı PWM: — ({neden})")
            slot_metni = "—"
            yeni_aktif = -1
        else:
            self._lbl_ch_pwm.setText(f"Canlı PWM: {int(pwm)} µs")
            yeni_aktif = self._slot_bul(int(pwm))
            slot_metni = (FLTMODE_SLOTLARI[yeni_aktif][0]
                          if yeni_aktif >= 0 else "—")
        self._canli_lbl.setText(
            f"Canlı: uçuş modu {mod}   |   mod anahtarı slotu: {slot_metni}"
        )
        if yeni_aktif != self._aktif_slot:
            self._aktif_slot = yeni_aktif
            self._aktif_slotu_vurgula()

    @staticmethod
    def _slot_bul(pwm: int) -> int:
        for i, (_ad, _metin, lo, hi) in enumerate(FLTMODE_SLOTLARI):
            if lo <= pwm <= hi:
                return i
        return -1

    def _aktif_slotu_vurgula(self) -> None:
        for i, (ad, metin, _lo, _hi) in enumerate(FLTMODE_SLOTLARI):
            lbl = self._lbl_pwm.get(ad)
            if lbl is None:
                continue
            if i == self._aktif_slot:
                lbl.setText(f"▶ {metin}  (AKTİF)")
                lbl.setStyleSheet(f"color:{theme.c('accent')};font-weight:bold;")
            else:
                lbl.setText(metin)
                lbl.setStyleSheet("")

    def _depo_durum(self, metin: str) -> None:
        self._durum_yaz(metin, "info")
        self._kapilari_uygula()

    # ══════════════════════════════════════════════════════════════════════
    # Eylemler
    # ══════════════════════════════════════════════════════════════════════
    def _on_oku(self) -> None:
        """FC'den tam parametre listesini ister / süren indirmenin izlenmesini bırakır.

        NEDEN aynı düğme iki işi yapıyor: tam parametre indirmesi telemetri
        linkinde DAKİKALAR sürebilen bir işlem ve kullanıcıyı belirsiz bırakmak
        yasak. MAVLink'te başlamış bir PARAM_REQUEST_LIST akışını İPTAL EDEN bir
        mesaj YOK; dolayısıyla yapılabilecek tek dürüst "iptal", YKİ tarafındaki
        beklemeyi bırakmaktır — düğme de tam olarak bunu söylüyor.
        """
        if self._depo.indiriyor:
            self._depo.indirmeyi_durdur()
            self._logla("[OKU] İzleme bırakıldı (FC gönderimi kendiliğinden bitecek)")
        elif self._depo.indir():
            self._logla("[OKU] Tam parametre listesi istendi")
        self._kapilari_uygula()

    def _on_yaz(self) -> None:
        neden = self._kanal.neden_kapali(param_da_gerek=True)
        if neden:
            self._durum_yaz(f"YAZILMADI — {neden}", "danger")
            return

        istekler: dict[str, float] = {}
        for ad, _pwm, _lo, _hi in FLTMODE_SLOTLARI:
            secili = self._combo_slot[ad].currentData()
            if secili is None:
                continue
            fc = self._depo.get(ad)
            # NEDEN yalnız FARKLI olanları yazıyoruz: FC'de zaten doğru olan bir
            # parametreyi yeniden yazmak EEPROM'a gereksiz yazma çevrimi ekler ve
            # doğrulama turunu uzatır. FC değeri hiç okunmadıysa (None) yazılır —
            # o durumda "aynı" olduğunu bilemeyiz, varsaymayız.
            if fc is None or not param_esit(fc, float(secili)):
                istekler[ad] = float(secili)
        ch_secili = self._combo_ch.currentData()
        ch_fc = self._depo.get("FLTMODE_CH")
        if ch_secili is not None and (ch_fc is None or not param_esit(ch_fc, float(ch_secili))):
            istekler["FLTMODE_CH"] = float(ch_secili)

        if not istekler:
            QMessageBox.information(
                self, "Değişiklik yok",
                "Seçilen modlar FC'dekiyle aynı — yazılacak bir şey yok.\n"
                "FC değerleri hiç okunmadıysa önce 'FC'den OKU'ya basın."
            )
            return

        ozet = "\n".join(
            f"  {ad} = {self._mod_metni(d)}" if ad != "FLTMODE_CH"
            else f"  FLTMODE_CH = {int(d)}"
            for ad, d in istekler.items()
        )
        ek_uyari = ""
        if istekler.get("FLTMODE_CH") == 0:
            # NEDEN ayrı uyarı: FLTMODE_CH=0, mod anahtarını TAMAMEN devre dışı
            # bırakır — pilot uçuşta moda RC ile müdahale EDEMEZ hâle gelir.
            # Bu, tek bir açılır listeyle yapılabilen en tehlikeli değişiklik.
            ek_uyari = (
                "\n\n⛔ DİKKAT: FLTMODE_CH = 0 seçtiniz. Bu, MOD ANAHTARINI "
                "TAMAMEN DEVRE DIŞI bırakır — pilot uçuş sırasında vericiden "
                "mod değiştiremez. Bunu bilerek yapmıyorsanız İPTAL edin.\n"
            )
        cevap = QMessageBox.question(
            self, "Uçuş modları yazılacak",
            "Aşağıdaki parametreler uçuş kontrolcüsüne YAZILACAK ve geri "
            "okunarak doğrulanacak:\n\n" + ozet + ek_uyari +
            "\n\nUYARI: Mod anahtarınızın konumları değişecek. Yazdıktan sonra "
            "vericideki anahtarı TEK TEK gezdirip her konumun doğru modu "
            "seçtiğini YERDE doğrulayın.\n\nDevam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if cevap != QMessageBox.StandardButton.Yes:
            self._durum_yaz("İptal edildi — hiçbir parametre yazılmadı")
            return

        self._ilerleme.setRange(0, len(istekler))
        self._ilerleme.setValue(0)
        self._ilerleme.setFormat("%v / %m doğrulandı")
        for ad in istekler:
            self._satir_durum(ad, "yazılıyor…", "info")
        if not self._yazici.yaz(istekler):
            self._durum_yaz("YAZILAMADI — parametre kanalı bağlı değil", "danger")
            return
        self._durum_yaz(f"{len(istekler)} parametre yazıldı — FC'den geri okuma "
                        "bekleniyor…", "info")
        self._kapilari_uygula()

    def _on_iptal(self) -> None:
        self._yazici.iptal()
        self._durum_yaz(
            "İptal edildi — bekleyen doğrulamalar bırakıldı. GÖNDERİLMİŞ yazmalar "
            "geri ALINMADI; 'FC'den OKU' ile gerçek durumu doğrulayın.",
            "warning",
        )
        self._kapilari_uygula()

    # ── ParamYazici geri bildirimleri ────────────────────────────────────
    def _on_yazma_ilerleme(self, ad: str, durum: str) -> None:
        self._logla(f"[{ad}] {durum}")
        token = "info"
        if durum.startswith("✓"):
            token = "success"
        elif durum.startswith("✗") or durum.startswith("İPTAL"):
            token = "danger"
        elif durum.startswith("yanıt yok"):
            token = "warning"
        self._satir_durum(ad, durum, token)

    def _on_yazma_sonuc(self, ad: str, ok: bool, _beklenen: float, okunan: float) -> None:
        self._ilerleme.setValue(self._ilerleme.value() + 1)
        if ok:
            # Yazılan değer FC'de doğrulandı → artık "kullanıcı değişikliği"
            # sayılmaz; bundan sonra FC'den gelen değer serbestçe yansıtılabilir.
            self._kullanici_degistirdi.discard(ad)
        if ok and ad in self._lbl_fc:
            self._lbl_fc[ad].setText(self._mod_metni(okunan))
        elif ok and ad == "FLTMODE_CH":
            self._lbl_ch_fc.setText(f"FC'deki: {int(round(okunan))}")

    def _on_yazma_bitti(self, basarili: int, toplam: int) -> None:
        self._ilerleme.setFormat("İşlem yok")
        self._ilerleme.setRange(0, 100)
        self._ilerleme.setValue(0)
        if basarili == toplam:
            self._durum_yaz(
                f"✓ {basarili}/{toplam} parametre FC'den GERİ OKUNARAK doğrulandı. "
                "Şimdi vericideki mod anahtarını tek tek gezdirip yerde doğrulayın.",
                "success",
            )
        else:
            self._durum_yaz(
                f"✗ {basarili}/{toplam} doğrulandı — kalanlar YAZILDIĞI KANITLANAMADI. "
                "Satır durumlarına bakın ve 'FC'den OKU' ile gerçek değerleri görün.",
                "danger",
            )
        self._kapilari_uygula()
