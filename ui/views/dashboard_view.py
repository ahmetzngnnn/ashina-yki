"""
dashboard_view.py — Modül 1 (Mission Planner Stil)
Sol  : Hızlı Veri (büyük sayılar) + Hizli Komutlar + Guided Kontrol + Drone Mesajlari
Orta : MP-Stil HUD (compass tape, pitch merdiveni, taraf bantlari, alt bilgi)
Sag  : 3D Drone Modeli (sag üst) + Esri harita (sag alt)
Alt  : Video akisi (HUD ile esit yükseklik)
"""
import math
import logging
from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QPoint, QUrl, QTimer, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPixmap,
    QFont, QImage, QPolygon, QTransform,
)
from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame,
    QSizePolicy, QPushButton, QTextEdit, QSpinBox, QDoubleSpinBox,
    QGroupBox, QGridLayout, QDialog, QComboBox, QLineEdit,
    QDialogButtonBox, QFormLayout, QMenu, QTabWidget, QSlider, QScrollArea, QProgressBar
)

from core.gcs_config import config

try:
    from ui.widgets.tof_widget import TofWidget
except ImportError:
    TofWidget = None

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False
    logging.warning("PyQt6-WebEngine kurulu degil — Harita devre disi")

try:
    from pymavlink import mavutil as _mavu
    _MV = _mavu
except ImportError:
    _MV = None

logger = logging.getLogger(__name__)

# ── Esri Uydu + Leaflet + Dönen Drone ──────────────────────────────────────────
_MAP_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
html,body,#map{width:100%;height:100%;margin:0;padding:0;}
.drone-icon{background:transparent!important;border:none!important;}
</style>
</head><body><div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
var map = L.map('map',{zoomControl:true,attributionControl:false,maxZoom:28}).setView([39.925,32.866],16);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  {maxZoom:28,maxNativeZoom:19,attribution:'Esri'}).addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  {maxZoom:28,maxNativeZoom:19,opacity:0.7}).addTo(map);

function makeDroneIcon(hdg){
  var svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" width="44" height="44"'
      +' style="transform:rotate('+hdg+'deg);transform-origin:center;'
      +'filter:drop-shadow(0 0 3px rgba(0,0,0,0.9));">'
      +'<polygon points="20,3 13,37 20,27 27,37" fill="#ff3333" stroke="white" stroke-width="2"/>'
      +'<circle cx="20" cy="20" r="4" fill="white" stroke="#ff3333" stroke-width="1.5"/>'
      +'</svg>';
  return L.divIcon({html:svg,iconSize:[44,44],iconAnchor:[22,22],className:'drone-icon'});
}
var homeIcon = L.divIcon({html:'<div style="background:#4ade80;color:#000;font-weight:bold;font-size:10px;padding:1px 4px;border-radius:3px;">H</div>',iconSize:[20,16],iconAnchor:[10,8],className:''});

var drone = L.marker([39.925,32.866],{icon:makeDroneIcon(0)}).addTo(map);
var trail = []; var trailLine = L.polyline([],{color:'#4ade80',weight:2,opacity:0.8}).addTo(map);
var homeSet = false; var homeMarker = null;
var isFirstFix = true;

function updateDrone(lat,lon,hdg){
  drone.setLatLng([lat,lon]);
  drone.setIcon(makeDroneIcon(hdg));
  trail.push([lat,lon]);
  if(trail.length>500) trail.shift();
  trailLine.setLatLngs(trail);
  if(!homeSet){homeMarker=L.marker([lat,lon],{icon:homeIcon}).addTo(map);homeSet=true;}
  if(isFirstFix){
    map.setView([lat,lon], 18);
    isFirstFix = false;
  }
}
function clearTrail(){
  trail=[]; trailLine.setLatLngs([]);
  homeSet=false; if(homeMarker){map.removeLayer(homeMarker);homeMarker=null;}
}
</script></body></html>"""


# ── Guided Mod Kontrol Paneli ─────────────────────────────────────────────────
class GuidedControlWidget(QWidget):
    """Guided modda PC'den metrik mesafe/açı göndererek drone kontrolü."""
    send_velocity = pyqtSignal(float, float, float)   # vx, vy, vz (m/s)
    send_yaw     = pyqtSignal(float, bool)             # yaw_deg, relative
    send_command  = pyqtSignal(int, list)              # MAVLink komut

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        self.setStyleSheet("background:transparent;")
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(3)

        grp = QGroupBox("Guided Kontrol (m / derece)")
        grp.setStyleSheet("")
        gl = QVBoxLayout(grp)
        gl.setSpacing(3)

        # Mesafe girişi
        dist_row = QHBoxLayout()
        dist_row.addWidget(QLabel("Mesafe:"))
        self._dist_spin = QDoubleSpinBox()
        self._dist_spin.setRange(0.1, 500.0)
        self._dist_spin.setValue(2.0)
        self._dist_spin.setSuffix(" m")
        self._dist_spin.setDecimals(1)
        self._dist_spin.setStyleSheet("")
        self._dist_spin.setFixedWidth(80)
        dist_row.addWidget(self._dist_spin)
        dist_row.addStretch()
        gl.addLayout(dist_row)

        # Yön butonları (3x3 grid)
        dir_grid = QGridLayout()
        dir_grid.setSpacing(3)
        BTN_STYLE = "background:#0f3460;color:#e0e0e0;"
        MOVE_BTNS = [
            ("↖ Sol-İleri",  0, 0, (-1,  1,  0)),
            ("↑ İleri",      0, 1, ( 0,  1,  0)),
            ("↗ Sağ-İleri",  0, 2, ( 1,  1,  0)),
            ("← Sol",        1, 0, (-1,  0,  0)),
            ("■ Dur",         1, 1, ( 0,  0,  0)),
            ("→ Sağ",        1, 2, ( 1,  0,  0)),
            ("↙ Sol-Geri",   2, 0, (-1, -1,  0)),
            ("↓ Geri",       2, 1, ( 0, -1,  0)),
            ("↘ Sağ-Geri",   2, 2, ( 1, -1,  0)),
        ]
        for lbl, r, c, (dx, dy, _dz) in MOVE_BTNS:
            btn = QPushButton(lbl)
            if lbl == "■ Dur":
                btn.setStyleSheet("background:#c87800;color:white;")
            else:
                btn.setStyleSheet(BTN_STYLE)
            # dx: sağa (+), dy: ileri (+), NED: x=north, y=east
            dist = self._dist_spin
            vx_, vy_ = dy, dx   # NED: forward=x, right=y
            def _make_handler(_vx, _vy):
                def _clicked():
                    d = dist.value()
                    spd = 1.5   # m/s sabit hız
                    if _vx == 0 and _vy == 0:
                        self._stop()
                    else:
                        mag = math.sqrt(_vx * _vx + _vy * _vy)
                        self.send_velocity.emit(
                            _vx / mag * spd, _vy / mag * spd, 0.0
                        )
                return _clicked
            btn.clicked.connect(_make_handler(vx_, vy_))
            dir_grid.addWidget(btn, r, c)
        gl.addLayout(dir_grid)

        # Yükseklik satırı
        alt_row = QHBoxLayout()
        b_up = QPushButton("▲ Yukarı")
        b_up.setStyleSheet("background:#0f6446;color:white;")
        b_dn = QPushButton("▼ Aşağı")
        b_dn.setStyleSheet("background:#6a0f0f;color:white;")
        b_up.clicked.connect(lambda: self.send_velocity.emit(0.0, 0.0, -self._dist_spin.value() / 3.0))
        b_dn.clicked.connect(lambda: self.send_velocity.emit(0.0, 0.0,  self._dist_spin.value() / 3.0))
        alt_row.addWidget(b_up)
        alt_row.addWidget(b_dn)
        gl.addLayout(alt_row)

        # Yaw satırı
        yaw_row = QHBoxLayout()
        yaw_row.addWidget(QLabel("Yaw:"))
        self._yaw_spin = QDoubleSpinBox()
        self._yaw_spin.setRange(-180, 180)
        self._yaw_spin.setValue(45.0)
        self._yaw_spin.setSuffix("°")
        self._yaw_spin.setDecimals(0)
        self._yaw_spin.setStyleSheet("")
        self._yaw_spin.setFixedWidth(70)
        b_yaw_l = QPushButton("↺ Sol")
        b_yaw_l.setStyleSheet(BTN_STYLE)
        b_yaw_r = QPushButton("↻ Sağ")
        b_yaw_r.setStyleSheet(BTN_STYLE)
        b_yaw_l.clicked.connect(lambda: self.send_yaw.emit(-abs(self._yaw_spin.value()), True))
        b_yaw_r.clicked.connect(lambda: self.send_yaw.emit( abs(self._yaw_spin.value()), True))
        yaw_row.addWidget(self._yaw_spin)
        yaw_row.addWidget(b_yaw_l)
        yaw_row.addWidget(b_yaw_r)
        gl.addLayout(yaw_row)

        root.addWidget(grp)

    def _stop(self) -> None:
        self.send_velocity.emit(0.0, 0.0, 0.0)


