"""
mission_view.py — Modül 6: Normal Görev Planlama
Harita üzerinde tiklayarak waypoint ekle, parametreleri düzenle, görevi drone ya yaz.
AI görev görünümünden bagimsiz, ArduPilot standart MAVLink görev komutlari kullanir.
"""
import json
import logging
import tempfile
from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QTimer, QUrl
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QComboBox, QDoubleSpinBox, QHeaderView, QFileDialog, QMessageBox,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    _WE = True
except ImportError:
    _WE = False

logger = logging.getLogger(__name__)

# ── Görev haritasi HTML ────────────────────────────────────────────────────────
_MISSION_MAP_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
html,body,#map{width:100%;height:100%;margin:0;padding:0;}
.wp-number{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;
           justify-content:center;font-weight:bold;font-size:11px;
           border:2px solid #000;color:#fff;}
</style>
</head><body><div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
var map = L.map('map',{zoomControl:true,attributionControl:false,maxZoom:28}).setView([39.925,32.866],16);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  {maxZoom:28, maxNativeZoom:19, attribution:'Esri'}).addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  {maxZoom:28, maxNativeZoom:19, opacity:0.7}).addTo(map);

var waypoints = [];
var wpMarkers = [];
var routeLine = L.polyline([],{color:'#f0a500',weight:2,dashArray:'6,4'}).addTo(map);
var pending = [];

function makeWpIcon(n){
  return L.divIcon({html:'<div class="wp-number" style="background:#f0a500">'+n+'</div>',
                    iconSize:[26,26],iconAnchor:[13,13],className:''});
}

map.on('click', function(e){
  pending.push({lat:e.latlng.lat, lon:e.latlng.lng});
});

function addWaypointFromPython(lat, lon, alt, idx){
  var marker = L.marker([lat,lon],{icon:makeWpIcon(idx),draggable:true}).addTo(map);
  marker.bindPopup('WP'+idx+'<br>'+lat.toFixed(6)+', '+lon.toFixed(6)+'<br>Alt: '+alt+'m');
  marker.on('dragend', function(e){
    var ll = e.target.getLatLng();
    pending.push({lat:ll.lat, lon:ll.lng, update_idx:idx});
  });
  wpMarkers.push(marker);
  waypoints.push({idx:idx,lat:lat,lon:lon,alt:alt});
  updateRoute();
}

function updateRoute(){
  var pts = waypoints.map(function(w){return [w.lat,w.lon];});
  routeLine.setLatLngs(pts);
}

function clearAllWaypoints(){
  wpMarkers.forEach(function(m){map.removeLayer(m);});
  wpMarkers=[]; waypoints=[]; pending=[];
  routeLine.setLatLngs([]);
}

function removeLastWaypoint(){
  if(wpMarkers.length>0){
    map.removeLayer(wpMarkers.pop());
    waypoints.pop();
    updateRoute();
  }
}

function getPendingJSON(){
  var out = JSON.stringify(pending);
  pending = [];
  return out;
}

