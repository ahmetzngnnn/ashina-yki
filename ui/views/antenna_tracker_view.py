"""
ui/views/antenna_tracker_view.py — Anten Takip Paneli
======================================================

Antenin İHA'yı takibini görsel olarak gösterir ve kontrol eder:
  • Pusula göstergesi (azimut okı + elevasyon barı)
  • Canlı değerler (azimut/elevasyon/mesafe/irtifa farkı)
  • Aç/kapat, bağlantı durumu, mod
  • Anten home ayarı (İHA konumundan veya elle)

Sinyaller AntennaTrackerWorker'a/worker'dan main_window üzerinden bağlanır.
"""
from __future__ import annotations
import math

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QGroupBox, QLineEdit, QFormLayout, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPolygonF


# ── Pusula / Elevasyon Göstergesi ───────────────────────────────────────────
class CompassWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._az = 0.0
        self._el = 0.0
        self._tracking = False
        self.setMinimumSize(260, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_aim(self, az: float, el: float, tracking: bool):
        self._az = az; self._el = el; self._tracking = tracking
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        size = min(w, h) - 20
        cx, cy = w / 2, h / 2
        r = size / 2

        # Dış halka
        p.setPen(QPen(QColor("#0f3460"), 3))
        p.setBrush(QBrush(QColor("#0d1b2a")))
        p.drawEllipse(QPointF(cx, cy), r, r)

        # Yön etiketleri ve çizgiler
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        for ang, lbl in [(0, "K"), (90, "D"), (180, "G"), (270, "B")]:
            a = math.radians(ang - 90)
            x1 = cx + (r - 18) * math.cos(a); y1 = cy + (r - 18) * math.sin(a)
            x2 = cx + r * math.cos(a); y2 = cy + r * math.sin(a)
            p.setPen(QPen(QColor("#415a77"), 2))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            tx = cx + (r - 32) * math.cos(a); ty = cy + (r - 32) * math.sin(a)
            p.setPen(QColor("#e0e1dd"))
            p.drawText(QRectF(tx - 12, ty - 10, 24, 20), Qt.AlignmentFlag.AlignCenter, lbl)

        # Derece tikleri (her 30°)
        p.setPen(QPen(QColor("#27445c"), 1))
        for ang in range(0, 360, 30):
            a = math.radians(ang - 90)
            x1 = cx + (r - 8) * math.cos(a); y1 = cy + (r - 8) * math.sin(a)
            x2 = cx + r * math.cos(a); y2 = cy + r * math.sin(a)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Azimut okı (antenin baktığı yön)
        col = QColor("#00e676") if self._tracking else QColor("#777777")
        a = math.radians(self._az - 90)
        tip = QPointF(cx + (r - 24) * math.cos(a), cy + (r - 24) * math.sin(a))
        left = QPointF(cx + 14 * math.cos(a + 2.5), cy + 14 * math.sin(a + 2.5))
        right = QPointF(cx + 14 * math.cos(a - 2.5), cy + 14 * math.sin(a - 2.5))
        p.setBrush(QBrush(col)); p.setPen(QPen(col, 2))
        p.drawPolygon(QPolygonF([tip, left, right]))
        p.setBrush(QBrush(QColor("#e0e1dd")))
        p.drawEllipse(QPointF(cx, cy), 6, 6)

        # Merkez azimut yazısı
        p.setPen(QColor("#00e5ff")); p.setFont(QFont("Consolas", 13, QFont.Weight.Bold))
        p.drawText(QRectF(cx - 40, cy + 12, 80, 22), Qt.AlignmentFlag.AlignCenter,
                   f"{self._az:.0f}°")

        # Elevasyon barı (sağ kenar dikey)
        bx = w - 16; bt = cy - r; bb = cy + r
        p.setPen(QPen(QColor("#0f3460"), 2)); p.setBrush(QBrush(QColor("#0d1b2a")))
        p.drawRect(QRectF(bx - 6, bt, 12, bb - bt))
        # -90..+90 → bar konumu
        frac = (90 - max(-90, min(90, self._el))) / 180.0
        ey = bt + frac * (bb - bt)
        p.setBrush(QBrush(col)); p.setPen(QPen(col, 1))
        p.drawRect(QRectF(bx - 9, ey - 3, 18, 6))
        p.setPen(QColor("#e0e1dd")); p.setFont(QFont("Consolas", 8))
        p.drawText(QRectF(bx - 20, ey - 18, 40, 14), Qt.AlignmentFlag.AlignCenter, f"{self._el:.0f}°")
        p.end()


# ── Ana Panel ────────────────────────────────────────────────────────────────
class AntennaTrackerView(QWidget):
    enable_requested      = pyqtSignal()
    disable_requested     = pyqtSignal()
    set_home_from_drone   = pyqtSignal()
    set_home_manual       = pyqtSignal(float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._build()

    def _build(self):
        root = QHBoxLayout(self)

        # ── Sol: pusula ──
        self._compass = CompassWidget()
        root.addWidget(self._compass, stretch=2)

        # ── Sağ: kontrol + değerler ──
        right = QVBoxLayout()

        # Durum başlığı
        st_grp = QGroupBox("Anten Takip Durumu")
        st = QGridLayout(st_grp)
        self._lbl_state = QLabel("● PASİF"); self._lbl_state.setStyleSheet("color:#777; font-size:15px; font-weight:bold;")
        self._lbl_conn = QLabel("Anten Pixhawk: bağlı değil"); self._lbl_conn.setStyleSheet("color:#8b949e;")
        st.addWidget(self._lbl_state, 0, 0, 1, 2)
        st.addWidget(self._lbl_conn, 1, 0, 1, 2)
        right.addWidget(st_grp)

        # Canlı değerler
        val_grp = QGroupBox("Hedef Açıları ve Mesafe")
        vg = QFormLayout(val_grp); vg.setSpacing(6)
        def _v():
            l = QLabel("—"); l.setStyleSheet("font-family:Consolas; font-size:14px; color:#00e5ff; font-weight:bold;")
            return l
        self._v_az = _v(); self._v_el = _v(); self._v_gd = _v(); self._v_sd = _v(); self._v_ad = _v()
        vg.addRow("Azimut (Pan):", self._v_az)
        vg.addRow("Elevasyon (Tilt):", self._v_el)
        vg.addRow("Yatay Mesafe:", self._v_gd)
        vg.addRow("Eğik Mesafe:", self._v_sd)
        vg.addRow("İrtifa Farkı:", self._v_ad)
        right.addWidget(val_grp)

        # Kontrol
        ctl_grp = QGroupBox("Kontrol")
        cg = QVBoxLayout(ctl_grp)
        self._btn_toggle = QPushButton("▶ Takibi Başlat")
        self._btn_toggle.setStyleSheet("background:#2e7d32; color:white; font-weight:bold; padding:8px; border-radius:4px;")
        self._btn_toggle.clicked.connect(self._on_toggle)
        cg.addWidget(self._btn_toggle)

        home_row = QHBoxLayout()
        self._btn_home_drone = QPushButton("Anten Home = İHA Konumu")
        self._btn_home_drone.setToolTip("Antenin referans konumunu, anlık İHA GPS'ine ayarlar (saha kalibrasyonu)")
        self._btn_home_drone.clicked.connect(self.set_home_from_drone.emit)
        cg.addWidget(self._btn_home_drone)

        man_grp = QGroupBox("Elle Anten Home (opsiyonel)")
        mg = QFormLayout(man_grp); mg.setSpacing(4)
        self._e_lat = QLineEdit(); self._e_lat.setPlaceholderText("Enlem")
        self._e_lon = QLineEdit(); self._e_lon.setPlaceholderText("Boylam")
        self._e_alt = QLineEdit(); self._e_alt.setPlaceholderText("İrtifa (m)")
        mg.addRow("Enlem:", self._e_lat); mg.addRow("Boylam:", self._e_lon); mg.addRow("İrtifa:", self._e_alt)
        btn_man = QPushButton("Elle Ayarla")
        btn_man.clicked.connect(self._on_manual_home)
        mg.addRow(btn_man)
        cg.addWidget(man_grp)
        right.addWidget(ctl_grp)

        right.addStretch()
        root.addLayout(right, stretch=1)

    # ── Kullanıcı eylemleri ────────────────────────────────────────────────
    def _on_toggle(self):
        if self._active:
            self.disable_requested.emit()
        else:
            self.enable_requested.emit()

    def _on_manual_home(self):
        try:
            lat = float(self._e_lat.text()); lon = float(self._e_lon.text())
            alt = float(self._e_alt.text() or "0")
            self.set_home_manual.emit(lat, lon, alt)
        except ValueError:
            pass

    # ── Worker → UI slotları ───────────────────────────────────────────────
    @pyqtSlot(dict)
    def update_status(self, s: dict):
        az = s.get("azimuth", 0.0); el = s.get("elevation", 0.0)
        tracking = bool(s.get("tracking", False))
        self._compass.set_aim(az, el, tracking)
        self._v_az.setText(f"{az:6.1f}°")
        self._v_el.setText(f"{el:6.1f}°")
        self._v_gd.setText(f"{s.get('ground_distance_m', 0):.0f} m")
        self._v_sd.setText(f"{s.get('slant_distance_m', 0):.0f} m")
        self._v_ad.setText(f"{s.get('alt_diff_m', 0):+.0f} m")
        if tracking:
            self._lbl_state.setText("● TAKİP EDİYOR")
            self._lbl_state.setStyleSheet("color:#00e676; font-size:15px; font-weight:bold;")
        else:
            self._lbl_state.setText("● BEKLEMEDE (yakın/hedef yok)")
            self._lbl_state.setStyleSheet("color:#ffb300; font-size:15px; font-weight:bold;")

    @pyqtSlot(bool)
    def set_tracking_active(self, active: bool):
        """enable/disable sonrası buton/etiket durumunu güncelle."""
        self._active = active
        if active:
            self._btn_toggle.setText("■ Takibi Durdur")
            self._btn_toggle.setStyleSheet("background:#c62828; color:white; font-weight:bold; padding:8px; border-radius:4px;")
        else:
            self._btn_toggle.setText("▶ Takibi Başlat")
            self._btn_toggle.setStyleSheet("background:#2e7d32; color:white; font-weight:bold; padding:8px; border-radius:4px;")
            self._lbl_state.setText("● PASİF")
            self._lbl_state.setStyleSheet("color:#777; font-size:15px; font-weight:bold;")

    @pyqtSlot(bool)
    def set_connection(self, connected: bool):
        if connected:
            self._lbl_conn.setText("Anten Pixhawk: ● bağlı")
            self._lbl_conn.setStyleSheet("color:#00e676;")
        else:
            self._lbl_conn.setText("Anten Pixhawk: bağlı değil")
            self._lbl_conn.setStyleSheet("color:#8b949e;")
