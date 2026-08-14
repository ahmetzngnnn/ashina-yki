"""
map_assets.py — Offline harita (Leaflet) desteği
================================================
3 harita (dashboard/mission/mission_panel) Leaflet CSS/JS'i unpkg.com CDN'inden
çekiyordu → SAHADA İNTERNETSİZ RPi'de harita TAMAMEN BOŞ kalıyordu (kütüphane
yüklenemiyor). Bu modül, vendored (yerel) Leaflet dosyalarını HTML'e INLINE gömer;
gömme, setHtml/tempfile ve qrc/file origin fark etmeksizin çalışır (harici istek yok).

Kullanım (harita yükleyen her yerde):
    from ui.map_assets import localize_leaflet
    self._web.setHtml(localize_leaflet(_MAP_HTML), QUrl("qrc:///"))

Vendoring (bir kez, internet olan makinede):
    bash assets/vendor/fetch_vendor.sh
→ assets/vendor/leaflet/leaflet.js + leaflet.css indirir. Sonra harita çevrimdışı çalışır.

NOT: Uydu TILE'ları (Esri) hâlâ internet ister — tüm tile'ları paketlemek pratik değil.
Vendoring, Leaflet KÜTÜPHANESİNİ çevrimdışı yükler → harita çerçevesi + işaretçiler +
çizilen rota/drone offline çalışır; yalnızca uydu görüntüsü karesi internetsiz boş kalır.
Tam offline uydu için sahaya özel tile önbelleği ayrı bir operasyonel adımdır.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_VENDOR_DIR = Path(__file__).resolve().parent.parent / "assets" / "vendor" / "leaflet"
_LEAFLET_JS = _VENDOR_DIR / "leaflet.js"
_LEAFLET_CSS = _VENDOR_DIR / "leaflet.css"

# CDN <link>/<script> etiketlerini yakalayan desenler
_CSS_LINK_RE = re.compile(
    r'<link[^>]*leaflet@[^>]*\.css[^>]*>', re.IGNORECASE)
_JS_SCRIPT_RE = re.compile(
    r'<script[^>]*src=["\'][^"\']*leaflet@[^"\']*\.js["\'][^>]*>\s*</script>',
    re.IGNORECASE)

_cache: dict = {"js": None, "css": None, "checked": False}


def _load_vendor() -> None:
    if _cache["checked"]:
        return
    _cache["checked"] = True
    try:
        if _LEAFLET_JS.is_file() and _LEAFLET_CSS.is_file():
            _cache["js"] = _LEAFLET_JS.read_text(encoding="utf-8", errors="ignore")
            _cache["css"] = _LEAFLET_CSS.read_text(encoding="utf-8", errors="ignore")
            logger.info("Offline harita: vendored Leaflet inline gömülecek (%s)", _VENDOR_DIR)
        else:
            logger.info("Offline harita: vendored Leaflet YOK → CDN kullanılacak "
                        "(çevrimdışı için: bash assets/vendor/fetch_vendor.sh)")
    except Exception as exc:
        logger.warning("Vendored Leaflet okunamadı: %s — CDN'e düşülüyor", exc)


def is_vendored() -> bool:
    _load_vendor()
    return bool(_cache["js"] and _cache["css"])


def localize_leaflet(html: str) -> str:
    """Vendored Leaflet varsa CDN <link>/<script>'i INLINE ile değiştirir; yoksa aynen döner."""
    _load_vendor()
    if not (_cache["js"] and _cache["css"]):
        return html   # CDN yolu (değişmez)
    # NEDEN lambda: re.sub'ın DÜZ METİN değiştiricisi bir ŞABLONDUR — içindeki
    # ters bölü dizileri (\s, \1, \g) kaçış olarak yorumlanır. Leaflet'in
    # küçültülmüş kaynağı regex literalleri yüzünden \s içerir ve düz metin
    # verildiğinde re.sub "bad escape \s" ile PATLAR. Yani bu fonksiyon
    # vendored dosyalar İNDİRİLDİĞİ anda çöküyordu (dosyalar yokken erken
    # döndüğü için fark edilmemişti). Fonksiyon-değiştirici şablon işlemez,
    # dönen metni birebir yazar.
    out = _CSS_LINK_RE.sub(lambda _m: f"<style>{_cache['css']}</style>", html, count=1)
    out = _JS_SCRIPT_RE.sub(lambda _m: f"<script>{_cache['js']}</script>", out, count=1)
    return out
