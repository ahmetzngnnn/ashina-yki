"""
calibration_view.py — Modül 3
Yalnızca ADMIN yetkisiyle erişilebilir.
- İvmeölçer: 3D wizard (6 adım, DroneViz ile görselleştirme)
- Pusula: dönen animasyon + ilerleme
- Radyo: 8 kanal PWM barı (sınır yakalama)
- Motor Test: X-config görsel, dönüş yönü, throttle, tahmini RPM
"""
import logging
import math
from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QTimer, QPointF, QObject, QEvent
from PyQt6.QtGui import (
    QFont, QPainter, QPen, QBrush, QColor, QTransform,
    QPolygonF, QRadialGradient
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTabWidget, QPushButton, QSpinBox, QProgressBar,
    QGroupBox, QTextEdit, QMessageBox, QSplitter, QSizePolicy,
    QAbstractSpinBox, QComboBox,
)
from pymavlink import mavutil

from core import theme

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# AVİYONİK SEMANTİK SABİTLER — temadan bağımsız
# ═══════════════════════════════════════════════════════════════
# Bu renkler uçuş/donanım semantiği taşır: dönüş yönü (CW/CCW), burun-kuyruk
# ayrımı, dönen rotor izi. Tema değişince BUNLAR DEĞİŞMEZ — aksi hâlde
# kullanıcının "kırmızı = CCW" gibi ezberlediği eşleşme bozulur ve yanlış
# motora gaz verilir. Gövde zemini/gölgesi gibi CHROME parçaları ise tema
# token'larından gelir (beyaz temada siyah kutu görünmesin diye).
AV_CCW          = "#e94560"   # saat yönünün TERSİ dönen motorlar (M1/M2)
AV_CW           = "#4ade80"   # saat yönünde dönen motorlar (M3/M4)
AV_FRONT_MARK   = "#e94560"   # burun (FRONT) işareti — üstten görünüm
AV_BODY_EDGE    = "#4a6eff"   # gövde/hub kenarı — yön referansı
AV_ROTOR_TRACE  = "#00e5ff"   # dönen rotor izi (çarpı çizgileri)
AV_HIGHLIGHT    = "#ffcc00"   # seçili/test edilen motor vurgusu
AV_3D_ACCENT    = "#00d2ff"   # 3B gövde kenarı + ön yön noktası
AV_PROP_FRONT   = (0, 210, 255)    # 3B ön pervane diski (RGB)
AV_PROP_REAR    = (255, 50, 50)    # 3B arka pervane diski (RGB)
AV_INK_DARK     = "#101010"   # parlak dolgu üzerindeki etiket mürekkebi
AV_INK_LIGHT    = "#f5f5f5"   # koyu dolgu üzerindeki etiket mürekkebi


def _ink_on(col: QColor) -> QColor:
    """Dolgu renginin parlaklığına göre okunur etiket rengi döndürür.

    NEDEN: Motor diskinin dolgusu ya tema token'ından (açık temada beyaza yakın)
    ya da aviyonik sabitten (sarı vurgu / gaz ısı rampası) gelir. Etiket rengini
    temaya bağlarsak sarı disk üstünde beyaz yazı okunmaz kalır; bu yüzden
    kontrast TEMAYLA değil DOLGUYLA kurulur.
    """
    lum = 0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()
    return QColor(AV_INK_DARK if lum > 140 else AV_INK_LIGHT)


# ═══════════════════════════════════════════════════════════════
# WheelGuard — fare tekerleği koruması
# ═══════════════════════════════════════════════════════════════
class _WheelGuard(QObject):
    """Odakta OLMAYAN spin/combo widget'larında fare tekerleği olayını yutar.

    NEDEN: Qt'nin varsayılan davranışında imleç bir QSpinBox/QComboBox üzerinden
    geçerken çevrilen tekerlek widget'ın değerini SESSİZCE değiştirir. Kalibrasyon
    paneli kaydırılırken motor gaz yüzdesinin kendiliğinden değiştiği canlı olarak
    gözlendi — pervane takılıyken bu doğrudan güvenlik riski. Artık değer yalnızca
    widget'a tıklanıp odak verildikten SONRA tekerlekle değiştirilebilir.
    """

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            event.ignore()
            return True          # olayı yut → değer değişmez
        return False


