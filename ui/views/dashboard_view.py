"""
dashboard_view.py — Modül 1 (Mission Planner Stil)
Sol  : Hızlı Veri (büyük sayılar) + Hizli Komutlar + Guided Kontrol + Drone Mesajlari
Orta : MP-Stil HUD (compass tape, pitch merdiveni, taraf bantlari, alt bilgi)
Sag  : 3D Drone Modeli (sag üst) + Esri harita (sag alt)
Alt  : Video akisi (HUD ile esit yükseklik)
"""
import time
import math
import logging
from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QPoint, QUrl, QTimer, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPixmap,
    QFont, QFontMetrics, QImage, QPolygon, QTransform,
)
from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame,
    QSizePolicy, QPushButton, QTextEdit, QSpinBox, QDoubleSpinBox,
    QGroupBox, QGridLayout, QDialog, QComboBox, QLineEdit,
    QDialogButtonBox, QFormLayout, QMenu, QTabWidget, QSlider, QScrollArea, QProgressBar,
    QCheckBox
)

from core import theme
from core.gcs_config import config


# ── RTK GPS yorumlama tablosu ─────────────────────────────────────────────────
# NEDEN: HDOP/PDOP/VDOP ham sayı olarak operatöre bir şey ifade etmiyor —
# GNSS camiasında standart kabul gören eşikler burada (bkz. u-blox/Trimble DOP
# tabloları). Fix kalitesi + DOP birlikte TEK bir "genel değerlendirme"
# cümlesine indirgenir. (Eskiden ui/views/antenna_tracker_view.py'deydi —
# 2026-08-28 anten takip sistemi kaldırılınca buraya taşındı, RTK paneli
# antenna tracker'dan bağımsız.)
def _dop_rating(dop: float) -> tuple[str, str]:
    """DOP değerini (İyi/Orta/...) + tema rengi anahtarına çevirir."""
    if dop < 1.0:  return "İdeal", "success"
    if dop < 2.0:  return "Mükemmel", "success"
    if dop < 5.0:  return "İyi", "accent"
    if dop < 10.0: return "Orta", "warning"
    if dop < 20.0: return "Zayıf", "danger"
    return "Kullanılamaz", "danger"


# fix_quality kodu -> (etiket, tema rengi, yaklaşık hassasiyet açıklaması)
_RTK_FIX_VERDICT: dict[str, tuple[str, str, str]] = {
    "0": ("Konum yok",      "danger",  "fix alınamıyor — uydu görüşünü kontrol edin"),
    "1": ("Sıradan GPS",    "warning", "~1-3 m hassasiyet — hassas görev için yetersiz"),
    "2": ("DGPS",           "accent",  "~1 m hassasiyet"),
    "3": ("PPS",            "accent",  "askeri/hassas kod"),
    "4": ("RTK FIXED",      "success", "~1-2 cm hassasiyet — en iyi durum"),
    "5": ("RTK FLOAT",      "accent",  "~10-50 cm — düzeltme var, henüz sabitlenmedi"),
    "6": ("Dead Reckoning", "warning", "uydu kaybı — atalet tahmini kullanılıyor"),
}

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

# ── Aviyonik semantik renkler — temadan bağımsız ─────────────────────────────
# NEDEN: PFD/HUD ve motor göstergesi renkleri uçuş aleti standardıdır: gökyüzü
# mavi, yer yeşil, ufuk çizgisi beyaz, uçak referansı sarı, pusula ibresi
# kırmızı, saat yönü / ters yön rotorlar camgöbeği / kırmızı. Bunlar tema
# değişse de AYNI kalır — operatör aleti renginden tanır. HUD üzerindeki yarı
# saydam siyah bantlar ve o bantlara yazılan beyaz/gri metinler de bu gruba
# girer (açık temada da bant koyu kalır, metin okunur olur).
HUD_SKY          = "#1a6ea0"   # aviyonik semantik — temadan bağımsız
HUD_GROUND       = "#4a6e28"   # aviyonik semantik — temadan bağımsız
HUD_HORIZON      = "#ffffff"   # aviyonik semantik — temadan bağımsız
HUD_AIRCRAFT     = "#ffff00"   # sarı uçak referansı + yön kutusu — aviyonik semantik
HUD_ROLL_PTR     = "#ff6600"   # roll göstergesi üçgeni — aviyonik semantik
HUD_COMPASS_PTR  = "#e94560"   # pusula kırmızı ibresi — aviyonik semantik
HUD_TEXT         = "#ffffff"   # bant üstü metin — aviyonik semantik
HUD_TEXT_DIM     = "#aaaaaa"   # bant üstü ikincil metin — aviyonik semantik
HUD_TICK         = "#777777"   # ara pusula çentiği — aviyonik semantik
HUD_TICK_FAINT   = "#333333"   # veri akış sayacı — aviyonik semantik
HUD_GS           = "#4ade80"   # yer hızı — aviyonik semantik
HUD_VS           = "#4adeb0"   # dikey hız — aviyonik semantik
HUD_THR          = "#f0a500"   # gaz — aviyonik semantik
HUD_OK           = "#4ade80"   # normal durum — aviyonik semantik
HUD_WARN         = "#ff8800"   # uyarı durumu — aviyonik semantik
HUD_CRIT         = "#ff4444"   # kritik durum — aviyonik semantik
# HUD bantları (yarı saydam siyah) — aviyonik semantik, temadan bağımsız
HUD_BAND_TOP     = QColor(0, 0, 0, 175)   # pusula bandı
HUD_BAND_SIDE    = QColor(0, 0, 0, 155)   # hız / irtifa bandı
HUD_BAND_FOCUS   = QColor(0, 0, 0, 230)   # bant ortasındaki değer kutusu
HUD_BAND_HDG     = QColor(0, 0, 0, 220)   # yön derecesi kutusu
HUD_BAND_BOTTOM  = QColor(0, 0, 0, 215)   # alt bilgi bandı

# Motor (ESC) göstergesi — pervane dönüş yönü renkleri de aviyonik semantiktir
ESC_CW           = "#00e5ff"   # saat yönü rotor — aviyonik semantik
ESC_CCW          = "#ff6666"   # ters yön rotor — aviyonik semantik
ESC_ARROW        = "#ff4444"   # burun (ileri) oku — aviyonik semantik
ESC_BODY_FILL    = QColor(15, 52, 96, 200)    # gövde dolgusu — aviyonik semantik
ESC_CW_PROP      = QColor(0, 229, 255, 60)    # saat yönü pervane izi — aviyonik
ESC_CCW_PROP     = QColor(255, 102, 102, 60)  # ters yön pervane izi — aviyonik
ESC_PROP_EDGE    = QColor(255, 255, 255, 40)  # pervane kenarı — aviyonik
ESC_MOTOR_BASE   = "#1a1a24"   # motor gövdesi — aviyonik semantik
ESC_LABEL_BOX    = QColor(10, 10, 20, 220)    # HUD etiket kutusu — aviyonik
ESC_LBL_RPM      = "#ffffff"   # aviyonik semantik
ESC_LBL_VOLT     = "#4ade80"   # aviyonik semantik
ESC_LBL_AMP      = "#f0a500"   # aviyonik semantik
ESC_LBL_TEMP     = "#ff4444"   # aviyonik semantik

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
ASHINA_addBaseLayers(map);   // altlık + katman seçici + ölçek + çözünürlük göstergesi (ui/map_assets.py)

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
var stationIcon = L.divIcon({html:'<div style="background:#38bdf8;color:#00232e;font-weight:bold;font-size:10px;padding:1px 5px;border-radius:3px;border:1px solid #0369a1;">📡 RTK</div>',iconSize:[52,18],iconAnchor:[26,9],className:''});

