"""
ui/widgets/gorev_tof_panel.py — Ana ekranın SAĞ ALT paneli: GÖREV + TOF
=======================================================================

Operatör sahada, güneş altında, TEK BAKIŞTA iki şeyi birlikte görmek istiyor:

  • solda  GÖREV DETAYI  — hangi görev, hangi aşama, kalan süre, hedef/bırakma,
                           yük ve kavrayıcı durumu,
  • sağda  TOF HALKASI   — Görev 3 yük alma sırasında aracın yükü hangi yönde
                           gördüğü (ui/widgets/tof_widget.py yeniden kullanılır).

TASARIM İLKESİ — "kanıt yokluğu ≠ olumsuz kanıt" (bu deponun kuralı):
  Bu panel HİÇBİR alanı uydurmaz. Telemetride olmayan alan "—" gösterilir ve
  neden boş olduğu dipnotta yazar. Veri GELMİŞ ama BAYATLAMIŞSA sayı canlı
  gibi durmaz: TOF sütunu tümden "veri yok" sayfasına düşer, görev sütununun
  değerleri söner. Sahada en tehlikeli hata, 8 saniye önce donmuş bir TOF
  okumasına bakarak alçalmaya devam etmektir.

RENK: tüm renkler core/theme.py token'larından gelir; sabit renk YOKTUR.
Tema değişince theme.notifier().changed → _apply_theme_colors ile yeniden
boyanır (depodaki mevcut desen).

BAĞLAMA (A takımı için — panel TEK bağlantıyla tam çalışır):
    self._gorev_tof = GorevTofPanel()
    ...
    # dashboard_view.update_rpi_status(data) içinden:
    self._gorev_tof.telemetri_guncelle(data)
  Bu tek çağrı hem TOF'u hem görev alanlarını besler. İstenirse görev kısmı
  ayrıca `gorev_guncelle(data["mission"])` ile de beslenebilir; iki yol da
  desteklenir. FC telemetrisi (update_telemetry) bağlanması GEREKMEZ — o
  pakette bu panelin kullandığı hiçbir alan yoktur.

VERİNİN GERÇEK KAYNAĞI (uydurulmadı, koddan çıkarıldı):
  IHA/comms/status_server.py::_build_status() → YKİ/core/rpi_status_worker.py
  → dashboard_view.update_rpi_status(data). İlgili alanlar:
    data["mission"]         : active, state, pending  (+ status_server beyaz
                              listesi: lap, total_laps, progress_pct,
                              cmd_speed, mission_time_left_s, dropped_targets,
                              dropped_channels, phase, scan_round)
    data["vision_status"]   : connected, target_detected
    data["payload_loaded"]  : bool
    data["servo_state"]     : gimbal_angle, grabber, red_drop, blue_drop,
                              last_cmd_ok
    data["tof_distances"]   : merkez/on/arka/sag/sol (mm) + temas_mm,
                              basamak_mm, gecerli_min_mm, gecerli_maks_mm,
                              halka_acik
"""
from __future__ import annotations

import logging
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import theme

logger = logging.getLogger(__name__)

# TOF halkası tof_widget.py'de ZATEN var — yeniden yazılmaz, gömülür.
try:
    from ui.widgets.tof_widget import TofWidget
except Exception as _exc:                                    # pragma: no cover
    TofWidget = None                                          # type: ignore
    logger.error("TofWidget alınamadı, TOF sütunu boş kalacak: %s", _exc)


# ── Tazelik eşikleri ────────────────────────────────────────────────────────
# NEDEN bu sayılar: companion durum akışı 1 Hz'dir
# (IHA/comms/status_server.py, cfg "status_interval_s" varsayılan 1.0).
# 3 s = ÜÇ paket kaçtı demektir; alçalma çevriminde bu kadar eski bir TOF
# okuması karar vermek için kullanılamaz, o yüzden sayılar EKRANDAN KALKAR.
_TOF_GECIKME_S = 1.8      # bu değerin üstü: uyarı rengi, sayılar hâlâ ekranda
_TOF_BAYAT_S = 3.0        # bu değerin üstü: sayılar kaldırılır, "veri yok"
# Görev durumu yavaş değişir; son bilinen aşamayı göstermek hâlâ işe yarar —
# ama "canlı" görünmemesi için değerler söner.
_GOREV_BAYAT_S = 6.0