function centerMap(lat,lon){
  map.setView([lat,lon],16);
}

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
</script></body></html>"""

# ── Komut tipi listesi (ArduPilot) ────────────────────────────────────────────
_CMD_TYPES = [
    ("WAYPOINT",     16),
    ("TAKEOFF",      22),
    ("LAND",         21),
    ("RTL",          20),
    ("LOITER_UNLIM", 17),
    ("LOITER_TURNS", 18),
    ("LOITER_TIME",  19),
    ("DELAY",        93),
    ("CHANGE_ALT",   186),
]
_CMD_NAMES = {v: k for k, v in _CMD_TYPES}

# Varsayilan yükseklik
_DEFAULT_ALT = 30.0


class MissionPlanView(QWidget):
    """Harita üzerinde tiklayarak standart ArduPilot görevi olustur."""

    write_mission = pyqtSignal(dict, list)   # (params, görev listesi) → mavlink_worker
    read_mission  = pyqtSignal()       # drone'dan görevi oku

    def __init__(self, parent=None):
        super().__init__(parent)
        self._waypoints: list = []
        self._map_ready = False
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._poll_js)
        self._build_ui()
        self._poll_timer.start()

    def _build_ui(self) -> None:
        self.setStyleSheet("color:#e0e0e0;")
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # Üst araç çubugu
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        lbl = QLabel("Haritaya tikla → Waypoint ekle  |  Noktayi sürükle → Konum güncelle")
        lbl.setStyleSheet("color:#888;font-size:11px;")
        toolbar.addWidget(lbl, stretch=1)

        self._alt_spin = QDoubleSpinBox()
        self._alt_spin.setRange(1, 500)
        self._alt_spin.setValue(_DEFAULT_ALT)
        self._alt_spin.setSuffix(" m")
        self._alt_spin.setFixedWidth(90)
        self._alt_spin.setStyleSheet(
            "border:1px solid #0f3460;color:#e0e0e0;padding:4px;"
        )
        toolbar.addWidget(QLabel("Varsayilan Alt:"))
        toolbar.addWidget(self._alt_spin)

        def btn(text, color, tip=""):
            b = QPushButton(text)
            b.setStyleSheet(
                f"background:{color};color:white;padding:6px 10px;"
                f"border-radius:4px;font-weight:bold;font-size:11px;"
            )
            if tip:
                b.setToolTip(tip)
            return b

        b_clear  = btn("Temizle",  "#444", "Tüm waypointleri sil")
        b_undo   = btn("Geri Al",  "#333", "Son waypointti sil")
        b_save   = btn("JSON Kaydet", "#0f3460", "Görevi dosyaya kaydet")
        b_load   = btn("JSON Yükle",  "#0f3460", "Dosyadan görev yükle")
        b_read   = btn("Drone Oku",   "#2a6496", "Drone bellegindeki görevi al")
        b_write  = btn("Drone Yaz",   "#e94560", "Görevi drone bellegi'ne yaz")

        b_clear.clicked.connect(self._clear_all)
        b_undo.clicked.connect(self._undo_last)
        b_save.clicked.connect(self._save_json)
        b_load.clicked.connect(self._load_json)
        b_read.clicked.connect(self.read_mission)
        b_write.clicked.connect(self._write_to_drone)

        for w in (b_clear, b_undo, b_save, b_load, b_read, b_write):
            toolbar.addWidget(w)

        root.addLayout(toolbar)

        # Splitter: Harita (üst) / Tablo (alt)
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Üst bölüm: Sol HUD + Sağ Harita
        top_pane = QWidget()
        top_lay = QHBoxLayout(top_pane)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(0)

        # --- SOL HUD PANELI ---
        from PyQt6.QtWidgets import QListWidget
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
        
        top_lay.addWidget(self._hud_widget)

        # Harita
        if _WE:
            self._web = QWebEngineView()
            s = self._web.settings()
            if s is not None:
                s.setAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                    True,
                )
            self._load_map()
            self._web.loadFinished.connect(self._on_map_loaded)
            top_lay.addWidget(self._web, stretch=1)
        else:
            lbl2 = QLabel("Harita: PyQt6-WebEngine kurulu degil")
            lbl2.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl2.setStyleSheet("color:#888;font-size:13px;")
            top_lay.addWidget(lbl2, stretch=1)
            
        splitter.addWidget(top_pane)

        # Waypoint tablosu
        tbl_widget = QWidget()
        tbl_widget.setStyleSheet("")
        tbl_layout = QVBoxLayout(tbl_widget)
        tbl_layout.setContentsMargins(0, 0, 0, 0)
        tbl_layout.setSpacing(0)

        header_lbl = QLabel("  Waypoint Listesi")
        header_lbl.setStyleSheet(
            "color:#aaa;font-weight:bold;font-size:11px;padding:4px;"
        )
        tbl_layout.addWidget(header_lbl)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["#", "Komut", "Enlem", "Boylam", "Irtifa (m)", "Gecikme (s)", "Silme"]
        )
        self._table.setStyleSheet(
            "QTableWidget{color:#e0e0e0;gridline-color:#1a1a3e;"
            "font-size:12px;border:none;}"
            "QHeaderView::section{color:#aaa;padding:4px;border:none;}"
        )
        _hh = self._table.horizontalHeader()  # guard: None dönebilir (Pylance)
        if _hh is not None:
            _hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        _vh = self._table.verticalHeader()
        if _vh is not None:
            _vh.setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        tbl_layout.addWidget(self._table, stretch=1)

        splitter.addWidget(tbl_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, stretch=1)

    def _load_map(self) -> None:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        )
        tmp.write(_MISSION_MAP_HTML)
        tmp.close()
        self._map_ready = False
        self._web.load(QUrl.fromLocalFile(tmp.name))

    def _on_map_loaded(self, ok: bool) -> None:
        self._map_ready = ok
        if not ok:
            logger.warning("Görev haritası yüklenemedi")

    # ── JS Polling ────────────────────────────────────────────────
    def _poll_js(self) -> None:
        if not _WE or not self._map_ready or not hasattr(self, "_web"):
            return
        page = self._web.page()
        if page is None:
            return
        # Sayfa hazır olsa bile fonksiyon yoksa boş döner
        page.runJavaScript(
            "typeof getPendingJSON==='function' ? getPendingJSON() : '[]'",
            self._handle_pending,
        )

    def _handle_pending(self, result: object) -> None:
        if not result or result == "[]":
            return
        try:
            clicks = json.loads(str(result))
        except (json.JSONDecodeError, TypeError):
            return
        for click in clicks:
            if "update_idx" in click:
                idx = int(click["update_idx"]) - 1
                if 0 <= idx < len(self._waypoints):
                    self._waypoints[idx]["lat"] = click["lat"]
                    self._waypoints[idx]["lon"] = click["lon"]
                    self._refresh_table()
            else:
                self._add_waypoint(click["lat"], click["lon"])

    # ── Waypoint yönetimi ─────────────────────────────────────────────────
    def _add_waypoint(self, lat: float, lon: float) -> None:
        idx = len(self._waypoints) + 1
        alt = self._alt_spin.value()
        wp = {
            "idx": idx,
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "cmd_id": 16,   # NAV_WAYPOINT
            "delay": 0,
        }
        self._waypoints.append(wp)
        if _WE and hasattr(self, "_web"):
            page = self._web.page()
            if page is not None:
                page.runJavaScript(
                    f"addWaypointFromPython({lat},{lon},{alt},{idx})"
                )
        self._refresh_table()

    def _refresh_table(self) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for wp in self._waypoints:
            row = self._table.rowCount()
            self._table.insertRow(row)

            self._table.setItem(row, 0, QTableWidgetItem(str(wp["idx"])))

            cmd_combo = QComboBox()
            cmd_combo.setStyleSheet(
                "color:#e0e0e0;border:1px solid #0f3460;"
            )
            for name, cid in _CMD_TYPES:
                cmd_combo.addItem(name, cid)
            cur = next(
                (i for i, (_, cid) in enumerate(_CMD_TYPES) if cid == wp.get("cmd_id", 16)),
                0,
            )
            cmd_combo.setCurrentIndex(cur)
            cmd_combo.currentIndexChanged.connect(
                lambda _, r=row, w=wp: self._on_cmd_changed(r, w, _)
            )
            self._table.setCellWidget(row, 1, cmd_combo)

            for col, key, digits in (
                (2, "lat", 6),
                (3, "lon", 6),
                (4, "alt", 1),
            ):
                item = QTableWidgetItem(f"{wp[key]:.{digits}f}")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)

            delay_item = QTableWidgetItem(str(wp.get("delay", 0)))
            delay_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 5, delay_item)

            del_btn = QPushButton("X")
            del_btn.setStyleSheet(
                "color:white;padding:2px 6px;"
                "font-weight:bold;border-radius:3px;"
            )
            idx_capture = wp["idx"]
            del_btn.clicked.connect(lambda _, i=idx_capture: self._delete_wp(i))
            self._table.setCellWidget(row, 6, del_btn)

        self._table.blockSignals(False)
        self._refresh_hud()

    def _refresh_hud(self) -> None:
        self._hud_list.clear()
        if not self._waypoints:
            self._hud_summary.setText("Haritaya tıklayarak nokta ekleyin.")
            return
            
        import math
        total_dist = 0.0
        
        def haversine(lat1, lon1, lat2, lon2):
            R = 6371000
            phi1, phi2 = math.radians(lat1), math.radians(lat2)
            dphi = math.radians(lat2 - lat1)
            dlam = math.radians(lon2 - lon1)
            a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            return R * c

        for i in range(1, len(self._waypoints)):
            p1 = self._waypoints[i-1]
            p2 = self._waypoints[i]
            d = haversine(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
            total_dist += d
            self._hud_list.addItem(f"WP {i-1} ➔ WP {i} : {d:.1f} m")
            
        self._hud_summary.setText(f"Toplam Nokta: {len(self._waypoints)}\nToplam Mesafe: {total_dist:.1f} m")

    def _on_cmd_changed(self, row: int, wp: dict, _: int) -> None:
        combo = self._table.cellWidget(row, 1)
        if isinstance(combo, QComboBox):
            wp["cmd_id"] = combo.currentData()

    def _delete_wp(self, idx: int) -> None:
        self._waypoints = [w for w in self._waypoints if w["idx"] != idx]
        for i, w in enumerate(self._waypoints):
            w["idx"] = i + 1
        if _WE and hasattr(self, "_web"):
            page = self._web.page()
            if page is not None:
                page.runJavaScript("clearAllWaypoints()")
                for w in self._waypoints:
                    page.runJavaScript(
                        f"addWaypointFromPython({w['lat']},{w['lon']},{w['alt']},{w['idx']})"
                    )
        self._refresh_table()

    def _clear_all(self) -> None:
        self._waypoints.clear()
        if _WE and hasattr(self, "_web"):
            page = self._web.page()
            if page is not None:
                page.runJavaScript("clearAllWaypoints()")
        self._refresh_table()

    def _undo_last(self) -> None:
        if not self._waypoints:
            return
        self._waypoints.pop()
        if _WE and hasattr(self, "_web"):
            page = self._web.page()
            if page is not None:
                page.runJavaScript("removeLastWaypoint()")
        self._refresh_table()

    def _save_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Görevi Kaydet", "", "JSON (*.json)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._waypoints, f, indent=2, ensure_ascii=False)

    def _load_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Görevi Yükle", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._clear_all()
            for wp in data:
                self._add_waypoint(float(wp["lat"]), float(wp["lon"]))
                if self._waypoints:
                    self._waypoints[-1]["alt"]    = float(wp.get("alt", _DEFAULT_ALT))
                    self._waypoints[-1]["cmd_id"] = int(wp.get("cmd_id", 16))
                    self._waypoints[-1]["delay"]  = int(wp.get("delay", 0))
            self._refresh_table()
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            QMessageBox.warning(self, "Hata", f"Görev dosyasi okunamadi:\n{e}")

    def _write_to_drone(self) -> None:
        if not self._waypoints:
            QMessageBox.information(self, "Bilgi", "Waypoint listesi bos.")
            return
        self.write_mission.emit({}, self._waypoints)


    @pyqtSlot(dict)
    def update_telemetry(self, data: dict):
        lat = data.get("lat")
        lon = data.get("lon")
        hdg = data.get("hdg", 0)
        if lat and lon and hasattr(self, "_web"):
            _page = self._web.page()
            if _page is not None:
                _page.runJavaScript(f"set_drone_pos({lat}, {lon}, {hdg});")
            
    # ── Harici güncelleme (drone'dan okunan görev) ────────────────────────
    @pyqtSlot(list)
    def load_from_drone(self, mission_items: list) -> None:
        self._clear_all()
        for item in mission_items:
            lat = item.get("lat", 0.0)
            lon = item.get("lon", 0.0)
            if abs(lat) < 0.0001 and abs(lon) < 0.0001:
                continue
            self._add_waypoint(lat, lon)
            if self._waypoints:
                self._waypoints[-1]["alt"]    = float(item.get("alt", _DEFAULT_ALT))
                self._waypoints[-1]["cmd_id"] = int(item.get("cmd_id", 16))
        self._refresh_table()
