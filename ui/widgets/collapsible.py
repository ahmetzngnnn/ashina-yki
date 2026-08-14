"""
collapsible.py — Açılır/kapanır bölüm (accordion) widget'ı

Görev parametreleri gibi uzun form panellerini tıklanabilir alt kategorilere
ayırır: başlığa tıkla → gövde açılır/kapanır. Kapalı bölüm dikey yer kaplamaz,
böylece dar panelde tüm kategoriler tek bakışta görünür.

Kullanım (yeni bölüm):
    sec = CollapsibleSection("Kamera / Gimbal", expanded=True)
    sec.body_layout().addWidget(widget)
    parent_layout.addWidget(sec)

Kullanım (mevcut QGroupBox'ı sarmala — form kodu hiç değişmez):
    grp = QGroupBox("Kamera / Gimbal")
    ...  # form kurulumu aynen
    parent_layout.addWidget(wrap_group(grp, expanded=False))
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QGroupBox, QSizePolicy,
)

# Başlıktaki ok — tema/ikon dosyasına bağımlı olmasın diye metin karakteri.
_ARROW_OPEN = "▾"
_ARROW_SHUT = "▸"


class CollapsibleSection(QWidget):
    """Tıklanabilir başlık + gizlenebilir gövde.

    `toggled(bool)` sinyali açık/kapalı durumunu yayar (dış kod bölüm
    durumunu kaydetmek isterse kullanır)."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, parent: QWidget | None = None,
                 expanded: bool = False) -> None:
        super().__init__(parent)
        self._title = title

        self._header = QPushButton()
        self._header.setObjectName("sectionHeader")
        self._header.setCheckable(True)
        self._header.setChecked(expanded)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setSizePolicy(QSizePolicy.Policy.Preferred,
                                   QSizePolicy.Policy.Fixed)
        self._header.toggled.connect(self._on_toggled)

        self._body = QWidget()
        self._body.setObjectName("sectionBody")
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(6)
        self._body.setVisible(expanded)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header)
        root.addWidget(self._body)

        self._refresh_header()

    # ── API ──────────────────────────────────────────────────────────────
    def body_layout(self) -> QVBoxLayout:
        return self._body_lay

    def add_widget(self, widget: QWidget) -> None:
        self._body_lay.addWidget(widget)

    def set_expanded(self, expanded: bool) -> None:
        self._header.setChecked(bool(expanded))

    def is_expanded(self) -> bool:
        return self._header.isChecked()

    def title(self) -> str:
        return self._title

    def set_title(self, title: str) -> None:
        self._title = title
        self._refresh_header()

    def header_button(self) -> QPushButton:
        """Başlık butonu — ek gösterge (ör. rozet) eklemek isteyen kod için."""
        return self._header

    # ── İç ───────────────────────────────────────────────────────────────
    def _on_toggled(self, checked: bool) -> None:
        self._body.setVisible(checked)
        self._refresh_header()
        self.toggled.emit(checked)

    def _refresh_header(self) -> None:
        arrow = _ARROW_OPEN if self._header.isChecked() else _ARROW_SHUT
        self._header.setText(f"{arrow}  {self._title}")


def wrap_group(group: QGroupBox, expanded: bool = False,
               title: str | None = None) -> CollapsibleSection:
    """Mevcut bir QGroupBox'ı açılır/kapanır bölüme dönüştürür.

    GroupBox'ın başlığı bölüm başlığına taşınır ve kutu `sectionBody` stiline
    geçer (çift çerçeve/çift başlık olmaz). İçindeki form kodu değişmez —
    bu yüzden dönüşüm mevcut view'larda satır satır düzenleme gerektirmez.
    """
    text = title if title is not None else group.title()
    section = CollapsibleSection(text, expanded=expanded)
    group.setTitle("")
    group.setObjectName("sectionBody")
    # GroupBox'ın QSS'teki 30px üst dolgusu başlık içindi; başlık gittiğine
    # göre gövde stili (sectionBody) devreye girer — yalnızca objectName
    # değişimini Qt'ye bildirmek gerekir.
    group.style().unpolish(group)
    group.style().polish(group)
    section.add_widget(group)
    return section