# ── Sözlükler ───────────────────────────────────────────────────────────────
# executor.status()["active"] SINIF ADI, ["pending"] ise mission_type dizesidir
# (IHA/missions/executor.py::_MISSION_CLASSES). İkisi de eşlenir.
_GOREV_ADI: dict[str, tuple[str, str]] = {
    "InfinityFlightMission": ("GÖREV 1", "sonsuzluk uçuşu"),
    "INFINITY_FLIGHT":       ("GÖREV 1", "sonsuzluk uçuşu"),
    "TeknofestTask2Mission": ("GÖREV 2·3", "yük bırakma + yük alma"),
    "TEKNOFEST_TASK2":       ("GÖREV 2·3", "yük bırakma + yük alma"),
}

# IHA/missions/base.py::MissionState değerleri → Türkçe + durum rengi.
_DURUM_TR: dict[str, tuple[str, str]] = {
    "IDLE":      ("BEKLEMEDE",       "text_muted"),
    "ARMING":    ("ARM EDİLİYOR",    "warning"),
    "TAKEOFF":   ("KALKIŞ",          "warning"),
    "RUNNING":   ("GÖREV SÜRÜYOR",   "accent"),
    "DROPPING":  ("YÜK BIRAKILIYOR", "accent"),
    "LANDING":   ("İNİŞ",            "warning"),
    "COMPLETED": ("TAMAMLANDI",      "success"),
    "ABORTED":   ("İPTAL EDİLDİ",    "danger"),
    "ERROR":     ("HATA",            "danger"),
}

_RENK_TR = {"red": "KIRMIZI", "blue": "MAVİ"}

# Değer alanları tek aralıklı yazı ister: sayılar sütun sütun hizalanınca
# operatör okumaz, TANIR. Consolas Windows'ta, DejaVu Sans Mono Pi'de var.
_MONO = "'DejaVu Sans Mono','Consolas','Liberation Mono',monospace"


def _sure_metni(saniye: float) -> str:
    """Saniyeyi d:ss biçiminde yazar (kalan süre bütçesi için)."""
    sn = int(max(0.0, round(saniye)))
    return f"{sn // 60:d}:{sn % 60:02d}"


