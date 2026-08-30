"""
mission_panel_view.py — Birleşik Görev Paneli (5 Görev Tipi)

Sekmeler:
 1. AI Görevleri    — Nesne tespiti + alan tarama
 2. Görüntü İşleme   — Geometrik/renk tespiti
 3. Yük Bırakma    — Belirli noktalarda yük bırakma
 4. Termal/Multispektral — Isı ve spektrum tarama
 5. Sonsuz Uçuş ()  — TÜBİTAK yarışması Görev 1 (iki direk etrafında figürü)
"""

import json
import math


def _vfov_deg(hfov_deg: float, en_boy: float = 0.75) -> float:
  """HFOV -> VFOV, pinhole tanjant bağıntısıyla (İHA ile AYNI).

  tan(vfov/2) = (H/W)·tan(hfov/2).  4:3 karede H/W = 0.75.
  `vfov = hfov·0.75` kestirmesi 65.9°'de 2.4° hata verir.
  """
  return 2.0 * math.degrees(math.atan(en_boy * math.tan(math.radians(hfov_deg) / 2.0)))
import time
import logging

from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QTimer, QObject, QEvent
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
  QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QPushButton, QLabel,
  QTableWidget, QTableWidgetItem, QGroupBox, QComboBox, QDoubleSpinBox,
  QSpinBox, QHeaderView, QTextEdit, QTabWidget, QCheckBox, QMessageBox,
  QFormLayout, QLineEdit, QScrollArea, QListWidget, QListWidgetItem,
  QAbstractItemView, QRadioButton, QButtonGroup, QFrame, QFileDialog,
  QGridLayout, QInputDialog,
)

from core import theme
from core import mission_profiles
from ui.widgets.collapsible import wrap_group

try:
  from PyQt6.QtWebEngineWidgets import QWebEngineView
  from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile
  _WE = True
except ImportError:
  _WE = False

logger = logging.getLogger(__name__)

# ── Harita HTML ───────────────────────────────────────────────────────────────
# NOT: Aşağıdaki Leaflet/CSS renkleri (rota sarısı #f0a500, kalkış yeşili
# #4ade80, iniş kırmızısı #ff4444, HOME camgöbeği #00e5ff, alan moru #bb86fc,
# uyarı/durum banner'ları) HARİTA SEMANTİĞİDİR — zeminleri YKİ teması değil
# UYDU GÖRÜNTÜSÜ belirler. Tema tokenına çevrilirlerse (ör. açık temada koyu
# metin rengi) koyu asfalt/gölge üzerinde okunamaz hale gelirler. Bu yüzden
# temadan BAĞIMSIZ sabit tutulur ve _MAP_*_COLOR sabitleriyle birebir aynıdır.
_MAP_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
html,body,#map{width:100%;height:100%;margin:0;padding:0;}
.wi{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;
  justify-content:center;font-weight:bold;font-size:10px;border:2px solid #000;}
#sb{position:absolute;top:8px;left:50%;transform:translateX(-50%);
  background:rgba(0,120,215,.9);padding:4px 14px;
  border-radius:12px;font-weight:bold;font-size:12px;z-index:1000;
  display:none;pointer-events:none;}
/* NEDEN: Seçim modu KAPALIYKEN harita tıklaması sessizce hiçbir şey yapmıyordu;
   operatör panelin bozuk olduğunu sanıyordu. Artık kısa bir uyarı banner'ı çıkar. */
#wb{position:absolute;top:8px;left:50%;transform:translateX(-50%);
  background:rgba(240,165,0,.95);color:#1a1200;padding:5px 14px;
  border-radius:12px;font-weight:bold;font-size:12px;z-index:1001;
  display:none;pointer-events:none;
  font-family:Segoe UI,Arial,sans-serif;}
</style></head><body>
<div id="sb">&#127919; HARITA SEÇIM MODU AKTİF — Noktaya Tıkla</div>
<div id="wb">&#9888; Seçim modu KAPALI — sağ panelden &quot;Harita Seçim Modu&quot;nu açın</div>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
var map=L.map('map',{zoomControl:true,attributionControl:false,maxZoom:28}).setView([39.925,32.866],15);
ASHINA_addBaseLayers(map);   // altlık + katman seçici + ölçek + çözünürlük göstergesi (ui/map_assets.py)

var wps=[],mrks=[],xtra=[],pend=[],sel=false;
var rl=L.polyline([],{color:'#f0a500',weight:2,dashArray:'5,4'}).addTo(map);

var wbT=null;
/* Seçim modu kapalıyken tıklanırsa sessiz kalma — kullanıcıyı bilgilendir. */
function warnSel(){
 var e=document.getElementById('wb');
 if(!e)return;
 e.style.display='block';
 if(wbT)clearTimeout(wbT);
 wbT=setTimeout(function(){e.style.display='none';},2800);
}
map.on('click',function(e){
 if(sel){pend.push({lat:e.latlng.lat,lon:e.latlng.lng});}
 else{warnSel();}
});

function setSel(on){
 sel=on;
 document.getElementById('sb').style.display=on?'block':'none';
 if(on){ /* mod açıldı → uyarı banner'ı anlamsız, gizle */
  var w=document.getElementById('wb');
  if(w)w.style.display='none';
  if(wbT)clearTimeout(wbT);
 }
 map.getContainer().style.cursor=on?'crosshair':'';
}
function mkIco(bg,t){
 return L.divIcon({
  html:'<div class="wi" style="background:'+bg+'">'+t+'</div>',
  iconSize:[26,26],iconAnchor:[13,13],className:''
 });
}
function addWP(lat,lon,alt,idx,type){
 var bg=type==='TAKEOFF'?'#4ade80':type==='LAND'?'#ff4444':'#f0a500';
 var lb=type==='TAKEOFF'?'T':type==='LAND'?'L':String(idx);
 var m=L.marker([lat,lon],{icon:mkIco(bg,lb),draggable:true}).addTo(map);
 m.bindPopup('<b>'+type+'</b> WP#'+idx+'<br>'+lat.toFixed(6)+', '+lon.toFixed(6)+'<br>Alt: '+alt+'m');
 m.on('dragend',function(ev){
  var ll=ev.target.getLatLng();
  pend.push({lat:ll.lat,lon:ll.lng,update_idx:idx});
 });
 mrks.push({m:m,idx:idx});
 wps.push({idx:idx,lat:lat,lon:lon,alt:alt,type:type});
 rl.setLatLngs(wps.map(function(w){return[w.lat,w.lon];}));
}
function clearWPs(){
 mrks.forEach(function(e){map.removeLayer(e.m);});
 mrks=[];wps=[];pend=[];rl.setLatLngs([]);clearX();
}
function clearX(){xtra.forEach(function(l){map.removeLayer(l);});xtra=[];}
function drawPoly(pts,col){
 clearX();
 xtra.push(L.polygon(pts.map(function(p){return[p.lat,p.lon];}),
  {color:col||'#bb86fc',weight:1.5,fillOpacity:0.1}).addTo(map));
}
function drawCirc(lat,lon,r,col){
 clearX();
 xtra.push(L.circle([lat,lon],{radius:r,color:col||'#bb86fc',weight:2,fillOpacity:0.1}).addTo(map));
}
/* ── ROTA ÖNİZLEME ─────────────────────────────────────────────────────────
   AYRI katman dizisi (prv), xtra DEĞİL. NEDEN: drawPoly/drawCirc ikisi de
   girişte clearX() çağırıyor, yani xtra'yı paylaşan her çizim bir öncekini
   siler. Tarama alanı poligonu ile rota önizlemesi aynı anda görünmeli;
   aynı diziye konsalardı poligon çizilir çizilmez rota kaybolurdu. */
var prv=[];
function clearRoute(){prv.forEach(function(l){map.removeLayer(l);});prv=[];}
function drawRoute(pts,col,fit){
 clearRoute();
 if(!pts||pts.length<2)return;
 var ll=pts.map(function(p){return[p.lat,p.lon];});
 /* Altta kalın yarı saydam gövde, üstte ince parlak çizgi: uydu görüntüsü
    üzerinde tek renk çizgi zeminle karışıyordu (koyu asfalt/gölge). */
 prv.push(L.polyline(ll,{color:'#000',weight:6,opacity:0.35}).addTo(map));
 prv.push(L.polyline(ll,{color:col||'#4ade80',weight:2.5,opacity:0.95}).addTo(map));
 /* Yön göstergesi: rotanın 1/4, 2/4, 3/4 noktalarına gidiş yönünü gösteren
    üçgen. Ok başı olmadan operatör hangi yönde uçulacağını göremiyor. */
 [0.25,0.5,0.75].forEach(function(f){
  var i=Math.max(1,Math.min(ll.length-1,Math.round(f*(ll.length-1))));
  var a=ll[i-1],b=ll[i];
  var ang=Math.atan2(b[1]-a[1],b[0]-a[0])*180/Math.PI;
  prv.push(L.marker(b,{interactive:false,icon:L.divIcon({
    html:'<div style="width:0;height:0;border-left:5px solid transparent;'+
         'border-right:5px solid transparent;border-bottom:11px solid '+(col||'#4ade80')+';'+
         'transform:rotate('+ang+'deg);transform-origin:50% 50%;"></div>',
    className:'',iconSize:[11,11],iconAnchor:[5,5]})}).addTo(map));
 });
 /* Başlangıç/bitiş: aynı noktada birleşen kapalı rotalarda ikisi üst üste
    gelir, bu yüzden bitiş biraz daha küçük ve içi boş çizilir. */
 prv.push(L.circleMarker(ll[0],{radius:5,color:'#4ade80',fillColor:'#4ade80',
   fillOpacity:1,weight:2}).bindTooltip('Rota başı').addTo(map));
 prv.push(L.circleMarker(ll[ll.length-1],{radius:4,color:'#ff4444',
   fillOpacity:0,weight:2}).bindTooltip('Rota sonu').addTo(map));
 if(fit){try{map.fitBounds(L.latLngBounds(ll),{padding:[40,40]});}catch(e){}}
}
function setHome(lat,lon){
 if(window._hm)map.removeLayer(window._hm);
 window._hm=L.marker([lat,lon],{icon:mkIco('#00e5ff','H')}).addTo(map);
 window._hm.bindPopup('<b>HOME / KALKIŞ NOKTASI</b><br>'+lat.toFixed(6)+','+lon.toFixed(6));
}
function centerMap(lat,lon){map.setView([lat,lon],16);}
function ctr(lat,lon,z){map.setView([lat,lon],z||16);}

var droneMarker = null;
var isFirstFix = true;
function set_drone_pos(lat, lon, hdg) {
  if (!droneMarker) {
    droneMarker = L.marker([lat, lon], {
      icon: L.divIcon({
        html: '<div style="width:0;height:0;border-left:8px solid transparent;border-right:8px solid transparent;border-bottom:16px solid red;transform:rotate(' + hdg + 'deg);transform-origin:50% 50%;"></div>',
        className: '',
        iconSize: [16, 16],
        iconAnchor: [8, 8]
      }),
      zIndexOffset: 1000
    }).addTo(map);
  } else {
    droneMarker.setLatLng([lat, lon]);
    droneMarker.setIcon(L.divIcon({
      html: '<div style="width:0;height:0;border-left:8px solid transparent;border-right:8px solid transparent;border-bottom:16px solid red;transform:rotate(' + hdg + 'deg);transform-origin:50% 50%;"></div>',
      className: '',
      iconSize: [16, 16],
      iconAnchor: [8, 8]
    }));
  }
  if (isFirstFix) {
    map.setView([lat, lon], 18);
    isFirstFix = false;
  }
}
function getPend(){var p=JSON.stringify(pend);pend=[];return p;}
</script></body></html>"""

# ── Stil Sabitleri ────────────────────────────────────────────────────────────
# Renk/kenarlık QSS temasından gelir (yki_design_spec §1) — burada yalnız
# zararsız tipografi sabiti kaldı.
_SL = "font-size:12px;"

# ── Harita katman renkleri — aviyonik semantik, temadan bağımsız ──────────────
# NEDEN sabit: bu renkler UI kromu değil, UYDU GÖRÜNTÜSÜ üzerine çizilen harita
# katmanlarıdır (Leaflet). Zeminleri tema değil ortofoto belirler; açık temada
# tema tokenına çevrilirlerse (ör. koyu metin rengi) koyu asfalt/gölge üzerinde
# okunamaz hale gelirler. Yeşil=başlangıç/rota, kırmızı=iniş, amber=waypoint
# eşlemesi ayrıca _MAP_HTML içindeki JS ile birebir aynı kalmalıdır.
_MAP_ROUTE_COLOR = "#4ade80"   # rota önizleme çizgisi
_MAP_AREA_COLOR  = "#bb86fc"   # tarama poligonu / yarıçap çemberi
_MAP_POLE1_COLOR = "#ffd700"   # Direk 1 çemberi
_MAP_POLE2_COLOR = "#ff8c00"   # Direk 2 çemberi
_MAP_FW_AREA_COLOR = "#a78bfa"  # Sabit Kanat pisti köşe çemberi (mor — ana tarama alanından ayırt edilsin)
_MAP_RED_AREA_COLOR  = "#ef4444"  # Kırmızı hedefin TAHMİNİ bölgesi
_MAP_BLUE_AREA_COLOR = "#3b82f6"  # Mavi hedefin TAHMİNİ bölgesi

# ── Fare Tekerleği Koruması ───────────────────────────────────────────────────
# NEDEN: Sağ parametre paneli tekerlekle kaydırılırken imlecin ALTINDAKİ
# QSpinBox/QDoubleSpinBox/QComboBox değeri sessizce değişiyordu (canlı testte
# "Bırakma Yarıçapı" 4.0 → 1.0 düştü ve fark edilmedi). Uçuş parametresinin
# operatör farkında olmadan bozulması kritik güvenlik riski.
#
# ÇÖZÜM: Widget odakta DEĞİLKEN tekerlek olayını yut. event.ignore() + return
# True kombinasyonu Qt'nin wheel yayılım döngüsünde olayı ÜST widget'a
# (QScrollArea) taşır → panel normal şekilde kaydırılmaya devam eder, yalnızca
# değer değişmez. Kullanıcı alana TIKLADIKTAN (odak aldıktan) sonra tekerlek
# yine eskisi gibi çalışır. StrongFocus: tekerlek artık odak vermez.
class _WheelGuard(QObject):
  """Odakta olmayan spin/combo widget'larında fare tekerleğini etkisizleştirir."""

  def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
    if event is not None and event.type() == QEvent.Type.Wheel:
      if isinstance(obj, QWidget) and not obj.hasFocus():
        event.ignore()   # kabul etme → olay üst widget'a (scroll area) gider
        return True    # widget'a teslim etme → değer değişmez
    return super().eventFilter(obj, event)


_WHEEL_GUARD: _WheelGuard | None = None


def guard_wheel(*widgets):
  """Verilen spin/combo widget'larına tekerlek korumasını uygular.

  Tekrarı önlemek için tek noktadan uygulanır; ilk widget'ı geri döndürür ki
  `self._x = guard_wheel(QSpinBox())` şeklinde satır içi kullanılabilsin.
  """
  global _WHEEL_GUARD
  if _WHEEL_GUARD is None:
    _WHEEL_GUARD = _WheelGuard()
  for w in widgets:
    if w is None:
      continue
    w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    w.installEventFilter(_WHEEL_GUARD)
  return widgets[0] if widgets else None


# ── Koordinat Girişi Yardımcısı ───────────────────────────────────────────────
# NEDEN: Enlem/Boylam alanları çok dardı; 10 haneli koordinat girildiğinde
# yalnız son 3 hane görünüyordu (değer doğruydu ama operatör GÖZLE kontrol
# edemiyordu). Makul minimum genişlik + tooltip'te tam değer.
_COORD_MIN_W = 120  # px — "39.9250140" tamamen sığar


def wire_coord_edit(edit, hint: str) -> None:
  """Koordinat QLineEdit'ine minimum genişlik ve 'tam değer' tooltip'i verir."""
  edit.setMinimumWidth(_COORD_MIN_W)
  edit.setToolTip(hint)

  def _upd(text: str, e=edit, h=hint):
    e.setToolTip(f"{h}\nGirilen tam değer: {text}" if text else h)

  edit.textChanged.connect(_upd)

# ── Harita Widget ─────────────────────────────────────────────────────────────
class MissionMapWidget(QWidget):
  wp_clicked = pyqtSignal(float, float)   # yeni WP tıklaması: lat, lon
  wp_dragged = pyqtSignal(int, float, float) # WP taşıma: idx, lat, lon

  def __init__(self, parent=None):
    super().__init__(parent)
    self._ready = False
    self._buf: list[str] = []
    lay = QVBoxLayout(self)
    lay.setContentsMargins(0, 0, 0, 0)

    if _WE:
      # Varsayılan profilde CDN erişimine izin ver — view öncesinde yapılmalı
      _profile = QWebEngineProfile.defaultProfile()
      if _profile is not None:
        _profile_s = _profile.settings()
        if _profile_s is not None:
          _profile_s.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
          )
          _profile_s.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
          )
          _profile_s.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, True
          )

      self._view = QWebEngineView()
      # loadFinished ÖNCE bağla, SONRA yükle
      self._view.loadFinished.connect(self._on_load)
      lay.addWidget(self._view)
      self._load_html()

      self._timer = QTimer(self)
      self._timer.setInterval(450)
      self._timer.timeout.connect(self._poll)
    else:
      lbl = QLabel("Harita yüklenemedi\n(PyQt6-WebEngine kurulu değil)")
      lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
      lay.addWidget(lbl)

  def _load_html(self):
    from PyQt6.QtCore import QUrl
    from ui.map_assets import localize_leaflet
    # setHtml + qrc:/// → Qt bu origin'den dış CDN'e erişime izin verir.
    # localize_leaflet: vendored Leaflet varsa inline gömer (çevrimdışı çalışır).
    self._view.setHtml(localize_leaflet(_MAP_HTML), QUrl("qrc:///"))

  def _on_load(self, ok: bool):
    self._ready = ok
    if ok:
      _page = self._view.page()
      if _page is not None:
        for js in self._buf:
          _page.runJavaScript(js)
      self._buf.clear()
      # NEDEN isVisible() kontrolü: sekme henüz görünür değilken (arka planda
      # yüklenirken) polling'i BAŞLATMA — showEvent zaten görünür olunca
      # başlatacak. Bkz. showEvent/hideEvent altında (performans: gizli
      # sekmede 450 ms'de bir runJavaScript çağırmamak için).
      if self.isVisible():
        self._timer.start()

  def showEvent(self, event):
    # Widget (veya üst sekmesi) tekrar görünür olunca polling'i devam ettir.
    # NEDEN: main_window._dispatch_telemetry'deki "yalnızca aktif sekme"
    # ilkesiyle AYNI motivasyon — gizli sekmede 450 ms'de bir runJavaScript
    # çağırmak gereksiz CPU/JS köprüsü yükü yaratıyordu.
    super().showEvent(event)
    if _WE and self._ready and hasattr(self, "_timer") and not self._timer.isActive():
      self._timer.start()

  def hideEvent(self, event):
    # Widget (veya üst sekmesi) gizlenince polling'i durdur — bkz. showEvent.
    super().hideEvent(event)
    if _WE and hasattr(self, "_timer") and self._timer.isActive():
      self._timer.stop()

  def _js(self, code: str):
    if not _WE:
      return
    if self._ready:
      _page = self._view.page()
      if _page is not None:
        _page.runJavaScript(code)
    else:
      self._buf.append(code)

  # BUG FIX: center_at burada iki kez tanımlanmıştı (ölü kod kaldırıldı).
  # Geçerli tanım aşağıda self._js() kullanan sürümdür (satır ~298).

  def set_drone_pos(self, lat: float, lon: float, hdg: float):
    if _WE and self._ready:
      _page = self._view.page()
      if _page is not None:
        _page.runJavaScript(f"set_drone_pos({lat},{lon},{hdg})")
    else:
      self._buf.append(f"set_drone_pos({lat},{lon},{hdg})")

  def _poll(self):
    if not (_WE and self._ready):
      return

    def _cb(r):
      if not r:
        return
      try:
        items = json.loads(r)
      except Exception:
        return
      for it in items:
        if "update_idx" in it:
          self.wp_dragged.emit(
            int(it["update_idx"]), float(it["lat"]), float(it["lon"])
          )
        else:
          self.wp_clicked.emit(float(it["lat"]), float(it["lon"]))

    _page = self._view.page()
    if _page is not None:
      _page.runJavaScript("getPend()", _cb)

  # ── Public API ─────────────────────────────────────────────────────────
  def set_select_mode(self, on: bool):
    self._js(f"setSel({'true' if on else 'false'})")

  def add_wp(self, lat: float, lon: float, alt: float, idx: int, wp_type: str):
    self._js(f"addWP({lat},{lon},{alt},{idx},'{wp_type}')")

  def clear_wps(self):
    self._js("clearWPs()")

  def draw_polygon(self, pts: list[tuple[float, float]], color: str = _MAP_AREA_COLOR):
    js_pts = json.dumps([{"lat": p[0], "lon": p[1]} for p in pts])
    self._js(f"drawPoly({js_pts},'{color}')")

  def draw_circle(self, lat: float, lon: float, r: float, color: str = _MAP_AREA_COLOR):
    self._js(f"drawCirc({lat},{lon},{r},'{color}')")

  def draw_route(self, pts: list[tuple[float, float]],
                 color: str = _MAP_ROUTE_COLOR, fit: bool = False):
    """
    Tahmini uçuş rotasını haritaya çizer (bağımsız önizleme katmanı).

    pts: [(lat, lon), ...] — uçuşta gidilecek noktaların AYNISI olmalı.
    fit: True ise harita rotayı kadraja sığdırır. Yalnızca operatör açıkça
         "önizle" dediğinde True verin — her düzenlemede haritayı kaydırmak,
         kullanıcı elle yakınlaştırdığında sinir bozucu olur.
    """
    js_pts = json.dumps([{"lat": p[0], "lon": p[1]} for p in pts])
    self._js(f"drawRoute({js_pts},'{color}',{'true' if fit else 'false'})")

  def clear_route(self):
    self._js("clearRoute()")

  def set_home(self, lat: float, lon: float):
    self._js(f"setHome({lat},{lon})")

  def center_at(self, lat: float, lon: float, zoom: int = 16):
    self._js(f"ctr({lat},{lon},{zoom})")


