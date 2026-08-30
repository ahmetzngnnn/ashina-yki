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

# NEDEN: Fare tekerleği koruması (odakta olmayan spin/combo değerini sessizce
# değiştirmesin). Yardımcı tek yerde tanımlı — mission_panel_view.py; burada
# yeniden yazmak yerine paylaşılıyor (döngüsel import yok: o modül bunu
# import etmiyor).
from ui.views.mission_panel_view import guard_wheel

logger = logging.getLogger(__name__)

# ── Görev haritasi HTML ────────────────────────────────────────────────────────
# NOT: Asagidaki Leaflet/CSS renkleri HARITA SEMANTIGIDIR (rota sarisi, WP
# rozeti, kirmizi drone ok isareti) — uydu görüntüsü zemininde okunur kalmasi
# gerekir, bu yüzden YKI temasindan BAGIMSIZ sabit tutulur. Tema tokenlarina
# çevrilmez.
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
ASHINA_addBaseLayers(map);   // altlık + katman seçici + ölçek + çözünürlük göstergesi (ui/map_assets.py)

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
    ("SPLINE_WP",    82),
    ("DELAY",        93),
    ("CHANGE_ALT",   186),
]
_CMD_NAMES = {v: k for k, v in _CMD_TYPES}

# Konum (lat/lon) tasiyan, harita/tabloda gösterilebilen komutlar (NAV_*)
_POSITIONAL_CMDS = {16, 17, 18, 19, 20, 21, 22, 82}

# Varsayilan yükseklik
_DEFAULT_ALT = 30.0

# Manuel eklemede _alt_spin.setRange(1, 500) ile uygulanan sinir — dosyadan/
# FC'den gelen waypoint'ler icin de AYNI sinir burada uygulanir (bkz. _clamp_alt).
_ALT_MIN = 1.0
_ALT_MAX = 500.0


def _clamp_alt(alt: float, context: str = "") -> float:
    """Waypoint irtifasini 1-500 m araligina kirpar (manuel eklemede _alt_spin
    ile uygulanan sinirla TUTARLI). Dosyadan/FC'den gelen alt degerleri bu
    kontrolden gecmeden dogrudan FC'ye yazilmamali. Araliğin disindaysa loglar.
    """
    if alt < _ALT_MIN or alt > _ALT_MAX:
        clamped = max(_ALT_MIN, min(_ALT_MAX, alt))
        logger.warning(
            "Waypoint irtifasi %.2f m izin verilen araligin (%.0f-%.0f m) disinda%s; "
            "%.2f m'ye kirpildi.",
            alt, _ALT_MIN, _ALT_MAX, f" ({context})" if context else "", clamped,
        )
        return clamped
    return alt


# ── Mission Planner / QGC WPL 110 dosya format (.waypoints) ─────────────────
def _parse_qgc_wpl(text: str) -> tuple[list[dict], int, tuple[float, float] | None]:
    """QGC WPL 110 (Mission Planner .waypoints) metnini waypoint listesine çevirir.
    Satir 0 (seq=0) HOME noktasidir; waypoint olarak eklenmez ama kaydedilirken
    geri yazilabilsin diye ayrica döndürülür. Konumsuz DO_* komutlari
    (ör. DO_CHANGE_SPEED) bu görünümün harita/tablo modeli konum gerektirdiginden
    atlanir; atlanan komut sayisi ikinci eleman olarak döner.
    Dönüş: (waypoints, atlanan_komut_sayisi, home_latlon)
    """
    waypoints: list[dict] = []
    skipped = 0
    home: tuple[float, float] | None = None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines:
        if line.upper().startswith("QGC WPL"):
            continue
        parts = line.split()
        if len(parts) < 12:
            continue
        try:
            seq = int(parts[0])
            command = int(parts[3])
            param1 = float(parts[4])
            lat = float(parts[8])
            lon = float(parts[9])
            alt = float(parts[10])
        except ValueError:
            continue
        if seq == 0:
            if lat != 0.0 or lon != 0.0:
                home = (lat, lon)
            continue  # HOME
        if command not in _POSITIONAL_CMDS:
            skipped += 1
            continue
        waypoints.append({
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "cmd_id": command,
            "delay": param1,
        })
    return waypoints, skipped, home


