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


# ── Ortak altlık katmanları ──────────────────────────────────────────────────
# Üç harita da (dashboard / görev planlama / özel görevler) aynı altlığı
# kullanır; tanım TEK yerde durur ve her HTML'e enjekte edilir.
#
# NEDEN maxNativeZoom 18 (eskiden 19):
#   Esri World Imagery Türkiye'nin BÜYÜK BÖLÜMÜNDE z18'de biter. Ölçüldü
#   (2026-08-14): Türkoğlu sahasında z18 gerçek görüntü (13.798 bayt),
#   z19 ise Esri'nin gri "veri yok" karosu (2.521 bayt, RGB 204,204,204).
#   196 Wayback arşiv sürümünün hiçbirinde de z19 yok. Ankara/İstanbul gibi
#   şehirlerde z19 VAR. maxNativeZoom 19 kalırsa operatör sahada
#   yakınlaştırınca görüntü yerine GRİ EKRAN görüyordu; 18'de ise Leaflet
#   z18'i büyütür — hafif yumuşak ama sürekli ve kullanılabilir görüntü.
#   Şehir içi çalışmalar için z19'lu katman ayrı bir seçenek olarak duruyor.
#
# detectRetina: yüksek DPI ekranda bir üst seviye karo çekip yarı boyutta
# çizer → gözle görülür netlik artışı (1x ekranda hiçbir şey değişmez).
BASE_LAYERS_JS = r"""
(function(){
  var ESRI_IMG   = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
  var ESRI_REF   = 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}';
  var ESRI_ROAD  = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}';

  window.ASHINA_addBaseLayers = function(map){
    var uydu = L.tileLayer(ESRI_IMG, {
      maxZoom: 28, maxNativeZoom: 18, detectRetina: true, attribution: 'Esri'
    });
    // Şehir/yerleşim bölgelerinde Esri z19 sunar (0,23 m/px). Kırsalda bu
    // katman gri karo gösterir — bu yüzden VARSAYILAN DEĞİL, seçenek.
    var uyduDerin = L.tileLayer(ESRI_IMG, {
      maxZoom: 28, maxNativeZoom: 19, detectRetina: true, attribution: 'Esri'
    });
    var yol = L.tileLayer(ESRI_ROAD, {
      maxZoom: 28, maxNativeZoom: 19, detectRetina: true, attribution: 'Esri'
    });
    var etiket = L.tileLayer(ESRI_REF, {
      maxZoom: 28, maxNativeZoom: 18, detectRetina: true, opacity: 0.7
    });

    uydu.addTo(map);
    etiket.addTo(map);

    L.control.layers(
      {'Uydu': uydu, 'Uydu — şehir (z19)': uyduDerin, 'Yol haritası': yol},
      {'Yer adları': etiket},
      {collapsed: true, position: 'topright'}
    ).addTo(map);

    // Metrik ölçek çubuğu — "iki noktayı 1 m arayla işaretle" işi için
    // ekrandaki mesafenin karşılığını doğrudan gösterir.
    L.control.scale({metric: true, imperial: false, maxWidth: 140}).addTo(map);

    // ── Çözünürlük göstergesi ────────────────────────────────────────────
    // Operatör, baktığı detayın GERÇEK mi yoksa büyütülmüş mü olduğunu
    // bilmeli: z21'e yakınlaştırmak görüntüyü keskinleştirmez, yalnızca
    // z18 karosunu büyütür. İşaretleme hassasiyeti gerçek çözünürlükle
    // sınırlıdır — bu kutu onu açıkça yazar.
    var bilgi = L.control({position: 'bottomleft'});
    bilgi.onAdd = function(){
      var d = L.DomUtil.create('div');
      d.style.cssText = 'background:rgba(0,0,0,.72);color:#fff;font:11px/1.35 ' +
        'Consolas,monospace;padding:4px 7px;border-radius:4px;white-space:nowrap;';
      this._d = d; return d;
    };
    bilgi.guncelle = function(){
      var z = map.getZoom();
      var lat = map.getCenter().lat;
      var gercekZ = Math.min(z, 18);           // uydu katmanının gerçek tavanı
      var mpp = 40075016.686 * Math.cos(lat * Math.PI / 180) / (256 * Math.pow(2, z));
      var gercekMpp = 40075016.686 * Math.cos(lat * Math.PI / 180) / (256 * Math.pow(2, gercekZ));
      var txt = 'z' + z + '  ' + mpp.toFixed(2) + ' m/px';
      if (z > gercekZ) {
        txt += '  ⚠ büyütülmüş (gerçek ' + gercekMpp.toFixed(2) + ' m/px)';
        this._d.style.color = '#f0c060';
      } else {
        txt += '  ✓ gerçek çözünürlük';
        this._d.style.color = '#7ee0c0';
      }
      this._d.textContent = txt;
    };
    bilgi.addTo(map);
    map.on('zoomend moveend', function(){ bilgi.guncelle(); });
    bilgi.guncelle();
  };
})();
"""


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
        # CDN yolu: Leaflet gömülmez AMA altlık tanımı yine de enjekte
        # EDİLMELİDİR. Harita HTML'leri artık ASHINA_addBaseLayers(map)
        # çağırıyor; enjeksiyon atlanırsa fonksiyon tanımsız kalır ve
        # harita hiç kurulmaz (boş ekran). Yani bu satır, vendor dosyaları
        # silinse bile haritayı ayakta tutar.
        return with_base_layers(html)
    # NEDEN lambda: re.sub'ın DÜZ METİN değiştiricisi bir ŞABLONDUR — içindeki
    # ters bölü dizileri (\s, \1, \g) kaçış olarak yorumlanır. Leaflet'in
    # küçültülmüş kaynağı regex literalleri yüzünden \s içerir ve düz metin
    # verildiğinde re.sub "bad escape \s" ile PATLAR. Yani bu fonksiyon
    # vendored dosyalar İNDİRİLDİĞİ anda çöküyordu (dosyalar yokken erken
    # döndüğü için fark edilmemişti). Fonksiyon-değiştirici şablon işlemez,
    # dönen metni birebir yazar.
    out = _CSS_LINK_RE.sub(lambda _m: f"<style>{_cache['css']}</style>", html, count=1)
    out = _JS_SCRIPT_RE.sub(lambda _m: f"<script>{_cache['js']}</script>", out, count=1)
    return with_base_layers(out)