# ═══════════════════════════════════════════════════════════════
# DroneVizWidget
# ═══════════════════════════════════════════════════════════════
class DroneVizWidget(QWidget):
    """QPainter ile çizilmiş drone görselleştirmesi.
    view: 'top'|'side_nose'|'side_tail'|'front_left'|'front_right'|'bottom'
    """
    # Renkler AVİYONİK SABİT (dönüş yönü kodlaması) — temadan bağımsız
    _MOTORS = [
        ("M1", "CCW", AV_CCW),  # Front-Right
        ("M2", "CCW", AV_CCW),  # Back-Left
        ("M3", "CW",  AV_CW),   # Front-Left
        ("M4", "CW",  AV_CW),   # Back-Right
    ]
    _MOTOR_XY = [(1, -1), (-1, 1), (-1, -1), (1, 1)]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self._view = "top"
        self._highlight = -1
        self._throttles = [0, 0, 0, 0]
        self._angle = 0
        self._spinning = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # Renkler paint sırasında okunur (cache YOK); tema değişince yeniden çiz
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _apply_theme_colors(self):
        """Tema değişince yeniden çizer — renkler paintEvent içinde okunur."""
        self.update()

    def set_view(self, v: str):
        self._view = v
        self.update()

    def set_highlight(self, m: int):
        self._highlight = m
        self.update()

    def set_throttle(self, idx: int, pct: int):
        if 0 <= idx < 4:
            self._throttles[idx] = pct
            self.update()

    def start_spin(self):
        self._spinning = True
        self._timer.start(40)

    def stop_spin(self):
        self._spinning = False
        self._timer.stop()
        self._angle = 0
        self.update()

    def _tick(self):
        self._angle = (self._angle + 25) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # Alet zemini tema token'ından — açık temada beyaz, koyu temada kuyu
        p.fillRect(0, 0, w, h, QColor(theme.c("chart_bg")))
        p.translate(w // 2, h // 2)
        # Tüm drone'u çevirmek yerine motorları çevireceğiz
        if self._view in ("top", "bottom"):
            self._draw_top(p, w, h)
        elif self._view in ("side_nose", "side_tail"):
            self._draw_side(p, w, h)
        elif self._view in ("front_left", "front_right"):
            self._draw_front(p, w, h)
        p.end()

    def _draw_top(self, p, w, h):
        sc = min(w, h) // 2 - 24
        arm = int(sc * 0.82)
        mr = max(14, sc // 5)
        # Kollar = şasi kroması → tema token'ı
        p.setPen(QPen(QColor(theme.c("border_strong")), max(5, sc // 10)))
        for nx, ny in self._MOTOR_XY:
            p.drawLine(0, 0, int(nx * arm), int(ny * arm))
        for i, (nx, ny) in enumerate(self._MOTOR_XY):
            mx, my = int(nx * arm), int(ny * arm)
            thr = self._throttles[i]
            if thr > 0:
                # Gaz ısı rampası (yeşil→kırmızı) — aviyonik semantik, sabit
                col = QColor(min(255, 80 + thr * 2), max(0, 200 - thr * 2), 0)
            elif (i + 1) == self._highlight:
                col = QColor(AV_HIGHLIGHT)   # aviyonik semantik — temadan bağımsız
            else:
                col = QColor(theme.c("panel_alt"))   # boştaki disk = krom
            p.setBrush(QBrush(col))
            p.setPen(QPen(QColor(self._MOTORS[i][2]), 2))
            p.drawEllipse(mx - mr, my - mr, mr * 2, mr * 2)
            p.setPen(_ink_on(col))
            p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            p.drawText(mx - 8, my + 4, self._MOTORS[i][0])
            p.setFont(QFont("Courier New", 7))
            p.setPen(QColor(self._MOTORS[i][2]))
            p.drawText(mx - 10, my + mr + 14, self._MOTORS[i][1])
            if thr > 0 or (i + 1) == self._highlight:
                p.save()
                p.translate(mx, my)
                ra = self._angle if self._MOTORS[i][1] == "CW" else -self._angle
                p.rotate(ra)
                # Dönen rotor izi — aviyonik semantik, temadan bağımsız
                p.setPen(QPen(QColor(AV_ROTOR_TRACE), 2))
                p.drawLine(-mr, 0, mr, 0)
                p.drawLine(0, -mr, 0, mr)
                p.restore()
            else:
                self._arc(p, mx, my, mr + 6, self._MOTORS[i][1] == "CW")
            if thr > 0:
                rpm = int(thr * 80)
                # RPM okuması = veri metni → tema uyarı rengi (iki temada da okunur)
                p.setPen(QColor(theme.c("warning")))
                p.setFont(QFont("Courier New", 7))
                p.drawText(mx - 18, my - mr - 4, f"{rpm}rpm")
        br = max(14, sc // 4)
        # Gövde zemini krom (tema), gövde kenarı yön referansı (aviyonik sabit)
        p.setBrush(QBrush(QColor(theme.c("panel_alt"))))
        p.setPen(QPen(QColor(AV_BODY_EDGE), 2))
        p.drawEllipse(-br, -br, br * 2, br * 2)
        fn = int(arm * 0.45)
        p.setBrush(QBrush(QColor(AV_FRONT_MARK)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(-6, -fn - 14, 12, 12)
        p.setPen(QColor(AV_FRONT_MARK))     # aviyonik semantik — temadan bağımsız
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.drawText(-16, -sc + 14, "FRONT")
        if self._view == "bottom":
            # Yönerge etiketi (aviyonik gösterge değil) → tema uyarı rengi
            p.setPen(QColor(theme.c("warning")))
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            p.drawText(-30, sc - 6, "ALT YÜZEY")

    def _arc(self, p, cx, cy, r, cw):
        p.save()
        p.translate(cx, cy)
        p.setPen(QPen(QColor(theme.c("text_muted")), 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(-r, -r, r * 2, r * 2, 0, 270 * 16 if cw else -270 * 16)
        p.restore()

    def _draw_side(self, p, w, h):
        sc = min(w, h) // 2 - 20
        bw, bh = int(sc * 1.2), int(sc * 0.28)
        # Gövde dolgusu krom (tema); kenar = yön referansı (aviyonik sabit)
        p.setBrush(QBrush(QColor(theme.c("panel_alt"))))
        p.setPen(QPen(QColor(AV_BODY_EDGE), 2))
        p.drawRoundedRect(-bw // 2, -bh // 2, bw, bh, 4, 4)
        arm_y = -bh // 2 - int(sc * 0.45)
        pr = int(sc * 0.18)
        for x in [-bw // 2 + 15, bw // 2 - 15]:
            p.setPen(QPen(QColor(theme.c("border_strong")), 4))
            p.drawLine(x, -bh // 2, x, arm_y)
            p.setBrush(QBrush(QColor(theme.c("panel_alt"))))
            p.setPen(QPen(QColor(AV_CW), 2))   # aviyonik semantik — temadan bağımsız
            p.drawEllipse(x - pr, arm_y - pr, pr * 2, pr * 2)
        # Yönlendirme oku + etiketi bir gösterge değil YÖNERGEDİR → tema rengi
        ac = QColor(theme.c("warning"))
        p.setPen(QPen(ac, 3))
        nd = self._view == "side_nose"
        ax = -bw // 2 - 28 if nd else bw // 2 + 28
        p.drawLine(ax, -18, ax, 18)
        p.drawLine(ax, 18, ax - 7, 8)
        p.drawLine(ax, 18, ax + 7, 8)
        p.setPen(QColor(ac))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        lbl = ("BURUN", "AŞAĞI") if nd else ("KUYRUK", "AŞAĞI")
        p.drawText(ax - 22, -30, lbl[0])
        p.drawText(ax - 22, -14, lbl[1])

    def _draw_front(self, p, w, h):
        sc = min(w, h) // 2 - 20
        br, arm, pr = int(sc * 0.22), int(sc * 0.82), int(sc * 0.18)
        # Gövde dolgusu/kollar krom (tema); kenar+pervane yön kodu (aviyonik)
        p.setBrush(QBrush(QColor(theme.c("panel_alt"))))
        p.setPen(QPen(QColor(AV_BODY_EDGE), 2))
        p.drawEllipse(-br, -br, br * 2, br * 2)
        p.setPen(QPen(QColor(theme.c("border_strong")), 6))
        p.drawLine(-arm, 0, arm, 0)
        for x in [-arm, arm]:
            p.setBrush(QBrush(QColor(theme.c("panel_alt"))))
            p.setPen(QPen(QColor(AV_CW), 2))   # aviyonik semantik — temadan bağımsız
            p.drawEllipse(x - pr, -pr, pr * 2, pr * 2)
        # Yönlendirme oku + etiketi YÖNERGEDİR → tema rengi (iki temada okunur)
        ac = QColor(theme.c("warning"))
        ld = self._view == "front_left"
        ax = -arm - 28 if ld else arm + 28
        p.setPen(QPen(ac, 3))
        p.drawLine(ax, -18, ax, 18)
        p.drawLine(ax, 18, ax - 7, 8)
        p.drawLine(ax, 18, ax + 7, 8)
        p.setPen(QColor(ac))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.drawText(ax - 14, -28, "SOL" if ld else "SAĞ")
        p.drawText(ax - 22, 32, "AŞAĞI")

# ═══════════════════════════════════════════════════════════════
# Drone3DWidget (Kalibrasyon için Animasyonlu)
# ═══════════════════════════════════════════════════════════════
class Drone3DWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(250, 250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._roll  = 0.0
        self._pitch = 0.0
        self._yaw   = 0.0
        self._target_roll = 0.0
        self._target_pitch = 0.0
        self._target_yaw = 0.0
        self._rotor_angle = 0.0
        self._spin_all = False
        
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # Timer yalnızca widget görünürken çalışır (gizli Kalibrasyon sekmesinde
        # 25Hz animasyon CPU yakmasın). showEvent/hideEvent ile gated.
        # Renkler paint sırasında okunur (cache YOK); tema değişince yeniden çiz
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _apply_theme_colors(self):
        """Tema değişince yeniden çizer — renkler paintEvent içinde okunur."""
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(40)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def set_target_attitude(self, r, p, y):
        self._target_roll = r
        self._target_pitch = p
        self._target_yaw = y

    def start_spin(self):
        self._spin_all = True

    def stop_spin(self):
        self._spin_all = False
        self._target_roll = 0
        self._target_pitch = 0
        self._target_yaw = 0

    def _tick(self):
        self._rotor_angle = (self._rotor_angle + 15) % 360
        
        if self._spin_all:
            self._target_yaw = (self._target_yaw + 5) % 360
            self._target_roll = math.sin(math.radians(self._target_yaw)) * 30
            self._target_pitch = math.cos(math.radians(self._target_yaw)) * 30

        # Smooth interpolation
        dr = self._target_roll - self._roll
        dp = self._target_pitch - self._pitch
        dy = self._target_yaw - self._yaw
        
        # Handle wrap around for yaw
        if dy > 180: dy -= 360
        elif dy < -180: dy += 360
            
        self._roll += dr * 0.1
        self._pitch += dp * 0.1
        self._yaw += dy * 0.1
        
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2

        # Alet zemini KROM parçasıdır → tema token'ı (açık temada siyah kutu
        # gibi durmasın diye sabit koyu renk kullanılmaz)
        p.fillRect(0, 0, w, h, QColor(theme.c("chart_bg")))

        # 3D Dönüşüm matrisi (Gerçek 3D Perspektif)
        transform = QTransform()
        transform.translate(cx, cy)
        
        # Dönüşüm sırası ArduPilot vb eksenleri ile eşleşir
        transform.rotate(self._pitch, Qt.Axis.XAxis)
        transform.rotate(self._roll, Qt.Axis.YAxis)
        transform.rotate(self._yaw, Qt.Axis.ZAxis)
        p.setTransform(transform)

        scale = min(w, h) * 0.45
        arm_len = scale * 0.65
        motor_r = scale * 0.18
        body_r = scale * 0.25

        # 1. Kollar — şasi kroması → tema token'ı
        p.setPen(QPen(QColor(theme.c("border_strong")), int(scale * 0.08),
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for ang in [45, 135, 225, 315]:
            ang_r = math.radians(ang)
            mx = arm_len * math.cos(ang_r)
            my = arm_len * math.sin(ang_r)
            p.drawLine(0, 0, int(mx), int(my))

        # 2. Ana Gövde (Altıgen)
        poly = QPolygonF()
        for i in range(6):
            a = math.radians(i * 60 + 30)
            poly.append(QPointF(body_r * math.cos(a), body_r * math.sin(a)))
        
        # Gövde dolgusu/gölgesi KROM → tema token'ları; kenar vurgusu ise
        # yön referansı olduğu için AVİYONİK SABİT
        bg_grad = QRadialGradient(0, 0, body_r)
        bg_grad.setColorAt(0.0, QColor(theme.c("panel_alt")))
        bg_grad.setColorAt(1.0, QColor(theme.c("panel")))

        p.setBrush(QBrush(bg_grad))
        p.setPen(QPen(QColor(AV_3D_ACCENT), 2))
        p.drawPolygon(poly)

        # 3. Motorlar ve Pervaneler
        for i, ang in enumerate([45, 135, 225, 315]):
            ang_r = math.radians(ang)
            mx = arm_len * math.cos(ang_r)
            my = arm_len * math.sin(ang_r)

            # Motor gövdesi = krom → tema token'ları
            p.setBrush(QBrush(QColor(theme.c("panel"))))
            p.setPen(QPen(QColor(theme.c("border_strong")), 1.5))
            p.drawEllipse(QPointF(mx, my), motor_r * 0.5, motor_r * 0.5)

            # Pervane diski (yarı saydam) — ön mavi / arka kırmızı ayrımı
            # AVİYONİK SEMANTİK, temadan bağımsız
            if i < 2:  # Ön kollar
                prop_color = QColor(*AV_PROP_FRONT, 60)
                edge_color = QColor(*AV_PROP_FRONT, 150)
            else:      # Arka kollar
                prop_color = QColor(*AV_PROP_REAR, 60)
                edge_color = QColor(*AV_PROP_REAR, 150)


            p.setBrush(QBrush(prop_color))
            p.setPen(QPen(edge_color, 1.5))
            p.drawEllipse(QPointF(mx, my), motor_r, motor_r)

            # Dönüş efekti
            for ri in range(3):
                ra = math.radians(self._rotor_angle + ri * 120 + (i*15))
                rx = mx + motor_r * 0.9 * math.cos(ra)
                ry = my + motor_r * 0.9 * math.sin(ra)
                p.setPen(QPen(edge_color, 2))
                p.drawLine(QPointF(mx, my), QPointF(rx, ry))

        # Ön yön belirteci — AVİYONİK SEMANTİK, temadan bağımsız
        p.setBrush(QBrush(QColor(AV_3D_ACCENT)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(0, -body_r * 0.6), 5, 5)

        p.end()

# ═══════════════════════════════════════════════════════════════
# CalibrationView
# ═══════════════════════════════════════════════════════════════
_ACCEL_STEPS = [
    ("top",        "ADIM 1/6 — Drone'u düz ve yatay tutun (üst yüzey yukarı)"),
    ("front_left", "ADIM 2/6 — Drone'u sol tarafa yatırın"),
    ("front_right","ADIM 3/6 — Drone'u sağ tarafa yatırın"),
    ("side_nose",  "ADIM 4/6 — Drone'un burnunu aşağı getirin"),
    ("side_tail",  "ADIM 5/6 — Drone'un burnunu yukarı getirin (kuyruk aşağı)"),
    ("bottom",     "ADIM 6/6 — Drone'u tamamen ters çevirin"),
]

_RC_CHANNELS = [
    (1, "Roll"),
    (2, "Pitch"),
    (3, "Throttle"),
    (4, "Yaw"),
    (5, "Aux1"),
    (6, "Aux2"),
    (7, "Aux3"),
    (8, "Aux4"),
]

class CalibrationView(QWidget):
    send_command = pyqtSignal(int, list)
    # NEDEN: Radyo kalibrasyonu ArduPilot'ta bir FC "modu" değildir — GCS uçları
    # yakalar ve RCn_MIN/RCn_MAX parametrelerini YAZAR. Bunun için parametre yazma
    # kanalı gerekliydi; tuning_view/advanced_view ile AYNI imza kullanıldı
    # (str, float) → mavlink_worker.set_param.
    set_param = pyqtSignal(str, float)

    # FC STATUSTEXT anahtar kelimeleri (ArduPilot ivmeölçer kalibrasyon akışı)
    _ST_PROMPT = ("place vehicle", "press any key", "place aircraft")
    _ST_OK     = ("calibration successful", "calibration succeeded")
    _ST_FAIL   = ("calibration failed", "calibration cancelled",
                  "calibration canceled", "calibration timed out")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._accel_step = 0
        self._calib = False
        self._rc_min = {}
        self._rc_max = {}
        self._rc_bars = {}
        self._rc_min_labels = {}
        self._rc_max_labels = {}
        self._capturing_rc = False
        self._motor_timers = {}
        self._compass_active = False
        # NEDEN: İvmeölçer akışı artık FC onayına (STATUSTEXT) dayanıyor;
        # bu durum bayrakları olmadan "tamamlandı" iddiası doğrulanamıyordu.
        self._accel_active = False
        self._accel_awaiting_fc = False
        # FC onayı hiç gelmezse UI sonsuza dek kilitli kalmasın diye emniyet sayacı
        self._accel_ack_timer = QTimer(self)
        self._accel_ack_timer.setSingleShot(True)
        self._accel_ack_timer.timeout.connect(self._on_accel_ack_timeout)
        # NEDEN: Radyo yakalaması gerçekten başlatılmadan "Kaydedildi" denmesin.
        self._rc_capture_started = False
        # Satır-içi renklerin SON durumu — tema değişince aynı anlamla yeniden
        # yazılabilsin diye token adı olarak saklanır (hex değil).
        self._accel_lbl_token = "warning"
        self._compass_token = "text_muted"
        self._compass_bold = False
        self._rc_token = "text_muted"
        self._rc_bold = False
        self._motor_token = "text_muted"
        self._wheel_guard = _WheelGuard(self)   # referans tutulmazsa GC'ye gider
        self._build_ui()
        self._install_wheel_guards()
        # Tema değişince satır-içi renkler son duruma göre yeniden yazılır
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ──────────────────────────────────────────────────────────────
    # TEMA — satır-içi renklerin tek kaynağı
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _log_style() -> str:
        """Kalibrasyon log kutularının (terminal görünümlü) stili.

        NEDEN seçici: seçicisiz (bare) bir stil QTextEdit'in ALT widget'larına
        (viewport, kaydırma çubukları) da yayılır — şartname §5 bunu yasaklıyor.
        `QTextEdit{...}` yalnız kutunun kendisini hedefler.
        """
        return (f"QTextEdit{{color:{theme.c('success')};"
                "font-family:'Courier New';font-size:11px;}")

    def _accel_step_style(self, token: str) -> None:
        """İvmeölçer adım etiketinin rengini token ile ayarlar ve saklar."""
        self._accel_lbl_token = token
        self._accel_step_lbl.setStyleSheet(f"color:{theme.c(token)}; padding:4px;")

    def _compass_status_set(self, text: str, token: str = "text_muted",
                            bold: bool = False) -> None:
        """Pusula durum metnini ve rengini (token) tek noktadan yazar."""
        self._compass_token = token
        self._compass_bold = bold
        self._compass_status.setText(text)
        self._apply_compass_status_style()

    def _apply_compass_status_style(self) -> None:
        weight = "font-weight:bold;" if self._compass_bold else ""
        self._compass_status.setStyleSheet(
            f"color:{theme.c(self._compass_token)}; font-size:12px;{weight}"
        )

    def _rc_status_set(self, text: str, token: str = "text_muted",
                       bold: bool = False) -> None:
        """Radyo durum metnini ve rengini (token) tek noktadan yazar."""
        self._rc_token = token
        self._rc_bold = bold
        self._rc_status.setText(text)
        self._apply_rc_status_style()

    def _apply_rc_status_style(self) -> None:
        weight = "font-weight:bold;" if self._rc_bold else ""
        self._rc_status.setStyleSheet(f"color:{theme.c(self._rc_token)};{weight}")

    def _apply_theme_colors(self) -> None:
        """Tema değişince TÜM satır-içi renkleri son duruma göre yeniden yazar.

        NEDEN: Kurulumda yazılan satır-içi renkler tema değişince BAYAT kalır
        (koyu tema yeşili beyaz zeminde okunmaz). Her durum metninin son
        token'ı saklandığı için burada anlamı bozmadan yeniden uygulanır.
        """
        self._accel_step_style(self._accel_lbl_token)
        self._apply_compass_status_style()
        self._apply_rc_status_style()
        self._motor_status.setStyleSheet(
            f"color:{theme.c(self._motor_token)};font-size:11px;"
        )
        self._accel_log.setStyleSheet(self._log_style())
        self._compass_log.setStyleSheet(self._log_style())
        # QPainter alet widget'ları renkleri paint sırasında okur → yeniden çiz
        for wdg in (self._accel_drone, self._compass_drone, self._motor_drone):
            wdg.update()

    # ──────────────────────────────────────────────────────────────
    # ORTAK YARDIMCILAR
    # ──────────────────────────────────────────────────────────────
    def _install_wheel_guards(self):
        """Bu view'deki TÜM spin/combo widget'larına tekerlek korumasını kurar.

        NEDEN: Tek tek widget'a eklemek yerine tarayarak kuruyoruz; ileride
        eklenecek yeni bir QSpinBox/QDoubleSpinBox/QComboBox da otomatik korunur.
        StrongFocus → widget tekerlekle değil, yalnızca tıklama/Tab ile odak alır.
        """
        for cls in (QAbstractSpinBox, QComboBox):   # QAbstractSpinBox = QSpinBox + QDoubleSpinBox
            for wdg in self.findChildren(cls):
                wdg.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                wdg.installEventFilter(self._wheel_guard)

    def _signal_connected(self, signal) -> bool:
        """Sinyalin gerçekten bir slota bağlı olup olmadığını döndürür.

        NEDEN: Bağlanmamış bir sinyale emit etmek sessizce hiçbir şey yapmaz.
        Kullanıcıya 'Kaydedildi/Gönderildi' demeden ÖNCE komutun gerçekten bir
        alıcıya gittiğini doğruluyoruz (bu dosyadaki 'ölü kontrol' hatalarının
        kök nedeni buydu).
        """
        try:
            return self.receivers(signal) > 0
        except (TypeError, RuntimeError):
            return True   # tespit edilemiyorsa akışı engelleme

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QLabel("Kalibrasyon & Kurulum  [Sadece ADMIN]")
        header.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        header.setObjectName("dangerText")
        layout.addWidget(header)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._accel_tab(), "İvmeölçer")
        self._tabs.addTab(self._compass_tab(), "Pusula")
        self._tabs.addTab(self._radio_tab(), "Radyo Kalibrasyon")
        self._tabs.addTab(self._motor_tab(), "Motor Test")
        layout.addWidget(self._tabs)

    # ──────────────────────────────────────────────────────────────
    # İVMEÖLÇER
    # ──────────────────────────────────────────────────────────────
    def _accel_tab(self):
        w = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)

        # Sol: araç görseli — 3B model varsa o, yoksa mevcut 2B çizim
        # NEDEN 3B: ivmeölçer kalibrasyonu aracın altı ayrı pozisyona
        # getirilmesini ister ("sol yanına yatırın"). Metin + 2B çizimle bunu
        # anlatmak, özellikle deneyimsiz bir operatöre, hataya açık; dönen bir
        # model hangi yüzün yere bakacağını tartışmasız gösterir.
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        self._accel_model3d = None
        try:
            from ui.widgets.model3d_view import Model3DView
            self._accel_model3d = Model3DView()
            self._accel_model3d.setMinimumSize(280, 260)
            ll.addWidget(self._accel_model3d, 1)
        except Exception:
            logger.exception("3B görünüm kurulamadı — 2B çizime düşülüyor")
        # 2B çizim yedek olarak kalır: WebEngine/WebGL yoksa panel boş kalmasın
        self._accel_drone = Drone3DWidget()
        if self._accel_model3d is None:
            ll.addWidget(self._accel_drone)
        else:
            self._accel_drone.setVisible(False)
        split.addWidget(left)

        # Sağ: wizard
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 8, 8, 8)

        self._accel_progress = QProgressBar()
        self._accel_progress.setRange(0, 6)
        self._accel_progress.setValue(0)
        self._accel_progress.setFormat("Adım %v / 6")
        rl.addWidget(self._accel_progress)

        self._accel_step_lbl = QLabel("Kalibrasyonu başlatmak için butona basın.")
        self._accel_step_lbl.setFont(QFont("Segoe UI", 11))
        self._accel_step_lbl.setWordWrap(True)
        self._accel_step_style("warning")
        rl.addWidget(self._accel_step_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._btn_accel_start = QPushButton("Kalibrasyonu Başlat")
        self._btn_accel_start.setObjectName("primaryButton")
        self._btn_accel_start.setFixedHeight(30)
        self._btn_accel_start.clicked.connect(self._start_accel)

        self._btn_accel_next = QPushButton("Sonraki Konum ▶")
        self._btn_accel_next.setFixedHeight(30)
        self._btn_accel_next.setEnabled(False)
        self._btn_accel_next.clicked.connect(self._next_accel_step)

        btn_row.addStretch()
        btn_row.addWidget(self._btn_accel_start)
        btn_row.addWidget(self._btn_accel_next)
        rl.addLayout(btn_row)

        self._accel_log = QTextEdit()
        self._accel_log.setReadOnly(True)
        self._accel_log.setStyleSheet(self._log_style())
        rl.addWidget(self._accel_log, stretch=1)
        split.addWidget(right)

        split.setSizes([300, 400])
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        return w

    # İvmeölçer görselleştirme açıları (adım adı → roll/pitch/yaw)
    _ACCEL_ATT = {
        "top": (0, 0, 0), "front_left": (90, 0, 0), "front_right": (-90, 0, 0),
        "side_nose": (0, -90, 0), "side_tail": (0, 90, 0), "bottom": (180, 0, 0),
    }

    def _model3d_poz(self, adim: int) -> None:
        """3B modeli verilen kalibrasyon adımının pozisyonuna döndürür.

        NEDEN buradan: pozisyon açıları ZATEN `_ACCEL_ATT` içinde tanımlı ve
        2B çizim de onu kullanıyor. 3B görünüm kendi tablosunu tutsaydı iki
        kaynak ayrışabilir, model bir yöne 2B çizim başka yöne bakardı —
        bu projede aynı hata (YKİ/İHA rota matematiği) zaten yaşandı.
        """
        if self._accel_model3d is None or not (0 <= adim < len(_ACCEL_STEPS)):
            return
        view, msg = _ACCEL_STEPS[adim]
        att = self._ACCEL_ATT.get(view)
        if att is None:
            return
        self._accel_model3d.set_attitude(*att)
        self._accel_model3d.set_info(msg)

    def _start_accel(self):
        ml = self._get_mavlink()
        # NEDEN: Komut gerçekten bir alıcıya gitmiyorsa kullanıcıya kalibrasyon
        # başladı denmemeli — eskiden UI koşulsuz ilerliyordu.
        if not self._signal_connected(self.send_command):
            self._accel_step_lbl.setText("✗ Komut kanalı bağlı değil — kalibrasyon başlatılamadı")
            self._accel_step_style("danger")
            self._accel_log.append("[HATA] send_command sinyali bağlı değil")
            return

        self.send_command.emit(
            ml.MAV_CMD_PREFLIGHT_CALIBRATION, [0, 0, 0, 0, 1, 0, 0]
        )
        self._accel_step = 0
        self._accel_active = True
        self._accel_awaiting_fc = False
        self._accel_progress.setValue(1)
        self._btn_accel_next.setEnabled(True)
        self._btn_accel_start.setEnabled(False)
        view, msg = _ACCEL_STEPS[0]
        self._accel_drone.set_target_attitude(0, 0, 0)
        self._model3d_poz(0)   # 3B model ilk pozisyona (düz/yatay)
        self._accel_step_lbl.setText(msg)
        self._accel_step_style("warning")
        self._accel_log.append("[BAŞLAT] İvmeölçer kalibrasyon komutu gönderildi — " + msg)
        self._accel_log.append("[BİLGİ] Adım sonuçları FC'nin STATUSTEXT mesajlarından okunur")

    def _next_accel_step(self):
        """Mevcut pozisyonu FC'ye bildirir ve FC onayını BEKLER.

        NEDEN: Eski kod yalnızca buton tıklamalarını sayıyor, FC'nin STATUSTEXT
        yanıtını ('Place vehicle...', 'Calibration successful/FAILED') hiç
        okumuyordu; 6. tıklamada koşulsuz '✓ Kalibrasyon tamamlandı!' yazıyordu.
        Artık UI yalnızca 'gönderildi' der; başarı/başarısızlık kararını
        on_status_text() FC'den gelen metne göre verir.
        """
        ml = self._get_mavlink()
        if not self._signal_connected(self.send_command):
            self._accel_log.append("[HATA] send_command sinyali bağlı değil — adım gönderilmedi")
            return

        pos = self._accel_step + 1          # ArduPilot pozisyon indeksi 1..6
        self.send_command.emit(
            ml.MAV_CMD_ACCELCAL_VEHICLE_POS, [pos, 0, 0, 0, 0, 0, 0]
        )
        self._accel_log.append(f"[GÖNDER] ACCELCAL_VEHICLE_POS pos={pos}")

        self._accel_step += 1
        # 3B model bir SONRAKİ pozisyona döner: operatör şimdi aracı oraya
        # getirecek. (Gönderilen pos, az önce tamamlanan konumdur.)
        self._model3d_poz(self._accel_step)
        self._accel_awaiting_fc = True
        # NEDEN: FC yanıtı gelene kadar buton kilitli; ama STATUSTEXT hiç bağlı
        # değilse kullanıcı kilitli kalmasın diye zaman aşımı ile serbest bırakılır.
        self._btn_accel_next.setEnabled(False)
        self._accel_ack_timer.start(3000)

        if self._accel_step >= len(_ACCEL_STEPS):
            self._accel_progress.setValue(6)
            self._accel_step_lbl.setText(
                "Son konum gönderildi — FC sonucu bekleniyor…"
            )
            self._accel_step_style("warning")
            return

        view, msg = _ACCEL_STEPS[self._accel_step]
        r, p, y = self._ACCEL_ATT.get(view, (0, 0, 0))
        self._accel_drone.set_target_attitude(r, p, y)
        self._accel_progress.setValue(self._accel_step + 1)
        self._accel_step_lbl.setText(msg + "\n(adım gönderildi — FC onayı bekleniyor)")
        self._accel_log.append(f"[ADIM {self._accel_step + 1}] {msg}")

    def _on_accel_ack_timeout(self):
        """FC onayı gelmediğinde kullanıcıyı kilitli bırakmaz, ama BAŞARI da demez."""
        if not (self._accel_active and self._accel_awaiting_fc):
            return
        self._accel_awaiting_fc = False
        if self._accel_step >= len(_ACCEL_STEPS):
            self._accel_step_lbl.setText(
                "⚠ Tüm konumlar gönderildi — FC'den sonuç mesajı alınamadı.\n"
                "Kalibrasyonun BAŞARILI olduğu doğrulanamadı."
            )
            self._accel_step_style("warning")
            self._accel_log.append("[UYARI] FC sonuç mesajı (STATUSTEXT) alınamadı")
            self._accel_active = False
            self._btn_accel_start.setEnabled(True)
        else:
            self._btn_accel_next.setEnabled(True)
            self._accel_log.append("[UYARI] FC onayı alınamadı — elle devam edebilirsiniz")

    def _accel_finish(self, ok: bool, text: str):
        """FC'den gelen kesin sonuca göre UI'yı kapatır."""
        self._accel_active = False
        self._accel_awaiting_fc = False
        self._accel_ack_timer.stop()
        self._btn_accel_next.setEnabled(False)
        self._btn_accel_start.setEnabled(True)
        if ok:
            self._accel_progress.setValue(6)
            self._accel_step_lbl.setText(f"✓ FC onayladı: {text}")
            self._accel_step_style("success")
            self._accel_log.append(f"[TAMAM] {text}")
        else:
            self._accel_step_lbl.setText(f"✗ Kalibrasyon başarısız: {text}")
            self._accel_step_style("danger")
            self._accel_log.append(f"[HATA] {text}")

    @pyqtSlot(str, int)
    def on_status_text(self, text: str, severity: int) -> None:
        """FC'den gelen STATUSTEXT mesajlarını kalibrasyon akışına yansıtır.

        NEDEN: İvmeölçer kalibrasyonunun tek gerçek geri bildirimi budur —
        ArduPilot COMMAND_ACK ile 'kalibrasyon başarılı' demez, STATUSTEXT yollar.
        main_window'da bağlanması GEREKİR:
            w.status_text_received.connect(self._calib_view.on_status_text)
        """
        if not text:
            return
        low = text.strip().lower()

        # ── Pusula mesajları (bilgi amaçlı log) ──
        if "compass" in low or "mag cal" in low:
            self._compass_log.append(f"[FC] {text.strip()}")

        if not self._accel_active:
            return

        # ── İvmeölçer sonucu ──
        if any(k in low for k in self._ST_FAIL):
            self._accel_finish(False, text.strip())
            return
        if any(k in low for k in self._ST_OK):
            self._accel_finish(True, text.strip())
            return
        # ── FC yeni pozisyon istiyor → sıradaki adıma izin ver ──
        if any(k in low for k in self._ST_PROMPT):
            self._accel_ack_timer.stop()
            self._accel_awaiting_fc = False
            self._accel_log.append(f"[FC] {text.strip()}")
            if self._accel_step < len(_ACCEL_STEPS):
                self._btn_accel_next.setEnabled(True)
                self._accel_step_lbl.setText(
                    _ACCEL_STEPS[self._accel_step][1] + "\n(FC hazır — konumlayıp 'Sonraki'ye basın)"
                )
                self._accel_step_style("warning")

    # ──────────────────────────────────────────────────────────────
    # PUSULA
    # ──────────────────────────────────────────────────────────────
    def _compass_tab(self):
        w = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        self._compass_drone = Drone3DWidget()
        ll.addWidget(self._compass_drone)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 8, 8, 8)

        info = QLabel(
            "Drone'u tüm eksenlerde yavaşça ve düzgünce döndürün.\n"
            "Kalibrasyon tamamlandığında yeşil ışık yanacaktır."
        )
        info.setWordWrap(True)
        info.setObjectName("mutedLabel")
        rl.addWidget(info)

        self._compass_progress = QProgressBar()
        self._compass_progress.setRange(0, 100)
        self._compass_progress.setValue(0)
        self._compass_progress.setFormat("Pusula 1: %p%")
        # Yerel QProgressBar override'ı SİLİNDİ — global QSS zaten stillendiriyor
        rl.addWidget(self._compass_progress)

        self._compass_progress2 = QProgressBar()
        self._compass_progress2.setRange(0, 100)
        self._compass_progress2.setValue(0)
        self._compass_progress2.setFormat("Pusula 2: %p%")
        rl.addWidget(self._compass_progress2)

        self._compass_status = QLabel("Hazır")
        self._apply_compass_status_style()
        rl.addWidget(self._compass_status)

        btn_row = QHBoxLayout()
        self._btn_compass_start = QPushButton("Pusula Kalibrasyonunu Başlat")
        self._btn_compass_start.setObjectName("primaryButton")
        self._btn_compass_start.setMinimumHeight(30)
        self._btn_compass_start.clicked.connect(self._start_compass)
        btn_row.addWidget(self._btn_compass_start)

        self._btn_compass_cancel = QPushButton("İptal")
        self._btn_compass_cancel.setObjectName("dangerButton")
        self._btn_compass_cancel.setMinimumHeight(30)
        self._btn_compass_cancel.clicked.connect(self._cancel_compass)
        btn_row.addWidget(self._btn_compass_cancel)
        rl.addLayout(btn_row)

        self._compass_log = QTextEdit()
        self._compass_log.setReadOnly(True)
        self._compass_log.setStyleSheet(self._log_style())
        rl.addWidget(self._compass_log)
        split.addWidget(right)
        split.setSizes([300, 400])

        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        return w

    def _start_compass(self):
        # MAV_CMD_PREFLIGHT_CALIBRATION: param2=1 → pusula kalibrasyonu
        ml = self._get_mavlink()
        # NEDEN: Komut kimseye gitmiyorsa "başladı" demek yanıltıcı olur.
        if not self._signal_connected(self.send_command):
            self._compass_status_set(
                "✗ Komut kanalı bağlı değil — komut gönderilmedi", "danger", bold=True
            )
            self._compass_log.append("[HATA] send_command sinyali bağlı değil")
            return

        self.send_command.emit(
            ml.MAV_CMD_PREFLIGHT_CALIBRATION, [0, 1, 0, 0, 0, 0, 0]
        )
        self._compass_active = True
        self._compass_drone.start_spin()
        self._compass_progress.setValue(0)
        self._compass_progress.setFormat("Pusula 1: %p%")
        self._compass_progress2.setValue(0)
        self._compass_progress2.setFormat("Pusula 2: %p%")
        # NEDEN: Kalibrasyonun gerçekten başladığını ancak MAG_CAL_PROGRESS kanıtlar;
        # o gelene kadar yalnızca "komut gönderildi" diyoruz.
        self._compass_status_set(
            "⏳ Başlatma komutu gönderildi — FC ilerleme raporu bekleniyor "
            "(drone'u tüm eksenlerde döndürün)",
            "warning", bold=True,
        )
        self._compass_log.append("[GÖNDER] PREFLIGHT_CALIBRATION param2=1 (pusula)")

    def _cancel_compass(self):
        """Devam eden mag-cal'i GERÇEKTEN iptal eder.

        NEDEN: Eskiden PREFLIGHT_CALIBRATION [0,0,0,0,0,0,0] gönderiliyordu;
        tüm parametreleri sıfır olan bu komut ArduPilot'ta NO-OP'tur — hiçbir
        kalibrasyonu iptal etmez. Doğru komut MAV_CMD_DO_CANCEL_MAG_CAL'dir
        (MAVLink enum değeri 42426; 42425 = DO_ACCEPT_MAG_CAL, karıştırılmamalı).
        param1 = pusula bit maskesi, 0 → tüm pusulalar.
        """
        ml = self._get_mavlink()
        cmd = getattr(ml, "MAV_CMD_DO_CANCEL_MAG_CAL", 42426)
        # NEDEN: Komut gönderilmediyse "iptal edildi" YAZILMAMALI.
        if not self._signal_connected(self.send_command):
            self._compass_status_set(
                "✗ Komut kanalı bağlı değil — iptal gönderilemedi", "danger", bold=True
            )
            self._compass_log.append("[HATA] send_command sinyali bağlı değil — iptal YAPILMADI")
            return

        self.send_command.emit(cmd, [0, 0, 0, 0, 0, 0, 0])
        self._compass_active = False
        self._compass_drone.stop_spin()
        self._compass_status_set(
            "⛔ İptal komutu gönderildi (DO_CANCEL_MAG_CAL)", "danger"
        )
        self._compass_log.append(f"[İPTAL] DO_CANCEL_MAG_CAL ({cmd}) gönderildi")

    @pyqtSlot(int, int, float)
    def on_mag_cal_progress(self, compass_id: int, pct: int, fitness: float) -> None:
        """MAVLink MAG_CAL_PROGRESS mesajı geldiğinde çağrılır."""
        if compass_id == 0:
            self._compass_progress.setValue(pct)
            self._compass_progress.setFormat(f"Pusula 1: {pct}%")
        else:
            self._compass_progress2.setValue(pct)
            self._compass_progress2.setFormat(f"Pusula {compass_id + 1}: {pct}%")
        self._compass_log.append(
            f"[PROGRESS] Pusula {compass_id + 1}: %{pct}  fitness={fitness:.2f}"
        )

    @pyqtSlot(int, int, float)
    def on_mag_cal_report(self, compass_id: int, cal_status: int, fitness: float) -> None:
        """MAVLink MAG_CAL_REPORT mesajı geldiğinde çağrılır."""
        # cal_status: 0=FAILED 1=AUTOPILOT 2=GOOD 3=COMPASS_CAL_SUCCESS
        # Renkler tema TOKEN adı olarak tutulur; hex çözümü _compass_status_set'te
        _STATUS = {0: ("BAŞARISIZ", "danger"),
                   1: ("Kabul edilebilir", "warning"),
                   2: ("OK", "success"),
                   3: ("MÜKEMMEL", "success")}
        label, token = _STATUS.get(cal_status, (f"status={cal_status}", "text_muted"))
        if compass_id == 0:
            self._compass_progress.setValue(100)
        else:
            self._compass_progress2.setValue(100)
        self._compass_log.append(
            f"[RAPOR] Pusula {compass_id + 1}: {label}  fitness={fitness:.3f}"
        )
        # Tüm pusulalar tamamlandıysa animasyonu durdur
        if self._compass_progress.value() == 100 and self._compass_progress2.value() == 100:
            self._compass_drone.stop_spin()
        if cal_status >= 2:
            self._compass_status_set(
                f"✓ Pusula {compass_id + 1} kalibrasyonu başarılı ({label})",
                token, bold=True,
            )
        else:
            self._compass_status_set(
                f"✗ Pusula {compass_id + 1} kalibrasyonu başarısız!", "danger", bold=True
            )

    # ──────────────────────────────────────────────────────────────
    # RADYO
    # ──────────────────────────────────────────────────────────────
    def _radio_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        info = QLabel(
            "1. Vericiyi açın ve alıcının bağlı olduğundan emin olun.\n"
            "2. 'Yakalamayı Başlat' butonuna basın.\n"
            "3. Tüm çubukları ve düğmeleri sınır konumlarına (min/max) getirin.\n"
            "4. 'Kaydet / Bitir' → yakalanan uçlar RCn_MIN / RCn_MAX olarak FC'ye YAZILIR."
        )
        info.setObjectName("hintLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Kanal barları — yerel QGroupBox override'ı SİLİNDİ (global QSS yönetir)
        grp = QGroupBox("RC Kanalları (PWM µs)")
        gl = QVBoxLayout(grp)

        for ch, name in _RC_CHANNELS:
            row = QHBoxLayout()
            lbl = QLabel(f"CH{ch} {name}")
            lbl.setFixedWidth(90)

            bar = QProgressBar()
            bar.setRange(1000, 2000)
            bar.setValue(1500)
            bar.setFormat("%v µs")
            bar.setFixedHeight(20)
            self._rc_bars[ch] = bar

            min_lbl = QLabel("min: 1000")
            min_lbl.setObjectName("mutedLabel")
            min_lbl.setFixedWidth(70)
            self._rc_min_labels[ch] = min_lbl
            self._rc_min[ch] = 1000

            max_lbl = QLabel("max: 2000")
            max_lbl.setObjectName("mutedLabel")
            max_lbl.setFixedWidth(70)
            self._rc_max_labels[ch] = max_lbl
            self._rc_max[ch] = 2000

            row.addWidget(lbl)
            row.addWidget(bar, stretch=1)
            row.addWidget(min_lbl)
            row.addWidget(max_lbl)
            gl.addLayout(row)

        layout.addWidget(grp)

        btn_row = QHBoxLayout()
        btn_cap = QPushButton("Yakalamayı Başlat")
        btn_cap.setObjectName("primaryButton")
        btn_cap.setMinimumHeight(30)
        btn_cap.clicked.connect(self._start_rc_capture)
        btn_save = QPushButton("Kaydet / Bitir")
        btn_save.setObjectName("successButton")
        btn_save.setMinimumHeight(30)
        btn_save.clicked.connect(self._stop_rc_capture)
        btn_reset = QPushButton("Sıfırla")
        btn_reset.setMinimumHeight(30)
        btn_reset.clicked.connect(self._reset_rc)
        btn_row.addWidget(btn_cap)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_reset)
        layout.addLayout(btn_row)

        self._rc_status = QLabel("Durum: Pasif")
        self._apply_rc_status_style()
        layout.addWidget(self._rc_status)
        layout.addStretch()
        return w

    # Yakalama başlangıç sentinel'leri: ilk gelen PWM örneği hem min hem max olur
    _RC_SENT_MIN = 3000
    _RC_SENT_MAX = 0
    # Kabul edilebilirlik eşikleri (ArduPilot varsayılan RCn_MIN/MAX aralığına göre)
    _RC_MIN_LIMIT = 1200      # min bu değerin ALTINDA olmalı
    _RC_MAX_LIMIT = 1800      # max bu değerin ÜSTÜNDE olmalı
    _RC_ABS_LO = 800
    _RC_ABS_HI = 2200

    def _start_rc_capture(self):
        """Uç yakalama modunu açar.

        NEDEN (1/2): Eski kod yalnızca bir bayrak set ediyor, başka HİÇBİR şey
        yapmıyordu. Üstelik _rc_min=1000 / _rc_max=2000 ile başlatıldığı için
        update_telemetry'deki 'val < min' / 'val > max' koşulları pratikte HİÇ
        tetiklenmiyordu (gerçek PWM'ler bu aralığın içinde kalır) → yakalama da
        ölüydü. Artık sentinel değerlerle başlatıyoruz.

        NEDEN (2/2): ArduPilot'ta RC min/max kalibrasyonu bir FC "modu" DEĞİLDİR;
        uçları GCS yakalar ve RCn_MIN/RCn_MAX parametrelerini yazar (Mission
        Planner de böyle yapar). PREFLIGHT_CALIBRATION param4=1 radyo TRIM'idir
        (RCn_TRIM), min/max ile ilgisi yoktur — bu yüzden burada gönderilmiyor.
        Eski koddaki param6=1 ise compass/motor girişim kalibrasyonuydu; radyo
        ile hiç ilgisi yoktu.
        """
        for ch, _ in _RC_CHANNELS:
            self._rc_min[ch] = self._RC_SENT_MIN
            self._rc_max[ch] = self._RC_SENT_MAX
            if ch in self._rc_min_labels:
                self._rc_min_labels[ch].setText("min: —")
            if ch in self._rc_max_labels:
                self._rc_max_labels[ch].setText("max: —")
        self._capturing_rc = True
        self._rc_capture_started = True
        self._rc_status_set(
            "Durum: YAKALANIYOR — Tüm eksenleri/düğmeleri sınır konumlarına getirin",
            "warning", bold=True,
        )

    def _stop_rc_capture(self):
        """Yakalanan uçları RCn_MIN / RCn_MAX parametreleri olarak FC'ye YAZAR.

        NEDEN: Eski kod yakalanan self._rc_min/_rc_max değerlerini HİÇBİR YERE
        yazmıyordu — RC1_MIN/RC1_MAX… asla set edilmiyordu. Buna rağmen UI
        "Durum: Kaydedildi" diyordu. Artık gerçekten set_param ile yazılıyor ve
        "Kaydedildi" YALNIZCA yazma yapıldıysa gösteriliyor.
        """
        self._capturing_rc = False

        if not self._rc_capture_started:
            self._rc_fail("Durum: KAYDEDİLMEDİ — önce 'Yakalamayı Başlat'a basın")
            return

        # NEDEN: set_param bağlı değilse emit sessizce kaybolur → yalan mesaj olur.
        if not self._signal_connected(self.set_param):
            self._rc_fail("Durum: KAYDEDİLMEDİ — parametre yazma kanalı bağlı değil")
            QMessageBox.critical(
                self, "Kaydedilemedi",
                "set_param sinyali hiçbir slota bağlı değil.\n"
                "RCn_MIN / RCn_MAX parametreleri FC'ye YAZILMADI.",
            )
            return

        valid, problems = {}, []
        for ch, name in _RC_CHANNELS:
            lo = self._rc_min.get(ch, self._RC_SENT_MIN)
            hi = self._rc_max.get(ch, self._RC_SENT_MAX)
            if lo >= hi:                     # hiç veri gelmedi (sentinel'ler duruyor)
                problems.append(f"CH{ch} {name}: veri yok (alıcı bağlı mı?)")
                continue
            # NEDEN: Makul aralık doğrulaması — yarım hareket ettirilmiş bir
            # çubuğun dar aralığını FC'ye yazmak uçuşta kontrol kaybına yol açar.
            if not (lo < self._RC_MIN_LIMIT and hi > self._RC_MAX_LIMIT):
                problems.append(f"CH{ch} {name}: aralık dar (min={lo}, max={hi})")
                continue
            valid[ch] = (max(self._RC_ABS_LO, lo), min(self._RC_ABS_HI, hi))

        # Ana kumanda eksenleri (1-4) eksiksiz doğrulanmadan HİÇBİR şey yazılmaz.
        missing_primary = [ch for ch in (1, 2, 3, 4) if ch not in valid]
        if missing_primary:
            self._rc_fail("Durum: KAYDEDİLMEDİ — doğrulama başarısız")
            QMessageBox.warning(
                self, "Kalibrasyon geçersiz",
                "Ana kanallar (CH1-CH4) doğrulanamadı, hiçbir parametre YAZILMADI.\n\n"
                + "\n".join(problems)
                + f"\n\nBeklenen: min < {self._RC_MIN_LIMIT} ve max > {self._RC_MAX_LIMIT}",
            )
            return

        written = 0
        for ch, (lo, hi) in sorted(valid.items()):
            self.set_param.emit(f"RC{ch}_MIN", float(lo))
            self.set_param.emit(f"RC{ch}_MAX", float(hi))
            written += 2
            logger.info("RC kalibrasyon: RC%d_MIN=%d RC%d_MAX=%d", ch, lo, ch, hi)

        self._rc_capture_started = False
        self._rc_status_set(
            f"Durum: Kaydedildi — {len(valid)} kanal, {written} parametre yazıldı",
            "success", bold=True,
        )
        if problems:
            QMessageBox.information(
                self, "Kısmi kayıt",
                f"CH1-CH4 yazıldı. Aşağıdaki yardımcı kanallar ATLANDI:\n\n"
                + "\n".join(problems),
            )

    def _rc_fail(self, msg: str):
        """Radyo sekmesinde başarısızlık durumunu tek noktadan gösterir."""
        self._rc_status_set(msg, "danger", bold=True)

    def _reset_rc(self):
        self._capturing_rc = False
        self._rc_capture_started = False
        for ch in range(1, 9):
            self._rc_min[ch] = 1000
            self._rc_max[ch] = 2000
            if ch in self._rc_bars:
                self._rc_bars[ch].setValue(1500)
            if ch in self._rc_min_labels:
                self._rc_min_labels[ch].setText("min: 1000")
            if ch in self._rc_max_labels:
                self._rc_max_labels[ch].setText("max: 2000")
        # NEDEN: Bu buton yalnızca EKRANDAKİ değerleri sıfırlar; FC'deki
        # RCn_MIN/RCn_MAX parametrelerine dokunmaz. Metin bunu açıkça söylüyor.
        self._rc_status_set("Durum: Ekran değerleri sıfırlandı (FC'ye yazılmadı)")

    @pyqtSlot(dict)
    def update_telemetry(self, data: dict):
        for ch, _ in _RC_CHANNELS:
            key = f"rc_{ch}"
            val = data.get(key)
            if val is None or not isinstance(val, int):
                continue
            if ch in self._rc_bars:
                self._rc_bars[ch].setValue(max(800, min(2200, val)))
            if self._capturing_rc:
                # NEDEN: Varsayılanlar sentinel olmalı — eskiden 2000/1000 idi ve
                # yakalama başlangıcında min=1000/max=2000 kaldığı için gerçek PWM
                # değerleri hiçbir zaman bu eşikleri geçemiyor, uçlar yakalanamıyordu.
                if val < self._rc_min.get(ch, self._RC_SENT_MIN):
                    self._rc_min[ch] = val
                    if ch in self._rc_min_labels:
                        self._rc_min_labels[ch].setText(f"min: {val}")
                if val > self._rc_max.get(ch, self._RC_SENT_MAX):
                    self._rc_max[ch] = val
                    if ch in self._rc_max_labels:
                        self._rc_max_labels[ch].setText(f"max: {val}")

    # ──────────────────────────────────────────────────────────────
    # MOTOR TEST
    # ──────────────────────────────────────────────────────────────
    def _motor_tab(self):
        w = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)

        # Sol: X-config görsel
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        self._motor_drone = DroneVizWidget()
        ll.addWidget(self._motor_drone)

        # Açıklama
        info_lbl = QLabel("M1/M2=CCW (kırmızı)  M3/M4=CW (yeşil)\n"
                           "RPM = tahmini (gaz × 80)")
        info_lbl.setObjectName("mutedLabel")
        info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(info_lbl)
        split.addWidget(left)

        # Sağ: Motor kontrol
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 8, 8, 8)

        warning = QLabel("⚠  UYARI: Pervaneler takılıysa motor testi YAPMAYIN!")
        warning.setObjectName("dangerText")
        warning.setWordWrap(True)
        rl.addWidget(warning)

        # Yerel QGroupBox override'ı SİLİNDİ — global QSS yönetir
        grp = QGroupBox("Motor Gaz Kontrol")
        gl = QVBoxLayout(grp)

        self._motor_spins = []
        self._motor_rpm_labels = []
        motor_positions = ["M1 (Ön-Sağ, CCW)", "M2 (Arka-Sol, CCW)",
                           "M3 (Ön-Sol, CW)", "M4 (Arka-Sağ, CW)"]
        for i in range(4):
            row = QHBoxLayout()
            lbl = QLabel(motor_positions[i])
            lbl.setFixedWidth(160)

            spin = QSpinBox()
            spin.setRange(0, 100)
            spin.setValue(0)
            spin.setSuffix(" %")
            spin.setFixedWidth(80)
            # Yerel spin stili SİLİNDİ — global QSS kenar/renk/dolguyu veriyor
            idx = i
            spin.valueChanged.connect(lambda v, n=idx: self._on_throttle_change(n, v))
            self._motor_spins.append(spin)

            rpm_lbl = QLabel("0 rpm")
            rpm_lbl.setObjectName("warningText")   # tahmini RPM okuması
            rpm_lbl.setMinimumWidth(60)
            self._motor_rpm_labels.append(rpm_lbl)

            row.addWidget(lbl)
            row.addWidget(spin)
            row.addWidget(rpm_lbl)
            gl.addLayout(row)

        rl.addWidget(grp)

        btn_row = QHBoxLayout()
        btn_test = QPushButton("Seçili Motorları Test Et")
        btn_test.setObjectName("warningButton")
        btn_test.setMinimumHeight(32)
        btn_test.clicked.connect(self._on_motor_test)
        btn_all = QPushButton("Hepsini Test Et")
        btn_all.setObjectName("warningButton")
        btn_all.setMinimumHeight(32)
        btn_all.clicked.connect(self._on_test_all)
        btn_stop = QPushButton("DURDUR")
        btn_stop.setObjectName("dangerButton")
        btn_stop.setMinimumHeight(32)
        btn_stop.clicked.connect(self._on_motor_stop)
        btn_row.addWidget(btn_test)
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_stop)
        rl.addLayout(btn_row)

        # NEDEN: Motor testinin hiçbir geri bildirimi yoktu — kullanıcı komutun
        # gönderilip gönderilmediğini göremiyordu. Metinler kasıtlı olarak
        # "gönderildi" der; FC'nin motoru gerçekten döndürdüğü doğrulanamaz.
        self._motor_status = QLabel("Durum: Beklemede")
        self._motor_status.setWordWrap(True)
        self._motor_status.setStyleSheet(
            f"color:{theme.c(self._motor_token)};font-size:11px;"
        )
        rl.addWidget(self._motor_status)
        rl.addStretch()

        split.addWidget(right)
        split.setSizes([300, 400])

        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        return w

    def _on_throttle_change(self, idx: int, val: int):
        self._motor_drone.set_throttle(idx, val)
        est_rpm = val * 80
        self._motor_rpm_labels[idx].setText(f"{est_rpm} rpm")
        if val > 0:
            self._motor_drone.set_highlight(idx + 1)
        else:
            self._motor_drone.set_highlight(-1)

    @staticmethod
    def _get_mavlink():
        """BUG FIX: mavlink modülü doğrudan import edilmemişti → NameError.
        mavutil.mavlink dialect yüklü değilse önce yükler, sonra döndürür."""
        if getattr(mavutil, "mavlink", None) is None:
            mavutil.set_dialect("ardupilotmega")
        return mavutil.mavlink

    def _motor_status_set(self, text: str, token: str = "text_muted"):
        """Motor durum metnini yazar. İkinci argüman artık ham hex değil TEMA
        TOKEN'ıdır; son token saklanır ki tema değişince yeniden çözülebilsin."""
        self._motor_token = token
        self._motor_status.setText(text)
        self._motor_status.setStyleSheet(f"color:{theme.c(token)};font-size:11px;")

    def _on_motor_test(self):
        reply = QMessageBox.question(
            self, "Onay",
            "Motor testi başlatılacak.\nPervaneler çıkarılmış olmalı!\nDevam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._motor_status_set("Durum: İptal edildi — komut gönderilmedi")
            return
        ml = self._get_mavlink()
        # NEDEN: Komut kanalı bağlı değilse hiçbir şey olmaz; bunu gizlememeliyiz.
        if not self._signal_connected(self.send_command):
            self._motor_status_set("✗ Komut kanalı bağlı değil — komut GÖNDERİLMEDİ", "danger")
            return
        sent = []
        for i, spin in enumerate(self._motor_spins):
            thr = spin.value()
            if thr > 0:
                self.send_command.emit(
                    ml.MAV_CMD_DO_MOTOR_TEST,
                    [i + 1, 0, thr, 3, 0, 0, 0],
                )
                sent.append(f"M{i + 1}=%{thr}")
        # NEDEN: Tüm gazlar 0 iken eskiden hiçbir komut gitmiyordu ama kullanıcıya
        # da hiçbir şey söylenmiyordu — buton "ölü" görünüyordu.
        if not sent:
            self._motor_status_set(
                "Hiçbir motora gaz verilmedi (hepsi %0) — komut gönderilmedi", "warning"
            )
            return
        self._motor_status_set(
            "Test komutu gönderildi (" + ", ".join(sent) + ", 3 sn) — FC yanıtı doğrulanmıyor",
            "warning",
        )

    def _on_test_all(self):
        reply = QMessageBox.question(
            self, "Onay",
            "TÜM motorlar %20 gaz ile 3 saniye test edilecek.\nPervaneler çıkarılmış olmalı!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._motor_status_set("Durum: İptal edildi — komut gönderilmedi")
            return
        ml = self._get_mavlink()
        if not self._signal_connected(self.send_command):
            self._motor_status_set("✗ Komut kanalı bağlı değil — komut GÖNDERİLMEDİ", "danger")
            return
        for i in range(4):
            self._motor_spins[i].setValue(20)
            self.send_command.emit(
                ml.MAV_CMD_DO_MOTOR_TEST,
                [i + 1, 0, 20, 3, 0, 0, 0],
            )
        self._motor_status_set(
            "4 motor için %20 / 3 sn test komutu gönderildi — FC yanıtı doğrulanmıyor",
            "warning",
        )

    def _on_motor_stop(self):
        ml = self._get_mavlink()
        # NEDEN: DURDUR kritik bir güvenlik kontrolü — komut gitmediyse kullanıcı
        # bunu MUTLAKA görmeli, "durduruldu" izlenimi verilmemeli.
        if not self._signal_connected(self.send_command):
            self._motor_status_set(
                "✗ DUR komutu GÖNDERİLEMEDİ — komut kanalı bağlı değil! Bataryayı ayırın.",
                "danger",
            )
            self._motor_drone.set_highlight(-1)
            return
        for i in range(4):
            self._motor_spins[i].setValue(0)
            self.send_command.emit(
                ml.MAV_CMD_DO_MOTOR_TEST,
                [i + 1, 0, 0, 0, 0, 0, 0],
            )
        self._motor_drone.set_highlight(-1)
        self._motor_status_set("DUR komutu gönderildi (4 motor, gaz %0)", "success")
