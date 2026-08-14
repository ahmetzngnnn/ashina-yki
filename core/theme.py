"""
theme.py — YKİ Tema Sistemi (koyu / açık)

TEK renk kaynağı. Hem QSS (assets/style.qss şablonu) hem de Python tarafındaki
çalışma-anı stilleri ve QPainter çizimleri buradan renk alır.

Kullanım:
    from core import theme
    theme.apply(app)                     # başlangıçta (kayıtlı temayı yükler)
    theme.set_theme("light", app)        # çalışma anında değiştirir
    lbl.setStyleSheet(f"color:{theme.c('success')};")
    theme.notifier().changed.connect(self._on_theme_changed)

KURAL: View kodunda ham hex renk YAZMA. Statik renkler QSS class'ından
(objectName), çalışma anında değişenler `theme.c(token)` üzerinden gelir.
QPainter widget'ları paint sırasında `theme.c(...)` okur; tema değişince
`notifier().changed` sinyali gelir ve widget'lar kendini yeniler.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

DARK = "dark"
LIGHT = "light"

# ── Palet ────────────────────────────────────────────────────────────────────
# İki temada AYNI anahtar kümesi bulunmak ZORUNDA (doğrulama: _validate).
# Açık tema renkleri WCAG AA (>=4.5:1 metin kontrastı) gözetilerek seçildi;
# koyu temanın "parlak" aksanları beyaz zeminde okunmaz, bu yüzden açık temada
# aynı rolün daha koyu tonu kullanılır (accent teal #2dd4bf → #0f766e).
_PALETTES: dict[str, dict[str, str]] = {
    DARK: {
        # Zeminler
        "bg":             "#0d1318",
        "panel":          "#151d25",
        "panel_alt":      "#1b242d",
        "well":           "#0f161c",   # girdi/tablo kuyusu
        "toolbar":        "#10181f",
        "statusbar":      "#0a0f13",
        "row_alt":        "#121a21",
        "overlay":        "#151d25",   # menü/açılır liste zemini
        # Kenarlıklar
        "border":         "#29333d",
        "border_strong":  "#35424d",
        "border_soft":    "#232c34",
        # Metin
        "text":           "#e4ebf1",
        "text_muted":     "#8996a3",
        "text_disabled":  "#55626d",
        # Aksan / semantik
        "accent":         "#2dd4bf",
        "accent_hover":   "#45e0cf",
        "accent_press":   "#23b3a1",
        "accent_soft":    "#123a35",   # aksanın soluk zemini (hover/checked)
        "on_accent":      "#06231f",   # aksan zemini üzerindeki metin
        "info":           "#4aa3e0",
        "success":        "#40c463",
        "warning":        "#e0a83e",
        "danger":         "#ef5a55",
        # Nötr buton
        "btn_bg":         "#1f2a33",
        "btn_hover":      "#27343e",
        "btn_press":      "#18222a",
        "btn_border":     "#35424d",
        "btn_border_hover": "#46545f",
        "btn_disabled_bg":  "#141b21",
        # Semantik butonlar (zemin / metin / kenar)
        "success_bg":     "#1c3a2a", "success_fg": "#6ee7a0", "success_border": "#2f6b48",
        "success_hover":  "#244d38", "success_press": "#16301f",
        "danger_bg":      "#3a1f22", "danger_fg":  "#ff8a85", "danger_border":  "#7a3b3b",
        "danger_hover":   "#4d2629", "danger_press":  "#2e181a",
        "warning_bg":     "#3a2f18", "warning_fg": "#f0c060", "warning_border": "#7a6330",
        "warning_hover":  "#4b3d20",
        # Seçim / vurgular
        "selection_bg":   "#1f2a33",
        "selection_fg":   "#2dd4bf",
        "scroll_handle":  "#35424d",
        "scroll_hover":   "#46545f",
        "grid":           "#1f2932",
        # Grafik (pyqtgraph) — QSS dışı
        "chart_bg":       "#0f161c",
        "chart_fg":       "#d4d4d4",
        # Durum ışığı "sönük" hali
        "idle":           "#55626d",
        # İkon seti klasörü (QSS url() için)
        "iconset":        "dark",
    },
    LIGHT: {
        "bg":             "#eef1f5",
        "panel":          "#ffffff",
        "panel_alt":      "#f7f9fb",
        "well":           "#ffffff",
        "toolbar":        "#e6ebf1",
        "statusbar":      "#e0e6ed",
        "row_alt":        "#f5f7fa",
        "overlay":        "#ffffff",
        "border":         "#ccd5df",
        "border_strong":  "#aab6c2",
        "border_soft":    "#dde4ea",
        "text":           "#16202a",
        "text_muted":     "#55636f",
        "text_disabled":  "#9aa6b2",
        "accent":         "#0f766e",
        "accent_hover":   "#0d9488",
        "accent_press":   "#115e59",
        "accent_soft":    "#d7f0ec",
        "on_accent":      "#ffffff",
        "info":           "#1a6ba8",
        "success":        "#15803d",
        "warning":        "#a25a06",
        "danger":         "#c0281f",
        "btn_bg":         "#ffffff",
        "btn_hover":      "#eef2f7",
        "btn_press":      "#e0e6ed",
        "btn_border":     "#c2ccd7",
        "btn_border_hover": "#9aa8b6",
        "btn_disabled_bg":  "#f0f3f6",
        "success_bg":     "#dcf5e4", "success_fg": "#12652f", "success_border": "#8dcfa4",
        "success_hover":  "#c9eed6", "success_press": "#b6e4c7",
        "danger_bg":      "#fde4e2", "danger_fg":  "#a81f17", "danger_border":  "#eda9a3",
        "danger_hover":   "#fbd3d0", "danger_press":  "#f7c1bd",
        "warning_bg":     "#fdefd6", "warning_fg": "#8a4c05", "warning_border": "#e5bb79",
        "warning_hover":  "#fae4bd",
        "selection_bg":   "#d7f0ec",
        "selection_fg":   "#0f766e",
        "scroll_handle":  "#b6c2ce",
        "scroll_hover":   "#96a5b3",
        "grid":           "#e2e8ee",
        "chart_bg":       "#ffffff",
        "chart_fg":       "#33414f",
        "idle":           "#9aa6b2",
        "iconset":        "light",
    },
}


def _validate() -> None:
    """İki paletin anahtar kümesi birebir aynı olmalı — eksik anahtar, tema
    değişince ham `@token@` metninin QSS'e sızmasına yol açar."""
    dk, lk = set(_PALETTES[DARK]), set(_PALETTES[LIGHT])
    if dk != lk:
        raise RuntimeError(
            f"Tema paletleri uyuşmuyor. Yalnız koyuda: {sorted(dk - lk)}; "
            f"yalnız açıkta: {sorted(lk - dk)}"
        )


