"""
calibration_view.py — Kalibrasyon & Kurulum (Modül 3)
Yalnızca ADMIN yetkisiyle erişilebilir.

Bu sınıf artık bir KAPSAYICIDIR: kendi içinde 3 klasik kalibrasyon sihirbazını
barındırır, geri kalan panelleri ayrı dosyalardan kurar ve hepsinin FC ile
haberleşmesini TEK bir kanal üzerinden yürütür.

Sekmeler:
  • İvmeölçer         — 6 pozisyonlu 3B sihirbaz (bu dosya)
  • Pusula            — dönen animasyon + MAG_CAL_PROGRESS/REPORT (bu dosya)
  • Radyo Kalibrasyon — 8 kanal uç yakalama → RCn_MIN/MAX yazma (bu dosya)
  • Sensör & Seviye   — ui/views/sensor_cal_view.py
  • Motor Test        — ui/views/motor_test_view.py   (⚠ motor döndürür)
  • Uçuş Modları      — ui/views/flight_modes_view.py
  • Uçuş Öncesi Kontrol — ui/views/preflight_check_view.py

MİMARİ — NEDEN alt paneller AYRI DOSYADA ama AYNI SEKMEDE:
(a) Bu dosya zaten 1500 satırdı; motor/mod/parametre panellerini de buraya
    koymak bakımı imkânsız hale getirirdi.
(b) main_window'un sekme çubuğu 12 sekmeyle DOLU ve kaydırma okları kapalı
    (setUsesScrollButtons(False)) — üç yeni ana sekme çubuğu taşırırdı.
(c) RBAC: main_window bu görünümü ADMIN'e kilitliyor (_apply_rbac). Alt
    paneller aynı kilidin ARKASINDA kaldığı için ayrıca yetkilendirme
    kurgusu gerekmedi — motor testi ve parametre yazma zaten ADMIN işi.

MİMARİ — NEDEN alt paneller kendi pyqtSignal'ini TANIMLAMIYOR:
Alt panel kendi `send_command` sinyalini tanımlayıp buradakine zincirleseydi,
"sinyal gerçekten bir alıcıya bağlı mı" denetimi (bu dosyadaki ölü-kontrol
hatalarının çözümü) HER ZAMAN doğru dönerdi — zincirin kendisi bir alıcıdır.
Bunun yerine alt paneller `ui/widgets/panel_ortak.MavKanal` alır; denetim
gerçekten uçtaki mavlink_worker bağlantısına bakar.
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
    QTabWidget, QPushButton, QProgressBar,
    QGroupBox, QTextEdit, QMessageBox, QSplitter, QSizePolicy,
    QAbstractSpinBox, QComboBox,
)
from pymavlink import mavutil

from core import theme
from ui.widgets.panel_ortak import MavKanal, ParamDeposu, ParamYazici

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
    # NEDEN: Uçuş modları ve uçuş öncesi parametre denetimi panelleri FC'deki
    # GERÇEK değerleri görmek zorunda. mavlink_worker'da tek parametre okuyan
    # bir slot YOK (yalnız request_parameters = TÜM liste), bu yüzden istek de
    # tam liste isteğidir. main_window bunu w.request_parameters'a bağlar.
    request_params = pyqtSignal()

    # FC STATUSTEXT anahtar kelimeleri (ArduPilot ivmeölçer kalibrasyon akışı)
    _ST_PROMPT = ("place vehicle", "press any key", "place aircraft")
    _ST_OK     = ("calibration successful", "calibration succeeded")
    _ST_FAIL   = ("calibration failed", "calibration cancelled",
                  "calibration canceled", "calibration timed out")

    # Zaman aşımı süreleri. NEDEN İKİ FARKLI DEĞER (2026-09-03 saha ölçümü):
    # eskiden tek bir 3000 ms vardı. Konumlar arası 3 sn FC daha örnekleme
    # bitirmeden doluyor, SON konumdan sonra ise FC'nin altı konumu çözüp
    # "Calibration successful" yollaması saniyeler sürüyor. 3 sn dolunca
    # _accel_active sıfırlanıyor, sonra gelen GERÇEK başarı mesajı
    # on_status_text'in ilk kapısından sessizce düşüyordu.
    _ACCEL_ADIM_ACK_MS  = 10_000    # konumlar arası istem beklemesi
    _ACCEL_SONUC_ACK_MS = 45_000    # son konumdan sonra sonuç beklemesi

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
        self._compass_active = False
        # Reboot kapısı için: ARM'lı araçta yeniden başlatma ENGELLENİR.
        # Telemetriden okunur (motor testiyle aynı kaynak: data['armed']).
        self._armed = False
        # NEDEN: İvmeölçer akışı artık FC onayına (STATUSTEXT) dayanıyor;
        # bu durum bayrakları olmadan "tamamlandı" iddiası doğrulanamıyordu.
        self._accel_active = False
        self._accel_awaiting_fc = False
        # FC sonucu 1 Hz TEKRARLANIR; aynı sonucu defalarca işlemeyelim.
        self._accel_sonuc_islendi = False
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
        self._wheel_guard = _WheelGuard(self)   # referans tutulmazsa GC'ye gider
        # Pusula: FC'den ilk MAG_CAL_PROGRESS gelmezse kullanıcıyı uyaran sayaç.
        # NEDEN: eski kod "FC ilerleme raporu bekleniyor" yazıp SONSUZA DEK
        # öyle kalıyordu; komut hiç uygulanmasa bile (ki uygulanmıyordu) panel
        # bunu asla söylemiyordu.
        self._compass_ilk_rapor_timer = QTimer(self)
        self._compass_ilk_rapor_timer.setSingleShot(True)
        self._compass_ilk_rapor_timer.timeout.connect(self._on_compass_rapor_yok)

        # ── Alt panellerin FC'ye giden ORTAK yolu ──
        # Emit'i ve "gerçekten bağlı mı" denetimini bu sınıf sağlar; alt paneller
        # kendi sinyalini tanımlamaz (bkz. modül başlığındaki MİMARİ notu).
        self._kanal = MavKanal(
            komut_gonder=self.send_command.emit,
            param_yaz=self.set_param.emit,
            komut_yolu_denetle=lambda: self._signal_connected(self.send_command),
            param_yolu_denetle=lambda: self._signal_connected(self.set_param),
        )
        # Parametre önbelleği — üç panel de aynı indirmeyi kullanır
        self._depo = ParamDeposu(
            istek_gonder=self.request_params.emit,
            istek_yolu_denetle=lambda: self._signal_connected(self.request_params),
            parent=self,
        )
        # Radyo kalibrasyonu yazmalarını GERİ OKUYARAK doğrulayan yazıcı.
        # NEDEN: PARAM_SET'in MAVLink'te ACK'i yoktur; eski kod RCn_MIN/MAX'i
        # emit edip koşulsuz "Kaydedildi" diyordu. Artık FC'nin PARAM_VALUE
        # yanıtı beklenir ve gelmezse AÇIKÇA "doğrulanamadı" denir.
        self._rc_yazici = ParamYazici(self._kanal, self)
        self._rc_yazici.bitti.connect(self._on_rc_yazma_bitti)
        # "Kaydet / Bitir" ile FC'ye YAZILAN aralık — TRIM denetimi bunu kullanır
        self._rc_yazilan: dict = {}

        # Alt paneller _build_ui içinde kurulur; kurulmazsa None kalır
        self._sensor_view = None
        self._motor_view = None
        self._modes_view = None
        self._preflight_view = None

        self._build_ui()
        self._install_wheel_guards()
        # Tema değişince satir-içi renkler son duruma göre yeniden yazılır
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ──────────────────────────────────────────────────────────────
    # TEMA — satir-içi renklerin tek kaynağı
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
        """Tema değişince TÜM satir-içi renkleri son duruma göre yeniden yazar.

        NEDEN: Kurulumda yazılan satir-içi renkler tema değişince BAYAT kalır
        (koyu tema yeşili beyaz zeminde okunmaz). Her durum metninin son
        token'ı saklandığı için burada anlamı bozmadan yeniden uygulanır.
        """
        self._accel_step_style(self._accel_lbl_token)
        self._apply_compass_status_style()
        self._apply_rc_status_style()
        self._accel_log.setStyleSheet(self._log_style())
        self._compass_log.setStyleSheet(self._log_style())
        # QPainter alet widget'ları renkleri paint sırasında okur → yeniden çiz.
        # NEDEN motor widget'ı burada YOK: motor görselleştirmesi artık
        # MotorTestView'a ait ve kendi tema aboneliğiyle yeniliyor.
        for wdg in (self._accel_drone, self._compass_drone):
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
        self._alt_panelleri_kur()
        # NEDEN sekme değişimini dinliyoruz: Motor Test sekmesinden çıkılırken
        # motorlar DURDURULMAK ZORUNDA. MotorTestView.hideEvent bunu zaten
        # yapıyor; buradaki bağlantı ikinci emniyet — Qt'nin bir sürümünde/
        # platformunda gizleme olayı gelmezse bile durdurma çalışır.
        self._tabs.currentChanged.connect(self._on_alt_sekme_degisti)
        layout.addWidget(self._tabs)

    def _alt_panelleri_kur(self) -> None:
        """Ayrı dosyalardaki panelleri kurar ve sekme olarak ekler.

        NEDEN geç (fonksiyon içi) import: motor_test_view, bu dosyadaki
        DroneVizWidget'i kullanıyor. Modül seviyesinde karşılıklı import
        dairesel olurdu; kurulum anında (modüller tam yüklendikten sonra)
        yapılan import bu döngüyü kırar.

        NEDEN her panel AYRI try: birinin (ör. eksik bir bağımlılık yüzünden)
        kurulamaması diğerlerini ve KLASİK kalibrasyon sekmelerini götürmemeli
        — kalibrasyon paneli sahada tek başına kritik bir araçtır.
        """
        def _hata_sekmesi(ad: str, exc: Exception) -> QLabel:
            """Kurulamayan panelin yerine, nedeni YAZAN bir sekme koyar.

            NEDEN boş sekme değil: sessizce eksik bir sekme, operatörün
            "panel neden yok?" diye aramasına yol açar. Hata metni ekranda.
            """
            lbl = QLabel(
                f"{ad} paneli KURULAMADI.\n\nHata: {exc}\n\n"
                "Diğer kalibrasyon sekmeleri etkilenmedi. "
                "Ayrıntı için uygulama günlüğüne bakın."
            )
            lbl.setWordWrap(True)
            lbl.setObjectName("dangerText")
            return lbl

        try:
            from ui.views.sensor_cal_view import SensorCalView
            self._sensor_view = SensorCalView(self._kanal, self._depo)
            self._tabs.addTab(self._sensor_view, "Sensör & Seviye")
        except Exception as exc:
            logger.exception("Sensör/Seviye paneli kurulamadı")
            self._tabs.addTab(_hata_sekmesi("Sensör & Seviye", exc), "Sensör & Seviye")

        try:
            from ui.views.motor_test_view import MotorTestView
            self._motor_view = MotorTestView(self._kanal)
            self._tabs.addTab(self._motor_view, "⚠ Motor Test")
        except Exception as exc:
            logger.exception("Motor test paneli kurulamadı")
            self._tabs.addTab(_hata_sekmesi("Motor Test", exc), "⚠ Motor Test")

        try:
            from ui.views.flight_modes_view import FlightModesView
            self._modes_view = FlightModesView(self._kanal, self._depo)
            self._tabs.addTab(self._modes_view, "Uçuş Modları")
        except Exception as exc:
            logger.exception("Uçuş modları paneli kurulamadı")
            self._tabs.addTab(_hata_sekmesi("Uçuş Modları", exc), "Uçuş Modları")

        try:
            from ui.views.preflight_check_view import PreflightCheckView
            self._preflight_view = PreflightCheckView(self._kanal, self._depo)
            self._tabs.addTab(self._preflight_view, "Uçuş Öncesi Kontrol")
        except Exception as exc:
            logger.exception("Uçuş öncesi kontrol paneli kurulamadı")
            self._tabs.addTab(_hata_sekmesi("Uçuş Öncesi Kontrol", exc),
                              "Uçuş Öncesi Kontrol")

    def _on_alt_sekme_degisti(self, _index: int) -> None:
        """Motor Test sekmesi AKTİF DEĞİLSE motorları durdurur."""
        if self._motor_view is None:
            return
        if self._tabs.currentWidget() is not self._motor_view:
            try:
                self._motor_view.panel_terk_edildi()
            except Exception:
                logger.exception("Motor paneli durdurulurken hata")

    # ──────────────────────────────────────────────────────────────
    # ALT PANELLERE DAĞITIM — main_window yalnızca bu sınıfa bağlanır
    # ──────────────────────────────────────────────────────────────
    @pyqtSlot(str, float)
    def on_param_received(self, ad: str, deger: float) -> None:
        """mavlink_worker.param_received → paylaşılan parametre deposu.

        Depo, `degisti` sinyaliyle tüm alt panelleri besler; ayrıca yazma
        doğrulaması (ParamYazici) da bu akıştan gelen PARAM_VALUE'yu kullanır.
        """
        self._depo.param_geldi(ad, deger)
        self._rc_yazici.param_geldi(ad, deger)
        if ad.startswith("RC") and ad.endswith("_TRIM"):
            self._rc_trim_denetle()
        if self._motor_view is not None:
            self._motor_view.on_param(ad, deger)

    @pyqtSlot(bool)
    def on_connection_changed(self, bagli: bool) -> None:
        """FC linki kurulunca/kopunca tüm alt panelleri günceller.

        NEDEN gerekli: "bağlantı yoksa buton KAPALI ve nedeni YAZILI" kuralı,
        panellerin link durumunu bilmesini gerektiriyor. Sinyal bağlantısının
        varlığı (receivers>0) linkin varlığını KANITLAMAZ — worker bağlı ama
        telemetri kopuk olabilir.
        """
        self._kanal.link_ayarla(bagli)
        if not bagli:
            # Link koptuysa motor testine devam edilemez; panel kendi
            # durumunu da temizler.
            self._compass_active = False
        for panel in (self._sensor_view, self._motor_view,
                      self._modes_view, self._preflight_view):
            fn = getattr(panel, "on_connection_changed", None)
            if callable(fn):
                try:
                    fn(bagli)
                except Exception:
                    logger.exception("Alt panel bağlantı bildirimi hatası")
        self._guncelle_reboot_butonu()

    # ── ARACI YENİDEN BAŞLAT ────────────────────────────────────────────
    # param1=1 → yalnızca uçuş kontrolcüsünü yeniden başlat.
    # param1=3 BOOTLOADER'a düşürür ve araç telemetriye BİR DAHA dönmez
    # (kurtarmak için USB + yer istasyonu gerekir) — bu yüzden sabit 1.
    _REBOOT_PARAMS = [1, 0, 0, 0, 0, 0, 0]

    def _guncelle_reboot_butonu(self) -> None:
        """Reboot düğmesinin açık/kapalı durumunu ve NEDENİNİ yazar."""
        if not hasattr(self, "_btn_reboot"):
            return
        neden = self._kanal.neden_kapali()
        if not neden and self._armed:
            # Uçan/ARM'lı araçta reboot = motorların anında durması.
            neden = "Araç ARM'LI — reboot engellendi. Önce DISARM edin."
        self._btn_reboot.setEnabled(not neden)
        self._reboot_lbl.setText(neden)
        self._reboot_lbl.setStyleSheet(
            f"color:{theme.c('danger' if self._armed else 'text_muted')};")

    def _reboot_fc(self) -> None:
        """Uçuş kontrolcüsünü yeniden başlatır (onay ister)."""
        neden = self._kanal.neden_kapali()
        if neden or self._armed:
            aciklama = neden or "araç ARM'lı"
            self._compass_log.append("[RET] Reboot gönderilmedi: " + aciklama)
            return
        yanit = QMessageBox.question(
            self, "Aracı Yeniden Başlat",
            "Uçuş kontrolcüsü YENİDEN BAŞLATILACAK.\n\n"
            "• Telemetri linki kopacak, birkaç saniye sonra geri gelir.\n"
            "• Kaydedilmemiş parametreler KAYBOLUR.\n"
            "• Araç havadayken ASLA yapılmamalıdır.\n\n"
            "Devam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if yanit != QMessageBox.StandardButton.Yes:
            return
        gonderildi = self._kanal.komut(
            self._get_mavlink().MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
            self._REBOOT_PARAMS)
        if gonderildi:
            self._compass_log.append(
                "[REBOOT] Yeniden başlatma komutu gönderildi — link birkaç "
                "saniye kopacak. FC hükmü (COMMAND_ACK) aşağıda görünecek.")
            self._reboot_lbl.setText(
                "Reboot gönderildi — telemetri geri gelene kadar bekleyin.")
        else:
            self._compass_log.append(
                "[HATA] Reboot komutu gönderilemedi (link yok).")

    def acil_motor_durdur(self) -> None:
        """Dışarıdan (main_window sekme değişimi / kapanış) çağrılır."""
        if self._motor_view is not None:
            try:
                self._motor_view.panel_terk_edildi()
            except Exception:
                logger.exception("Acil motor durdurma hatası")

    def hideEvent(self, event):  # noqa: N802 (Qt API)
        # Kalibrasyon sekmesinden çıkıldığında (QTabWidget sayfayı gizler)
        # motorlar durdurulur — panelden çıkınca motor dönmeye devam edemez.
        try:
            self.acil_motor_durdur()
        except Exception:
            logger.exception("Kalibrasyon görünümü gizlenirken hata")
        super().hideEvent(event)

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
        self._accel_log.document().setMaximumBlockCount(500)   # sınırsız büyümeyi engelle
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
        self._accel_sonuc_islendi = False
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
        self._accel_ack_timer.start(
            self._ACCEL_SONUC_ACK_MS if self._accel_step >= len(_ACCEL_STEPS)
            else self._ACCEL_ADIM_ACK_MS)

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
                "⚠ Tüm konumlar gönderildi — FC'den sonuç mesajı GELMEDİ.\n"
                "Kalibrasyonun BAŞARILI olduğu DOĞRULANMADI. Dinleme sürüyor."
            )
            self._accel_step_style("warning")
            self._accel_log.append(
                "[UYARI] FC sonuç mesajı (STATUSTEXT) alınamadı — dinleme sürüyor")
            # NEDEN _accel_active KAPATILMIYOR (2026-09-03): kapatılırsa
            # on_status_text'in ilk kapısı, SONRADAN gelen gerçek
            # "Calibration successful" mesajını sessizce yutuyordu. Kullanıcı
            # kilitlenmesin diye Başlat açılır; geç gelen sonuç yine işlenir.
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

        # ── Alt panellere DAĞIT ──
        # NEDEN buradan: main_window w.status_text_received'i YALNIZCA bu
        # görünüme bağlıyor. Motor testi ve seviye/baro/jiroskop kalibrasyonu
        # sonuçlarını da SADECE STATUSTEXT'ten öğrenebiliyoruz (mavlink_worker
        # COMMAND_ACK yaymıyor) — dağıtım yapılmazsa o paneller kör kalır.
        for panel in (self._motor_view, self._sensor_view):
            fn = getattr(panel, "on_status_text", None)
            if callable(fn):
                try:
                    fn(text, severity)
                except Exception:
                    logger.exception("Alt panel STATUSTEXT işleme hatası")

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

    # FC hükmünü operatör diline çeviren tablo. Ham "result=4" saha ekibine
    # hiçbir şey anlatmıyordu.
    _ACK_ADLARI = {
        0: "kabul edildi",
        1: "GEÇİCİ RET — araç şu an uygun değil (armed mi? güvenlik anahtarı?)",
        2: "GEÇERSİZ PARAMETRE",
        3: "DESTEKLENMİYOR — bu FC sürümü bu komutu tanımıyor",
        4: "BAŞARISIZ — FC komutu reddetti",
        5: "sürüyor",
        6: "iptal edildi",
    }
    # İlgilendiğimiz komutlar: kalibrasyon başlat / konum bildir / pusula / reboot
    _ACK_KOMUT_ADLARI = {
        241:   "Kalibrasyon başlat (PREFLIGHT_CALIBRATION)",
        42429: "İvmeölçer konum bildir (ACCELCAL_VEHICLE_POS)",
        42424: "Pusula kalibrasyonu (DO_START_MAG_CAL)",
        42425: "Pusula kalibrasyonu durdur",
        246:   "Yeniden başlat (PREFLIGHT_REBOOT_SHUTDOWN)",
    }

    @pyqtSlot(int, int)
    def on_command_ack(self, cmd_id: int, result: int) -> None:
        """FC'nin komut hükmünü panele yansıtır.

        NEDEN VAR (2026-09-03 saha ölçümü): operatör ivmeölçer kalibrasyonunu
        başlattı, altı konumu da gönderdi (GCS logu: CMD id=241, sonra
        42429 pos=1..6), FC HİÇBİR STATUSTEXT döndürmedi. Komutun kabul mü
        edildiği red mi olduğu YKİ'de görünmüyordu çünkü COMMAND_ACK hiç
        okunmuyordu — panel bu sessizliği "devam ediyor" sanıp operatörü
        altı adım boyunca körlemesine tıklattı. Artık ret ANINDA yazılır.
        """
        if cmd_id not in self._ACK_KOMUT_ADLARI:
            return
        ad = self._ACK_KOMUT_ADLARI[cmd_id]
        hukum = self._ACK_ADLARI.get(result, f"bilinmeyen sonuç ({result})")
        satir = f"[FC HÜKMÜ] {ad}: {hukum}"

        if cmd_id == 42424 or cmd_id == 42425:
            self._compass_log.append(satir)
        elif cmd_id == 246:
            self._compass_log.append(satir)
        else:
            self._accel_log.append(satir)

        # Kabul (0) ve "sürüyor" (5) dışındaki her sonuç REDDİR.
        if result in (0, 5):
            return

        if cmd_id in (241, 42429) and self._accel_active:
            # Kalibrasyon fiilen başlamadı/ilerlemedi — sahte ilerlemeyi durdur.
            self._accel_ack_timer.stop()
            self._accel_active = False
            self._accel_awaiting_fc = False
            self._btn_accel_next.setEnabled(False)
            self._btn_accel_start.setEnabled(True)
            self._accel_step_lbl.setText(
                f"✗ FC komutu REDDETTİ: {hukum}\n"
                "Kalibrasyon BAŞLAMADI — aracın disarm ve düz durduğundan,\n"
                "güvenlik anahtarının basılı olmadığından emin olup tekrar deneyin."
            )
            self._accel_step_style("danger")

    _ACCELCAL_BASARILI = 16777215
    _ACCELCAL_BASARISIZ = 16777216
    _ACCELCAL_KONUM_ADI = {
        1: "DÜZ/YATAY", 2: "SOL YANA", 3: "SAĞ YANA",
        4: "BURUN AŞAĞI", 5: "BURUN YUKARI", 6: "SIRTÜSTÜ",
    }

    @pyqtSlot(int)
    def on_accelcal_pos(self, poz: int) -> None:
        """Aracın ivmeölçer kalibrasyonu konum/sonuç kanalı (COMMAND_LONG 42429).

        NEDEN ASIL KANAL BU (ArduPilot kaynağından doğrulandı, 2026-09-03):
        AP_AccelCal.cpp:402 gcs_vehicle_position() ilk ACCELCAL_VEHICLE_POS
        komutunda _use_gcs_snoop=false yapar — yani "Place vehicle ... and
        press any key." STATUSTEXT istemleri İLK ADIMDAN SONRA HİÇ GELMEZ.
        Panel yalnız STATUSTEXT dinlediği sürece 2..6. adımlarda FC'den asla
        onay göremezdi. Araç bunun yerine GCS'e COMMAND_LONG(42429) yollar ve
        1 Hz TEKRARLAR (GCS_Common.cpp:3441, AP_AccelCal.cpp:113-116,166-183).

        TUZAK: sonuç tekrarı, BİR ÖNCEKİ kalibrasyondan kalmış olabilir
        (_last_result yalnız start()/cancel() ile sıfırlanır). Bu yüzden
        sonuç YALNIZCA bu panel bir kalibrasyon başlattıysa kabul edilir.
        """
        if not self._accel_active:
            return

        if poz == self._ACCELCAL_BASARILI:
            if self._accel_sonuc_islendi:
                return
            self._accel_sonuc_islendi = True
            self._accel_finish(True, "FC: kalibrasyon BAŞARILI (ACCELCAL_VEHICLE_POS)")
            return
        if poz == self._ACCELCAL_BASARISIZ:
            if self._accel_sonuc_islendi:
                return
            self._accel_sonuc_islendi = True
            self._accel_finish(False, "FC: kalibrasyon BAŞARISIZ (ACCELCAL_VEHICLE_POS)")
            return

        if not 1 <= poz <= 6:
            return
        # FC bu konumu BEKLİYOR demektir → operatör aracı oraya koyup
        # 'Sonraki'ye basacak. Adım sayacını FC'ye göre hizala; panelin kendi
        # sayması ile FC'nin beklediği konum ayrışırsa FC kazanır.
        self._accel_ack_timer.stop()
        self._accel_awaiting_fc = False
        self._accel_step = poz - 1
        self._accel_progress.setValue(poz)
        self._model3d_poz(self._accel_step)
        if self._accel_step < len(_ACCEL_STEPS):
            _view, msg = _ACCEL_STEPS[self._accel_step]
            r, p, y = self._ACCEL_ATT.get(_view, (0, 0, 0))
            self._accel_drone.set_target_attitude(r, p, y)
            self._accel_step_lbl.setText(
                msg + "\n(FC bu konumu BEKLİYOR — yerleştirip 'Sonraki'ye basın)")
        else:
            self._accel_step_lbl.setText(
                f"FC {poz}. konumu bekliyor — yerleştirip 'Sonraki'ye basın.")
        self._accel_step_style("warning")
        self._btn_accel_next.setEnabled(True)
        self._accel_log.append(
            f"[FC İSTER] {poz}. konum: {self._ACCELCAL_KONUM_ADI.get(poz, '?')}")

    # ──────────────────────────────────────────────────────────────
    # PUSULA
    # ──────────────────────────────────────────────────────────────
    def _compass_tab(self):
        w = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        # 3B: pusula kalibrasyonu aracin HER EKSENDE cevrilmesini ister;
        # donen model bunu metinden cok daha iyi anlatir. WebEngine yoksa
        # mevcut 2B cizime dusulur.
        self._compass_model3d = None
        try:
            from ui.widgets.model3d_view import Model3DView
            self._compass_model3d = Model3DView()
            self._compass_model3d.setMinimumSize(280, 260)
            ll.addWidget(self._compass_model3d, 1)
        except Exception:
            logger.exception("Pusula 3B gorunumu kurulamadi")
        self._compass_drone = Drone3DWidget()
        if self._compass_model3d is None:
            ll.addWidget(self._compass_drone)
        else:
            self._compass_drone.setVisible(False)
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

        # ── ARACI YENİDEN BAŞLAT ────────────────────────────────────────────
        # NEDEN BURADA: ArduPilot pusula kalibrasyonundan sonra ayarları
        # uygulamak için reboot İSTER — sahada ölçüldü (2026-09-03), FC
        # dakikalarca "PreArm: Compass calibrated requires reboot" bastı ve
        # YKİ'de reboot yolu olmadığı için operatör Mission Planner'a geçmek
        # zorunda kalıyordu. Bu yüzden düğme pusula sekmesinde.
        self._btn_reboot = QPushButton("⟳ Aracı Yeniden Başlat (FC reboot)")
        self._btn_reboot.setObjectName("dangerButton")
        self._btn_reboot.setMinimumHeight(30)
        self._btn_reboot.setToolTip(
            "Uçuş kontrol kartını yeniden başlatır.\n"
            "Pusula kalibrasyonundan sonra ayarların uygulanması için gerekir.\n"
            "ARM'lıyken veya bağlantı yokken ÇALIŞMAZ. Telemetri kısa süre kopar."
        )
        self._btn_reboot.clicked.connect(self._reboot_fc)
        rl.addWidget(self._btn_reboot)

        self._reboot_lbl = QLabel("")
        self._reboot_lbl.setWordWrap(True)
        self._reboot_lbl.setObjectName("mutedLabel")
        rl.addWidget(self._reboot_lbl)
        self._guncelle_reboot_butonu()

        self._compass_log = QTextEdit()
        self._compass_log.setReadOnly(True)
        self._compass_log.document().setMaximumBlockCount(500)   # sınırsız büyümeyi engelle
        self._compass_log.setStyleSheet(self._log_style())
        rl.addWidget(self._compass_log)
        split.addWidget(right)
        split.setSizes([300, 400])

        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        return w

    # ── Pusula kalibrasyonu komut sabitleri ──
    # MAV_CMD_DO_START_MAG_CAL (42424) param şeması — ArduPilot kaynağından
    # DOĞRULANDI (libraries/AP_Compass/AP_Compass_Calibration.cpp):
    #   param1 = pusula bit maskesi (0 = kullanılan TÜM pusulalar)
    #   param2 = başarısızlıkta otomatik tekrar (0 = hayır)
    #   param3 = otomatik kaydet (1 = evet)
    #   param4 = başlama gecikmesi (s)
    #   param5 = otomatik yeniden başlat (0 = hayır)
    _MAG_CAL_MASK = 0        # tüm pusulalar
    _MAG_CAL_RETRY = 0
    # NEDEN autosave = 1: autosave 0 olsaydı başarılı kalibrasyondan sonra
    # ofsetlerin KAYDEDİLMESİ için GCS'in ayrıca MAV_CMD_DO_ACCEPT_MAG_CAL
    # (42425) göndermesi gerekirdi. Bu panelde "Kabul Et" adımı YOK; autosave=0
    # bırakmak, kullanıcının başarılı gördüğü kalibrasyonun FC'ye hiç
    # yazılmaması demekti (sessiz veri kaybı).
    _MAG_CAL_AUTOSAVE = 1
    _MAG_CAL_DELAY_S = 0
    _MAG_CAL_AUTOREBOOT = 0
    # FC ilerleme raporu bu süre içinde gelmezse kullanıcı uyarılır.
    _MAG_CAL_ILK_RAPOR_S = 8

    def _start_compass(self):
        """Onboard (uçuş içi) pusula kalibrasyonunu BAŞLATIR.

        NEDEN DEĞİŞTİ (ÖLÜ BUTON DÜZELTMESİ): Bu buton eskiden
        MAV_CMD_PREFLIGHT_CALIBRATION'ı param2=1 ile gönderiyordu. MAVLink
        şemasında param2 gerçekten "magnetometer calibration"dır, AMA ArduPilot
        bu dalı UYGULAMIYOR: GCS_Common.cpp::_handle_command_preflight_calibration
        yalnızca param1 (jiro), param3 (baro), param5 (ivmeölçer 1/2/4/…) ve
        param2 == 76 (mevcut kalibrasyonu ZORLA KAYDET) durumlarını ele alıyor;
        geri kalan her şey için MAV_RESULT_UNSUPPORTED dönüyor. Yani bu düğme
        gerçek FC'de HİÇBİR ŞEY YAPMIYORDU — animasyon dönüyor, ilerleme çubuğu
        hiç ilerlemiyordu. (Aynı dosyadaki İPTAL düğmesi ZATEN doğru API'yi —
        DO_CANCEL_MAG_CAL — kullanıyordu; başlat/iptal çifti tutarsızdı.)
        Doğrulama: /home/ash/ardupilot @27b35986e9 kaynak kodundan okundu.
        Doğru komut MAV_CMD_DO_START_MAG_CAL'dir (42424).
        """
        ml = self._get_mavlink()
        cmd = getattr(ml, "MAV_CMD_DO_START_MAG_CAL", 42424)
        # NEDEN: Komut kimseye gitmiyorsa "başladı" demek yanıltıcı olur.
        if not self._signal_connected(self.send_command):
            self._compass_status_set(
                "✗ Komut kanalı bağlı değil — komut gönderilmedi", "danger", bold=True
            )
            self._compass_log.append("[HATA] send_command sinyali bağlı değil")
            return

        self.send_command.emit(cmd, [
            self._MAG_CAL_MASK, self._MAG_CAL_RETRY, self._MAG_CAL_AUTOSAVE,
            self._MAG_CAL_DELAY_S, self._MAG_CAL_AUTOREBOOT, 0, 0,
        ])
        self._compass_active = True
        # FC hiç ilerleme raporu göndermezse kullanıcı sonsuza dek "bekleniyor"
        # görmesin — bu, düğmenin ölü olduğu dönemde tam olarak yaşanan durumdu.
        self._compass_ilk_rapor_timer.start(self._MAG_CAL_ILK_RAPOR_S * 1000)
        self._compass_drone.start_spin()
        if self._compass_model3d is not None:
            self._compass_model3d.rotation_demo(True)
            self._compass_model3d.set_info("Araci her eksende yavasca cevirin")
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
        self._compass_log.append(
            f"[GÖNDER] DO_START_MAG_CAL ({cmd}) mask=0(tümü) retry=0 autosave=1"
        )

    def _on_compass_rapor_yok(self):
        """FC belirlenen sürede hiç MAG_CAL_PROGRESS göndermedi."""
        if not self._compass_active:
            return
        self._compass_status_set(
            f"⚠ {self._MAG_CAL_ILK_RAPOR_S} s içinde FC'den ilerleme raporu GELMEDİ. "
            "Kalibrasyon BAŞLAMAMIŞ olabilir. Kontrol edin: araç DISARM mı, "
            "FC pusula kalibrasyonunu destekliyor mu, telemetri linki sağlam mı?",
            "danger", bold=True,
        )
        self._compass_log.append(
            "[UYARI] MAG_CAL_PROGRESS alınamadı — kalibrasyonun başladığı "
            "DOĞRULANAMADI"
        )

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
        self._compass_ilk_rapor_timer.stop()
        self._compass_drone.stop_spin()
        if self._compass_model3d is not None:
            self._compass_model3d.rotation_demo(False)
        self._compass_status_set(
            "⛔ İptal komutu gönderildi (DO_CANCEL_MAG_CAL)", "danger"
        )
        self._compass_log.append(f"[İPTAL] DO_CANCEL_MAG_CAL ({cmd}) gönderildi")

    @pyqtSlot(int, int, float)
    def on_mag_cal_progress(self, compass_id: int, pct: int, fitness: float) -> None:
        """MAVLink MAG_CAL_PROGRESS mesajı geldiğinde çağrılır."""
        # İlk rapor geldi → "FC yanıt vermiyor" uyarısına gerek yok
        self._compass_ilk_rapor_timer.stop()
        if self._compass_active:
            self._compass_status_set(
                "Kalibrasyon SÜRÜYOR — aracı tüm eksenlerde yavaşça çevirin",
                "warning", bold=True,
            )
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
        self._compass_ilk_rapor_timer.stop()
        # Tüm pusulalar tamamlandıysa animasyonu durdur
        if self._compass_progress.value() == 100 and self._compass_progress2.value() == 100:
            self._compass_drone.stop_spin()
        if self._compass_model3d is not None:
            self._compass_model3d.rotation_demo(False)
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
        btn_trim = QPushButton("Nötr Konumları (RCn_TRIM) Yaz")
        btn_trim.setObjectName("warningButton")
        btn_trim.setMinimumHeight(30)
        btn_trim.setToolTip(
            "Çubuklar ORTADA ve gaz EN ALTTA iken basın — o anki PWM'ler "
            "RC1/2/3/4_TRIM olarak yazılır."
        )
        btn_trim.clicked.connect(self._write_rc_trims)
        btn_reset = QPushButton("Sıfırla")
        btn_reset.setMinimumHeight(30)
        btn_reset.clicked.connect(self._reset_rc)
        btn_row.addWidget(btn_cap)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_trim)
        btn_row.addWidget(btn_reset)
        layout.addLayout(btn_row)

        self._rc_status = QLabel("Durum: Pasif")
        self._apply_rc_status_style()
        layout.addWidget(self._rc_status)

        # ── TRIM denetimi ──
        # NEDEN bu uyarı var: ArduPilot ön-arm denetimi
        # (AP_Arming::rc_calibration_checks) RCn_TRIM'in RCn_MIN..RCn_MAX
        # ARALIĞINDA olmasını ŞART koşuyor; değilse "RC1_MIN is greater than
        # RC1_TRIM" gibi bir hata verip ARM ETMİYOR. Eski panel MIN/MAX yazıp
        # TRIM'e hiç bakmıyordu — dar aralıklı yeni bir kalibrasyondan sonra
        # araç sahada ARM OLMAYABİLİRDİ ve nedeni bu panelde görünmezdi.
        self._rc_trim_lbl = QLabel(
            "RCn_TRIM durumu: FC'den parametre okunmadı "
            "(Uçuş Öncesi Kontrol sekmesindeki 'FC'den OKU' bunu da getirir)."
        )
        self._rc_trim_lbl.setWordWrap(True)
        self._rc_trim_lbl.setObjectName("mutedLabel")
        layout.addWidget(self._rc_trim_lbl)
        layout.addStretch()
        return w

    # ── TRIM denetimi / yazımı ───────────────────────────────────────────
    # Ana kumanda kanalları: roll, pitch, gaz, yaw
    _RC_ANA_KANALLAR = (1, 2, 3, 4)

    def _rc_trim_denetle(self) -> None:
        """FC'deki RCn_TRIM değerlerinin MIN..MAX aralığında olup olmadığını yazar."""
        sorunlar = []
        okunan = 0
        for ch in self._RC_ANA_KANALLAR:
            trim = self._depo.get(f"RC{ch}_TRIM")
            lo = self._depo.get(f"RC{ch}_MIN")
            hi = self._depo.get(f"RC{ch}_MAX")
            # Yeni yazdığımız aralık varsa ONU esas al (FC henüz geri okunmamış olabilir)
            if ch in self._rc_yazilan:
                lo, hi = self._rc_yazilan[ch]
            if trim is None or lo is None or hi is None:
                continue
            okunan += 1
            if trim < lo:
                sorunlar.append(f"CH{ch}: TRIM {trim:.0f} < MIN {lo:.0f}")
            elif trim > hi:
                sorunlar.append(f"CH{ch}: TRIM {trim:.0f} > MAX {hi:.0f}")
        if okunan == 0:
            self._rc_trim_lbl.setText(
                "RCn_TRIM durumu: FC'den parametre okunmadı "
                "(Uçuş Öncesi Kontrol sekmesindeki 'FC'den OKU' bunu da getirir)."
            )
            self._rc_trim_lbl.setStyleSheet("")
        elif sorunlar:
            self._rc_trim_lbl.setText(
                "⛔ RCn_TRIM aralık DIŞINDA — ArduPilot bu hâlde ARM ETMEZ "
                "('RCn_MIN is greater than RCn_TRIM'):\n   " + "\n   ".join(sorunlar)
                + "\nÇubukları ortalayıp gazı en alta alın ve "
                "'Nötr Konumları (RCn_TRIM) Yaz'a basın."
            )
            self._rc_trim_lbl.setStyleSheet(f"color:{theme.c('danger')};font-weight:bold;")
        else:
            self._rc_trim_lbl.setText(
                f"✓ RCn_TRIM ({okunan} kanal) MIN..MAX aralığında — ön-arm RC "
                "denetimi bu yönden geçer."
            )
            self._rc_trim_lbl.setStyleSheet(f"color:{theme.c('success')};")

    def _write_rc_trims(self) -> None:
        """O anki çubuk konumlarını RC1..RC4_TRIM olarak FC'ye yazar.

        NEDEN ayrı düğme (otomatik değil): TRIM, çubuğun NÖTR konumudur.
        'Kaydet / Bitir'in içinde otomatik yazılsaydı, kullanıcı uçları
        gezdirdikten sonra çubuğu nereye bıraktıysa orası nötr sayılırdı ve
        araç uçuşta o yöne sürekli kayardı. Bu yüzden kullanıcıdan çubukları
        ORTALAMASI açıkça isteniyor ve yazma ayrı bir eylem.
        """
        if not self._signal_connected(self.set_param):
            self._rc_fail("Durum: TRIM YAZILMADI — parametre kanalı bağlı değil")
            return
        cevap = QMessageBox.question(
            self, "Nötr konumları yazılacak",
            "Şu anda:\n"
            "  • Roll / Pitch / Yaw çubukları TAM ORTADA,\n"
            "  • Gaz çubuğu EN ALTTA\n"
            "olmalı. O anki PWM değerleri RC1/2/3/4_TRIM olarak FC'ye yazılacak "
            "ve geri okunarak doğrulanacak.\n\nDevam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if cevap != QMessageBox.StandardButton.Yes:
            self._rc_status_set("Durum: TRIM yazımı iptal edildi")
            return

        istekler = {}
        sorunlar = []
        for ch in self._RC_ANA_KANALLAR:
            bar = self._rc_bars.get(ch)
            if bar is None:
                continue
            val = bar.value()
            lo, hi = self._rc_yazilan.get(
                ch, (self._depo.get(f"RC{ch}_MIN"), self._depo.get(f"RC{ch}_MAX"))
            )
            # NEDEN kelepçelemiyoruz, REDDEDİYORUZ: aralık dışındaki bir değeri
            # sessizce kırpmak, yanlış bir nötr noktasını "düzeltilmiş" gibi
            # göstermek olurdu. Aralık dışıysa gerçekten bir sorun vardır.
            if lo is not None and hi is not None and not (lo <= val <= hi):
                sorunlar.append(f"CH{ch}: {val} µs, aralık {lo:.0f}-{hi:.0f} DIŞINDA")
                continue
            istekler[f"RC{ch}_TRIM"] = float(val)

        if sorunlar:
            QMessageBox.warning(
                self, "TRIM yazılamadı",
                "Aşağıdaki kanalların o anki PWM'i kendi MIN..MAX aralığının "
                "dışında — hiçbir TRIM yazılmadı:\n\n" + "\n".join(sorunlar)
                + "\n\nÖnce uç yakalamayı ('Yakalamayı Başlat' → uçlar → "
                "'Kaydet / Bitir') tamamlayın.",
            )
            self._rc_fail("Durum: TRIM YAZILMADI — değerler aralık dışında")
            return
        if not istekler:
            self._rc_fail("Durum: TRIM YAZILMADI — RC verisi yok (alıcı bağlı mı?)")
            return
        if not self._rc_yazici.yaz(istekler):
            self._rc_fail("Durum: TRIM YAZILMADI — yazma kanalı meşgul/bağlı değil")
            return
        self._rc_status_set(
            f"{len(istekler)} TRIM yazıldı — FC'den geri okuma bekleniyor…",
            "warning", bold=True,
        )

    def _on_rc_yazma_bitti(self, basarili: int, toplam: int) -> None:
        """ParamYazici bitti — kullanıcıya DOĞRULANMIŞ sonucu söyler."""
        if basarili == toplam:
            self._rc_status_set(
                f"✓ {basarili}/{toplam} parametre FC'den GERİ OKUNARAK doğrulandı",
                "success", bold=True,
            )
        else:
            self._rc_status_set(
                f"✗ {basarili}/{toplam} doğrulandı — kalanların FC'ye yazıldığı "
                "KANITLANAMADI (paket kaybı ya da FC reddi)",
                "danger", bold=True,
            )
        self._rc_trim_denetle()

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

        # NEDEN ParamYazici: eski kod set_param.emit edip HEMEN "Kaydedildi"
        # diyordu. PARAM_SET'in MAVLink'te ACK'i olmadığı için bu iddia
        # kanıtsızdı — paket kaybında kullanıcı kalibrasyonun yazıldığını
        # sanıyordu. Artık her parametre FC'den GERİ OKUNARAK doğrulanıyor,
        # yanıt gelmezse tekrarlanıyor ve sonuç _on_rc_yazma_bitti'de
        # DÜRÜSTÇE raporlanıyor.
        istekler = {}
        self._rc_yazilan = {}
        for ch, (lo, hi) in sorted(valid.items()):
            istekler[f"RC{ch}_MIN"] = float(lo)
            istekler[f"RC{ch}_MAX"] = float(hi)
            self._rc_yazilan[ch] = (float(lo), float(hi))
            logger.info("RC kalibrasyon: RC%d_MIN=%d RC%d_MAX=%d", ch, lo, ch, hi)

        if not self._rc_yazici.yaz(istekler):
            self._rc_fail("Durum: KAYDEDİLMEDİ — yazma kanalı meşgul/bağlı değil")
            return

        self._rc_capture_started = False
        self._rc_status_set(
            f"{len(valid)} kanal / {len(istekler)} parametre yazıldı — "
            "FC'den geri okuma bekleniyor…",
            "warning", bold=True,
        )
        if problems:
            QMessageBox.information(
                self, "Kısmi kayıt",
                "CH1-CH4 yazıldı. Aşağıdaki yardımcı kanallar ATLANDI:\n\n"
                + "\n".join(problems),
            )

    def _rc_fail(self, msg: str):
        """Radyo sekmesinde başarısızlık durumunu tek noktadan gösterir."""
        self._rc_status_set(msg, "danger", bold=True)

    def _reset_rc(self):
        self._capturing_rc = False
        self._rc_capture_started = False
        self._rc_yazilan = {}
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
        # Alt panellere dağıt. NEDEN: main_window telemetriyi YALNIZCA GÖRÜNÜR
        # sekmeye veriyor (_dispatch_telemetry) ve gördüğü widget bu sınıftır;
        # alt paneller aksi hâlde canlı veri (armed durumu, RC PWM'i, titreşim)
        # HİÇ almazdı — motor kapısı "araç ARM'lı" durumunu göremezdi.
        _armed_yeni = bool(data.get("armed", False))
        if _armed_yeni != self._armed:
            self._armed = _armed_yeni
            self._guncelle_reboot_butonu()

        for panel in (self._sensor_view, self._motor_view, self._modes_view):
            fn = getattr(panel, "update_telemetry", None)
            if callable(fn):
                try:
                    fn(data)
                except Exception:
                    logger.exception("Alt panel telemetri işleme hatası")

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


    @staticmethod
    def _get_mavlink():
        """BUG FIX: mavlink modülü doğrudan import edilmemişti → NameError.
        mavutil.mavlink dialect yüklü değilse önce yükler, sonra döndürür."""
        if getattr(mavutil, "mavlink", None) is None:
            mavutil.set_dialect("ardupilotmega")
        return mavutil.mavlink
