"""
ui/views/camera_mount_cal_view.py — Kamera Montaj Yönü Kalibrasyonu
======================================================================

NEDEN VAR (2026-09-05, sahada operatör bildirdi): Görev 2/3 otonom
denetleyicisinin (IHA/missions/task2/alignment.py::precise_align_drop)
piksel-hata → hız işaret sözleşmesi YALNIZ SITL'de ölçülüp doğrulandı.
O dosyanın kendi yorumu riski açıkça yazıyor: "gerçek araçta kamera 180°
dönük takılıysa işaret ters çevrilmelidir." Sahada TAM BUNUNLA karşılaşıldı
— araç hedefi kamerada gördü ama TAM ZITI yöne düzeltmeye gitti.

Kamera fiziksel olarak yeniden monte edilemiyor (operatör kararı) — bu
yüzden telafi YAZILIMSAL yapılır: `Task2Tuning.cam_mount_rot_deg`
(core.subsystem_monitor._rotate_px_offset — TEK uygulama noktası, x_off/
y_off/angle SHM'den okunur okunmaz döndürülür, aşağı akıştaki HİÇBİR
tüketici değişmez).

Bu panel o AÇIYI BELİRLEMEK içindir — camera_cal_view.py (FOV kalibrasyonu)
ile AYNI kapsam sınırı: config.json/tuning.py'a UZAKTAN YAZMAZ, yalnız
canlı video üzerinden doğru değeri BULUP panoya kopyalar. Uygulamak
(IHA/missions/task2/tuning.py cam_mount_rot_deg alanını değiştirip
deploy etmek) ayrı, elle/onaylı bir adımdır — bu değer bir MISYON JSON
parametresi değil, kod sabiti (cam_boresight_pitch_deg/cam_mount_pitch_deg
ile AYNI kategori), o yüzden canlı uygulama kanalı YOK.

YÖNTEM: cv2/hesaplama GEREKMEZ — yalnız Qt QTransform ile görüntüyü
döndürüp gösterir (ucuz, ana thread'de güvenli, camera_cal_view.py'nin
arka-plan-thread disiplinini gerektirmez). Operatör aracın burnunun TAM
ÖNÜNE geçer, 4 ön ayarı dener, hangisinde kendisi görüntünün ÜST-ORTASINDA
görünüyorsa doğru açı odur.

NEDEN HAM AKIŞ (8080), İŞLENMİŞ (8081) DEĞİL: 8081 HSV-eşiklenmiş vision
akışıdır, çıplak gözle yön okumaya uygun değildir (bkz. camera_cal_view.py
AYNI gerekçe).
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QFont, QImage, QPixmap, QTransform
from PyQt6.QtWidgets import (
    QButtonGroup, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from core import theme
from core.gcs_config import config
from core.video_stream_worker import VideoStreamWorker

logger = logging.getLogger(__name__)

# Ham video (8080) — 8081 HSV-eşiklenmiş, yön okumaya uygun değil
# (bkz. modül başlığı, camera_cal_view.py ile AYNI gerekçe).
_RAW_VIDEO_PORT = 8080

_PRESETS = (0, 90, 180, 270)


class CameraMountCalView(QWidget):
    """Canlı video üzerinde kamera montaj-döndürme açısını (cam_mount_rot_deg)
    belirleme paneli. Bağımsız çalışır — MavKanal/ParamDeposu ALMAZ (bkz.
    CameraCalView docstring'indeki AYNI gerekçe: bu panel FC'ye değil bir
    mission-kodu sabitine karşılık gelir)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._connected = False
        self._rot_deg = 0
        self._son_pixmap: Optional[QPixmap] = None

        self.video_worker = VideoStreamWorker(source="none")
        self.video_worker.frame_ready.connect(self._on_frame)
        self.video_worker.error_occurred.connect(self._on_video_error)
        self.video_worker.start()

        self._build_ui()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ──────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        kok = QVBoxLayout(self)
        kok.setContentsMargins(8, 8, 8, 8)
        kok.setSpacing(8)

        aciklama = QLabel(
            "Aracın BURNUNUN TAM ÖNÜNE, birkaç metre uzağa geçin (kendiniz ya "
            "da belirgin bir referans nesne). Aşağıdaki 4 düğmeyi sırayla "
            "deneyin — hangisinde o referans görüntünün TAM ÜST-ORTASINDA "
            "görünüyorsa gerçek montaj açısı odur. Bu, otonom hizalamanın "
            "hangi yöne düzelteceğini belirleyen TEK sayıdır — yanlış "
            "seçilirse araç hedeften TAM ZITI yöne gider (sahada 2026-09-05 "
            "tarihinde gerçekleşen olay)."
        )
        aciklama.setObjectName("hintLabel")
        aciklama.setWordWrap(True)
        kok.addWidget(aciklama)

        ust = QHBoxLayout()

        # ── SOL: video (seçili açıyla döndürülmüş önizleme) ──
        sol = QVBoxLayout()
        bag_row = QHBoxLayout()
        bag_row.addWidget(QLabel(f"Araç: {config.companion_host}:{_RAW_VIDEO_PORT} (ham akış)"))
        self._btn_baglan = QPushButton("▶ Bağlan")
        self._btn_baglan.clicked.connect(self._on_connect_clicked)
        bag_row.addWidget(self._btn_baglan)
        bag_row.addStretch()
        sol.addLayout(bag_row)

        ust_isaret = QLabel("▲ BURASI \"ÖN\" OLMALI (referans burada görünmeli)")
        ust_isaret.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ust_isaret.setObjectName("hintLabel")
        sol.addWidget(ust_isaret)

        self._video_lbl = QLabel("Video bağlı değil")
        self._video_lbl.setMinimumSize(420, 240)
        self._video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_lbl.setStyleSheet(
            f"background:{theme.c('chart_bg')}; border:1px solid {theme.c('border')};")
        sol.addWidget(self._video_lbl, stretch=1)

        self._durum_lbl = QLabel("● bağlı değil")
        sol.addWidget(self._durum_lbl)
        ust.addLayout(sol, stretch=2)

        # ── SAĞ: döndürme ön ayarları + sonuç ──
        sag = QVBoxLayout()

        rot_grp = QGroupBox("Döndürme Ön Ayarı")
        rot_l = QVBoxLayout(rot_grp)
        btn_row = QHBoxLayout()
        self._rot_group = QButtonGroup(self)
        self._rot_group.setExclusive(True)
        self._rot_buttons: dict[int, QPushButton] = {}
        for deg in _PRESETS:
            b = QPushButton(f"{deg}°")
            b.setCheckable(True)
            b.setChecked(deg == 0)
            b.clicked.connect(lambda _c, d=deg: self._on_preset_secildi(d))
            self._rot_group.addButton(b)
            self._rot_buttons[deg] = b
            btn_row.addWidget(b)
        rot_l.addLayout(btn_row)
        rot_l.addWidget(QLabel(
            "En olası hatalı montaj 180°'dir (kamera kartı ters takılı) — "
            "önce onu deneyin."))
        sag.addWidget(rot_grp)

        sonuc_grp = QGroupBox("Sonuç")
        sonuc_l = QVBoxLayout(sonuc_grp)
        self._sonuc_lbl = QLabel("Seçili: cam_mount_rot_deg = 0")
        self._sonuc_lbl.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        sonuc_l.addWidget(self._sonuc_lbl)

        self._btn_kopyala = QPushButton("Değeri Panoya Kopyala")
        self._btn_kopyala.clicked.connect(self._on_kopyala)
        sonuc_l.addWidget(self._btn_kopyala)

        kapsam = QTextEdit()
        kapsam.setReadOnly(True)
        kapsam.setMaximumHeight(130)
        kapsam.setPlainText(
            "KAPSAM SINIRI: bu panel hiçbir yere UZAKTAN YAZMAZ, yalnız doğru "
            "açıyı bulmanıza yardım eder.\n\n"
            "Uygulamak için: IHA/missions/task2/tuning.py içindeki "
            "cam_mount_rot_deg alanını bu değere ayarlayıp normal deploy "
            "adımını izleyin (bu, cam_boresight_pitch_deg/cam_mount_pitch_deg "
            "ile AYNI kategoride bir kod sabitidir — canlı mission "
            "parametresi DEĞİLDİR)."
        )
        sonuc_l.addWidget(kapsam)
        sag.addWidget(sonuc_grp)

        sag.addStretch()
        ust.addLayout(sag, stretch=1)
        kok.addLayout(ust)

        self._apply_theme_colors()

    def _apply_theme_colors(self) -> None:
        self._durum_stilini_uygula()

    def _durum_stilini_uygula(self) -> None:
        if not self._connected:
            self._durum_lbl.setText("● bağlı değil")
            self._durum_lbl.setStyleSheet(f"color:{theme.c('text_muted')};")
        else:
            self._durum_lbl.setText("● bağlı — canlı önizleme döndürülüyor")
            self._durum_lbl.setStyleSheet(f"color:{theme.c('success')};")

    # ──────────────────────────────────────────────────────────────
    @pyqtSlot()
    def _on_connect_clicked(self) -> None:
        if self._connected:
            self.video_worker.set_source("none")
            self._connected = False
            self._btn_baglan.setText("▶ Bağlan")
            self._video_lbl.setPixmap(QPixmap())
            self._video_lbl.setText("Video bağlı değil")
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
        self._son_pixmap = QPixmap.fromImage(q_img)
        self._yeniden_ciz()
        self._durum_stilini_uygula()

    def _yeniden_ciz(self) -> None:
        if self._son_pixmap is None:
            return
        px = self._son_pixmap
        if self._rot_deg:
            px = px.transformed(QTransform().rotate(self._rot_deg),
                                 Qt.TransformationMode.SmoothTransformation)
        self._video_lbl.setPixmap(px.scaled(
            self._video_lbl.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    @pyqtSlot(str)
    def _on_video_error(self, msg: str) -> None:
        self._durum_lbl.setText(f"HATA: {msg[:80]}")
        self._durum_lbl.setStyleSheet(f"color:{theme.c('danger')};")

    # ──────────────────────────────────────────────────────────────
    def _on_preset_secildi(self, deg: int) -> None:
        self._rot_deg = deg
        self._sonuc_lbl.setText(f"Seçili: cam_mount_rot_deg = {deg}")
        self._yeniden_ciz()

    def _on_kopyala(self) -> None:
        from PyQt6.QtWidgets import QApplication
        metin = f"cam_mount_rot_deg: float = {float(self._rot_deg)}"
        QApplication.clipboard().setText(metin)
        QMessageBox.information(
            self, "Kopyalandı",
            f"\"{metin}\" panoya kopyalandı.\n\n"
            "Bu panel uzaktan yazmaz (bkz. panel açıklaması) — "
            "IHA/missions/task2/tuning.py içine uygulamak ve deploy etmek "
            "ayrı, elle/onaylı bir adımdır.")

    # ──────────────────────────────────────────────────────────────
    def hideEvent(self, event):  # noqa: N802 (Qt API)
        # Sekmeden çıkılınca video akışını durdur (bkz. camera_cal_view.py
        # AYNI desen — diğer video-tüketen paneller de bunu izliyor).
        try:
            if self._connected:
                self.video_worker.set_source("none")
        except Exception:
            logger.exception("Kamera montaj kalibrasyon video durdurma hatası")
        super().hideEvent(event)
