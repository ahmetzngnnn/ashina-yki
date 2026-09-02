"""
ui/views/sensor_cal_view.py — Seviye / Jiroskop / Barometre + Sensör Sağlığı
=============================================================================

Bu sekme, "büyük" kalibrasyonların (ivmeölçer 6-pozisyon, pusula, radyo)
ARASINDAKİ boşluğu doldurur — uçuş öncesi her seferinde yapılan küçük ama
kritik işlemler ve kalibrasyonun TUTTUĞUNU gösteren canlı okumalar:

  1. SEVİYE (level) kalibrasyonu — MAV_CMD_PREFLIGHT_CALIBRATION param5=2
     (ArduPilot: AP::ins().calibrate_trim()). Araç düz zeminde dururken
     "sıfır açı"yı yeniden tanımlar. LOITER/POSHOLD'da aracın bir yöne
     kaymasının en sık nedeni budur.
  2. JİROSKOP kalibrasyonu — param1=1. FC her açılışta zaten yapar, ama
     araç açılış anında sallandıysa bozulur; bu düğme yeniden yaptırır.
  3. BAROMETRE sıfırlama — param3=1. Yer basıncını o anki değere çeker;
     yapılmazsa irtifa sıfırı kayar.
  4. PUSULA kurulum bilgisi — COMPASS_ORIENT / DEC / OFS okunur ve
     yorumlanır (kalibrasyonun sonucu buradan görülür).
  5. CANLI SENSÖR SAĞLIĞI — titreşim, açı, EKF bayrakları, GPS.

KAPSAM SINIRI (dürüstlük notu):
Ham IMU/mag/baro değerleri (RAW_IMU, SCALED_IMU2, SCALED_PRESSURE) YKİ'ye
GELMİYOR — core/mavlink_worker.py bu mesaj tiplerini işlemiyor ve o dosya bu
görevin kapsamı dışında (A ekibinde). Bu yüzden panel yalnızca worker'ın
GERÇEKTEN yaydığı alanları gösterir: ATTITUDE (roll/pitch/yaw), VIBRATION
(vib_x/y/z), VFR_HUD (baro_alt/heading), SYS_STATUS sağlık bitleri
(accel_cal_done/compass_cal_done), EKF_STATUS_REPORT (ekf_flags), GPS_RAW_INT.
Ham mag/accel eksenleri için gereken değişiklik raporda yazılıdır.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Optional

from PyQt6.QtCore import QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)
from pymavlink import mavutil

from core import theme
from ui.widgets.panel_ortak import MavKanal, ParamDeposu

logger = logging.getLogger(__name__)


# ── Titreşim eşikleri (ArduPilot "Measuring Vibration" dokümanı) ───────────
# < 30 m/s²  : iyi
# 30-60 m/s² : sınırda — EKF konum/hız kestirimi bozulmaya başlayabilir
# > 60 m/s²  : sorunlu — "clipping" ve irtifa kaçırma beklenir
VIB_IYI = 30.0
VIB_SINIR = 60.0

# Seviye kabul eşiği: araç düz zeminde dururken beklenen |roll|,|pitch|.
# NEDEN 2°: ArduPilot'un kendi pre-arm eşiği değil (o INS tabanlıdır); bu
# yalnızca OPERATÖRE "zemin/seviye şüpheli" demek için kullanılan GÖRSEL
# eşiktir. FC'nin kararı değil, panelin uyarısıdır — metinde de böyle yazar.
SEVIYE_UYARI_DERECE = 2.0

# FC onayı için bekleme penceresi (s). ArduPilot bu kalibrasyonların sonucunu
# COMMAND_ACK ile döner; mavlink_worker.py COMMAND_ACK'i İŞLEMİYOR, elimizdeki
# tek kanal STATUSTEXT. Bu süre boyunca ilgili STATUSTEXT gelmezse panel
# "doğrulanamadı" der — "başarılı" DEMEZ.
ONAY_BEKLEME_S = 8.0

_EKF_BAYRAKLARI = [
    (1,    "Tutum (attitude)"),
    (2,    "Yatay hız"),
    (4,    "Dikey hız"),
    (8,    "Yatay konum (bağıl)"),
    (16,   "Yatay konum (mutlak)"),
    (32,   "Dikey konum (mutlak)"),
    (64,   "Dikey konum (yer üstü)"),
    (256,  "Yatay konum kestirimi (bağıl) öngörülü"),
    (512,  "Yatay konum kestirimi (mutlak) öngörülü"),
]
# Bunlar SET olduğunda SORUN gösterir (diğerleri tersine, set = sağlıklı)
_EKF_KOTU_BAYRAKLAR = [
    (128,   "SABİT KONUM MODU (GPS yok — EKF konum kestirmiyor)"),
    (1024,  "EKF BAŞLATILMADI"),
    (32768, "GPS GLITCH"),
]


def _rotasyon_adi(deger: int) -> str:
    """MAV_SENSOR_ORIENTATION enum adı — pymavlink'ten CANLI okunur.

    NEDEN elle tablo yazmıyoruz: 40'tan fazla yönelim var ve ezberden yazılan
    bir tablo sessizce yanlış olabilir. pymavlink'in kendi enum sözlüğü
    (dialect ile birlikte gelir) tek doğruluk kaynağıdır.
    """
    try:
        from pymavlink.dialects.v20 import ardupilotmega as _ap
        girdi = _ap.enums.get("MAV_SENSOR_ORIENTATION", {}).get(int(deger))
        if girdi is not None:
            ad = getattr(girdi, "name", "")
            aciklama = getattr(girdi, "description", "")
            kisa = ad.replace("MAV_SENSOR_ROTATION_", "")
            return f"{kisa}  ({aciklama})" if aciklama else kisa
    except Exception:
        logger.exception("Yönelim adı çözülemedi")
    return "BİLİNMEYEN yönelim kodu"


class SensorCalView(QWidget):
    """Seviye/jiroskop/barometre kalibrasyonu + pusula kurulumu + canlı sağlık."""

    def __init__(self, kanal: MavKanal, depo: ParamDeposu,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kanal = kanal
        self._depo = depo
        self._bekleyen: Optional[str] = None     # süren işlemin adı
        # Süren işlemin STATUSTEXT'te aranacak anahtar kelimeleri. __init__'te
        # boş: _bekleyen None iken on_status_text zaten erken dönüyor, ama
        # tanımsız öznitelik riski hiç doğmasın diye burada kuruluyor.
        self._bekleyen_kelimeler: tuple = ()
        self._bekleyen_basari: tuple = ()
        self._bekleyen_hata: tuple = ()
        self._bekleyen_metin_onayi = True
        self._bekleme_bitis = 0.0
        self._durum_token = "text_muted"

        self._bekleme_sayaci = QTimer(self)
        self._bekleme_sayaci.setInterval(200)
        self._bekleme_sayaci.timeout.connect(self._bekleme_tik)

        self._build_ui()
        self._depo.degisti.connect(self.on_param)
        self._pusula_bilgisini_tazele()
        self._kapilari_uygula()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ══════════════════════════════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════════════════════════════
    def _build_ui(self) -> None:
        kok = QVBoxLayout(self)
        kok.setContentsMargins(8, 8, 8, 8)
        kok.setSpacing(8)

        ust = QHBoxLayout()

        # ── SOL: kalibrasyon işlemleri ──
        sol = QVBoxLayout()
        kal_grp = QGroupBox("Uçuş Öncesi Küçük Kalibrasyonlar")
        kg = QVBoxLayout(kal_grp)

        aciklama = QLabel(
            "Aracı DÜZ ve SAĞLAM bir zemine koyun, tamamen HAREKETSİZ tutun. "
            "Bu üç işlem de saniyeler sürer ve pervanelerin sökülü olmasını "
            "gerektirmez — motorlar dönmez."
        )
        aciklama.setObjectName("hintLabel")
        aciklama.setWordWrap(True)
        kg.addWidget(aciklama)

        self._btn_seviye = QPushButton("SEVİYE (level) kalibrasyonu")
        self._btn_seviye.setObjectName("primaryButton")
        self._btn_seviye.setMinimumHeight(34)
        self._btn_seviye.clicked.connect(self._on_seviye)
        kg.addWidget(self._btn_seviye)

        self._btn_gyro = QPushButton("JİROSKOP kalibrasyonu")
        self._btn_gyro.setMinimumHeight(34)
        self._btn_gyro.clicked.connect(self._on_gyro)
        kg.addWidget(self._btn_gyro)

        self._btn_baro = QPushButton("BAROMETRE sıfırlama (yer basıncı)")
        self._btn_baro.setMinimumHeight(34)
        self._btn_baro.clicked.connect(self._on_baro)
        kg.addWidget(self._btn_baro)

        self._ilerleme = QProgressBar()
        self._ilerleme.setRange(0, 100)
        self._ilerleme.setValue(0)
        self._ilerleme.setFormat("İşlem yok")
        kg.addWidget(self._ilerleme)

        self._btn_vazgec = QPushButton("Beklemeyi bırak")
        self._btn_vazgec.setMinimumHeight(28)
        self._btn_vazgec.setEnabled(False)
        self._btn_vazgec.clicked.connect(self._on_vazgec)
        kg.addWidget(self._btn_vazgec)

        self._durum = QLabel("Durum: hazır")
        self._durum.setWordWrap(True)
        kg.addWidget(self._durum)
        sol.addWidget(kal_grp)

        # ── Pusula kurulumu ──
        pus_grp = QGroupBox("Pusula Kurulumu (FC parametrelerinden)")
        pg = QGridLayout(pus_grp)
        self._pusula_alanlari: dict[str, QLabel] = {}
        for satir, (anahtar, etiket) in enumerate([
            ("ORIENT", "COMPASS_ORIENT (yönelim)"),
            ("DEC", "COMPASS_DEC (deklinasyon)"),
            ("AUTODEC", "COMPASS_AUTODEC (otomatik dekl.)"),
            ("USE", "Kullanılan pusulalar"),
            ("OFS1", "Pusula 1 ofset büyüklüğü"),
            ("OFS2", "Pusula 2 ofset büyüklüğü"),
            ("OFS3", "Pusula 3 ofset büyüklüğü"),
        ]):
            lbl = QLabel(etiket + ":")
            pg.addWidget(lbl, satir, 0)
            deger = QLabel("okunmadı")
            deger.setObjectName("monoLabel")
            deger.setWordWrap(True)
            self._pusula_alanlari[anahtar] = deger
            pg.addWidget(deger, satir, 1)
        pg.setColumnStretch(1, 1)
        sol.addWidget(pus_grp)
        sol.addStretch()
        ust.addLayout(sol, 1)

        # ── SAĞ: canlı sensör sağlığı ──
        sag = QVBoxLayout()
        canli_grp = QGroupBox("Canlı Sensör Sağlığı")
        cg = QGridLayout(canli_grp)
        self._canli: dict[str, QLabel] = {}
        alanlar = [
            ("aci", "Açı (roll / pitch)"),
            ("yaw", "Yaw / heading"),
            ("vib", "Titreşim X / Y / Z"),
            ("baro", "Barometrik irtifa"),
            ("cal", "FC sağlık bitleri"),
            ("ekf", "EKF durumu"),
            ("gps", "GPS"),
        ]
        for satir, (anahtar, etiket) in enumerate(alanlar):
            cg.addWidget(QLabel(etiket + ":"), satir, 0)
            lbl = QLabel("—")
            lbl.setObjectName("monoLabel")
            lbl.setWordWrap(True)
            self._canli[anahtar] = lbl
            cg.addWidget(lbl, satir, 1)
        cg.setColumnStretch(1, 1)
        sag.addWidget(canli_grp)

        kapsam = QLabel(
            "NOT: Ham IMU/mag/baro eksen değerleri (RAW_IMU / SCALED_IMU2 / "
            "SCALED_PRESSURE) burada GÖSTERİLEMİYOR — YKİ'nin MAVLink "
            "worker'ı bu mesaj tiplerini işlemiyor. Yukarıdakiler worker'ın "
            "gerçekten yaydığı alanlardır; uydurulmuş değer yoktur."
        )
        kapsam.setObjectName("mutedLabel")
        kapsam.setWordWrap(True)
        sag.addWidget(kapsam)
        sag.addStretch()
        ust.addLayout(sag, 1)

        kok.addLayout(ust)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.document().setMaximumBlockCount(300)
        kok.addWidget(self._log, 1)

        self._apply_theme_colors()

    def _apply_theme_colors(self) -> None:
        self._durum.setStyleSheet(
            f"color:{theme.c(self._durum_token)};font-size:12px;font-weight:bold;"
        )
        self._log.setStyleSheet(
            f"QTextEdit{{color:{theme.c('success')};"
            "font-family:'Courier New';font-size:11px;}"
        )
        for lbl in list(self._canli.values()) + list(self._pusula_alanlari.values()):
            token = lbl.property("tema_token")
            lbl.setStyleSheet(f"color:{theme.c(token)};" if token else "")

    # ══════════════════════════════════════════════════════════════════════
    # Yardımcılar
    # ══════════════════════════════════════════════════════════════════════
    @staticmethod
    def _ml():
        if getattr(mavutil, "mavlink", None) is None:
            mavutil.set_dialect("ardupilotmega")
        return mavutil.mavlink

    def _durum_yaz(self, metin: str, token: str = "text_muted") -> None:
        self._durum_token = token
        self._durum.setText(metin)
        self._durum.setStyleSheet(
            f"color:{theme.c(token)};font-size:12px;font-weight:bold;"
        )

    def _logla(self, metin: str) -> None:
        self._log.append(f"[{time.strftime('%H:%M:%S')}] {metin}")

    def _canli_yaz(self, anahtar: str, metin: str, token: Optional[str] = None) -> None:
        lbl = self._canli.get(anahtar)
        if lbl is None:
            return
        lbl.setText(metin)
        lbl.setProperty("tema_token", token)
        lbl.setStyleSheet(f"color:{theme.c(token)};" if token else "")

    def _kapilari_uygula(self) -> None:
        neden = self._kanal.neden_kapali()
        mesgul = self._bekleyen is not None
        acik = (not neden) and (not mesgul)
        for btn in (self._btn_seviye, self._btn_gyro, self._btn_baro):
            btn.setEnabled(acik)
            btn.setToolTip(neden or ("İşlem sürüyor" if mesgul else ""))
        self._btn_vazgec.setEnabled(mesgul)
        if neden and not mesgul:
            self._durum_yaz(f"KAPALI — {neden}", "warning")

    # ══════════════════════════════════════════════════════════════════════
    # Kalibrasyon komutları
    # ══════════════════════════════════════════════════════════════════════
    def _kalibrasyon_gonder(self, ad: str, params: list, onay_metni: str,
                            izle: tuple, basari: tuple, hata: tuple,
                            metin_onayi: bool) -> None:
        """Kalibrasyon komutunu gönderir ve FC metnini bekler.

        `izle`   : bu işleme ait STATUSTEXT'i tanıyan kelimeler
        `basari` : başarıyı KESİN gösteren kelimeler (ArduPilot kaynağından)
        `hata`   : başarısızlığı KESİN gösteren kelimeler
        `metin_onayi`: ArduPilot bu işlem için hiç metin gönderiyor mu

        NEDEN kelimeler ezberden değil: /home/ash/ardupilot @27b35986e9
        kaynağından okundu (AP_InertialSensor::calibrate_trim → "Trim OK: ...",
        "trim over maximum of 10 degrees", "unsupported trim rotation";
        GCS_Common::_handle_command_preflight_calibration_baro → "Updating
        barometer calibration", "Barometer calibration complete"). Genel
        "ok/complete" araması ilk sürümde "Updating barometer calibration"i
        BAŞARI sanıyordu ("updat" eşleşmesi) — işlem daha bitmeden.
        """
        neden = self._kanal.neden_kapali()
        if neden:
            self._durum_yaz(f"GÖNDERİLMEDİ — {neden}", "danger")
            return
        cevap = QMessageBox.question(
            self, f"{ad} — onay", onay_metni,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if cevap != QMessageBox.StandardButton.Yes:
            self._durum_yaz("İptal edildi — komut gönderilmedi")
            return
        ok = self._kanal.komut(self._ml().MAV_CMD_PREFLIGHT_CALIBRATION, params)
        self._logla(f"[GÖNDER] PREFLIGHT_CALIBRATION {params} ({ad})")
        if not ok:
            self._durum_yaz(
                "✗ Komut GÖNDERİLEMEDİ — " + self._kanal.neden_kapali(), "danger"
            )
            return
        self._bekleyen = ad
        self._bekleyen_kelimeler = izle
        self._bekleyen_basari = basari
        self._bekleyen_hata = hata
        self._bekleyen_metin_onayi = metin_onayi
        self._bekleme_bitis = time.monotonic() + ONAY_BEKLEME_S
        self._ilerleme.setRange(0, 100)
        self._ilerleme.setValue(0)
        self._bekleme_sayaci.start()
        self._durum_yaz(
            f"{ad}: komut gönderildi — FC mesajı bekleniyor. ARACI HAREKET ETTİRMEYİN.",
            "warning",
        )
        self._kapilari_uygula()

    def _on_seviye(self) -> None:
        # param5 = 2 → ArduPilot AP::ins().calibrate_trim() (kart seviyesi)
        self._kalibrasyon_gonder(
            "SEVİYE kalibrasyonu",
            [0, 0, 0, 0, 2, 0, 0],
            "Aracı UÇUŞTAKİ GİBİ düz duracağı konumda, SAĞLAM ve DÜZ bir zemine "
            "koydunuz mu?\n\nSeviye kalibrasyonu, aracın O ANKİ duruşunu 'sıfır "
            "açı' kabul eder. Eğik bir zeminde yapılırsa araç uçuşta o yöne "
            "sürekli kayar.\n\nNOT: ArduPilot bu işlemden önce jiroskopu da "
            "yeniden kalibre eder ve son 5 saniye içinde başka bir ivmeölçer "
            "kalibrasyonu yapıldıysa komutu REDDEDER.\n\nDevam edilsin mi?",
            izle=("trim", "level"),
            basari=("trim ok",),
            hata=("over maximum", "unsupported trim"),
            metin_onayi=True,
        )

    def _on_gyro(self) -> None:
        self._kalibrasyon_gonder(
            "JİROSKOP kalibrasyonu",
            [1, 0, 0, 0, 0, 0, 0],
            "Araç tamamen HAREKETSİZ mi?\n\nJiroskop kalibrasyonu birkaç saniye "
            "sürer; bu sırada en küçük hareket bile kalibrasyonu bozar veya "
            "başarısız yapar.\n\nUYARI: ArduPilot jiroskop kalibrasyonu için "
            "HİÇBİR metin mesajı göndermez (sonucu yalnızca COMMAND_ACK ile "
            "bildirir ve YKİ'nin MAVLink worker'ı COMMAND_ACK'i işlemiyor). "
            "Bu yüzden panel 'başarılı' DİYEMEZ — yalnızca komutun gittiğini "
            "söyler.\n\nDevam edilsin mi?",
            izle=("gyro",),
            basari=(),
            hata=("gyro calibration failed", "failed to register gyro"),
            metin_onayi=False,
        )

    def _on_baro(self) -> None:
        self._kalibrasyon_gonder(
            "BAROMETRE sıfırlama",
            [0, 0, 1, 0, 0, 0, 0],
            "Yer basıncı, aracın ŞU ANKİ bulunduğu yüksekliğe sıfırlanacak.\n\n"
            "Bunu aracın KALKIŞ YAPACAĞI noktada yapın — başka bir yükseklikte "
            "yapılırsa tüm irtifa okumaları o fark kadar kayar.\n\n"
            "Devam edilsin mi?",
            izle=("barometer", "baro"),
            basari=("barometer calibration complete",),
            hata=("fail",),
            metin_onayi=True,
        )

    def _on_vazgec(self) -> None:
        """Bekleme penceresini kapatır. FC'de başlamış işlemi DURDURMAZ.

        NEDEN: PREFLIGHT_CALIBRATION'ın iptal mesajı yoktur (pusula hariç —
        DO_CANCEL_MAG_CAL). Burada yalnızca YKİ'nin beklemesi bırakılır;
        bunu kullanıcıya açıkça söylüyoruz ki "iptal ettim" sanmasın.
        """
        if self._bekleyen is None:
            return
        ad = self._bekleyen
        self._bekleme_bitir()
        self._durum_yaz(
            f"{ad}: bekleme bırakıldı. FC'de işlem SÜRÜYOR OLABİLİR — "
            "iptal mesajı MAVLink'te yoktur. Sonucu FC mesajlarından izleyin.",
            "warning",
        )

    def _bekleme_tik(self) -> None:
        if self._bekleyen is None:
            self._bekleme_sayaci.stop()
            return
        kalan = self._bekleme_bitis - time.monotonic()
        if kalan <= 0:
            ad = self._bekleyen
            metin_onayi = self._bekleyen_metin_onayi
            self._bekleme_bitir()
            if metin_onayi:
                self._durum_yaz(
                    f"⚠ {ad}: FC'den {ONAY_BEKLEME_S:.0f} s içinde onay mesajı "
                    "GELMEDİ. BAŞARILI OLDUĞU DOĞRULANAMADI — canlı okumalara "
                    "bakarak (açı, EKF) sonucu kendiniz teyit edin.",
                    "warning",
                )
            else:
                self._durum_yaz(
                    f"{ad}: komut gönderildi. ArduPilot bu işlem için metin "
                    "mesajı GÖNDERMEZ, bu yüzden panel sonucu DOĞRULAYAMAZ. "
                    "Aracı hareketsiz tutarken sağdaki canlı açı/EKF "
                    "okumalarının oturmasına bakın.",
                    "warning",
                )
            return
        self._ilerleme.setValue(int(100 * (1 - kalan / ONAY_BEKLEME_S)))
        self._ilerleme.setFormat(f"{self._bekleyen} — FC yanıtı {kalan:.0f} s")

    def _bekleme_bitir(self) -> None:
        self._bekleyen = None
        self._bekleme_sayaci.stop()
        self._ilerleme.setValue(0)
        self._ilerleme.setFormat("İşlem yok")
        self._kapilari_uygula()

    # ══════════════════════════════════════════════════════════════════════
    # FC geri bildirimi
    # ══════════════════════════════════════════════════════════════════════
    @pyqtSlot(str, int)
    def on_status_text(self, text: str, severity: int) -> None:
        if not text or self._bekleyen is None:
            return
        low = text.strip().lower()
        if not any(k in low for k in self._bekleyen_kelimeler):
            return
        self._logla(f"[FC sev={severity}] {text.strip()}")
        ad = self._bekleyen
        # Karar sırası: KESİN hata → KESİN başarı → severity. Genel "ok/complete"
        # taraması yapılmıyor (bkz. _kalibrasyon_gonder docstring'i).
        if any(k in low for k in self._bekleyen_hata):
            self._bekleme_bitir()
            self._durum_yaz(f"✗ {ad} BAŞARISIZ — FC: {text.strip()}", "danger")
            return
        if self._bekleyen_basari and any(k in low for k in self._bekleyen_basari):
            self._bekleme_bitir()
            self._durum_yaz(f"✓ {ad} — FC: {text.strip()}", "success")
            return
        if severity <= 3:      # ERROR / CRITICAL / ALERT / EMERGENCY
            self._bekleme_bitir()
            self._durum_yaz(f"✗ {ad} BAŞARISIZ — FC: {text.strip()}", "danger")
            return
        # Ne kesin başarı ne kesin hata: ara mesaj ("Updating barometer
        # calibration" gibi). Bekleme sürüyor, kullanıcı mesajı log'da görüyor.
        self._durum_yaz(f"{ad} sürüyor — FC: {text.strip()}", "warning")

    @pyqtSlot(bool)
    def on_connection_changed(self, bagli: bool) -> None:
        if not bagli and self._bekleyen is not None:
            ad = self._bekleyen
            self._bekleme_bitir()
            self._durum_yaz(
                f"⚠ {ad}: bağlantı koptu — sonuç DOĞRULANAMADI", "danger"
            )
        self._kapilari_uygula()

    @pyqtSlot(str, float)
    def on_param(self, ad: str, _deger: float) -> None:
        if ad.startswith("COMPASS_"):
            self._pusula_bilgisini_tazele()

    # ══════════════════════════════════════════════════════════════════════
    # Pusula kurulumu
    # ══════════════════════════════════════════════════════════════════════
    def _pusula_bilgisini_tazele(self) -> None:
        def yaz(anahtar: str, metin: str, token: Optional[str] = None) -> None:
            lbl = self._pusula_alanlari.get(anahtar)
            if lbl is None:
                return
            lbl.setText(metin)
            lbl.setProperty("tema_token", token)
            lbl.setStyleSheet(f"color:{theme.c(token)};" if token else "")

        orient = self._depo.get("COMPASS_ORIENT")
        if orient is None:
            yaz("ORIENT", "okunmadı")
        else:
            yaz("ORIENT", f"{int(orient)} — {_rotasyon_adi(int(orient))}")

        dec = self._depo.get("COMPASS_DEC")
        autodec = self._depo.get("COMPASS_AUTODEC")
        if dec is None:
            yaz("DEC", "okunmadı")
        else:
            # COMPASS_DEC birimi RADYANDIR (ArduPilot parametre meta verisi).
            yaz("DEC", f"{dec:.5f} rad = {math.degrees(dec):+.2f}°")
        if autodec is None:
            yaz("AUTODEC", "okunmadı")
        elif autodec >= 0.5:
            yaz("AUTODEC", "1 — AÇIK (deklinasyon GPS konumundan otomatik)", "success")
        else:
            yaz("AUTODEC", "0 — KAPALI (COMPASS_DEC elle girilmiş olmalı)", "warning")

        kullanilan = []
        for i, ad in enumerate(("COMPASS_USE", "COMPASS_USE2", "COMPASS_USE3"), start=1):
            v = self._depo.get(ad)
            if v is None:
                continue
            kullanilan.append(f"#{i}={'EVET' if v >= 0.5 else 'hayır'}")
        yaz("USE", ", ".join(kullanilan) if kullanilan else "okunmadı")

        # Ofset büyüklüğü — kalibrasyonun TUTTUĞUNU gösteren en doğrudan sayı
        sinir = self._depo.get("COMPASS_OFFS_MAX")
        for idx, sonek in enumerate(("", "2", "3"), start=1):
            eksenler = [self._depo.get(f"COMPASS_OFS{sonek}_{eksen}")
                        for eksen in ("X", "Y", "Z")]
            anahtar = f"OFS{idx}"
            if any(v is None for v in eksenler):
                yaz(anahtar, "okunmadı")
                continue
            buyukluk = math.sqrt(sum(v * v for v in eksenler))
            metin = (f"|ofs| = {buyukluk:.0f}  "
                     f"(X={eksenler[0]:.0f} Y={eksenler[1]:.0f} Z={eksenler[2]:.0f})")
            if sinir is None:
                yaz(anahtar, metin + "   [sınır COMPASS_OFFS_MAX okunmadı]")
            elif buyukluk > sinir:
                yaz(anahtar,
                    metin + f"   ✗ COMPASS_OFFS_MAX={sinir:.0f} AŞILDI — "
                    "ArduPilot ARM ETMEZ, pusula yeniden kalibre edilmeli",
                    "danger")
            elif buyukluk > 0.75 * sinir:
                yaz(anahtar, metin + f"   ⚠ sınıra yakın (max {sinir:.0f})", "warning")
            else:
                yaz(anahtar, metin + f"   ✓ (max {sinir:.0f})", "success")

    # ══════════════════════════════════════════════════════════════════════
    # Canlı telemetri
    # ══════════════════════════════════════════════════════════════════════
    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        # ── Açı / seviye ──
        roll = data.get("roll")
        pitch = data.get("pitch")
        if roll is not None and pitch is not None:
            r_deg, p_deg = math.degrees(roll), math.degrees(pitch)
            sapma = max(abs(r_deg), abs(p_deg))
            token = "success" if sapma <= SEVIYE_UYARI_DERECE else "warning"
            self._canli_yaz(
                "aci",
                f"roll {r_deg:+6.2f}°   pitch {p_deg:+6.2f}°   "
                + ("(düz)" if sapma <= SEVIYE_UYARI_DERECE
                   else f"(>{SEVIYE_UYARI_DERECE:.0f}° — zemin eğik ya da "
                        "seviye kalibrasyonu gerekli)"),
                token,
            )
        yaw = data.get("yaw")
        hdg = data.get("heading")
        if yaw is not None:
            metin = f"yaw {math.degrees(yaw):+7.2f}°"
            if hdg is not None:
                metin += f"   heading {hdg:.0f}°"
            self._canli_yaz("yaw", metin)

        # ── Titreşim ──
        vx, vy, vz = data.get("vib_x"), data.get("vib_y"), data.get("vib_z")
        if None not in (vx, vy, vz):
            en_buyuk = max(vx, vy, vz)
            if en_buyuk < VIB_IYI:
                token, yorum = "success", "iyi"
            elif en_buyuk < VIB_SINIR:
                token, yorum = "warning", f"SINIRDA (>{VIB_IYI:.0f} m/s²)"
            else:
                token, yorum = "danger", f"YÜKSEK (>{VIB_SINIR:.0f} m/s²) — EKF bozulur"
            self._canli_yaz(
                "vib",
                f"{vx:5.1f} / {vy:5.1f} / {vz:5.1f} m/s²   {yorum}",
                token,
            )

        # ── Barometre ──
        baro = data.get("baro_alt")
        alt = data.get("alt")
        if baro is not None:
            metin = f"{baro:+.2f} m (VFR_HUD)"
            if alt is not None:
                metin += f"   |   EKF bağıl irtifa {alt:+.2f} m"
            self._canli_yaz("baro", metin)

        # ── FC sağlık bitleri ──
        accel_ok = data.get("accel_cal_done")
        mag_ok = data.get("compass_cal_done")
        if accel_ok is not None or mag_ok is not None:
            parcalar = []
            if accel_ok is not None:
                parcalar.append("ivmeölçer " + ("SAĞLIKLI" if accel_ok else "ARIZALI"))
            if mag_ok is not None:
                parcalar.append("pusula " + ("SAĞLIKLI" if mag_ok else "ARIZALI"))
            kotu = (accel_ok is False) or (mag_ok is False)
            self._canli_yaz("cal", ", ".join(parcalar),
                            "danger" if kotu else "success")

        # ── EKF ──
        bayrak = data.get("ekf_flags")
        if bayrak is not None:
            bayrak = int(bayrak)
            eksik = [ad for bit, ad in _EKF_BAYRAKLARI if not (bayrak & bit)]
            kotu = [ad for bit, ad in _EKF_KOTU_BAYRAKLAR if bayrak & bit]
            if kotu:
                self._canli_yaz("ekf", "⛔ " + "; ".join(kotu), "danger")
            elif eksik:
                self._canli_yaz("ekf", "⚠ hazır olmayan: " + ", ".join(eksik), "warning")
            else:
                self._canli_yaz("ekf", "✓ tüm kestirimler hazır", "success")

        # ── GPS ──
        fix = data.get("gps_fix_name")
        sats = data.get("gps_sats")
        hdop = data.get("gps_hdop")
        if fix is not None:
            metin = f"{fix}   uydu {sats}   HDOP {hdop:.2f}" if sats is not None and hdop is not None else str(fix)
            token = "success" if (sats or 0) >= 8 and (hdop or 99) <= 1.5 else "warning"
            self._canli_yaz("gps", metin, token)
