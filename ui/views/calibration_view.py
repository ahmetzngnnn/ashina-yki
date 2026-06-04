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
from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QTimer, QPointF
from PyQt6.QtGui import (
    QFont, QPainter, QPen, QBrush, QColor, QTransform, 
    QPolygonF, QRadialGradient
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTabWidget, QPushButton, QSpinBox, QProgressBar,
    QGroupBox, QTextEdit, QMessageBox, QSplitter, QSizePolicy,
)
from pymavlink import mavutil

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# DroneVizWidget
# ═══════════════════════════════════════════════════════════════
class DroneVizWidget(QWidget):
    """QPainter ile çizilmiş drone görselleştirmesi.
    view: 'top'|'side_nose'|'side_tail'|'front_left'|'front_right'|'bottom'
    """
    _MOTORS = [
        ("M1", "CCW", "#e94560"),  # Front-Right
        ("M2", "CCW", "#e94560"),  # Back-Left
        ("M3", "CW",  "#4ade80"),  # Front-Left
        ("M4", "CW",  "#4ade80"),  # Back-Right
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
        self.setStyleSheet(" border-radius:8px;")

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
        p.setPen(QPen(QColor("#3a3a5a"), max(5, sc // 10)))
        for nx, ny in self._MOTOR_XY:
            p.drawLine(0, 0, int(nx * arm), int(ny * arm))
        for i, (nx, ny) in enumerate(self._MOTOR_XY):
            mx, my = int(nx * arm), int(ny * arm)
            thr = self._throttles[i]
            if thr > 0:
                col = QColor(min(255, 80 + thr * 2), max(0, 200 - thr * 2), 0)
            elif (i + 1) == self._highlight:
                col = QColor("#ffcc00")
            else:
                col = QColor("#1e2050")
            p.setBrush(QBrush(col))
            p.setPen(QPen(QColor(self._MOTORS[i][2]), 2))
            p.drawEllipse(mx - mr, my - mr, mr * 2, mr * 2)
            p.setPen(QColor("white"))
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
                p.setPen(QPen(QColor("#00e5ff"), 2))
                p.drawLine(-mr, 0, mr, 0)
                p.drawLine(0, -mr, 0, mr)
                p.restore()
            else:
                self._arc(p, mx, my, mr + 6, self._MOTORS[i][1] == "CW")
            if thr > 0:
                rpm = int(thr * 80)
                p.setPen(QColor("#ffcc00"))
                p.setFont(QFont("Courier New", 7))
                p.drawText(mx - 18, my - mr - 4, f"{rpm}rpm")
        br = max(14, sc // 4)
        p.setBrush(QBrush(QColor("#1a1a3e")))
        p.setPen(QPen(QColor("#4a6eff"), 2))
        p.drawEllipse(-br, -br, br * 2, br * 2)
        fn = int(arm * 0.45)
        p.setBrush(QBrush(QColor("#e94560")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(-6, -fn - 14, 12, 12)
        p.setPen(QColor("#e94560"))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.drawText(-16, -sc + 14, "FRONT")
        if self._view == "bottom":
            p.setPen(QColor("#f0a500"))
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            p.drawText(-30, sc - 6, "ALT YÜZEY")

    def _arc(self, p, cx, cy, r, cw):
        p.save()
        p.translate(cx, cy)
        p.setPen(QPen(QColor("#777"), 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(-r, -r, r * 2, r * 2, 0, 270 * 16 if cw else -270 * 16)
        p.restore()

    def _draw_side(self, p, w, h):
        sc = min(w, h) // 2 - 20
        bw, bh = int(sc * 1.2), int(sc * 0.28)
        p.setBrush(QBrush(QColor("#1a1a3e")))
        p.setPen(QPen(QColor("#4a6eff"), 2))
        p.drawRoundedRect(-bw // 2, -bh // 2, bw, bh, 4, 4)
        arm_y = -bh // 2 - int(sc * 0.45)
        pr = int(sc * 0.18)
        for x in [-bw // 2 + 15, bw // 2 - 15]:
            p.setPen(QPen(QColor("#3a3a5a"), 4))
            p.drawLine(x, -bh // 2, x, arm_y)
            p.setBrush(QBrush(QColor("#1e2050")))
            p.setPen(QPen(QColor("#4ade80"), 2))
            p.drawEllipse(x - pr, arm_y - pr, pr * 2, pr * 2)
        ac = QColor("#f0a500")
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
        p.setBrush(QBrush(QColor("#1a1a3e")))
        p.setPen(QPen(QColor("#4a6eff"), 2))
        p.drawEllipse(-br, -br, br * 2, br * 2)
        p.setPen(QPen(QColor("#3a3a5a"), 6))
        p.drawLine(-arm, 0, arm, 0)
        for x in [-arm, arm]:
            p.setBrush(QBrush(QColor("#1e2050")))
            p.setPen(QPen(QColor("#4ade80"), 2))
            p.drawEllipse(x - pr, -pr, pr * 2, pr * 2)
        ac = QColor("#f0a500")
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
        self._timer.start(40)

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

        # Koyu ve modern arkaplan
        p.fillRect(0, 0, w, h, QColor("#121215"))

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

        # 1. Kollar
        p.setPen(QPen(QColor("#3b3b45"), int(scale * 0.08), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
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
        
        bg_grad = QRadialGradient(0, 0, body_r)
        bg_grad.setColorAt(0.0, QColor("#2a2a35"))
        bg_grad.setColorAt(1.0, QColor("#181820"))
        
        p.setBrush(QBrush(bg_grad))
        p.setPen(QPen(QColor("#00d2ff"), 2))
        p.drawPolygon(poly)

        # 3. Motorlar ve Pervaneler
        for i, ang in enumerate([45, 135, 225, 315]):
            ang_r = math.radians(ang)
            mx = arm_len * math.cos(ang_r)
            my = arm_len * math.sin(ang_r)

            # Motor
            p.setBrush(QBrush(QColor("#202025")))
            p.setPen(QPen(QColor("#555566"), 1.5))
            p.drawEllipse(QPointF(mx, my), motor_r * 0.5, motor_r * 0.5)

            # Pervane diski (yarı saydam)
            if i < 2:  # Ön kollar
                prop_color = QColor(0, 210, 255, 60)
                edge_color = QColor(0, 210, 255, 150)
            else:      # Arka kollar
                prop_color = QColor(255, 50, 50, 60)
                edge_color = QColor(255, 50, 50, 150)
            
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

        # Ön yön belirteci
        p.setBrush(QBrush(QColor("#00d2ff")))
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

BTN_STYLE = "color:#e0e0e0;border-radius:4px;padding:6px 14px;font-weight:bold;"
WARN_STYLE = "color:white;border-radius:4px;padding:8px 14px;font-weight:bold;"


class CalibrationView(QWidget):
    send_command = pyqtSignal(int, list)

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
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet("color:#e0e0e0;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QLabel("Kalibrasyon & Kurulum  [Sadece ADMIN]")
        header.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        header.setStyleSheet("color:#e94560;")
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

        # Sol: DroneViz
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        self._accel_drone = Drone3DWidget()
        ll.addWidget(self._accel_drone)
        split.addWidget(left)

        # Sağ: wizard
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 8, 8, 8)

        self._accel_progress = QProgressBar()
        self._accel_progress.setRange(0, 6)
        self._accel_progress.setValue(0)
        self._accel_progress.setFormat("Adım %v / 6")
        self._accel_progress.setStyleSheet(
            "QProgressBar{border:1px solid #0f3460;border-radius:4px;color:white;}"
            "QProgressBar::chunk{border-radius:3px;}"
        )
        rl.addWidget(self._accel_progress)

        self._accel_step_lbl = QLabel("Kalibrasyonu başlatmak için butona basın.")
        self._accel_step_lbl.setFont(QFont("Segoe UI", 11))
        self._accel_step_lbl.setWordWrap(True)
        self._accel_step_lbl.setStyleSheet("color:#f0a500; padding:8px;")
        rl.addWidget(self._accel_step_lbl)

        btn_row = QHBoxLayout()
        self._btn_accel_start = QPushButton("Kalibrasyonu Başlat")
        self._btn_accel_start.setStyleSheet(WARN_STYLE)
        self._btn_accel_start.clicked.connect(self._start_accel)

        self._btn_accel_next = QPushButton("Sonraki Konum ▶")
        self._btn_accel_next.setStyleSheet(BTN_STYLE)
        self._btn_accel_next.setEnabled(False)
        self._btn_accel_next.clicked.connect(self._next_accel_step)

        btn_row.addWidget(self._btn_accel_start)
        btn_row.addWidget(self._btn_accel_next)
        rl.addLayout(btn_row)

        self._accel_log = QTextEdit()
        self._accel_log.setReadOnly(True)
        self._accel_log.setStyleSheet(
            "color:#4ade80;font-family:'Courier New';font-size:11px;"
        )
        rl.addWidget(self._accel_log)
        split.addWidget(right)

        split.setSizes([300, 400])
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        return w

    def _start_accel(self):
        try:
            from pymavlink.dialects.v20 import ardupilotmega as mavlink
        except ImportError:
            from pymavlink.dialects.v10 import ardupilotmega as mavlink

        self.send_command.emit(
            mavlink.MAV_CMD_PREFLIGHT_CALIBRATION, [0, 0, 0, 0, 1, 0, 0]
        )
        self._accel_step = 0
        self._accel_progress.setValue(1)
        self._btn_accel_next.setEnabled(True)
        self._btn_accel_start.setEnabled(False)
        view, msg = _ACCEL_STEPS[0]
        self._accel_drone.set_target_attitude(0, 0, 0)
        self._accel_step_lbl.setText(msg)
        self._accel_log.append("[BAŞLAT] İvmeölçer kalibrasyonu — " + msg)

    def _next_accel_step(self):
        try:
            from pymavlink.dialects.v20 import ardupilotmega as mavlink
        except ImportError:
            from pymavlink.dialects.v10 import ardupilotmega as mavlink

        # Drone'a güncel adımı gönderiyoruz (1'den 6'ya kadar pozisyonlar)
        self.send_command.emit(
            mavlink.MAV_CMD_ACCELCAL_VEHICLE_POS,
            [self._accel_step + 1, 0, 0, 0, 0, 0, 0]
        )

        self._accel_step += 1
        if self._accel_step >= len(_ACCEL_STEPS):
            self._accel_progress.setValue(6)
            self._accel_step_lbl.setText("✓ Kalibrasyon tamamlandı!")
            self._accel_step_lbl.setStyleSheet("color:#4ade80; padding:8px;")
            self._btn_accel_next.setEnabled(False)
            self._btn_accel_start.setEnabled(True)
            self._accel_log.append("[TAMAM] Tüm adımlar tamamlandı")
            return
        view, msg = _ACCEL_STEPS[self._accel_step]
        _MAP = {"top": (0,0,0), "front_left": (90,0,0), "front_right": (-90,0,0), "side_nose": (0,-90,0), "side_tail": (0,90,0), "bottom": (180,0,0)}
        r, p, y = _MAP.get(view, (0,0,0))
        self._accel_drone.set_target_attitude(r, p, y)
        self._accel_progress.setValue(self._accel_step + 1)
        self._accel_step_lbl.setText(msg)
        self._accel_log.append(f"[ADIM {self._accel_step + 1}] {msg}")

    # ──────────────────────────────────────────────────────────────
    # PUSULA
    # ──────────────────────────────────────────────────────────────
    def _compass_tab(self):
        w = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
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
        info.setStyleSheet("color:#aaa;")
        rl.addWidget(info)

        self._compass_progress = QProgressBar()
        self._compass_progress.setRange(0, 100)
        self._compass_progress.setValue(0)
        self._compass_progress.setFormat("Pusula 1: %p%")
        self._compass_progress.setStyleSheet(
            "QProgressBar{border:1px solid #0f3460;border-radius:4px;color:white;}"
            "QProgressBar::chunk{border-radius:3px;}"
        )
        rl.addWidget(self._compass_progress)

        self._compass_progress2 = QProgressBar()
        self._compass_progress2.setRange(0, 100)
        self._compass_progress2.setValue(0)
        self._compass_progress2.setFormat("Pusula 2: %p%")
        self._compass_progress2.setStyleSheet(
            "QProgressBar{border:1px solid #0f3460;border-radius:4px;color:white;}"
            "QProgressBar::chunk{border-radius:3px;}"
        )
        rl.addWidget(self._compass_progress2)

        self._compass_status = QLabel("Hazır")
        self._compass_status.setStyleSheet("color:#888; font-size:12px;")
        rl.addWidget(self._compass_status)

        btn_row = QHBoxLayout()
        self._btn_compass_start = QPushButton("Pusula Kalibrasyonunu Başlat")
        self._btn_compass_start.setStyleSheet(WARN_STYLE)
        self._btn_compass_start.clicked.connect(self._start_compass)
        btn_row.addWidget(self._btn_compass_start)
        
        self._btn_compass_cancel = QPushButton("İptal")
        self._btn_compass_cancel.setStyleSheet("color:#ff6666;border-radius:4px;padding:6px 10px;font-weight:bold;")
        self._btn_compass_cancel.clicked.connect(self._cancel_compass)
        btn_row.addWidget(self._btn_compass_cancel)
        rl.addLayout(btn_row)

        self._compass_log = QTextEdit()
        self._compass_log.setReadOnly(True)
        self._compass_log.setStyleSheet(
            "color:#4ade80;font-family:'Courier New';font-size:11px;"
        )
        rl.addWidget(self._compass_log)
        split.addWidget(right)
        split.setSizes([300, 400])

        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        return w

    def _start_compass(self):
        # MAV_CMD_PREFLIGHT_CALIBRATION: param2=1 → pusula kalibrasyonu
        if getattr(mavutil, "mavlink", None) is None:
            mavutil.set_dialect("ardupilotmega")

        self.send_command.emit(
            mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION, [0, 1, 0, 0, 0, 0, 0]
        )
        self._compass_drone.start_spin()
        self._compass_progress.setValue(0)
        self._compass_progress.setFormat("Pusula 1: %p%")
        self._compass_progress2.setValue(0)
        self._compass_progress2.setFormat("Pusula 2: %p%")
        self._compass_status.setText("⏳ Kalibrasyon başladı — Drone'u tüm eksenlerde döndürün")
        self._compass_status.setStyleSheet("color:#f0a500; font-weight:bold;")
        self._compass_log.append("[BAŞLAT] MAV_CMD_PREFLIGHT_CALIBRATION gönderildi")

    def _cancel_compass(self):
        # MAV_CMD_PREFLIGHT_CALIBRATION param2=0 → iptal
        if getattr(mavutil, "mavlink", None) is None:
            mavutil.set_dialect("ardupilotmega")
        self.send_command.emit(
            mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION, [0, 0, 0, 0, 0, 0, 0]
        )
        self._compass_drone.stop_spin()
        self._compass_status.setText("⛔ Kalibrasyon iptal edildi")
        self._compass_status.setStyleSheet("color:#ff6666;")
        self._compass_log.append("[İPTAL] Kalibrasyon iptal edildi")

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
        _STATUS = {0: ("BAŞARISIZ", "#ff4444"),
                   1: ("Kabul edilebilir", "#f0a500"),
                   2: ("OK", "#4ade80"),
                   3: ("MÜKEMMEL", "#4ade80")}
        label, color = _STATUS.get(cal_status, (f"status={cal_status}", "#aaa"))
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
            self._compass_status.setText(f"✓ Pusula {compass_id + 1} kalibrasyonu başarılı ({label})")
            self._compass_status.setStyleSheet(f"color:{color}; font-weight:bold;")
        else:
            self._compass_status.setText(f"✗ Pusula {compass_id + 1} kalibrasyonu başarısız!")
            self._compass_status.setStyleSheet("color:#ff4444; font-weight:bold;")

    # ──────────────────────────────────────────────────────────────
    # RADYO
    # ──────────────────────────────────────────────────────────────
    def _radio_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        info = QLabel(
            "1. 'Yakalamayı Başlat' butonuna basın.\n"
            "2. Tüm çubukları ve düğmeleri sınır konumlarına (min/max) getirin.\n"
            "3. 'Kaydet' butonuna basın."
        )
        info.setStyleSheet("color:#aaa;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Kanal barları
        grp = QGroupBox("RC Kanalları (PWM µs)")
        grp.setStyleSheet("QGroupBox{border:1px solid #0f3460;color:#aaa;font-weight:bold;padding-top:14px;}")
        gl = QVBoxLayout(grp)

        for ch, name in _RC_CHANNELS:
            row = QHBoxLayout()
            lbl = QLabel(f"CH{ch} {name}")
            lbl.setFixedWidth(90)
            lbl.setStyleSheet("color:#e0e0e0; font-size:11px;")

            bar = QProgressBar()
            bar.setRange(1000, 2000)
            bar.setValue(1500)
            bar.setFormat("%v µs")
            bar.setFixedHeight(20)
            bar.setStyleSheet(
                "QProgressBar{border:1px solid #0f3460;border-radius:3px;color:white;}"
                "QProgressBar::chunk{}"
            )
            self._rc_bars[ch] = bar

            min_lbl = QLabel("min: 1000")
            min_lbl.setStyleSheet("color:#888;font-size:10px;")
            min_lbl.setFixedWidth(70)
            self._rc_min_labels[ch] = min_lbl
            self._rc_min[ch] = 1000

            max_lbl = QLabel("max: 2000")
            max_lbl.setStyleSheet("color:#888;font-size:10px;")
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
        btn_cap.setStyleSheet("color:white;border-radius:4px;padding:6px 14px;font-weight:bold;")
        btn_cap.clicked.connect(self._start_rc_capture)
        btn_save = QPushButton("Kaydet / Bitir")
        btn_save.setStyleSheet(BTN_STYLE)
        btn_save.clicked.connect(self._stop_rc_capture)
        btn_reset = QPushButton("Sıfırla")
        btn_reset.setStyleSheet("color:#aaa;border-radius:4px;padding:6px 14px;")
        btn_reset.clicked.connect(self._reset_rc)
        btn_row.addWidget(btn_cap)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_reset)
        layout.addLayout(btn_row)

        self._rc_status = QLabel("Durum: Pasif")
        self._rc_status.setStyleSheet("color:#888;")
        layout.addWidget(self._rc_status)
        layout.addStretch()
        return w

    def _start_rc_capture(self):
        self._capturing_rc = True
        self._rc_status.setText("Durum: YAKALANIYOR — Tüm eksenleri sınır konumlara getirin")
        self._rc_status.setStyleSheet("color:#f0a500; font-weight:bold;")

    def _stop_rc_capture(self):
        self._capturing_rc = False
        self._rc_status.setText("Durum: Kaydedildi")
        self._rc_status.setStyleSheet("color:#4ade80;")
        if getattr(mavutil, "mavlink", None) is None:
            mavutil.set_dialect("ardupilotmega")

        self.send_command.emit(
            mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION, [0, 0, 0, 0, 0, 1, 0]
        )

    def _reset_rc(self):
        self._capturing_rc = False
        for ch in range(1, 9):
            self._rc_min[ch] = 1000
            self._rc_max[ch] = 2000
            if ch in self._rc_bars:
                self._rc_bars[ch].setValue(1500)
            if ch in self._rc_min_labels:
                self._rc_min_labels[ch].setText("min: 1000")
            if ch in self._rc_max_labels:
                self._rc_max_labels[ch].setText("max: 2000")
        self._rc_status.setText("Durum: Sıfırlandı")
        self._rc_status.setStyleSheet("color:#888;")

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
                if val < self._rc_min.get(ch, 2000):
                    self._rc_min[ch] = val
                    if ch in self._rc_min_labels:
                        self._rc_min_labels[ch].setText(f"min:{val}")
                if val > self._rc_max.get(ch, 1000):
                    self._rc_max[ch] = val
                    if ch in self._rc_max_labels:
                        self._rc_max_labels[ch].setText(f"max:{val}")

    # ──────────────────────────────────────────────────────────────
    # MOTOR TEST
    # ──────────────────────────────────────────────────────────────
    def _motor_tab(self):
        w = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)

        # Sol: X-config görsel
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        self._motor_drone = DroneVizWidget()
        ll.addWidget(self._motor_drone)

        # Açıklama
        info_lbl = QLabel("M1/M2=CCW (kırmızı)  M3/M4=CW (yeşil)\n"
                           "RPM = tahmini (gaz × 80)")
        info_lbl.setStyleSheet("color:#888;font-size:10px;")
        info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(info_lbl)
        split.addWidget(left)

        # Sağ: Motor kontrol
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 8, 8, 8)

        warning = QLabel("⚠  UYARI: Pervaneler takılıysa motor testi YAPMAYIN!")
        warning.setStyleSheet("color:#e94560;font-weight:bold;font-size:12px;")
        warning.setWordWrap(True)
        rl.addWidget(warning)

        grp = QGroupBox("Motor Gaz Kontrol")
        grp.setStyleSheet("QGroupBox{border:1px solid #0f3460;color:#aaa;font-weight:bold;padding-top:14px;}")
        gl = QVBoxLayout(grp)

        self._motor_spins = []
        self._motor_rpm_labels = []
        motor_positions = ["M1 (Ön-Sağ, CCW)", "M2 (Arka-Sol, CCW)",
                           "M3 (Ön-Sol, CW)", "M4 (Arka-Sağ, CW)"]
        for i in range(4):
            row = QHBoxLayout()
            lbl = QLabel(motor_positions[i])
            lbl.setFixedWidth(160)
            lbl.setStyleSheet("color:#e0e0e0;font-size:11px;")

            spin = QSpinBox()
            spin.setRange(0, 100)
            spin.setValue(0)
            spin.setSuffix(" %")
            spin.setFixedWidth(80)
            spin.setStyleSheet(
                "border:1px solid #0f3460;color:#e0e0e0;padding:4px;"
            )
            idx = i
            spin.valueChanged.connect(lambda v, n=idx: self._on_throttle_change(n, v))
            self._motor_spins.append(spin)

            rpm_lbl = QLabel("0 rpm")
            rpm_lbl.setStyleSheet("color:#ffcc00;font-size:11px;min-width:60px;")
            self._motor_rpm_labels.append(rpm_lbl)

            row.addWidget(lbl)
            row.addWidget(spin)
            row.addWidget(rpm_lbl)
            gl.addLayout(row)

        rl.addWidget(grp)

        btn_row = QHBoxLayout()
        btn_test = QPushButton("Seçili Motorları Test Et")
        btn_test.setStyleSheet(WARN_STYLE)
        btn_test.clicked.connect(self._on_motor_test)
        btn_all = QPushButton("Hepsini Test Et")
        btn_all.setStyleSheet("color:white;border-radius:4px;padding:8px 14px;font-weight:bold;")
        btn_all.clicked.connect(self._on_test_all)
        btn_stop = QPushButton("DURDUR")
        btn_stop.setStyleSheet("color:#e0e0e0;border-radius:4px;padding:8px 14px;font-weight:bold;")
        btn_stop.clicked.connect(self._on_motor_stop)
        btn_row.addWidget(btn_test)
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_stop)
        rl.addLayout(btn_row)
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

    def _on_motor_test(self):
        reply = QMessageBox.question(
            self, "Onay",
            "Motor testi başlatılacak.\nPervaneler çıkarılmış olmalı!\nDevam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ml = self._get_mavlink()
        for i, spin in enumerate(self._motor_spins):
            thr = spin.value()
            if thr > 0:
                self.send_command.emit(
                    ml.MAV_CMD_DO_MOTOR_TEST,
                    [i + 1, 0, thr, 3, 0, 0, 0],
                )

    def _on_test_all(self):
        reply = QMessageBox.question(
            self, "Onay",
            "TÜM motorlar %20 gaz ile 3 saniye test edilecek.\nPervaneler çıkarılmış olmalı!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ml = self._get_mavlink()
        for i in range(4):
            self._motor_spins[i].setValue(20)
            self.send_command.emit(
                ml.MAV_CMD_DO_MOTOR_TEST,
                [i + 1, 0, 20, 3, 0, 0, 0],
            )

    def _on_motor_stop(self):
        ml = self._get_mavlink()
        for i in range(4):
            self._motor_spins[i].setValue(0)
            self.send_command.emit(
                ml.MAV_CMD_DO_MOTOR_TEST,
                [i + 1, 0, 0, 0, 0, 0, 0],
            )
        self._motor_drone.set_highlight(-1)