_validate()

_current: str = DARK


class _Notifier(QObject):
    """Tema değişimini yayan tekil nesne (widget'lar buna bağlanır)."""
    changed = pyqtSignal(str)   # yeni tema adı


_notifier: _Notifier | None = None


def notifier() -> _Notifier:
    global _notifier
    if _notifier is None:
        _notifier = _Notifier()
    return _notifier


# ── Sorgu ────────────────────────────────────────────────────────────────────
def current() -> str:
    """Aktif tema adı: 'dark' | 'light'."""
    return _current


def is_dark() -> bool:
    return _current == DARK


def c(token: str) -> str:
    """Token'ın aktif temadaki hex değeri. Bilinmeyen token -> metin rengi
    (görünmez widget yerine gözle görülür bir hata)."""
    pal = _PALETTES[_current]
    if token not in pal:
        logger.warning("Bilinmeyen tema token'ı: %s", token)
        return pal["text"]
    return pal[token]


def palette(name: str | None = None) -> dict[str, str]:
    return dict(_PALETTES[name or _current])


# ── QSS üretimi ──────────────────────────────────────────────────────────────
def _assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def build_qss(name: str | None = None) -> str:
    """assets/style.qss şablonundaki @token@ yer tutucularını doldurur."""
    theme_name = name or _current
    pal = _PALETTES[theme_name]
    qss_path = _assets_dir() / "style.qss"
    try:
        text = qss_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("style.qss okunamadı: %s", exc)
        return ""
    # Qt url() her platformda '/' ayracı bekler; paketlenmiş kurulumda cwd
    # farklı olabileceğinden ikon yolları MUTLAK verilir.
    text = text.replace("@ASSETS@", str(_assets_dir()).replace("\\", "/"))
    for key, value in pal.items():
        text = text.replace(f"@{key}@", value)
    return text


def apply(app, name: str | None = None) -> None:
    """Temayı uygulama geneline uygular. name verilmezse aktif tema yenilenir."""
    global _current
    if name:
        _current = LIGHT if str(name).lower() == LIGHT else DARK
    app.setStyleSheet(build_qss(_current))


def set_theme(name: str, app=None) -> None:
    """Temayı değiştirir, QSS'i yeniden uygular ve `changed` sinyalini yayar.

    QSS ile stillenen widget'lar setStyleSheet ile otomatik yenilenir; kendi
    paintEvent'ini çizen widget'lar için tüm widget'lara update() gönderilir
    (sinyale bağlı olmayan çizimler de tazelensin)."""
    global _current
    new = LIGHT if str(name).lower() == LIGHT else DARK
    if new == _current and app is None:
        return
    _current = new
    if app is None:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(build_qss(_current))
        for w in app.allWidgets():
            w.update()
    notifier().changed.emit(_current)
    logger.info("Tema değiştirildi: %s", _current)