# ── Video Kaynak Seçim Diyalogu ───────────────────────────────────────────────
class VideoSourceDialog(QDialog):
    source_selected = pyqtSignal(str)   # video_worker.set_source(str) için

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Video Kaynağı Seç")
        self.setMinimumWidth(380)
        # self.setStyleSheet() kaldırıldı, global style kullanılacak.
        self._build()

    # Kaynak tipi index'leri (combo sırasıyla aynı)
    _T_AUTO  = 0   # Otomatik — görev durumuna göre ham/işlenmiş
    _T_RAW   = 1   # İHA ham kamera (port 8080)
    _T_PROC  = 2   # İHA işlenmiş görüntü (port 8081)
    _T_CAM   = 3   # PC kamera (index)
    _T_URL   = 4   # URL / RTSP / HTTP
    _T_IP    = 5   # IP kamera (ethernet)

    def _build(self) -> None:
        root = QVBoxLayout(self)

        form = QFormLayout()
        self._type_cb = QComboBox()
        raw_p  = config.companion_video_raw_port
        proc_p = config.companion_video_port
        self._type_cb.addItems([
            "Otomatik (görev durumuna göre)",
            f"İHA Ham Kamera (port {raw_p})",
            f"İHA İşlenmiş Görüntü (port {proc_p})",
            "PC Kamera (Index)",
            "URL / RTSP / HTTP",
            "IP Kamera (Ethernet)",
        ])
        self._type_cb.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("Kaynak Tipi:", self._type_cb)

        # Bilgi etiketi (Otomatik/Ham/İşlenmiş seçilince çözülen adresi gösterir)
        self._info_lbl = QLabel()
        self._info_lbl.setStyleSheet("color:#8ab4f8;")
        form.addRow("", self._info_lbl)

        self._cam_idx = QSpinBox()
        self._cam_idx.setRange(0, 8)
        self._cam_idx.setValue(0)
        self._cam_idx.setVisible(False)
        form.addRow("Kamera İndex:", self._cam_idx)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("rtsp://192.168.1.100:554/stream")
        self._url_edit.setVisible(False)
        form.addRow("URL:", self._url_edit)

        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText(f"192.168.1.200 (port {proc_p} otomatik)")
        self._ip_edit.setVisible(False)
        form.addRow("IP (Port Otomatik):", self._ip_edit)

        root.addLayout(form)

        # Butonlar
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._on_type_changed(self._T_AUTO)   # başlangıç bilgisini göster

    def _on_type_changed(self, idx: int) -> None:
        self._cam_idx.setVisible(idx == self._T_CAM)
        self._url_edit.setVisible(idx == self._T_URL)
        self._ip_edit.setVisible(idx == self._T_IP)

        host   = config.companion_host
        raw_p  = config.companion_video_raw_port
        proc_p = config.companion_video_port
        if idx == self._T_AUTO:
            self._info_lbl.setText(
                "Görev çalışırken işlenmiş, dururken ham görüntüye otomatik geçer.")
        elif idx == self._T_RAW:
            self._info_lbl.setText(f"→ http://{host}:{raw_p}/video")
        elif idx == self._T_PROC:
            self._info_lbl.setText(f"→ http://{host}:{proc_p}/video")
        else:
            self._info_lbl.setText("")

    def _on_ok(self) -> None:
        idx  = self._type_cb.currentIndex()
        host = config.companion_host
        if idx == self._T_AUTO:
            src = "auto"   # main_window otomatik geçişi yeniden açar
        elif idx == self._T_RAW:
            src = f"http://{host}:{config.companion_video_raw_port}/video"
        elif idx == self._T_PROC:
            src = f"http://{host}:{config.companion_video_port}/video"
        elif idx == self._T_CAM:
            src = str(self._cam_idx.value())
        elif idx == self._T_URL:
            src = self._url_edit.text().strip()
        else:  # _T_IP
            ip = self._ip_edit.text().strip()
            if not ip.startswith("http"):
                port = config.companion_video_port
                src = f"http://{ip}:{port}/video"
            else:
                src = ip
        if src:
            self.source_selected.emit(src)
        self.accept()


