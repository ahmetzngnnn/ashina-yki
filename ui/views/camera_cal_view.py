"""
ui/views/camera_cal_view.py — Kamera Görüş Açısı Kalibrasyonu
================================================================

NEDEN VAR: Görüş açısı (FOV) sistemin en kritik tek sayısıdır — yanlışsa hem
tarama kapsaması hem hedefin GPS izdüşümü bozulur. Kataloğa güvenilemez: bu
bedel bir kez ödendi (IMX296'da katalog "63°" etiketi, gerçek 45.4° çıktı,
bırakma isabeti 0.26 m'den 4-5 m'ye fırladı). `IHA/tools/calibrate_camera.py`
bunu SSH ile, araca fotoğraf gönderip-alarak yapıyordu — bu panel aynı
matematiği YKİ'nin zaten açık olan CANLI video akışı üzerinden yapar:
operatör tahtayı elinde tutarken "bulundu / bulunamadı" anında görür, kör
bir round-trip (20 kare çek → SSH'la indir → script çalıştır → 15'i
bulunamadı → tekrar saha) riskini ortadan kaldırır.

NEDEN İKİ REPO ARASINDA KOD PAYLAŞILMIYOR: `calibrate_camera.py`'deki
fov_from_focal/report matematiği burada BİLEREK yeniden yazıldı, import
EDİLMEDİ — YKİ ve IHA ayrı git depoları, ayrı makinelere dağıtılıyor
(ashina-gorev2 sözleşmesi: "iki repo ayrı, ortak import yok"). İki kopya
ayrı tutulur, biri değişirse diğeri elle senkron edilir.

NEDEN CANLI (8080 HAM AKIŞ), 8081 (İŞLENMİŞ) DEĞİL: satranç tahtası tespiti
gerçek pikselleri ister; 8081 HSV-eşiklenmiş görüş akışıdır, köşe bulmaya
uygun değildir.

KAPSAM SINIRI (dürüstlük notu): bu panel `config.json`'a UZAKTAN YAZMAZ.
Sonucu hesaplar, gösterir ve panoya JSON olarak kopyalar — araca yazmak
companion'ın dosya sistemine dokunmak demektir (FC parametre yazmaktan
FARKLI bir kanal, MAVLink'in kapsamı dışında) ve bu panelin acele inşa
edilen bir sürümüne güvenilecek kadar düşük riskli değil. Sonucu SSH ile
uygulamak operatörün ya da GCS oturumunun elle yaptığı ayrı bir adımdır.

NEDEN TÜM cv2 İŞİ ARKA PLAN THREAD'İNDE (2026-09-04, gerçek arıza sonrası):
İlk sürüm `findChessboardCorners`'ı `_on_frame` içinde, yani ANA ARAYÜZ
THREAD'İNDE, doğrudan çağırıyordu. GCS Pi'de ÖLÇÜLDÜ: tahta kadrajdayken
FAST_CHECK bayraklı arama 900-1450 ms, "Kare Yakala"nın kullandığı TAM
(bayraksız) arama 5.7-6.2 SANİYE sürüyor. Bu, saniyede birkaç kez tetiklenen
bir Qt slotu için ana thread'i dakikalarca bloke etmek demek — YKİ gerçekten
DONDU (%100 CPU, SSH banner exchange bile tamamlanamadı, Pi'nin fiziksel
olarak yeniden başlatılması gerekti). Şimdi üç ağır işlem de (`_CvIsci`
üzerinden) ayrı bir QThread'de koşuyor; ana thread YALNIZCA sonucu bir
sinyalle alıp arayüzü günceller, asla cv2 çağrısının kendisini yapmaz.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Optional

import numpy as np
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QListWidget, QMessageBox,
    QPushButton, QSpinBox, QDoubleSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from core import theme
from core.gcs_config import config
from core.video_stream_worker import VideoStreamWorker

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

logger = logging.getLogger(__name__)

# Ham video her zaman bu porttan — işlenmiş (8081) HSV-eşiklenmiştir,
# köşe tespiti için uygun değildir (bkz. modül başlığı).
_RAW_VIDEO_PORT = 8080

# En az bu kadar geçerli kare olmadan kalibrasyon hesaplanmaz
# (calibrate_camera.py --board ile AYNI eşik).
_MIN_KARE = 5

# Canlı "tahta bulundu" kontrolü YENİ İŞ göndermeden önce en az bu kadar
# bekler — GERÇEK kilit ise `_canli_kontrol_calisiyor` bayrağıdır (bir
# önceki arka-plan kontrolü bitmeden ikincisi ASLA gönderilmez). Bu sabit
# yalnızca tahta hiç yokken (30-70 ms'lik ucuz durum) thread'i gereksiz
# sıklıkta başlatmamak için bir taban.
_CANLI_KONTROL_ARALIK_S = 0.35


class _CvIsci(QThread):
    """Tek seferlik ağır bir cv2 çağrısını ARKA PLANDA koşar, sonucu sinyalle
    döner. Ana thread ASLA cv2 çağrısının kendisini yapmaz — bkz. modül
    başlığındaki 2026-09-04 NEDEN notu (ölçülen 0.9-6.2 sn'lik bloklamalar).
    """
    tamamlandi = pyqtSignal(object)
    hata = pyqtSignal(str)

    def __init__(self, fn, *args, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args

    def run(self) -> None:  # noqa: D102 (Qt API)
        try:
            sonuc = self._fn(*self._args)
        except Exception as exc:  # cv2 hatasi ana thread'i ASLA cokertmesin
            logger.exception("Arka plan cv2 isi basarisiz")
            self.hata.emit(str(exc))
            return
        self.tamamlandi.emit(sonuc)


def _fov_from_focal(f_px: float, size_px: int) -> float:
    """Piksel odak -> görüş açısı (derece). calibrate_camera.py ile AYNI formül."""
    return 2.0 * math.degrees(math.atan((size_px / 2.0) / f_px))


def _canli_kontrol_isi(gray: "np.ndarray", cols: int, rows: int) -> bool:
    """Arka plan thread'inde koşar (bkz. _CvIsci) — HIZLI (FAST_CHECK) tarama."""
    ok, _ = cv2.findChessboardCorners(
        gray, (cols, rows),
        flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_FAST_CHECK)
    return bool(ok)


def _yakala_isi(gray: "np.ndarray", cols: int, rows: int) -> Optional[tuple]:
    """Arka plan thread'inde koşar — TAM (bayraksız) arama + subpixel rafine.
    calibrate_camera.py --board ile AYNI yol. Bulunamazsa None döner."""
    ok, corners = cv2.findChessboardCorners(gray, (cols, rows), None)
    if not ok:
        return None
    corners = cv2.cornerSubPix(
        gray, corners, (11, 11), (-1, -1),
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001))
    return (corners, gray.shape[::-1])   # (corners, (w, h))


