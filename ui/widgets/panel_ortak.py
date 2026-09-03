"""
ui/widgets/panel_ortak.py — Kalibrasyon/test panellerinin ORTAK parçaları
=========================================================================

Bu dosya, "Kalibrasyon" sekmesi altındaki alt panellerin (motor testi, uçuş
modları, uçuş öncesi parametre denetimi, sensör/seviye) PAYLAŞTIĞI üç şeyi
tek yerde toplar:

  1. MavKanal          — FC'ye komut/parametre gönderme yolu + "gerçekten
                         gidiyor mu" denetimi,
  2. TekerlekKorumasi  — odakta olmayan spin/combo'da fare tekerleğini yutar,
  3. PervaneKapisi     — motor döndüren her panelin ÖNÜNDEKİ fiziksel
                         güvenlik kapısı.

NEDEN ayrı dosya: bu üç parçanın her biri, calibration_view.py içinde
KOPYALANMIŞ hâlde ortaya çıkmaya adaydı (motor testi + ESC + seviye panelleri
aynı kapıyı ister). Kopya güvenlik kodu, bir kopyada düzeltilip diğerinde
düzeltilmeyen açık demektir — bu depoda aynı hata sınıfı (YKİ/İHA rota
matematiğinin iki kopyası) zaten yaşandı.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QFrame, QLabel, QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# MavKanal — FC'ye giden tek yol
# ═══════════════════════════════════════════════════════════════════════════
class MavKanal:
    """Alt panellerin FC'ye komut/parametre gönderdiği YOL + bağlılık denetimi.

    NEDEN (1/2 — sessiz kayıp): PyQt'de bağlanmamış bir sinyale emit etmek
    HİÇBİR ŞEY yapmaz ve hata da vermez. calibration_view.py'deki "ölü kontrol"
    hatalarının (kaydedildi diyen ama hiçbir şey yazmayan radyo kalibrasyonu,
    gönderildi diyen ama gitmeyen motor komutu) kök nedeni buydu. Bu sınıf,
    emit etmeden ÖNCE yolun bir alıcıya bağlı olduğunu doğrular ve sonucu
    bool olarak döndürür — çağıran "gönderildi" demeden önce bakmak ZORUNDA.

    NEDEN (2/2 — sinyal değil callable): Alt paneller kendi pyqtSignal'lerini
    tanımlayıp CalibrationView'inkine zincirleseydi, `receivers() > 0` denetimi
    HER ZAMAN doğru dönerdi (zincirin kendisi bir alıcıdır) — yani denetim
    sessizce işlevsizleşirdi. Bu yüzden kanal, sinyalin SAHİBİNDEN gelen
    emit + denetim callable'larını taşır; denetim gerçekten uçtaki
    mavlink_worker bağlantısına bakar.

    `link_var` ise ayrı bir şeydir: YKİ içi bağlantı (sinyal) sağlam olsa bile
    FC ile telemetri linki kopuksa komut kuyruğa girip yok olur. İki durum
    kullanıcıya AYRI metinlerle bildirilir (`neden_kapali`).
    """

    def __init__(
        self,
        komut_gonder: Callable[[int, list], None],
        param_yaz: Callable[[str, float], None],
        komut_yolu_denetle: Callable[[], bool],
        param_yolu_denetle: Callable[[], bool],
    ) -> None:
        self._komut_gonder = komut_gonder
        self._param_yaz = param_yaz
        self._komut_yolu_denetle = komut_yolu_denetle
        self._param_yolu_denetle = param_yolu_denetle
        self._link_var = False

    # ── Bağlantı durumu ──────────────────────────────────────────────────
    def link_ayarla(self, bagli: bool) -> None:
        """mavlink_worker.connection_changed'ten beslenir."""
        self._link_var = bool(bagli)

    @property
    def link_var(self) -> bool:
        return self._link_var

    def komut_yolu_var(self) -> bool:
        try:
            return bool(self._komut_yolu_denetle())
        except (TypeError, RuntimeError):
            # Denetim yapılamıyorsa akışı engelleme — ama link durumu yine geçerli
            return True

    def param_yolu_var(self) -> bool:
        try:
            return bool(self._param_yolu_denetle())
        except (TypeError, RuntimeError):
            return True

    def neden_kapali(self, param_da_gerek: bool = False) -> str:
        """Butonların NEDEN kapalı olduğunu açıklayan metin ('' = açık).

        Tasarım kuralı: bağlantı yoksa buton kapalı VE nedeni YAZILI olmalı.
        """
        if not self.komut_yolu_var():
            return ("YKİ iç komut kanalı hiçbir alıcıya bağlı değil "
                    "(main_window bağlantı hatası) — komut gönderilemez")
        if param_da_gerek and not self.param_yolu_var():
            return ("YKİ iç parametre yazma kanalı bağlı değil "
                    "(main_window bağlantı hatası) — parametre yazılamaz")
        if not self._link_var:
            return ("Uçuş kontrolcüsüne BAĞLI DEĞİL — üst çubuktan "
                    "'BAĞLAN' ile telemetri linkini kurun")
        return ""

    # ── Gönderim ─────────────────────────────────────────────────────────
    def komut(self, cmd_id: int, params: list) -> bool:
        """COMMAND_LONG gönderir. Dönüş: gerçekten emit edildi mi.

        Link yokken de emit edilir (kuyruğa girer) AMA False döner — çağıran
        kullanıcıya "gönderildi" DEMEMELİDİR.
        """
        if not self.komut_yolu_var():
            logger.error("Komut gönderilemedi (yol yok): cmd=%s params=%s", cmd_id, params)
            return False
        try:
            self._komut_gonder(int(cmd_id), list(params))
        except Exception:
            logger.exception("Komut emit hatası: cmd=%s", cmd_id)
            return False
        logger.info("Panel komutu: cmd=%s params=%s link=%s", cmd_id, params, self._link_var)
        return self._link_var

    def param(self, ad: str, deger: float) -> bool:
        """PARAM_SET gönderir. Dönüş: gerçekten emit edildi mi."""
        if not self.param_yolu_var():
            logger.error("Parametre yazılamadı (yol yok): %s=%s", ad, deger)
            return False
        try:
            self._param_yaz(str(ad), float(deger))
        except Exception:
            logger.exception("Parametre emit hatası: %s", ad)
            return False
        logger.info("Panel parametre yazımı: %s=%s link=%s", ad, deger, self._link_var)
        return self._link_var