# Route matematiği ve coğrafi yardımcılar core/route_math.py'ye taşındı
# (god-object küçültme + IHA ile senkron sözleşmesini tek yere toplama).
from core.route_math import (  # noqa: F401
    _haversine, _bearing, _offset_point, _gen_lemniscate_pts, _gen_dubins_pts, compute_optimal_turn_radius, compute_ideal_cruise, build_infinity_route, infinity_to_qgc_wpl, _estimate_battery,
)

# ── Temel Görev Sekmesi ───────────────────────────────────────────────────────
class _BaseMissionTab(QWidget):
  mission_ready_to_send = pyqtSignal(dict, list)
  send_command     = pyqtSignal(int, list)

  # NEDEN: Sol panel her zaman "Haritaya tıklayarak nokta ekleyin." yazıyordu,
  # ama harita tıklaması yalnızca "Harita Seçim Modu" AÇIKKEN çalışıyor
  # (_toggle_select → map.set_select_mode). Operatör yanlış yönlendirilip
  # panelin bozuk olduğunu sanıyordu. Metin artık moda göre değişiyor.
  _HINT_SEL_OFF = "⚠ Önce 'Harita Seçim Modu'nu açın, sonra haritaya tıklayın."
  _HINT_SEL_ON  = "✓ Haritaya tıklayarak nokta ekleyin."

  def __init__(self, parent=None):
    super().__init__(parent)
    self._wps: list[dict] = []
    self._first_home_set = False
    # Aracın ilk telemetri konumu = otomatik kalkış (HOME) noktası
    self._home_lat: float | None = None
    self._home_lon: float | None = None

    self._map = MissionMapWidget()
    self._map.wp_clicked.connect(self._add_wp)
    self._map.wp_dragged.connect(self._on_drag)

    # --- SOL HUD PANELI ---
    self._hud_widget = QWidget()
    self._hud_widget.setFixedWidth(300)
    self._hud_lay = QVBoxLayout(self._hud_widget)
    self._hud_lay.setContentsMargins(6, 6, 6, 6)
    self._hud_lay.setSpacing(6)

    lbl_hud_title = QLabel("<b>GÖREV ÖZETİ</b>")
    lbl_hud_title.setObjectName("sectionTitle")
    self._hud_lay.addWidget(lbl_hud_title)

    # Panel açılışta seçim modu KAPALI → doğru yönerge "önce modu aç" olmalı
    self._hud_summary = QLabel(self._HINT_SEL_OFF)
    self._hud_summary.setWordWrap(True)
    self._hud_summary.setObjectName("hintLabel")
    self._hud_lay.addWidget(self._hud_summary)

    lbl_wp_dists = QLabel("<b>WP Mesafeleri:</b>")
    self._hud_lay.addWidget(lbl_wp_dists)

    self._hud_list = QListWidget()
    self._hud_lay.addWidget(self._hud_list)
    
    left_pane = QWidget()
    left_lay = QHBoxLayout(left_pane)
    left_lay.setContentsMargins(0, 0, 0, 0)
    left_lay.setSpacing(8)
    left_lay.addWidget(self._hud_widget)
    left_lay.addWidget(self._map, 1)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.addWidget(left_pane)

    right = QScrollArea()
    right.setWidgetResizable(True)
    # Yatay scrollbar KAPALI: form içeriği sağdan kırpılmak yerine
    # WrapLongRows ile alta sarar (yki_design_spec §4 — Görev sayfaları).
    right.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    # NEDEN (410/520 → 470/620): 1366 px pencerede en sağdaki girdiler (kamera
    # combo'su, "Varsayılan" butonlu hız satırları) panelin dışında kalıyordu.
    # Harita alanı fazlasıyla genişti; form paneline yer aktarıldı.
    right.setMinimumWidth(470)  # uzun etiket + 190px combo + dikey scrollbar sığsın
    right.setMaximumWidth(620)  # form paneli sonsuz genişlemesin
    right_w = QWidget()
    self._right_lay = QVBoxLayout(right_w)
    self._right_lay.setContentsMargins(8, 8, 8, 8)
    self._right_lay.setSpacing(8)
    right.setWidget(right_w)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 1)   # harita baskın
    splitter.setStretchFactor(1, 0)
    # Harita küçülür, form genişler — kullanıcı geri bildirimi: harita alanı
    # zaten yeterliydi, sağdaki kutular görünmüyordu.
    splitter.setSizes([760, 560])

    main_lay = QVBoxLayout(self)
    main_lay.setContentsMargins(0, 0, 0, 0)
    main_lay.addWidget(splitter)

    self._build_wp_group()
    self._build_specific_controls()
    self._build_speed_group()
    self._build_battery_group()
    self._build_send_button()
    self._right_lay.addStretch(1)
    # NEDEN: Ortak kontrollerin bir kısmı bazı görev tiplerinde İHA tarafından
    # okunmuyor. Tüm gruplar kurulduktan SONRA sekmeye özel kısıtları uygula.
    self._apply_tab_constraints()

    # Profil çubuğu EN SON kurulur (tüm parametre widget'ları var olsun) ama
    # panelin EN ÜSTÜNE yerleşir — operatör önce profilini seçer.
    self._build_profile_group()
    self._load_default_profile()

    # Tema değişince kurulumda yazılan çalışma-anı renkleri (WP tablosu satır
    # renkleri) bayat kalır — yeniden uygula.
    theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

  def _apply_theme_colors(self):
    """Tema değişiminde inline/çalışma-anı renklerini yeniden yazar.

    WP tablosundaki tür renkleri ve metin rengi QTableWidgetItem üzerinde
    saklandığı için QSS yenilenmesiyle tazelenmez; tabloyu yeniden kur."""
    if hasattr(self, "_table"):
      self._refresh_table()

  def _apply_tab_constraints(self):
    """Sekmeye özel kısıt/uyarılar (alt sınıflar override eder)."""
    pass

  # ── Görev Profilleri (kayıtlı parametre ön ayarları) ──────────────────────
  # Operatör sahada aynı ayarları tekrar tekrar girmesin: mevcut tüm parametre
  # değerleri isimli bir profil olarak kaydedilir, seçilince tek hamlede geri
  # yüklenir. "Açılışta uygula" işaretli profil, sekme her açıldığında
  # otomatik uygulanır. Profiller sekme başına ayrı tutulur (Görev 1'in
  # parametreleri Görev 2'ye karışmaz) — bkz. core/mission_profiles.py.

  # Profil dışı bırakılan widget'lar: profil çubuğunun kendi kontrolleri.
  _PROFILE_SKIP_PREFIX = "_profile"

  def _profile_key(self) -> str:
    """Profil deposundaki bölüm anahtarı — sekme sınıfının adı."""
    return type(self).__name__

  def _build_profile_group(self):
    bar = QFrame()
    bar.setObjectName("toolbarFrame")
    outer = QVBoxLayout(bar)
    outer.setContentsMargins(8, 6, 8, 6)
    outer.setSpacing(6)

    row1 = QHBoxLayout()
    row1.setSpacing(6)
    lbl = QLabel("Görev Profili:")
    lbl.setObjectName("sectionTitle")
    row1.addWidget(lbl)

    self._profile_combo = QComboBox()
    self._profile_combo.setToolTip(
      "Kayıtlı parametre profilleri. Seçince bu sekmedeki tüm ayarlar\n"
      "(irtifa, hız, kamera, tarama, servo ...) profildeki değerlerle dolar."
    )
    self._profile_combo.currentIndexChanged.connect(self._on_profile_selected)
    row1.addWidget(self._profile_combo, 1)
    outer.addLayout(row1)

    row2 = QHBoxLayout()
    row2.setSpacing(6)

    self._profile_btn_save = QPushButton("💾 Kaydet")
    self._profile_btn_save.setObjectName("primaryButton")
    self._profile_btn_save.setMinimumHeight(28)
    self._profile_btn_save.setToolTip(
      "Seçili profilin üzerine mevcut ayarları yazar.\n"
      "Profil seçili değilse yeni profil adı sorar."
    )
    self._profile_btn_save.clicked.connect(self._on_profile_save)
    row2.addWidget(self._profile_btn_save)

    self._profile_btn_save_as = QPushButton("Farklı Kaydet")
    self._profile_btn_save_as.setMinimumHeight(28)
    self._profile_btn_save_as.setToolTip("Mevcut ayarları YENİ bir profil adıyla kaydeder.")
    self._profile_btn_save_as.clicked.connect(self._on_profile_save_as)
    row2.addWidget(self._profile_btn_save_as)

    self._profile_btn_delete = QPushButton("Sil")
    self._profile_btn_delete.setObjectName("dangerButton")
    self._profile_btn_delete.setMinimumHeight(28)
    self._profile_btn_delete.setToolTip("Seçili profili siler.")
    self._profile_btn_delete.clicked.connect(self._on_profile_delete)
    row2.addWidget(self._profile_btn_delete)

    row2.addStretch(1)

    self._profile_default_cb = QCheckBox("Açılışta uygula")
    self._profile_default_cb.setToolTip(
      "İşaretli profil, bu sekme her açıldığında otomatik yüklenir."
    )
    self._profile_default_cb.toggled.connect(self._on_profile_default_toggled)
    row2.addWidget(self._profile_default_cb)
    outer.addLayout(row2)

    self._profile_status = QLabel("")
    self._profile_status.setObjectName("mutedLabel")
    self._profile_status.setWordWrap(True)
    outer.addWidget(self._profile_status)

    self._right_lay.insertWidget(0, bar)
    self._refresh_profile_combo()

  # ── Parametre topla / uygula ──────────────────────────────────────────────
  def _param_widgets(self) -> dict:
    """Profile girecek form widget'ları: {attribute_adı: widget}.

    Attribute adı anahtar olarak kullanılır — bu yüzden profil dosyası
    widget isimlendirmesine bağlıdır; bir attribute yeniden adlandırılırsa
    eski profillerdeki o değer sessizce atlanır (uygulama çökmez)."""
    out: dict = {}
    for name, obj in vars(self).items():
      if name.startswith(self._PROFILE_SKIP_PREFIX):
        continue
      if isinstance(obj, (QDoubleSpinBox, QSpinBox, QCheckBox, QComboBox, QLineEdit)):
        out[name] = obj
    return out

  def _snapshot_params(self) -> dict:
    values: dict = {}
    for name, w in self._param_widgets().items():
      if isinstance(w, (QDoubleSpinBox, QSpinBox)):
        values[name] = w.value()
      elif isinstance(w, QCheckBox):
        values[name] = w.isChecked()
      elif isinstance(w, QComboBox):
        values[name] = w.currentText()
      elif isinstance(w, QLineEdit):
        values[name] = w.text()
    return values

  def _apply_params(self, values: dict) -> int:
    """Profildeki değerleri widget'lara yazar; uygulanan sayısını döndürür.

    Sinyaller BLOKLANMAZ: bir değerin değişmesi bağlı mantığı (ör. HFOV
    kilidini açan onay kutusu, rota önizlemesi) tetiklemeli."""
    applied = 0
    widgets = self._param_widgets()
    for name, value in (values or {}).items():
      w = widgets.get(name)
      if w is None:
        continue   # profil eski sürümden — bilinmeyen alanı sessizce atla
      try:
        if isinstance(w, (QDoubleSpinBox, QSpinBox)):
          w.setValue(type(w.value())(value))
        elif isinstance(w, QCheckBox):
          w.setChecked(bool(value))
        elif isinstance(w, QComboBox):
          idx = w.findText(str(value))
          if idx >= 0:
            w.setCurrentIndex(idx)
          elif w.isEditable():
            w.setEditText(str(value))
          else:
            continue   # listede olmayan seçenek — atla
        elif isinstance(w, QLineEdit):
          w.setText(str(value))
        applied += 1
      except (TypeError, ValueError) as exc:
        logger.warning("Profil değeri uygulanamadı (%s=%r): %s", name, value, exc)
    return applied

  # ── Profil çubuğu davranışı ───────────────────────────────────────────────
  def _refresh_profile_combo(self, select: str | None = None):
    key = self._profile_key()
    names = mission_profiles.list_profiles(key)
    default_name = mission_profiles.get_default(key)

    self._profile_combo.blockSignals(True)
    self._profile_combo.clear()
    self._profile_combo.addItem("— Profil seçilmedi —", None)
    for name in names:
      label = f"{name}  ★" if name == default_name else name
      self._profile_combo.addItem(label, name)
    if select:
      idx = self._profile_combo.findData(select)
      self._profile_combo.setCurrentIndex(idx if idx >= 0 else 0)
    self._profile_combo.blockSignals(False)

    self._sync_profile_buttons()

  def _current_profile_name(self) -> str | None:
    return self._profile_combo.currentData() if hasattr(self, "_profile_combo") else None

  def _sync_profile_buttons(self):
    name = self._current_profile_name()
    self._profile_btn_delete.setEnabled(bool(name))
    self._profile_default_cb.blockSignals(True)
    self._profile_default_cb.setChecked(
      bool(name) and mission_profiles.get_default(self._profile_key()) == name
    )
    self._profile_default_cb.setEnabled(bool(name))
    self._profile_default_cb.blockSignals(False)

  def _set_profile_status(self, text: str, token: str = "text_muted"):
    self._profile_status.setText(text)
    self._profile_status.setStyleSheet(f"color:{theme.c(token)};")

  def _on_profile_selected(self, _index: int):
    name = self._current_profile_name()
    self._sync_profile_buttons()
    if not name:
      self._set_profile_status("")
      return
    values = mission_profiles.get_profile(self._profile_key(), name)
    if values is None:
      self._set_profile_status(f"'{name}' profili bulunamadı.", "danger")
      return
    count = self._apply_params(values)
    self._set_profile_status(f"✓ '{name}' uygulandı ({count} parametre).", "success")
    logger.info("Görev profili uygulandı: %s / %s (%d parametre)",
                self._profile_key(), name, count)

  def _on_profile_save(self):
    name = self._current_profile_name()
    if not name:
      self._on_profile_save_as()
      return
    reply = QMessageBox.question(
      self, "Profili Güncelle",
      f"'{name}' profilinin üzerine mevcut ayarlar yazılsın mı?",
      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
      QMessageBox.StandardButton.Yes,
    )
    if reply != QMessageBox.StandardButton.Yes:
      return
    self._save_profile_named(name)

  def _on_profile_save_as(self):
    name, ok = QInputDialog.getText(
      self, "Profili Kaydet",
      "Profil adı (ör. 'Saha — Rüzgarlı', 'Yarışma finali'):",
    )
    if not ok:
      return
    name = (name or "").strip()
    if not name:
      self._set_profile_status("Profil adı boş olamaz.", "warning")
      return
    if mission_profiles.get_profile(self._profile_key(), name) is not None:
      reply = QMessageBox.question(
        self, "Profil Var",
        f"'{name}' zaten var. Üzerine yazılsın mı?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
      )
      if reply != QMessageBox.StandardButton.Yes:
        return
    self._save_profile_named(name)

  def _save_profile_named(self, name: str):
    values = self._snapshot_params()
    if mission_profiles.save_profile(self._profile_key(), name, values):
      self._refresh_profile_combo(select=name)
      self._set_profile_status(
        f"✓ '{name}' kaydedildi ({len(values)} parametre).", "success")
    else:
      self._set_profile_status(
        "Profil kaydedilemedi (dosya yazma hatası — loga bakın).", "danger")

  def _on_profile_delete(self):
    name = self._current_profile_name()
    if not name:
      return
    reply = QMessageBox.question(
      self, "Profili Sil", f"'{name}' profili silinsin mi?",
      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
      QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
      return
    if mission_profiles.delete_profile(self._profile_key(), name):
      self._refresh_profile_combo()
      self._set_profile_status(f"'{name}' silindi.", "text_muted")
    else:
      self._set_profile_status("Profil silinemedi.", "danger")

  def _on_profile_default_toggled(self, checked: bool):
    name = self._current_profile_name()
    if not name:
      return
    mission_profiles.set_default(self._profile_key(), name if checked else None)
    # ★ işareti listede güncellensin
    self._refresh_profile_combo(select=name)
    self._set_profile_status(
      f"'{name}' artık açılışta otomatik uygulanacak." if checked
      else f"'{name}' açılışta otomatik uygulanmayacak.",
      "success" if checked else "text_muted",
    )

  def _load_default_profile(self):
    """Sekme kurulurken varsayılan profili uygular (varsa)."""
    name = mission_profiles.get_default(self._profile_key())
    if not name:
      return
    idx = self._profile_combo.findData(name)
    if idx < 0:
      return
    # setCurrentIndex → _on_profile_selected profili uygular ve durumu yazar.
    self._profile_combo.setCurrentIndex(idx)

  # ── WP Yönetim Grubu ─────────────────────────────────────────────────────
  def _build_wp_group(self):
    grp = QGroupBox("Tarama Alanı / Waypoint Yönetimi")
    self._wp_grp = grp     # alt sınıflar/testler grubu bulabilsin
    lay = QVBoxLayout(grp)

    btn_row = QHBoxLayout()
    self._sel_btn = QPushButton("🖱 Harita Seçim Modu (KAPALI)")
    self._sel_btn.setCheckable(True)
    # NEDEN: Bu buton açılmadan harita tıklaması çalışmıyor; en kritik kontrol
    # olmasına rağmen diğer butonlardan ayırt edilemiyordu. Belirgin hale getir.
    self._sel_btn.setMinimumHeight(34)
    self._sel_btn.setToolTip(
      "Haritaya tıklayarak nokta ekleyebilmek için bu modu AÇIN.\n"
      "Kapalıyken harita tıklamaları yok sayılır."
    )
    self._sel_btn.toggled.connect(self._toggle_select)

    btn_del = QPushButton("↩ Son WP Sil")
    btn_del.clicked.connect(self._remove_last)

    btn_clr = QPushButton("Tümünü Temizle")
    btn_clr.clicked.connect(self._clear_all)

    # Seçim modu butonu KENDİ satırında: uzun metni ("🎯 Seçim Modu AÇIK —
    # Haritaya Tıkla") dar panelde diğer iki butonla aynı satıra sığmıyor ve
    # üçü birden kırpılıyordu. Sil/Temizle ikinci satırda sağa yaslı.
    btn_row.addWidget(self._sel_btn)
    lay.addLayout(btn_row)

    btn_row_b = QHBoxLayout()
    btn_row_b.addStretch(1)
    btn_row_b.addWidget(btn_del)
    btn_row_b.addWidget(btn_clr)
    lay.addLayout(btn_row_b)

    opt_row = QHBoxLayout()
    self._rth_cb = QCheckBox("🏠 Kalkış Noktasına Dön (RTH)")

    self._uni_cb = QCheckBox("📏 Eşit İrtifa:")

    self._alt_spin = QDoubleSpinBox()
    self._alt_spin.setRange(1, 500)
    self._alt_spin.setValue(30)
    self._alt_spin.setSuffix("m")
    self._alt_spin.setFixedWidth(85)
    guard_wheel(self._alt_spin)  # kaydırırken irtifa sessizce değişmesin

    # NEDEN İKİ SATIR: RTH kutusunun metni Görev 2'de uzuyor ("Kalkış Noktasına
    # Dön (Görev 2'de kullanılmıyor)"); "Eşit İrtifa" + spin ile aynı satırda
    # 525 px istiyordu ve TÜM sağ paneli minimum genişliğinin üstüne itip
    # kırpılmaya yol açıyordu (yatay scrollbar kapalı). Ölçüldü: 565 → 368 px.
    opt_row.addWidget(self._rth_cb)
    opt_row.addStretch(1)
    lay.addLayout(opt_row)

    alt_row = QHBoxLayout()
    alt_row.addStretch(1)
    alt_row.addWidget(self._uni_cb)
    alt_row.addWidget(self._alt_spin)
    lay.addLayout(alt_row)

    # WP Tablosu
    self._table = QTableWidget()
    self._table.setColumnCount(5)
    self._table.setHorizontalHeaderLabels(["#", "Tür", "Enlem", "Boylam", "İrtifa(m)"])
    _hh = self._table.horizontalHeader()  # guard: None dönebilir (Pylance)
    if _hh is not None:
        _hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        _hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    self._table.setMinimumHeight(140)
    self._table.setMaximumHeight(200)
    self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
    self._table.itemChanged.connect(self._on_item_changed)

    lay.addWidget(self._table)

    # ── Manuel Koordinat Girişi ───────────────────────────────────────
    man_frame = QFrame()
    man_lay = QVBoxLayout(man_frame)
    man_lay.setContentsMargins(6, 6, 6, 6)
    man_lay.setSpacing(4)

    hdr_row = QHBoxLayout()
    hdr_lbl = QLabel("Manuel Koordinat Ekle")
    hdr_row.addWidget(hdr_lbl)
    hdr_row.addStretch()
    man_lay.addLayout(hdr_row)

    # NEDEN: Enlem/Boylam + İrtifa + Tür tek satıra sıkışıyordu; koordinat
    # alanlarında 10 haneli değerin yalnız son 3 hanesi görünüyordu. Artık
    # koordinatlar kendi satırında ve en az 120 px genişlikte (wire_coord_edit).
    # ÇİFT SÜTUN YOK: enlem ve boylam ALT ALTA. Dar sağ panelde (470 px) iki
    # koordinat girdisi yan yana sığmıyor ve sağdaki kırpılıyordu.
    coord_row = QGridLayout()
    coord_row.setHorizontalSpacing(4)
    coord_row.setVerticalSpacing(4)

    lbl_lat = QLabel("Enlem:")
    lbl_lat.setFixedWidth(48)
    self._man_lat = QLineEdit()
    self._man_lat.setPlaceholderText("ör. 39.925014")
    self._man_lat.setMaximumWidth(220)
    wire_coord_edit(self._man_lat, "Ondalıklı derece (ör: 39.925014)")

    lbl_lon = QLabel("Boylam:")
    lbl_lon.setFixedWidth(48)
    self._man_lon = QLineEdit()
    self._man_lon.setPlaceholderText("ör. 32.866200")
    self._man_lon.setMaximumWidth(220)
    wire_coord_edit(self._man_lon, "Ondalıklı derece (ör: 32.866200)")

    coord_row.addWidget(lbl_lat, 0, 0)
    coord_row.addWidget(self._man_lat, 0, 1)
    coord_row.addWidget(lbl_lon, 1, 0)
    coord_row.addWidget(self._man_lon, 1, 1)
    coord_row.setColumnStretch(1, 1)
    man_lay.addLayout(coord_row)

    # İrtifa + tür ikinci satırda — koordinat alanlarını daraltmasın
    meta_row = QHBoxLayout()
    meta_row.setSpacing(4)

    lbl_alt_m = QLabel("İrt:")
    lbl_alt_m.setFixedWidth(24)
    self._man_alt = QDoubleSpinBox()
    self._man_alt.setRange(1, 500)
    self._man_alt.setValue(30)
    self._man_alt.setSuffix("m")
    self._man_alt.setFixedWidth(76)

    lbl_type_m = QLabel("Tür:")
    lbl_type_m.setFixedWidth(28)
    self._man_type = QComboBox()
    self._man_type.addItems(["WAYPOINT", "TAKEOFF", "LAND"])
    self._man_type.setMinimumWidth(104)
    self._man_type.setMaximumWidth(160)

    # tekerlek koruması: kaydırma sırasında irtifa/tür sessizce değişmesin
    guard_wheel(self._man_alt, self._man_type)

    meta_row.addWidget(lbl_alt_m)
    meta_row.addWidget(self._man_alt)
    meta_row.addWidget(lbl_type_m)
    meta_row.addWidget(self._man_type)
    meta_row.addStretch(1)
    man_lay.addLayout(meta_row)

    btn_row2 = QHBoxLayout()
    btn_row2.setSpacing(4)
    btn_add_man = QPushButton("➕ Ekle")
    btn_add_man.clicked.connect(self._on_add_manual)
    btn_add_man.setToolTip("Girilen koordinatı WP listesine ekle")

    btn_center_man = QPushButton("🗺 Haritada Göster")
    btn_center_man.clicked.connect(self._on_center_manual)
    btn_center_man.setToolTip("Girilen koordinata haritayı ortala")

    self._man_status = QLabel("")

    btn_row2.addWidget(btn_add_man)
    btn_row2.addWidget(btn_center_man)
    btn_row2.addStretch()
    btn_row2.addWidget(self._man_status)
    man_lay.addLayout(btn_row2)

    lay.addWidget(man_frame)
    # Açılır bölüm: görev tipini belirleyen panel dışındaki her şey KAPALI
    # başlar; böylece sağ panel açılışta tek ekrana sığar.
    self._wp_sec = wrap_group(grp, expanded=False)
    self._right_lay.addWidget(self._wp_sec)

  def _build_specific_controls(self):
    """Subclass'lar buraya özel kontroller ekler."""
    pass

  # ── Hız Kontrolü ─────────────────────────────────────────────────────────
  def _build_speed_group(self):
    grp = QGroupBox("Hız Kontrolü")
    self._speed_grp = grp     # alt sınıflar devre dışı bırakabilsin
    form = QFormLayout(grp)
    form.setSpacing(6)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

    def _spn(val: float, mn: float, mx: float) -> QDoubleSpinBox:
      s = QDoubleSpinBox()
      s.setRange(mn, mx)
      s.setValue(val)
      s.setSuffix("m/s")
      s.setMaximumWidth(140)  # sayısal girdi — uçsuz uzamasın (spec §3)
      guard_wheel(s)   # kaydırırken hız değeri sessizce değişmesin
      return s

    self._sp_tkf = _spn(3.0, 0.5, 10.0)
    self._sp_crz = _spn(8.0, 1.0, 20.0)
    self._sp_lnd = _spn(1.5, 0.3, 5.0)

    def _row(spn: QDoubleSpinBox, default: float) -> QHBoxLayout:
      row = QHBoxLayout()
      row.addWidget(spn)
      btn = QPushButton("Varsayılan")
      btn.setFixedWidth(80)
      btn.clicked.connect(lambda: spn.setValue(default))
      row.addWidget(btn)
      return row

    _lbl_tkf = QLabel("Kalkış Hızı:"); _lbl_tkf.setStyleSheet(_SL)
    _lbl_crz = QLabel("Tur Hızı:");  _lbl_crz.setStyleSheet(_SL)
    _lbl_lnd = QLabel("İniş Hızı:");  _lbl_lnd.setStyleSheet(_SL)
    form.addRow(_lbl_tkf, _row(self._sp_tkf, 3.0))
    form.addRow(_lbl_crz, _row(self._sp_crz, 8.0))
    form.addRow(_lbl_lnd, _row(self._sp_lnd, 1.5))

    # NEDEN: Bazı görev tiplerinde (ör. TEKNOFEST Görev 2) İHA bu alanları
    # OKUMUYOR. Kullanıcı ayarlayıp etkisi olduğunu sanmasın diye sekmeye özel
    # uyarı satırı; varsayılan olarak gizli (bkz. _apply_tab_constraints).
    self._speed_note = QLabel("")
    self._speed_note.setWordWrap(True)
    self._speed_note.setObjectName("warningText")
    self._speed_note.setVisible(False)
    form.addRow(self._speed_note)
    # Bölüm nesnesi SAKLANIR: başlık artık kutuda değil bölüm başlığında, ve
    # setVisible/setEnabled çağrıları sarmalayıcıya gitmelidir.
    self._speed_sec = wrap_group(grp, expanded=False)
    self._right_lay.addWidget(self._speed_sec)

  # ── Batarya Tahmini ───────────────────────────────────────────────────────
  def _build_battery_group(self):
    grp = QGroupBox("Batarya Tahmini")
    self._bat_grp = grp
    form = QFormLayout(grp)
    form.setSpacing(6)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

    self._bat_cap = QDoubleSpinBox()
    self._bat_cap.setRange(0, 50000)
    self._bat_cap.setValue(5000)
    self._bat_cap.setSuffix("mAh")
    self._bat_cap.setMaximumWidth(140)
    self._bat_cap.valueChanged.connect(self._update_battery)

    self._bat_cur = QDoubleSpinBox()
    self._bat_cur.setRange(1, 200)
    self._bat_cur.setValue(25)
    self._bat_cur.setSuffix("A ort.")
    self._bat_cur.setMaximumWidth(140)
    self._bat_cur.valueChanged.connect(self._update_battery)

    # tekerlek koruması: batarya değerleri kaydırma sırasında bozulmasın
    guard_wheel(self._bat_cap, self._bat_cur)

    _lbl_cap = QLabel("Batarya Kapasitesi:"); _lbl_cap.setStyleSheet(_SL)
    _lbl_cur = QLabel("Ort. Akım Tüketimi:"); _lbl_cur.setStyleSheet(_SL)
    form.addRow(_lbl_cap, self._bat_cap)
    form.addRow(_lbl_cur, self._bat_cur)

    self._bat_lbl = QLabel("— Rota hesaplanmadı —")
    self._bat_lbl.setWordWrap(True)
    form.addRow(self._bat_lbl)
    self._bat_sec = wrap_group(grp, expanded=False)
    self._right_lay.addWidget(self._bat_sec)

  # ── Gönder Butonu ─────────────────────────────────────────────────────────
  def _build_send_button(self):
    self._send_btn = QPushButton(" Görevi Gönder → Drone")
    self._send_btn.setObjectName("primaryButton")
    self._send_btn.setMinimumHeight(34)  # spec §3: birincil eylem ≤34px
    self._send_btn.clicked.connect(self._on_send)
    self._right_lay.addWidget(self._send_btn)

  # ── Manuel Koordinat Handler'ları ────────────────────────────────────────
  def _parse_coord(self, text: str) -> float | None:
    """DD veya DMS (39°55'30.0"N) formatlarını ondalık dereceye çevirir."""
    text = text.strip().replace(",", ".")
    # DD — doğrudan float
    try:
      return float(text)
    except ValueError:
      pass
    # DMS — "39°55'30.05\"N" veya "39 55 30.05 N"
    import re
    m = re.match(
      r"""(\d+)[°\s]+(\d+)['\s]+([0-9.]+)["\s]*([NSEW]?)""",
      text, re.IGNORECASE
    )
    if m:
      d, mi, s, hemi = m.groups()
      val = float(d) + float(mi) / 60 + float(s) / 3600
      if hemi.upper() in ("S", "W"):
        val = -val
      return val
    return None

  def _on_add_manual(self) -> None:
    lat = self._parse_coord(self._man_lat.text())
    lon = self._parse_coord(self._man_lon.text())
    if lat is None or lon is None:
      self._man_status.setText("⚠️ Geçersiz koordinat")
      return
    if not (-90 <= lat <= 90):
      self._man_status.setText("⚠️ Enlem -90…+90 aralığında olmalı")
      return
    if not (-180 <= lon <= 180):
      self._man_status.setText("⚠️ Boylam -180…+180 aralığında olmalı")
      return

    alt = self._man_alt.value()
    wp_type = self._man_type.currentText()
    idx = len(self._wps)
    self._wps.append({"lat": lat, "lon": lon, "alt": alt, "type": wp_type, "idx": idx})
    self._refresh_map()
    self._refresh_table()
    self._update_battery()
    self._map.center_at(lat, lon, 16)
    self._man_status.setText(f"✓ WP#{idx} eklendi ({lat:.5f}, {lon:.5f})")
    self._man_lat.clear()
    self._man_lon.clear()

  def _on_center_manual(self) -> None:
    lat = self._parse_coord(self._man_lat.text())
    lon = self._parse_coord(self._man_lon.text())
    if lat is None or lon is None:
      self._man_status.setText("⚠️ Geçersiz koordinat")
      return
    self._map.center_at(lat, lon, 17)
    self._man_status.setText(f"🗺 Harita ortalandı ({lat:.5f}, {lon:.5f})")

  # ── WP Mantığı ────────────────────────────────────────────────────────────
  @pyqtSlot(float, float)
  def _add_wp(self, lat: float, lon: float):
    alt = self._alt_spin.value() if self._uni_cb.isChecked() else 30.0
    idx = len(self._wps)

    # Kalkış noktası araçtan otomatik alındıysa, kullanıcının ilk WP'si de
    # TAKEOFF değil WAYPOINT olur (kullanıcı yalnızca hedef/tarama WP'leri seçer).
    home_auto = self._home_lat is not None

    if idx == 0 and not home_auto:
      wp_type = "TAKEOFF"
    elif self._rth_cb.isChecked() or home_auto:
      wp_type = "WAYPOINT"
    else:
      if self._wps and self._wps[-1]["type"] == "LAND":
        self._wps[-1]["type"] = "WAYPOINT"
      wp_type = "LAND"

    self._wps.append({"lat": lat, "lon": lon, "alt": alt, "type": wp_type, "idx": idx})
    self._refresh_map()
    self._refresh_table()
    self._update_battery()

  @pyqtSlot(int, float, float)
  def _on_drag(self, idx: int, lat: float, lon: float):
    if 0 <= idx < len(self._wps):
      self._wps[idx]["lat"] = lat
      self._wps[idx]["lon"] = lon
      self._refresh_table()
      self._update_battery()

  def _remove_last(self):
    if not self._wps:
      return
    self._wps.pop()
    if self._wps:
      if not self._rth_cb.isChecked() and len(self._wps) > 1:
        self._wps[-1]["type"] = "LAND"
      elif len(self._wps) == 1:
        self._wps[0]["type"] = "TAKEOFF"
    for i, wp in enumerate(self._wps):
      wp["idx"] = i
    self._refresh_map()
    self._refresh_table()
    self._update_battery()

  def _clear_all(self):
    self._wps.clear()
    self._refresh_map()
    self._refresh_table()
    self._update_battery()

  def _refresh_map(self):
    self._map.clear_wps()
    for wp in self._wps:
      self._map.add_wp(wp["lat"], wp["lon"], wp["alt"], wp["idx"], wp["type"])

  def _refresh_table(self):
    self._table.blockSignals(True)
    self._table.setRowCount(len(self._wps))
    for i, wp in enumerate(self._wps):
      # Tür rengi TEMA tokenından (kalkış=success, iniş=danger, ara=warning);
      # tema değişince _apply_theme_colors bu metodu yeniden çağırır.
      clr = (
        theme.c("success") if wp["type"] == "TAKEOFF"
        else theme.c("danger") if wp["type"] == "LAND"
        else theme.c("warning")
      )
      cells = [
        str(i),
        wp["type"],
        f"{wp['lat']:.6f}",
        f"{wp['lon']:.6f}",
        f"{wp['alt']:.1f}",
      ]
      for col, txt in enumerate(cells):
        it = QTableWidgetItem(txt)
        it.setForeground(QColor(clr if col == 1 else theme.c("text")))
        if col != 4:
          it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(i, col, it)
    self._table.blockSignals(False)
    self._refresh_hud()

  def _refresh_hud(self):
    self._hud_list.clear()
    if not self._wps:
        # NEDEN: Yönerge, harita seçim modunun GERÇEK durumunu yansıtmalı.
        _on = getattr(self, "_sel_btn", None) is not None and self._sel_btn.isChecked()
        self._hud_summary.setText(self._HINT_SEL_ON if _on else self._HINT_SEL_OFF)
        return
        
    total_dist = 0.0
    
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    for i in range(1, len(self._wps)):
        p1 = self._wps[i-1]
        p2 = self._wps[i]
        d = haversine(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
        total_dist += d
        self._hud_list.addItem(f"WP {i-1} ➔ WP {i} : {d:.1f} m")
        
    self._hud_summary.setText(f"Toplam Nokta: {len(self._wps)}\nToplam Mesafe: {total_dist:.1f} m")

  def _on_item_changed(self, item: QTableWidgetItem):
    if item.column() == 4:
      try:
        val = float(item.text())
        row = item.row()
        if 0 <= row < len(self._wps):
          self._wps[row]["alt"] = max(1.0, val)
          self._refresh_map()
          self._update_battery()
      except ValueError:
        pass

  def _toggle_select(self, on: bool):
    self._map.set_select_mode(on)
    self._sel_btn.setText(
      "🎯 Seçim Modu AÇIK — Haritaya Tıkla" if on else "🖱 Harita Seçim Modu (KAPALI)"
    )
    # NEDEN: Modun açık/kapalı olduğu bir bakışta anlaşılmıyordu. Açıkken tema
    # vurgu rengiyle (teal #2dd4bf → QPushButton#primaryButton) boyanır.
    # objectName değişince QSS yeniden uygulanmalı → unpolish/polish şart.
    self._sel_btn.setObjectName("primaryButton" if on else "")
    _st = self._sel_btn.style()
    if _st is not None:
      _st.unpolish(self._sel_btn)
      _st.polish(self._sel_btn)
    self._sel_btn.update()
    # Sol paneldeki yönerge metnini modun gerçek durumuna göre düzelt
    if not self._wps:
      self._hud_summary.setText(self._HINT_SEL_ON if on else self._HINT_SEL_OFF)

  def _update_battery(self):
    if not self._wps:
      self._bat_lbl.setText("— Rota hesaplanmadı —")
      return
    est = _estimate_battery(
      self._wps, self._sp_crz.value(), self._bat_cur.value(), self._bat_cap.value()
    )
    if not est:
      return
    t_min = int(est["time_s"] / 60)
    t_sec = int(est["time_s"] % 60)
    ok = est.get("sufficient")
    suf = ""
    if ok is True:
      suf = " ✅ Batarya Yeterli"
    elif ok is False:
      suf = f" ⚠️ Yetersiz! Min {int(est['required_mah'])} mAh gerekli"
    self._bat_lbl.setText(
      f"Mesafe: {est['dist_m']:.0f} m | Süre: ~{t_min}d {t_sec:02d}s\n"
      f"Tüketim: {int(est['consumed_mah'])} mAh | Gerekli (rezerv): "
      f"{int(est['required_mah'])} mAh{suf}"
    )

  @pyqtSlot(dict)
  def update_telemetry(self, data: dict):
    lat = data.get("lat")
    lon = data.get("lon")
    hdg = data.get("hdg", 0)
    if lat and lon:
      if not self._first_home_set:
        # Aracın ilk konumu = otomatik kalkış (HOME) noktası
        self._home_lat = float(lat)
        self._home_lon = float(lon)
        self._map.center_at(float(lat), float(lon))
        self._map.set_home(float(lat), float(lon))
        self._first_home_set = True
      self._map.set_drone_pos(float(lat), float(lon), float(hdg))

  def _on_send(self):
    """Görev gönderimi — her somut sekme kendi mission_type'ı ile override eder.

    NEDEN (ölü kontrol): Buradaki eski gövde mission_type="WAYPOINT" gönderiyordu;
    İHA/companion böyle bir görev tipi TANIMIYOR. Her iki somut sekme de override
    ettiği için şu an ulaşılamıyor, ancak ileride override etmeyen bir sekme
    eklenirse sessizce geçersiz görev gönderilirdi. Artık sessiz kalmak yerine
    geliştirme zamanında patlıyor.
    """
    raise NotImplementedError(
      f"{type(self).__name__}._on_send() override edilmeli — "
      "geçerli bir mission_type ile mission_ready_to_send yayılmalıdır."
    )

  def get_mission_params(self) -> dict:
    """Subclass'lar ek parametreleri döndürür."""
    return {
      "waypoints": list(self._wps),
      "speeds": {
        "takeoff": self._sp_tkf.value(),
        "cruise": self._sp_crz.value(),
        "land": self._sp_lnd.value(),
      },
      "return_to_home": self._rth_cb.isChecked(),
      # auto_start KAPALI — kullanıcı TopBar'daki "GÖREVİ BAŞLAT"
      # butonuyla manuel tetikler. Yardımcı Bilgisayar sadece görev datasını saklar.
      "auto_start": False,
      "auto_start_delay_s": 0.0,
    }


# ── Sekme 1: AI Görevleri ─────────────────────────────────────────────────────
class TargetDropMissionTab(_BaseMissionTab):
  """
  TÜBİTAK İHA Yarışması Döner Kanat Kategorisi Görev 2.

  Görev akışı:
   • Drone tarama alanı waypoint'lerini uçar.
   • Her WP'de kamera görüntüsünden mavi altıgen ve kırmızı üçgeni arar.
   • Tespit edilen hedefe uygun yükü (slot) otonom bırakır.
   • Bonus (Görev 3): ilk bırakılan yükü yerden al, ikinci hedefe bırak.

  Hedefler (yarışma tanımı):
   Mavi düzgün altıgen  (kenar 2 m) → Slot 1 — kırmızı dış yük
   Kırmızı eşkenar üçgen (kenar 1 m) → Slot 2 — mavi dış yük
  """

  # Hedef tespit durumu güncellemesi (air tarafından polling ile alınır)
  target_detected = pyqtSignal(dict)

  # Harita tıklamasının ne ekleyeceği
  _SEL_AREA  = "Tarama Köşesi"
  _SEL_POLE1 = "Direk 1"
  _SEL_POLE2 = "Direk 2"
  _SEL_FW_AREA = "Sabit Kanat Pisti Köşesi"
  # Operatörün haritada işaretlediği TAHMİNİ hedef bölgeleri (opsiyonel).
  # Verilmezse görev tam alan taraması yapar — geriye dönük tam uyumlu.
  _SEL_RED_AREA  = "Kırmızı Hedef Bölgesi (tahmini)"
  _SEL_BLUE_AREA = "Mavi Hedef Bölgesi (tahmini)"

  # Çeldirici yerleşimi modu — bkz. _MODE_MIXED/_MODE_SEPARATE NEDEN
  _MODE_MIXED    = "Karışık (çeldiriciler hedeflerle aynı alanda)"
  _MODE_SEPARATE = "Ayrık (Sabit Kanat ayrı pistte)"

  def _build_specific_controls(self):
    self._pole1: dict | None = None
    self._pole2: dict | None = None
    self._fw_wps: list = []
    # Tahmini hedef bölgeleri — renk başına poligon köşeleri.
    self._red_area_wps: list = []
    self._blue_area_wps: list = []

    # NEDEN: Bu sekmedeki TÜM spin kutuları uçuş parametresi taşıyor; fabrika
    # fonksiyonlarına tek noktadan tekerlek koruması eklenir (canlı testte
    # "Bırakma Yarıçapı" kaydırma sırasında 4.0 → 1.0 düşmüştü).
    def _dsb(lo, hi, val, step, suffix, dec=2):
      w = QDoubleSpinBox(); w.setRange(lo, hi); w.setValue(val)
      w.setSingleStep(step); w.setDecimals(dec); w.setSuffix(suffix)
      w.setMaximumWidth(140)  # sayısal girdi — uçsuz uzamasın (spec §3)
      return guard_wheel(w)

    def _isb(lo, hi, val, suffix=""):
      w = QSpinBox(); w.setRange(lo, hi); w.setValue(val)
      if suffix: w.setSuffix(suffix)
      w.setMaximumWidth(140)  # sayısal girdi — uçsuz uzamasın (spec §3)
      return guard_wheel(w)

    grp = QGroupBox("Hedef Tespit + Yük Bırakma (TÜBİTAK Görev 2/3)")
    self._task2_grp = grp
    lay = QVBoxLayout(grp); lay.setSpacing(6)

    # ── ÇELDİRİCİ YERLEŞİMİ MODU (DEVIR §0p, kullanıcı isteğiyle) ─────────
    # NEDEN: şartname (s.27/48-49), sabit kanat kategorisinin gerçek
    # hedeflerinin ("çeldirici" dediğimiz kareler) Döner Kanat ile AYNI
    # uçuş alanını paylaştığını gösteriyor — AMA bazı sahalarda sabit kanat
    # ayrı bir pistte/alanda uçurulabiliyor. Ayrıksa, İHA'nın çeldirici-
    # savunma mekanizmalarının (erken kimlik bekleyişi, alan çapraz
    # kontrolü, çoklu deneme — hepsi §0j-§0m'de eklendi) HİÇBİRİNE gerek
    # yok, yalnızca SÜRE maliyeti eklerler. Mod seçimi tek bir "decoy_layout"
    # parametresiyle İHA'ya gider (Task2Tuning.from_params, _SEPARATE_
    # LAYOUT_PRESET); "Karışık" gönderilmezse ya da seçilirse davranış
    # BUGÜNKÜYLE BİREBİR AYNI kalır (geriye dönük tam uyumlu).
    mode_row = QHBoxLayout()
    mode_row.addWidget(QLabel("Çeldirici yerleşimi:"))
    self._decoy_mode = QComboBox()
    self._decoy_mode.addItems([self._MODE_MIXED, self._MODE_SEPARATE])
    self._decoy_mode.setToolTip(
      "Karışık: çeldiriciler (sabit kanadın gerçek hedefleri) Döner Kanat "
      "tarama alanıyla aynı bölgede — mevcut çeldirici-savunma mantığı "
      "(erken kimlik doğrulama, alan kontrolü, çoklu deneme) AKTİF kalır.\n"
      "Ayrık: sabit kanat ayrı bir pistte — bu mantık DEVRE DIŞI bırakılır, "
      "görev daha hızlı tamamlanır. Aşağıda sabit kanat pistinin alanını "
      "(4 köşe) işaretleyin — yalnızca haritada/operatör bilgisi içindir."
    )
    guard_wheel(self._decoy_mode)
    self._decoy_mode.currentIndexChanged.connect(self._on_decoy_mode_changed)
    mode_row.addWidget(self._decoy_mode, 1)
    lay.addLayout(mode_row)

    self._hedef_bolge_lbl = QLabel(
        "Tahmini hedef bölgesi: işaretlenmedi (opsiyonel — işaretlenirse arama "
        "o bölgeye daraltılır, işaretlenmezse tüm alan taranır).")
    self._hedef_bolge_lbl.setObjectName("mutedLabel")
    self._hedef_bolge_lbl.setWordWrap(True)
    self._hedef_bolge_lbl.setToolTip(
        "Haritada renk başına en az 3 köşe işaretleyin. Bu bir İPUCUDUR, kanıt "
        "DEĞİLDİR:\n"
        "• Hedef yine GÖRÜNTÜ İŞLEME ile tespit edilir ve kimlik kapısından geçer.\n"
        "• Bölge boş çıkarsa araç tam alan taramasına döner ve o bölge negatif "
        "olarak İŞARETLENMEZ.\n"
        "• Bölge küçüldükçe İHA daha alçaktan ve daha az geçişle bakar "
        "(tek bakış → nokta dizisi → mini şerit taraması).")
    lay.addWidget(self._hedef_bolge_lbl)

    self._fw_area_lbl = QLabel("Sabit Kanat pisti: 0 köşe işaretlendi (en az 3 gerekir).")
    self._fw_area_lbl.setObjectName("mutedLabel")
    self._fw_area_lbl.setWordWrap(True)
    self._fw_area_lbl.setVisible(False)
    lay.addWidget(self._fw_area_lbl)

    # ── Görev akışı: harita seçim hedefi + Direk 1/2 ──────────────────────
    sel_row = QHBoxLayout()
    sel_row.addWidget(QLabel("Harita tıklaması:"))
    self._sel_target = QComboBox()
    self._sel_target.addItems([self._SEL_AREA, self._SEL_POLE1, self._SEL_POLE2,
                               self._SEL_RED_AREA, self._SEL_BLUE_AREA,
                                self._SEL_FW_AREA])
    guard_wheel(self._sel_target)  # kaydırma tıklama hedefini değiştirmesin
    sel_row.addWidget(self._sel_target, 1)
    lay.addLayout(sel_row)

    self._pole_lbl = QLabel("Direk 1: —    Direk 2: —")
    self._pole_lbl.setObjectName("mutedLabel")
    lay.addWidget(self._pole_lbl)

    # ── İrtifa ────────────────────────────────────────────────────────────
    alt_grp = QGroupBox("İrtifa"); alt_f = QFormLayout(alt_grp); alt_f.setSpacing(5)
    alt_f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    # NEDEN (30.0 → 16.0): Bu panelin gönderdiği "scan_altitude_m",
    # IHA/missions/task2/tuning.py içindeki Task2Tuning.scan_alt varsayılanını
    # from_params() ile EZER. Orada değer bilinçli olarak 30 → 16 m indirildi
    # (tuning.py:29-39, tespit bütçesi analizi): 320×240 lores + 63° HFOV ile
    # 30 m'de 1 m kenarlı KIRMIZI ÜÇGEN ~26 px² eder ve video_streamer'ın
    # MIN_AREA_PX=60 eşiğinde SESSİZCE elenir; sahada yalnızca mavi altıgen
    # bulunur. YKİ 30 gönderdiği sürece İHA'nın düzeltmesi hiç devreye girmiyordu
    # (canlı Gazebo uçuşunda "Kalkış tamam (30m)" logu bunun kanıtıdır).
    # Aralık da İHA'nın güvenlik clamp'i ile hizalandı: TeknofestTask2Mission.
    # PARAM_LIMITS["scan_alt"] = (5, 60) — dışına çıkan değer zaten kısılıyor ve
    # operatöre uyarı basılıyordu (teknofest_task2.py:105).
    self._scan_alt    = _dsb(5, 60, 16.0, 1.0, " m", 0)
    self._scan_alt.setToolTip(
      "Tarama irtifası. 16 m saha kalibrasyonlu varsayılandır: daha yükseğe "
      "çıkıldığında kırmızı üçgen (1 m kenar) kamera çözünürlüğünde minimum "
      "alan eşiğinin altına düşüp tespit edilemez. İHA 5-60 m dışını reddeder."
    )
    # NEDEN (aralık 1-15 → 1.5-10): PARAM_LIMITS["drop_alt"] = (1.5, 10.0).
    # Varsayılan 2.0, Task2Tuning.drop_alt ile zaten aynı.
    self._drop_alt    = _dsb(1.5, 10, 2.0, 0.5, " m", 1)
    # NOT: "Yük Alma İrtifası (G3)" alanı KALDIRILDI. O alan TOF ile ölçülen
    # bir hedef irtifaydı; TOF sensörleri araçtan söküldü. Yük alma artık
    # kamerayla hizalanıp motor akımı + irtifa durmasıyla temas tespit ederek
    # yapılıyor — ilgili ayarlar aşağıdaki "Görev 3" grubunda.
    alt_f.addRow("Tarama (maks) İrtifası:", self._scan_alt)
    alt_f.addRow("Bırakma İrtifası:", self._drop_alt)
    # İç kategoriler de açılır bölüm — hepsi KAPALI başlar, operatör
    # ilgilendiği kategoriyi tıklayarak açar (panel tek ekrana sığsın).
    self._alt_sec = wrap_group(alt_grp, expanded=False)
    lay.addWidget(self._alt_sec)

    # ── Kamera / Gimbal ───────────────────────────────────────────────────
    cam_grp = QGroupBox("Kamera / Gimbal"); cam_f = QFormLayout(cam_grp); cam_f.setSpacing(5)
    cam_f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    # 2026-08-30: İLERİ-BAKIŞ AÇISI (tilt) alanı KALDIRILDI — araçta gimbal
    # YOK, kamera gövdeye sabit ve daima nadire bakıyor. Ayarlanacak bir açı
    # kalmadı. (Firmware tarafı: config servos.gimbal.present=false,
    # tuning body_fixed_camera=1, scan_tilt=90.)

    # ── KALİBRASYON EZME — VARSAYILAN OLARAK KAPALI ────────────────────────
    # NEDEN: bu alan İHA'nın ÖLÇÜLEN kalibrasyonunu (config.camera.calibration)
    # from_params ile EZER. Panel varsayılanı 63.0° idi; gerçek kalibrasyon
    # 65.9°. Yani YKİ'den görev göndermek, doğru kalibrasyonu eski yanlış
    # değerle değiştiriyordu. Bu hata bir kez yaşandı ve ÖLÇÜLDÜ: bırakma
    # isabeti 0.26 m yerine 4-5 m oldu, yani puan yarıçapının DIŞINA taştı.
    #
    # Artık kutu işaretlenmedikçe alan GÖNDERİLMEZ ve İHA kendi kalibrasyonunu
    # kullanır. İşaretlenirse alan etkinleşir — bilerek ezmek isteyen operatör
    # için kapı açık, ama yanlışlıkla ezmek imkânsız.
    self._cam_hfov_override = QCheckBox("Kamera kalibrasyonunu EZ (dikkat)")
    self._cam_hfov_override.setToolTip(
      "İşaretli DEĞİLSE İHA kendi ölçülen kalibrasyonunu kullanır (doğrusu bu).\n"
      "İşaretlenirse aşağıdaki HFOV değeri kalibrasyonu ezer. Yanlış değer\n"
      "bırakma isabetini metrelerce bozar — ölçüldü: 0.26 m → 4-5 m.")
    self._cam_hfov  = _dsb(30, 120, 65.9, 0.1, "°", 2)
    self._cam_hfov.setEnabled(False)
    self._cam_hfov_override.toggled.connect(self._cam_hfov.setEnabled)

    # ── PANELDEKİ GEOMETRİ HESAPLARI HANGİ KAMERAYI VARSAYSIN ────────────
    # DÜZELTİLEN HATA: hizalama tabanı ve yaw kademesi etiketleri _cam_hfov'un
    # DEĞERİNİ okuyordu. O alan "kalibrasyonu EZ" kutusu işaretli değilken
    # DEVRE DIŞIdır ve varsayılanı 65.9° (artık araçta olmayan IMX519 profili).
    # Araçta aktif olan IMX296 ise 45.4°. Etiketler böylece hizalama tabanını
    # ~%47 ALÇAK gösteriyordu — operatör panele bakıp "mavi altıgen 4.4 m'de
    # hizalanır" diye plan yaparken İHA 8 m'de hizalıyordu.
    # Çözüm: önizleme geometrisini override kutusundan AYIR. Kutu işaretliyse
    # İHA gerçekten o değeri kullanacağı için o okunur; değilse burada seçilen
    # profil kullanılır ve etiket hangi varsayımla hesapladığını YAZAR.
    # Profil listesi IHA/config.json::camera.profiles ile aynı tutulmalıdır.
    self._geo_cam = QComboBox()
    self._geo_cam.setMaximumWidth(190)  # kamera combo — spec §3 genişlik sınırı (uzun etiketle aynı satırda taşmasın)
    self._geo_cam.addItems([
        "IMX296 GS 6 mm — 45.4° (araçta AKTİF)",
        "IMX519 AF — 65.9°",
    ])
    self._geo_cam.setToolTip(
      "Panelin hizalama tabanı / yaw kademesi hesaplarında VARSAYDIĞI kamera.\n"
      "Uçuşu ETKİLEMEZ — yalnızca bu paneldeki önizleme sayılarını belirler.\n"
      "'Kalibrasyonu EZ' işaretliyse bu seçim yok sayılır ve İHA'ya gerçekten\n"
      "gönderilecek HFOV değeri kullanılır.")
    guard_wheel(self._geo_cam)

    # Kamera aracın MERKEZİNDE (ofset 0). Eski değer 15 cm idi; kamera
    # merkeze alındığında güncellenmedi ve panel her görevde 15 cm gönderiyordu.
    self._cam_offset = _dsb(0, 50, 0.0, 0.5, " cm", 1)
    self._cam_offset.setToolTip(
      "Kameranın gövde merkezinden İLERİ ofseti. Mevcut araçta kamera tam\n"
      "merkezde olduğu için 0. Sıfırdan farklı değer, hedefin yer izdüşümünü\n"
      "o kadar kaydırır.")

    # Tek arg → satır iki sütuna yayılır; "" etiketli form satırı checkbox
    # metnini sağ sütuna sıkıştırıp kırpıyordu.
    cam_f.addRow(self._cam_hfov_override)
    cam_f.addRow("Kamera Görüş Açısı (HFOV):", self._cam_hfov)
    cam_f.addRow("Önizleme Kamerası (uçuşu etkilemez):", self._geo_cam)
    cam_f.addRow("Kamera İleri Ofseti:", self._cam_offset)
    self._cam_sec = wrap_group(cam_grp, expanded=False)
    lay.addWidget(self._cam_sec)

    # ── Tarama Davranışı ──────────────────────────────────────────────────
    sc_grp = QGroupBox("Tarama Davranışı"); sc_f = QFormLayout(sc_grp); sc_f.setSpacing(5)
    sc_f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    # NEDEN (aralık 1-20 → 1-12): PARAM_LIMITS["scan_speed"] = (1.0, 12.0)
    # (teknofest_task2.py:107). 12 m/s üstü değer İHA'da sessizce değil ama
    # UÇUŞ SIRASINDA kısılıyordu; panelde hiç girilememesi daha doğru.
    # Varsayılan 6.0, Task2Tuning.scan_speed ile aynı.
    self._scan_speed    = _dsb(1, 12, 6.0, 0.5, " m/s", 1)
    self._drop_radius   = _dsb(1, 15, 4.0, 0.5, " m", 1)
    self._scan_wp_radius = _dsb(1, 10, 3.0, 0.5, " m", 1)
    self._scan_timeout  = _isb(30, 600, 180, " s")
    sc_f.addRow("Tarama Hızı:", self._scan_speed)
    sc_f.addRow("Bırakma Yarıçapı:", self._drop_radius)
    sc_f.addRow("WP Kabul Yarıçapı:", self._scan_wp_radius)
    sc_f.addRow("Tarama Zaman Aşımı:", self._scan_timeout)
    self._scan_sec = wrap_group(sc_grp, expanded=False)
    lay.addWidget(self._scan_sec)

    # ── Hedef Tanımı (alan-bazlı sınıflandırma) ───────────────────────────
    tg_grp = QGroupBox("Hedef Tanımı (gerçek alan)"); tg_f = QFormLayout(tg_grp); tg_f.setSpacing(5)
    tg_f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    self._red_area  = _dsb(0.05, 5.0, 0.43, 0.01, " m²", 2)
    self._blue_area = _dsb(1.0, 30.0, 10.39, 0.1, " m²", 2)
    tg_f.addRow("Kırmızı Üçgen Alanı:", self._red_area)
    tg_f.addRow("Mavi Altıgen Alanı:", self._blue_area)
    self._target_sec = wrap_group(tg_grp, expanded=False)
    lay.addWidget(self._target_sec)

    # ── Hassas Hizalama / Alçalma Profili ─────────────────────────────────
    # NEDEN: Task2Tuning'e alçalma hız profili eklendi (align_descend_vz,
    # align_descend_max_vz, align_coarse_factor) ve from_params ile override
    # edilebiliyor (tuning.py:117-119). Bu üç değer canlı Gazebo uçuşunda
    # ölçülen soruna karşı ayarlandı: sabit 0.4 m/s alçalma ile hedef başına
    # 116-125 s harcanıyor ve align_timeout_s (120 s) doluyordu
    # (alignment.py:313-335). Sahada yeniden ayarlanabilmesi için panele
    # açıldı; VARSAYILANLAR İHA'nınkiyle BİREBİR aynı tutulmalıdır — aksi
    # halde bu panel, İHA'nın kalibre edilmiş değerini yine sessizce ezer.
    al_grp = QGroupBox("Hassas Hizalama (alçalma profili)")
    al_f = QFormLayout(al_grp); al_f.setSpacing(5)
    al_f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    self._align_vz     = _dsb(0.1, 2.0, 0.4, 0.05, " m/s", 2)   # tuning.align_descend_vz
    self._align_max_vz = _dsb(0.2, 4.0, 1.5, 0.1, " m/s", 1)    # tuning.align_descend_max_vz
    self._align_coarse = _dsb(1.0, 8.0, 3.0, 0.5, "×", 1)       # tuning.align_coarse_factor
    self._align_vz.setToolTip(
      "Alçalma TABAN hızı. Bırakma irtifasının son metrelerinde ve tam "
      "ortalanmış durumda kullanılan hız."
    )
    self._align_max_vz.setToolTip(
      "Alçalma hız TAVANI. Yüksekteyken hız, irtifa payının %25'i kadar "
      "ölçeklenir ve bu değerle sınırlanır (30 m'de ~1.5 m/s)."
    )
    self._align_coarse.setToolTip(
      "Kaba hizalama katsayısı: hedef, merkez toleransının bu katı içindeyse "
      "alçalma durmaz, yarı hızda sürer. Küçültmek daha hassas ama daha yavaş."
    )
    al_f.addRow("Alçalma Hızı (taban):", self._align_vz)
    al_f.addRow("Alçalma Hızı (tavan):", self._align_max_vz)
    al_f.addRow("Kaba Hizalama Katsayısı:", self._align_coarse)
    self._align_sec = wrap_group(al_grp, expanded=False)
    lay.addWidget(self._align_sec)

    # ── Donanım / Servo Kanalları ─────────────────────────────────────────
    pin_grp = QGroupBox("Donanım / Servo Kanalları")
    pin_form = QFormLayout(pin_grp); pin_form.setSpacing(4)
    pin_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    # Varsayılanlar config.json "servos" ile hizalı (gerçek kablolama):
    # gimbal=5, red_drop=6, blue_drop=7, grabber_left=8, grabber_right=9
    # 2026-08-25 KRİTİK DÜZELTME: eskiden gimbal=1, red=2, blue=3, gripL=4,
    # gripR=5 idi — gerçek uçuş logu SERVO1-4'ün MOTOR olduğunu ortaya
    # çıkardı (bkz. İHA config.json fc_params_expected notu). Bu alanlar
    # HER Görev 2/3 başlatmasında KOŞULSUZ gönderiliyor (aşağıda
    # params["gpio_pins"]) — eski değerler kalsaydı her başlatmada 4 kanal
    # reddedilir (izinli aralık artık 5-9) VE grab_r=5 sessizce KABUL EDİLİP
    # gimbal'in kanalıyla ÇAKIŞIRDI (5, artık gimbal'e ait).
    # 2026-08-26 DÜZELTME: aralık 1-16'ydı — İHA tarafı (servo_controller.py
    # is_safe_channel) motor kanallarını (1-4) fail-closed reddediyor, ama
    # YKİ'de hiçbir ön-kontrol yoktu; operatör 1-4 arası bir kanal girip
    # göndersin diye izin verilebiliyordu (reddedilme artık İHA'dan GCS
    # bildirimiyle görünür — bkz. teknofest_task2.py — ama en iyisi kullanıcıyı
    # baştan izinli aralığa sınırlamak). İzinli aralık: 5-9 (SERVO1-4=motor).
    self._pin_drop1 = _isb(5, 9, 6)
    self._pin_drop2 = _isb(5, 9, 7)
    self._pin_grabL = _isb(5, 9, 8)
    self._pin_grabR = _isb(5, 9, 9)
    # 2026-08-30: SERVO5 (gimbal) alanı KALDIRILDI — kanal BOŞTA. Buraya
    # yeni bir aktüatör bağlanırsa hem bu panel hem İHA config.json
    # fc_params_expected SERVO5_FUNCTION güncellenmelidir.
    pin_form.addRow("Kırmızı Yük (red_drop):", self._pin_drop1)
    pin_form.addRow("Mavi Yük (blue_drop):", self._pin_drop2)
    pin_form.addRow("Sol Kapak (grab_L):", self._pin_grabL)
    pin_form.addRow("Sağ Kapak (grab_R):", self._pin_grabR)
    self._pin_sec = wrap_group(pin_grp, expanded=False)
    lay.addWidget(self._pin_sec)

    # ── Mod ───────────────────────────────────────────────────────────────
    self._auto_cb = QCheckBox("Otonom Bırakma (Tespit → Anında Bırak)")
    self._auto_cb.setChecked(True)
    lay.addWidget(self._auto_cb)
    self._bonus_cb = QCheckBox("Bonus Görev 3: Yükü yerden al → ikinci hedefe taşı")
    lay.addWidget(self._bonus_cb)

    # ── GÖREV 3 — KAMERAYLA YÜK ALMA ──────────────────────────────────────
    # TOF sensörleri araçtan söküldü. Bu fazın TAMAMI artık kamerayla
    # yapılıyor: alçalma sırasında görüntüyle sürekli düzeltme, kavrayıcı
    # kapanma eksenine yaw hizalaması, ve motor akımı + irtifa durmasıyla
    # yere temas tespiti. Değerler benzetimle süpürüldü ve Gazebo'da
    # doğrulandı; buradan saha koşuluna göre değiştirilebilir.
    g3_grp = QGroupBox("Görev 3 — Kamerayla Yük Alma")
    g3_v = QVBoxLayout(g3_grp); g3_v.setSpacing(4)

    g3_f = QFormLayout(); g3_f.setSpacing(5)
    g3_f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    # 2026-08-30: Görev 3 gimbal açısı alanı KALDIRILDI — gimbal yok,
    # kavrama zaten nadirden yapılıyor (firmware grab_gimbal=90 sabiti).

    self._grab_vz_max = _dsb(0.1, 1.5, 0.60, 0.05, " m/s", 2)
    self._grab_vz_max.setToolTip("Tam hizalıyken azami alçalma hızı.")

    self._grab_u_gate = _dsb(0.10, 0.60, 0.35, 0.01, "", 2)
    self._grab_u_gate.setToolTip(
      "Alçalma kapısı: hedefin kare yarı-boyutuna göre normalize hatası.\n"
      "0 = merkez, 1 = kare kenarı. Bu değerin üstünde alçalma DURUR,\n"
      "aşılırsa araç TIRMANIR — böylece hedefin kadrajdan çıkması\n"
      "yapısal olarak engellenir. Küçültmek isabeti artırır ama süreyi uzatır.")

    self._grab_commit_alt = _dsb(0.05, 0.50, 0.12, 0.01, " m", 2)
    self._grab_commit_alt.setToolTip(
      "Taahhüt irtifası: altında düzeltme KESİLİR ve düz inilir.\n"
      "Nedeni fiziksel — kameranın asgari odak mesafesi ve kavrayıcıların\n"
      "kadraj merkezini gölgelemesi. Taahhüde YALNIZCA hata yeterince\n"
      "küçükken girilir; değilse tırmanılıp yeniden hizalanır.")

    self._grab_commit_vz = _dsb(0.2, 1.2, 0.80, 0.05, " m/s", 2)
    self._grab_commit_vz.setToolTip(
      "Kör (taahhüt) fazındaki iniş hızı. SEZGİYE AYKIRI: burada HIZLI\n"
      "inmek daha isabetli. Kör fazda düzeltme zaten yok; tek değişken\n"
      "bozucuya maruz kalma SÜRESİ. 480 benzetim uçuşunda 0.50→0.60→0.80\n"
      "m/s gittikçe isabet monoton arttı (415→445→464 / 480).")
    g3_f.addRow("Alçalma Hızı (tavan):", self._grab_vz_max)
    g3_f.addRow("Alçalma Kapısı (u):", self._grab_u_gate)
    g3_f.addRow("Taahhüt İrtifası:", self._grab_commit_alt)
    g3_f.addRow("Taahhüt İniş Hızı:", self._grab_commit_vz)
    g3_v.addLayout(g3_f)

    # ── Yaw hizalama ──────────────────────────────────────────────────────
    yaw_lbl = QLabel("Yaw Hizalama (kavrayıcı kapanma ekseni)")
    yaw_lbl.setObjectName("sectionTitle")
    g3_v.addWidget(yaw_lbl)
    yaw_uyari = QLabel(
      "⚠ Eksen açısı ve işaret MONTAJA bağlıdır, ÖLÇÜLMELİDİR. Yanlışsa yaw "
      "çevrimi kapanmaz ve araç inmeyi reddeder.")
    yaw_uyari.setWordWrap(True)
    yaw_uyari.setObjectName("warningText")
    g3_v.addWidget(yaw_uyari)

    yaw_f = QFormLayout(); yaw_f.setSpacing(5)
    yaw_f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    self._grab_axis = _dsb(-180, 180, 0.0, 1.0, "°", 1)
    self._grab_axis.setToolTip(
      "Kavrayıcıların kapanma ekseninin GÖRÜNTÜDEKİ açısı.\n"
      "Tezgâhta ölçülür: yükü kavrayıcılar arasına koyup kameradan bakın,\n"
      "yükün görüntüdeki açısı ile kapanma ekseni arasındaki fark budur.")
    self._grab_yaw_sign = QComboBox()
    self._grab_yaw_sign.setMaximumWidth(160)  # kısa metin combo
    self._grab_yaw_sign.addItems(["+1 (düz)", "-1 (ters)"])
    # NEDEN "+1" ÖNCE (varsayılan): panel varsayılanı İHA'ya HER GÖREVDE
    # gönderilir, yani firmware'deki değeri EZER. Liste "-1" ile başlıyordu
    # ve tuning.py'de ölçümle +1.0'a çekilen değeri sessizce geri -1'e
    # döndürüyordu — düzeltme uçuşa hiç ulaşmazdı. Sıra, tuning.py'deki
    # ölçülmüş varsayılanla AYNI olmalıdır (bkz. grab_yaw_sign NOT'u:
    # Gazebo kaydında -1 ile hata 10.8°→39°'ye ıraksadı).
    self._grab_yaw_sign.setToolTip(
      "Aracı +yaw döndürmek hedefin görüntüdeki açısını ARTIRIYOR mu,\n"
      "AZALTIYOR mu? Tezgâhta 30 saniyede ölçülür: aracı elde tutup yaw'ı\n"
      "azıcık döndürün ve görüntüdeki açının hangi yöne gittiğine bakın.\n"
      "Yanlış seçim düzeltmeyi TERS yöne sürer, hata hiç kapanmaz.\n"
      "Simde ÖLÇÜLDÜ: +1 doğru. Gerçek araçta montaj farklıysa yeniden ölçün.")
    self._grab_yaw_gate = _dsb(5, 45, 15.0, 1.0, "°", 0)
    self._grab_yaw_gate.setToolTip(
      "Yaw kapısı: bu değerin üstünde alçalma durur. Yaw ve konum AYNI\n"
      "yakalama zarfını paylaşır — 11 cm zarfta 8 cm yükün dönmüş\n"
      "sınırlayıcı kutusu ~33°'de zarfı doldurur, o noktada konum payı sıfırdır.")
    self._grab_yaw_sym = QComboBox()
    # NEDEN "90° (kare)" ÖNCE (varsayılan): ASHİNA yükü 8x8 cm KAREDİR.
    # Liste "180°" ile başlıyordu ve panel bunu her görevde İHA'ya
    # gönderip tuning.py'deki doğru 90.0 değerini eziyordu. Ölçüldü:
    # 180° ile 26 koşunun 26'sı yaw kapısında takıldı (medyan hata 82.2°);
    # 90° ile aynı ham açılar 7.8° medyana düşüyor ve 25'i geçiyor.
    self._grab_yaw_sym.setMaximumWidth(160)  # kısa metin combo
    self._grab_yaw_sym.addItems(["90° (kare)", "180° (dikdörtgen)", "360° (asimetrik)"])
    self._grab_yaw_sym.setToolTip(
      "Yükün dönme simetrisi. Kavrayıcılar karşılıklı kapandığı için yükü\n"
      "θ ya da θ+180°'de kavramak aynıdır; simetriyi hesaba katmamak aracı\n"
      "gereksiz yere 180° döndürür ve dönüş sırasında yanal hatayı büyütür.")
    yaw_f.addRow("Kapanma Ekseni Açısı:", self._grab_axis)
    yaw_f.addRow("Yaw Komut İşareti:", self._grab_yaw_sign)
    yaw_f.addRow("Yaw Kapısı:", self._grab_yaw_gate)
    yaw_f.addRow("Yük Simetrisi:", self._grab_yaw_sym)
    g3_v.addLayout(yaw_f)

    # ── Yere temas ────────────────────────────────────────────────────────
    td_lbl = QLabel("Yere Temas Tespiti")
    td_lbl.setObjectName("sectionTitle")
    g3_v.addWidget(td_lbl)
    td_f = QFormLayout(); td_f.setSpacing(5)
    td_f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    self._td_ratio = _dsb(0.20, 0.90, 0.55, 0.05, "", 2)
    self._td_ratio.setToolTip(
      "Temas eşiği, ASKI AKIMININ ORANI olarak. Sabit amper DEĞİL:\n"
      "askı akımı batarya gerilimiyle, yükle ve hava yoğunluğuyla kayar.\n"
      "Orana bağlamak, akım sensörünün ÖLÇEK hatasına da bağışıklık verir\n"
      "— yanlış kalibre bir sensörle bile pay ve payda sadeleşir.")
    self._td_arm = _dsb(0.2, 3.0, 1.0, 0.1, " m", 1)
    self._td_arm.setToolTip(
      "İrtifa tanığı, alçalmanın başından bu kadar inilmeden SİLAHLANMAZ.\n"
      "ÖLÇÜLDÜ: bu kapı yokken tanık alçalma komutu verilir verilmez\n"
      "tetiklendi (araç henüz hızlanmamıştı) ve 5.56 m'de 'TEMAS' dedi —\n"
      "kavrayıcı havada kapandı, görev boş kavrayıcıyla 'başarılı' raporladı.")
    td_f.addRow("Akım Düşüş Oranı:", self._td_ratio)
    td_f.addRow("Silahlanma Mesafesi:", self._td_arm)
    g3_v.addLayout(td_f)

    # ── Yük görüsü + iki kademeli yaw ─────────────────────────────────────
    pv_lbl = QLabel("Yük Görüsü ve Yaw Kademeleri")
    pv_lbl.setObjectName("sectionTitle")
    g3_v.addWidget(pv_lbl)
    pv_note = QLabel(
      "Yük tanıma, aracın yükü BIRAKTIĞI irtifada açılır — o irtifada yükün "
      "görülebilirliği aynı uçuşta ölçülmüştür. Erken açmak kesik tespit "
      "üretir ve görev 'yük yok' diyerek iptal olur.")
    pv_note.setWordWrap(True)
    pv_note.setObjectName("mutedLabel")
    g3_v.addWidget(pv_note)

    pv_f = QFormLayout(); pv_f.setSpacing(5)
    pv_f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    self._grab_vis_alt = _dsb(0.0, 10.0, 0.0, 0.5, " m", 1)
    self._grab_vis_alt.setToolTip(
      "Yük tanımanın açılacağı irtifa. 0 = Bırakma İrtifasını kullan (önerilen).\n"
      "Elle bir değer vermek, yükü bırakıldığından farklı bir ölçekte aramak\n"
      "demektir — tespit eşiğe yaklaşır ve kesik kesik gelir.")
    self._grab_vis_settle = _dsb(1.0, 15.0, 3.0, 0.5, " s", 1)
    self._grab_vis_settle.setToolTip(
      "Görü açıldıktan sonra tespitin oturması için tanınan süre.\n"
      "Bu süre içinde yeterli ardışık onay gelmezse faz güvenle atlanır.\n"
      "Tek kareye bakmak, dedektörün kendi zamansal filtresi (2 kare)\n"
      "yüzünden yük ORADAYKEN bile 'yok' kararı üretebiliyordu.")
    self._grab_vis_conf = _dsb(1, 10, 3, 1, " kare", 0)
    self._grab_vis_conf.setToolTip("Görüyü 'oturmuş' saymak için ardışık onay sayısı.")

    self._yaw_coarse = _dsb(0.02, 0.60, 0.12, 0.01, "", 2)
    self._yaw_fine = _dsb(0.05, 0.90, 0.40, 0.01, "", 2)
    self._yaw_fine_kp = _dsb(0.05, 1.00, 0.40, 0.05, "×", 2)
    self._yaw_fine_kp.setToolTip(
      "İnce kademede yaw kazancının çarpanı. Bu pencerede taahhüt\n"
      "irtifasına çok az kaldığı için büyük yaw komutu aracı yatırır\n"
      "ve yükü kaydırır — kazanç kısılır, düzeltme ince ayara döner.")
    # Kaplama oranının irtifa karşılığı kameraya bağlı; operatör oranı
    # ayarlarken hangi irtifada devreye gireceğini görmeli — yoksa %40 gibi
    # makul görünen bir sayı, pratikte hiç ulaşılamayan bir irtifaya denk gelir.
    self._yaw_stage_lbl = QLabel("")
    self._yaw_stage_lbl.setWordWrap(True)
    self._yaw_stage_lbl.setObjectName("mutedLabel")
    # DÜZELTİLEN HATA: etiket yalnızca iki eşiğe bağlıydı. Ama içindeki
    # "İNCE kademe taahhüt irtifasının ALTINDA kalıyor" uyarısı _grab_commit_alt
    # ve HFOV'a bakarak üretiliyor — o iki değer değiştiğinde etiket yeniden
    # hesaplanmadığı için uyarı, korumak istediği parametre değiştirildiğinde
    # tam da hiç çıkmıyordu (bayat metin ekranda kalıyordu).
    for _w in (self._yaw_coarse, self._yaw_fine, self._grab_commit_alt,
               self._cam_hfov):
      _w.valueChanged.connect(lambda *_: self._update_yaw_stage_lbl())
    for _w in (self._cam_hfov_override, ):
      _w.toggled.connect(lambda *_: (self._update_yaw_stage_lbl(),
                                     self._update_floor_lbl()))
    self._geo_cam.currentIndexChanged.connect(
      lambda *_: (self._update_yaw_stage_lbl(), self._update_floor_lbl()))

    pv_f.addRow("Görü Başlangıç İrtifası:", self._grab_vis_alt)
    pv_f.addRow("Görü Oturma Süresi:", self._grab_vis_settle)
    pv_f.addRow("Görü Onay Sayısı:", self._grab_vis_conf)
    pv_f.addRow("KABA Yaw Eşiği (kaplama):", self._yaw_coarse)
    pv_f.addRow("İNCE Yaw Eşiği (kaplama):", self._yaw_fine)
    pv_f.addRow("İnce Kademe Kazanç Çarpanı:", self._yaw_fine_kp)
    g3_v.addLayout(pv_f)
    g3_v.addWidget(self._yaw_stage_lbl)

    self._g3_sec = wrap_group(g3_grp, expanded=False)
    lay.addWidget(self._g3_sec)

    # ── YAKLAŞMA EĞRİSİ (Görev 2 ve 3 ortak) ──────────────────────────────
    ap_grp = QGroupBox("Hedefe Yaklaşma — İrtifa Eğrisi ve Gimbal İzleme")
    ap_v = QVBoxLayout(ap_grp); ap_v.setSpacing(4)
    ap_note = QLabel(
      "Hedefe giderken irtifa ve gimbal açısı kalan mesafenin fonksiyonudur: "
      "araç hedefe VARDIĞINDA zaten hizalama tabanındadır ve kamera nadire "
      "dönmüştür. Hizalama tabanı hedefin kadraja sığdığı en alçak irtifadır; "
      "altına inmek isabeti artırmaz, taşan kenar ağırlık merkezini kaydırır.")
    ap_note.setWordWrap(True)
    ap_note.setObjectName("mutedLabel")
    ap_v.addWidget(ap_note)

    ap_f = QFormLayout(); ap_f.setSpacing(5)
    ap_f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    self._ap_enable = QCheckBox("Eğriyle yaklaşma etkin (kapalı = eski iki adım)")
    self._ap_enable.setChecked(True)
    ap_v.addWidget(self._ap_enable)

    self._ap_k = _dsb(0.3, 3.0, 1.0, 0.1, "", 2)
    self._ap_k.setToolTip(
      "Eğri üsteli. YÖN: büyük değer DAHA ALÇAK verir.\n"
      "  k > 1 → ERKEN iniş (yolun başında hızlı alçalır)\n"
      "  k = 1 → doğrusal (kalan mesafeyle orantılı)\n"
      "  k < 1 → GEÇ iniş (irtifayı korur, son metrelerde iner)")
    self._ap_fill = _dsb(0.4, 1.0, 0.8, 0.05, "", 2)
    self._ap_fill.setToolTip(
      "Hizalama tabanında hedefin kadrajı kaplama oranı.\n"
      "1.0 = hedef kadrajı tam doldurur (en alçak geçerli irtifa).\n"
      "0.8 = %25 kenar payı — rüzgâr/yalpa ile hedef kenara kaysa da kırpılmaz.")
    # 2026-08-30: 'Gimbal hedefi kademeli izlesin' kutusu KALDIRILDI —
    # parametresi (approach_gimbal_track) firmware'de ZATEN ölüydü ve
    # gimbal söküldüğü için kavramın kendisi de kalmadı.

    self._ap_floor_red = _dsb(0.0, 20.0, 0.0, 0.5, " m", 1)
    self._ap_floor_blue = _dsb(0.0, 20.0, 0.0, 0.5, " m", 1)
    _floor_tip = ("0 = OTOMATİK: hedef alanı + aktif kamera FOV'undan türetilir.\n"
                  "Kamera profili değişince kendiliğinden güncellenir — sabit\n"
                  "yazılan bir değer, profil değişince sessizce yanlışlanır.")
    self._ap_floor_red.setToolTip(_floor_tip)
    self._ap_floor_blue.setToolTip(_floor_tip)

    ap_f.addRow("Eğri Üsteli (k):", self._ap_k)
    ap_f.addRow("Kadraj Doluluk Oranı:", self._ap_fill)
    ap_f.addRow("Kırmızı Hizalama Tabanı:", self._ap_floor_red)
    ap_f.addRow("Mavi Hizalama Tabanı:", self._ap_floor_blue)
    ap_v.addLayout(ap_f)

    self._ap_floor_lbl = QLabel("")
    self._ap_floor_lbl.setWordWrap(True)
    self._ap_floor_lbl.setObjectName("mutedLabel")
    ap_v.addWidget(self._ap_floor_lbl)
    for _w in (self._ap_fill, self._ap_floor_red, self._ap_floor_blue,
               self._cam_hfov, self._red_area, self._blue_area, self._drop_alt):
      _w.valueChanged.connect(lambda *_: self._update_floor_lbl())

    # ── HEDEF KUYRUĞU ─────────────────────────────────────────────────────
    self._queue_cb = QCheckBox("Yolda görülen diğer hedefi hafızaya al")
    self._queue_cb.setChecked(True)
    self._queue_cb.setToolTip(
      "Bir hedefe giderken kadraja giren DİĞER hedefin koordinatı kaydedilir\n"
      "ve sırası gelince tarama YAPILMADAN oraya gidilir.\n"
      "Kapalıysa ikinci hedef için baştan tam tarama turu atılır (60–100 s).")
    ap_v.addWidget(self._queue_cb)
    q_f = QFormLayout(); q_f.setSpacing(5)
    q_f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    self._queue_conf = _dsb(1, 10, 3, 1, " kare", 0)
    self._queue_conf.setToolTip(
      "Kuyruğa yazmadan önce gereken ardışık onay sayısı.\n"
      "Tek kare yeterli değil: yanlış-pozitif bir izdüşüm, alanın dışında\n"
      "bir koordinatı kuyruğa sokar ve o hedef 'denendi' sayılıp kaybedilir.")
    self._queue_spread = _dsb(1.0, 30.0, 6.0, 0.5, " m", 1)
    self._queue_spread.setToolTip(
      "Ardışık onaylar bu mesafeden fazla oynarsa örneklem sıfırlanır —\n"
      "kararsız bir tespitin ortalaması 'üç kez onaylandı' diye güven verir\n"
      "ama koordinatı uydurur.")
    q_f.addRow("Kuyruk Onay Sayısı:", self._queue_conf)
    q_f.addRow("Kuyruk Yayılım Sınırı:", self._queue_spread)
    ap_v.addLayout(q_f)

    self._approach_sec = wrap_group(ap_grp, expanded=False)
    lay.addWidget(self._approach_sec)
    self._update_floor_lbl()
    self._update_yaw_stage_lbl()
    # Görev 3 ayarları yalnız bonus seçiliyken anlamlı.
    # DİKKAT: artık SARMALAYICI bölüm devre dışı bırakılır — yalnız kutuyu
    # kapatmak, başlığı tıklanabilir bırakıp devre dışı bölümü açtırırdı.
    self._g3_sec.setEnabled(self._bonus_cb.isChecked())
    self._bonus_cb.toggled.connect(self._g3_sec.setEnabled)

    # Görev tipini belirleyen ÜST DÜZEY bölüm açık başlar (diğerleri kapalı).
    self._task2_sec = wrap_group(grp, expanded=True)
    self._right_lay.insertWidget(0, self._task2_sec)

  # ── Panelde canlı geometri: operatör sayının ne demek olduğunu görsün ────
  # NEDEN bu iki etiket var: hem "kadraj doluluk oranı" hem "yaw kaplama
  # eşiği" BOYUTSUZ oranlardır ve karşılıkları KAMERAYA bağlıdır. %40 gibi
  # makul görünen bir sayı, dar lensle ulaşılamayacak bir irtifaya denk
  # gelebilir. Karşılığı panelde yazmazsa operatör bunu ancak uçuşta anlar.
  # Formüller İHA tarafındakiyle aynı (missions/task2/geometry.py):
  #   çevrel çap  2R = 2·√(2A / (n·sin(2π/n)))
  #   taban        h = extent / (2·fill·tan(fov/2)),  yatay ve dikeyin BÜYÜĞÜ
  # VFOV: 2026-08-30 DÜZELTİLDİ. Eskiden `vfov = hfov · 0.75` yazıyordu —
  # bu, görüş açısının sensör boyutuyla DOĞRUSAL değiştiğini varsayar ve
  # YANLIŞTIR. Pinhole geometrisinde bağıntı TANJANTLAR üzerindendir:
  #     tan(vfov/2) = (H/W) · tan(hfov/2)
  # HFOV 65.9° / 4:3 karede gerçek VFOV 51.80°, eski kural 49.43° veriyordu
  # — 2.4° hata. İHA tarafı bu düzeltmeyi zaten yapmıştı
  # (teknofest_task2.py TargetFilter kurulumu); YKİ önizlemesi ayrışıktı.
  @staticmethod
  def _poly_extent_m(area_m2: float, n: int) -> float:
    if area_m2 <= 0 or n < 3:
      return 0.0
    return 2.0 * math.sqrt(2.0 * area_m2 / (n * math.sin(2.0 * math.pi / n)))

  def _geo_hfov(self) -> tuple[float, str]:
    """
    Panel geometri hesaplarının kullanacağı HFOV → (derece, kaynak_metni).

    Öncelik: "kalibrasyonu EZ" işaretliyse İHA'ya GERÇEKTEN gönderilecek değer;
    değilse önizleme kamerası seçimi. İkisini karıştırmak, devre dışı bir
    alanın varsayılanıyla hesap yapmak demekti (bkz. _geo_cam yorumu).
    """
    if self._cam_hfov_override.isChecked():
      return self._cam_hfov.value(), "panelden EZİLEN değer"
    if self._geo_cam.currentIndex() == 0:
      return 45.4, "IMX296 GS (araçta aktif)"
    return 65.9, "IMX519 AF"

  @staticmethod
  def _fit_alt_m(extent_m: float, hfov_deg: float, fill: float) -> float:
    if extent_m <= 0 or hfov_deg <= 0:
      return 0.0
    vfov = _vfov_deg(hfov_deg)
    f = max(0.05, min(1.0, fill))
    hw = extent_m / (2.0 * f * math.tan(math.radians(hfov_deg) / 2.0))
    hh = extent_m / (2.0 * f * math.tan(math.radians(vfov) / 2.0))
    return max(hw, hh)

  def _update_floor_lbl(self):
    """Hizalama tabanlarının o anki kamerayla ne olacağını yazar."""
    if not hasattr(self, "_ap_floor_lbl"):
      return
    hfov, kaynak = self._geo_hfov()
    fill = self._ap_fill.value()
    drop = self._drop_alt.value()
    parts = []
    for ad, area_w, manual_w, n in (
        ("kırmızı üçgen", self._red_area, self._ap_floor_red, 3),
        ("mavi altıgen", self._blue_area, self._ap_floor_blue, 6)):
      ext = self._poly_extent_m(area_w.value(), n)
      auto = self._fit_alt_m(ext, hfov, fill)
      man = manual_w.value()
      if man > 0:
        parts.append(f"{ad}: {max(drop, man):.2f} m (elle; otomatik olsa "
                     f"{max(drop, auto):.2f} m)")
      else:
        parts.append(f"{ad}: {max(drop, auto):.2f} m (otomatik, çap {ext:.2f} m)")
    self._ap_floor_lbl.setText(
      f"HFOV {hfov:.1f}° [{kaynak}] · doluluk {fill:.2f} → hizalama tabanı — "
      + ";  ".join(parts)
      + f".  (Alt sınır Bırakma İrtifası {drop:.1f} m'dir; bırakma hizalama "
        "kurulduktan sonra bu irtifaya inilerek yapılır.)")

  def _update_yaw_stage_lbl(self):
    """Yaw kaplama eşiklerinin irtifa karşılığını yazar."""
    if not hasattr(self, "_yaw_stage_lbl"):
      return
    hfov, kaynak = self._geo_hfov()
    vfov = _vfov_deg(hfov)
    # 2026-08-30: 0.08 -> 0.061. Firmware tuning.payload_size_m = 0.061
    # (yük 6.1x6.1x2.1 cm). YKİ önizlemesi 8 cm varsayıyordu — %31 büyük.
    size = 0.061                    # yükün kenarı (m) — tuning.payload_size_m
    # Kaplama oranı DOĞRUSAL: frac = yük_boyu / kare_dar_kenarı.
    # Kare dar kenarı irtifa h'de: 2·h·tan(vfov/2)  (dikey daima daha dar).
    tan_v = math.tan(math.radians(vfov) / 2.0)

    def _alt(frac):
      if frac <= 0 or tan_v <= 0:
        return 0.0
      return size / (2.0 * frac * tan_v)

    kaba, ince = self._yaw_coarse.value(), self._yaw_fine.value()
    uyari = ""
    if ince <= kaba:
      uyari = "  ⚠ İNCE eşik KABA eşikten büyük olmalı."
    elif _alt(ince) < self._grab_commit_alt.value():
      uyari = ("  ⚠ İNCE eşik, taahhüt irtifasının ALTINA denk geliyor — "
               "o kademe hiç açılmayacak.")
    self._yaw_stage_lbl.setText(
      f"8 cm yük, HFOV {hfov:.1f}° [{kaynak}]: KABA kademe %{kaba * 100:.0f} → "
      f"~{_alt(kaba):.2f} m irtifada,  İNCE kademe %{ince * 100:.0f} → "
      f"~{_alt(ince):.2f} m irtifada açılır. "
      f"Taahhüt irtifası {self._grab_commit_alt.value():.2f} m." + uyari)

  def _apply_tab_constraints(self):
    """Görev 2'de İHA tarafından OKUNMAYAN alanları devre dışı bırak.

    NEDEN (ölü kontrol): Bu sekme speeds{takeoff,cruise,land} ve return_to_home
    alanlarını gönderiyor, ancak İHA Görev 2 akışında bunları okumuyor; hız
    "Tarama Hızı", irtifa ise "Tarama/Bırakma İrtifası" alanlarından geliyor.
    Kullanıcı bu alanları ayarlayıp uçuşu etkilediğini sanıyordu.
    """
    _tip = ("Bu görev tipinde kullanılmıyor — Görev 2'de hız 'Tarama Hızı' "
        "alanından alınır.")
    for _w in (self._sp_tkf, self._sp_crz, self._sp_lnd):
      _w.setEnabled(False)
      _w.setToolTip(_tip)
    # Başlık artık kutunun değil AÇILIR BÖLÜMÜN üstünde (wrap_group grubun
    # başlığını boşaltır) — uyarıyı bölüm başlığına yaz, yoksa "kullanılmıyor"
    # notu hiç görünmeyen bir QGroupBox başlığında kalırdı.
    self._speed_sec.set_title("Hız Kontrolü (Görev 2'de kullanılmıyor)")
    self._speed_grp.setToolTip(_tip)
    self._speed_sec.header_button().setToolTip(_tip)
    self._speed_note.setText(
      "⚠ Bu görev tipinde kullanılmıyor: İHA, Görev 2'de kalkış/tur/iniş "
      "hızlarını okumaz. Geçerli hız → 'Tarama Davranışı ▸ Tarama Hızı'. "
      "Tur hızı, batarya tahmini doğru kalsın diye tarama hızından otomatik "
      "kopyalanır."
    )
    self._speed_note.setVisible(True)

    # NEDEN: Tur hızı kilitlendiği için batarya tahmini sabit 8 m/s ile
    # hesaplanıp yanıltıcı olurdu; gerçekte uçulan hız "Tarama Hızı".
    self._scan_speed.valueChanged.connect(self._sync_cruise_from_scan)
    self._sync_cruise_from_scan(self._scan_speed.value())

    self._rth_cb.setEnabled(False)
    self._rth_cb.setToolTip(
      "Bu görev tipinde kullanılmıyor — Görev 2'de dönüş davranışını İHA "
      "kendi görev akışına göre yönetir."
    )
    self._rth_cb.setText("🏠 Kalkış Noktasına Dön (Görev 2'de kullanılmıyor)")

  def _sync_cruise_from_scan(self, val: float):
    """Batarya tahmini için tur hızını tarama hızıyla eşitler (Görev 2)."""
    lo, hi = self._sp_crz.minimum(), self._sp_crz.maximum()
    self._sp_crz.setValue(max(lo, min(hi, float(val))))
    self._update_battery()

  def _update_pole_lbl(self):
    p1 = f"{self._pole1['lat']:.5f},{self._pole1['lon']:.5f}" if self._pole1 else "—"
    p2 = f"{self._pole2['lat']:.5f},{self._pole2['lon']:.5f}" if self._pole2 else "—"
    self._pole_lbl.setText(f"Direk 1: {p1}    Direk 2: {p2}")

  def _on_decoy_mode_changed(self, _idx: int = 0):
    """Mod "Ayrık" iken sabit kanat pisti köşe-sayacı görünür olur."""
    is_separate = self._decoy_mode.currentText() == self._MODE_SEPARATE
    self._fw_area_lbl.setVisible(is_separate)
    self._update_fw_area_lbl()

  def _update_hedef_bolge_lbl(self):
    """Tahmini hedef bölgelerinin köşe sayılarını göster."""
    parcalar = []
    for ad, lst in (("Kırmızı", self._red_area_wps), ("Mavi", self._blue_area_wps)):
      if not lst:
        continue
      n = len(lst)
      parcalar.append(f"{ad}: {n} köşe" + ("" if n >= 3 else " (en az 3 gerekir)"))
    if not parcalar:
      self._hedef_bolge_lbl.setText(
          "Tahmini hedef bölgesi: işaretlenmedi (opsiyonel — işaretlenirse arama "
          "o bölgeye daraltılır, işaretlenmezse tüm alan taranır).")
    else:
      self._hedef_bolge_lbl.setText(
          "Tahmini hedef bölgesi — " + "  ·  ".join(parcalar)
          + ".  Bu bir ipucudur: hedef yine görüntü işleme ile doğrulanır.")

  def _update_fw_area_lbl(self):
    n = len(self._fw_wps)
    ok = "✓" if n >= 3 else "⚠"
    self._fw_area_lbl.setText(
      f"Sabit Kanat pisti: {n} köşe işaretlendi (en az 3 gerekir) {ok}")

  @pyqtSlot(float, float)
  def _add_wp(self, lat: float, lon: float):
    """Harita tıklaması: seçime göre Direk 1/2, sabit kanat pisti ya da tarama köşesi ekle."""
    t = self._sel_target.currentText()
    if t == self._SEL_POLE1:
      self._pole1 = {"lat": lat, "lon": lon}
      self._map.draw_circle(lat, lon, 3, _MAP_POLE1_COLOR)
      self._update_pole_lbl()
    elif t == self._SEL_POLE2:
      self._pole2 = {"lat": lat, "lon": lon}
      self._map.draw_circle(lat, lon, 3, _MAP_POLE2_COLOR)
      self._update_pole_lbl()
    elif t == self._SEL_FW_AREA:
      self._fw_wps.append({"lat": lat, "lon": lon})
      self._map.draw_circle(lat, lon, 3, _MAP_FW_AREA_COLOR)
      self._update_fw_area_lbl()
    elif t == self._SEL_RED_AREA:
      self._red_area_wps.append({"lat": lat, "lon": lon})
      self._map.draw_circle(lat, lon, 3, _MAP_RED_AREA_COLOR)
      self._update_hedef_bolge_lbl()
    elif t == self._SEL_BLUE_AREA:
      self._blue_area_wps.append({"lat": lat, "lon": lon})
      self._map.draw_circle(lat, lon, 3, _MAP_BLUE_AREA_COLOR)
      self._update_hedef_bolge_lbl()
    else:
      super()._add_wp(lat, lon)

  def _on_send(self):
    # Tarama alanı bir poligon olmalı → en az 3 köşe (waypoint)
    if len(self._wps) < 3:
      QMessageBox.warning(
        self, "Görev",
        "Tarama alanı için en az 3 köşe (waypoint) gereklidir.\n"
        "Kalkış noktası araçtan otomatik alınır; siz yalnızca tarama "
        "bölgesinin köşelerini işaretleyin."
      )
      return

    if self._home_lat is None or self._home_lon is None:
      QMessageBox.warning(
        self, "Görev",
        "Aracın konumu (kalkış noktası) henüz alınamadı.\n"
        "MAVLink telemetri bağlantısının aktif olduğundan emin olun."
      )
      return

    params = self.get_mission_params()
    # Kalkış noktasını (araç konumu) waypoints'in başına TAKEOFF olarak ekle.
    # Kullanıcının seçtiği tüm WP'ler tarama poligonu köşesi (WAYPOINT) olur.
    takeoff_wp = {
      "lat": self._home_lat, "lon": self._home_lon,
      "alt": self._scan_alt.value(), "type": "TAKEOFF", "idx": 0,
    }
    # NEDEN (sabit 30.0 → tarama irtifası): İHA Görev 2'de poligon köşelerinin
    # "alt" alanını OKUMAZ (geometry.compute_scanline/generate_coverage_path
    # yalnızca lat/lon kullanır, teknofest_task2.py:213-222); uçulan irtifa
    # scan_altitude_m'dir. Yine de burada sabit 30 durması YKİ tarafında
    # tutarsız bir ikinci "tarama irtifası" kaynağı yaratıyordu.
    area_wps = [
      {"lat": w["lat"], "lon": w["lon"], "alt": w.get("alt", self._scan_alt.value()),
       "type": "WAYPOINT", "idx": i + 1}
      for i, w in enumerate(self._wps)
    ]
    params["waypoints"] = [takeoff_wp] + area_wps

    params.update({
      "mission_type":        "TEKNOFEST_TASK2",
      # İrtifa
      "scan_altitude_m":     self._scan_alt.value(),
      "drop_altitude_m":     self._drop_alt.value(),
      # Kamera / gimbal
      # "scan_tilt_deg" GÖNDERİLMİYOR (2026-08-30): gimbal söküldü, kamera
      # sabit nadir. Gönderilseydi firmware'in scan_tilt=90 varsayılanını
      # EZERDİ — bu tam olarak yaşanan hataydı.
      "cam_offset_cm":       self._cam_offset.value(),
      # Tarama davranışı
      "scan_speed_ms":       self._scan_speed.value(),
      "drop_radius_m":       self._drop_radius.value(),
      "scan_wp_radius_m":    self._scan_wp_radius.value(),
      "scan_timeout_s":      self._scan_timeout.value(),
      # Hedef tanımı
      "red_target_area_m2":  self._red_area.value(),
      "blue_target_area_m2": self._blue_area.value(),
      # Hassas hizalama alçalma profili (tuning.py from_params eşlemesi)
      "align_descend_vz":     self._align_vz.value(),
      "align_descend_max_vz": self._align_max_vz.value(),
      "align_coarse_factor":  self._align_coarse.value(),
      # ── Görev 3 — kamerayla yük alma ────────────────────────────────
      # "grab_gimbal_deg" GÖNDERİLMİYOR — sabit nadir (firmware 90 kullanır).
      "grab_vz_max":         self._grab_vz_max.value(),
      "grab_u_gate":         self._grab_u_gate.value(),
      "grab_commit_alt":     self._grab_commit_alt.value(),
      "grab_commit_vz":      self._grab_commit_vz.value(),
      # Yaw hizalama — eksen açısı ve işaret MONTAJA bağlı, ölçülmeli
      "grab_grabber_axis_deg": self._grab_axis.value(),
      # Liste sırası: 0="+1 (düz)", 1="-1 (ters)" — bkz. _grab_yaw_sign NOT'u
      "grab_yaw_sign":       (1.0 if self._grab_yaw_sign.currentIndex() == 0 else -1.0),
      "grab_yaw_gate_deg":   self._grab_yaw_gate.value(),
      # Liste sırası: 0=90°(kare) 1=180°(dikdörtgen) 2=360° — bkz. NOT
      "grab_yaw_symmetry_deg": (90.0, 180.0, 360.0)[self._grab_yaw_sym.currentIndex()],
      # Yük görüsü — erken açmak kesik tespit ve yanlış "yük yok" üretiyordu
      "grab_vision_start_alt":  self._grab_vis_alt.value(),
      "grab_vision_settle_s":   self._grab_vis_settle.value(),
      "grab_vision_confirm_n":  self._grab_vis_conf.value(),
      # İki kademeli yaw kapısı (yükün kadraj kaplama oranına göre)
      "grab_yaw_coarse_frac":   self._yaw_coarse.value(),
      "grab_yaw_fine_frac":     self._yaw_fine.value(),
      "grab_yaw_fine_kp_scale": self._yaw_fine_kp.value(),
      # ── Hedefe yaklaşma eğrisi + gimbal izleme (Görev 2 ve 3 ortak) ──
      "approach_curve_enable":  (1.0 if self._ap_enable.isChecked() else 0.0),
      "approach_curve_k":       self._ap_k.value(),
      # "approach_gimbal_track" GÖNDERİLMİYOR — firmware'de zaten ölü parametre.
      "approach_fill":          self._ap_fill.value(),
      "approach_floor_red_m":   self._ap_floor_red.value(),
      "approach_floor_blue_m":  self._ap_floor_blue.value(),
      # Hedef kuyruğu
      "target_queue_enable":    (1.0 if self._queue_cb.isChecked() else 0.0),
      "target_queue_confirm_n": self._queue_conf.value(),
      "target_queue_spread_m":  self._queue_spread.value(),
      # Yere temas
      "touchdown_current_ratio": self._td_ratio.value(),
      "touchdown_arm_descent_m": self._td_arm.value(),
      # Mod
      "autonomous_drop":     self._auto_cb.isChecked(),
      "bonus_mission":       self._bonus_cb.isChecked(),
      # Donanım
      "gpio_pins": {
          "drop1": self._pin_drop1.value(),
          "drop2": self._pin_drop2.value(),
          "grab_l": self._pin_grabL.value(),
          "grab_r": self._pin_grabR.value(),
      },
    })
    # ── KAMERA KALİBRASYONU: yalnız operatör AÇIKÇA isterse gönderilir ──
    # NEDEN: bu alan İHA'nın ölçülen kalibrasyonunu EZER. Panel her görevde
    # koşulsuz gönderdiği sürece, İHA'nın kendi kalibrasyonu hiç kullanılmaz.
    # Bir kez yaşandı ve ÖLÇÜLDÜ: panel 63.0° gönderirken gerçek değer 65.9°
    # idi; bırakma isabeti 0.26 m yerine 4-5 m oldu — puan yarıçapının dışı.
    # Anahtar SÖZLÜKTE HİÇ BULUNMAZSA from_params o alana dokunmaz.
    if self._cam_hfov_override.isChecked():
        params["camera_hfov_deg"] = self._cam_hfov.value()

    # Direkler (GATE_RUN) — seçildiyse gönder
    if self._pole1:
      params["pole1"] = self._pole1
    if self._pole2:
      params["pole2"] = self._pole2

    # ── Çeldirici yerleşimi modu (DEVIR §0p) ──────────────────────────────
    is_separate = self._decoy_mode.currentText() == self._MODE_SEPARATE
    params["decoy_layout"] = "separate" if is_separate else "mixed"
    if is_separate:
      if len(self._fw_wps) < 3:
        QMessageBox.warning(
          self, "Sabit Kanat Pisti Eksik",
          "\"Ayrık\" modu seçildi ama Sabit Kanat pisti için en az 3 köşe "
          "işaretlenmedi. Haritada \"Sabit Kanat Pisti Köşesi\" seçerek "
          "pistin sınırlarını işaretleyin, ya da modu \"Karışık\"a alın."
        )
        return
      # NEDEN yalnızca operatör/harita bilgisi: İHA'nın tarama alanı ZATEN
      # bu bölgeyi içermez (ayrı pist varsayımı) — bu liste bir geofence
      # DEĞİL, yalnızca YKİ haritasında ve companion loglarında görünürlük
      # sağlar (bkz. Task2Tuning._SEPARATE_LAYOUT_PRESET NEDEN).
      params["fixed_wing_area"] = list(self._fw_wps)

    # ── TAHMİNİ HEDEF BÖLGELERİ (opsiyonel) ───────────────────────────────
    # Yalnızca >= 3 köşe işaretlenmiş renkler gönderilir; eksik işaretleme
    # sessizce YOK SAYILMAZ, operatöre söylenir.
    #
    # NE YAPAR / NE YAPMAZ (İHA tarafı: missions/teknofest_task2._region_scan)
    #   YAPAR : aramanın NEREYE bakacağını daraltır; deseni ve irtifayı bölge
    #           boyutundan türetir (missions/task2/search_plan.py).
    #   YAPMAZ: hiçbir kapıyı atlamaz. Tespit yine görüntü işleme ile yapılır,
    #           geofence + şekil kapısı + bırakma kimlik kapısı aynen uygulanır.
    #           Bölge boş çıkarsa negatif bölge YAZILMAZ.
    _bolgeler = {}
    _eksik = []
    for _renk, _lst, _ad in (("red", self._red_area_wps, "Kırmızı"),
                             ("blue", self._blue_area_wps, "Mavi")):
      if not _lst:
        continue
      if len(_lst) < 3:
        _eksik.append(f"{_ad} ({len(_lst)} köşe)")
        continue
      _bolgeler[_renk] = list(_lst)
    if _eksik:
      QMessageBox.warning(
        self, "Tahmini Hedef Bölgesi Eksik",
        "Şu bölge(ler) için 3'ten az köşe işaretlendi ve GÖNDERİLMEYECEK:\n  "
        + ", ".join(_eksik) +
        "\n\nYa en az 3 köşe işaretleyin ya da işaretleri temizleyin. "
        "Bölge gönderilmezse o renk için TÜM ALAN taranır (görev yine çalışır, "
        "yalnızca daha uzun sürer)."
      )
    if _bolgeler:
      params["target_areas"] = _bolgeler

    self.mission_ready_to_send.emit(params, [])
    # BUG FIX: _poll_timer bu sınıfta tanımlı değildi → AttributeError kaldırıldı

  @pyqtSlot(dict)
  def update_detection_status(self, resp: dict) -> None:
    """
    BUG FIX: MissionPanelView.update_detection_status bu metodu çağırıyordu
    ama tanımlı değildi → AttributeError crash.
    Air/companion'dan gelen tespit durumu güncellemesini loglar ve sinyal yayar.
    """
    status = resp.get("status", "")
    targets = resp.get("detected_targets", [])
    if targets:
      logger.info("Hedef tespit güncellendi: %s", targets)
      self.target_detected.emit(resp)
    elif status == "ok":
      logger.debug("Companion görev yanıtı: %s", resp)


# ── Sekme 6: Sonsuz () Uçuş — TÜBİTAK Görev 1 ──────────────────────────────
class InfinityMissionTab(_BaseMissionTab):
  """
  İki direk etrafında (yatay 8 / sonsuz) figürü uçuşu.
  TÜBİTAK Uluslararası İHA Yarışması Döner Kanat Kategorisi Görev 1.
  """

  # Harita tıklamasının hangi noktayı ayarlayacağı
  _SEL_TAKEOFF = "Kalkış Noktası"
  _SEL_POLE1  = "Direk 1"
  _SEL_POLE2  = "Direk 2"
  _SEL_FINISH_A = "Pist Çizgisi Ucu A"
  _SEL_FINISH_B = "Pist Çizgisi Ucu B"
  _SEL_WP   = "Normal Waypoint"

  def _build_specific_controls(self):
    grp = QGroupBox("Sonsuz Uçuş Görevi (TÜBİTAK Görev 1)")
    self._inf_grp = grp
    lay = QVBoxLayout(grp)
    # Koordinatlar alt alta sarınca satır sayısı arttı; bölüm tek ekranda
    # kalsın diye satır arası kompaktlaştırıldı (spec §2: dense 4-6 px).
    lay.setSpacing(4)

    # Harita tıklama hedefi
    sel_row = QHBoxLayout()
    _lbl_map = QLabel("Harita tıklaması:"); _lbl_map.setStyleSheet(_SL)
    sel_row.addWidget(_lbl_map)
    self._sel_target = QComboBox()
    self._sel_target.addItems([
      self._SEL_TAKEOFF, self._SEL_POLE1, self._SEL_POLE2,
      self._SEL_FINISH_A, self._SEL_FINISH_B, self._SEL_WP
    ])
    guard_wheel(self._sel_target)  # kaydırma tıklama hedefini değiştirmesin
    sel_row.addWidget(self._sel_target, 1)
    lay.addLayout(sel_row)

    def _mk_coord_row(icon_label: str):
      # NEDEN ÇİFT SÜTUN YOK: etiket + iki koordinat girdisi yan yana
      # (70 + 160 + 160 + boşluklar ≈ 400 px) dar sağ panele sığmıyor ve
      # 1366 px pencerede Boylam alanı kırpılıyordu. Enlem/Boylam artık ALT
      # ALTA; etiket iki satır boyunca solda dikey ortalı durur.
      row = QGridLayout()
      row.setHorizontalSpacing(6)
      row.setVerticalSpacing(4)
      lbl = QLabel(icon_label)
      lbl.setFixedWidth(70)
      lat_e = QLineEdit()
      lat_e.setPlaceholderText("Enlem (ör. 39.9250)")
      lat_e.setMaximumWidth(220)
      lon_e = QLineEdit()
      lon_e.setPlaceholderText("Boylam (ör. 32.8662)")
      lon_e.setMaximumWidth(220)
      # NEDEN: 7 ondalıklı koordinat (39.9250140) dar alanda kırpılıyordu;
      # min. genişlik + tooltip'te tam değer ile operatör gözle doğrulayabilir.
      wire_coord_edit(lat_e, f"{icon_label} enlem — ondalıklı derece")
      wire_coord_edit(lon_e, f"{icon_label} boylam — ondalıklı derece")
      row.addWidget(lbl, 0, 0, 2, 1, Qt.AlignmentFlag.AlignVCenter)
      row.addWidget(lat_e, 0, 1)
      row.addWidget(lon_e, 1, 1)
      row.setColumnStretch(1, 1)
      return row, lat_e, lon_e

    r0, self._tkf_lat, self._tkf_lon = _mk_coord_row("Kalkış:")
    r1, self._p1_lat, self._p1_lon = _mk_coord_row("Direk 1:")
    r2, self._p2_lat, self._p2_lon = _mk_coord_row("Direk 2:")
    r3, self._fa_lat, self._fa_lon = _mk_coord_row("Pist Ucu A:")
    r4, self._fb_lat, self._fb_lon = _mk_coord_row("Pist Ucu B:")
    lay.addLayout(r0)
    lay.addLayout(r1)
    lay.addLayout(r2)
    lay.addLayout(r3)
    lay.addLayout(r4)
    # NEDEN (DEVIR §0o): şartname, 2 tur tamamlandıktan sonra "direk 2'nin
    # arka tarafından geçerek dışından dönüş" yapılıp "pist başlangıç/bitiş
    # çizgisinden geçerek" görevin sonlandırılmasını İSTER — bu çizgi kalkış
    # noktasından AYRI bir kavramdır (bir NOKTA değil, aracın fiilen İÇİNDEN
    # GEÇMESİ gereken bir HAT). İki uç nokta olarak tanımlanır; İHA bu hattı
    # geçtiğini konum-tarafı testiyle doğrular. İsteğe bağlı bırakıldı
    # (boşsa İHA eski davranışa — RTL/LOITER — döner) ki hazır olmayan
    # takımlar/sahalar Görev 1'i eskisi gibi uçurabilsin.
    _fin_note = QLabel(
      "Pist çizgisi (isteğe bağlı): 2 tur bittikten sonra araç, direk 2'yi "
      "son kez dışından dolanıp bu hattı geçerek kalkış noktasına iner "
      "(kalkış = iniş noktası). Boş bırakılırsa eski davranış (doğrudan "
      "RTL/LOITER) kullanılır.")
    _fin_note.setStyleSheet(_SL)
    _fin_note.setWordWrap(True)
    lay.addWidget(_fin_note)

    # Uçuş parametreleri
    form = QFormLayout()
    form.setSpacing(6)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

    self._inf_algo = QComboBox()
    # Yuvarlak Sekiz varsayılan (ilk sıra): sabit yarıçaplı iki döngü — dönüş
    # açısı HER noktada aynı, en akıcı/yumuşak seçenek, sert dönüş yapmaz.
    # Lemniscate: matematiksel olarak tam ∞ eğrisi ama apekste daha keskin.
    #
    # ÇAKIŞMA UYARISI (2026-08-25 incelemesi, öğe METNİ KASITLI DEĞİŞTİRİLMEDİ
    # — kayıtlı profiller bu metne göre eşleşiyor, bkz. _apply_params):
    # companion (missions/infinity_flight.py) CANLI GUIDED akışında SADECE
    # Lemniscate (Gerono ∞) matematiğini uygular; "Yuvarlak Sekiz/DUBINS"
    # gönderilirse companion bunu SESSİZCE Lemniscate'e çevirir (GCS'e uyarı
    # bildirimiyle). Bu seçim GERÇEKTE yalnızca (a) aşağıdaki rota önizlemesi
    # ve (b) "İndir" (QGC WPL) dosyası için anlamlıdır — Mission Planner'da
    # AYRI/manuel bir uçuş için. Canlı companion uçuşunda ikisi de AYNI sonucu
    # (Lemniscate) verir; "Önerilen" etiketi CANLI UÇUŞ için değil, QGC
    # dosyası/önizleme akıcılığı içindir.
    self._inf_algo.addItems([
        "Yuvarlak Sekiz (Akıcı — Önerilen)",
        "Lemniscate (Tam ∞ Şekli — Daha Keskin Apeks)"
    ])
    self._inf_algo.setToolTip(
        "Yalnızca rota ÖNİZLEMESİNİ ve 'İndir' (QGC WPL) dosyasını etkiler.\n"
        "CANLI companion uçuşu HER İKİ seçimde de Lemniscate (Gerono ∞)\n"
        "matematiğini kullanır — 'Yuvarlak Sekiz' gönderilirse companion\n"
        "bunu sessizce Lemniscate'e çevirir ve GCS'e uyarı bildirir."
    )
    _lbl_algo = QLabel("Algoritma:"); _lbl_algo.setStyleSheet(_SL)
    form.addRow(_lbl_algo, self._inf_algo)
    _algo_note = QLabel(
        "Not: bu seçim yalnızca önizleme/QGC dosyasını etkiler — canlı uçuşta "
        "companion her zaman Lemniscate (Gerono ∞) uçar.")
    _algo_note.setStyleSheet(_SL)
    _algo_note.setWordWrap(True)
    form.addRow(_algo_note)

    self._inf_alt = QDoubleSpinBox()
    self._inf_alt.setRange(5, 500)
    self._inf_alt.setValue(30)
    self._inf_alt.setSuffix("m")
    self._inf_alt.setMaximumWidth(140)
    _lbl_alt = QLabel("Uçuş İrtifası:"); _lbl_alt.setStyleSheet(_SL)
    form.addRow(_lbl_alt, self._inf_alt)

    self._inf_laps = QSpinBox()
    self._inf_laps.setRange(1, 5)
    self._inf_laps.setValue(2)
    self._inf_laps.setMaximumWidth(140)
    _lbl_laps = QLabel("Tur Sayısı:"); _lbl_laps.setStyleSheet(_SL)
    form.addRow(_lbl_laps, self._inf_laps)

    self._inf_accel = QDoubleSpinBox()
    self._inf_accel.setRange(50, 500)
    self._inf_accel.setValue(250)
    self._inf_accel.setSuffix(" cm/s²")
    self._inf_accel.setMaximumWidth(140)
    _lbl_accel = QLabel("İvmelenme (WPNAV_ACCEL):"); _lbl_accel.setStyleSheet(_SL)
    form.addRow(_lbl_accel, self._inf_accel)

    # ── İNCE-AYAR PARAMETRELERİ (2026-08-25 — DEVIR, Görev 1 parametre
    # süpürmesi) ──────────────────────────────────────────────────────────
    # NEDEN eklendi: companion (missions/infinity_flight.py) bu 4 alanı
    # zaten JSON parametresi olarak KABUL EDİYORDU ama YKİ'de HİÇ arayüz
    # yoktu — operatör yalnızca companion'ın kendi sabit varsayılanlarına
    # güveniyordu, sahada ayarlayamıyordu. Varsayılanlar companion'ın KENDİ
    # varsayılanlarıyla AYNI (0 = companion kendi hesabını kullanır) —
    # gönderilmeyen/0 alan companion davranışını DEĞİŞTİRMEZ.
    self._inf_bank = QDoubleSpinBox()
    self._inf_bank.setRange(5.0, 35.0)
    self._inf_bank.setValue(10.0)
    self._inf_bank.setSuffix("°")
    self._inf_bank.setMaximumWidth(140)
    self._inf_bank.setToolTip(
        "Viraj tasarım banka açısı — hedef tur hızını belirler (v=√(g·R·tanβ)).\n"
        "Gerçek uçuşta ÖLÇÜLEN güvenli tavan: 21.8°. Bu açının ÜSTÜNE çıkmak\n"
        "gerçek aracın hiç test edilmediği bir bölgedir — yalnızca sim'de\n"
        "doğrulanmış bir değer girin. Companion varsayılanı: 10°.")
    _lbl_bank = QLabel("Tasarım Banka Açısı:"); _lbl_bank.setStyleSheet(_SL)
    form.addRow(_lbl_bank, self._inf_bank)

    self._inf_radius = QDoubleSpinBox()
    self._inf_radius.setRange(0.0, 60.0)
    self._inf_radius.setValue(0.0)
    self._inf_radius.setSuffix(" m (0=otomatik)")
    self._inf_radius.setMaximumWidth(160)
    self._inf_radius.setToolTip(
        "Viraj yarıçapı — 0 bırakılırsa companion direk mesafesinden otomatik\n"
        "türetir (D/2.75, 8-60 m arası kırpılır). Şartname direk mesafesi\n"
        "(~150 m) için bu otomatik değer HENÜZ sim'de doğrulanmadı — burayı\n"
        "yalnızca kendi ölçtüğünüz bir değerle doldurun.")
    _lbl_radius = QLabel("Viraj Yarıçapı:"); _lbl_radius.setStyleSheet(_SL)
    form.addRow(_lbl_radius, self._inf_radius)

    self._inf_lookahead = QDoubleSpinBox()
    self._inf_lookahead.setRange(0.3, 3.0)
    self._inf_lookahead.setValue(1.0)
    self._inf_lookahead.setSuffix(" m")
    self._inf_lookahead.setMaximumWidth(140)
    self._inf_lookahead.setToolTip(
        "Pure-pursuit 'havuç' bakış mesafesi — büyütmek kalıcı bir önde-hedef\n"
        "pozisyon hatası yaratıp hızı tasarım bandının üstüne çıkarabilir\n"
        "(ölçüldü). Companion varsayılanı: 1.0 m.")
    _lbl_lookahead = QLabel("Bakış Mesafesi (Lookahead):"); _lbl_lookahead.setStyleSheet(_SL)
    form.addRow(_lbl_lookahead, self._inf_lookahead)

    self._inf_cross_mult = QDoubleSpinBox()
    self._inf_cross_mult.setRange(1.0, 2.0)
    self._inf_cross_mult.setValue(1.35)
    self._inf_cross_mult.setSingleStep(0.05)
    self._inf_cross_mult.setMaximumWidth(140)
    self._inf_cross_mult.setToolTip(
        "Merkez geçişi/viraj hız oranı — merkezde viraj hızının kaç katı\n"
        "hızlanılacağı. Companion varsayılanı: 1.35 (D=150 m'de 45 koşuluk\n"
        "süpürmeyle en hızlı VE güvenli seçenek olarak doğrulandı).")
    _lbl_cross = QLabel("Merkez Geçiş Hız Oranı:"); _lbl_cross.setStyleSheet(_SL)
    form.addRow(_lbl_cross, self._inf_cross_mult)

    # tekerlek koruması: paneli kaydırırken sayısal alanlar bozulmasın
    guard_wheel(self._inf_algo, self._inf_alt, self._inf_laps, self._inf_accel,
                self._inf_bank, self._inf_radius, self._inf_lookahead, self._inf_cross_mult)

    lay.addLayout(form)

    # ── ROTA ÖNİZLEME ───────────────────────────────────────────────────
    # NEDEN: rota bugüne kadar YALNIZCA dosyaya yazılıyordu (_on_download_wp)
    # ve operatör aracın nereden geçeceğini ancak Mission Planner'da açınca
    # görüyordu. Direk koordinatları haritadan tıklanarak seçildiği için,
    # yanlış tıklanmış bir direk ilk uçuşa kadar fark edilmiyordu.
    # Önizleme, indirilen dosyayla AYNI fonksiyondan (build_infinity_route)
    # üretilir — ayrı bir "yaklaşık çizim" değildir, uçuşta gidilecek
    # noktaların kendisidir.
    self._inf_prev_lbl = QLabel("Rota: direk koordinatları bekleniyor.")
    self._inf_prev_lbl.setStyleSheet(_SL)
    self._inf_prev_lbl.setWordWrap(True)
    lay.addWidget(self._inf_prev_lbl)

    self._btn_prev = QPushButton("🗺 Rotayı Haritada Göster")
    self._btn_prev.clicked.connect(lambda: self._refresh_inf_preview(fit=True))
    lay.addWidget(self._btn_prev)

    # WP'leri bilgisayara indir (Mission Planner'da test için)
    self._btn_dl = QPushButton("📥 WP'leri İndir (.waypoints — Mission Planner)")
    # ghostButton: sayfada EN FAZLA 1 primaryButton olur (spec §1) — ana eylem
    # "Görevi Gönder"; indirme ikincil vurgulu eylemdir.
    self._btn_dl.setObjectName("ghostButton")
    self._btn_dl.clicked.connect(self._on_download_wp)
    lay.addWidget(self._btn_dl)

    # Görev tipini belirleyen ÜST DÜZEY bölüm açık başlar (diğerleri kapalı).
    self._inf_sec = wrap_group(grp, expanded=True)
    self._right_lay.insertWidget(0, self._inf_sec)

  def _apply_tab_constraints(self):
    """
    Önizleme sinyallerini BURADA bağlar — _build_specific_controls'te DEĞİL.

    NEDEN: taban sınıf sırayla _build_specific_controls → _build_speed_group
    → ... → _apply_tab_constraints çağırıyor. Hız grubundaki _sp_crz ve
    _rth_cb, bu sekmeye özel kontroller kurulurken HENÜZ YOK; orada bağlamak
    AttributeError veriyordu. _apply_tab_constraints, adı üstünde, her şey
    kurulduktan sonra çalışan kancadır.

    fit=False: harita kaydırılmaz — operatör elle yakınlaştırmışken her tuş
    vuruşunda kadrajın sıçraması düzeltmeyi imkânsız kılardı. Kadraja sığdırma
    yalnızca "Rotayı Haritada Göster" düğmesiyle olur.
    """
    super()._apply_tab_constraints()
    for _e in (self._tkf_lat, self._tkf_lon, self._p1_lat, self._p1_lon,
               self._p2_lat, self._p2_lon, self._fa_lat, self._fa_lon,
               self._fb_lat, self._fb_lon):
      _e.textChanged.connect(lambda *_: self._refresh_inf_preview())
    self._inf_algo.currentIndexChanged.connect(lambda *_: self._refresh_inf_preview())
    for _s in (self._inf_alt, self._inf_laps, self._sp_crz,
               self._inf_bank, self._inf_radius, self._inf_cross_mult):
      _s.valueChanged.connect(lambda *_: self._refresh_inf_preview())
    self._rth_cb.toggled.connect(lambda *_: self._refresh_inf_preview())
    self._refresh_inf_preview()

  # ── Görev 1 rota önizlemesi ──────────────────────────────────────────────
  def _inf_route_pts(self):
    """
    Uçulacak noktaların TAM listesi → ([(lat,lon)...], bilgi_sözlüğü).

    İndirilen .waypoints ile aynı kaynaktan üretilir; ayrıştıkları gün
    önizleme yalan söylemeye başlar. Koordinat eksik/bozuksa (None, mesaj).
    """
    try:
      p1 = {"lat": float(self._p1_lat.text()), "lon": float(self._p1_lon.text())}
      p2 = {"lat": float(self._p2_lat.text()), "lon": float(self._p2_lon.text())}
    except ValueError:
      return None, "Rota: Direk 1 ve Direk 2 koordinatlarını girin."

    dist = _haversine(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
    if dist < 5.0:
      return None, f"Rota: direkler çok yakın ({dist:.1f} m) — en az 5 m olmalı."

    # Kalkış noktası: girilmediyse direklerin ortası (indirme yolu ile aynı kural)
    try:
      tkf = (float(self._tkf_lat.text()), float(self._tkf_lon.text()))
    except ValueError:
      tkf = ((p1["lat"] + p2["lat"]) / 2.0, (p1["lon"] + p2["lon"]) / 2.0)

    algo = "LEMNISCATE" if "Lemniscate" in self._inf_algo.currentText() else "DUBINS"
    try:
      route, loop_v, cross_v, radius = build_infinity_route(
        p1, p2, float(self._sp_crz.value()), int(self._inf_laps.value()), algo,
        turn_radius_override=float(self._inf_radius.value()),
        cross_speed_mult=float(self._inf_cross_mult.value()),
        bank_design_deg=float(self._inf_bank.value()))
    except Exception as exc:
      return None, f"Rota üretilemedi: {exc}"

    pts = [(it["lat"], it["lon"]) for it in route if it.get("cmd") == 82]
    if len(pts) < 2:
      return None, "Rota: geçerli nokta üretilemedi."

    # Gerçek uçuş yolu kalkıştan başlar ve RTH açıksa oraya döner —
    # önizlemenin bunu göstermesi gerekir, yoksa operatör kalkış ile ∞
    # arasındaki geçiş bacağını hiç görmez.
    full = [tkf] + pts
    if self._rth_cb.isChecked():
      full.append(tkf)

    # Yol uzunluğu ve kaba süre (döngü hızıyla; DUBINS'te düz geçiş daha hızlı
    # olduğu için bu tahmin ÜST sınırdır, uçuş biraz daha kısa sürer).
    length = sum(_haversine(full[i][0], full[i][1], full[i + 1][0], full[i + 1][1])
                 for i in range(len(full) - 1))
    return full, {
      "n": len(pts), "length_m": length, "radius": radius,
      "loop_v": loop_v, "cross_v": cross_v, "dist": dist, "algo": algo,
      "eta_s": length / max(0.5, loop_v),
    }

  def _refresh_inf_preview(self, fit: bool = False):
    """Rota önizlemesini haritaya çizer ve özet etiketini günceller."""
    if not hasattr(self, "_inf_prev_lbl"):
      return
    pts, info = self._inf_route_pts()
    if pts is None:
      self._inf_prev_lbl.setText(str(info))
      try:
        self._map.clear_route()
      except Exception:
        pass
      return
    try:
      self._map.draw_route(pts, _MAP_ROUTE_COLOR, fit=fit)
    except Exception:
      pass
    _eta = info["eta_s"]
    self._inf_prev_lbl.setText(
      f"Rota: {info['n']} nokta, {info['length_m']:.0f} m yol, "
      f"~{int(_eta // 60)} dk {int(_eta % 60)} s (üst sınır).  "
      f"Direk arası {info['dist']:.0f} m → dönüş yarıçapı {info['radius']:.1f} m "
      f"(D/R={info['dist'] / max(0.1, info['radius']):.2f}).  "
      f"Döngü hızı {info['loop_v']:.1f} m/s"
      + (f", düz geçiş {info['cross_v']:.1f} m/s" if info["algo"] == "DUBINS"
         and info["cross_v"] > info["loop_v"] + 0.05 else "")
      + f".  İrtifa {self._inf_alt.value():.0f} m.")

  # ── WP'leri QGC .waypoints olarak indir ───────────────────────────────────
  def _on_download_wp(self):
    """Üretilen ∞ rotasını (uçuşta gidenle birebir aynı) dosyaya kaydeder."""
    try:
      p1 = {"lat": float(self._p1_lat.text()), "lon": float(self._p1_lon.text())}
      p2 = {"lat": float(self._p2_lat.text()), "lon": float(self._p2_lon.text())}
    except ValueError:
      QMessageBox.warning(
        self, "Eksik Koordinat",
        "Direk 1 ve Direk 2 koordinatları eksiksiz girilmelidir.\n"
        "Haritada 'Direk 1' / 'Direk 2' seçerek noktalara tıklayın.")
      return

    dist = _haversine(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
    if dist < 5.0:
      QMessageBox.warning(
        self, "Direkler Çok Yakın",
        f"Direkler arası mesafe {dist:.1f} m — en az 5 m olmalı.")
      return

    # Kalkış noktası: girildiyse onu, yoksa direklerin ortasını kullan.
    try:
      tkf_lat = float(self._tkf_lat.text()); tkf_lon = float(self._tkf_lon.text())
    except ValueError:
      tkf_lat = (p1["lat"] + p2["lat"]) / 2.0
      tkf_lon = (p1["lon"] + p2["lon"]) / 2.0

    alt_m    = float(self._inf_alt.value())
    num_laps = int(self._inf_laps.value())
    cruise   = float(self._sp_crz.value())
    algo     = "LEMNISCATE" if "Lemniscate" in self._inf_algo.currentText() else "DUBINS"
    rth      = bool(self._rth_cb.isChecked())

    try:
      route, loop_speed, cross_speed, turn_radius = build_infinity_route(
        p1, p2, cruise, num_laps, algo,
        turn_radius_override=float(self._inf_radius.value()),
        cross_speed_mult=float(self._inf_cross_mult.value()),
        bank_design_deg=float(self._inf_bank.value()))
      text = infinity_to_qgc_wpl(tkf_lat, tkf_lon, route, alt_m, rth)
    except Exception as exc:
      QMessageBox.critical(self, "Üretim Hatası", f"Rota üretilemedi:\n{exc}")
      return

    path, _ = QFileDialog.getSaveFileName(
      self, "WP'leri Kaydet", "gorev1_sonsuz.waypoints",
      "Waypoints (*.waypoints);;Tüm Dosyalar (*)")
    if not path:
      return
    try:
      with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    except OSError as exc:
      QMessageBox.critical(self, "Kayıt Hatası", f"Dosya yazılamadı:\n{exc}")
      return

    nav_count = sum(1 for it in route if it["cmd"] == 82)
    spd_count = sum(1 for it in route if it["cmd"] == 178)
    if algo == "DUBINS" and spd_count > 0:
      hiz_notu = (f"Döngü (viraj) hızı: {loop_speed:.1f} m/s  |  Düz geçiş hızı: "
                 f"{cross_speed:.1f} m/s\n"
                 f"Rotaya {spd_count} adet WP-bazlı hız komutu (DO_CHANGE_SPEED) "
                 f"gömüldü — dönüşten önce otomatik yavaşlar, düz geçişte hızlanır. "
                 f"Bu mission'ı uçuran her GCS (MP dahil) bu komutları uygular.")
    else:
      hiz_notu = f"Tur hızı: {loop_speed:.1f} m/s (bu mesafe için 20° bank veren ideal hız)."
    QMessageBox.information(
      self, "İndirildi",
      f"∞ rota kaydedildi:\n{path}\n\n"
      f"Algoritma: {algo}\nDirek mesafesi: {dist:.1f} m  →  Dönüş yarıçapı: {turn_radius:.1f} m "
      f"(D/R={dist/turn_radius:.2f})\n"
      f"∞ nokta sayısı: {nav_count}  ({num_laps} tur)\n"
      f"İrtifa: {alt_m:.0f} m\n{hiz_notu}\n\n"
      "Mission Planner → Flight Plan → Load WP File ile açıp test edebilirsiniz.")

  # ── Harita tıklama — hedef seçime göre yönlendir ──────────────────────────
  @pyqtSlot(float, float)
  def _add_wp(self, lat: float, lon: float):
    t = self._sel_target.currentText()
    if t == self._SEL_TAKEOFF:
      self._tkf_lat.setText(f"{lat:.7f}")
      self._tkf_lon.setText(f"{lon:.7f}")
      self._map.set_home(lat, lon)
    elif t == self._SEL_POLE1:
      self._p1_lat.setText(f"{lat:.7f}")
      self._p1_lon.setText(f"{lon:.7f}")
      # Clear previous pole 1 if exists, then add
      self._wps = [w for w in self._wps if w["type"] != "DIREK 1"]
      self._wps.append({"lat": lat, "lon": lon, "alt": 0, "type": "DIREK 1", "idx": len(self._wps)})
      self._refresh_map()
      self._refresh_table()
    elif t == self._SEL_POLE2:
      self._p2_lat.setText(f"{lat:.7f}")
      self._p2_lon.setText(f"{lon:.7f}")
      # Clear previous pole 2 if exists, then add
      self._wps = [w for w in self._wps if w["type"] != "DIREK 2"]
      self._wps.append({"lat": lat, "lon": lon, "alt": 0, "type": "DIREK 2", "idx": len(self._wps)})
      self._refresh_map()
      self._refresh_table()
    elif t == self._SEL_FINISH_A:
      self._fa_lat.setText(f"{lat:.7f}")
      self._fa_lon.setText(f"{lon:.7f}")
      self._wps = [w for w in self._wps if w["type"] != "PIST UCU A"]
      self._wps.append({"lat": lat, "lon": lon, "alt": 0, "type": "PIST UCU A", "idx": len(self._wps)})
      self._refresh_map()
      self._refresh_table()
    elif t == self._SEL_FINISH_B:
      self._fb_lat.setText(f"{lat:.7f}")
      self._fb_lon.setText(f"{lon:.7f}")
      self._wps = [w for w in self._wps if w["type"] != "PIST UCU B"]
      self._wps.append({"lat": lat, "lon": lon, "alt": 0, "type": "PIST UCU B", "idx": len(self._wps)})
      self._refresh_map()
      self._refresh_table()
    else:
      super()._add_wp(lat, lon)

  def _on_send(self):
    # BUG FIX: Önceden ValueError olunca p1/p2={} (boş dict) gönderiliyordu.
    # Şimdi koordinat eksikse kullanıcıya uyarı gösterilir, görev gönderilmez.
    try:
      p1_lat = float(self._p1_lat.text())
      p1_lon = float(self._p1_lon.text())
      p2_lat = float(self._p2_lat.text())
      p2_lon = float(self._p2_lon.text())
    except ValueError:
      QMessageBox.warning(
        self, "Eksik Koordinat",
        "Direk 1 ve Direk 2 koordinatları eksiksiz girilmelidir.\n"
        "Haritada 'Direk 1' / 'Direk 2' seçerek noktalara tıklayın\n"
        "veya koordinatları manuel olarak girin."
      )
      return

    # Direk mesafesi sıfıra yakın uyarısı
    dist = _haversine(p1_lat, p1_lon, p2_lat, p2_lon)
    if dist < 5.0:
      QMessageBox.warning(
        self, "Direkler Çok Yakın",
        f"Direk 1 ve Direk 2 arasındaki mesafe {dist:.1f} m — en az 5 m olmalı.\n"
        "Lütfen direkleri tekrar konumlandırın."
      )
      return

    p1 = {"lat": p1_lat, "lon": p1_lon}
    p2 = {"lat": p2_lat, "lon": p2_lon}

    params = self.get_mission_params()
    
    # Enum-like parsing
    algo_text = self._inf_algo.currentText()
    algo_key = "LEMNISCATE" if "Lemniscate" in algo_text else "DUBINS"

    params.update({
      "mission_type":      "INFINITY_FLIGHT",
      "pole1":             p1,
      "pole2":             p2,
      "num_laps":          self._inf_laps.value(),
      "flight_altitude_m": self._inf_alt.value(),
      "algorithm":         algo_key,
      "wpnav_accel":       self._inf_accel.value(),
      # 2026-08-25 DEVIR: companion bu 3 alanı zaten kabul ediyordu, YKİ
      # şimdiye kadar hiç göndermiyordu — companion kendi sabit
      # varsayılanlarını (10°/1.0m/1.15) kullanıyordu. Artık operatör
      # ayarlayabiliyor; alanların KENDİSİ companion varsayılanlarıyla
      # AYNI değerde başlar, göndermek davranışı DEĞİŞTİRMEZ.
      "bank_design_deg":          self._inf_bank.value(),
      "lookahead_m":              self._inf_lookahead.value(),
      "infinity_cross_speed_mult": self._inf_cross_mult.value(),
    })
    # turn_radius_m: 0 = "otomatik" (companion D/2.75'ten türetir) —
    # yalnızca operatör AÇIKÇA bir değer girdiyse gönderilir (finish_a/b ile
    # AYNI "isteğe bağlı alan" deseni).
    if self._inf_radius.value() > 0.0:
      params["turn_radius_m"] = self._inf_radius.value()
    # Pist başlangıç/bitiş çizgisi — İSTEĞE BAĞLI (DEVIR §0o). İki uç da
    # girilmemişse alan hiç gönderilmez; İHA tarafı bunu "eski davranış"
    # (doğrudan RTL/LOITER, ek yarım tur/hat-geçişi YOK) olarak yorumlar —
    # geriye dönük uyumluluk, hazır olmayan sahalar için görevi bozmaz.
    try:
      fa = {"lat": float(self._fa_lat.text()), "lon": float(self._fa_lon.text())}
      fb = {"lat": float(self._fb_lat.text()), "lon": float(self._fb_lon.text())}
      params["finish_a"] = fa
      params["finish_b"] = fb
    except ValueError:
      pass
    # BUG FIX: self._wps yalnızca harita işaretçilerini (DIREK 1/2) içerir —
    # bunlar uçuş rotası WP'si değildir. Companion, pole1/pole2'den ∞ rotayı üretir.
    params["waypoints"] = []
    self.mission_ready_to_send.emit(params, [])


# ── Sonsuz () Rota Üretici — modül seviyesi yardımcı ───────────────────────
class MissionPanelView(QWidget):
  """Birleşik görev paneli — 5 görev tipi sekmesi içerir."""

  mission_ready_to_send = pyqtSignal(dict, list)
  send_command     = pyqtSignal(int, list)
  # NEDEN (ölü kontrol): TargetDropMissionTab.target_detected emit ediliyordu
  # ama sekme dışına çıkmıyordu → hiçbir dinleyicisi yoktu. Dış kapsayıcıdan
  # yeniden yayınlanıyor ki main_window bağlayabilsin (bkz. rapor).
  target_detected  = pyqtSignal(dict)

  def __init__(self, parent=None):
    super().__init__(parent)
    lay = QVBoxLayout(self)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(8)

    self._tabs = QTabWidget()
    self._tabs.setFont(QFont("Segoe UI", 11))

    self._target_drop_tab = TargetDropMissionTab()
    self._infinity_tab  = InfinityMissionTab()

    self._tabs.addTab(self._target_drop_tab, " Görev 2 — Hedef")
    self._tabs.addTab(self._infinity_tab,  " Görev 1 — Sonsuz")

    lay.addWidget(self._tabs)

    _all_tabs = (self._target_drop_tab, self._infinity_tab)

    for tab in _all_tabs:
      tab.mission_ready_to_send.connect(self.mission_ready_to_send)
      tab.send_command.connect(self.send_command)

    # Hedef tespit bildirimini dışarı taşı (Görev 2 sekmesi → panel → uygulama)
    self._target_drop_tab.target_detected.connect(self.target_detected)

  @pyqtSlot(dict)
  def update_telemetry(self, data: dict):
    for tab in (self._target_drop_tab, self._infinity_tab):
      tab.update_telemetry(data)

  @pyqtSlot(dict)
  def update_detection_status(self, resp: dict):
    """Air'den gelen durum yanıtını Görev 2 sekmesine ilet."""
    self._target_drop_tab.update_detection_status(resp)
