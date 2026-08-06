"""
settings_view.py — GCS Bağlantı Ayarları Ekranı

Tüm IP/port değerleri burada düzenlenir ve settings.json'a kaydedilir.
Kaydet butonuna basınca workers yeniden yapılandırılır.
"""
import logging
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QSpinBox, QPushButton,
    QGroupBox, QFrame, QMessageBox,
)

from core.gcs_config import config, DEFAULTS

logger = logging.getLogger(__name__)

_LABEL_STYLE = "color: #aaa; font-size: 12px;"
_INPUT_STYLE = (
    " color:#e0e0e0; border:1px solid #0f3460;"
    "border-radius:4px; padding:4px 8px; font-size:12px;"
)
_BTN_SAVE = (
    " color:white; font-weight:bold; padding:8px 24px;"
    "border-radius:6px; font-size:13px;"
)
_BTN_RESET = (
    " color:#aaa; padding:8px 16px;"
    "border-radius:6px; font-size:12px;"
)
_GROUP_STYLE = (
    "QGroupBox { color:#4ade80; font-weight:bold; font-size:13px;"
    "border:1px solid #0f3460; border-radius:6px; margin-top:8px; padding:12px; }"
    "QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }"
)


class SettingsView(QWidget):
    """Bağlantı ayarları ekranı. Kaydedince settings_changed sinyali yayar."""

    settings_changed = pyqtSignal(dict)   # yeni ayarlar dict'i

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fields: dict[str, QLineEdit | QSpinBox] = {}
        self._build_ui()
        self._load_current()

    # ── UI ────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(20)

        title = QLabel("Bağlantı Ayarları")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: white;")
        root.addWidget(title)

        sub = QLabel("Degisiklikler kaydedilince worker'lar yeniden yapilandirilir.")
        sub.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(sub)

        root.addWidget(self._separator())

        # ── Companion ─────────────────────────────────────────────────
        comp_grp = QGroupBox("Yardımcı Bilgisayar (Companion)")
        comp_grp.setStyleSheet(_GROUP_STYLE)
        comp_layout = QGridLayout(comp_grp)
        comp_layout.setSpacing(10)

        self._add_field(comp_layout, 0, "IP Adresi",
                        "companion_host", is_spin=False,
                        placeholder="192.168.1.10")
        self._add_field(comp_layout, 1, "Görev Portu (TCP)",
                        "companion_mission_port", is_spin=True, spin_range=(1, 65535))
        self._add_field(comp_layout, 2, "Durum Portu (TCP)",
                        "companion_status_port", is_spin=True, spin_range=(1, 65535))
        self._add_field(comp_layout, 3, "Video Portu (HTTP)",
                        "companion_video_port", is_spin=True, spin_range=(1, 65535))

        root.addWidget(comp_grp)

        # ── MAVLink ───────────────────────────────────────────────────
        mav_grp = QGroupBox("MAVLink Bağlantısı (SITL / FCU)")
        mav_grp.setStyleSheet(_GROUP_STYLE)
        mav_layout = QGridLayout(mav_grp)
        mav_layout.setSpacing(10)

        self._add_field(mav_layout, 0, "Host / IP",
                        "mavlink_host", is_spin=False,
                        placeholder="192.168.1.10")
        self._add_field(mav_layout, 1, "Port",
                        "mavlink_port", is_spin=True, spin_range=(1, 65535))

        root.addWidget(mav_grp)

        # ── Mevcut değerler önizleme ───────────────────────────────────
        self._preview = QLabel()
        self._preview.setStyleSheet("color:#888; font-size:11px; font-family:monospace;")
        self._preview.setWordWrap(True)
        root.addWidget(self._preview)

        root.addStretch()

        # ── Butonlar ──────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_reset = QPushButton("Varsayilanlara Sifirla")
        btn_reset.setStyleSheet(_BTN_RESET)
        btn_reset.clicked.connect(self._on_reset)
        btn_row.addWidget(btn_reset)

        btn_save = QPushButton("Kaydet ve Uygula")
        btn_save.setStyleSheet(_BTN_SAVE)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        root.addLayout(btn_row)

    def _add_field(self, layout: QGridLayout, row: int, label: str,
                   key: str, is_spin: bool, placeholder: str = "",
                   spin_range: tuple = (1, 65535)) -> None:
        lbl = QLabel(label + ":")
        lbl.setStyleSheet(_LABEL_STYLE)
        layout.addWidget(lbl, row, 0)

        if is_spin:
            widget = QSpinBox()
            widget.setRange(*spin_range)
            widget.setStyleSheet(_INPUT_STYLE)
            widget.setFixedWidth(120)
        else:
            widget = QLineEdit()
            widget.setPlaceholderText(placeholder)
            widget.setStyleSheet(_INPUT_STYLE)
            widget.setFixedWidth(200)

        layout.addWidget(widget, row, 1)
        layout.setColumnStretch(2, 1)
        self._fields[key] = widget

    def _separator(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet("color: #0f3460;")
        return f

    # ── Değer yönetimi ────────────────────────────────────────────────
    def _load_current(self) -> None:
        for key, widget in self._fields.items():
            val = config.get(key)
            if isinstance(widget, QSpinBox):
                # BUG FIX: config.get() None dönebilir → int(None) TypeError crash
                widget.setValue(int(val) if val is not None else 0)
            else:
                widget.setText(str(val) if val is not None else "")
        self._update_preview()

    def _update_preview(self) -> None:
        vals = self._collect()
        lines = [
            f"Görev: {vals['companion_host']}:{vals['companion_mission_port']}",
            f"Durum: {vals['companion_host']}:{vals['companion_status_port']}",
            f"Video: http://{vals['companion_host']}:{vals['companion_video_port']}/video",
            f"MAVLink: tcp:{vals['mavlink_host']}:{vals['mavlink_port']}",
        ]
        self._preview.setText("  |  ".join(lines))

    def _collect(self) -> dict:
        result = {}
        for key, widget in self._fields.items():
            result[key] = widget.value() if isinstance(widget, QSpinBox) else widget.text().strip()
        return result

    # ── Slotlar ───────────────────────────────────────────────────────
    def _on_save(self) -> None:
        vals = self._collect()

        # Basit doğrulama
        if not vals.get("companion_host"):
            QMessageBox.warning(self, "Hata", "Companion IP adresi bos birakilamaz.")
            return
        if not vals.get("mavlink_host"):
            QMessageBox.warning(self, "Hata", "MAVLink host bos birakilamaz.")
            return

        for key, value in vals.items():
            config.set(key, value)
        config.save()

        self._update_preview()
        self.settings_changed.emit(vals)
        logger.info("Ayarlar kaydedildi ve uygulanmaya gönderildi.")

        QMessageBox.information(
            self, "Kaydedildi",
            "Ayarlar kaydedildi.\n"
            "Bağlantı worker'lari yeniden yapılandırıldı."
        )

    def _on_reset(self) -> None:
        reply = QMessageBox.question(
            self, "Sifirla",
            "Tüm ayarlar fabrika değerlerine dönecek. Emin misiniz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for key, widget in self._fields.items():
                val = DEFAULTS[key]
                if isinstance(widget, QSpinBox):
                    widget.setValue(int(val))
                else:
                    widget.setText(str(val))
            self._update_preview()