# ═══════════════════════════════════════════════════════════════════════════
# TekerlekKorumasi
# ═══════════════════════════════════════════════════════════════════════════
class TekerlekKorumasi(QObject):
    """Odakta OLMAYAN spin/combo widget'larında fare tekerleği olayını yutar.

    NEDEN: calibration_view.py'deki _WheelGuard ile aynı gerekçe — panel
    kaydırılırken imlecin altından geçen bir QSpinBox'ın değeri SESSİZCE
    değişiyordu ve bu, motor gaz yüzdesinde doğrudan güvenlik riskiydi.
    Buradaki kopya değil, ORTAK sürümdür: yeni paneller bunu kullanır.
    """

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return False


def tekerlek_korumasi_kur(kok: QWidget, filtre: TekerlekKorumasi) -> None:
    """kok altındaki TÜM spin/combo widget'larına tekerlek korumasını kurar.

    Taramalı kurulum: ileride eklenecek yeni bir spin/combo da otomatik korunur.
    `filtre` çağıran tarafta REFERANS TUTULMALIDIR (yoksa GC toplar ve koruma
    sessizce ortadan kalkar).
    """
    for cls in (QAbstractSpinBox, QComboBox):
        for wdg in kok.findChildren(cls):
            wdg.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            wdg.installEventFilter(filtre)