def _hesapla_isi(karolar: list, cols: int, rows: int, square: float) -> dict:
    """Arka plan thread'inde koşar — calibrateCamera + FOV matematiği.
    "cal" alt sözlüğü config.json camera.calibration şemasıyla BİREBİR
    (fazladan alan yok, panoya olduğu gibi kopyalanır); exp_vfov_deg/n_kare
    yalnızca sonuç METNİ için, uygulanacak JSON'a KARIŞMAZ."""
    objp = np.zeros((rows * cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square
    shape = karolar[0][1]
    objpoints = [objp.copy() for _ in karolar]
    imgpoints = [c for c, _ in karolar]

    ret, K, dist, _, _ = cv2.calibrateCamera(objpoints, imgpoints, shape, None, None)
    fx, fy = float(K[0, 0]), float(K[1, 1])
    w, h = shape
    hfov = _fov_from_focal(fx, w)
    vfov = _fov_from_focal(fy, h)
    exp_v = 2 * math.degrees(math.atan((h / w) * math.tan(math.radians(hfov) / 2)))

    cal = {
        "fx": round(fx, 2), "fy": round(fy, 2),
        "cx": round(float(K[0, 2]), 2), "cy": round(float(K[1, 2]), 2),
        "hfov_deg": round(hfov, 3), "vfov_deg": round(vfov, 3),
        "width": w, "height": h,
        "dist": [round(float(x), 5) for x in dist.ravel()],
        "reproj_err_px": round(float(ret), 4),
        "method": f"YKI_canli_{time.strftime('%Y%m%d_%H%M')}",
    }
    return {"cal": cal, "exp_vfov_deg": round(exp_v, 3), "n_kare": len(karolar)}


def _qimage_to_gray(qimg: QImage) -> Optional["np.ndarray"]:
    """QImage -> gri tonlamalı numpy dizisi (cv2 için)."""
    if qimg.isNull():
        return None
    q = qimg.convertToFormat(QImage.Format.Format_RGB888)
    w, h = q.width(), q.height()
    if w <= 0 or h <= 0:
        return None
    ptr = q.bits()
    ptr.setsize(q.bytesPerLine() * h)
    arr = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape((h, q.bytesPerLine()))
    rgb = arr[:, : w * 3].reshape((h, w, 3))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


class CameraCalView(QWidget):
    """Canlı video akışından satranç tahtası kalibrasyonu.

    Bağımsız çalışır — MavKanal/ParamDeposu ALMAZ, çünkü sonucu FC'ye değil
    companion'ın config.json'ına yazar (bu panelin kapsamında değil, bkz.
    modül başlığı KAPSAM SINIRI notu). Diğer alt panellerden bu yüzden
    farklı bir imzası var; CalibrationView._alt_panelleri_kur bunu
    parametresiz kurar.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._connected = False
        self._son_gri: Optional["np.ndarray"] = None
        self._son_tahta_bulundu = False
        self._son_kontrol_t = 0.0
        # Her kayıtlı kare: (corners, image_shape_wh)
        self._karolar: list[tuple] = []

        # Arka plan cv2 işçilerinin GÜÇLÜ referansları — Qt, çalışan bir
        # QThread'in Python referansı hiç tutulmazsa çöp toplayabilir.
        # Meşguliyet bayrakları: bir iş sürerken AYNI TÜRDE ikinci iş
        # gönderilmez (busy-gate) — sabit zamanlayıcı DEĞİL, çünkü gerçek
        # süre donanıma ve tahtanın kadrajda olup olmamasına göre 30 ms
        # ile 6 sn arasında değişiyor (ölçüldü, bkz. modül başlığı).
        self._canli_worker: Optional[_CvIsci] = None
        self._canli_kontrol_calisiyor = False
        self._yakala_worker: Optional[_CvIsci] = None
        self._hesapla_worker: Optional[_CvIsci] = None

        self.video_worker = VideoStreamWorker(source="none")
        self.video_worker.frame_ready.connect(self._on_frame)
        self.video_worker.error_occurred.connect(self._on_video_error)
        self.video_worker.start()

        self._build_ui()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

        if not CV2_AVAILABLE:
            self._btn_baglan.setEnabled(False)
            self._durum_lbl.setText(
                "cv2 (opencv-python) kurulu değil — bu panel çalışamaz.")
            self._durum_lbl.setStyleSheet(f"color:{theme.c('danger')};")

    # ──────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        kok = QVBoxLayout(self)
        kok.setContentsMargins(8, 8, 8, 8)
        kok.setSpacing(8)

        aciklama = QLabel(
            "Satranç tahtasını (varsayılan 9×6 iç köşe) kameraya 10-20 farklı "
            "açı/mesafeden göster. Her seferinde \"TAHTA BULUNDU\" yeşile "
            "dönünce \"Kare Yakala\"ya bas. En az 5 kare gerekir, 12-15 "
            "önerilir. Odak/çözünürlük uçuştaki ile AYNIDIR — bu, canlı "
            "akıştan çekildiği için garanti."
        )
        aciklama.setObjectName("hintLabel")
        aciklama.setWordWrap(True)
        kok.addWidget(aciklama)

        ust = QHBoxLayout()

        # ── SOL: video + canlı durum ──
        sol = QVBoxLayout()
        bag_row = QHBoxLayout()
        bag_row.addWidget(QLabel(f"Araç: {config.companion_host}:{_RAW_VIDEO_PORT} (ham akış)"))
        self._btn_baglan = QPushButton("▶ Bağlan")
        self._btn_baglan.clicked.connect(self._on_connect_clicked)
        bag_row.addWidget(self._btn_baglan)
        bag_row.addStretch()
        sol.addLayout(bag_row)

        self._video_lbl = QLabel("Video bağlı değil")
        self._video_lbl.setMinimumSize(420, 240)
        self._video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_lbl.setStyleSheet(
            f"background:{theme.c('chart_bg')}; border:1px solid {theme.c('border')};")
        sol.addWidget(self._video_lbl, stretch=1)

        self._durum_lbl = QLabel("● bağlı değil")
        sol.addWidget(self._durum_lbl)
        ust.addLayout(sol, stretch=2)

        # ── SAĞ: tahta geometrisi + yakalama + sonuç ──
        sag = QVBoxLayout()

        geo_grp = QGroupBox("Tahta Geometrisi")
        geo_l = QHBoxLayout(geo_grp)
        geo_l.addWidget(QLabel("İç köşe (yatay):"))
        self._sp_cols = QSpinBox(); self._sp_cols.setRange(3, 20); self._sp_cols.setValue(9)
        geo_l.addWidget(self._sp_cols)
        geo_l.addWidget(QLabel("(dikey):"))
        # Varsayılanlar operatörün elindeki gerçek tahtaya göre (2026-09-04):
        # 8x10 kare, kare kenarı 8 cm, uzun kenar (10 kare -> 9 iç köşe) yatay
        # tutulacak şekilde -> cols=9 (zaten varsayılandı), rows=7, square=80mm.
        self._sp_rows = QSpinBox(); self._sp_rows.setRange(3, 20); self._sp_rows.setValue(7)
        geo_l.addWidget(self._sp_rows)
        geo_l.addWidget(QLabel("kare (mm):"))
        self._sp_square = QDoubleSpinBox(); self._sp_square.setRange(1.0, 200.0)
        self._sp_square.setValue(80.0)
        geo_l.addWidget(self._sp_square)
        sag.addWidget(geo_grp)

        yak_grp = QGroupBox("Yakalama")
        yak_l = QVBoxLayout(yak_grp)
        btn_row = QHBoxLayout()
        self._btn_yakala = QPushButton("Kare Yakala")
        self._btn_yakala.setObjectName("primaryButton")
        self._btn_yakala.setEnabled(False)
        self._btn_yakala.clicked.connect(self._on_yakala)
        btn_row.addWidget(self._btn_yakala)
        self._btn_geri_al = QPushButton("Son Kareyi Sil")
        self._btn_geri_al.setEnabled(False)
        self._btn_geri_al.clicked.connect(self._on_geri_al)
        btn_row.addWidget(self._btn_geri_al)
        yak_l.addLayout(btn_row)

        self._liste = QListWidget()
        self._liste.setMaximumHeight(120)
        yak_l.addWidget(self._liste)
        sag.addWidget(yak_grp)

        hes_grp = QGroupBox("Kalibrasyon")
        hes_l = QVBoxLayout(hes_grp)
        self._btn_hesapla = QPushButton("Kalibrasyonu Hesapla")
        self._btn_hesapla.setEnabled(False)
        self._btn_hesapla.clicked.connect(self._on_hesapla)
        hes_l.addWidget(self._btn_hesapla)

        self._sonuc = QTextEdit()
        self._sonuc.setReadOnly(True)
        self._sonuc.setPlaceholderText("Sonuç burada görünecek…")
        self._sonuc.setMaximumHeight(160)
        hes_l.addWidget(self._sonuc)

        self._btn_kopyala = QPushButton("Sonucu Panoya Kopyala (config.json parçası)")
        self._btn_kopyala.setEnabled(False)
        self._btn_kopyala.clicked.connect(self._on_kopyala)
        hes_l.addWidget(self._btn_kopyala)
        sag.addWidget(hes_grp)

        sag.addStretch()
        ust.addLayout(sag, stretch=1)
        kok.addLayout(ust)

        self._apply_theme_colors()

    def _apply_theme_colors(self) -> None:
        self._sonuc.setStyleSheet(
            f"QTextEdit{{color:{theme.c('text')};font-family:'Courier New';font-size:11px;}}")
        self._durum_stilini_uygula()

    def _durum_stilini_uygula(self) -> None:
        if not self._connected:
            self._durum_lbl.setText("● bağlı değil")
            self._durum_lbl.setStyleSheet(f"color:{theme.c('text_muted')};")
        elif self._son_tahta_bulundu:
            self._durum_lbl.setText("✓ TAHTA BULUNDU — yakalayabilirsin")
            self._durum_lbl.setStyleSheet(f"color:{theme.c('success')};font-weight:bold;")
        else:
            self._durum_lbl.setText("○ tahta görünmüyor — açıyı/mesafeyi değiştir")
            self._durum_lbl.setStyleSheet(f"color:{theme.c('warning')};")

    # ──────────────────────────────────────────────────────────────
    # Video
    # ──────────────────────────────────────────────────────────────
    @pyqtSlot()
    def _on_connect_clicked(self) -> None:
        if self._connected:
            self.video_worker.set_source("none")
            self._connected = False
            self._btn_baglan.setText("▶ Bağlan")
            self._video_lbl.setPixmap(QPixmap())
            self._video_lbl.setText("Video bağlı değil")
            self._btn_yakala.setEnabled(False)
            self._durum_stilini_uygula()
            return
        url = f"http://{config.companion_host}:{_RAW_VIDEO_PORT}/video"
        self.video_worker.set_source(url)
        self._connected = True
        self._btn_baglan.setText("⏹ Kes")
        self._durum_lbl.setText("● bağlanıyor…")

    @pyqtSlot(QImage)
    def _on_frame(self, q_img: QImage) -> None:
        if not self._connected or q_img.isNull():
            return
        px = QPixmap.fromImage(q_img)
        self._video_lbl.setPixmap(px.scaled(
            self._video_lbl.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

        if not CV2_AVAILABLE:
            return
        # BUSY-GATE (asıl kilit) + zaman tabanı (gereksiz sık gönderimi
        # önler). Bir önceki canlı kontrol İŞİ hâlâ arka planda çalışıyorsa
        # bu kare tamamen atlanır — QImage->numpy dönüşümü bile ana thread'de
        # yapılmaz, çünkü asıl amaç ana thread'i HİÇ meşgul etmemek.
        if self._canli_kontrol_calisiyor:
            return
        now = time.monotonic()
        if now - self._son_kontrol_t < _CANLI_KONTROL_ARALIK_S:
            return
        self._son_kontrol_t = now
        gray = _qimage_to_gray(q_img)   # ucuz (bellek kopyası), cv2 arama DEĞİL
        if gray is None:
            return
        self._son_gri = gray
        cols, rows = self._sp_cols.value(), self._sp_rows.value()

        self._canli_kontrol_calisiyor = True
        self._canli_worker = _CvIsci(_canli_kontrol_isi, gray, cols, rows)
        self._canli_worker.tamamlandi.connect(self._on_canli_kontrol_sonuc)
        self._canli_worker.hata.connect(self._on_canli_kontrol_hata)
        self._canli_worker.finished.connect(self._canli_worker.deleteLater)
        self._canli_worker.start()

    @pyqtSlot(object)
    def _on_canli_kontrol_sonuc(self, bulundu: bool) -> None:
        self._canli_kontrol_calisiyor = False
        self._son_tahta_bulundu = bool(bulundu)
        self._btn_yakala.setEnabled(bool(bulundu) and not self._is_biriminde())
        self._durum_stilini_uygula()

    @pyqtSlot(str)
    def _on_canli_kontrol_hata(self, msg: str) -> None:
        self._canli_kontrol_calisiyor = False
        logger.warning("Canlı tahta kontrolü hata verdi: %s", msg)

    def _is_biriminde(self) -> bool:
        """Yakalama ya da hesaplama işi sürüyorsa True — düğmeler o sırada
        yeni bir iş göndermesin diye canlı kontrol sonucu bunu da hesaba katar."""
        return bool(self._yakala_worker or self._hesapla_worker)

    @pyqtSlot(str)
    def _on_video_error(self, msg: str) -> None:
        self._durum_lbl.setText(f"HATA: {msg[:80]}")
        self._durum_lbl.setStyleSheet(f"color:{theme.c('danger')};")

    # ──────────────────────────────────────────────────────────────
    # Yakalama (ARKA PLANDA — TAM arama ölçüldü 5.7-6.2 sn, bkz. modül başlığı)
    # ──────────────────────────────────────────────────────────────
    def _on_yakala(self) -> None:
        if (self._son_gri is None or not CV2_AVAILABLE
                or self._yakala_worker is not None or self._hesapla_worker is not None):
            return
        cols, rows = self._sp_cols.value(), self._sp_rows.value()
        self._btn_yakala.setEnabled(False)
        self._btn_yakala.setText("Yakalanıyor…")
        w = _CvIsci(_yakala_isi, self._son_gri, cols, rows)
        self._yakala_worker = w
        w.tamamlandi.connect(self._on_yakala_sonuc)
        w.hata.connect(self._on_yakala_hata)
        w.finished.connect(w.deleteLater)
        w.start()

    @pyqtSlot(object)
    def _on_yakala_sonuc(self, sonuc) -> None:
        self._yakala_worker = None
        self._btn_yakala.setText("Kare Yakala")
        self._btn_yakala.setEnabled(self._son_tahta_bulundu)
        if sonuc is None:
            QMessageBox.warning(self, "Yakalama", "Tam taramada köşeler bulunamadı — tekrar dene.")
            return
        corners, shape = sonuc
        self._karolar.append((corners, shape))
        self._liste.addItem(f"Kare {len(self._karolar)}  —  {shape[0]}×{shape[1]}")
        self._btn_geri_al.setEnabled(True)
        self._btn_hesapla.setEnabled(len(self._karolar) >= _MIN_KARE)

    @pyqtSlot(str)
    def _on_yakala_hata(self, msg: str) -> None:
        self._yakala_worker = None
        self._btn_yakala.setText("Kare Yakala")
        self._btn_yakala.setEnabled(self._son_tahta_bulundu)
        QMessageBox.warning(self, "Yakalama", f"Köşe tespiti başarısız: {msg}")

    def _on_geri_al(self) -> None:
        if not self._karolar:
            return
        self._karolar.pop()
        self._liste.takeItem(self._liste.count() - 1)
        self._btn_geri_al.setEnabled(bool(self._karolar))
        self._btn_hesapla.setEnabled(len(self._karolar) >= _MIN_KARE)

    # ──────────────────────────────────────────────────────────────
    # Kalibrasyon hesabı (ARKA PLANDA — gerçek verilerle sn'ler sürebilir)
    # ──────────────────────────────────────────────────────────────
    def _on_hesapla(self) -> None:
        if (len(self._karolar) < _MIN_KARE or not CV2_AVAILABLE
                or self._hesapla_worker is not None):
            return
        cols, rows, square = self._sp_cols.value(), self._sp_rows.value(), self._sp_square.value()
        karolar_kopya = list(self._karolar)   # thread'e göndermeden önce sabitlenir
        self._btn_hesapla.setEnabled(False)
        self._btn_hesapla.setText("Hesaplanıyor…")
        w = _CvIsci(_hesapla_isi, karolar_kopya, cols, rows, square)
        self._hesapla_worker = w
        w.tamamlandi.connect(self._on_hesapla_sonuc)
        w.hata.connect(self._on_hesapla_hata)
        w.finished.connect(w.deleteLater)
        w.start()

    @pyqtSlot(object)
    def _on_hesapla_sonuc(self, sonuc: dict) -> None:
        self._hesapla_worker = None
        self._btn_hesapla.setText("Kalibrasyonu Hesapla")
        self._btn_hesapla.setEnabled(True)

        cal = sonuc["cal"]
        exp_v = sonuc["exp_vfov_deg"]
        ret = cal["reproj_err_px"]
        tutarli = abs(exp_v - cal["vfov_deg"]) < 1.5
        self._son_kalibrasyon = cal

        metin = (
            f"görüntü      : {cal['width']} x {cal['height']} px\n"
            f"kare sayısı  : {sonuc['n_kare']}\n"
            f"yeniden-izdüşüm hatası: {ret:.3f} px  "
            f"({'iyi' if ret < 1.0 else 'YÜKSEK — kareleri gözden geçir'})\n\n"
            f"odak (piksel): f_x = {cal['fx']:.1f}   f_y = {cal['fy']:.1f}\n"
            f"GÖRÜŞ AÇISI  : HFOV = {cal['hfov_deg']:.2f}°   VFOV = {cal['vfov_deg']:.2f}°\n"
            f"beklenen VFOV (en-boydan): {exp_v:.2f}°  "
            f"({'✓ tutarlı' if tutarli else '⚠ SAPMA — piksel kare değil ya da ölçüm hatalı'})\n\n"
            f"Katalog referansı (config.json'daki mevcut): 66.0° / 41.0°\n"
            f"Sapma: {cal['hfov_deg'] - 66.0:+.2f}° / {cal['vfov_deg'] - 41.0:+.2f}°"
        )
        self._sonuc.setPlainText(metin)
        self._btn_kopyala.setEnabled(True)
        if ret >= 1.0:
            QMessageBox.warning(
                self, "Yeniden-izdüşüm hatası yüksek",
                f"{ret:.2f} px — beklenen < 1.0 px. Bazı kareler bulanık ya da "
                "açılı olabilir. \"Son Kareyi Sil\" ile şüphelileri çıkarıp "
                "yeniden hesaplayabilir ya da birkaç kare daha ekleyebilirsin.")

    @pyqtSlot(str)
    def _on_hesapla_hata(self, msg: str) -> None:
        self._hesapla_worker = None
        self._btn_hesapla.setText("Kalibrasyonu Hesapla")
        self._btn_hesapla.setEnabled(True)
        QMessageBox.warning(self, "Kalibrasyon", f"Hesaplama başarısız: {msg}")

    def _on_kopyala(self) -> None:
        cal = getattr(self, "_son_kalibrasyon", None)
        if not cal:
            return
        import json
        from PyQt6.QtWidgets import QApplication
        metin = json.dumps(cal, ensure_ascii=False, indent=2)
        QApplication.clipboard().setText(metin)
        QMessageBox.information(
            self, "Kopyalandı",
            "camera.calibration JSON'u panoya kopyalandı.\n\n"
            "Bu panel config.json'a UZAKTAN YAZMAZ (bkz. panel açıklaması) — "
            "bu metni araca uygulamak ayrı, elle/onaylı bir adımdır.")

    # ──────────────────────────────────────────────────────────────
    def hideEvent(self, event):  # noqa: N802 (Qt API)
        # Sekmeden çıkılınca video akışını durdur — arka planda gereksiz
        # bant genişliği/CPU tüketmesin (diğer video-tüketen paneller aynı
        # deseni izliyor, bkz. CalibrationView.hideEvent / motor testi).
        try:
            if self._connected:
                self.video_worker.set_source("none")
        except Exception:
            logger.exception("Kamera kalibrasyon video durdurma hatası")
        super().hideEvent(event)