var drone = L.marker([39.925,32.866],{icon:makeDroneIcon(0)}).addTo(map);
var trail = []; var trailLine = L.polyline([],{color:'#4ade80',weight:2,opacity:0.8}).addTo(map);
var homeSet = false; var homeMarker = null;
var isFirstFix = true;
// İstasyon (RTK GPS) konumu — İHA'dan BAĞIMSIZ, kendi ilk fix'inde bir kez
// yerleştirilir ve o andan sonra sabit kalır (yer istasyonu hareket etmez).
var stationMarker = null; var stationSet = false;

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
function updateStation(lat,lon){
  if(!stationSet){
    stationMarker=L.marker([lat,lon],{icon:stationIcon}).addTo(map);
    stationSet=true;
    // İHA henüz bağlı değilse harita ilk RTK fix'ine ortalansın.
    if(isFirstFix){ map.setView([lat,lon], 17); }
  } else {
    stationMarker.setLatLng([lat,lon]);
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
        # NOT: Savunmacı "background:transparent" satırı kaldırıldı — QSS v3
        # zaten QFrame/QLabel zeminini şeffaf tutuyor (şartname §0).
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # NEDEN: Panel eskiden "Guided Kontrol (m / derece)" başlığıyla " m" sonekli bir
        # "Mesafe" kutusu gösteriyordu, ama handler bu değeri OKUYUP KULLANMIYOR; sabit
        # 1.5 m/s HIZ komutu (SET_POSITION_TARGET_LOCAL_NED velocity) emit ediyordu.
        # Hız komutu yeni komut/DUR gelene kadar geçerli olduğundan operatör "2 m" yazıp
        # İleri'ye bastığında araç süresiz 1.5 m/s ile gidiyordu (10 sn = 15 m).
        # Komut kanalı gerçekte HIZ tabanlı olduğu için paneli gerçeğe uygun hale
        # getiriyoruz: kutu artık mesafe değil HIZ (m/s) giriyor.
        grp = QGroupBox("Guided Kontrol (m/s / derece)")   # stil: global QSS
        gl = QVBoxLayout(grp)
        gl.setSpacing(3)

        # Hız girişi (eski adı "Mesafe" idi — birim karışıklığı yaratıyordu)
        dist_row = QHBoxLayout()
        dist_row.addWidget(QLabel("Hız:"))
        self._dist_spin = QDoubleSpinBox()   # isim korundu (dış bağımlılık kırılmasın)
        self._dist_spin.setRange(0.2, 5.0)   # NEDEN: makul guided hız aralığı
        self._dist_spin.setValue(1.0)
        self._dist_spin.setSuffix(" m/s")
        self._dist_spin.setDecimals(1)
        self._dist_spin.setSingleStep(0.1)
        self._dist_spin.setToolTip("Guided hız komutu büyüklüğü (m/s). "
                                   "Komut, DUR'a basılana kadar sürer.")
        self._dist_spin.setFixedWidth(80)
        dist_row.addWidget(self._dist_spin)
        dist_row.addStretch()
        gl.addLayout(dist_row)

        # Yön butonları (3x3 grid)
        dir_grid = QGridLayout()
        dir_grid.setSpacing(3)
        # Yön butonları accent vurgulu ikincil eylem → QSS "ghostButton" class'ı
        # (eski satır-içi teal stil kaldırıldı; tema ile birlikte değişir).
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
                btn.setObjectName("warningButton")
            else:
                btn.setObjectName("ghostButton")
            # dx: sağa (+), dy: ileri (+), NED: x=north, y=east
            spd_spin = self._dist_spin
            vx_, vy_ = dy, dx   # NED: forward=x, right=y
            # NEDEN: Bu bir HIZ komutudur ve yenisi/DUR gelene kadar geçerlidir.
            # Operatör bunu bilmeli — aksi halde "2 m gider" sanıp araç uçup gider.
            if lbl == "■ Dur":
                btn.setToolTip("Tüm hareketi durdur — hız komutunu (0,0,0) yapar.")
            else:
                btn.setToolTip("Guided hız komutu gönderir. "
                               "Komut, DUR'a basılana kadar sürer.")
            def _make_handler(_vx, _vy):
                def _clicked():
                    # NEDEN: sabit 1.5 m/s yerine kullanıcının girdiği HIZ kullanılır;
                    # kutu değeri artık gerçekten komuta yansıyor.
                    spd = float(spd_spin.value())
                    if _vx == 0 and _vy == 0:
                        self._stop()   # "■ Dur" → (0,0,0)
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
        b_up.setObjectName("successButton")
        b_dn = QPushButton("▼ Aşağı")
        b_dn.setObjectName("dangerButton")
        # NEDEN: Önceden METRE değeri /3.0 ile bölünüp m/s yerine konuyordu (birim
        # karışıklığı). Kutu artık m/s tuttuğu için bölme kaldırıldı; NED işaret
        # mantığı korundu (yukarı = negatif vz).
        b_up.setToolTip("Yukarı hız komutu. Komut, DUR'a basılana kadar sürer.")
        b_dn.setToolTip("Aşağı hız komutu. Komut, DUR'a basılana kadar sürer.")
        b_up.clicked.connect(lambda: self.send_velocity.emit(0.0, 0.0, -float(self._dist_spin.value())))
        b_dn.clicked.connect(lambda: self.send_velocity.emit(0.0, 0.0,  float(self._dist_spin.value())))
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
        self._yaw_spin.setFixedWidth(70)
        b_yaw_l = QPushButton("↺ Sol")
        b_yaw_l.setObjectName("ghostButton")
        b_yaw_r = QPushButton("↻ Sağ")
        b_yaw_r.setObjectName("ghostButton")
        b_yaw_l.clicked.connect(lambda: self.send_yaw.emit(-abs(self._yaw_spin.value()), True))
        b_yaw_r.clicked.connect(lambda: self.send_yaw.emit( abs(self._yaw_spin.value()), True))
        yaw_row.addWidget(self._yaw_spin)
        yaw_row.addWidget(b_yaw_l)
        yaw_row.addWidget(b_yaw_r)
        gl.addLayout(yaw_row)

        root.addWidget(grp)
        root.addStretch()   # kontrolleri üste topla; alttaki ölü boşluğu tek noktada tut

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
        self._info_lbl.setObjectName("mutedLabel")   # statik renk → QSS class
        self._info_lbl.setWordWrap(True)
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
        # NOT: Zemin satır-içi stylesheet'i kaldırıldı — paintEvent zaten tüm
        # dikdörtgeni HUD_SKY ile dolduruyor (özel paintEvent'te QSS zemini
        # hiç çizilmez, bu satır ölü koddu).
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

        # ── Gökyüzü/Yer arka plan (aviyonik semantik — temadan bağımsız) ──
        p.fillRect(0, 0, w, h, QColor(HUD_SKY))   # varsayilan gökyüzü
        p.save()
        p.translate(cx, cy)
        p.rotate(roll_deg)
        p.fillRect(-w * 2, -h * 2, w * 4, h * 2 + pitch_px, QColor(HUD_SKY))
        p.fillRect(-w * 2, pitch_px, w * 4, h * 2, QColor(HUD_GROUND))
        p.setPen(QPen(QColor(HUD_HORIZON), 2))
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
                p.setPen(QPen(QColor(HUD_TEXT), 1))
                p.drawLine(-lw, py, lw, py)
                p.setPen(QColor(HUD_TEXT))
                p.drawText(lw + 4, py + 4, str(deg))
                p.drawText(-lw - 22, py + 4, str(deg))
            else:
                lw = 24
                p.setPen(QPen(QColor(HUD_TEXT), 1))
                p.drawLine(-lw, py, lw, py)
        p.restore()

        # ── Sabit ucak referansi (sari V) ────────────────────────────────
        p.setPen(QPen(QColor(HUD_AIRCRAFT), 3))
        p.drawLine(cx - 65, cy,      cx - 18, cy)
        p.drawLine(cx - 18, cy,      cx,      cy + 12)
        p.drawLine(cx,      cy + 12, cx + 18, cy)
        p.drawLine(cx + 18, cy,      cx + 65, cy)
        p.setBrush(QBrush(QColor(HUD_AIRCRAFT)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx - 4, cy - 4, 8, 8)

        # ── Roll yay (keman) ─────────────────────────────────────────────
        arc_r = min(w, h) // 2 - 2
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(HUD_TEXT), 1))
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
        p.setBrush(QBrush(QColor(HUD_ROLL_PTR)))
        p.setPen(Qt.PenStyle.NoPen)
        pr = -arc_r
        p.drawPolygon(QPolygon([QPoint(0, pr + 2),
                                 QPoint(-7, pr + 16),
                                 QPoint(7,  pr + 16)]))
        p.restore()

        # ── Pusula banti (üst) ───────────────────────────────────────────
        p.fillRect(0, 0, w, CMP_H, HUD_BAND_TOP)
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
                p.setPen(QPen(QColor(HUD_TEXT), 2))
                p.drawLine(xp, CMP_H - 10, xp, CMP_H)
                p.setPen(QColor(HUD_TEXT))
                p.drawText(xp - len(lbl) * 4, CMP_H - 12, lbl)
            elif ah % 10 == 0:
                p.setPen(QPen(QColor(HUD_TICK), 1))
                p.drawLine(xp, CMP_H - 5, xp, CMP_H)

        # Orta yon göstergesi (kirmizi üçgen + derece kutusu)
        p.setBrush(QBrush(QColor(HUD_COMPASS_PTR)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygon([QPoint(cx, CMP_H - 1),
                                 QPoint(cx - 6, CMP_H - 13),
                                 QPoint(cx + 6, CMP_H - 13)]))
        p.fillRect(cx - 23, 1, 46, 18, HUD_BAND_HDG)
        p.setPen(QColor(HUD_AIRCRAFT))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(cx - 19, 15, f"{hdg:03d}\u00b0")

        # ── Hava hizi banti (sol) ─────────────────────────────────────────
        ty = CMP_H
        th = h - CMP_H - BOT_H
        p.fillRect(0, ty, SIDE_W, th, HUD_BAND_SIDE)
        p.fillRect(0, cy - 16, SIDE_W + 2, 32, HUD_BAND_FOCUS)
        p.setPen(QColor(HUD_TEXT))
        p.setFont(QFont("Courier New", 16, QFont.Weight.Bold))
        p.drawText(4, cy + 7, f"{self._airspeed:.1f}")
        p.setPen(QColor(HUD_TEXT_DIM))
        p.setFont(QFont("Courier New", 8))
        p.drawText(4, ty + 14, "AS m/s")
        p.setPen(QColor(HUD_GS))
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.drawText(4, cy - 23, f"GS {self._groundspeed:.1f}")
        p.setPen(QColor(HUD_VS))
        p.drawText(4, cy + 33, f"VS {self._climb:+.1f}")

        # ── Yükseklik banti (sag) ─────────────────────────────────────────
        rx = w - SIDE_W
        p.fillRect(rx, ty, SIDE_W, th, HUD_BAND_SIDE)
        p.fillRect(rx - 2, cy - 16, SIDE_W + 2, 32, HUD_BAND_FOCUS)
        p.setPen(QColor(HUD_TEXT))
        p.setFont(QFont("Courier New", 16, QFont.Weight.Bold))
        p.drawText(rx + 4, cy + 7, f"{self._alt:.1f}")
        p.setPen(QColor(HUD_TEXT_DIM))
        p.setFont(QFont("Courier New", 8))
        p.drawText(rx + 4, ty + 14, "REL m")
        p.setPen(QColor(HUD_THR))
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.drawText(rx + 4, cy + 33, f"THR {self._throttle}%")

        # ── Alt bilgi bari ────────────────────────────────────────────────
        by = h - BOT_H
        p.fillRect(0, by, w, BOT_H, HUD_BAND_BOTTOM)

        # ── Adaptif alt bar ──────────────────────────────────────────────────
        # Dört grup (Batarya | Mod | EKF | GPS) eskiden SABİT x offset'lerine
        # çiziliyordu; PFD daralınca (pencere min 1280px → orta kolon ~280px)
        # üst üste biniyorlardı ("12.0GUIDED", "EKGPS"). Artık her metnin
        # genişliği QFontMetrics ile ölçülür: Batarya sola, GPS sağa yaslanır,
        # aradaki boşluğa Mod ve EKF ancak SIĞDIĞI kadar yerleştirilir. Yer
        # yoksa zarifçe elenir (Mod/EKF üst çubukta da görünür) — çakışma yok.
        f_bat = QFont("Courier New", 9, QFont.Weight.Bold)
        f_sub = QFont("Courier New", 8)
        f_mode = QFont("Courier New", 11, QFont.Weight.Bold)
        f_ekf = QFont("Courier New", 9, QFont.Weight.Bold)
        fm_bat = QFontMetrics(f_bat)
        fm_sub = QFontMetrics(f_sub)
        fm_mode = QFontMetrics(f_mode)
        fm_ekf = QFontMetrics(f_ekf)

        # Batarya (sol)
        bcol = (HUD_OK if self._batt_r > 50
                else HUD_WARN if self._batt_r > 20 else HUD_CRIT)
        bat_txt = f"Bat {self._batt_v:.1f}v {self._batt_a:.1f}A {self._batt_r}%"
        if self._cell_voltages:
            min_c = min(self._cell_voltages)
            cells_str = " ".join(f"{v:.2f}" for v in self._cell_voltages)
            bat_sub = f"Hücre: {cells_str}  min:{min_c:.2f}v"
        else:
            cv = self._batt_v / 3.0 if self._batt_v > 0 else 0.0
            bat_sub = f"Cell ~{cv:.2f}v"
        p.setPen(QColor(bcol))
        p.setFont(f_bat)
        p.drawText(4, by + 16, bat_txt)
        p.setPen(QColor(HUD_TEXT_DIM))
        p.setFont(f_sub)
        p.drawText(4, by + 30, bat_sub)
        bat_right = 4 + max(fm_bat.horizontalAdvance(bat_txt),
                            fm_sub.horizontalAdvance(bat_sub))

        # GPS (sağ) — iki satırın en genişine göre ortak sol kenar
        gn = self._gps_name.upper()
        gc = HUD_OK if ("3D" in gn or "RTK" in gn) else HUD_WARN
        gps_txt = f"GPS: {self._gps_name}"
        sat_txt = f"Sat:{self._gps_sats} HDOP:{self._hdop:.1f}"
        gps_w = fm_bat.horizontalAdvance(gps_txt)
        sat_w = fm_sub.horizontalAdvance(sat_txt)
        gps_x = w - max(gps_w, sat_w) - 8
        p.setPen(QColor(gc))
        p.setFont(f_bat)
        p.drawText(gps_x, by + 16, gps_txt)
        p.setPen(QColor(HUD_TEXT_DIM))
        p.setFont(f_sub)
        p.drawText(gps_x, by + 30, sat_txt)

        # Mod + EKF — batarya ile GPS arasındaki boşluğa sığdığı kadar
        span_l = bat_right + 10
        span_r = gps_x - 10
        span_w = span_r - span_l
        mode_w = fm_mode.horizontalAdvance(self._flight_mode)
        ekf_w = fm_ekf.horizontalAdvance("EKF")
        mc = HUD_CRIT if self._armed else HUD_OK
        arm_s = "ARM" if self._armed else "DSRM"
        ekf_ok = (self._ekf_flags & 0x1F) == 0x1F if self._ekf_flags else False
        ec = HUD_OK if ekf_ok else HUD_TEXT_DIM if not self._ekf_flags else HUD_CRIT

        if span_w >= mode_w + ekf_w + 12:
            # İkisi de sığar: Mod sol tarafa, EKF sağ tarafa dağıtılır
            mode_x = span_l
            p.setPen(QColor(mc)); p.setFont(f_mode)
            p.drawText(mode_x, by + 16, self._flight_mode)
            p.setPen(QColor(HUD_TEXT_DIM)); p.setFont(f_sub)
            p.drawText(mode_x, by + 30, arm_s)
            p.setPen(QColor(ec)); p.setFont(f_ekf)
            p.drawText(span_r - ekf_w, by + 16, "EKF")
        elif span_w >= mode_w:
            # Sadece Mod sığar → boşlukta ortala (EKF üst çubukta zaten var)
            mode_x = span_l + (span_w - mode_w) // 2
            p.setPen(QColor(mc)); p.setFont(f_mode)
            p.drawText(mode_x, by + 16, self._flight_mode)
            p.setPen(QColor(HUD_TEXT_DIM)); p.setFont(f_sub)
            p.drawText(mode_x, by + 30, arm_s)
        # aksi halde: aşırı dar — Mod/EKF elenir (üst çubukta görünür)

        # Güncelleme sayaci (veri akisi göstergesi)
        tick = getattr(self, "_update_tick", 0)
        p.setPen(QColor(HUD_TICK_FAINT))
        p.setFont(QFont("Courier New", 7))
        p.drawText(w - 32, by + 42, f"#{tick}")

        p.end()


# ── Hizli Veri (MP Hizli Veri Stili - Büyük Sayilar) ─────────────────────────
class QuickDataWidget(QWidget):
    # NOT: Üçüncü alan eskiden alan başına ham hex renkti ama HİÇ kullanılmıyordu
    # (tüm değerler tek accent renk — şartname §1). Yapı korunuyor, renk yerine
    # tema token'ı yazıldı ki ileride kullanılırsa theme.c(...) ile çözülsün.
    _FIELDS = [
        ("alt",         "Bağıl İrtifa (m)", "accent"),
        ("groundspeed", "Yer Hizi (m/s)",  "accent"),
        ("dist_wp",     "WP Mesafesi (m)", "accent"),
        ("yaw_deg",     "Yaw (°)",         "accent"),
        ("climb",       "Dikey Hiz (m/s)", "accent"),
        ("heading",     "Iz Açisi (°)",    "accent"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value_labels: dict = {}
        self._build()

    def _build(self) -> None:
        g = QGridLayout(self)
        g.setSpacing(6)   # kartlar arası 6px (şartname §2)
        g.setContentsMargins(6, 6, 6, 6)
        for i, (key, title, _col) in enumerate(self._FIELDS):
            row, c = divmod(i, 2)
            cell = QFrame()
            # cell stili QSS'ten alınacak
            fl = QVBoxLayout(cell)
            fl.setContentsMargins(6, 6, 6, 6)   # kart iç dolgusu min 6px
            fl.setSpacing(0)
            tl = QLabel(title)
            tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tl.setWordWrap(True)   # uzun TR başlıklar kırpılmasın, alt satıra sarsın
            tl.setObjectName("mutedLabel")   # statik renk → QSS class
            vl = QLabel("0,00")
            vl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # Büyük accent değer → QSS "valueLabel" class'ı (18px bold accent).
            # Tüm değerler tek accent renk (şartname §1) — alan başına renk yok.
            vl.setObjectName("valueLabel")
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
        self._pending_station: tuple | None = None   # (lat, lon) — istasyon konumu
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
            # NEDEN ertelenmiş: setHtml() ilk kez çağrıldığında WebEngine motoru
            # (Chromium alt süreci) devreye girer — Pi'de bu ~1-2s sürüyor ve
            # ana pencere DAHA GÖRÜNMEDEN (MainWindow.__init__ içinde, _build_ui
            # sırasında) bu maliyeti üstleniyordu. singleShot ile pencere
            # zaten ekrandayken (event loop döndükten sonra) yüklenir.
            QTimer.singleShot(200, self._load_map)
            self._has_web = True
        else:
            lbl = QLabel("Harita: PyQt6-WebEngine kurulu degil")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setObjectName("mutedLabel")   # statik renk → QSS class
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
        if ok and self._pending_station is not None:
            lat, lon = self._pending_station
            self._pending_station = None
            self._run_station_js(lat, lon)

    def _run_js(self, lat: float, lon: float, hdg: float) -> None:
        page = self._web.page()
        if page is not None:
            page.runJavaScript(f"updateDrone({lat},{lon},{hdg:.1f})")

    def _run_station_js(self, lat: float, lon: float) -> None:
        page = self._web.page()
        if page is not None:
            page.runJavaScript(f"updateStation({lat},{lon})")

    def update_position(self, lat: float, lon: float, hdg: float = 0.0) -> None:
        if not self._has_web:
            return
        if not self._map_ready:
            self._pending = (lat, lon, hdg)   # yüklenince gönder
            return
        self._run_js(lat, lon, hdg)

    def update_station(self, lat: float, lon: float) -> None:
        """Yer istasyonu (RTK GPS) konumu — İHA'dan bağımsız, ayrı işaretçi."""
        if not self._has_web:
            return
        if not self._map_ready:
            self._pending_station = (lat, lon)
            return
        self._run_station_js(lat, lon)


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

        # Panel zemini ve çerçevesi TEMADAN gelir (paint sırasında okunur —
        # __init__'te cachelenmez, tema değişince otomatik doğru renge geçer).
        painter.fillRect(0, 0, w, h, QColor(theme.c("chart_bg")))

        # Frame Border
        painter.setPen(QPen(QColor(theme.c("border")), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

        # Titles
        painter.setPen(QColor(theme.c("accent")))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(12, 22, "ESC TELEMETRİ (2D)")

        # Top-Right Info (Total Current and Consumption) - Moved down to prevent overlap
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        info_text = f"SİSTEM AKIMI: {self._sys_current:.1f} A   |   TÜKETİM: {self._total_mah:.0f} mAh"
        fm = painter.fontMetrics()
        painter.setPen(QColor(theme.c("success")))
        # Draw on the right side but slightly lower
        painter.drawText(w - fm.horizontalAdvance(info_text) - 12, 40, info_text)
        
        # Center the coordinate system
        painter.translate(w / 2, h / 2 + 10)
        
        scale = min(w, h) * 0.35
        arm_len = scale * 0.55
        
        # Drone Center Body (gövde + burun oku aviyonik semantik)
        body_rect = QRectF(-20, -20, 40, 40)
        painter.setPen(QPen(QColor(ESC_CW), 2))
        painter.setBrush(ESC_BODY_FILL)
        painter.drawRoundedRect(body_rect, 8, 8)

        # Drone Direction Arrow (North/Forward)
        painter.setBrush(QColor(ESC_ARROW))
        painter.setPen(Qt.PenStyle.NoPen)
        arrow_poly = QPolygon([QPoint(0, -15), QPoint(-6, -5), QPoint(6, -5)])
        painter.drawPolygon(arrow_poly)

        # Arms — panel zemini üstünde çizildiği için TEMA rengi kullanır
        painter.setPen(QPen(QColor(theme.c("text_muted")), 6,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
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
            
            # Motor Base — halka rengi dönüş yönünü gösterir (aviyonik semantik)
            painter.setBrush(QColor(ESC_MOTOR_BASE))
            painter.setPen(QPen(QColor(ESC_CW if i in (0, 2) else ESC_CCW), 2))
            painter.drawEllipse(QPointF(0, 0), 12, 12)

            # Spinning Propeller Effect
            painter.rotate(self._spin_angle[i])
            prop_len = arm_len * 0.8
            prop_color = ESC_CW_PROP if i in (0, 2) else ESC_CCW_PROP
            painter.setBrush(prop_color)
            painter.setPen(QPen(ESC_PROP_EDGE, 1))
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
            
            # Glassmorphism box — HUD kromu: koyu kutu + parlak metin, açık
            # temada da okunur kalsın diye aviyonik sabit olarak korunur.
            painter.setBrush(ESC_LABEL_BOX)
            painter.setPen(QPen(QColor(ESC_CW if i in (0, 2) else ESC_CCW), 1))
            painter.drawRoundedRect(int(box_x), int(box_y), box_w, box_h, 4, 4)

            painter.setFont(QFont("Consolas", 8, QFont.Weight.Bold))

            # Values
            painter.setPen(QColor(ESC_LBL_RPM))
            painter.drawText(int(box_x) + 5, int(box_y) + 14, f"RPM: {self._esc_rpm[i]}")
            painter.setPen(QColor(ESC_LBL_VOLT))
            painter.drawText(int(box_x) + 5, int(box_y) + 26, f"V  : {self._esc_voltage[i]:.1f}")
            painter.setPen(QColor(ESC_LBL_AMP))
            painter.drawText(int(box_x) + 5, int(box_y) + 38, f"A  : {self._esc_current[i]:.1f}")
            painter.setPen(QColor(ESC_LBL_TEMP))
            painter.drawText(int(box_x) + 5, int(box_y) + 50, f"°C : {self._esc_temp[i]}")


class DashboardView(QWidget):
    send_command      = pyqtSignal(int, list)
    ned_velocity_cmd  = pyqtSignal(float, float, float)   # vx, vy, vz m/s
    video_source_changed = pyqtSignal(str)   # → video_worker.set_source
    speak_requested   = pyqtSignal(str)      # sesli otopilot → TTS worker
    send_servo_action = pyqtSignal(str, str, int, bool) # target, state, angle, hold_torque

    # NEDEN: Tespit kartları sınırsız birikip bellek/layout yükü yaratıyordu.
    _MAX_AI_CARDS = 50   # ekranda tutulacak en yeni tespit kartı sayısı

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_detection_time = None
        # Fix: kart referansları — eskiyenler layout'tan çıkarılıp silinsin
        self._ai_cards: list = []
        # Fix: yol haritası son uygulanan durum listesi (gereksiz stylesheet parse'ı önler)
        self._last_roadmap_statuses = None
        # Kırmızı/mavi manuel PWM slider debounce'u — son gönderilen değerler
        self._last_red_drop_pwm_sent = None
        self._last_blue_drop_pwm_sent = None
        # Fix: sağlık göstergeleri gerçek veri gelene kadar "—" gösterir
        self._hw_seen = {"ping": False, "rssi": False, "loss": False,
                         "cpu": False, "ram": False, "temp": False}
        # Tema değişince İHA durum etiketini aynı veriyle yeniden kurmak için
        self._last_rpi_status: dict | None = None
        self._health_bars: list = []
        self._build_ui()
        # Kurulumda yazılan satır-içi renkler tema değişince bayat kalır —
        # sinyale bağlanıp hepsini son duruma göre yeniden yazıyoruz.
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ── Satır-içi stil üreticileri (tek renk kaynağı: theme.c) ─────────────
    @staticmethod
    def _log_css(token: str) -> str:
        """Mesaj günlüğü (QTextEdit) — mono yazı + anlamsal metin rengi."""
        return (f"color:{theme.c(token)};font-family:'Courier New';"
                f"font-size:12px;border:none;")

    @staticmethod
    def _video_frame_css() -> str:
        """Video çerçevesi — 'kaynak yok' zemini tema kuyusundan gelir."""
        return (f"QFrame#videoFrame{{background:{theme.c('well')};"
                f"border:1px solid {theme.c('border')};}}")

    @staticmethod
    def _servo_panel_css() -> str:
        return (f"QFrame#servoPanel{{background-color:{theme.c('panel')};"
                f"border:1px solid {theme.c('border')};border-radius:8px;}}")

    @staticmethod
    def _gimbal_val_css() -> str:
        """Slider yanindaki accent deger etiketi stili.

        AD TARİHSEL: gimbal slider'ı için yazılmıştı, gimbal 2026-08-30'da
        söküldü. Kırmızı/mavi yük bırakma PWM slider'ları hâlâ kullanıyor,
        o yüzden fonksiyon duruyor."""
        return (f"font-weight:bold;color:{theme.c('accent')};"
                f"font-size:11px;padding-left:4px;")

    @staticmethod
    def _bar_chunk_css(token: str) -> str:
        """Yalnız dolgu rengi — çubuk gövdesi global QSS'ten gelir."""
        return f"QProgressBar::chunk{{background-color:{theme.c(token)};}}"

    @staticmethod
    def _det_card_css(token: str) -> str:
        """Tespit kartı — sol kenar rengi tespit sınıfına göre (dinamik)."""
        return (f"QFrame#detCard{{background-color:{theme.c('panel')};"
                f"border:1px solid {theme.c('border')};"
                f"border-left:4px solid {theme.c(token)};border-radius:4px;}}")

    @staticmethod
    def _step_box_css(bg_token: str, border_token: str) -> str:
        return (f"QFrame#panelFrame{{background-color:{theme.c(bg_token)};"
                f"border:1px solid {theme.c(border_token)};border-radius:4px;}}")

    def _apply_health_bar_colors(self) -> None:
        """Sağlık çubuklarının anlamsal dolgu renklerini (yeniden) yazar."""
        for bar, token in self._health_bars:
            bar.setStyleSheet(self._bar_chunk_css(token))

    def _apply_theme_colors(self) -> None:
        """Tema değişince TÜM satır-içi renkleri son duruma göre yeniden yazar.

        NEDEN: Satır-içi stiller kurulumda BİR KEZ yazılır; tema değişince eski
        paletle bayat kalırlar. Burada hepsi güncel palete göre tazelenir."""
        self._msg_log.setStyleSheet(self._log_css("info"))
        self._rpi_msg_log.setStyleSheet(self._log_css("success"))
        self._video_frame.setStyleSheet(self._video_frame_css())
        self._servo_panel.setStyleSheet(self._servo_panel_css())
        self._apply_health_bar_colors()
        # Yol haritası: son bilinen durum listesiyle yeniden boyanır
        self._apply_roadmap_styles(
            self._last_roadmap_statuses or ["pending"] * len(self._road_steps)
        )
        # Tespit kartları: her kart kendi anlam token'ını taşır
        for card in self._ai_cards:
            token = getattr(card, "_accent_token", "warning")
            card.setStyleSheet(self._det_card_css(token))
        # İHA durum etiketi HTML renkleri: son veriyle yeniden kurulur
        if self._last_rpi_status is not None:
            self.update_rpi_status(self._last_rpi_status)

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Sol panel (sabit 340 px — şartname §4) ────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(340)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        # Hizli Veri
        # NOT: Yerel QGroupBox stil override'ı SİLİNDİ — başlık/kenarlık/dolgu
        # artık global QSS'ten gelir (şartname §0).
        qd_grp = QGroupBox("Hızlı Veri")
        qdl = QVBoxLayout(qd_grp)
        qdl.setContentsMargins(6, 6, 6, 6)
        self._quick_data = QuickDataWidget()
        qdl.addWidget(self._quick_data)
        ll.addWidget(qd_grp)

        # RTK GPS (F9P) — kompakt özet, ayrıntılı panel "Anten Takip" sekmesinde.
        rtk_grp = QGroupBox("RTK GPS")
        rtk_form = QFormLayout(rtk_grp)
        rtk_form.setContentsMargins(6, 6, 6, 6)
        rtk_form.setSpacing(2)
        self._rtk_lbl_conn2 = QLabel("● bağlı değil")
        self._rtk_lbl_conn2.setStyleSheet(f"color:{theme.c('text_muted')};")
        rtk_form.addRow(self._rtk_lbl_conn2)
        self._rtk_lbl_verdict2 = QLabel("—")
        self._rtk_lbl_verdict2.setWordWrap(True)
        _rf = self._rtk_lbl_verdict2.font(); _rf.setBold(True); self._rtk_lbl_verdict2.setFont(_rf)
        rtk_form.addRow(self._rtk_lbl_verdict2)
        self._rtk_lbl_pos2 = QLabel("—")
        self._rtk_lbl_pos2.setObjectName("monoLabel")
        rtk_form.addRow("Konum:", self._rtk_lbl_pos2)
        # NTRIP (TUSAGA-Aktif vb.) düzeltme kaynağı durumu — F9P'nin kendi
        # NMEA fix'inden AYRI: bu, İHA'ya giden RTCM'in NEREDEN geldiğini
        # gösterir (2026-08-28, kullanıcı talebi — NTRIP entegrasyonuyla
        # birlikte eklendi, bkz. main_window._on_start_ntrip).
        self._ntrip_lbl_status = QLabel("NTRIP: ● bağlı değil")
        self._ntrip_lbl_status.setStyleSheet(f"color:{theme.c('text_muted')};")
        rtk_form.addRow(self._ntrip_lbl_status)
        ll.addWidget(rtk_grp)

        # Hizli Komutlar (grup + buton stilleri global QSS'ten — şartname §0)
        cmd_grp = QGroupBox("Hızlı Komutlar")
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
        msg_tabs.setMinimumHeight(150)
        # 400 px sol panelde 4 alt-sekme kaydırma oku olmadan sığsın:
        # kısa etiketler + dar padding + kaydırma butonlarını kapat.
        msg_tabs.setUsesScrollButtons(False)
        msg_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        _msg_tabbar = msg_tabs.tabBar()
        if _msg_tabbar is not None:
            _msg_tabbar.setExpanding(False)
        msg_tabs.setStyleSheet(
            "QTabBar::tab{padding:4px 8px;font-size:10px;margin-right:1px;}"
        )

        # Sekme 1: Drone / FC mesajları
        drone_tab = QWidget()
        dtl = QVBoxLayout(drone_tab)
        dtl.setContentsMargins(6, 6, 6, 6)
        self._msg_log = QTextEdit()
        self._msg_log.document().setMaximumBlockCount(500)   # sınırsız büyümeyi engelle
        self._msg_log.setReadOnly(True)
        self._msg_log.setStyleSheet(self._log_css("info"))
        dtl.addWidget(self._msg_log, stretch=1)
        msg_tabs.addTab(drone_tab, "Drone/FC")

        # Sekme 2: RPi görev mesajları
        rpi_tab = QWidget()
        rtl = QVBoxLayout(rpi_tab)
        rtl.setContentsMargins(6, 6, 6, 6)
        # İHA canlı durum özeti (hedef tespit / yük / servo) — companion'dan gelir
        self._iha_status_lbl = QLabel("İHA durumu bekleniyor...")
        self._iha_status_lbl.setWordWrap(True)
        # Kutu görünümlü bilgi etiketi → QSS "hintLabel" class'ı (zemin/kenar/renk
        # temadan gelir; içerideki durum renkleri HTML span'lerinde dinamiktir).
        self._iha_status_lbl.setObjectName("hintLabel")
        rtl.addWidget(self._iha_status_lbl)
        self._rpi_msg_log = QTextEdit()
        self._rpi_msg_log.document().setMaximumBlockCount(500)
        self._rpi_msg_log.setReadOnly(True)
        self._rpi_msg_log.setStyleSheet(self._log_css("success"))
        rtl.addWidget(self._rpi_msg_log, stretch=1)
        msg_tabs.addTab(rpi_tab, "Yardımcı")

        # Sekme 3: Guided Kontrol
        self._guided_ctrl = GuidedControlWidget()
        self._guided_ctrl.send_velocity.connect(self._on_guided_velocity)
        self._guided_ctrl.send_yaw.connect(self._on_guided_yaw)
        msg_tabs.addTab(self._guided_ctrl, "Guided")

        # Sekme 4: TOF (Durum Makinesi)
        if TofWidget:
            self._tof_widget = TofWidget()
            msg_tabs.addTab(self._tof_widget, "TOF")

        ll.addWidget(msg_tabs, stretch=1)

        root.addWidget(left)

        # ── Orta panel (HUD + Video) ──────────────────────────────────────
        self._center_panel = QWidget()
        cl = QVBoxLayout(self._center_panel)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)

        self._hud = HUDWidget()
        cl.addWidget(self._hud, stretch=2)

        # Video — HUD ile eşit yükseklik (her ikisi stretch=1)
        vframe = QFrame()
        vframe.setObjectName("videoFrame")
        vframe.setMinimumHeight(140)
        # ID seçicisi: bare stylesheet çocuklara yayılıp "Kaynak" butonunun
        # primaryButton stilini eziyordu. Zemin/kenar artık tema token'ından.
        self._video_frame = vframe
        vframe.setStyleSheet(self._video_frame_css())
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
        self._video_label.setObjectName("mutedLabel")   # statik renk → QSS class
        vfl.addWidget(self._video_label, stretch=1)
        # Video HUD'dan daha büyük olacak ve dikey alanı dolduracak
        cl.addWidget(vframe, stretch=3)

        # ── Otonom Yük & Servo (Sonsuz Derece Premium Grid Düzen) ──────────────────────
        servo_grp = QFrame()
        # NOT: objectName "servoPanel" DEĞİŞMEZ — main_window RBAC kilidi bu adı
        # arayarak paneli devre dışı bırakıyor.
        servo_grp.setObjectName("servoPanel")
        # Seçicisiz (bare) stylesheet çocuklara da yayılıp butonların
        # dangerButton/primaryButton/successButton QSS class'larını eziyordu —
        # ID seçicisiyle yalnızca panelin kendisine kapsamlandı.
        self._servo_panel = servo_grp
        servo_grp.setStyleSheet(self._servo_panel_css())
        servo_lay = QGridLayout(servo_grp)
        servo_lay.setContentsMargins(12, 10, 12, 10)
        servo_lay.setSpacing(6)

        # Satır etiketleri: statik soluk metin → QSS "mutedLabel" class'ı

        # ── ETİKET NETLİĞİ (2026-08-30 şartname denetimi) ──────────────────
        # Şartname s.1674-1676: yük rengi hedef renginin TERSİDİR — mavi
        # altıgene KIRMIZI yük, kırmızı üçgene MAVİ yük bırakılır. Servo adı
        # "red_drop" HEDEFE göredir (kırmızı hedefe bırakan göz), taşıdığı
        # yük MAVİDİR. Eski "Kırmızı Yük" etiketi operatörü yük rengine
        # yönlendiriyordu; servo adları/protokol DEĞİŞMEDİ, yalnız görünen
        # metinler netleştirildi.
        _capraz_tip = (
            "Şartname s.1674-1676: yük rengi hedef renginin TERSİDİR —\n"
            "mavi altıgene KIRMIZI yük, kırmızı üçgene MAVİ yük bırakılır.\n"
            "Servo adı (red_drop/blue_drop) HEDEF rengine göredir."
        )

        # 1. Kırmızı hedef gözü (red_drop) — MAVİ yükü taşır
        lbl_red = QLabel("Kırmızı hedef gözü — MAVİ yük:")
        lbl_red.setObjectName("mutedLabel")
        lbl_red.setToolTip(_capraz_tip)
        btn_drop_red_open = QPushButton("Yük Bırak")
        btn_drop_red_open.setObjectName("dangerButton")
        btn_drop_red_open.setToolTip(_capraz_tip)
        btn_drop_red_close = QPushButton("Yük Al")   # nötr — global QSS

        # 2. Mavi hedef gözü (blue_drop) — KIRMIZI yükü taşır
        lbl_blue = QLabel("Mavi hedef gözü — KIRMIZI yük:")
        lbl_blue.setObjectName("mutedLabel")
        lbl_blue.setToolTip(_capraz_tip)
        btn_drop_blue_open = QPushButton("Yük Bırak")
        btn_drop_blue_open.setObjectName("primaryButton")
        btn_drop_blue_open.setToolTip(_capraz_tip)
        btn_drop_blue_close = QPushButton("Yük Al")   # nötr — global QSS
        
        # 3. Yük Alma Kapakları
        lbl_grab = QLabel("Yük Kapak:")
        lbl_grab.setObjectName("mutedLabel")
        btn_grab_open = QPushButton("Aç")
        btn_grab_open.setObjectName("successButton")
        btn_grab_close = QPushButton("Kapat")   # nötr — global QSS

        # 1b/2b. Manuel PWM slider'ları — yük ELLE takılırken/çıkarılırken
        # Aç/Yük Al'ın tek seferlik SIÇRAMASI (pwm_open<->pwm_close) yerine
        # kademeli, gözle takip edilebilir hareket sağlar (kullanıcı talebi,
        # 2026-08-27: ani sıçrama mekanizmayı/yükü zorluyor/kırıyordu).
        # NEDEN state tracking'i BOZMAZ: bu yol companion'da _state_*'i
        # GÜNCELLEMEZ (bkz. ServoController.set_drop_pwm_raw) — yükleme
        # bittiğinde operatör hâlâ "Yük Al" butonuna basmalı; o buton hem
        # servoyu TAM pwm_close'a taşır (slider'ın bıraktığı ara PWM'den
        # değil) hem de sisteme "yük yüklendi" durumunu resmen kaydeder.
        lbl_red_manual = QLabel("Kırmızı Göz Manuel:")
        lbl_red_manual.setObjectName("mutedLabel")
        lbl_red_manual.setToolTip(
            "red_drop servosunun ham PWM'i (kırmızı HEDEFE bırakan göz — "
            "MAVİ yükü taşır).\n" + _capraz_tip)
        self.red_drop_slider = QSlider(Qt.Orientation.Horizontal)
        self.red_drop_slider.setRange(200, 2000)
        self.red_drop_slider.setValue(1500)
        self.lbl_red_drop_pwm_val = QLabel("1500 µs")
        self.lbl_red_drop_pwm_val.setStyleSheet(self._gimbal_val_css())

        lbl_blue_manual = QLabel("Mavi Göz Manuel:")
        lbl_blue_manual.setObjectName("mutedLabel")
        lbl_blue_manual.setToolTip(
            "blue_drop servosunun ham PWM'i (mavi HEDEFE bırakan göz — "
            "KIRMIZI yükü taşır).\n" + _capraz_tip)
        self.blue_drop_slider = QSlider(Qt.Orientation.Horizontal)
        self.blue_drop_slider.setRange(200, 2000)
        self.blue_drop_slider.setValue(1500)
        self.lbl_blue_drop_pwm_val = QLabel("1500 µs")
        self.lbl_blue_drop_pwm_val.setStyleSheet(self._gimbal_val_css())

        # 3b. Minimal tutma torku seçeneği — kavrayıcı sürekli-dönüş servosu
        # limit switch'e ulaştıktan sonra nötr (pwm_stop) bırakılırsa hiçbir
        # tutma kuvveti kalmaz; gözlemlenen kullanıcı arızası: kapak açıkken
        # kendiliğinden yavaşça kapalı konuma sürüklendi. İşaretlenirse hem
        # açık hem kapalı uçta çok küçük bir PWM sapması (config.json
        # grabber_switches.hold_torque_pwm_delta) limit switch'e karşı
        # sürdürülür — varsayılan KAPALI (opt-in, sürekli enerjilenmiş motor
        # istenmeyen bir durumdur, operatör bilerek açmalı).
        lbl_hold = QLabel("Tutma Torku:")
        lbl_hold.setObjectName("mutedLabel")
        self.chk_grab_hold_torque = QCheckBox("Minimal tutma torku (açık/kapalı)")

        # 4. Gimbal
        # NEDEN 15-90/varsayılan 90 (2026-08-27, saha testiyle güncellendi):
        # aralık artık companion hardware/servo_controller.py'deki GERÇEK
        # nominal sınırlarla (GIMBAL_NOMINAL_MIN=15, GIMBAL_NOMINAL_MAX=90)
        # birebir eşleşiyor — eski 30-90 aralığı bu servonun (EMAX ES3252)
        # gerçek fiziksel/yazılımsal limitlerini yansıtmıyordu. 90°=NADİR
        # (yere dik) — gimbal artık açılışta otomatik bu konuma gidiyor
        # (bkz. main.py _home_servos_on_startup), slider varsayılanı da
        # gerçek başlangıç durumuyla eşleşsin diye 90'da başlıyor.
        # 2026-08-30: GİMBAL AÇI KONTROLÜ KALDIRILDI — araçta gimbal YOK.
        # Kamera gövdeye sabit ve daima nadire (90°) bakıyor; ayarlanacak
        # açı kalmadı. Araç tutumunun telafisi yazılımda yapılıyor
        # (İHA missions/task2/camera_pose.py).
        
        # Sinyal bağlantıları
        # NEDEN onay ("Aç" butonları): yük bırakma/kapak açma GERİ ALINAMAZ —
        # yanlış tıklama yükü havada, hedef dışında bırakır. "Kapat" onaysız
        # kalır (zararsız/geri alınabilir). Aynı desen top_bar.py._on_drop_click.
        # Onay diyaloğu etiketi de çapraz-yük kuralını söyler (şartname
        # s.1674-1676) — operatör bırakma ANINDA hangi renk yükün düşeceğini
        # görsün.
        btn_drop_red_open.clicked.connect(
            lambda: self._confirm_and_emit_servo(
                "red_drop", "open", 0, "Kırmızı hedef gözü (MAVİ yük)")
        )
        btn_drop_red_close.clicked.connect(lambda: self.send_servo_action.emit("red_drop", "close", 0, False))
        btn_drop_blue_open.clicked.connect(
            lambda: self._confirm_and_emit_servo(
                "blue_drop", "open", 0, "Mavi hedef gözü (KIRMIZI yük)")
        )
        btn_drop_blue_close.clicked.connect(lambda: self.send_servo_action.emit("blue_drop", "close", 0, False))

        btn_grab_open.clicked.connect(
            lambda: self._confirm_and_emit_servo(
                "grabber", "open", 0, "Yük Kapak", self.chk_grab_hold_torque.isChecked())
        )
        btn_grab_close.clicked.connect(
            lambda: self.send_servo_action.emit(
                "grabber", "close", 0, self.chk_grab_hold_torque.isChecked())
        )

        # Kırmızı/mavi manuel PWM slider'ları — gimbal ile AYNI debounce deseni
        # (sliderReleased anında, klavye/tekerlek 250ms debounce sonrası).
        self.red_drop_slider.valueChanged.connect(
            lambda v: self.lbl_red_drop_pwm_val.setText(f"{v} µs"))
        self.red_drop_slider.sliderReleased.connect(self._send_red_drop_pwm_cmd)
        self._red_drop_debounce = QTimer(self)
        self._red_drop_debounce.setSingleShot(True)
        self._red_drop_debounce.setInterval(250)
        self._red_drop_debounce.timeout.connect(self._send_red_drop_pwm_cmd)
        self.red_drop_slider.valueChanged.connect(lambda _v: self._red_drop_debounce.start())

        self.blue_drop_slider.valueChanged.connect(
            lambda v: self.lbl_blue_drop_pwm_val.setText(f"{v} µs"))
        self.blue_drop_slider.sliderReleased.connect(self._send_blue_drop_pwm_cmd)
        self._blue_drop_debounce = QTimer(self)
        self._blue_drop_debounce.setSingleShot(True)
        self._blue_drop_debounce.setInterval(250)
        self._blue_drop_debounce.timeout.connect(self._send_blue_drop_pwm_cmd)
        self.blue_drop_slider.valueChanged.connect(lambda _v: self._blue_drop_debounce.start())

        # Sizing Policies to prevent cramping
        btn_height = 26   # dense grid buton yüksekliği ≥26 (şartname §3)
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

        servo_lay.addWidget(lbl_hold, 3, 0)
        servo_lay.addWidget(self.chk_grab_hold_torque, 3, 1, 1, 2)


        servo_lay.addWidget(lbl_red_manual, 5, 0)
        servo_lay.addWidget(self.red_drop_slider, 5, 1)
        servo_lay.addWidget(self.lbl_red_drop_pwm_val, 5, 2)

        servo_lay.addWidget(lbl_blue_manual, 6, 0)
        servo_lay.addWidget(self.blue_drop_slider, 6, 1)
        servo_lay.addWidget(self.lbl_blue_drop_pwm_val, 6, 2)

        servo_lay.setColumnStretch(0, 3)
        servo_lay.setColumnStretch(1, 4)
        servo_lay.setColumnStretch(2, 4)
        
        cl.addWidget(servo_grp, stretch=0)

        # Kolon oranı: GÖREV İZLEME (PFD + video + yük paneli) haritadan belirgin
        # şekilde geniş olmalı — operatör uçuşu buradan takip eder. Minimum genişlik
        # şart: yalnız stretch vermek yetmez, harita sekmesi kendi boyut ipucuyla
        # alanı geri kapabiliyor.
        self._center_panel.setMinimumWidth(560)
        root.addWidget(self._center_panel, stretch=3)

        # ── Sağ panel (Sekmeli Premium Panel: Harita, ESC, Tespit Geçmişi, Sağlık & Görev Adımları) ────────────
        right_tabs = QTabWidget()   # sekme stili global QSS'ten gelir (şartname §4)

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
        lbl_ai_title.setObjectName("sectionTitle")
        ai_lay.addWidget(lbl_ai_title)

        self._ai_scroll = QScrollArea()
        self._ai_scroll.setWidgetResizable(True)
        self._ai_scroll.setObjectName("cardFrame")   # bare stylesheet yerine QSS class
        
        self._ai_container = QWidget()
        self._ai_container_lay = QVBoxLayout(self._ai_container)
        self._ai_container_lay.setContentsMargins(4, 4, 4, 4)
        self._ai_container_lay.setSpacing(6)
        self._ai_container_lay.addStretch() # pushing items upwards
        
        self._ai_scroll.setWidget(self._ai_container)
        ai_lay.addWidget(self._ai_scroll)
        # NOT: "Hedefler" sekmesi 3B izleme ile değiştirildi; tespit kaydı
        # widget'ları korunuyor (update_detection_history hâlâ besliyor),
        # ileride başka bir yere taşınabilir.
        right_tabs.addTab(self._build_track3d_tab(), "3B İzleme")

        # 4. Sinyal & Donanım Sekmesi
        hw_tab = QWidget()
        hw_lay = QVBoxLayout(hw_tab)
        hw_lay.setContentsMargins(12, 12, 12, 12)
        hw_lay.setSpacing(10)
        
        lbl_hw_title = QLabel("Sistem Sağlığı & Sinyal Kalitesi")
        lbl_hw_title.setObjectName("sectionTitle")
        hw_lay.addWidget(lbl_hw_title)
        
        # Telemetry ve Ping Grubu
        net_grp = QGroupBox("Kablosuz Bağlantı Kalitesi")   # stil: global QSS
        net_lay = QFormLayout(net_grp)
        net_lay.setContentsMargins(8, 12, 8, 8)
        net_lay.setVerticalSpacing(8)
        
        # NEDEN: Bu göstergeler önceden sahte başlangıç değerleriyle (98/92/35/42/44)
        # açılıyordu; hiçbir veri gelmemişken bile YKİ "%92 sinyal gücü", "44°C" gibi
        # gerçek görünen ama tamamen uydurma bilgiler gösteriyordu (bağlı olmayan
        # sistemde tehlikeli yanlış güven). Hepsi 0 ile başlar ve ilk GERÇEK veri
        # gelene kadar "—" (veri yok) yazar.
        # NOT: Çubuk gövdesi (zemin/kenar/yazı) artık global QSS'ten gelir;
        # burada YALNIZCA anlamsal dolgu rengi (chunk) satır-içi verilir ve
        # tema değişince _apply_theme_colors yeniden yazar.
        self._ping_bar = QProgressBar()
        self._ping_bar.setRange(0, 100)
        self._ping_bar.setValue(0)
        self._ping_bar.setFormat("—")

        self._rssi_bar = QProgressBar()
        self._rssi_bar.setRange(0, 100)
        self._rssi_bar.setValue(0)
        self._rssi_bar.setFormat("—")

        self._loss_bar = QProgressBar()
        self._loss_bar.setRange(0, 100)
        self._loss_bar.setValue(0)
        self._loss_bar.setFormat("—")   # veri gelene kadar "0%" da yanıltıcı


        lbl_p = QLabel("Gecikme (Ping):"); lbl_p.setObjectName("mutedLabel")
        lbl_r = QLabel("Sinyal Gücü (RSSI):"); lbl_r.setObjectName("mutedLabel")
        lbl_l = QLabel("Paket Kaybı:"); lbl_l.setObjectName("mutedLabel")
        
        net_lay.addRow(lbl_p, self._ping_bar)
        net_lay.addRow(lbl_r, self._rssi_bar)
        net_lay.addRow(lbl_l, self._loss_bar)
        hw_lay.addWidget(net_grp)
        
        # Donanım Kaynak Grubu
        rpi_grp = QGroupBox("Yardımcı Bilgisayar (RPi 4)")   # stil: global QSS
        rpi_lay = QFormLayout(rpi_grp)
        rpi_lay.setContentsMargins(8, 12, 8, 8)
        rpi_lay.setVerticalSpacing(8)
        
        # NEDEN: sahte başlangıç değerleri kaldırıldı (bkz. yukarıdaki not) —
        # companion bağlı değilken CPU/RAM/sıcaklık uydurulmaz.
        self._cpu_bar = QProgressBar()
        self._cpu_bar.setRange(0, 100)
        self._cpu_bar.setValue(0)
        self._cpu_bar.setFormat("—")

        self._ram_bar = QProgressBar()
        self._ram_bar.setRange(0, 100)
        self._ram_bar.setValue(0)
        self._ram_bar.setFormat("—")

        self._temp_bar = QProgressBar()
        self._temp_bar.setRange(0, 100)
        self._temp_bar.setValue(0)
        self._temp_bar.setFormat("—")

        # (çubuk, anlam token'ı) — tema değişiminde topluca yeniden uygulanır
        self._health_bars = [
            (self._ping_bar, "success"),
            (self._rssi_bar, "info"),
            (self._loss_bar, "danger"),
            (self._cpu_bar,  "success"),
            (self._ram_bar,  "success"),
            (self._temp_bar, "warning"),
        ]
        self._apply_health_bar_colors()

        lbl_c = QLabel("CPU Kullanımı:"); lbl_c.setObjectName("mutedLabel")
        lbl_rm = QLabel("RAM Kullanımı:"); lbl_rm.setObjectName("mutedLabel")
        lbl_t = QLabel("İşlemci Sıcaklığı:"); lbl_t.setObjectName("mutedLabel")
        
        rpi_lay.addRow(lbl_c, self._cpu_bar)
        rpi_lay.addRow(lbl_rm, self._ram_bar)
        rpi_lay.addRow(lbl_t, self._temp_bar)
        hw_lay.addWidget(rpi_grp)
        
        hw_lay.addStretch()
        right_tabs.addTab(hw_tab, "Sağlık")

        # (Görev İzleme paneli _build_mission_monitor'da kurulur)

        # 5. Görev Yol Haritası (artık sekme olarak eklenmiyor; adım
        #    durumlarını güncelleyen mevcut mantık korunuyor)
        road_tab = QWidget()
        road_lay = QVBoxLayout(road_tab)
        road_lay.setContentsMargins(12, 12, 12, 12)
        road_lay.setSpacing(10)
        
        lbl_road_title = QLabel("Canlı Görev Yol Haritası")
        lbl_road_title.setObjectName("sectionTitle")
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
            # Bare stylesheet yerine QSS class (şartname §5); durum renkleri
            # çalışma anında ID-kapsamlı stylesheet ile güncellenir.
            step_box.setObjectName("panelFrame")

            s_lay = QHBoxLayout(step_box)
            s_lay.setContentsMargins(10, 4, 10, 4)
            s_lay.setSpacing(10)

            # State indicator (Tick or dot) — renkler _apply_roadmap_styles'ta
            lbl_indicator = QLabel("-")

            # Step Text
            t_box = QVBoxLayout()
            t_box.setSpacing(1)

            lbl_name = QLabel(f"{idx+1}. {name}")
            lbl_desc = QLabel(desc)


            t_box.addWidget(lbl_name)
            t_box.addWidget(lbl_desc)
            
            s_lay.addWidget(lbl_indicator)
            s_lay.addLayout(t_box)
            s_lay.addStretch()
            
            road_lay.addWidget(step_box)
            self._road_steps.append((step_box, lbl_indicator, lbl_name, lbl_desc))

        # Başlangıç görünümü: tüm adımlar "bekliyor"
        self._apply_roadmap_styles(["pending"] * len(steps_data))

        road_lay.addStretch()

        # ── 6. Görev İzleme + Sesli Otopilot ─────────────────────────────
        # NEDEN "Adımlar" yerine: sabit 7 maddelik yol haritası görevin ne
        # yaptığını değil, ne YAPACAĞINI gösteriyordu; uçuş sırasında bilgi
        # taşımıyordu. Burası anlık akışı METİN olarak yazar ve sesli anlatır.
        right_tabs.addTab(self._build_mission_monitor(), "Görev İzleme")

        # Harita/yan sekmeler: okunur kalsın ama görev izleme alanını yutmasın.
        # Üst sınır olmadan QWebEngineView'in boyut ipucu tüm artan alanı çekiyordu.
        right_tabs.setMinimumWidth(340)
        right_tabs.setMaximumWidth(760)
        root.addWidget(right_tabs, stretch=2)

    # ── Sağlık göstergeleri: "veri yok" → gerçek değer geçişi ──────────────
    def _hw_set(self, bar, key: str, value: int, fmt: str) -> None:
        """Sağlık çubuğuna GERÇEK veri yaz; ilk veride "—" formatını asıl formatla
        değiştirir. NEDEN: veri gelmeden sahte/uydurma değer gösterilmemeli."""
        if not self._hw_seen.get(key, False):
            self._hw_seen[key] = True
            bar.setFormat(fmt)
        bar.setValue(int(value))

    # ── Yük/kapak "Aç" onayı ────────────────────────────────────────────────
    def _confirm_and_emit_servo(self, channel: str, action: str, value: int, label: str,
                                 hold_torque: bool = False) -> None:
        """Yük bırakma / kapak açma servo komutunu onay sonrası gönderir.

        NEDEN: Bkz. top_bar.py._on_drop_click — "Aç" GERİ ALINAMAZ bir eylemdir.
        """
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, f"{label}: Aç",
            f"{label} ŞİMDİ açılacak. Bu işlem GERİ ALINAMAZ.\n\n"
            "Aracın hedef üzerinde ve doğru irtifada olduğunu doğrulayın.\n\n"
            "Onaylıyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.send_servo_action.emit(channel, action, value, hold_torque)


    # ── Kırmızı/mavi yük bırakma manuel PWM (debounce'lu, gimbal ile aynı desen) ──
    def _send_red_drop_pwm_cmd(self) -> None:
        """Kırmızı yük bırakma servosunu slider'daki ARA PWM'e götürür.

        NEDEN state="" gönderilir: ai_socket_worker.send_servo_command bunu
        target=red_drop + boş state ile ayırt edip 'angle' alanını ham PWM
        (µs) olarak companion'a iletir (bkz. o dosyadaki docstring) — normal
        Aç/Yük Al butonlarının state="open"/"close" yolundan TAMAMEN AYRI.
        """
        if self.red_drop_slider.isSliderDown():
            return
        self._red_drop_debounce.stop()
        val = int(self.red_drop_slider.value())
        if val == self._last_red_drop_pwm_sent:
            return
        self._last_red_drop_pwm_sent = val
        self.send_servo_action.emit("red_drop", "", val, False)

    def _send_blue_drop_pwm_cmd(self) -> None:
        """Mavi yük bırakma servosunu slider'daki ARA PWM'e götürür (bkz. yukarı)."""
        if self.blue_drop_slider.isSliderDown():
            return
        self._blue_drop_debounce.stop()
        val = int(self.blue_drop_slider.value())
        if val == self._last_blue_drop_pwm_sent:
            return
        self._last_blue_drop_pwm_sent = val
        self.send_servo_action.emit("blue_drop", "", val, False)

    # ── MAVLink komutlari ──────────────────────────────────────────────────
    def _arm(self, arm: bool) -> None:
        if _MV is None:
            return
        if arm:
            # NEDEN onay: ARM motorları çalıştırır — yanlış tıklama pervaneleri
            # beklenmedik şekilde döndürebilir. DISARM (acil/güvenliği artıran)
            # onaysız kalır; aynı desen top_bar.py._on_drop_click'te kullanılıyor.
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "ARM Onayı",
                "Drone ARM edilecek (motorlar çalışmaya hazır hale gelecek).\n\n"
                "Çevrede kişi/engel olmadığını doğrulayın.\n\n"
                "Onaylıyor musunuz?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.send_command.emit(
            _MV.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            [1 if arm else 0, 0, 0, 0, 0, 0, 0],
        )

    def _takeoff(self) -> None:
        if _MV is None:
            return
        # NEDEN onay: TAKEOFF GERİ ALINAMAZ bir uçuş komutudur — yanlış tıklama
        # aracı beklenmedik anda havalandırır. Aynı desen top_bar.py._on_drop_click.
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "TAKEOFF Onayı",
            f"Drone {self._alt_spin.value()} m irtifaya kalkacak.\n\n"
            "Aracın ARM olduğunu ve etrafın uçuşa uygun olduğunu doğrulayın.\n\n"
            "Onaylıyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
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
        # NOT: Yerel QMenu stili SİLİNDİ — global QSS zaten aynı görünümü
        # (overlay zemin + accent seçim) tema uyumlu olarak veriyor.
        menu = QMenu(self)
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
        self.update_track3d(data)
        
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
                    self._hw_set(self._rssi_bar, "rssi",
                                 max(0, min(100, rssi_raw / 254.0 * 100)), "%p%")
            # Paket Kaybı — FC'nin bildirdiği GERÇEK haberleşme düşme oranı (% = c%/100)
            drop = data.get("drop_rate_comm")
            if drop is not None:
                self._hw_set(self._loss_bar, "loss",
                             max(0, min(100, drop / 100.0)), "%v%")
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
        
        # Renk kodlaması (Kırmızı/Mavi) — tema token'ı olarak saklanır
        low = label.lower()
        if "red" in low or "kirmizi" in low:
            accent_token = "danger"
        elif "blue" in low or "mavi" in low:
            accent_token = "info"
        else:
            accent_token = "warning"

        card = QFrame()
        card.setFixedHeight(62)
        # ID-kapsamlı stil: bare stylesheet çocuk label'lara yayılmasın (şartname §5).
        # Sol kenar accent rengi tespit sınıfına göre çalışma anında belirlenir.
        card.setObjectName("detCard")
        # Token kartla birlikte saklanır — tema değişince aynı anlamla yeniden boyanır
        card._accent_token = accent_token   # type: ignore[attr-defined]
        card.setStyleSheet(self._det_card_css(accent_token))

        clay = QVBoxLayout(card)
        clay.setContentsMargins(8, 4, 8, 4)
        clay.setSpacing(2)

        # Üst Satır: Başlık ve Zaman
        r1 = QHBoxLayout()
        lbl_head = QLabel(f"{label.upper()} (%{conf:.0f})")
        # Rengi QSS'ten (varsayılan metin) — burada yalnız yazı tipi ayarı
        lbl_head.setStyleSheet("font-weight:bold;font-size:11px;")
        lbl_ts = QLabel(ts)
        lbl_ts.setObjectName("mutedLabel")
        r1.addWidget(lbl_head)
        r1.addStretch()
        r1.addWidget(lbl_ts)

        # Alt Satır: Koordinatlar (renk mutedLabel class'ından, yazı tipi mono)
        lbl_coord = QLabel(f"Enlem: {lat:.6f} | Boylam: {lon:.6f}")
        lbl_coord.setObjectName("mutedLabel")
        lbl_coord.setStyleSheet("font-family:'Courier New';")
        
        clay.addLayout(r1)
        clay.addWidget(lbl_coord)
        
        # Kartı en üste ekle (spacer'ın hemen önüne)
        self._ai_container_lay.insertWidget(self._ai_container_lay.count() - 1, card)

        # NEDEN: Önceden her tespitte yeni QFrame + 3 QLabel yaratılıyor ve HİÇ
        # silinmiyordu → uzun görevde yüzlerce/binlerce widget birikip bellek ve
        # layout maliyetini sürekli artırıyordu. Son _MAX_AI_CARDS kart tutulur,
        # fazlası layout'tan çıkarılıp deleteLater() ile yok edilir.
        self._ai_cards.append(card)
        while len(self._ai_cards) > self._MAX_AI_CARDS:
            old = self._ai_cards.pop(0)   # en eski kart
            try:
                self._ai_container_lay.removeWidget(old)
                old.setParent(None)
                old.deleteLater()
            except Exception:
                pass

    # ── 3B Canlı İzleme ────────────────────────────────────────────────────
    def _build_track3d_tab(self):
        """Aracın gerçek hareketini 3B gösteren sekme.

        Konum home'a göre metre cinsinden hesaplanır (kuzey/doğu/yukarı);
        model gerçek yönelimiyle döner ve arkasında zamanla solan bir iz
        bırakır. Harita 2B kuş bakışı verir, bu sekme İRTİFA ve YÖNELİMİ
        aynı anda görünür kılar — özellikle alçalma/hizalama fazında.
        """
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        self._track3d = None
        try:
            from ui.widgets.model3d_view import Model3DView
            self._track3d = Model3DView()
            lay.addWidget(self._track3d, 1)
        except Exception:
            logger.exception("3B izleme kurulamadı")
            uyari = QLabel("3B izleme kurulamadı (WebEngine gerekli).")
            uyari.setObjectName("mutedLabel")
            lay.addWidget(uyari)
            return w

        alt = QHBoxLayout(); alt.setSpacing(6)
        self._lbl_track3d = QLabel("Home bekleniyor…")
        self._lbl_track3d.setObjectName("mutedLabel")
        alt.addWidget(self._lbl_track3d)
        alt.addStretch()
        btn_iz = QPushButton("İzi Temizle")
        btn_iz.setMinimumHeight(26)
        btn_iz.clicked.connect(self._on_clear_trail)
        alt.addWidget(btn_iz)
        lay.addLayout(alt)
        return w

    def _on_clear_trail(self) -> None:
        if getattr(self, "_track3d", None) is not None:
            self._track3d.clear_trail()

    def update_track3d(self, data: dict) -> None:
        """Telemetriden 3B izleme konumunu günceller.

        Home noktası İLK geçerli fix'te kilitlenir ve konum ona göre metre
        cinsinden hesaplanır. Home yoksa hesaplanamaz — koordinat farkını
        ölçmek için bir başlangıç şart."""
        if getattr(self, "_track3d", None) is None:
            return
        lat = data.get("lat"); lon = data.get("lon")
        if lat is None or lon is None or (abs(lat) < 1e-7 and abs(lon) < 1e-7):
            return
        if getattr(self, "_t3d_home", None) is None:
            self._t3d_home = (float(lat), float(lon))
            self._lbl_track3d.setText("Home kilitlendi — iz kaydediliyor")
        hlat, hlon = self._t3d_home
        # Küçük mesafede düz-dünya yaklaşımı yeterli (birkaç km'de hata < %0,1)
        kuzey = (float(lat) - hlat) * 111320.0
        dogu = (float(lon) - hlon) * 111320.0 * math.cos(math.radians(hlat))
        yukari = float(data.get("relative_alt", data.get("alt", 0.0)) or 0.0)
        roll = math.degrees(float(data.get("roll", 0.0) or 0.0))
        pitch = math.degrees(float(data.get("pitch", 0.0) or 0.0))
        yaw = math.degrees(float(data.get("yaw", 0.0) or 0.0))
        self._track3d.set_pose(kuzey, dogu, yukari, roll, pitch, yaw)

    # ── Görev İzleme + Sesli Otopilot ──────────────────────────────────────
    def _build_mission_monitor(self):
        """Görevi metin olarak akıtan ve sesli anlatan panel.

        Anlatım mantığı core/flight_narrator.py'de (Qt'siz, test edilebilir);
        burası yalnızca gösterim ve kullanıcı kontrolü."""
        from PyQt6.QtWidgets import QCheckBox, QTextEdit

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Üst şerit: sesli asistan aç/kapa + anlık faz
        ust = QHBoxLayout(); ust.setSpacing(8)
        self._chk_voice = QCheckBox("Sesli otopilot")
        self._chk_voice.setChecked(True)
        self._chk_voice.setToolTip(
            "Görev akışını, aracın yönelimini ve rüzgâr durumunu sesli anlatır.\n"
            "Kritik uyarılar (batarya, EKF) bu kapalıyken de yazılır."
        )
        ust.addWidget(self._chk_voice)
        ust.addStretch()
        self._lbl_faz = QLabel("—")
        self._lbl_faz.setObjectName("sectionTitle")
        ust.addWidget(self._lbl_faz)
        lay.addLayout(ust)

        # Rüzgâr göstergesi
        self._lbl_ruzgar = QLabel("Rüzgâr: ölçüm için askıda bekleme gerekiyor")
        self._lbl_ruzgar.setObjectName("hintLabel")
        self._lbl_ruzgar.setWordWrap(True)
        lay.addWidget(self._lbl_ruzgar)

        # Anlatı akışı
        self._txt_narrative = QTextEdit()
        self._txt_narrative.setReadOnly(True)
        self._txt_narrative.setFont(QFont("Consolas", 9))
        # Bellek sınırı: uzun uçuşta sınırsız büyümesin
        self._txt_narrative.document().setMaximumBlockCount(500)
        lay.addWidget(self._txt_narrative, 1)

        alt = QHBoxLayout(); alt.addStretch()
        btn_temizle = QPushButton("Temizle")
        btn_temizle.setMinimumHeight(26)
        btn_temizle.clicked.connect(self._txt_narrative.clear)
        alt.addWidget(btn_temizle)
        lay.addLayout(alt)
        return w

    @pyqtSlot(dict)
    def add_narration(self, olay: dict) -> None:
        """Anlatıcıdan gelen olayı akışa yazar (ve gerekirse seslendirir).

        Seslendirme kararı BURADA verilir: kullanıcı sesi kapattıysa yalnızca
        kritik olaylar okunur — batarya/EKF gibi uyarılar sessizce geçilmemeli.
        """
        renk = {"bilgi": theme.c("text"), "uyari": theme.c("warning"),
                "kritik": theme.c("danger")}.get(olay.get("onem", "bilgi"), theme.c("text"))
        ts = time.strftime("%H:%M:%S", time.localtime(olay.get("zaman", time.time())))
        metin = olay.get("metin", "")
        self._txt_narrative.append(
            f'<span style="color:{theme.c("text_muted")}">{ts}</span> '
            f'<span style="color:{renk}">{metin}</span>')
        sb = self._txt_narrative.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

        if olay.get("etiket") == "ruzgar":
            self._lbl_ruzgar.setText(metin)
        faz = olay.get("faz")
        if faz:
            self._lbl_faz.setText(faz)

        sesli = self._chk_voice.isChecked() or olay.get("onem") == "kritik"
        if sesli and olay.get("seslendir", True):
            self.speak_requested.emit(metin)

    def update_mission_roadmap(self, data: dict) -> None:
        """Telemetri durumuna göre görev adımlarını canlı olarak günceller."""
        is_armed = data.get("armed", False)
        alt = data.get("alt", 0.0)
        # NEDEN: Gerçek MavlinkWorker telemetride "flight_mode" anahtarını üretiyor;
        # burada yalnızca "mode" okunduğu için mod her zaman varsayılan kalıyordu ve
        # RTL/LAND koşulu hiç sağlanmıyordu → yol haritasının 6. ve 7. adımı hiç
        # aktifleşmiyordu. İki anahtar da desteklenir.
        mode = data.get("flight_mode", data.get("mode", "")) or ""
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
            
        # Eğer mod RTL / SMART_RTL / LAND ise görevin son adımı
        if mode in ("RTL", "SMART_RTL", "LAND"):
            statuses[0] = "completed"
            statuses[1] = "completed"
            statuses[2] = "completed"
            statuses[3] = "completed"
            statuses[4] = "completed"
            statuses[5] = "completed"
            statuses[6] = "active"
            if alt < 0.3 and not is_armed:
                statuses[6] = "completed"
                
        # NEDEN: update_telemetry ~15 Hz çağrılıyor ve bu döngü her seferinde
        # 7 adım × 4 setStyleSheet = 28 stylesheet parse + yeniden stil hesabı
        # yapıyordu (sürekli CPU yükü, görsel değişiklik olmasa bile). Durum
        # listesi bir öncekiyle aynıysa hiçbir şey yapmaya gerek yok.
        if statuses == self._last_roadmap_statuses:
            return
        self._last_roadmap_statuses = list(statuses)
        self._apply_roadmap_styles(statuses)

    def _apply_roadmap_styles(self, statuses: list) -> None:
        """Adım kutularını verilen durum listesine göre boyar.

        Ayrı metot: hem canlı güncellemede hem de tema değişiminde (son bilinen
        durumla) çağrılır — tüm renkler tema token'larından okunur."""
        for idx, (step_box, lbl_indicator, lbl_name, lbl_desc) in enumerate(self._road_steps):
            stat = statuses[idx] if idx < len(statuses) else "pending"
            if stat == "completed":
                lbl_indicator.setText("✓")
                lbl_indicator.setStyleSheet(
                    f"color:{theme.c('success')};font-weight:bold;font-size:12px;")
                lbl_name.setStyleSheet(
                    f"color:{theme.c('success')};font-weight:bold;font-size:11px;")
                lbl_desc.setStyleSheet(f"color:{theme.c('text_muted')};font-size:10px;")
                step_box.setStyleSheet(self._step_box_css("success_bg", "success"))
            elif stat == "active":
                lbl_indicator.setText("▶")
                lbl_indicator.setStyleSheet(
                    f"color:{theme.c('accent')};font-weight:bold;font-size:12px;")
                lbl_name.setStyleSheet(
                    f"color:{theme.c('text')};font-weight:bold;font-size:11px;")
                lbl_desc.setStyleSheet(f"color:{theme.c('accent')};font-size:10px;")
                step_box.setStyleSheet(self._step_box_css("accent_soft", "accent"))
            else:
                lbl_indicator.setText("-")
                lbl_indicator.setStyleSheet(
                    f"color:{theme.c('idle')};font-size:12px;")
                lbl_name.setStyleSheet(
                    f"color:{theme.c('text_muted')};font-weight:bold;font-size:11px;")
                lbl_desc.setStyleSheet(f"color:{theme.c('text_disabled')};font-size:10px;")
                step_box.setStyleSheet(self._step_box_css("panel", "border"))

    @pyqtSlot(str, int)
    def add_status_message(self, text: str, severity: int) -> None:
        # Önem derecesi → tema token'ı (renk çalışma anında theme.c ile çözülür)
        _C = {
            0: "danger", 1: "danger", 2: "danger",
            3: "warning", 4: "warning", 5: "text",
            6: "success", 7: "text_muted",
        }
        _N = {
            0: "EMRG", 1: "ALRT", 2: "CRIT", 3: "ERR ",
            4: "WARN", 5: "NOTE", 6: "INFO", 7: "DBG ",
        }
        col = theme.c(_C.get(severity, "text"))
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
        self._rpi_msg_log.append(
            f'<span style="color:{theme.c("accent")}">[{ts}] {text}</span>')
        sb = self._rpi_msg_log.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    @pyqtSlot(dict)
    def update_rpi_status(self, data: dict) -> None:
        """Companion durum JSON'undan İHA canlı durumunu görselleştir:
        TOF mesafeleri (TofWidget), hedef tespit, yük, servo durumları."""
        # Tema değişiminde aynı veriyle yeniden kurulabilsin diye saklanır
        self._last_rpi_status = data
        # ── Sağlık paneli: GERÇEK companion sistem metrikleri (psutil, 5602) ──
        # cpu_pct/mem_pct/temp_c companion status_server._sys_stats()'tan gelir.
        # ping_ms RpiStatusWorker'ın LAN RTT ölçümünden eklenir.
        try:
            cpu = data.get("cpu_pct")
            mem = data.get("mem_pct")
            tmp = data.get("temp_c")
            png = data.get("ping_ms")
            # NEDEN: ilk gerçek veride "—" formatı asıl formatla değiştirilir
            if cpu is not None:
                self._hw_set(self._cpu_bar, "cpu", max(0, min(100, round(cpu))), "%p%")
            if mem is not None:
                self._hw_set(self._ram_bar, "ram", max(0, min(100, round(mem))), "%p%")
            if tmp is not None:
                self._hw_set(self._temp_bar, "temp", max(0, min(120, round(tmp))), "%v°C")
            if png is not None:
                self._hw_set(self._ping_bar, "ping", max(0, min(100, round(png))), "%v ms")
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

        # Durum metinleri DİNAMİK renklidir → tema token'ları çalışma anında okunur
        c_ok    = theme.c("success")
        c_dim   = theme.c("text_muted")
        c_faint = theme.c("text_disabled")
        c_bad   = theme.c("danger")
        c_warn  = theme.c("warning")

        detected = bool(vis.get("target_detected", False))
        cam_ok   = bool(vis.get("connected", False))
        det_txt  = "🎯 HEDEF VAR"   if detected else "hedef yok"
        det_col  = c_ok             if detected else c_dim
        cam_txt  = "kamera ✓"       if cam_ok   else f"<span style='color:{c_bad}'>kamera ✗</span>"
        pl_txt   = (f"<span style='color:{c_warn}'>YÜK VAR</span>" if payload else "yük yok")

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
            fc_line = (f"<br><span style='color:{c_dim}'>FC görevi:</span> "
                       f"{fc.get('type','?')} {fc.get('wp_count','?')} WP "
                       f"<span style='color:{c_faint}'>({fc.get('source','?')})</span>")

        # ── Companion MAVLink görüşü (E6) — çapraz kontrol / radyo-kopuk fallback ──
        mav = data.get("mav", {}) or {}
        mav_line = ""
        if mav:
            batt = mav.get("battery", {}) or {}
            bv = batt.get("v")
            armed = mav.get("armed")
            arm_txt = (f"<span style='color:{c_bad}'>ARMED</span>" if armed
                       else "DISARM")
            mav_line = (f"<br><span style='color:{c_faint}'>İHA(companion): "
                        f"{mav.get('mode','?')} {arm_txt} "
                        f"{('%.1fV' % bv) if isinstance(bv,(int,float)) else ''}</span>")

        self._iha_status_lbl.setText(
            f"<b style='color:{det_col}'>{det_txt}</b> &nbsp;|&nbsp; {cam_txt} "
            f"&nbsp;|&nbsp; {pl_txt}<br>"
            f"<span style='color:{c_dim}'>Gimbal:</span>{gim}° "
            f"<span style='color:{c_dim}'>Grab:</span>{grab} "
            f"<span style='color:{c_dim}'>Kırmızı:</span>{rdr} "
            f"<span style='color:{c_dim}'>Mavi:</span>{bdr}"
            f"{fc_line}{mav_line}"
        )

    # ── RTK GPS (F9P) — kompakt özet + harita istasyon işaretçisi ──────────────
    @pyqtSlot(bool)
    def rtk_connection_changed(self, connected: bool) -> None:
        if connected:
            self._rtk_lbl_conn2.setText("● bağlı")
            self._rtk_lbl_conn2.setStyleSheet(f"color:{theme.c('success')};")
        else:
            self._rtk_lbl_conn2.setText("● bağlı değil")
            self._rtk_lbl_conn2.setStyleSheet(f"color:{theme.c('text_muted')};")

    @pyqtSlot(dict)
    def rtk_status_updated(self, s: dict) -> None:
        fix_code = s.get("fix_quality", "0")
        etiket, renk, aciklama = _RTK_FIX_VERDICT.get(fix_code, ("Bilinmiyor", "text_muted", "—"))
        hdop = s.get("hdop")
        if hdop is not None:
            dop_etk, _ = _dop_rating(hdop)
            verdict = f"{etiket} ({aciklama})  ·  HDOP {hdop:.2f} {dop_etk.lower()}"
        else:
            verdict = f"{etiket} ({aciklama})"
        self._rtk_lbl_verdict2.setText(verdict)
        self._rtk_lbl_verdict2.setStyleSheet(f"color:{theme.c(renk)};")

        lat, lon = s.get("lat"), s.get("lon")
        if lat is not None and lon is not None:
            self._rtk_lbl_pos2.setText(f"{lat:.6f}, {lon:.6f}")
            # İstasyon konumu haritaya İHA'dan BAĞIMSIZ ayrı işaretçi olarak
            # eklenir — İHA bağlanınca kendi drone/home işaretçisi ayrıca oluşur.
            self._map.update_station(lat, lon)
        else:
            self._rtk_lbl_pos2.setText("—")

    @pyqtSlot(bool)
    def ntrip_connection_changed(self, connected: bool) -> None:
        if connected:
            self._ntrip_lbl_status.setText("NTRIP: ● bağlı")
            self._ntrip_lbl_status.setStyleSheet(f"color:{theme.c('success')};")
        else:
            self._ntrip_lbl_status.setText("NTRIP: ● bağlı değil")
            self._ntrip_lbl_status.setStyleSheet(f"color:{theme.c('text_muted')};")

    @pyqtSlot(QImage)
    def update_video(self, q_img: QImage) -> None:
        if q_img.isNull():
            self._video_label.setPixmap(QPixmap())
            self._video_label.setText("Yeniden baglaniyor...")
            return
            
        px = QPixmap.fromImage(q_img)

        # NOT: Burada ÖNCEDEN `self._center_panel.setFixedWidth(h * aspect)` vardı —
        # orta panel video en-boy oranına göre SABİTLENİYORDU. Bunun iki kötü etkisi
        # oldu: (a) root layout'taki stretch oranları tamamen etkisiz kaldı, çünkü
        # sabit genişlikli widget stretch'e katılmaz; (b) kalan tüm yatay alanı sağdaki
        # harita sekmesi yuttu (1920px ekranda orta ~275px, harita ~1205px ölçüldü) —
        # görev izleme alanı okunamayacak kadar küçülüyordu.
        # Videonun oranı zaten aşağıda KeepAspectRatio ile korunuyor, dolayısıyla
        # paneli sabitlemeye GEREK YOK. Oran bilgisi yalnızca bilgi amaçlı saklanır.
        if q_img.height() > 0:
            self._last_video_aspect = round(q_img.width() / q_img.height(), 3)


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
