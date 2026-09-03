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
#
# ── v4 PALET KARARLARI: "sıcak grafit + çelik mavisi" ───────────────────────
# NEDEN nötrler SICAK: v3'te tüm zeminler 207-210° mavi-arduvaz ve %25-32
#   doygunluktaydı; ekran soğuk ve "hazır şablon" duruyordu. Yeni nötrler 30-40°
#   (sıcak grafit) ve %6-10 doygunluk — renk eğilimi kasıtlı ama gürültüsüz.
#   Sıcak zemin + soğuk aksan, karanlık kokpit + soğuk gösterge ışığı etkisidir;
#   ikisi birbirini iterek panel sınırlarını renk yardımıyla da ayırır.
# NEDEN TEK aksan: v3'te aksan (turkuaz), info (mavi), success (yeşil) ve
#   warning (amber) hepsi %53-83 doygunlukta yarışıyordu. Artık aksan tek ve
#   orta-düşük doygunlukta (%44) çelik mavisi; `info` aksanın AYNISI —
#   birbirine bu kadar yakın iki maviyi operatör ayıramaz, palet gürültüsü olur.
# NEDEN aksan mavi ailesinden: kırmızı/amber/yeşil alarm renkleridir; aksan
#   onlardan uzak bir hue'da kalmalı ki dolgu/başlık rengi alarmla karışmasın.
# NEDEN durum renkleri KADEMELİ: tehlike en doygun (S%74) ve en dikkat çeken,
#   uyarı orta (S%62), başarı en sakin (S%30) — "her şey aynı sesle bağırmasın";
#   yeşil sürekli görünen normal durumdur, kırmızı istisnadır.
# NEDEN hover yönü temaya göre değişir: dolgulu buton hover'da zeminden UZAKLAŞIR
#   (koyuda açılır, AÇIKTA KOYULAŞIR) — açık temada hover'ı açmak, üstündeki
#   beyaz metnin kontrastını 7.0'dan 5.2'ye düşürüyordu.
#
# ÖLÇÜLEN KONTRAST (WCAG 2.x göreli parlaklık; hedef: gövde ≥7, ikincil ≥4.5)
#   KOYU   text/bg 15.19  text/panel 13.02  text/panel_alt 11.31
#          text_muted/bg 7.60  text_muted/panel_alt 5.65 (en kötü)
#          accent/bg 7.48  success/bg 7.35  warning/bg 8.67  danger/bg 6.40
#          on_accent/accent 7.17
#   AÇIK   text/bg 14.23  text/panel 16.38  text/statusbar 12.38 (en kötü)
#          text_muted/bg 6.43  text_muted/statusbar 5.59 (en kötü)
#          accent/bg 5.93  success/bg 5.75  warning/bg 5.57  danger/bg 5.32
#          on_accent/accent 7.01
# Açık tema güneş altında okunan asıl temadır; koyu temanın parlak aksanları
# beyaz zeminde okunmaz, bu yüzden açık temada aynı rolün koyu tonu kullanılır
# (accent #7ba9cc → #2a5d84).
_PALETTES: dict[str, dict[str, str]] = {
    DARK: {
        # Zeminler — sıcak grafit rampası (30-40°, S %6-10).
        # Basamaklar: bg → panel 1.17×, panel → panel_alt 1.15× (net ama yumuşak)
        "bg":             "#131210",
        "panel":          "#23211f",
        "panel_alt":      "#2e2c28",
        "well":           "#1c1a17",   # girdi/tablo kuyusu — panelden ÇUKUR
        "toolbar":        "#191715",
        "statusbar":      "#0b0a09",
        "row_alt":        "#211f1c",
        "overlay":        "#2e2c28",   # menü/açılır liste zemini (en yüksek yüzey)
        # Kenarlıklar — zeminle aynı sıcak hue'da, üç kademe
        "border":         "#44403b",
        "border_strong":  "#5b554d",
        "border_soft":    "#37332f",
        # Metin — kağıt beyazı (hafif sıcak); nötrlerle aynı sıcaklık ailesinde
        "text":           "#ebe7e0",
        "text_muted":     "#aba49c",
        "text_disabled":  "#787168",
        # Aksan / semantik
        "accent":         "#7ba9cc",   # çelik mavisi, S%44 — neon DEĞİL
        "accent_hover":   "#94bcdb",
        "accent_press":   "#5a8db5",
        "accent_soft":    "#222f3a",   # aksanın soluk zemini (hover/checked)
        "on_accent":      "#0e1820",   # aksan zemini üzerindeki metin (7.17:1)
        "info":           "#7ba9cc",   # NEDEN: aksanla aynı — ikinci mavi yok
        "success":        "#6db08d",   # en sakin (S%30) — normal durum
        "warning":        "#d6aa51",   # orta (S%62)
        "danger":         "#e87568",   # en doygun (S%74) — istisna durum
        # Nötr buton — panelin bir üstünde duran yüzey
        "btn_bg":         "#2e2c28",
        "btn_hover":      "#393632",
        "btn_press":      "#23211f",
        "btn_border":     "#4a4540",
        "btn_border_hover": "#5f5a53",
        "btn_disabled_bg":  "#1e1c1a",
        # Semantik butonlar (zemin / metin / kenar). NEDEN dolgular panelden
        # belirgin ayrılıyor: koyu zeminde soluk tint "arızalı buton" gibi
        # duruyordu; tehlike dolgusu en açık/doygun, başarı en sakin.
        "success_bg":     "#21362b", "success_fg": "#92d3b0", "success_border": "#3d6c54",
        "success_hover":  "#2b4638", "success_press": "#192921",
        "danger_bg":      "#482723", "danger_fg":  "#f2968c", "danger_border":  "#8a4038",
        "danger_hover":   "#59302c", "danger_press":  "#371e1b",
        "warning_bg":     "#3f3522", "warning_fg": "#e3c382", "warning_border": "#766137",
        "warning_hover":  "#50432b",
        # Seçim / vurgular
        "selection_bg":   "#222f3a",
        "selection_fg":   "#7ba9cc",
        "scroll_handle":  "#47433e",
        "scroll_hover":   "#5d5851",
        "grid":           "#312e2b",
        # Grafik (pyqtgraph) — QSS dışı
        "chart_bg":       "#1c1a17",
        "chart_fg":       "#cec8c0",
        # Durum ışığı "sönük" hali
        "idle":           "#68625a",
        # İkon seti klasörü (QSS url() için)
        "iconset":        "dark",
    },
    LIGHT: {
        # Zeminler — sıcak kağıt rampası. NEDEN: v3'te panel/well/overlay üçü de
        # saf #ffffff idi, kart ile zemin ayrışmıyordu. Artık kart en parlak,
        # kuyu bir kademe çukur, gövde zemini kağıt tonunda.
        "bg":             "#eeece7",
        "panel":          "#fdfcfa",
        "panel_alt":      "#f6f4f1",
        "well":           "#faf8f5",
        "toolbar":        "#e7e4df",
        "statusbar":      "#e0ddd6",
        "row_alt":        "#f4f2ee",
        "overlay":        "#fdfcfa",
        "border":         "#cec9c0",
        "border_strong":  "#ada699",
        "border_soft":    "#e1ddd6",
        "text":           "#201d18",
        "text_muted":     "#5a5349",   # güneş altı için v3'ten koyulaştırıldı
        "text_disabled":  "#9c968b",
        "accent":         "#2a5d84",
        "accent_hover":   "#1e4a6b",   # NEDEN: açık temada hover KOYULAŞIR
        "accent_press":   "#153a56",
        "accent_soft":    "#dae7f1",
        "on_accent":      "#ffffff",
        "info":           "#2a5d84",   # NEDEN: aksanla aynı — ikinci mavi yok
        "success":        "#226745",
        "warning":        "#8b4e09",
        "danger":         "#b62a20",
        "btn_bg":         "#fdfcfa",
        "btn_hover":      "#f6f4f1",
        "btn_press":      "#ece9e4",
        "btn_border":     "#d4cec4",
        "btn_border_hover": "#b3ab9e",
        "btn_disabled_bg":  "#f2f0ed",
        "success_bg":     "#daf1e6", "success_fg": "#1a5b3b", "success_border": "#9acbb2",
        "success_hover":  "#c9e9d9", "success_press": "#b9dfcc",
        "danger_bg":      "#fae3e0", "danger_fg":  "#9a2219", "danger_border":  "#e3a7a0",
        "danger_hover":   "#f7d3cf", "danger_press":  "#f2c5c0",
        "warning_bg":     "#f9ecd2", "warning_fg": "#784308", "warning_border": "#deba7c",
        "warning_hover":  "#f5e2bc",
        "selection_bg":   "#dae7f1",
        "selection_fg":   "#2a5d84",
        "scroll_handle":  "#bfbab0",
        "scroll_hover":   "#9f9789",
        "grid":           "#eae7e1",
        "chart_bg":       "#faf8f5",
        "chart_fg":       "#4f4940",
        "idle":           "#9c968b",
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
