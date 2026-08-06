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
import time
import logging

from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
  QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QPushButton, QLabel,
  QTableWidget, QTableWidgetItem, QGroupBox, QComboBox, QDoubleSpinBox,
  QSpinBox, QHeaderView, QTextEdit, QTabWidget, QCheckBox, QMessageBox,
  QFormLayout, QLineEdit, QScrollArea, QListWidget, QListWidgetItem,
  QAbstractItemView, QRadioButton, QButtonGroup, QFrame, QFileDialog,
)

try:
  from PyQt6.QtWebEngineWidgets import QWebEngineView
  from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile
  _WE = True
except ImportError:
  _WE = False

logger = logging.getLogger(__name__)

# ── Harita HTML ───────────────────────────────────────────────────────────────
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
</style></head><body>
<div id="sb">&#127919; HARITA SEÇIM MODU AKTİF — Noktaya Tıkla</div>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
var map=L.map('map',{zoomControl:true,attributionControl:false,maxZoom:28}).setView([39.925,32.866],15);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
 {maxZoom:28, maxNativeZoom:19}).addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
 {maxZoom:28, maxNativeZoom:19, opacity:0.7}).addTo(map);

var wps=[],mrks=[],xtra=[],pend=[],sel=false;
var rl=L.polyline([],{color:'#f0a500',weight:2,dashArray:'5,4'}).addTo(map);

map.on('click',function(e){if(sel)pend.push({lat:e.latlng.lat,lon:e.latlng.lng});});