def _sayi(v):
    """Sayıya çevrilebiliyorsa float, çevrilemiyorsa None.

    NEDEN: bool da int'tir; `True` bir mesafe/yüzde DEĞİLDİR. Bozuk bir paket
    yüzünden ekran yolu düşmemeli — panele en çok ihtiyaç duyulan an, tam da
    telemetrinin bozulduğu andır."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Tek ölçüm kutusu
# ═══════════════════════════════════════════════════════════════════════════
class _Olcum(QFrame):
    """Üstte küçük etiket, altta büyük değer — sahada okunacak tek hücre.

    Değerin rengi TOKEN olarak saklanır (hex değil): tema değişince aynı
    anlam yeni paletle yeniden boyanabilsin diye."""

    def __init__(self, etiket: str, ipucu: str = "", buyuk: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("olcumKutu")
        self._buyuk = buyuk
        self._token = "text_muted"
        if ipucu:
            self.setToolTip(ipucu)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 6)
        lay.setSpacing(1)

        self._lbl_etiket = QLabel(etiket)
        self._lbl_deger = QLabel("—")
        self._lbl_deger.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addStretch(1)
        lay.addWidget(self._lbl_etiket)
        lay.addWidget(self._lbl_deger)
        lay.addStretch(1)

        # NEDEN dikeyde Expanding: panel kamera yüksekliğinde duruyor ve
        # içerik o alanı doldurmuyor. Artan boşluk boş bırakılırsa kutular
        # gereksizce küçük kalır; büyüyünce sayı hedefi büyür.
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.temayi_uygula()

    def ayarla(self, metin: str, token: str = "text", ipucu: str | None = None) -> None:
        self._token = token
        self._lbl_deger.setText(metin)
        self._lbl_deger.setStyleSheet(self._deger_qss())
        if ipucu is not None:
            self.setToolTip(ipucu)

    def _deger_qss(self) -> str:
        boy = 19 if self._buyuk else 14
        return (f"color:{theme.c(self._token)}; font-family:{_MONO};"
                f"font-size:{boy}px; font-weight:bold; background:transparent;"
                "border:none;")

    def temayi_uygula(self) -> None:
        self.setStyleSheet(
            f"QFrame#olcumKutu {{ background-color:{theme.c('panel_alt')};"
            f"border:1px solid {theme.c('border_soft')}; border-radius:4px; }}"
        )
        self._lbl_etiket.setStyleSheet(
            f"color:{theme.c('text_muted')}; font-size:9px; font-weight:bold;"
            "letter-spacing:1px; background:transparent; border:none;"
        )
        self._lbl_deger.setStyleSheet(self._deger_qss())


# ═══════════════════════════════════════════════════════════════════════════
# Panel
# ═══════════════════════════════════════════════════════════════════════════
class GorevTofPanel(QWidget):
    """Sağ alt panel: solda görev detayı, sağda TOF halkası.

    SÖZLEŞME (dashboard_view bunu bağlar — imza değişmez):
        telemetri_guncelle(veri: dict)   — companion durum paketi / TOF sözlüğü
        gorev_guncelle(gorev: dict)      — mission alt sözlüğü / tam durum paketi

    İki metot da TOLERANSLIDIR: paket ister tam companion durumu olsun
    (data["mission"], data["tof_distances"] …), ister doğrudan alt sözlük
    olsun tanınır. NEDEN: bağlama işini A takımı yapıyor ve tek sinyale de,
    iki ayrı sinyale de bağlaması mümkün; yanlış tahmin panelin sessizce boş
    kalması demek olurdu.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("gorevTofPanel")

        # ── Ham veri + GELİŞ ANI ────────────────────────────────────────────
        # NEDEN İKİ AYRI SAAT: panele FC telemetrisi de bağlanabilir; o pakette
        # ne TOF ne görev vardır. Tek ortak saat kullanılsaydı FC telemetrisi
        # TOF'u "taze" gösterirdi — sahte canlılık, panelin en tehlikeli hatası.
        self._tof_ts: float | None = None
        self._tof_veri: dict = {}
        self._gorev_ts: float | None = None
        self._m: dict = {}          # mission alt sözlüğü (son paket)
        self._vision: dict = {}
        self._servo: dict = {}
        self._payload: bool | None = None
        # Merkez TOF hücresinin O ANKİ görünürlüğü. Qt hücreyi GÖRÜNÜR yaratır;
        # bu alan gerçeği yansıtmazsa ilk gizleme atlanır ve araçta OLMAYAN bir
        # sensör kalıcı "veri yok" yazar (sahada ARIZA gibi okunur).
        self._merkez_acik = True

        self._build_ui()
        self._apply_theme_colors()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

        # Tazelik saati: veri gelmese de yaş ilerler, ekran bunu SÖYLEMELİ.
        self._saat = QTimer(self)
        self._saat.setInterval(500)
        self._saat.timeout.connect(self._tazelik_denetle)
        self._saat.start()
        self._tazelik_denetle()

    # ── Kurulum ────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        kok = QHBoxLayout(self)
        kok.setContentsMargins(0, 0, 0, 0)
        kok.setSpacing(8)
        # Sütun oranı: görev sütununda daha çok metin var; TOF ızgarası kare
        # kalmalı (tof_widget hücreleri 60×60 asgari).
        kok.addLayout(self._build_gorev_sutunu(), 5)
        kok.addLayout(self._build_tof_sutunu(), 4)

    def _baslik_satiri(self, baslik: str) -> tuple[QHBoxLayout, QLabel, QLabel]:
        """Sütun başlığı + sağda veri yaşı rozeti (ikisi de kartın DIŞINDA).

        NEDEN dışarıda: TOF gövdesi TofWidget'ın KENDİ çerçevesi. Başlığı
        çerçevenin içine koymak, çerçeve içinde çerçeve demek olurdu."""
        satir = QHBoxLayout()
        satir.setContentsMargins(2, 0, 2, 0)
        satir.setSpacing(6)
        lbl = QLabel(baslik)
        rozet = QLabel("—")
        rozet.setAlignment(Qt.AlignmentFlag.AlignCenter)
        satir.addWidget(lbl)
        satir.addStretch(1)
        satir.addWidget(rozet)
        return satir, lbl, rozet

    def _build_gorev_sutunu(self) -> QVBoxLayout:
        sut = QVBoxLayout()
        sut.setContentsMargins(0, 0, 0, 0)
        sut.setSpacing(5)

        satir, self._lbl_bas_gorev, self._rozet_gorev = self._baslik_satiri("GÖREV")
        self._rozet_gorev.setToolTip(
            "Companion durum paketinin yaşı. Akış 1 Hz'dir; birkaç saniyelik\n"
            "gecikme bağlantı sorununa işarettir.")
        sut.addLayout(satir)

        self._gorev_kart = QFrame()
        self._gorev_kart.setObjectName("gorevKart")
        kart = QVBoxLayout(self._gorev_kart)
        kart.setContentsMargins(10, 9, 10, 9)
        kart.setSpacing(7)

        # ── Üst şerit: hangi görev + büyük durum ────────────────────────────
        ust = QHBoxLayout()
        ust.setSpacing(8)
        self._lbl_gorev_no = QLabel("GÖREV —")
        self._lbl_gorev_no.setObjectName("gorevRozet")
        ust.addWidget(self._lbl_gorev_no, 0, Qt.AlignmentFlag.AlignVCenter)
        ust.addStretch(1)
        kart.addLayout(ust)

        self._lbl_durum = QLabel("VERİ YOK")
        self._lbl_durum.setToolTip(
            "İHA görev durum makinesi (IHA/missions/base.py::MissionState).")
        kart.addWidget(self._lbl_durum)

        self._lbl_alt = QLabel("companion durumu bekleniyor")
        self._lbl_alt.setWordWrap(True)
        kart.addWidget(self._lbl_alt)

        # ── Ölçüm ızgarası ─────────────────────────────────────────────────
        izgara = QGridLayout()
        izgara.setSpacing(5)
        self._o_sure = _Olcum(
            "KALAN SÜRE", buyuk=True,
            ipucu="Görev süre bütçesinden kalan (mission.mission_time_left_s).\n"
                  "Bütçenin KENDİSİ tel üzerinde yok — bkz. dipnot.")
        self._o_ilerleme = _Olcum(
            "İLERLEME",
            ipucu="Görev 1 için tur sayacı ve yüzde\n"
                  "(mission.lap / total_laps / progress_pct).")
        self._o_hedef = _Olcum(
            "HEDEF",
            ipucu="Kamera o AN hedef görüyor mu (vision_status.target_detected)\n"
                  "ve görüntü işleme bağlı mı (vision_status.connected).")
        self._o_birakma = _Olcum(
            "BIRAKILAN",
            ipucu="Bırakılmış yükler (mission.dropped_targets).\n"
                  "✓ = FC onayladı, ? = onay alınamadı.")
        self._o_yuk = _Olcum(
            "YÜK",
            ipucu="Araç şu an yük taşıyor mu (payload_loaded).")
        self._o_kavrayici = _Olcum(
            "KAVRAYICI",
            ipucu="Kavrayıcı servolarının son DOĞRULANMIŞ durumu\n"
                  "(servo_state.grabber + last_cmd_ok).")
        izgara.addWidget(self._o_sure, 0, 0)
        izgara.addWidget(self._o_ilerleme, 0, 1)
        izgara.addWidget(self._o_hedef, 1, 0)
        izgara.addWidget(self._o_birakma, 1, 1)
        izgara.addWidget(self._o_yuk, 2, 0)
        izgara.addWidget(self._o_kavrayici, 2, 1)
        izgara.setColumnStretch(0, 1)
        izgara.setColumnStretch(1, 1)
        for _r in range(3):
            izgara.setRowStretch(_r, 1)
        kart.addLayout(izgara, 1)

        # ── Dipnot: TELDE OLMAYAN alanlar ──────────────────────────────────
        # NEDEN: operatör boş bir alanı "sensör bozuk" diye okumamalı. Boşluğun
        # sebebi burada yazar (bu deponun "kanıt yokluğu ≠ olumsuz kanıt" kuralı).
        self._lbl_dipnot = QLabel("")
        self._lbl_dipnot.setWordWrap(True)
        kart.addWidget(self._lbl_dipnot)

        sut.addWidget(self._gorev_kart, 1)
        return sut

    def _build_tof_sutunu(self) -> QVBoxLayout:
        sut = QVBoxLayout()
        sut.setContentsMargins(0, 0, 0, 0)
        sut.setSpacing(5)

        # Başlık KISA tutuldu: uzun başlık sütunun asgari genişliğini büyütür
        # ve dar ekranda TOF ızgarasını ezer. Ayrıntı ipucunda.
        satir, self._lbl_bas_tof, self._rozet_tof = self._baslik_satiri("TOF HALKASI")
        self._lbl_bas_tof.setToolTip(
            "Dört kenar VL53L1X (ön/arka/sağ/sol). Merkez yuvası bu araçta\n"
            "fiziksel olarak YOK, o yüzden merkez hücresi gösterilmiyor.\n"
            "Kısa okuyan kenar yükün YÖNÜNÜ gösterir; araç o yöne kaymalıdır.")
        self._rozet_tof.setToolTip(
            "TOF verisinin yaşı. Alçalma sırasında birkaç saniyelik eski bir\n"
            "okuma karar vermek için KULLANILAMAZ; bayatlayınca sayılar kalkar.")
        sut.addLayout(satir)

        self._tof_stack = QStackedWidget()

        # Sayfa 0 — gerçek halka görselleştirmesi (mevcut widget, dokunulmadı)
        if TofWidget is not None:
            self._tof = TofWidget()
            # Merkez hücresi BAŞLANGIÇTA gizlenir: bu araçta merkez sensör YOK
            # (bkz. _merkez_gorunurlugu). Gerçek okuma gelirse geri açılır —
            # varsayılan "gizli", kanıt gelince "görünür".
            self._merkez_gorunurlugu({})
        else:                                                # pragma: no cover
            self._tof = QLabel("TOF görselleştirmesi yüklenemedi")
            self._tof.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tof_stack.addWidget(self._tof)

        # Sayfa 1 — "veri yok". NEDEN AYRI SAYFA: bayat veride hücreleri
        # oldukları gibi bırakmak canlı sanılır, tof_widget'ı boş sözlükle
        # beslemek ise "GEÇERLİ OKUMA YOK / sensör kontrolü" yazdırır — oysa
        # sorun sensörde değil, BAĞLANTIDA olabilir. İki durum ayrılmalı.
        self._tof_bos = QFrame()
        self._tof_bos.setObjectName("tofBosKart")
        bos_lay = QVBoxLayout(self._tof_bos)
        bos_lay.setContentsMargins(12, 12, 12, 12)
        self._lbl_tof_bos = QLabel("TOF VERİSİ YOK")
        self._lbl_tof_bos.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_tof_bos.setWordWrap(True)
        bos_lay.addStretch(1)
        bos_lay.addWidget(self._lbl_tof_bos)
        bos_lay.addStretch(1)
        self._tof_stack.addWidget(self._tof_bos)
        self._tof_stack.setCurrentWidget(self._tof_bos)

        sut.addWidget(self._tof_stack, 1)
        return sut

    # ── Sözleşme: dış dünya ────────────────────────────────────────────────
    @pyqtSlot(dict)
    def telemetri_guncelle(self, veri: dict) -> None:
        """Companion durum paketi (ya da doğrudan TOF sözlüğü) geldi."""
        if not isinstance(veri, dict) or not veri:
            return

        # (1) TOF — önce standart anahtar, sonra "paket zaten TOF sözlüğü mü?"
        tof = veri.get("tof_distances")
        if not isinstance(tof, dict) or not tof:
            tof = veri if any(k in veri for k in
                              ("on", "arka", "sag", "sol", "merkez")) else None
        if tof:
            self._tof_uygula(tof)

        # (2) Görev/donanım alanları aynı pakette taşınır.
        if any(k in veri for k in
               ("mission", "vision_status", "payload_loaded", "servo_state")):
            self.gorev_guncelle(veri)

    @pyqtSlot(dict)
    def gorev_guncelle(self, gorev: dict) -> None:
        """Görev bilgisi geldi: mission alt sözlüğü VEYA tam durum paketi."""
        if not isinstance(gorev, dict) or not gorev:
            return

        ic = gorev.get("mission")
        if isinstance(ic, dict):
            # NEDEN DEĞİŞTİRME (update değil): status_server her pakette görev
            # sözlüğünün TAMAMINI yollar. Birleştirseydik bırakılan yük listesi
            # KÜÇÜLEMEZDİ — görev yeniden başlasa bile eski bırakma ekranda
            # kalır, operatör dolu sandığı yuvayı boş sanırdı.
            self._m = dict(ic)
        elif any(k in gorev for k in ("active", "state", "pending")):
            self._m = dict(gorev)

        v = gorev.get("vision_status")
        if isinstance(v, dict):
            self._vision = dict(v)
        s = gorev.get("servo_state")
        if isinstance(s, dict):
            self._servo = dict(s)
        if "payload_loaded" in gorev:
            self._payload = bool(gorev.get("payload_loaded"))

        self._gorev_ts = time.monotonic()
        self._tazelik_denetle()

    # ── TOF ────────────────────────────────────────────────────────────────
    def _tof_uygula(self, tof: dict) -> None:
        self._tof_ts = time.monotonic()
        self._tof_veri = dict(tof)
        if TofWidget is not None and isinstance(self._tof, TofWidget):
            try:
                self._tof.update_tof_data(tof)
                self._merkez_gorunurlugu(tof)
            except Exception as exc:
                logger.debug("TOF görselleştirmesi güncellenemedi: %s", exc)
        self._tazelik_denetle()

    def _merkez_gorunurlugu(self, tof: dict) -> None:
        """MERKEZ hücresini yalnızca GERÇEK bir okuma varsa göster.

        NEDEN: bu araçta merkez TOF yuvası FİZİKSEL OLARAK YOK
        (IHA/config.json → tof_ring_hw._comment_merkez_yok; halka dört kenarla
        çalışıyor, geçersiz slota 0.0 yazılıyor). Kalıcı olarak "veri yok"
        yazan bir merkez hücresi sahada ARIZALI SENSÖR gibi okunur — oysa
        sensör hiç takılı değil. Bu yüzden hücre gizlenir; başka bir araçta /
        eski firmware'de gerçek okuma gelirse kendiliğinden geri döner.
        (tof_widget.py DEĞİŞTİRİLMEDİ: yalnızca kendi açtığı `labels`
        sözlüğündeki hücrenin görünürlüğü değiştiriliyor.)
        """
        etiketler = getattr(self._tof, "labels", None)
        if not isinstance(etiketler, dict):
            return
        hucre = etiketler.get((1, 1))
        if hucre is None:
            return
        m = _sayi(tof.get("merkez"))
        var = m is not None and m > 0.0
        if var != self._merkez_acik:
            self._merkez_acik = var
            hucre.setVisible(var)

    # ── Tazelik + çizim ────────────────────────────────────────────────────
    def _tazelik_denetle(self) -> None:
        simdi = time.monotonic()

        # ── TOF ────────────────────────────────────────────────────────────
        if self._tof_ts is None:
            self._rozet_ayarla(self._rozet_tof, "VERİ YOK", "text_muted")
            self._lbl_tof_bos.setText(
                "TOF VERİSİ YOK\n\nCompanion durum akışı bağlı değil ya da\n"
                "halka hiç veri yayınlamadı.")
            self._tof_stack.setCurrentWidget(self._tof_bos)
        else:
            yas = simdi - self._tof_ts
            if yas > _TOF_BAYAT_S:
                self._rozet_ayarla(self._rozet_tof, f"BAYAT {yas:.0f} s", "danger")
                self._lbl_tof_bos.setText(
                    f"TOF VERİSİ BAYAT\n\nSon okuma {yas:.0f} saniye önce geldi.\n"
                    "Ekrandaki sayılar canlı sanılmasın diye kaldırıldı.")
                self._tof_stack.setCurrentWidget(self._tof_bos)
            else:
                token = "warning" if yas > _TOF_GECIKME_S else "success"
                self._rozet_ayarla(self._rozet_tof, f"{yas:.1f} s", token)
                self._tof_stack.setCurrentWidget(self._tof)

        # ── Görev ──────────────────────────────────────────────────────────
        if self._gorev_ts is None:
            self._rozet_ayarla(self._rozet_gorev, "VERİ YOK", "text_muted")
            bayat = True
        else:
            yas = simdi - self._gorev_ts
            bayat = yas > _GOREV_BAYAT_S
            self._rozet_ayarla(
                self._rozet_gorev,
                f"BAYAT {yas:.0f} s" if bayat else f"{yas:.1f} s",
                "danger" if bayat else "success")
        self._gorev_ciz(bayat)

    def _rozet_ayarla(self, rozet: QLabel, metin: str, token: str) -> None:
        rozet.setText(metin)
        rozet.setProperty("_token", token)
        rozet.setStyleSheet(
            f"color:{theme.c(token)}; background-color:{theme.c('panel_alt')};"
            f"border:1px solid {theme.c('border_soft')}; border-radius:3px;"
            f"padding:1px 6px; font-size:10px; font-weight:bold;"
            f"font-family:{_MONO};"
        )

    def _gorev_ciz(self, bayat: bool) -> None:
        """Görev sütununu son bilinen veriyle çizer.

        `bayat` True iken TÜM değerler söner (text_disabled). NEDEN: son bilinen
        aşamayı göstermek işe yarar, ama canlı gibi göstermek yanıltır — kalan
        süre gibi bir sayı bayatken karar için KULLANILAMAZ."""
        m = self._m
        yok = self._gorev_ts is None

        def tk(token: str) -> str:
            """Bayat/veri yok durumunda anlam rengi sönük renge düşer."""
            return "text_disabled" if (bayat or yok) else token

        # ── Hangi görev ────────────────────────────────────────────────────
        aktif = m.get("active")
        bekleyen = m.get("pending")
        if aktif:
            ad, aciklama = _GOREV_ADI.get(str(aktif), ("GÖREV ?", str(aktif)))
        elif bekleyen:
            ad, aciklama = _GOREV_ADI.get(str(bekleyen), ("GÖREV ?", str(bekleyen)))
            aciklama += " — yüklendi, başlatılmadı"
        else:
            ad, aciklama = ("GÖREV —", "yüklü görev yok")
        self._lbl_gorev_no.setText(ad)

        # ── Durum / aşama ──────────────────────────────────────────────────
        if yok:
            durum_metin, durum_token = "VERİ YOK", "text_muted"
            aciklama = "companion durumu bekleniyor"
        else:
            ham = str(m.get("state") or "").upper()
            durum_metin, durum_token = _DURUM_TR.get(
                ham, (ham or "BİLİNMİYOR", "text"))
        faz = m.get("phase")
        if faz:
            aciklama = f"{aciklama} · faz: {faz}"
        if bayat and not yok:
            aciklama = "BAĞLANTI YOK — son bilinen durum · " + aciklama
        self._lbl_durum.setText(durum_metin)
        self._lbl_durum.setStyleSheet(
            f"color:{theme.c(tk(durum_token))}; font-size:22px; font-weight:bold;"
            "letter-spacing:0.5px; background:transparent; border:none;")
        self._lbl_alt.setText(aciklama)

        # ── Kalan süre ─────────────────────────────────────────────────────
        kalan = _sayi(m.get("mission_time_left_s"))
        if kalan is None:
            self._o_sure.ayarla("—", tk("text_muted"))
        else:
            # Eşikler görev bütçesine değil OPERATÖR TEPKİ SÜRESİNE göre:
            # 1 dk altı = artık bir şey yetiştirilemez, 2 dk altı = plan yap.
            sure_token = ("danger" if kalan < 60 else
                          "warning" if kalan < 120 else "text")
            self._o_sure.ayarla(_sure_metni(kalan), tk(sure_token))

        # ── İlerleme (Görev 1 tur sayacı / yüzde) ──────────────────────────
        parcalar = []
        yuzde = _sayi(m.get("progress_pct"))
        tur = _sayi(m.get("lap"))
        toplam = _sayi(m.get("total_laps"))
        if toplam is not None and toplam > 0:
            parcalar.append(f"tur {int(tur or 0)}/{int(toplam)}")
        if yuzde is not None:
            parcalar.append(f"%{yuzde:.0f}")
        self._o_ilerleme.ayarla(" · ".join(parcalar) if parcalar else "—",
                                tk("text" if parcalar else "text_muted"))

        # ── Hedef ──────────────────────────────────────────────────────────
        v = self._vision
        if not v:
            self._o_hedef.ayarla("—", tk("text_muted"))
        elif not v.get("connected"):
            # Kamera yoksa "hedef yok" demek YANLIŞ olur: bakan göz kapalı.
            self._o_hedef.ayarla("KAMERA YOK", tk("danger"))
        elif v.get("target_detected"):
            self._o_hedef.ayarla("GÖRÜLÜYOR", tk("success"))
        else:
            self._o_hedef.ayarla("aranıyor", tk("text_muted"))

        # ── Bırakılan yükler ───────────────────────────────────────────────
        self._birakma_ciz(tk)

        # ── Yük ────────────────────────────────────────────────────────────
        if self._payload is None:
            self._o_yuk.ayarla("—", tk("text_muted"))
        elif self._payload:
            self._o_yuk.ayarla("TAŞINIYOR", tk("accent"))
        else:
            self._o_yuk.ayarla("yok", tk("text_muted"))

        # ── Kavrayıcı ──────────────────────────────────────────────────────
        self._kavrayici_ciz(tk)

        # ── Dipnot: neden boş? ─────────────────────────────────────────────
        eksik = ["yük alma denemesi"]
        if not faz:
            eksik.append("görev fazı")
        if _sayi(m.get("mission_time_budget_s")) is None:
            eksik.append("süre bütçesi")
        self._lbl_dipnot.setText(
            "telemetride yayınlanmıyor: " + " · ".join(eksik))
        self._lbl_dipnot.setToolTip(
            "Bu alanlar İHA'dan YKİ'ye HİÇ gelmiyor, panel eksikliği değil:\n"
            "• yük alma denemesi — teknofest_task2.execute() içinde yerel bir\n"
            "  değişken (attempts); live_status() ile yayınlanmıyor.\n"
            "• görev fazı — status_server beyaz listesinde 'phase' var ama\n"
            "  hiçbir görev bu anahtarı üretmiyor.\n"
            "• süre bütçesi — live_status() 'mission_time_budget_s' üretiyor,\n"
            "  status_server beyaz listesi bu anahtarı GEÇİRMİYOR.")

    def _birakma_ciz(self, tk) -> None:
        m = self._m
        hedefler = m.get("dropped_targets")
        kanallar = m.get("dropped_channels") or {}
        if not isinstance(hedefler, (list, tuple, set)):
            self._o_birakma.ayarla("—", tk("text_muted"),
                                   "Bırakma bilgisi telemetride yok.")
            return
        if not hedefler:
            # BOŞ LİSTE = KANIT: hiçbir yük bırakılmadı ("—" ile aynı şey değil).
            self._o_birakma.ayarla("yok", tk("text_muted"),
                                   "Henüz hiçbir yük bırakılmadı.")
            return

        parcalar, detay, hepsi_onayli = [], [], True
        for renk in hedefler:
            anahtar = str(renk).lower()
            ad = _RENK_TR.get(anahtar, str(renk).upper())
            bilgi = kanallar.get(renk) or kanallar.get(anahtar) or {}
            onayli = bool(bilgi.get("confirmed")) if bilgi else False
            hepsi_onayli = hepsi_onayli and onayli
            parcalar.append(f"{ad}{'✓' if onayli else '?'}")
            detay.append(
                f"{ad}: servo={bilgi.get('servo', '?')} "
                f"kanal={bilgi.get('channel', '?')} "
                f"{'ELLE' if bilgi.get('manual') else 'otonom'} "
                f"{'onaylı' if onayli else 'ONAY YOK'}")
        self._o_birakma.ayarla(
            " · ".join(parcalar),
            tk("success" if hepsi_onayli else "warning"),
            "\n".join(detay) if detay else None)

    def _kavrayici_ciz(self, tk) -> None:
        durum = self._servo.get("grabber") if self._servo else None
        if durum in (None, "", "?"):
            # "?" servo_controller'ın DOĞRULANMAMIŞ durumu: komut gitti ama FC
            # onaylamadı ya da hiç komut verilmedi — "kapalı" DEMEK DEĞİL.
            self._o_kavrayici.ayarla("?" if durum == "?" else "—",
                                     tk("warning" if durum == "?" else "text_muted"))
            return
        metin = {"open": "AÇIK", "close": "KAPALI"}.get(str(durum), str(durum).upper())
        onay = (self._servo.get("last_cmd_ok") or {}).get("grabber")
        if onay is False:
            self._o_kavrayici.ayarla(
                f"{metin} !", tk("danger"),
                "Son kavrayıcı komutunu FC ONAYLAMADI — gerçek mekanik durum "
                "bilinmiyor.")
        else:
            self._o_kavrayici.ayarla(metin, tk("text"))

    # ── Tema ───────────────────────────────────────────────────────────────
    def _apply_theme_colors(self) -> None:
        """Tema değişince tüm sabit stiller + son veriyle dinamik renkler."""
        baslik_qss = (f"color:{theme.c('text_muted')}; font-size:10px;"
                      "font-weight:bold; letter-spacing:1.5px;"
                      "background:transparent; border:none;")
        self._lbl_bas_gorev.setStyleSheet(baslik_qss)
        self._lbl_bas_tof.setStyleSheet(baslik_qss)

        self._gorev_kart.setStyleSheet(
            f"QFrame#gorevKart {{ background-color:{theme.c('panel')};"
            f"border:1px solid {theme.c('border')}; border-radius:6px; }}"
        )
        self._tof_bos.setStyleSheet(
            f"QFrame#tofBosKart {{ background-color:{theme.c('panel')};"
            f"border:1px solid {theme.c('border')}; border-radius:6px; }}"
        )
        self._lbl_tof_bos.setStyleSheet(
            f"color:{theme.c('text_muted')}; font-size:12px; font-weight:bold;"
            "background:transparent; border:none;"
        )
        # Görev rozeti: paneldeki TEK aksanlı öge. NEDEN tek: sahada renk
        # anlam taşır; her kutuyu boyamak "yapay zekâ renk cümbüşü" yapar ve
        # gerçek uyarı rengi kalabalıkta kaybolur.
        self._lbl_gorev_no.setStyleSheet(
            f"color:{theme.c('accent')}; background-color:{theme.c('accent_soft')};"
            f"border:1px solid {theme.c('accent')}; border-radius:3px;"
            f"padding:1px 8px; font-size:11px; font-weight:bold;"
            "letter-spacing:1px;"
        )
        self._lbl_alt.setStyleSheet(
            f"color:{theme.c('text_muted')}; font-size:10px;"
            "background:transparent; border:none;")
        self._lbl_dipnot.setStyleSheet(
            f"color:{theme.c('text_disabled')}; font-size:9px;"
            "background:transparent; border:none;")
        for kutu in (self._o_sure, self._o_ilerleme, self._o_hedef,
                     self._o_birakma, self._o_yuk, self._o_kavrayici):
            kutu.temayi_uygula()
        # Dinamik parçalar (rozetler, durum metni, değerler) son veriyle yeniden.
        self._tazelik_denetle()
