"""
ui/views/saha_testi_view.py — Saha Testi (İnsan-Rehberli Bench) Paneli
=======================================================================

Görev 2/3 için pervanesiz, motor-bağlı, insan-rehberli bench testini (bkz.
`IHA/tools/human_guided_bench.py`) YKİ'den İZLEMEK için ayrı bir sekme.

MİMARİ — NEDEN ayrı, hafif bir görünüm (yeni bir telemetri kanalı İCAT
EDİLMEDİ): `human_guided_bench.py` zaten yön oku + metin + tolerans çemberi +
dwell çubuğunu doğrudan KAREYE ÇİZİP (bkz. o dosyanın `_ciz()` fonksiyonu)
MJPEG olarak `http://<companion-ip>:<port>/video` üzerinden yayınlıyor —
YKİ'nin normal uçuş kamerası akışıyla (`core/video_stream_worker.py`,
`dashboard_view.py`) BİREBİR AYNI protokol. Bu görünüm o worker'ı KENDİ
bağımsız örneğiyle yeniden kullanır; ayrı bir sinyal/veri yolu gerekmez —
yönlendirme YKİ'de AYRICA hesaplanmaz, yalnızca gösterilir.

Bırakma/kavrama ANLARI (checklist) burada OTOMATİK algılanmaz (companion'dan
o alanlar için ayrı bir telemetri sözleşmesi yok) — operatör videoda
"OTONOM BIRAKILDI" bandını GÖRÜNCE elle işaretler. Bu bilinçli bir kapsam
sınırlaması: yeni bir durum-senkron kanalı kurmak yerine mevcut MJPEG
sözleşmesiyle sınırlı kalındı.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QImage, QPixmap, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QGroupBox, QCheckBox, QSplitter, QTextEdit, QApplication,
    QSizePolicy,
)

from core import theme
from core.gcs_config import config
from core.video_stream_worker import VideoStreamWorker

# ── Test sırası (kullanıcı talebiyle sabit) ─────────────────────────────────
_ASAMALAR = [
    ("kirmizi", "1. KIRMIZI hedef — hizalama + otonom bırakma",
     "python3 tools/human_guided_bench.py --renk kirmizi"),
    ("mavi", "2. MAVİ hedef — hizalama + otonom bırakma",
     "python3 tools/human_guided_bench.py --renk mavi"),
    ("yuk", "3. Yük alma — alçalma + otonom kavrama (HENÜZ UYGULANMADI)",
     "— (bkz. plan §E madde 3, ayrı bir sonraki adım)"),
]

_UYARI_METNI = (
    "⚠ PERVANELER SÖKÜLÜ OLMALI. Bu test aracı GERÇEKTEN ARM EDER (motorlar "
    "döner) ve GUIDED moduna geçirir — normal GPS/EKF ön-uçuş kontrolleri "
    "(force=False) AYNEN uygulanır, atlanmaz.\n"
    "Aracın fiziksel konumunu SİZ elinizle değiştirirsiniz — companion "
    "betiği hiçbir zaman uçuş hızı/pozisyon komutu göndermez, yalnızca "
    "ARM/mod ve (hedef merkezlendiğinde) bırakma/kavrama servosu komutu "
    "gönderir."
)


class SahaTestiView(QWidget):
    """Saha testi video izleme + test protokolü paneli."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._checklist_boxes: dict[str, QCheckBox] = {}
        self._checklist_time_labels: dict[str, QLabel] = {}

        # Bu view kendi VideoStreamWorker'ını sahiplenir — normal uçuş
        # kamerası akışından (main_window.video_worker) BAĞIMSIZ, çünkü
        # bench betiği FARKLI bir port/akış yayınlıyor. "none" ile boşta
        # başlar, yalnızca "Bağlan" tıklanınca kaynak atanır.
        self.video_worker = VideoStreamWorker(source="none")
        self.video_worker.frame_ready.connect(self._on_frame)
        self.video_worker.error_occurred.connect(self._on_video_error)
        self.video_worker.start()

        self._build_ui()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ── UI Kurulumu ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        header_row = QHBoxLayout()
        title = QLabel("Saha Testi — İnsan Rehberli Bench")
        title.setObjectName("pageTitle")
        header_row.addWidget(title)
        subtitle = QLabel("Pervanesiz, motor-bağlı hazırlık testi — Görev 2/3 donanım doğrulaması")
        subtitle.setObjectName("mutedLabel")
        header_row.addWidget(subtitle, 0, Qt.AlignmentFlag.AlignBottom)
        header_row.addStretch()
        root.addLayout(header_row)

        # ── Bağlantı çubuğu ────────────────────────────────────────────
        conn_frame = QFrame()
        conn_frame.setObjectName("toolbarFrame")
        conn_layout = QHBoxLayout(conn_frame)
        conn_layout.setContentsMargins(8, 6, 8, 6)
        conn_layout.setSpacing(8)

        conn_layout.addWidget(QLabel("Companion IP:"))
        self._ip_edit = QLineEdit(config.companion_host)
        self._ip_edit.setFixedWidth(140)
        conn_layout.addWidget(self._ip_edit)

        conn_layout.addWidget(QLabel("Port:"))
        self._port_edit = QLineEdit(str(getattr(config, "companion_bench_port", 8090)))
        self._port_edit.setFixedWidth(60)
        conn_layout.addWidget(self._port_edit)

        self._btn_connect = QPushButton("▶  Bağlan")
        self._btn_connect.setObjectName("primaryButton")
        self._btn_connect.clicked.connect(self._on_connect_clicked)
        conn_layout.addWidget(self._btn_connect)

        self._status_lbl = QLabel("● bağlı değil")
        self._status_lbl.setStyleSheet(f"color:{theme.c('text_muted')};font-weight:bold;")
        conn_layout.addWidget(self._status_lbl)
        conn_layout.addStretch()
        root.addWidget(conn_frame)

        # ── Ana bölücü: video | protokol ───────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Sol: video
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        vframe = QFrame()
        vframe.setObjectName("videoFrame")
        vframe.setStyleSheet(f"QFrame#videoFrame{{background:{theme.c('well')};}}")
        vfl = QVBoxLayout(vframe)
        vfl.setContentsMargins(0, 0, 0, 0)
        self._video_label = QLabel("Bağlantı bekleniyor — “Bağlan” ile başlatın")
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setObjectName("mutedLabel")
        self._video_label.setMinimumSize(320, 240)
        self._video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vfl.addWidget(self._video_label, stretch=1)
        left_layout.addWidget(vframe, stretch=1)
        splitter.addWidget(left)

        # Sağ: uyarı + protokol + komut referansı
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        warn_box = QGroupBox("Güvenlik")
        warn_layout = QVBoxLayout(warn_box)
        warn_lbl = QLabel(_UYARI_METNI)
        warn_lbl.setWordWrap(True)
        warn_lbl.setStyleSheet(f"color:{theme.c('danger')};")
        warn_layout.addWidget(warn_lbl)
        right_layout.addWidget(warn_box)

        check_box = QGroupBox("Test protokolü (sırayla)")
        check_layout = QVBoxLayout(check_box)
        for key, label, _cmd in _ASAMALAR:
            row = QHBoxLayout()
            cb = QCheckBox(label)
            cb.toggled.connect(lambda checked, k=key: self._on_checklist_toggled(k, checked))
            self._checklist_boxes[key] = cb
            row.addWidget(cb, 1)
            t_lbl = QLabel("—")
            t_lbl.setObjectName("mutedLabel")
            self._checklist_time_labels[key] = t_lbl
            row.addWidget(t_lbl)
            check_layout.addLayout(row)
        self._btn_reset_checklist = QPushButton("Sıfırla")
        self._btn_reset_checklist.clicked.connect(self._reset_checklist)
        check_layout.addWidget(self._btn_reset_checklist)
        note = QLabel("Not: bu kutular OTOMATİK işaretlenmez — videoda "
                      "“OTONOM BIRAKILDI” bandını gördüğünüzde elle işaretleyin.")
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        check_layout.addWidget(note)
        right_layout.addWidget(check_box)

        cmd_box = QGroupBox("Companion'da çalıştırılacak komutlar")
        cmd_layout = QVBoxLayout(cmd_box)
        self._cmd_text = QTextEdit()
        self._cmd_text.setReadOnly(True)
        self._cmd_text.setFont(QFont("Courier New", 9))
        self._cmd_text.setPlainText(
            "\n".join(f"# {label}\n{cmd}\n" for _k, label, cmd in _ASAMALAR)
        )
        self._cmd_text.setMaximumHeight(160)
        cmd_layout.addWidget(self._cmd_text)
        btn_copy = QPushButton("📋 Kopyala")
        btn_copy.clicked.connect(self._copy_commands)
        cmd_layout.addWidget(btn_copy)
        right_layout.addWidget(cmd_box)

        right_layout.addStretch()
        splitter.addWidget(right)
        splitter.setSizes([700, 380])
        root.addWidget(splitter, 1)

    # ── Bağlantı ───────────────────────────────────────────────────────
    @pyqtSlot()
    def _on_connect_clicked(self) -> None:
        if self._connected:
            self.video_worker.set_source("none")
            self._connected = False
            self._btn_connect.setText("▶  Bağlan")
            self._status_lbl.setText("● bağlı değil")
            self._status_lbl.setStyleSheet(f"color:{theme.c('text_muted')};font-weight:bold;")
            self._video_label.setPixmap(QPixmap())
            self._video_label.setText("Bağlantı kesildi")
            return

        host = self._ip_edit.text().strip() or config.companion_host
        try:
            port = int(self._port_edit.text().strip())
        except ValueError:
            port = 8090
        url = f"http://{host}:{port}/video"
        self.video_worker.set_source(url)
        self._connected = True
        self._btn_connect.setText("⏹  Kes")
        self._status_lbl.setText(f"● bağlanıyor ({url})")
        self._status_lbl.setStyleSheet(f"color:{theme.c('info')};font-weight:bold;")

    @pyqtSlot(QImage)
    def _on_frame(self, q_img: QImage) -> None:
        if not self._connected:
            return
        if q_img.isNull():
            self._video_label.setPixmap(QPixmap())
            self._video_label.setText("Yeniden bağlanıyor…")
            return
        self._status_lbl.setText("● canlı")
        self._status_lbl.setStyleSheet(f"color:{theme.c('success')};font-weight:bold;")
        px = QPixmap.fromImage(q_img)
        self._video_label.setPixmap(
            px.scaled(self._video_label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
        )

    @pyqtSlot(str)
    def _on_video_error(self, msg: str) -> None:
        if not self._connected:
            return
        self._status_lbl.setText(f"● hata: {msg}")
        self._status_lbl.setStyleSheet(f"color:{theme.c('danger')};font-weight:bold;")

    # ── Checklist ──────────────────────────────────────────────────────
    def _on_checklist_toggled(self, key: str, checked: bool) -> None:
        lbl = self._checklist_time_labels.get(key)
        if lbl is None:
            return
        lbl.setText(time.strftime("%H:%M:%S") if checked else "—")

    @pyqtSlot()
    def _reset_checklist(self) -> None:
        for cb in self._checklist_boxes.values():
            cb.setChecked(False)
        for lbl in self._checklist_time_labels.values():
            lbl.setText("—")

    def _copy_commands(self) -> None:
        QApplication.clipboard().setText(self._cmd_text.toPlainText())

    # ── Tema ───────────────────────────────────────────────────────────
    def _apply_theme_colors(self) -> None:
        if self._connected:
            self._status_lbl.setStyleSheet(f"color:{theme.c('success')};font-weight:bold;")
        else:
            self._status_lbl.setStyleSheet(f"color:{theme.c('text_muted')};font-weight:bold;")