def _icon_css() -> str:
    """Leaflet kontrol ikonlarını data URI olarak gömen CSS.

    NEDEN: CSS satır içi gömüldüğünde içindeki `url(images/layers.png)` göreli
    yolu, setHtml'in origin'ine (qrc:///) göre çözülür ve BULUNAMAZ —
    katman seçici düğmesi boş bir kutu olarak çizilir. İkonlar küçük
    (~1-2 KB) olduğundan doğrudan gömmek en sağlam çözüm."""
    import base64
    css = []
    for name, sel in (("layers.png", ".leaflet-control-layers-toggle"),
                      ("layers-2x.png", ".leaflet-retina .leaflet-control-layers-toggle")):
        p = _VENDOR_DIR / "images" / name
        try:
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        except OSError:
            continue
        css.append(f"{sel}{{background-image:url(data:image/png;base64,{b64});"
                   f"background-size:26px 26px;}}")
    return "".join(css)


def with_base_layers(html: str) -> str:
    """Ortak altlık tanımını (ASHINA_addBaseLayers) HTML'e ekler.

    Leaflet yüklendikten SONRA, haritayı kuran betikten ÖNCE çalışmalı;
    bu yüzden Leaflet <script>'inin hemen ardına enjekte edilir. Vendored
    Leaflet inline gömüldüyse desen `</script>` ile eşleşir; CDN yolunda
    da aynı etiket vardır — iki durumda da doğru yere girer."""
    if "ASHINA_addBaseLayers" in html and "window.ASHINA_addBaseLayers" in html:
        return html   # zaten enjekte edilmiş
    marker = "</script>"
    idx = html.find(marker)
    if idx < 0:
        logger.warning("Altlık katmanları enjekte edilemedi: <script> bulunamadı")
        return html
    cut = idx + len(marker)
    return (html[:cut]
            + "\n<style>" + _icon_css() + "</style>\n"
            + "<script>" + BASE_LAYERS_JS + "</script>\n"
            + html[cut:])