# ═══════════════════════════════════════════════════════════════════════════
# PervaneKapisi
# ═══════════════════════════════════════════════════════════════════════════
class PervaneKapisi(QFrame):
    """Motor döndüren panellerin önündeki FİZİKSEL güvenlik kapısı.

    Sözleşme:
      • Kutu İŞARETLENMEDEN `acik()` False döner → panel tüm test butonlarını
        KAPALI tutmak zorundadır (ACİL DUR hariç — durdurmak her zaman güvenli).
      • Panel gizlenince/terk edilince `sifirla()` çağrılır ve onay DÜŞER.

    NEDEN sıfırlama: onay kutusu ARACIN O ANKİ FİZİKSEL DURUMUNA dair bir
    beyandır. Bir saat önce (belki pervaneler takılmadan önce) verilmiş bir
    onayın açık kalması, kutunun hiç olmamasından daha tehlikelidir — operatör
    "zaten onayladım" diye bakmadan basar.

    NEDEN şartnameye uygun: teknik kontrolde İHA'nın pervaneleri SÖKÜLÜ
    gelmesi isteniyor; bu panel tam da o senaryoda (pervanesiz tezgâh) motor
    yönü/sırası doğrulaması için kullanılacak.
    """

    durum_degisti = pyqtSignal(bool)

    def __init__(self, ek_uyari: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("cardFrame")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        baslik = QLabel("⛔  MOTOR DÖNDÜREN PANEL  ⛔")
        baslik.setObjectName("dangerText")
        baslik.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        baslik.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(baslik)

        metin = (
            "Bu panel motorları GERÇEKTEN döndürür. Devam etmeden önce:\n"
            "  1) PERVANELERİ SÖKÜN — takılı pervane ciddi yaralanmaya yol açar,\n"
            "  2) Araç yere sabit dursun, kimse kolların üzerinde olmasın,\n"
            "  3) Aracın ARM'lı OLMADIĞINDAN emin olun (ArduPilot armlıyken motor "
            "testini reddeder),\n"
            "  4) Emniyet düğmesine (safety switch) BASIN — bu araçta "
            "BRD_SAFETY_MASK=16368 olduğu için SERVO1-4 (motorlar) emniyet "
            "düğmesine UYAR; basılmazsa motorlar hiç dönmez."
        )
        if ek_uyari:
            metin += "\n" + ek_uyari
        aciklama = QLabel(metin)
        aciklama.setWordWrap(True)
        aciklama.setObjectName("hintLabel")
        lay.addWidget(aciklama)

        self._kutu = QCheckBox(
            "PERVANELERİN SÖKÜLÜ olduğunu ŞU AN gözle kontrol ettim"
        )
        self._kutu.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._kutu.toggled.connect(self._on_toggle)
        lay.addWidget(self._kutu)

    def _on_toggle(self, acik: bool) -> None:
        logger.info("Pervane güvenlik kapısı: %s", "AÇIK" if acik else "KAPALI")
        self.durum_degisti.emit(bool(acik))

    def acik(self) -> bool:
        return self._kutu.isChecked()

    def sifirla(self) -> None:
        """Onayı düşürür (panel terk edilince çağrılır)."""
        if self._kutu.isChecked():
            self._kutu.setChecked(False)   # toggled → durum_degisti(False)


# ═══════════════════════════════════════════════════════════════════════════
# Parametre karşılaştırma
# ═══════════════════════════════════════════════════════════════════════════
# NEDEN tolerans: MAVLink PARAM_VALUE alanı float32'dir. ArduPilot int8/int16
# saklanan bir parametreyi bile float32 olarak geri gönderir; ondalıklı olanlar
# (ör. INS_HNTCH_FREQ = 78.8) float32'ye yuvarlanınca 78.80000305... döner.
# Tam eşitlik ararsak DOĞRU yazılmış bir parametreyi "uyuşmuyor" sayardık.
# Mutlak taban 1e-4, ölçekli pay 1e-5: float32'nin ~1e-7 bağıl hatasının çok
# üstünde ama iki farklı ayarı (ör. 3 ve 3.0001) asla eşit saymayacak kadar dar.
PARAM_MUTLAK_TOLERANS = 1e-4
PARAM_BAGIL_TOLERANS = 1e-5


def param_esit(a: float, b: float) -> bool:
    """İki parametre değeri float32 yuvarlaması içinde aynı mı."""
    try:
        return abs(float(a) - float(b)) <= max(
            PARAM_MUTLAK_TOLERANS, abs(float(b)) * PARAM_BAGIL_TOLERANS
        )
    except (TypeError, ValueError):
        return False


# ═══════════════════════════════════════════════════════════════════════════
# ParamDeposu — FC parametrelerinin PAYLAŞILAN önbelleği + indirme durumu
# ═══════════════════════════════════════════════════════════════════════════
class ParamDeposu(QObject):
    """Alt panellerin ortak parametre önbelleği ve "tümünü indir" akışı.

    NEDEN paylaşılan: uçuş modları paneli FLTMODE*, uçuş öncesi denetim paneli
    fc_params_expected, motor paneli FRAME_CLASS/MOT_PWM_* okur. Her biri ayrı
    ayrı `request_parameters()` çağırsaydı, RFD900A üzerinden dakikalarca süren
    TAM parametre indirmesi üst üste üç kez başlardı ve linki boğardı.

    NEDEN "sessizlik" ile bitiriyoruz: mavlink_worker.py PARAM_VALUE.param_count
    bilgisini UI'ya YAYMIYOR (yalnız param_received(ad, değer) var; o dosya bu
    görevin kapsamı dışında). Elimizdeki tek gözlemlenebilir olay yeni parametre
    gelmesi olduğu için indirme, belirli süre yeni parametre GELMEDİĞİNDE bitmiş
    sayılır. Bu bir TAHMİN değil, gözleme dayalı bir bitiş ölçütüdür ve
    kullanıcıya "N parametre alındı" diye AÇIKÇA söylenir.
    """

    # ad, değer  → panellerin canlı güncellemesi
    degisti = pyqtSignal(str, float)
    # insan-okur durum metni
    durum = pyqtSignal(str)
    # indirme bitti (alınan parametre sayısı)
    indirme_bitti = pyqtSignal(int)

    # Yeni parametre gelmeden geçen bu süre sonunda indirme bitmiş sayılır.
    SESSIZLIK_S = 3.0

    # Eksik parametreler tek tek yeniden istenirken bir turda kaç istek
    # gönderileceği. RFD900A dar bir hat; hepsini birden sormak linki boğar.
    TUR_BASINA_ISTEK = 20
    # Art arda bu kadar tur HİÇ yeni parametre gelmezse durulur. ZAMAN
    # AŞIMI DEĞİL: ilerleme oldukça sonsuza kadar sürer.
    ILERLEMESIZ_TUR_SINIRI = 6

    def __init__(
        self,
        istek_gonder: Callable[[], None],
        istek_yolu_denetle: Callable[[], bool],
        parent: Optional[QObject] = None,
        tek_param_iste: Optional[Callable[[int], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._istek_gonder = istek_gonder
        self._istek_yolu_denetle = istek_yolu_denetle
        # Eksik tamamlama yolu. Verilmezse eski davranış (yalnız sessizlik).
        self._tek_param_iste = tek_param_iste
        self.degerler: dict[str, float] = {}
        self._indiriyor = False
        self._son_paket_ts = 0.0
        self._indirme_baslangic_adet = 0
        # ── Eksik takibi (2026-09-03) ────────────────────────────────────
        # FC'nin bildirdiği TOPLAM ve gelen SIRA NUMARALARI. Bunlar olmadan
        # indirmenin bittiği yalnızca TAHMİN edilebiliyordu.
        self._toplam = 0
        self._gelen_idx: set[int] = set()
        self._ilerlemesiz_tur = 0
        self._son_gelen_adet = 0
        self._zamanlayici = QTimer(self)
        self._zamanlayici.setInterval(500)
        self._zamanlayici.timeout.connect(self._sessizlik_denetle)

    # ── İlerleme (mavlink_worker.param_ilerleme'den) ─────────────────────
    def ilerleme_geldi(self, index: int, toplam: int) -> None:
        """PARAM_VALUE'nun sıra no + toplam alanları.

        NEDEN: ArduPilot kaybolan PARAM_VALUE'yu KENDİLİĞİNDEN TEKRARLAMAZ.
        Hangi sıraların geldiğini bilmeden hangilerinin eksik olduğu
        bilinemez, dolayısıyla tamamlanamaz.
        """
        if toplam and toplam > 0:
            self._toplam = int(toplam)
        if index is not None and index >= 0:
            self._gelen_idx.add(int(index))

    def eksik_sayisi(self) -> int:
        if self._toplam <= 0:
            return 0
        return max(0, self._toplam - len(self._gelen_idx))

    # ── Okuma ────────────────────────────────────────────────────────────
    def get(self, ad: str) -> Optional[float]:
        return self.degerler.get(ad)

    @property
    def indiriyor(self) -> bool:
        return self._indiriyor

    # ── Yazma (mavlink_worker.param_received'ten) ────────────────────────
    def param_geldi(self, ad: str, deger: float) -> None:
        self.degerler[str(ad)] = float(deger)
        self._son_paket_ts = time.monotonic()
        if self._indiriyor:
            self.durum.emit(
                f"İndiriliyor… {len(self.degerler)} parametre alındı (son: {ad})"
            )
        self.degisti.emit(str(ad), float(deger))

    # ── Tam indirme ──────────────────────────────────────────────────────
    def indir(self) -> bool:
        """FC'den TÜM parametre listesini ister. Dönüş: istek gönderildi mi."""
        try:
            yol_var = bool(self._istek_yolu_denetle())
        except (TypeError, RuntimeError):
            yol_var = True
        if not yol_var:
            self.durum.emit(
                "✗ Parametre isteği GÖNDERİLEMEDİ — YKİ iç istek kanalı bağlı değil"
            )
            return False
        try:
            self._istek_gonder()
        except Exception:
            logger.exception("Parametre listesi isteği emit edilemedi")
            self.durum.emit("✗ Parametre isteği gönderilemedi (iç hata)")
            return False
        self._indiriyor = True
        self._indirme_baslangic_adet = len(self.degerler)
        self._son_paket_ts = time.monotonic()
        # Yeni indirme: eksik takibi sıfırdan başlar.
        self._toplam = 0
        self._gelen_idx = set()
        self._ilerlemesiz_tur = 0
        self._son_gelen_adet = 0
        self._zamanlayici.start()
        self.durum.emit("İndiriliyor… (tam parametre listesi — telemetri "
                        "linkinde dakikalar sürebilir)")
        return True

    def indirmeyi_durdur(self) -> None:
        """Kullanıcı iptali. FC akışı DURDURULAMAZ — bunu açıkça söylüyoruz.

        NEDEN: MAVLink'te başlamış bir PARAM_REQUEST_LIST akışını iptal eden bir
        mesaj yoktur; yalnızca YKİ tarafındaki bekleme/gösterim durdurulur ve
        gelen paketler önbelleğe yazılmaya devam eder.
        """
        if not self._indiriyor:
            return
        self._indiriyor = False
        self._zamanlayici.stop()
        self.durum.emit(
            f"İzleme durduruldu — {len(self.degerler)} parametre önbellekte. "
            "(FC'nin gönderimi kendiliğinden bitecek; MAVLink'te iptal mesajı yok.)"
        )
        self.indirme_bitti.emit(len(self.degerler))

    def _sessizlik_denetle(self) -> None:
        """Akış durunca EKSİKLERİ TAMAMLAR; zaman aşımıyla pes ETMEZ.

        NEDEN DEĞİŞTİ (2026-09-03): eskiden 3 sn yeni paket gelmezse indirme
        "bitti" sayılıyordu. RFD900A'da (57600 baud) akış ortasında 3 sn
        boşluk olağan olduğu için liste YARIDA bitmiş sayılıyor, üstelik
        kaybolan PARAM_VALUE'lar hiç yeniden istenmiyordu — ArduPilot onları
        KENDİLİĞİNDEN TEKRARLAMAZ. Artık FC'nin bildirdiği TOPLAM ile gelen
        SIRA NUMARALARI karşılaştırılıp eksikler tek tek yeniden isteniyor.
        Durma ölçütü süre değil, İLERLEMENİN DURMASI.
        """
        if not self._indiriyor:
            self._zamanlayici.stop()
            return
        if time.monotonic() - self._son_paket_ts < self.SESSIZLIK_S:
            return

        # Toplam bilinmiyorsa (eski FC / param_count gelmiyorsa) eski
        # gözleme dayalı ölçüte düş.
        if self._toplam <= 0 or self._tek_param_iste is None:
            self._bitir("toplam bilinmiyor — sessizlik ölçütü kullanıldı")
            return

        eksik = [i for i in range(self._toplam) if i not in self._gelen_idx]
        if not eksik:
            self._bitir("TAMAM — eksik yok")
            return

        # İlerleme var mı?
        if len(self._gelen_idx) > self._son_gelen_adet:
            self._ilerlemesiz_tur = 0
            self._son_gelen_adet = len(self._gelen_idx)
        else:
            self._ilerlemesiz_tur += 1

        if self._ilerlemesiz_tur >= self.ILERLEMESIZ_TUR_SINIRI:
            self._bitir(
                f"{len(eksik)} parametre alınamadı "
                f"({self.ILERLEMESIZ_TUR_SINIRI} turdur ilerleme yok)")
            return

        # Eksikleri tur tur yeniden iste (linki boğmadan).
        istenecek = eksik[: self.TUR_BASINA_ISTEK]
        for idx in istenecek:
            try:
                self._tek_param_iste(idx)
            except Exception:
                logger.exception("Eksik parametre #%d istenemedi", idx)
        self._son_paket_ts = time.monotonic()   # yanıt için yeni pencere
        self.durum.emit(
            f"Eksik tamamlanıyor… {len(self._gelen_idx)}/{self._toplam} alındı, "
            f"{len(eksik)} eksik ({len(istenecek)} tanesi yeniden istendi)")

    def _bitir(self, neden: str) -> None:
        self._indiriyor = False
        self._zamanlayici.stop()
        yeni = len(self.degerler) - self._indirme_baslangic_adet
        if self._toplam > 0:
            self.durum.emit(
                f"İndirme bitti — {len(self._gelen_idx)}/{self._toplam} "
                f"parametre ({yeni} yeni). {neden}")
        else:
            self.durum.emit(
                f"İndirme bitti — toplam {len(self.degerler)} parametre "
                f"({yeni} yeni). {neden}")
        self.indirme_bitti.emit(len(self.degerler))


# ═══════════════════════════════════════════════════════════════════════════
# ParamYazici — DOĞRULANMIŞ parametre yazımı
# ═══════════════════════════════════════════════════════════════════════════
class ParamYazici(QObject):
    """set_param gönderir, PARAM_VALUE ile GERİ OKUYARAK doğrular, gerekirse tekrarlar.

    NEDEN (KRİTİK): MAVLink PARAM_SET'in ACK'i YOKTUR. Tek onay, FC'nin aynı
    parametre için gönderdiği PARAM_VALUE'dur. Bu depo aynı gerçeği İHA
    tarafında da biliyor: IHA/missions/base.py `_apply_safe_rtl_alt`, yazdığı
    RTL_ALT'ı `validate_fc_params` ile GERİ OKUYARAK doğruluyor. YKİ tarafında
    ise (tuning_view/advanced_view) yazma sonrası doğrulama YOKTU — kullanıcıya
    "gönderildi" deniyor, gerçekten yazıldığı hiç kanıtlanmıyordu.

    NEDEN tekrar: Paket kaybı olan bir telemetri linkinde (RFD900A) tek bir
    PARAM_SET sessizce kaybolabilir. Yanıt gelmezse aynı yazma tekrarlanır;
    PARAM_SET idempotenttir (aynı değeri yazmak zararsız), bu yüzden tekrar
    güvenlidir.
    """

    # ad, durum ("yazıldı"/"tekrar"/"doğrulandı"/"UYUŞMADI"/"yanıt yok")
    ilerleme = pyqtSignal(str, str)
    # ad, ok, beklenen, okunan (okunmadıysa None yerine beklenen döner + ok False)
    sonuc = pyqtSignal(str, bool, float, float)
    # tamamlandı: başarılı_sayısı, toplam
    bitti = pyqtSignal(int, int)

    YANIT_SURESI_S = 1.5
    MAX_DENEME = 3

    def __init__(self, kanal: MavKanal, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._kanal = kanal
        # ad → {"beklenen": float, "deneme": int, "ts": float}
        self._bekleyen: dict[str, dict] = {}
        self._toplam = 0
        self._basarili = 0
        self._zamanlayici = QTimer(self)
        self._zamanlayici.setInterval(200)
        self._zamanlayici.timeout.connect(self._tik)

    @property
    def calisiyor(self) -> bool:
        return bool(self._bekleyen)

    def yaz(self, istekler: dict) -> bool:
        """{ad: değer} sözlüğünü yazar ve doğrular. Dönüş: başlatıldı mı."""
        if not istekler:
            return False
        if self._bekleyen:
            logger.warning("ParamYazici zaten çalışıyor — yeni istek reddedildi")
            return False
        if not self._kanal.param_yolu_var():
            self.bitti.emit(0, len(istekler))
            return False
        self._toplam = len(istekler)
        self._basarili = 0
        simdi = time.monotonic()
        for ad, deger in istekler.items():
            self._bekleyen[str(ad)] = {
                "beklenen": float(deger), "deneme": 1, "ts": simdi,
            }
            self._kanal.param(str(ad), float(deger))
            self.ilerleme.emit(str(ad), "yazıldı — doğrulama bekleniyor")
        self._zamanlayici.start()
        return True

    def iptal(self) -> None:
        """Bekleyen doğrulamaları bırakır. YAZILANLAR GERİ ALINMAZ.

        NEDEN geri alınmıyor: hangi parametrenin FC'ye ulaştığını bilmiyoruz
        (doğrulama tam da bu yüzden bekliyordu). Eski değeri körlemesine geri
        yazmak, ulaşmış bir yazmayı bozabilir — sessiz geri alma yerine
        kullanıcıya "doğrulanmadı" demek daha güvenli.
        """
        if not self._bekleyen:
            return
        kalan = list(self._bekleyen.keys())
        self._bekleyen.clear()
        self._zamanlayici.stop()
        for ad in kalan:
            self.ilerleme.emit(ad, "İPTAL — doğrulanmadı (yazılmış OLABİLİR)")
        self.bitti.emit(self._basarili, self._toplam)

    def param_geldi(self, ad: str, deger: float) -> None:
        """mavlink_worker.param_received'ten beslenir."""
        kayit = self._bekleyen.get(str(ad))
        if kayit is None:
            return
        beklenen = kayit["beklenen"]
        okunan = float(deger)
        del self._bekleyen[str(ad)]
        if param_esit(okunan, beklenen):
            self._basarili += 1
            self.ilerleme.emit(str(ad), f"✓ doğrulandı ({okunan:g})")
            self.sonuc.emit(str(ad), True, beklenen, okunan)
        else:
            self.ilerleme.emit(
                str(ad), f"✗ UYUŞMADI — yazıldı {beklenen:g}, FC {okunan:g}"
            )
            self.sonuc.emit(str(ad), False, beklenen, okunan)
        self._bitis_denetle()

    def _tik(self) -> None:
        simdi = time.monotonic()
        for ad, kayit in list(self._bekleyen.items()):
            if simdi - kayit["ts"] < self.YANIT_SURESI_S:
                continue
            if kayit["deneme"] < self.MAX_DENEME:
                kayit["deneme"] += 1
                kayit["ts"] = simdi
                self._kanal.param(ad, kayit["beklenen"])
                self.ilerleme.emit(
                    ad, f"yanıt yok — tekrar {kayit['deneme']}/{self.MAX_DENEME}"
                )
            else:
                beklenen = kayit["beklenen"]
                del self._bekleyen[ad]
                self.ilerleme.emit(
                    ad,
                    f"✗ FC yanıt vermedi ({self.MAX_DENEME} deneme) — "
                    "YAZILDIĞI DOĞRULANAMADI",
                )
                # okunan == beklenen gönderiliyor ama ok=False: "değer okunmadı"
                self.sonuc.emit(ad, False, beklenen, beklenen)
        self._bitis_denetle()

    def _bitis_denetle(self) -> None:
        if self._bekleyen:
            return
        self._zamanlayici.stop()
        self.bitti.emit(self._basarili, self._toplam)