function setSel(on){
 sel=on;
 document.getElementById('sb').style.display=on?'block':'none';
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
_SS = ("QGroupBox{border:1px solid #0f3460;font-weight:bold;"
    "padding-top:18px;margin-top:6px;border-radius:4px;}")
_SB = "QPushButton{border-radius:4px;padding:6px 10px;font-weight:bold;}"
_SBR = "QPushButton{border-radius:4px;padding:6px 10px;font-weight:bold;}"
_SBG = ("QPushButton{border-radius:4px;"
    "padding:8px 12px;font-weight:bold;font-size:13px;}"
    "QPushButton:hover{}")
_SBW = "QPushButton{border-radius:4px;padding:6px 10px;font-weight:bold;}"
_SC = "border:1px solid #0f3460;padding:4px;border-radius:3px;"
_SL = "font-size:12px;"

# ── Sabitler ──────────────────────────────────────────────────────────────────
_AI_MODELS = [
  "YOLOv8n — Hızlı (Nano)",
  "YOLOv8s — Dengeli (Small)",
  "YOLOv8m — Orta (Medium)",
  "YOLOv8l — Güçlü (Large)",
  "YOLOv8x — En Güçlü (X-Large)",
  "YOLOv8_person — İnsan Tespiti",
  "YOLOv8_vehicle — Araç Tespiti",
  "RT-DETR — Gerçek Zamanlı",
  "Özel Model... (custom.pt)",
]

_DETECT_TARGETS = [
  "İnsan / Kişi",
  "Zırhlı Araç (Tank / APC)",
  "Hafif Araç (Araba / Kamyon)",
  "İnsansız Hava Aracı",
  "Kuş / Yaban Hayvanı",
  "Silah / Silah Sistemi",
  "Yangın / Duman",
  "Su Baskını / Sel",
  "Gemi / Deniz Aracı",
  "Bina Hasarı",
  "Özel Hedef (Tanımla)",
]

_DETECT_ACTIONS = [
  "Uyarı Ver (Alert Only)",
  "Takip Et (Track Target)",
  "Üzerinde Bekle (Hover)",
  "Eve Dön (RTL)",
  "Yere İn (Land)",
  "Sadece Kaydet (Log Only)",
]


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
      self._timer.start()

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

  def draw_polygon(self, pts: list[tuple[float, float]], color: str = "#bb86fc"):
    js_pts = json.dumps([{"lat": p[0], "lon": p[1]} for p in pts])
    self._js(f"drawPoly({js_pts},'{color}')")

  def draw_circle(self, lat: float, lon: float, r: float, color: str = "#bb86fc"):
    self._js(f"drawCirc({lat},{lon},{r},'{color}')")

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
    self._hud_widget.setMinimumWidth(240)
    self._hud_widget.setMaximumWidth(320)
    self._hud_lay = QVBoxLayout(self._hud_widget)
    self._hud_lay.setContentsMargins(5, 5, 5, 5)
    
    lbl_hud_title = QLabel("<b>GÖREV ÖZETİ</b>")
    self._hud_lay.addWidget(lbl_hud_title)
    
    self._hud_summary = QLabel("Haritaya tıklayarak nokta ekleyin.")
    self._hud_summary.setWordWrap(True)
    self._hud_summary.setStyleSheet("font-size: 13px; color: #bb86fc; padding: 5px; background: #222; border-radius: 4px;")
    self._hud_lay.addWidget(self._hud_summary)
    
    lbl_wp_dists = QLabel("<b>WP Mesafeleri:</b>")
    lbl_wp_dists.setStyleSheet("margin-top: 10px;")
    self._hud_lay.addWidget(lbl_wp_dists)
    
    self._hud_list = QListWidget()
    self._hud_list.setStyleSheet("font-size: 12px; background: #121212; color: #fff; border: 1px solid #333;")
    self._hud_lay.addWidget(self._hud_list)
    
    left_pane = QWidget()
    left_lay = QHBoxLayout(left_pane)
    left_lay.setContentsMargins(0, 0, 0, 0)
    left_lay.setSpacing(0)
    left_lay.addWidget(self._hud_widget)
    left_lay.addWidget(self._map, 1)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.addWidget(left_pane)

    right = QScrollArea()
    right.setWidgetResizable(True)
    right_w = QWidget()
    self._right_lay = QVBoxLayout(right_w)
    self._right_lay.setContentsMargins(6, 6, 6, 6)
    self._right_lay.setSpacing(8)
    right.setWidget(right_w)
    splitter.addWidget(right)
    splitter.setSizes([620, 400])

    main_lay = QVBoxLayout(self)
    main_lay.setContentsMargins(0, 0, 0, 0)
    main_lay.addWidget(splitter)

    self._build_wp_group()
    self._build_specific_controls()
    self._build_speed_group()
    self._build_battery_group()
    self._build_send_button()
    self._right_lay.addStretch(1)

  # ── WP Yönetim Grubu ─────────────────────────────────────────────────────
  def _build_wp_group(self):
    grp = QGroupBox("Tarama Alanı / Waypoint Yönetimi")
    lay = QVBoxLayout(grp)

    btn_row = QHBoxLayout()
    self._sel_btn = QPushButton("Harita Seçim Modu")
    self._sel_btn.setCheckable(True)
    self._sel_btn.toggled.connect(self._toggle_select)

    btn_del = QPushButton("↩ Son WP Sil")
    btn_del.clicked.connect(self._remove_last)

    btn_clr = QPushButton("Tümünü Temizle")
    btn_clr.clicked.connect(self._clear_all)

    btn_row.addWidget(self._sel_btn)
    btn_row.addWidget(btn_del)
    btn_row.addWidget(btn_clr)
    lay.addLayout(btn_row)

    opt_row = QHBoxLayout()
    self._rth_cb = QCheckBox("🏠 Kalkış Noktasına Dön (RTH)")

    self._uni_cb = QCheckBox("📏 Eşit İrtifa:")

    self._alt_spin = QDoubleSpinBox()
    self._alt_spin.setRange(1, 500)
    self._alt_spin.setValue(30)
    self._alt_spin.setSuffix("m")
    self._alt_spin.setFixedWidth(85)

    opt_row.addWidget(self._rth_cb)
    opt_row.addStretch()
    opt_row.addWidget(self._uni_cb)
    opt_row.addWidget(self._alt_spin)
    lay.addLayout(opt_row)

    # WP Tablosu
    self._table = QTableWidget()
    self._table.setColumnCount(5)
    self._table.setHorizontalHeaderLabels(["#", "Tür", "Enlem", "Boylam", "İrtifa(m)"])
    _hh = self._table.horizontalHeader()  # guard: None dönebilir (Pylance)
    if _hh is not None:
        _hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    self._table.setMinimumHeight(140)
    self._table.setMaximumHeight(200)
    self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
    self._table.itemChanged.connect(self._on_item_changed)

    lay.addWidget(self._table)

    # ── Manuel Koordinat Girişi ───────────────────────────────────────
    _input_style = (
      "border:1px solid #0f3460;"
      "padding:4px;border-radius:3px;font-size:12px;"
    )
    man_frame = QFrame()
    man_lay = QVBoxLayout(man_frame)
    man_lay.setContentsMargins(6, 6, 6, 6)
    man_lay.setSpacing(4)

    hdr_row = QHBoxLayout()
    hdr_lbl = QLabel("Manuel Koordinat Ekle")
    hdr_row.addWidget(hdr_lbl)
    hdr_row.addStretch()
    man_lay.addLayout(hdr_row)

    coord_row = QHBoxLayout()
    coord_row.setSpacing(4)

    lbl_lat = QLabel("Enlem:")
    lbl_lat.setFixedWidth(42)
    self._man_lat = QLineEdit()
    self._man_lat.setPlaceholderText("ör. 39.925014")
    self._man_lat.setToolTip("Ondalıklı derece (ör: 39.925014)")

    lbl_lon = QLabel("Boylam:")
    lbl_lon.setFixedWidth(48)
    self._man_lon = QLineEdit()
    self._man_lon.setPlaceholderText("ör. 32.866200")
    self._man_lon.setToolTip("Ondalıklı derece (ör: 32.866200)")

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
    self._man_type.setFixedWidth(90)

    coord_row.addWidget(lbl_lat)
    coord_row.addWidget(self._man_lat, 2)
    coord_row.addWidget(lbl_lon)
    coord_row.addWidget(self._man_lon, 2)
    coord_row.addWidget(lbl_alt_m)
    coord_row.addWidget(self._man_alt)
    coord_row.addWidget(lbl_type_m)
    coord_row.addWidget(self._man_type)
    man_lay.addLayout(coord_row)

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
    self._right_lay.addWidget(grp)

  def _build_specific_controls(self):
    """Subclass'lar buraya özel kontroller ekler."""
    pass

  # ── Hız Kontrolü ─────────────────────────────────────────────────────────
  def _build_speed_group(self):
    grp = QGroupBox("Hız Kontrolü")
    form = QFormLayout(grp)
    form.setSpacing(6)

    def _spn(val: float, mn: float, mx: float) -> QDoubleSpinBox:
      s = QDoubleSpinBox()
      s.setRange(mn, mx)
      s.setValue(val)
      s.setSuffix("m/s")
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
    self._right_lay.addWidget(grp)

  # ── Batarya Tahmini ───────────────────────────────────────────────────────
  def _build_battery_group(self):
    grp = QGroupBox("Batarya Tahmini")
    form = QFormLayout(grp)
    form.setSpacing(6)

    self._bat_cap = QDoubleSpinBox()
    self._bat_cap.setRange(0, 50000)
    self._bat_cap.setValue(5000)
    self._bat_cap.setSuffix("mAh")
    self._bat_cap.valueChanged.connect(self._update_battery)

    self._bat_cur = QDoubleSpinBox()
    self._bat_cur.setRange(1, 200)
    self._bat_cur.setValue(25)
    self._bat_cur.setSuffix("A ort.")
    self._bat_cur.valueChanged.connect(self._update_battery)

    _lbl_cap = QLabel("Batarya Kapasitesi:"); _lbl_cap.setStyleSheet(_SL)
    _lbl_cur = QLabel("Ort. Akım Tüketimi:"); _lbl_cur.setStyleSheet(_SL)
    form.addRow(_lbl_cap, self._bat_cap)
    form.addRow(_lbl_cur, self._bat_cur)

    self._bat_lbl = QLabel("— Rota hesaplanmadı —")
    self._bat_lbl.setWordWrap(True)
    form.addRow(self._bat_lbl)
    self._right_lay.addWidget(grp)

  # ── Gönder Butonu ─────────────────────────────────────────────────────────
  def _build_send_button(self):
    self._send_btn = QPushButton(" Görevi Gönder → Drone")
    self._send_btn.setMinimumHeight(44)
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
      clr = (
        "#4ade80" if wp["type"] == "TAKEOFF"
        else "#ff4444" if wp["type"] == "LAND"
        else "#f0a500"
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
        it.setForeground(QColor(clr if col == 1 else "#ddd"))
        if col != 4:
          it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(i, col, it)
    self._table.blockSignals(False)
    self._refresh_hud()

  def _refresh_hud(self):
    self._hud_list.clear()
    if not self._wps:
        self._hud_summary.setText("Haritaya tıklayarak nokta ekleyin.")
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
      " Seçim Modu AKTİF — Haritaya Tıkla" if on else " Harita Seçim Modu"
    )

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
    if len(self._wps) < 2:
      QMessageBox.warning(
        self, "Görev Gönder",
        "En az 2 waypoint gereklidir (Kalkış + Hedef)."
      )
      return
    params = self.get_mission_params()
    params["mission_type"] = "WAYPOINT"
    self.mission_ready_to_send.emit(params, [])

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

  def _build_specific_controls(self):
    self._pole1: dict | None = None
    self._pole2: dict | None = None

    def _dsb(lo, hi, val, step, suffix, dec=2):
      w = QDoubleSpinBox(); w.setRange(lo, hi); w.setValue(val)
      w.setSingleStep(step); w.setDecimals(dec); w.setSuffix(suffix)
      return w

    def _isb(lo, hi, val, suffix=""):
      w = QSpinBox(); w.setRange(lo, hi); w.setValue(val)
      if suffix: w.setSuffix(suffix)
      return w

    grp = QGroupBox("Hedef Tespit + Yük Bırakma (TÜBİTAK Görev 2/3)")
    lay = QVBoxLayout(grp); lay.setSpacing(6)

    # ── Görev akışı: harita seçim hedefi + Direk 1/2 ──────────────────────
    sel_row = QHBoxLayout()
    sel_row.addWidget(QLabel("Harita tıklaması:"))
    self._sel_target = QComboBox()
    self._sel_target.addItems([self._SEL_AREA, self._SEL_POLE1, self._SEL_POLE2])
    sel_row.addWidget(self._sel_target, 1)
    lay.addLayout(sel_row)

    self._pole_lbl = QLabel("Direk 1: —    Direk 2: —")
    self._pole_lbl.setStyleSheet("font-size:11px; color:#8b949e; padding:2px;")
    lay.addWidget(self._pole_lbl)

    # ── İrtifa ────────────────────────────────────────────────────────────
    alt_grp = QGroupBox("İrtifa"); alt_f = QFormLayout(alt_grp); alt_f.setSpacing(5)
    self._scan_alt    = _dsb(2, 120, 30.0, 1.0, " m", 0)
    self._drop_alt    = _dsb(1, 15, 2.0, 0.5, " m", 1)
    self._tof_grab_alt = _dsb(0.1, 1.5, 0.3, 0.05, " m", 2)
    alt_f.addRow("Tarama (maks) İrtifası:", self._scan_alt)
    alt_f.addRow("Bırakma İrtifası:", self._drop_alt)
    alt_f.addRow("Yük Alma İrtifası (G3):", self._tof_grab_alt)
    lay.addWidget(alt_grp)

    # ── Kamera / Gimbal ───────────────────────────────────────────────────
    cam_grp = QGroupBox("Kamera / Gimbal"); cam_f = QFormLayout(cam_grp); cam_f.setSpacing(5)
    self._scan_tilt = _isb(30, 90, 50, "°")
    self._cam_hfov  = _dsb(30, 120, 63.0, 1.0, "°", 0)
    self._cam_offset = _dsb(0, 50, 15.0, 1.0, " cm", 0)
    cam_f.addRow("İleri-Bakış Açısı (tilt):", self._scan_tilt)
    cam_f.addRow("Kamera Görüş Açısı (HFOV):", self._cam_hfov)
    cam_f.addRow("Kamera İleri Ofseti:", self._cam_offset)
    lay.addWidget(cam_grp)

    # ── Tarama Davranışı ──────────────────────────────────────────────────
    sc_grp = QGroupBox("Tarama Davranışı"); sc_f = QFormLayout(sc_grp); sc_f.setSpacing(5)
    self._scan_speed    = _dsb(1, 20, 6.0, 0.5, " m/s", 1)
    self._drop_radius   = _dsb(1, 15, 4.0, 0.5, " m", 1)
    self._scan_wp_radius = _dsb(1, 10, 3.0, 0.5, " m", 1)
    self._scan_timeout  = _isb(30, 600, 180, " s")
    sc_f.addRow("Tarama Hızı:", self._scan_speed)
    sc_f.addRow("Bırakma Yarıçapı:", self._drop_radius)
    sc_f.addRow("WP Kabul Yarıçapı:", self._scan_wp_radius)
    sc_f.addRow("Tarama Zaman Aşımı:", self._scan_timeout)
    lay.addWidget(sc_grp)

    # ── Hedef Tanımı (alan-bazlı sınıflandırma) ───────────────────────────
    tg_grp = QGroupBox("Hedef Tanımı (gerçek alan)"); tg_f = QFormLayout(tg_grp); tg_f.setSpacing(5)
    self._red_area  = _dsb(0.05, 5.0, 0.43, 0.01, " m²", 2)
    self._blue_area = _dsb(1.0, 30.0, 10.39, 0.1, " m²", 2)
    tg_f.addRow("Kırmızı Üçgen Alanı:", self._red_area)
    tg_f.addRow("Mavi Altıgen Alanı:", self._blue_area)
    lay.addWidget(tg_grp)

    # ── Donanım / Servo Kanalları ─────────────────────────────────────────
    pin_grp = QGroupBox("Donanım / Servo Kanalları")
    pin_form = QFormLayout(pin_grp); pin_form.setSpacing(4)
    # Varsayılanlar config.json "servos" ile hizalı (gerçek kablolama):
    # gimbal=1, red_drop=2, blue_drop=3, grabber_left=4, grabber_right=5
    self._pin_drop1 = _isb(1, 16, 2)
    self._pin_drop2 = _isb(1, 16, 3)
    self._pin_grabL = _isb(1, 16, 4)
    self._pin_grabR = _isb(1, 16, 5)
    self._pin_gimbal = _isb(1, 16, 1)
    pin_form.addRow("Kırmızı Yük (red_drop):", self._pin_drop1)
    pin_form.addRow("Mavi Yük (blue_drop):", self._pin_drop2)
    pin_form.addRow("Sol Kapak (grab_L):", self._pin_grabL)
    pin_form.addRow("Sağ Kapak (grab_R):", self._pin_grabR)
    pin_form.addRow("Gimbal (pitch):", self._pin_gimbal)
    lay.addWidget(pin_grp)

    # ── Mod ───────────────────────────────────────────────────────────────
    self._auto_cb = QCheckBox("Otonom Bırakma (Tespit → Anında Bırak)")
    self._auto_cb.setChecked(True)
    lay.addWidget(self._auto_cb)
    self._bonus_cb = QCheckBox("Bonus Görev 3: Yükü yerden al → ikinci hedefe taşı")
    lay.addWidget(self._bonus_cb)

    self._right_lay.insertWidget(0, grp)

  def _update_pole_lbl(self):
    p1 = f"{self._pole1['lat']:.5f},{self._pole1['lon']:.5f}" if self._pole1 else "—"
    p2 = f"{self._pole2['lat']:.5f},{self._pole2['lon']:.5f}" if self._pole2 else "—"
    self._pole_lbl.setText(f"Direk 1: {p1}    Direk 2: {p2}")

  @pyqtSlot(float, float)
  def _add_wp(self, lat: float, lon: float):
    """Harita tıklaması: seçime göre Direk 1/2 veya tarama köşesi ekle."""
    t = self._sel_target.currentText()
    if t == self._SEL_POLE1:
      self._pole1 = {"lat": lat, "lon": lon}
      self._map.draw_circle(lat, lon, 3, "#ffd700")
      self._update_pole_lbl()
    elif t == self._SEL_POLE2:
      self._pole2 = {"lat": lat, "lon": lon}
      self._map.draw_circle(lat, lon, 3, "#ff8c00")
      self._update_pole_lbl()
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
    area_wps = [
      {"lat": w["lat"], "lon": w["lon"], "alt": w.get("alt", 30.0),
       "type": "WAYPOINT", "idx": i + 1}
      for i, w in enumerate(self._wps)
    ]
    params["waypoints"] = [takeoff_wp] + area_wps

    params.update({
      "mission_type":        "TEKNOFEST_TASK2",
      # İrtifa
      "scan_altitude_m":     self._scan_alt.value(),
      "drop_altitude_m":     self._drop_alt.value(),
      "tof_grab_alt_m":      self._tof_grab_alt.value(),
      # Kamera / gimbal
      "scan_tilt_deg":       self._scan_tilt.value(),
      "camera_hfov_deg":     self._cam_hfov.value(),
      "cam_offset_cm":       self._cam_offset.value(),
      # Tarama davranışı
      "scan_speed_ms":       self._scan_speed.value(),
      "drop_radius_m":       self._drop_radius.value(),
      "scan_wp_radius_m":    self._scan_wp_radius.value(),
      "scan_timeout_s":      self._scan_timeout.value(),
      # Hedef tanımı
      "red_target_area_m2":  self._red_area.value(),
      "blue_target_area_m2": self._blue_area.value(),
      # Mod
      "autonomous_drop":     self._auto_cb.isChecked(),
      "bonus_mission":       self._bonus_cb.isChecked(),
      # Donanım
      "gpio_pins": {
          "drop1": self._pin_drop1.value(),
          "drop2": self._pin_drop2.value(),
          "grab_l": self._pin_grabL.value(),
          "grab_r": self._pin_grabR.value(),
          "gimbal": self._pin_gimbal.value(),
      },
    })
    # Direkler (GATE_RUN) — seçildiyse gönder
    if self._pole1:
      params["pole1"] = self._pole1
    if self._pole2:
      params["pole2"] = self._pole2

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
  _SEL_WP   = "Normal Waypoint"

  def _build_specific_controls(self):
    grp = QGroupBox("Sonsuz Uçuş Görevi (TÜBİTAK Görev 1)")
    lay = QVBoxLayout(grp)

    # Harita tıklama hedefi
    sel_row = QHBoxLayout()
    _lbl_map = QLabel("Harita tıklaması:"); _lbl_map.setStyleSheet(_SL)
    sel_row.addWidget(_lbl_map)
    self._sel_target = QComboBox()
    self._sel_target.addItems([
      self._SEL_TAKEOFF, self._SEL_POLE1, self._SEL_POLE2, self._SEL_WP
    ])
    sel_row.addWidget(self._sel_target, 1)
    lay.addLayout(sel_row)

    _is = ("border:1px solid #0f3460;"
        "padding:4px;border-radius:3px;font-size:12px;")

    def _mk_coord_row(icon_label: str):
      row = QHBoxLayout()
      lbl = QLabel(icon_label)
      lbl.setFixedWidth(70)
      lat_e = QLineEdit()
      lat_e.setPlaceholderText("Enlem (ör. 39.9250)")
      lon_e = QLineEdit()
      lon_e.setPlaceholderText("Boylam (ör. 32.8662)")
      row.addWidget(lbl)
      row.addWidget(lat_e, 1)
      row.addWidget(lon_e, 1)
      return row, lat_e, lon_e

    r0, self._tkf_lat, self._tkf_lon = _mk_coord_row("Kalkış:")
    r1, self._p1_lat, self._p1_lon = _mk_coord_row("Direk 1:")
    r2, self._p2_lat, self._p2_lon = _mk_coord_row("Direk 2:")
    lay.addLayout(r0)
    lay.addLayout(r1)
    lay.addLayout(r2)

    # Uçuş parametreleri
    form = QFormLayout()
    form.setSpacing(6)

    self._inf_algo = QComboBox()
    # Yuvarlak Sekiz varsayılan (ilk sıra): sabit yarıçaplı iki döngü — dönüş
    # açısı HER noktada aynı, en akıcı/yumuşak seçenek, sert dönüş yapmaz.
    # Lemniscate: matematiksel olarak tam ∞ eğrisi ama apekste daha keskin.
    self._inf_algo.addItems([
        "Yuvarlak Sekiz (Akıcı — Önerilen)",
        "Lemniscate (Tam ∞ Şekli — Daha Keskin Apeks)"
    ])
    _lbl_algo = QLabel("Algoritma:"); _lbl_algo.setStyleSheet(_SL)
    form.addRow(_lbl_algo, self._inf_algo)

    self._inf_alt = QDoubleSpinBox()
    self._inf_alt.setRange(5, 500)
    self._inf_alt.setValue(30)
    self._inf_alt.setSuffix("m")
    _lbl_alt = QLabel("Uçuş İrtifası:"); _lbl_alt.setStyleSheet(_SL)
    form.addRow(_lbl_alt, self._inf_alt)

    self._inf_laps = QSpinBox()
    self._inf_laps.setRange(1, 5)
    self._inf_laps.setValue(2)
    _lbl_laps = QLabel("Tur Sayısı:"); _lbl_laps.setStyleSheet(_SL)
    form.addRow(_lbl_laps, self._inf_laps)

    self._inf_accel = QDoubleSpinBox()
    self._inf_accel.setRange(50, 500)
    self._inf_accel.setValue(250)
    self._inf_accel.setSuffix(" cm/s²")
    _lbl_accel = QLabel("İvmelenme (WPNAV_ACCEL):"); _lbl_accel.setStyleSheet(_SL)
    form.addRow(_lbl_accel, self._inf_accel)

    lay.addLayout(form)

    # WP'leri bilgisayara indir (Mission Planner'da test için)
    self._btn_dl = QPushButton("📥 WP'leri İndir (.waypoints — Mission Planner)")
    self._btn_dl.setStyleSheet(
      "QPushButton{background:#0f3460;color:#e6edf3;padding:7px;border-radius:4px;"
      "font-weight:bold;} QPushButton:hover{background:#16508f;}")
    self._btn_dl.clicked.connect(self._on_download_wp)
    lay.addWidget(self._btn_dl)

    self._right_lay.insertWidget(0, grp)

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
        p1, p2, cruise, num_laps, algo)
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
    })
    # BUG FIX: self._wps yalnızca harita işaretçilerini (DIREK 1/2) içerir —
    # bunlar uçuş rotası WP'si değildir. Companion, pole1/pole2'den ∞ rotayı üretir.
    params["waypoints"] = []
    self.mission_ready_to_send.emit(params, [])