# ── Mission Planner-Stil HUD ──────────────────────────────────────────────────
class HUDWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background:#1a6ea0;")
        # Attitude (radyan)
        self._roll  = 0.0
        self._pitch = 0.0
        self._yaw   = 0.0
        # VFR
        self._alt         = 0.0
        self._airspeed    = 0.0
        self._groundspeed = 0.0
        self._climb       = 0.0
        self._heading     = 0
        self._throttle    = 0
        # Batarya
        self._batt_v = 0.0
        self._batt_a = 0.0
        self._batt_r = 0
        self._cell_voltages: list = []   # bireysel hücre voltajları (V)
        # GPS
        self._gps_name = "---"
        self._gps_sats = 0
        self._hdop     = 99.0
        # EKF & Mod
        self._ekf_flags   = 0
        self._flight_mode = "---"
        self._armed       = False

    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        self._roll        = float(data.get("roll",        0.0))
        self._pitch       = float(data.get("pitch",       0.0))
        self._yaw         = float(data.get("yaw",         0.0))
        self._airspeed    = float(data.get("airspeed",    0.0))
        self._groundspeed = float(data.get("groundspeed", 0.0))
        self._heading     = int(  data.get("heading",     0  ))
        self._alt         = float(data.get("alt",         0.0))   # GLOBAL_POSITION_INT.relative_alt
        self._baro_alt    = float(data.get("baro_alt",    self._alt))
        self._climb       = float(data.get("climb",       0.0))
        self._throttle    = int(  data.get("throttle",    0  ))
        self._batt_v      = float(data.get("battery_voltage",   0.0))
        self._batt_a      = float(data.get("battery_current",   0.0))
        self._batt_r      = int(  data.get("battery_remaining", 0  ))
        self._cell_voltages = list(data.get("battery_cell_voltages", []))
        self._gps_name    = str(  data.get("gps_fix_name", "---"))
        self._gps_sats    = int(  data.get("gps_sats",     0  ))
        self._hdop        = float(data.get("gps_hdop",     99.0))
        self._ekf_flags   = int(  data.get("ekf_flags",    0  ))
        self._flight_mode = str(  data.get("flight_mode",  "---"))
        self._armed       = bool( data.get("armed",        False))
        self._update_tick = getattr(self, "_update_tick", 0) + 1
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        try:
            self._paint(event)
        except Exception as exc:
            logger.exception("HUD paintEvent hatası: %s", exc)

    def _paint(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h   = self.width(), self.height()
        cx, cy = w // 2, h // 2

        CMP_H  = 36    # Pusula bant yüksekligi
        SIDE_W = 72    # Sol/Sag bant genisligi
        BOT_H  = 44    # Alt bar yüksekligi

        roll_deg  = math.degrees(self._roll)
        pitch_deg = math.degrees(self._pitch)
        pitch_px  = int(pitch_deg * h / 50.0)   # 50 derece = tam yükseklik

        # ── Gökyüzü/Yer arka plan ────────────────────────────────────────
        p.fillRect(0, 0, w, h, QColor("#1a6ea0"))   # varsayilan gökyüzü
        p.save()
        p.translate(cx, cy)
        p.rotate(roll_deg)
        p.fillRect(-w * 2, -h * 2, w * 4, h * 2 + pitch_px, QColor("#1a6ea0"))
        p.fillRect(-w * 2, pitch_px, w * 4, h * 2, QColor("#4a6e28"))
        p.setPen(QPen(QColor("white"), 2))
        p.drawLine(-w * 2, pitch_px, w * 2, pitch_px)

        # ── Pitch merdiveni ───────────────────────────────────────────────
        p.setFont(QFont("Courier New", 8))
        for deg in range(-40, 45, 5):
            if deg == 0:
                continue
            py = pitch_px - int(deg * h / 50.0)
            if py < -h or py > h:
                continue
            if abs(deg) % 10 == 0:
                lw = 48
                p.setPen(QPen(QColor("white"), 1))
                p.drawLine(-lw, py, lw, py)
                p.setPen(QColor("white"))
                p.drawText(lw + 4, py + 4, str(deg))
                p.drawText(-lw - 22, py + 4, str(deg))
            else:
                lw = 24
                p.setPen(QPen(QColor("white"), 1))
                p.drawLine(-lw, py, lw, py)
        p.restore()

        # ── Sabit ucak referansi (sari V) ────────────────────────────────
        p.setPen(QPen(QColor("#ffff00"), 3))
        p.drawLine(cx - 65, cy,      cx - 18, cy)
        p.drawLine(cx - 18, cy,      cx,      cy + 12)
        p.drawLine(cx,      cy + 12, cx + 18, cy)
        p.drawLine(cx + 18, cy,      cx + 65, cy)
        p.setBrush(QBrush(QColor("#ffff00")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx - 4, cy - 4, 8, 8)

        # ── Roll yay (keman) ─────────────────────────────────────────────
        arc_r = min(w, h) // 2 - 2
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("white"), 1))
        for ang in (-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60):
            rad = math.radians(ang - 90)
            ix  = int(cx + arc_r * math.cos(rad))
            iy  = int(cy + arc_r * math.sin(rad))
            tl  = 14 if ang % 30 == 0 else 7
            ix2 = int(cx + (arc_r - tl) * math.cos(rad))
            iy2 = int(cy + (arc_r - tl) * math.sin(rad))
            p.drawLine(ix, iy, ix2, iy2)

        # Roll göstergesi (üstte dönen üçgen)
        p.save()
        p.translate(cx, cy)
        p.rotate(roll_deg)
        p.setBrush(QBrush(QColor("#ff6600")))
        p.setPen(Qt.PenStyle.NoPen)
        pr = -arc_r
        p.drawPolygon(QPolygon([QPoint(0, pr + 2),
                                 QPoint(-7, pr + 16),
                                 QPoint(7,  pr + 16)]))
        p.restore()

        # ── Pusula banti (üst) ───────────────────────────────────────────
        p.fillRect(0, 0, w, CMP_H, QColor(0, 0, 0, 175))
        hdg  = self._heading
        ppd  = w / 60.0
        dirs_map = {0:"N", 45:"NE", 90:"E", 135:"SE",
                    180:"S", 225:"SW", 270:"W", 315:"NW"}
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        for d in range(-35, 36):
            ah = (hdg + d) % 360
            xp = cx + int(d * ppd)
            if xp < 0 or xp > w:
                continue
            if ah % 45 == 0:
                lbl = dirs_map.get(ah, str(ah))
                p.setPen(QPen(QColor("white"), 2))
                p.drawLine(xp, CMP_H - 10, xp, CMP_H)
                p.setPen(QColor("white"))
                p.drawText(xp - len(lbl) * 4, CMP_H - 12, lbl)
            elif ah % 10 == 0:
                p.setPen(QPen(QColor("#777"), 1))
                p.drawLine(xp, CMP_H - 5, xp, CMP_H)

        # Orta yon göstergesi (kirmizi üçgen + derece kutusu)
        p.setBrush(QBrush(QColor("#e94560")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygon([QPoint(cx, CMP_H - 1),
                                 QPoint(cx - 6, CMP_H - 13),
                                 QPoint(cx + 6, CMP_H - 13)]))
        p.fillRect(cx - 23, 1, 46, 18, QColor(0, 0, 0, 220))
        p.setPen(QColor("#ffff00"))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(cx - 19, 15, f"{hdg:03d}\u00b0")

        # ── Hava hizi banti (sol) ─────────────────────────────────────────
        ty = CMP_H
        th = h - CMP_H - BOT_H
        p.fillRect(0, ty, SIDE_W, th, QColor(0, 0, 0, 155))
        p.fillRect(0, cy - 16, SIDE_W + 2, 32, QColor(0, 0, 0, 230))
        p.setPen(QColor("white"))
        p.setFont(QFont("Courier New", 16, QFont.Weight.Bold))
        p.drawText(4, cy + 7, f"{self._airspeed:.1f}")
        p.setPen(QColor("#aaa"))
        p.setFont(QFont("Courier New", 8))
        p.drawText(4, ty + 14, "AS m/s")
        p.setPen(QColor("#4ade80"))
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.drawText(4, cy - 23, f"GS {self._groundspeed:.1f}")
        p.setPen(QColor("#4adeb0"))
        p.drawText(4, cy + 33, f"VS {self._climb:+.1f}")

        # ── Yükseklik banti (sag) ─────────────────────────────────────────
        rx = w - SIDE_W
        p.fillRect(rx, ty, SIDE_W, th, QColor(0, 0, 0, 155))
        p.fillRect(rx - 2, cy - 16, SIDE_W + 2, 32, QColor(0, 0, 0, 230))
        p.setPen(QColor("white"))
        p.setFont(QFont("Courier New", 16, QFont.Weight.Bold))
        p.drawText(rx + 4, cy + 7, f"{self._alt:.1f}")
        p.setPen(QColor("#aaa"))
        p.setFont(QFont("Courier New", 8))
        p.drawText(rx + 4, ty + 14, "REL m")
        p.setPen(QColor("#f0a500"))
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.drawText(rx + 4, cy + 33, f"THR {self._throttle}%")

        # ── Alt bilgi bari ────────────────────────────────────────────────
        by = h - BOT_H
        p.fillRect(0, by, w, BOT_H, QColor(0, 0, 0, 215))

        # Batarya (sol)
        bcol = ("#4ade80" if self._batt_r > 50
                else "#ff8800" if self._batt_r > 20 else "#ff4444")
        p.setPen(QColor(bcol))
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.drawText(4, by + 16, f"Bat {self._batt_v:.1f}v {self._batt_a:.1f}A {self._batt_r}%")
        p.setPen(QColor("#aaa"))
        p.setFont(QFont("Courier New", 8))
        if self._cell_voltages:
            min_c = min(self._cell_voltages)
            cells_str = " ".join(f"{v:.2f}" for v in self._cell_voltages)
            p.drawText(4, by + 30, f"Hücre: {cells_str}  min:{min_c:.2f}v")
        else:
            cv = self._batt_v / 3.0 if self._batt_v > 0 else 0.0
            p.drawText(4, by + 30, f"Cell ~{cv:.2f}v")

        # Mod + Arm (orta)
        mc = "#ff4444" if self._armed else "#4ade80"
        p.setPen(QColor(mc))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(cx - 38, by + 16, self._flight_mode)
        arm_s = "ARM" if self._armed else "DSRM"
        p.setPen(QColor("#aaa"))
        p.setFont(QFont("Courier New", 9))
        p.drawText(cx - 38, by + 30, arm_s)

        # EKF
        ekf_ok = (self._ekf_flags & 0x1F) == 0x1F if self._ekf_flags else False
        ec = "#4ade80" if ekf_ok else "#aaa" if not self._ekf_flags else "#ff4444"
        p.setPen(QColor(ec))
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.drawText(cx + 52, by + 16, "EKF")

        # GPS (sag)
        gn = self._gps_name.upper()
        gc = "#4ade80" if ("3D" in gn or "RTK" in gn) else "#ff8800"
        p.setPen(QColor(gc))
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.drawText(w - 162, by + 16, f"GPS: {self._gps_name}")
        p.setPen(QColor("#aaa"))
        p.setFont(QFont("Courier New", 8))
        p.drawText(w - 162, by + 30, f"Sat:{self._gps_sats} HDOP:{self._hdop:.1f}")

        # Güncelleme sayaci (veri akisi göstergesi)
        tick = getattr(self, "_update_tick", 0)
        p.setPen(QColor("#333"))
        p.setFont(QFont("Courier New", 7))
        p.drawText(w - 32, by + 42, f"#{tick}")

        p.end()


# ── Hizli Veri (MP Hizli Veri Stili - Büyük Sayilar) ─────────────────────────
class QuickDataWidget(QWidget):
    _FIELDS = [
        ("alt",         "Bağıl İrtifa (m)", "#bb86fc"),
        ("groundspeed", "Yer Hizi (m/s)",  "#ffb347"),
        ("dist_wp",     "WP Mesafesi (m)", "#ff7eb3"),
        ("yaw_deg",     "Yaw (°)",         "#4ade80"),
        ("climb",       "Dikey Hiz (m/s)", "#cfff70"),
        ("heading",     "Iz Açisi (°)",    "#00e5ff"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value_labels: dict = {}
        self._build()

    def _build(self) -> None:
        g = QGridLayout(self)
        g.setSpacing(3)
        g.setContentsMargins(2, 2, 2, 2)
        for i, (key, title, col) in enumerate(self._FIELDS):
            row, c = divmod(i, 2)
            cell = QFrame()
            # cell stili QSS'ten alınacak
            fl = QVBoxLayout(cell)
            fl.setContentsMargins(4, 2, 4, 2)
            fl.setSpacing(0)
            tl = QLabel(title)
            tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tl.setStyleSheet("color:#777;font-size:9px;font-family:'Segoe UI';")
            vl = QLabel("0,00")
            vl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.setStyleSheet(
                f"color:{col};font-size:22px;font-weight:bold;font-family:'Segoe UI';"
            )
            fl.addWidget(tl)
            fl.addWidget(vl)
            self._value_labels[key] = vl
            g.addWidget(cell, row, c)

    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        def fmt(v: float) -> str:
            return f"{v:.2f}".replace(".", ",")

        self._value_labels["alt"].setText(fmt(float(data.get("alt", 0.0))))
        self._value_labels["groundspeed"].setText(fmt(float(data.get("groundspeed", 0.0))))
        self._value_labels["dist_wp"].setText("0,00")
        yaw_d = math.degrees(float(data.get("yaw", 0.0)))
        if yaw_d < 0:
            yaw_d += 360.0
        self._value_labels["yaw_deg"].setText(fmt(yaw_d))
        self._value_labels["climb"].setText(fmt(float(data.get("climb", 0.0))))
        # heading: ham float yerine tam derece (0-359). ÖNCEDEN str() ile 15 ondalık
        # gösteriliyordu ("20.1747259...").
        self._value_labels["heading"].setText(f"{int(round(float(data.get('heading', 0)))) % 360}°")


# ── Harita Widget ─────────────────────────────────────────────────────────────
class MapWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._has_web = False
        self._map_ready = False
        self._pending: tuple | None = None   # (lat, lon, hdg) — sayfa hazır olunca gönderilir
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if WEBENGINE_AVAILABLE:
            # Global profil ayarları — CDN tile isteklerine izin ver
            _prof = QWebEngineProfile.defaultProfile()
            if _prof is not None:
                _prof_s = _prof.settings()
                if _prof_s is not None:
                    _prof_s.setAttribute(
                        QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
                    _prof_s.setAttribute(
                        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
                    _prof_s.setAttribute(
                        QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            self._web = QWebEngineView()
            layout.addWidget(self._web)
            # loadFinished'ı load'dan ÖNCE bağla
            self._web.loadFinished.connect(self._on_load_finished)
            self._load_map()
            self._has_web = True
        else:
            lbl = QLabel("Harita: PyQt6-WebEngine kurulu degil")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color:#888;font-size:13px;")
            layout.addWidget(lbl)

    def _load_map(self) -> None:
        self._map_ready = False
        # qrc:// origin ile CDN tile isteklerine izin verilir
        from ui.map_assets import localize_leaflet
        self._web.setHtml(localize_leaflet(_MAP_HTML), QUrl("qrc:///"))

    def _on_load_finished(self, ok: bool) -> None:
        self._map_ready = ok
        if ok and self._pending is not None:
            lat, lon, hdg = self._pending
            self._pending = None
            self._run_js(lat, lon, hdg)

    def _run_js(self, lat: float, lon: float, hdg: float) -> None:
        page = self._web.page()
        if page is not None:
            page.runJavaScript(f"updateDrone({lat},{lon},{hdg:.1f})")

    def update_position(self, lat: float, lon: float, hdg: float = 0.0) -> None:
        if not self._has_web:
            return
        if not self._map_ready:
            self._pending = (lat, lon, hdg)   # yüklenince gönder
            return
        self._run_js(lat, lon, hdg)


# ── Dashboard Görünümü ────────────────────────────────────────────────────────
# ── ESC Telemetry 2D Premium Widget ─────────────────────────────────────────
class EscTelemetry2DWidget(QWidget):
    """Premium 2D Top-Down view of 4 motors showing ESC telemetry data."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        
        self._esc_rpm = [0, 0, 0, 0]
        self._esc_voltage = [0.0, 0.0, 0.0, 0.0]
        self._esc_current = [0.0, 0.0, 0.0, 0.0]
        self._esc_temp = [0, 0, 0, 0]
        self._total_mah = 0
        self._sys_current = 0.0
        
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(30)
        self._anim_timer.timeout.connect(self._animate)
        self._spin_angle = [0.0, 0.0, 0.0, 0.0]
        
    def showEvent(self, event):
        super().showEvent(event)
        self._anim_timer.start()
        
    def hideEvent(self, event):
        super().hideEvent(event)
        self._anim_timer.stop()
        
    def update_data(self, rpms, volts, amps, temps, mahs, sys_current):
        if rpms and len(rpms) >= 4: self._esc_rpm = rpms[:4]
        if volts and len(volts) >= 4: self._esc_voltage = volts[:4]
        if amps and len(amps) >= 4: self._esc_current = amps[:4]
        if temps and len(temps) >= 4: self._esc_temp = temps[:4]
        if mahs: self._total_mah = sum(mahs)
        if sys_current is not None: self._sys_current = sys_current
        
    def _animate(self):
        for i in range(4):
            # RPM mapping to visual speed
            speed = max(2.0, self._esc_rpm[i] / 500.0) 
            self._spin_angle[i] = (self._spin_angle[i] + speed) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w, h = self.width(), self.height()
        
        # Premium Background
        painter.fillRect(0, 0, w, h, QColor("#0d0d16"))
        
        # Frame Border
        painter.setPen(QPen(QColor(15, 52, 96, 150), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)
        
        # Titles
        painter.setPen(QColor("#00e5ff"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(12, 22, "ESC TELEMETRİ (2D)")
        
        # Top-Right Info (Total Current and Consumption) - Moved down to prevent overlap
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        info_text = f"SİSTEM AKIMI: {self._sys_current:.1f} A   |   TÜKETİM: {self._total_mah:.0f} mAh"
        fm = painter.fontMetrics()
        painter.setPen(QColor("#4ade80"))
        # Draw on the right side but slightly lower
        painter.drawText(w - fm.horizontalAdvance(info_text) - 12, 40, info_text)
        
        # Center the coordinate system
        painter.translate(w / 2, h / 2 + 10)
        
        scale = min(w, h) * 0.35
        arm_len = scale * 0.55
        
        # Drone Center Body
        body_rect = QRectF(-20, -20, 40, 40)
        painter.setPen(QPen(QColor("#00e5ff"), 2))
        painter.setBrush(QColor(15, 52, 96, 200))
        painter.drawRoundedRect(body_rect, 8, 8)
        
        # Drone Direction Arrow (North/Forward)
        painter.setBrush(QColor("#ff4444"))
        painter.setPen(Qt.PenStyle.NoPen)
        arrow_poly = QPolygon([QPoint(0, -15), QPoint(-6, -5), QPoint(6, -5)])
        painter.drawPolygon(arrow_poly)
        
        # Arms
        painter.setPen(QPen(QColor("#2a2a35"), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(int(-arm_len), int(-arm_len), int(arm_len), int(arm_len))
        painter.drawLine(int(-arm_len), int(arm_len), int(arm_len), int(-arm_len))
        
        # Positions: 1:FR, 2:BL, 3:FL, 4:BR (Quad X config)
        positions = [
            (arm_len, -arm_len),   # 1
            (-arm_len, arm_len),   # 2
            (-arm_len, -arm_len),  # 3
            (arm_len, arm_len)     # 4
        ]
        
        for i, pos in enumerate(positions):
            painter.save()
            painter.translate(pos[0], pos[1])
            
            # Motor Base
            painter.setBrush(QColor("#1a1a24"))
            painter.setPen(QPen(QColor("#00e5ff" if i in (0,2) else "#ff6666"), 2))
            painter.drawEllipse(QPointF(0, 0), 12, 12)
            
            # Spinning Propeller Effect
            painter.rotate(self._spin_angle[i])
            prop_len = arm_len * 0.8
            prop_color = QColor(0, 229, 255, 60) if i in (0, 2) else QColor(255, 102, 102, 60)
            painter.setBrush(prop_color)
            painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
            painter.drawEllipse(QPointF(0, 0), prop_len, 6)
            painter.drawEllipse(QPointF(0, 0), 6, prop_len)
            
            painter.restore()
            
            # HUD Labels (placed neatly in the corners)
            box_w, box_h = 75, 58
            # Compute label box position relative to motor
            offset_x = 25 if pos[0] > 0 else -box_w - 25
            offset_y = 20 if pos[1] > 0 else -box_h - 20
            
            box_x = pos[0] + offset_x
            box_y = pos[1] + offset_y
            
            # Glassmorphism box
            painter.setBrush(QColor(10, 10, 20, 220))
            painter.setPen(QPen(QColor("#00e5ff" if i in (0,2) else "#ff6666"), 1))
            painter.drawRoundedRect(int(box_x), int(box_y), box_w, box_h, 4, 4)
            
            painter.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
            
            # Values
            painter.setPen(QColor("#ffffff"))
            painter.drawText(int(box_x) + 5, int(box_y) + 14, f"RPM: {self._esc_rpm[i]}")
            painter.setPen(QColor("#4ade80"))
            painter.drawText(int(box_x) + 5, int(box_y) + 26, f"V  : {self._esc_voltage[i]:.1f}")
            painter.setPen(QColor("#f0a500"))
            painter.drawText(int(box_x) + 5, int(box_y) + 38, f"A  : {self._esc_current[i]:.1f}")
            painter.setPen(QColor("#ff4444"))
            painter.drawText(int(box_x) + 5, int(box_y) + 50, f"°C : {self._esc_temp[i]}")


class DashboardView(QWidget):
    send_command      = pyqtSignal(int, list)
    ned_velocity_cmd  = pyqtSignal(float, float, float)   # vx, vy, vz m/s
    video_source_changed = pyqtSignal(str)   # → video_worker.set_source
    send_servo_action = pyqtSignal(str, str, int) # target, state, angle

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_detection_time = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet("color:#e0e0e0;")
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── Sol panel (sabit 400 px, daha geniş) ──────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(400)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        # Hizli Veri
        qd_grp = QGroupBox("Hızlı Veri")
        qd_grp.setStyleSheet(
            "QGroupBox{border:1px solid #0f3460;color:#aaa;font-weight:bold;"
            "padding-top:16px;margin-top:4px;}"
        )
        qdl = QVBoxLayout(qd_grp)
        qdl.setContentsMargins(2, 2, 2, 2)
        self._quick_data = QuickDataWidget()
        qdl.addWidget(self._quick_data)
        ll.addWidget(qd_grp)

        # Hizli Komutlar
        cmd_grp = QGroupBox("Hızlı Komutlar")
        cmd_grp.setStyleSheet(
            "QGroupBox{border:1px solid #0f3460;color:#aaa;font-weight:bold;"
            "padding-top:16px;margin-top:4px;}"
            "QPushButton{border-radius:4px;padding:5px 4px;"
            "font-weight:bold;font-size:11px;}"
        )
        cgl = QVBoxLayout(cmd_grp)
        cgl.setSpacing(4)

        r1 = QHBoxLayout()
        b_arm = QPushButton("ARM")
        b_arm.setObjectName("dangerButton")
        b_arm.clicked.connect(lambda: self._arm(True))
        b_dis = QPushButton("DISARM")
        b_dis.setObjectName("dangerButton")
        b_dis.clicked.connect(lambda: self._arm(False))
        r1.addWidget(b_arm)
        r1.addWidget(b_dis)
        cgl.addLayout(r1)

        r2 = QHBoxLayout()
        self._alt_spin = QSpinBox()
        self._alt_spin.setRange(1, 120)
        self._alt_spin.setValue(10)
        self._alt_spin.setSuffix(" m")
        self._alt_spin.setStyleSheet("")
        b_to = QPushButton("TAKEOFF")
        b_to.setObjectName("successButton")
        b_to.clicked.connect(self._takeoff)
        r2.addWidget(self._alt_spin)
        r2.addWidget(b_to)
        cgl.addLayout(r2)

        r3 = QHBoxLayout()
        b_land = QPushButton("LAND")
        b_land.setObjectName("successButton")
        b_land.clicked.connect(self._land)
        b_rtl = QPushButton("RTL")
        b_rtl.setObjectName("primaryButton")
        b_rtl.clicked.connect(lambda: self._set_mode(6))
        r3.addWidget(b_land)
        r3.addWidget(b_rtl)
        cgl.addLayout(r3)

        r4 = QHBoxLayout()
        b_loi = QPushButton("LOITER")
        b_loi.clicked.connect(lambda: self._set_mode(5))
        b_pos = QPushButton("POSHOLD")
        b_pos.clicked.connect(lambda: self._set_mode(16))
        r4.addWidget(b_loi)
        r4.addWidget(b_pos)
        cgl.addLayout(r4)

        r5 = QHBoxLayout()
        b_stab = QPushButton("STABILIZE")
        b_stab.clicked.connect(lambda: self._set_mode(0))
        b_gui = QPushButton("GUIDED")
        b_gui.clicked.connect(lambda: self._set_mode(4))
        r5.addWidget(b_stab)
        r5.addWidget(b_gui)
        cgl.addLayout(r5)

        # Mod Değiştir açılır menü butonu
        b_mode_sel = QPushButton("Mod Değiştir ▼")
        b_mode_sel.setObjectName("primaryButton")
        b_mode_sel.clicked.connect(self._open_mode_menu)
        cgl.addWidget(b_mode_sel)

        ll.addWidget(cmd_grp)

        # TOF Durum Makinesi (Daha sonra sekmeye eklenecek)

        # Mesaj Sekmeler (Drone + RPi Görev)
        msg_tabs = QTabWidget()
        msg_tabs.setStyleSheet("")

        # Sekme 1: Drone / FC mesajları
        drone_tab = QWidget()
        dtl = QVBoxLayout(drone_tab)
        dtl.setContentsMargins(2, 2, 2, 2)
        self._msg_log = QTextEdit()
        self._msg_log.document().setMaximumBlockCount(500)   # sınırsız büyümeyi engelle
        self._msg_log.setReadOnly(True)
        self._msg_log.setStyleSheet(
            "color:#3daee9;font-family:'Courier New';"
            "font-size:12px;border:none;"
        )
        dtl.addWidget(self._msg_log)
        msg_tabs.addTab(drone_tab, "Drone / FC")

        # Sekme 2: RPi görev mesajları
        rpi_tab = QWidget()
        rtl = QVBoxLayout(rpi_tab)
        rtl.setContentsMargins(2, 2, 2, 2)
        # İHA canlı durum özeti (hedef tespit / yük / servo) — companion'dan gelir
        self._iha_status_lbl = QLabel("İHA durumu bekleniyor...")
        self._iha_status_lbl.setWordWrap(True)
        self._iha_status_lbl.setStyleSheet(
            "color:#8ab4f8;font-size:11px;padding:4px;"
            "background:#0d1117;border:1px solid #1f2733;border-radius:3px;"
        )
        rtl.addWidget(self._iha_status_lbl)
        self._rpi_msg_log = QTextEdit()
        self._rpi_msg_log.document().setMaximumBlockCount(500)
        self._rpi_msg_log.setReadOnly(True)
        self._rpi_msg_log.setStyleSheet(
            "color:#10b981;font-family:'Courier New';"
            "font-size:12px;border:none;"
        )
        rtl.addWidget(self._rpi_msg_log)
        msg_tabs.addTab(rpi_tab, "Yardımcı Bilgisayar")

        # Sekme 3: Guided Kontrol
        self._guided_ctrl = GuidedControlWidget()
        self._guided_ctrl.send_velocity.connect(self._on_guided_velocity)
        self._guided_ctrl.send_yaw.connect(self._on_guided_yaw)
        msg_tabs.addTab(self._guided_ctrl, "Guided")

        # Sekme 4: TOF (Durum Makinesi)
        if TofWidget:
            self._tof_widget = TofWidget()
            msg_tabs.addTab(self._tof_widget, "TOF (Durum)")

        ll.addWidget(msg_tabs, stretch=1)

        root.addWidget(left)

        # ── Orta panel (HUD + Video) ──────────────────────────────────────
        self._center_panel = QWidget()
        cl = QVBoxLayout(self._center_panel)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)

        self._hud = HUDWidget()
        cl.addWidget(self._hud, stretch=1)

        # Video — HUD ile eşit yükseklik (her ikisi stretch=1)
        vframe = QFrame()
        vframe.setMinimumHeight(140)
        vframe.setStyleSheet("background:#000;border:1px solid #3b3b45;")
        vfl = QVBoxLayout(vframe)
        vfl.setContentsMargins(0, 0, 0, 0)
        vfl.setSpacing(0)
        # Video kaynak seçim butonu (sağ üst köşe)
        _vtop = QHBoxLayout()
        _vtop.setContentsMargins(4, 2, 4, 0)
        _vtop.addStretch()
        self._video_src_btn = QPushButton("Kaynak")
        self._video_src_btn.setFixedHeight(22)
        self._video_src_btn.setObjectName("primaryButton")
        self._video_src_btn.clicked.connect(self._open_video_source_dialog)
        _vtop.addWidget(self._video_src_btn)
        vfl.addLayout(_vtop)
        self._video_label = QLabel("Video Bekleniyor...")
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setStyleSheet("color:#555;font-size:13px;")
        vfl.addWidget(self._video_label, stretch=1)
        # Video HUD'dan daha büyük olacak ve dikey alanı dolduracak
        cl.addWidget(vframe, stretch=3)

        # ── Otonom Yük & Servo (Sonsuz Derece Premium Grid Düzen) ──────────────────────
        servo_grp = QFrame()
        servo_grp.setStyleSheet("background-color:#141419; border:1px solid #2b2b35; border-radius:6px;")
        servo_lay = QGridLayout(servo_grp)
        servo_lay.setContentsMargins(12, 10, 12, 10)
        servo_lay.setSpacing(6)
        
        # Stil tanımlamaları
        lbl_style = "color:#8f90a6; font-weight:bold; font-size:11px; border:none;"
        
        # 1. Kırmızı Yük
        lbl_red = QLabel("Kırmızı Yük:")
        lbl_red.setStyleSheet(lbl_style)
        btn_drop_red_open = QPushButton("Aç")
        btn_drop_red_open.setStyleSheet("background-color:#3a1f22; color:#ff8a85; border:1px solid #7a3b3b; border-radius:6px; padding:6px; font-weight:600; font-size:11px;")
        btn_drop_red_close = QPushButton("Kapat")
        btn_drop_red_close.setStyleSheet("background-color:#1f2a33; color:#8996a3; border:1px solid #35424d; border-radius:6px; padding:6px; font-weight:600; font-size:11px;")
        
        # 2. Mavi Yük
        lbl_blue = QLabel("Mavi Yük:")
        lbl_blue.setStyleSheet(lbl_style)
        btn_drop_blue_open = QPushButton("Aç")
        btn_drop_blue_open.setStyleSheet("background-color:#182a3d; color:#7db8f0; border:1px solid #35597a; border-radius:6px; padding:6px; font-weight:600; font-size:11px;")
        btn_drop_blue_close = QPushButton("Kapat")
        btn_drop_blue_close.setStyleSheet("background-color:#1f2a33; color:#8996a3; border:1px solid #35424d; border-radius:6px; padding:6px; font-weight:600; font-size:11px;")
        
        # 3. Yük Alma Kapakları
        lbl_grab = QLabel("Yük Kapak:")
        lbl_grab.setStyleSheet(lbl_style)
        btn_grab_open = QPushButton("Aç")
        btn_grab_open.setStyleSheet("background-color:#1c3a2a; color:#6ee7a0; border:1px solid #2f6b48; border-radius:6px; padding:6px; font-weight:600; font-size:11px;")
        btn_grab_close = QPushButton("Kapat")
        btn_grab_close.setStyleSheet("background-color:#1f2a33; color:#8996a3; border:1px solid #35424d; border-radius:6px; padding:6px; font-weight:600; font-size:11px;")
        
        # 4. Gimbal
        lbl_gimbal = QLabel("Gimbal Açısı:")
        lbl_gimbal.setStyleSheet(lbl_style)
        self.gimbal_slider = QSlider(Qt.Orientation.Horizontal)
        self.gimbal_slider.setRange(30, 90)
        self.gimbal_slider.setValue(60)
        self.gimbal_slider.setStyleSheet("border:none;")
        self.lbl_gimbal_val = QLabel("60°")
        self.lbl_gimbal_val.setStyleSheet("font-weight:bold; color:#00e5ff; font-size:11px; border:none; padding-left:4px;")
        
        # Sinyal bağlantıları
        btn_drop_red_open.clicked.connect(lambda: self.send_servo_action.emit("red_drop", "open", 0))
        btn_drop_red_close.clicked.connect(lambda: self.send_servo_action.emit("red_drop", "close", 0))
        btn_drop_blue_open.clicked.connect(lambda: self.send_servo_action.emit("blue_drop", "open", 0))
        btn_drop_blue_close.clicked.connect(lambda: self.send_servo_action.emit("blue_drop", "close", 0))
        
        btn_grab_open.clicked.connect(lambda: self.send_servo_action.emit("grabber", "open", 0))
        btn_grab_close.clicked.connect(lambda: self.send_servo_action.emit("grabber", "close", 0))
        self.gimbal_slider.valueChanged.connect(lambda v: self.lbl_gimbal_val.setText(f"{v}°"))
        self.gimbal_slider.valueChanged.connect(lambda val: self.send_servo_action.emit("gimbal", "", val))
        
        # Sizing Policies to prevent cramping
        btn_height = 24
        for btn in [btn_drop_red_open, btn_drop_red_close, btn_drop_blue_open, btn_drop_blue_close, btn_grab_open, btn_grab_close]:
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setFixedHeight(btn_height)
            
        # Grid layout yerleşimi
        servo_lay.addWidget(lbl_red, 0, 0)
        servo_lay.addWidget(btn_drop_red_open, 0, 1)
        servo_lay.addWidget(btn_drop_red_close, 0, 2)
        
        servo_lay.addWidget(lbl_blue, 1, 0)
        servo_lay.addWidget(btn_drop_blue_open, 1, 1)
        servo_lay.addWidget(btn_drop_blue_close, 1, 2)
        
        servo_lay.addWidget(lbl_grab, 2, 0)
        servo_lay.addWidget(btn_grab_open, 2, 1)
        servo_lay.addWidget(btn_grab_close, 2, 2)
        
        servo_lay.addWidget(lbl_gimbal, 3, 0)
        servo_lay.addWidget(self.gimbal_slider, 3, 1)
        servo_lay.addWidget(self.lbl_gimbal_val, 3, 2)
        
        servo_lay.setColumnStretch(0, 3)
        servo_lay.setColumnStretch(1, 4)
        servo_lay.setColumnStretch(2, 4)
        
        cl.addWidget(servo_grp, stretch=0)

        # Center paneli ile sağ panel arasındaki genişlik dengesini düzelt (siyah boşlukları gidermek için 3:4 oran)
        root.addWidget(self._center_panel, stretch=3)

        # ── Sağ panel (Sekmeli Premium Panel: Harita, ESC, Tespit Geçmişi, Sağlık & Görev Adımları) ────────────
        right_tabs = QTabWidget()
        right_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #2b2b35; background: #0c0c12; border-radius: 6px; }"
            "QTabBar::tab { background: #141419; color: #8f90a6; font-weight: bold; font-size: 11px; padding: 6px 12px; border-top-left-radius: 4px; border-top-right-radius: 4px; border: 1px solid #2b2b35; border-bottom: none; margin-right: 2px; }"
            "QTabBar::tab:selected { background: #0c0c12; color: #00e5ff; border-bottom: 2px solid #00e5ff; }"
            "QTabBar::tab:hover { background: #222232; color: #fff; }"
        )

        # 1. Harita Sekmesi
        map_tab = QWidget()
        map_lay = QVBoxLayout(map_tab)
        map_lay.setContentsMargins(0, 0, 0, 0)
        self._map = MapWidget()
        map_lay.addWidget(self._map)
        right_tabs.addTab(map_tab, "Harita")

        # 2. ESC Telemetri Sekmesi
        esc_tab = QWidget()
        esc_lay = QVBoxLayout(esc_tab)
        esc_lay.setContentsMargins(0, 0, 0, 0)
        self._esc_hud = EscTelemetry2DWidget()
        esc_lay.addWidget(self._esc_hud)
        right_tabs.addTab(esc_tab, "Motor")

        # 3. Yapay Zekâ Tespit Geçmişi Sekmesi
        ai_tab = QWidget()
        ai_lay = QVBoxLayout(ai_tab)
        ai_lay.setContentsMargins(8, 8, 8, 8)
        
        lbl_ai_title = QLabel("Canlı Görüntü İşleme Hedef Algılama Kaydı")
        lbl_ai_title.setStyleSheet("color:#00e5ff; font-weight:bold; font-size:11px; border:none; background:none;")
        ai_lay.addWidget(lbl_ai_title)
        
        self._ai_scroll = QScrollArea()
        self._ai_scroll.setWidgetResizable(True)
        self._ai_scroll.setStyleSheet("background-color:#0d0d16; border:1px solid #2a2a35; border-radius:4px;")
        
        self._ai_container = QWidget()
        self._ai_container_lay = QVBoxLayout(self._ai_container)
        self._ai_container_lay.setContentsMargins(4, 4, 4, 4)
        self._ai_container_lay.setSpacing(6)
        self._ai_container_lay.addStretch() # pushing items upwards
        
        self._ai_scroll.setWidget(self._ai_container)
        ai_lay.addWidget(self._ai_scroll)
        right_tabs.addTab(ai_tab, "Hedefler")

        # 4. Sinyal & Donanım Sekmesi
        hw_tab = QWidget()
        hw_lay = QVBoxLayout(hw_tab)
        hw_lay.setContentsMargins(12, 12, 12, 12)
        hw_lay.setSpacing(10)
        
        lbl_hw_title = QLabel("Sistem Sağlığı & Sinyal Kalitesi")
        lbl_hw_title.setStyleSheet("color:#00e5ff; font-weight:bold; font-size:11px; border:none; background:none;")
        hw_lay.addWidget(lbl_hw_title)
        
        # Telemetry ve Ping Grubu
        net_grp = QGroupBox("Kablosuz Bağlantı Kalitesi")
        net_grp.setStyleSheet("color:#3daee9; font-weight:bold; border:1px solid #2a2a35; background-color:#14141d;")
        net_lay = QFormLayout(net_grp)
        net_lay.setContentsMargins(8, 12, 8, 8)
        net_lay.setVerticalSpacing(8)
        
        self._ping_bar = QProgressBar()
        self._ping_bar.setRange(0, 100)
        self._ping_bar.setValue(98)
        self._ping_bar.setFormat("%v ms")
        self._ping_bar.setStyleSheet("QProgressBar { background-color:#1a1a24; border:1px solid #2a2a35; border-radius:3px; text-align:center; color:#fff; font-weight:bold; height:18px; }"
                                     "QProgressBar::chunk { background-color:#2e7d32; }")
        
        self._rssi_bar = QProgressBar()
        self._rssi_bar.setRange(0, 100)
        self._rssi_bar.setValue(92)
        self._rssi_bar.setFormat("%p%")
        self._rssi_bar.setStyleSheet("QProgressBar { background-color:#1a1a24; border:1px solid #2a2a35; border-radius:3px; text-align:center; color:#fff; font-weight:bold; height:18px; }"
                                     "QProgressBar::chunk { background-color:#3daee9; }")
        
        self._loss_bar = QProgressBar()
        self._loss_bar.setRange(0, 100)
        self._loss_bar.setValue(0)
        self._loss_bar.setFormat("%v%")
        self._loss_bar.setStyleSheet("QProgressBar { background-color:#1a1a24; border:1px solid #2a2a35; border-radius:3px; text-align:center; color:#fff; font-weight:bold; height:18px; }"
                                     "QProgressBar::chunk { background-color:#c62828; }")
                                     
        lbl_p = QLabel("Gecikme (Ping):"); lbl_p.setStyleSheet("color:#aaa; border:none; background:none;")
        lbl_r = QLabel("Sinyal Gücü (RSSI):"); lbl_r.setStyleSheet("color:#aaa; border:none; background:none;")
        lbl_l = QLabel("Paket Kaybı:"); lbl_l.setStyleSheet("color:#aaa; border:none; background:none;")
        
        net_lay.addRow(lbl_p, self._ping_bar)
        net_lay.addRow(lbl_r, self._rssi_bar)
        net_lay.addRow(lbl_l, self._loss_bar)
        hw_lay.addWidget(net_grp)
        
        # Donanım Kaynak Grubu
        rpi_grp = QGroupBox("Yardımcı Bilgisayar (RPi 4)")
        rpi_grp.setStyleSheet("color:#10b981; font-weight:bold; border:1px solid #2a2a35; background-color:#14141d;")
        rpi_lay = QFormLayout(rpi_grp)
        rpi_lay.setContentsMargins(8, 12, 8, 8)
        rpi_lay.setVerticalSpacing(8)
        
        self._cpu_bar = QProgressBar()
        self._cpu_bar.setRange(0, 100)
        self._cpu_bar.setValue(35)
        self._cpu_bar.setStyleSheet("QProgressBar { background-color:#1a1a24; border:1px solid #2a2a35; border-radius:3px; text-align:center; color:#fff; font-weight:bold; height:18px; }"
                                    "QProgressBar::chunk { background-color:#10b981; }")
                                    
        self._ram_bar = QProgressBar()
        self._ram_bar.setRange(0, 100)
        self._ram_bar.setValue(42)
        self._ram_bar.setStyleSheet("QProgressBar { background-color:#1a1a24; border:1px solid #2a2a35; border-radius:3px; text-align:center; color:#fff; font-weight:bold; height:18px; }"
                                    "QProgressBar::chunk { background-color:#10b981; }")
                                    
        self._temp_bar = QProgressBar()
        self._temp_bar.setRange(0, 100)
        self._temp_bar.setValue(44)
        self._temp_bar.setFormat("%v°C")
        self._temp_bar.setStyleSheet("QProgressBar { background-color:#1a1a24; border:1px solid #2a2a35; border-radius:3px; text-align:center; color:#fff; font-weight:bold; height:18px; }"
                                     "QProgressBar::chunk { background-color:#f57c00; }")
                                     
        lbl_c = QLabel("CPU Kullanımı:"); lbl_c.setStyleSheet("color:#aaa; border:none; background:none;")
        lbl_rm = QLabel("RAM Kullanımı:"); lbl_rm.setStyleSheet("color:#aaa; border:none; background:none;")
        lbl_t = QLabel("İşlemci Sıcaklığı:"); lbl_t.setStyleSheet("color:#aaa; border:none; background:none;")
        
        rpi_lay.addRow(lbl_c, self._cpu_bar)
        rpi_lay.addRow(lbl_rm, self._ram_bar)
        rpi_lay.addRow(lbl_t, self._temp_bar)
        hw_lay.addWidget(rpi_grp)
        
        hw_lay.addStretch()
        right_tabs.addTab(hw_tab, "Sağlık")

        # 5. Görev Yol Haritası Sekmesi
        road_tab = QWidget()
        road_lay = QVBoxLayout(road_tab)
        road_lay.setContentsMargins(12, 12, 12, 12)
        road_lay.setSpacing(10)
        
        lbl_road_title = QLabel("Canlı Görev Yol Haritası")
        lbl_road_title.setStyleSheet("color:#00e5ff; font-weight:bold; font-size:11px; border:none; background:none;")
        road_lay.addWidget(lbl_road_title)
        
        # Yol haritası adımları
        self._road_steps = []
        steps_data = [
            ("KALKIŞ VE OTONOM ARM", "Uçuşa hazırlık ve motorların çalıştırılması"),
            ("QR TARAMA VE SONSUZ UÇUŞ", "Görev 1: Rota üzerindeki QR kodun okunması"),
            ("HEDEF ARAMA VE ALGILAMA", "Görev 2: Otonom hedef algılama safhası"),
            ("HASSAS KİLİTLENME (HoverLock)", "Görsel PID ile hedef üzerine sabitleme"),
            ("YÜK BIRAKMA İŞLEMİ", "Mekanik servolar ile yük salınımı"),
            ("YERDEN YÜK ALMA (Bonus)", "Görev 3: Barometre+ToF ile temas ve alım"),
            ("EVE DÖNÜŞ (RTL / LAND)", "Otonom güvenli iniş ve görevin tamamlanması")
        ]
        
        for idx, (name, desc) in enumerate(steps_data):
            step_box = QFrame()
            step_box.setFixedHeight(48)
            step_box.setStyleSheet("background-color:#14141d; border:1px solid #22222d; border-radius:4px;")
            
            s_lay = QHBoxLayout(step_box)
            s_lay.setContentsMargins(10, 4, 10, 4)
            s_lay.setSpacing(10)
            
            # State indicator (Tick or dot)
            lbl_indicator = QLabel("-")
            lbl_indicator.setStyleSheet("font-size:12px; border:none; background:none;")
            
            # Step Text
            t_box = QVBoxLayout()
            t_box.setSpacing(1)
            
            lbl_name = QLabel(f"{idx+1}. {name}")
            lbl_name.setStyleSheet("color:#aaa; font-weight:bold; font-size:11px; border:none; background:none;")
            lbl_desc = QLabel(desc)
            lbl_desc.setStyleSheet("color:#666; font-size:9px; border:none; background:none;")
            
            t_box.addWidget(lbl_name)
            t_box.addWidget(lbl_desc)
            
            s_lay.addWidget(lbl_indicator)
            s_lay.addLayout(t_box)
            s_lay.addStretch()
            
            road_lay.addWidget(step_box)
            self._road_steps.append((step_box, lbl_indicator, lbl_name, lbl_desc))
            
        road_lay.addStretch()
        right_tabs.addTab(road_tab, "Adımlar")

        # Sağ panelin genişliği korunarak orta panel dengesi sürdürülür
        root.addWidget(right_tabs, stretch=4)

    # ── MAVLink komutlari ──────────────────────────────────────────────────
    def _arm(self, arm: bool) -> None:
        if _MV is None:
            return
        self.send_command.emit(
            _MV.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            [1 if arm else 0, 0, 0, 0, 0, 0, 0],
        )

    def _takeoff(self) -> None:
        if _MV is None:
            return
        self.send_command.emit(
            _MV.mavlink.MAV_CMD_NAV_TAKEOFF,
            [0, 0, 0, 0, 0, 0, float(self._alt_spin.value())],
        )

    def _land(self) -> None:
        if _MV is None:
            return
        self.send_command.emit(_MV.mavlink.MAV_CMD_NAV_LAND, [0, 0, 0, 0, 0, 0, 0])

    def _set_mode(self, mode: int) -> None:
        if _MV is None:
            return
        self.send_command.emit(
            _MV.mavlink.MAV_CMD_DO_SET_MODE,
            [_MV.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode, 0, 0, 0, 0, 0],
        )

    # Tüm mod listesiyle açılır menü
    _ALL_MODES = [
        (0,  "STABILIZE"),   (1,  "ACRO"),         (2,  "ALT_HOLD"),
        (3,  "AUTO"),        (4,  "GUIDED"),        (5,  "LOITER"),
        (6,  "RTL"),         (7,  "CIRCLE"),        (9,  "LAND"),
        (11, "DRIFT"),       (13, "SPORT"),         (14, "FLIP"),
        (15, "AUTOTUNE"),    (16, "POSHOLD"),       (17, "BRAKE"),
        (18, "THROW"),       (19, "AVOID_ADSB"),    (20, "GUIDED_NOGPS"),
        (21, "SMART_RTL"),   (23, "FLOWHOLD"),      (24, "FOLLOW"),
        (25, "ZIGZAG"),
    ]

    def _open_mode_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#0d0d1e;color:#e0e0e0;border:1px solid #0f3460;}"
            "QMenu::item{padding:6px 18px;}"
            "QMenu::item:selected{background:#1a1a5e;color:#00e5ff;}"
        )
        for mode_id, mode_name in self._ALL_MODES:
            act = menu.addAction(mode_name)
            if act is not None:
                act.setData(mode_id)
        # BUG FIX: self.sender() QObject döndürür; mapToGlobal/rect QWidget metodudur.
        # isinstance kontrolü ile güvenli cast yapılır.
        sender_widget = self.sender()
        if not isinstance(sender_widget, QWidget):
            chosen = menu.exec()
        else:
            chosen = menu.exec(sender_widget.mapToGlobal(
                sender_widget.rect().bottomLeft()
            ))
        if chosen is not None:
            self._set_mode(chosen.data())

    # ── Slotlar ───────────────────────────────────────────────────────────
    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        self._quick_data.update_telemetry(data)
        self._hud.update_telemetry(data)
        
        # ESC verilerini HUD'a gönder
        rpms = data.get("esc_rpm")
        volts = data.get("esc_voltage")
        amps = data.get("esc_current")
        temps = data.get("esc_temp")
        mahs = data.get("esc_mah")
        sys_current = data.get("battery_current", 0.0)
        
        # Her durumda güncelle ki en azından sistem akımını gösterebilelim
        self._esc_hud.update_data(rpms, volts, amps, temps, mahs, sys_current)

        # Harita güncellemesi
        lat = data.get("lat")
        lon = data.get("lon")
        if (lat is not None and lon is not None
                and abs(float(lat)) > 0.001):
            hdg_val = data.get("hdg", data.get("heading", 0))
            self._map.update_position(
                float(lat), float(lon), float(hdg_val)
            )

        # ── Kablosuz Bağlantı Kalitesi: GERÇEK veriler ───────────────────────
        # NOT: CPU/RAM/Sıcaklık gerçek companion akışından update_rpi_status()'ta,
        # Ping ise LAN RTT ölçümünden (RpiStatusWorker) gelir. Burada FC telemetrisi:
        try:
            # RSSI — RC alıcısının GERÇEK sinyal gücü (RC_CHANNELS.rssi, 0-254; 255=veri yok)
            rssi_raw = data.get("rc_rssi")
            if rssi_raw is not None:
                if rssi_raw == 255:
                    self._rssi_bar.setValue(0)   # RC sinyali yok (ör. tezgâhta verici kapalı)
                else:
                    self._rssi_bar.setValue(int(max(0, min(100, rssi_raw / 254.0 * 100))))
            # Paket Kaybı — FC'nin bildirdiği GERÇEK haberleşme düşme oranı (% = c%/100)
            drop = data.get("drop_rate_comm")
            if drop is not None:
                self._loss_bar.setValue(int(max(0, min(100, drop / 100.0))))
        except Exception:
            pass

        # ── Görev Adımları Canlı Güncellemesi ──────────────────────────────
        try:
            self.update_mission_roadmap(data)
        except Exception:
            pass

    def add_ai_detection(self, data: dict) -> None:
        """Yeni bir yapay zekâ hedef tespit kartı ekler."""
        from PyQt6.QtCore import QDateTime
        
        label = data.get("label", "hedef")
        lat = data.get("lat", 0.0)
        lon = data.get("lon", 0.0)
        conf = data.get("confidence", data.get("conf", 0.85)) * 100
        
        # Zaman damgasını güncelle
        self._last_detection_time = QDateTime.currentDateTime()
        ts = self._last_detection_time.toString("hh:mm:ss")
        
        # Renk kodlaması (Kırmızı/Mavi)
        accent_color = "#e24c4c" if "red" in label.lower() or "kirmizi" in label.lower() else "#3daee9" if "blue" in label.lower() or "mavi" in label.lower() else "#ffaa00"
        
        card = QFrame()
        card.setFixedHeight(62)
        card.setStyleSheet(f"background-color:#1e1e2d; border:1px solid #2a2a3f; border-left: 4px solid {accent_color}; border-radius:4px;")
        
        clay = QVBoxLayout(card)
        clay.setContentsMargins(8, 4, 8, 4)
        clay.setSpacing(2)
        
        # Üst Satır: Başlık ve Zaman
        r1 = QHBoxLayout()
        lbl_head = QLabel(f"{label.upper()} (%{conf:.0f})")
        lbl_head.setStyleSheet("color:#ffffff; font-weight:bold; font-size:11px; border:none; background:none;")
        lbl_ts = QLabel(ts)
        lbl_ts.setStyleSheet("color:#777; font-size:10px; border:none; background:none;")
        r1.addWidget(lbl_head)
        r1.addStretch()
        r1.addWidget(lbl_ts)
        
        # Alt Satır: Koordinatlar
        lbl_coord = QLabel(f"Enlem: {lat:.6f} | Boylam: {lon:.6f}")
        lbl_coord.setStyleSheet("color:#aaa; font-family:'Courier New'; font-size:10px; border:none; background:none;")
        
        clay.addLayout(r1)
        clay.addWidget(lbl_coord)
        
        # Kartı en üste ekle (spacer'ın hemen önüne)
        self._ai_container_lay.insertWidget(self._ai_container_lay.count() - 1, card)

    def update_mission_roadmap(self, data: dict) -> None:
        """Telemetri durumuna göre görev adımlarını canlı olarak günceller."""
        is_armed = data.get("armed", False)
        alt = data.get("alt", 0.0)
        mode = data.get("mode", "STABILIZE")
        wp_idx = data.get("wp_index", 0)
        
        # Uçuş aşamalarını analiz et
        statuses = ["pending"] * 7
        
        if is_armed:
            statuses[0] = "completed"
            statuses[1] = "active"
        else:
            statuses[0] = "active"
            
        if is_armed and alt > 1.5:
            statuses[0] = "completed"
            statuses[1] = "completed"
            statuses[2] = "active"
            
        if wp_idx >= 2:
            statuses[1] = "completed"
            statuses[2] = "completed"
            statuses[3] = "active"
            
        # Eğer yakın zamanda AI hedef tespiti olduysa
        if self._last_detection_time is not None:
            statuses[2] = "completed"
            statuses[3] = "completed"
            statuses[4] = "active"
            
        # Eğer mod RTL veya LAND ise görevin son adımı
        if mode in ["RTL", "LAND"]:
            statuses[0] = "completed"
            statuses[1] = "completed"
            statuses[2] = "completed"
            statuses[3] = "completed"
            statuses[4] = "completed"
            statuses[5] = "completed"
            statuses[6] = "active"
            if alt < 0.3 and not is_armed:
                statuses[6] = "completed"
                
        # UI Elemanlarını Güncelle
        for idx, (step_box, lbl_indicator, lbl_name, lbl_desc) in enumerate(self._road_steps):
            stat = statuses[idx]
            if stat == "completed":
                lbl_indicator.setText("✓")
                lbl_indicator.setStyleSheet("color:#10b981; font-weight:bold; font-size:12px; border:none; background:none;")
                lbl_name.setStyleSheet("color:#10b981; font-weight:bold; font-size:11px; border:none; background:none;")
                lbl_desc.setStyleSheet("color:#4e8869; font-size:9px; border:none; background:none;")
                step_box.setStyleSheet("background-color:#0b1e15; border:1px solid #10b981; border-radius:4px;")
            elif stat == "active":
                lbl_indicator.setText("▶")
                lbl_indicator.setStyleSheet("color:#00e5ff; font-weight:bold; font-size:12px; border:none; background:none;")
                lbl_name.setStyleSheet("color:#ffffff; font-weight:bold; font-size:11px; border:none; background:none;")
                lbl_desc.setStyleSheet("color:#00e5ff; font-size:9px; border:none; background:none;")
                step_box.setStyleSheet("background-color:#0d1e26; border:1px solid #00e5ff; border-radius:4px;")
            else:
                lbl_indicator.setText("-")
                lbl_indicator.setStyleSheet("color:#555; border:none; background:none;")
                lbl_name.setStyleSheet("color:#aaa; font-weight:bold; font-size:11px; border:none; background:none;")
                lbl_desc.setStyleSheet("color:#666; font-size:9px; border:none; background:none;")
                step_box.setStyleSheet("background-color:#14141d; border:1px solid #22222d; border-radius:4px;")

    @pyqtSlot(str, int)
    def add_status_message(self, text: str, severity: int) -> None:
        _C = {
            0: "#ff4444", 1: "#ff4444", 2: "#ff4444",
            3: "#ff8800", 4: "#ffbb00", 5: "#e0e0e0",
            6: "#4ade80", 7: "#888888",
        }
        _N = {
            0: "EMRG", 1: "ALRT", 2: "CRIT", 3: "ERR ",
            4: "WARN", 5: "NOTE", 6: "INFO", 7: "DBG ",
        }
        col = _C.get(severity, "#e0e0e0")
        nm  = _N.get(severity, "MSG ")
        self._msg_log.append(f'<span style="color:{col}">[{nm}] {text}</span>')
        sb = self._msg_log.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    @pyqtSlot(str)
    def add_rpi_mission_message(self, text: str) -> None:
        """RPi companion'dan gelen görev mesajlarını siyan renkte gösterir."""
        from PyQt6.QtCore import QDateTime
        ts = QDateTime.currentDateTime().toString("hh:mm:ss")
        self._rpi_msg_log.append(f'<span style="color:#00e5ff">[{ts}] {text}</span>')
        sb = self._rpi_msg_log.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    @pyqtSlot(dict)
    def update_rpi_status(self, data: dict) -> None:
        """Companion durum JSON'undan İHA canlı durumunu görselleştir:
        TOF mesafeleri (TofWidget), hedef tespit, yük, servo durumları."""
        # ── Sağlık paneli: GERÇEK companion sistem metrikleri (psutil, 5602) ──
        # cpu_pct/mem_pct/temp_c companion status_server._sys_stats()'tan gelir.
        # ping_ms RpiStatusWorker'ın LAN RTT ölçümünden eklenir.
        try:
            cpu = data.get("cpu_pct")
            mem = data.get("mem_pct")
            tmp = data.get("temp_c")
            png = data.get("ping_ms")
            if cpu is not None:
                self._cpu_bar.setValue(int(max(0, min(100, round(cpu)))))
            if mem is not None:
                self._ram_bar.setValue(int(max(0, min(100, round(mem)))))
            if tmp is not None:
                self._temp_bar.setValue(int(max(0, min(120, round(tmp)))))
            if png is not None:
                self._ping_bar.setValue(int(max(0, min(100, round(png)))))
        except Exception:
            pass

        # ── TOF widget'ı besle (Görev 3 yük alma görselleştirmesi) ──────────
        tof = data.get("tof_distances", {})
        if tof and getattr(self, "_tof_widget", None) is not None:
            try:
                self._tof_widget.update_tof_data(tof)
            except Exception:
                pass

        # ── Hedef tespit / yük / servo özeti ────────────────────────────────
        vis     = data.get("vision_status", {}) or {}
        servo   = data.get("servo_state", {}) or {}
        payload = data.get("payload_loaded", False)

        detected = bool(vis.get("target_detected", False))
        cam_ok   = bool(vis.get("connected", False))
        det_txt  = "🎯 HEDEF VAR"   if detected else "hedef yok"
        det_col  = "#10b981"        if detected else "#888"
        cam_txt  = "kamera ✓"       if cam_ok   else "<span style='color:#ef4444'>kamera ✗</span>"
        pl_txt   = ("<span style='color:#f59e0b'>YÜK VAR</span>" if payload else "yük yok")

        def _s(v):
            return "—" if v is None else str(v)
        gim  = _s(servo.get("gimbal_angle"))
        grab = _s(servo.get("grabber"))
        rdr  = _s(servo.get("red_drop"))
        bdr  = _s(servo.get("blue_drop"))

        # ── FC görev belleği sahipliği (E2) ─────────────────────────────────
        fc = data.get("fc_mission") or {}
        fc_line = ""
        if fc:
            fc_line = (f"<br><span style='color:#aaa'>FC görevi:</span> "
                       f"{fc.get('type','?')} {fc.get('wp_count','?')} WP "
                       f"<span style='color:#888'>({fc.get('source','?')})</span>")

        # ── Companion MAVLink görüşü (E6) — çapraz kontrol / radyo-kopuk fallback ──
        mav = data.get("mav", {}) or {}
        mav_line = ""
        if mav:
            batt = mav.get("battery", {}) or {}
            bv = batt.get("v")
            armed = mav.get("armed")
            arm_txt = ("<span style='color:#ef4444'>ARMED</span>" if armed
                       else "DISARM")
            mav_line = (f"<br><span style='color:#666'>İHA(companion): "
                        f"{mav.get('mode','?')} {arm_txt} "
                        f"{('%.1fV' % bv) if isinstance(bv,(int,float)) else ''}</span>")

        self._iha_status_lbl.setText(
            f"<b style='color:{det_col}'>{det_txt}</b> &nbsp;|&nbsp; {cam_txt} "
            f"&nbsp;|&nbsp; {pl_txt}<br>"
            f"<span style='color:#aaa'>Gimbal:</span>{gim}° "
            f"<span style='color:#aaa'>Grab:</span>{grab} "
            f"<span style='color:#aaa'>Kırmızı:</span>{rdr} "
            f"<span style='color:#aaa'>Mavi:</span>{bdr}"
            f"{fc_line}{mav_line}"
        )

    @pyqtSlot(QImage)
    def update_video(self, q_img: QImage) -> None:
        if q_img.isNull():
            self._video_label.setPixmap(QPixmap())
            self._video_label.setText("Yeniden baglaniyor...")
            return
            
        px = QPixmap.fromImage(q_img)

        # Orta paneli video en-boy oranına göre ayarla — ama YALNIZCA oran DEĞİŞİNCE.
        # ÖNCEDEN her karede ideal_w = label.height()*aspect hesaplanıyordu; label
        # yüksekliği layout'tan etkilendiği için bu, video fps'inde sürekli relayout
        # (ve salınım) tetikliyordu. Video çözünürlüğü akış boyunca sabit → oran sabit →
        # tek sefer ayarla, sonra dokunma.
        h = self._video_label.height()
        if h > 0 and q_img.height() > 0:
            aspect = round(q_img.width() / q_img.height(), 3)
            if aspect != getattr(self, "_last_video_aspect", None):
                self._last_video_aspect = aspect
                self._video_label.setMinimumWidth(0)
                self._video_label.setMaximumWidth(16777215)
                self._center_panel.setFixedWidth(int(h * aspect))
                
        # Scale with KeepAspectRatio to be safe, but since parent width is matched, there will be no black bars!
        self._video_label.setPixmap(
            px.scaled(
                self._video_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # ── Guided Hız Komutu ─────────────────────────────────────────────────────
    @pyqtSlot(float, float, float)
    def _on_guided_velocity(self, vx: float, vy: float, vz: float) -> None:
        """SET_POSITION_TARGET_LOCAL_NED ile hız komutu gönder."""
        # ned_velocity_cmd sinyali → MavlinkWorker.send_ned_velocity
        self.ned_velocity_cmd.emit(vx, vy, vz)

    # ── Guided Yaw Komutu ─────────────────────────────────────────────────────
    @pyqtSlot(float, bool)
    def _on_guided_yaw(self, yaw_deg: float, relative: bool) -> None:
        if _MV is None:
            return
        self.send_command.emit(
            _MV.mavlink.MAV_CMD_CONDITION_YAW,
            [
                abs(yaw_deg),               # hedef açı
                30.0,                        # dönüş hızı (deg/s)
                1.0 if yaw_deg >= 0 else -1.0,  # yön: 1=saat, -1=tersi
                1.0 if relative else 0.0,    # 1=göreceli
                0.0, 0.0, 0.0,
            ],
        )

    # ── Video Kaynak Diyalogu ─────────────────────────────────────────────────
    def _open_video_source_dialog(self) -> None:
        dlg = VideoSourceDialog(self)
        dlg.source_selected.connect(self.video_source_changed)
        dlg.exec()