def _build_qgc_wpl(waypoints: list[dict], home: tuple[float, float] | None = None) -> str:
    """Waypoint listesinden Mission Planner uyumlu QGC WPL 110 metni üretir."""
    lines = ["QGC WPL 110"]
    if home is not None:
        home_lat, home_lon = home
    else:
        home_lat, home_lon = 0.0, 0.0
        for wp in waypoints:
            if wp["lat"] != 0.0 or wp["lon"] != 0.0:
                home_lat, home_lon = wp["lat"], wp["lon"]
                break
    # HOME — seq 0, frame 0 (GLOBAL), cmd 16
    lines.append(
        f"0\t1\t0\t16\t0\t0\t0\t0\t{home_lat:.8f}\t{home_lon:.8f}\t0.000000\t1"
    )
    for i, wp in enumerate(waypoints, start=1):
        cmd = int(wp.get("cmd_id", 16))
        delay = float(wp.get("delay", 0))
        lat, lon, alt = wp["lat"], wp["lon"], wp["alt"]
        lines.append(
            f"{i}\t0\t3\t{cmd}\t{delay:.6f}\t0\t0\t0\t"
            f"{lat:.8f}\t{lon:.8f}\t{alt:.6f}\t1"
        )
    return "\n".join(lines) + "\n"