# ── Sonsuz () Rota Üretici — modül seviyesi yardımcı ───────────────────────
class MissionPanelView(QWidget):
  """Birleşik görev paneli — 5 görev tipi sekmesi içerir."""

  mission_ready_to_send = pyqtSignal(dict, list)
  send_command     = pyqtSignal(int, list)

  def __init__(self, parent=None):
    super().__init__(parent)
    lay = QVBoxLayout(self)
    lay.setContentsMargins(0, 0, 0, 0)

    self._tabs = QTabWidget()
    self._tabs.setFont(QFont("Segoe UI", 11))
    self._tabs

    self._target_drop_tab = TargetDropMissionTab()
    self._infinity_tab  = InfinityMissionTab()

    self._tabs.addTab(self._target_drop_tab, " Görev 2 — Hedef")
    self._tabs.addTab(self._infinity_tab,  " Görev 1 — Sonsuz")

    lay.addWidget(self._tabs)

    _all_tabs = (self._target_drop_tab, self._infinity_tab)

    for tab in _all_tabs:
      tab.mission_ready_to_send.connect(self.mission_ready_to_send)
      tab.send_command.connect(self.send_command)

  @pyqtSlot(dict)
  def update_telemetry(self, data: dict):
    for tab in (self._target_drop_tab, self._infinity_tab):
      tab.update_telemetry(data)

  @pyqtSlot(dict)
  def update_detection_status(self, resp: dict):
    """Air'den gelen durum yanıtını Görev 2 sekmesine ilet."""
    self._target_drop_tab.update_detection_status(resp)