class MissionPlanView(QWidget):
    """Harita üzerinde tiklayarak standart ArduPilot görevi olustur."""

    write_mission = pyqtSignal(dict, list)   # (params, görev listesi) → mavlink_worker
    read_mission  = pyqtSignal()       # drone'dan görevi oku

    def __init__(self, parent=None):
        super().__init__(parent)
        self._waypoints: list = []
        self._home_latlon: tuple[float, float] | None = None
        self._map_ready = False
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._poll_js)
        self._build_ui()
        # NEDEN isVisible() kontrolü: sekme henüz görünür değilse (main_window
        # bu sekmeyi ilk sekme olarak açmadıysa) polling'i BAŞLATMA — showEvent
        # zaten görünür olunca başlatacak (bkz. showEvent/hideEvent altında).
        if self.isVisible():
            self._poll_timer.start()

    def showEvent(self, event) -> None:
        # Sekme (main_window._tabs) tekrar görünür olunca polling'i devam
        # ettir. NEDEN: main_window._dispatch_telemetry'deki "yalnızca aktif
        # sekme" ilkesiyle AYNI motivasyon — gizli sekmede 400 ms'de bir
        # runJavaScript çağırmak gereksiz CPU/JS köprüsü yükü yaratıyordu.
        super().showEvent(event)
        if not self._poll_timer.isActive():
            self._poll_timer.start()

    def hideEvent(self, event) -> None:
        # Sekme gizlenince polling'i durdur — bkz. showEvent.
        super().hideEvent(event)
        if self._poll_timer.isActive():
            self._poll_timer.stop()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Üst araç çubugu
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        lbl = QLabel("Haritaya tikla → Waypoint ekle  |  Noktayi sürükle → Konum güncelle")
        lbl.setObjectName("mutedLabel")
        toolbar.addWidget(lbl, stretch=1)

        self._alt_spin = QDoubleSpinBox()
        self._alt_spin.setRange(1, 500)
        self._alt_spin.setValue(_DEFAULT_ALT)
        self._alt_spin.setSuffix(" m")
        self._alt_spin.setMinimumWidth(90)
        self._alt_spin.setMaximumWidth(110)   # şartname §3: sayı girdisi içerikle sınırlı
        self._alt_spin.setToolTip(
            "Yeni waypoint'ler için varsayılan irtifa.\n"
            "Değiştirmek için alana tıklayın (tekerlek yalnızca odaktayken çalışır)."
        )
        # NEDEN: Araç çubuğu/harita üzerinde tekerlek kullanılırken varsayılan
        # irtifa sessizce değişebiliyordu — uçuş parametresi fark edilmeden bozulur.
        guard_wheel(self._alt_spin)
        toolbar.addWidget(QLabel("Varsayilan Alt:"))
        toolbar.addWidget(self._alt_spin)

        def btn(text, obj_name="", tip=""):
            b = QPushButton(text)
            if obj_name:
                b.setObjectName(obj_name)
            b.setMinimumHeight(28)
            if tip:
                b.setToolTip(tip)
            return b

        b_clear  = btn("Temizle",  "", "Tüm waypointleri sil")
        b_undo   = btn("Geri Al",  "", "Son waypointti sil")
        b_save   = btn("Kaydet", "", "Görevi dosyaya kaydet (.waypoints / .json)")
        b_load   = btn("Yükle",  "", "Dosyadan görev yükle (.waypoints / .json)")
        b_read   = btn("Drone Oku",   "primaryButton", "Drone bellegindeki görevi al")
        b_write  = btn("Drone Yaz",   "dangerButton", "Görevi drone bellegi'ne yaz")

        b_clear.clicked.connect(self._clear_all)
        b_undo.clicked.connect(self._undo_last)
        b_save.clicked.connect(self._save_mission)
        b_load.clicked.connect(self._load_mission)
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
        top_lay.setSpacing(8)

        # --- SOL HUD PANELI ---
        from PyQt6.QtWidgets import QListWidget
        self._hud_widget = QWidget()
        self._hud_widget.setFixedWidth(300)
        self._hud_lay = QVBoxLayout(self._hud_widget)
        self._hud_lay.setContentsMargins(6, 6, 6, 6)
        self._hud_lay.setSpacing(6)

        # Kalinlik/renk QSS'ten (sectionTitle) gelir — <b> markup'i gereksiz
        lbl_hud_title = QLabel("GÖREV ÖZETİ")
        lbl_hud_title.setObjectName("sectionTitle")
        self._hud_lay.addWidget(lbl_hud_title)

        self._hud_summary = QLabel("Haritaya tıklayarak nokta ekleyin.")
        self._hud_summary.setWordWrap(True)
        self._hud_summary.setObjectName("hintLabel")
        self._hud_lay.addWidget(self._hud_summary)

        # Liste basligi: statik metin → QSS class'i (mutedLabel), markup yok
        lbl_wp_dists = QLabel("WP MESAFELERİ")
        lbl_wp_dists.setObjectName("mutedLabel")
        self._hud_lay.addWidget(lbl_wp_dists)

        self._hud_list = QListWidget()
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
            lbl2.setObjectName("mutedLabel")
            top_lay.addWidget(lbl2, stretch=1)
            
        splitter.addWidget(top_pane)

        # Waypoint tablosu
        tbl_widget = QWidget()
        tbl_layout = QVBoxLayout(tbl_widget)
        tbl_layout.setContentsMargins(0, 0, 0, 0)
        tbl_layout.setSpacing(0)

        header_lbl = QLabel("  Waypoint Listesi")
        header_lbl.setObjectName("sectionTitle")
        tbl_layout.addWidget(header_lbl)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["#", "Komut", "Enlem", "Boylam", "Irtifa (m)", "Gecikme (s)", "Silme"]
        )
        _hh = self._table.horizontalHeader()  # guard: None dönebilir (Pylance)
        if _hh is not None:
            # Komut/Enlem/Boylam/İrtifa/Gecikme esner; # ve Silme dar kalır
            _hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            _hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            _hh.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        _vh = self._table.verticalHeader()
        if _vh is not None:
            _vh.setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        tbl_layout.addWidget(self._table, stretch=1)

        splitter.addWidget(tbl_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([600, 200])  # harita baskın başlangıç oranı (3:1)

        root.addWidget(splitter, stretch=1)

    def _load_map(self) -> None:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        )
        from ui.map_assets import localize_leaflet
        tmp.write(localize_leaflet(_MISSION_MAP_HTML))
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
            for name, cid in _CMD_TYPES:
                cmd_combo.addItem(name, cid)
            cur = next(
                (i for i, (_, cid) in enumerate(_CMD_TYPES) if cid == wp.get("cmd_id", 16)),
                0,
            )
            cmd_combo.setCurrentIndex(cur)
            # NEDEN: Waypoint tablosu tekerlekle kaydırılırken imlecin altındaki
            # komut kutusu (WAYPOINT → TAKEOFF/LAND ...) sessizce değişiyordu.
            # Koruma sonrası tekerlek tabloyu kaydırır; komut değişmez.
            guard_wheel(cmd_combo)
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
            del_btn.setObjectName("dangerButton")
            del_btn.setToolTip("Bu waypoint'i sil")
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
        self._home_latlon = None
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

    def _save_mission(self) -> None:
        path, sel_filter = QFileDialog.getSaveFileName(
            self, "Görevi Kaydet", "",
            "Mission Planner Waypoints (*.waypoints);;JSON (*.json)"
        )
        if not path:
            return
        is_wpl = path.lower().endswith((".waypoints", ".txt")) or (
            "Waypoints" in sel_filter and not path.lower().endswith(".json")
        )
        try:
            if is_wpl:
                if not path.lower().endswith((".waypoints", ".txt")):
                    path += ".waypoints"
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(_build_qgc_wpl(self._waypoints, self._home_latlon))
            else:
                if not path.lower().endswith(".json"):
                    path += ".json"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self._waypoints, f, indent=2, ensure_ascii=False)
        except OSError as e:
            QMessageBox.warning(self, "Hata", f"Görev dosyasi kaydedilemedi:\n{e}")

    def _load_mission(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Görevi Yükle", "",
            "Görev Dosyalari (*.waypoints *.txt *.json);;"
            "Mission Planner Waypoints (*.waypoints *.txt);;JSON (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            if path.lower().endswith(".json"):
                data = json.loads(content)
                self._clear_all()
                self._home_latlon = None
                for wp in data:
                    self._add_waypoint(float(wp["lat"]), float(wp["lon"]))
                    if self._waypoints:
                        idx = self._waypoints[-1].get("idx")
                        self._waypoints[-1]["alt"]    = _clamp_alt(
                            float(wp.get("alt", _DEFAULT_ALT)), f"JSON WP#{idx}"
                        )
                        self._waypoints[-1]["cmd_id"] = int(wp.get("cmd_id", 16))
                        self._waypoints[-1]["delay"]  = float(wp.get("delay", 0))
                self._refresh_table()
            else:
                waypoints, skipped, home = _parse_qgc_wpl(content)
                if not waypoints:
                    QMessageBox.warning(
                        self, "Hata",
                        "Dosyada geçerli waypoint bulunamadi (QGC WPL 110 formati bekleniyor)."
                    )
                    return
                self._clear_all()
                self._home_latlon = home
                for wp in waypoints:
                    self._add_waypoint(wp["lat"], wp["lon"])
                    if self._waypoints:
                        idx = self._waypoints[-1].get("idx")
                        self._waypoints[-1]["alt"]    = _clamp_alt(wp["alt"], f"WPL WP#{idx}")
                        self._waypoints[-1]["cmd_id"] = wp["cmd_id"]
                        self._waypoints[-1]["delay"]  = wp["delay"]
                self._refresh_table()
                if skipped:
                    QMessageBox.information(
                        self, "Bilgi",
                        f"{skipped} adet konumsuz komut (ör. DO_CHANGE_SPEED) atlandi;"
                        " yalnizca haritada gösterilebilen waypoint'ler yüklendi."
                    )
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
            QMessageBox.warning(self, "Hata", f"Görev dosyasi okunamadi:\n{e}")

    def _write_to_drone(self) -> None:
        if not self._waypoints:
            QMessageBox.information(self, "Bilgi", "Waypoint listesi bos.")
            return
        # Son savunma hatti: kaynagi ne olursa olsun (harita/dosya/FC-okuma)
        # FC'ye yazilmadan hemen once TUM waypoint'lerin irtifasi 1-500 m
        # araligina kirpilir — manuel eklemede _alt_spin ile uygulanan sinirla
        # TUTARLI (bkz. _clamp_alt).
        for wp in self._waypoints:
            wp["alt"] = _clamp_alt(wp.get("alt", _DEFAULT_ALT), f"WP#{wp.get('idx', '?')}")
        # NEDEN onay: Görev FC'ye yazilinca arac otonom modda bu noktalari
        # ucacak; yanlis/eksik/kirpilmis bir görevin sessizce yazilmasi ciddi
        # sonuç doğurabilir. Aynı desen top_bar.py._on_drop_click /
        # tuning_view.py._apply_pid.
        reply = QMessageBox.question(
            self, "Görevi FC'ye Yaz",
            f"{len(self._waypoints)} waypoint içeren görev uçuş kontrolcüsüne (FC) "
            "YAZILACAK.\n\n"
            "Görev listesini (nokta sayısı, irtifalar, sıralama) gözden geçirdiğinizi "
            "doğrulayın.\n\n"
            "Onaylıyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
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
                idx = self._waypoints[-1].get("idx")
                self._waypoints[-1]["alt"]    = _clamp_alt(
                    float(item.get("alt", _DEFAULT_ALT)), f"FC WP#{idx}"
                )
                self._waypoints[-1]["cmd_id"] = int(item.get("cmd_id", 16))
        self._refresh_table()
