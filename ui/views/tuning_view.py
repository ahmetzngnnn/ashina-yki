"""
tuning_view.py — Modül 4
PID Tuning + pyqtgraph PlotWidget + Failsafe ayarları.
"""
import logging
import math
import os as _os
from PyQt6.QtCore import pyqtSignal, pyqtSlot, QTimer, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGroupBox, QFormLayout, QDoubleSpinBox, QComboBox,
    QPushButton, QMessageBox,
)

try:
    _os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt6")
    import pyqtgraph as pg
    PG_AVAILABLE = True
except ImportError:
    PG_AVAILABLE = False
    logging.warning("pyqtgraph kurulu değil — PID grafiği devre dışı")

logger = logging.getLogger(__name__)

PID_PARAMS = {
    "Roll": {
        "P": "ATC_RAT_RLL_P",
        "I": "ATC_RAT_RLL_I",
        "D": "ATC_RAT_RLL_D",
    },
    "Pitch": {
        "P": "ATC_RAT_PIT_P",
        "I": "ATC_RAT_PIT_I",
        "D": "ATC_RAT_PIT_D",
    },
    "Yaw": {
        "P": "ATC_RAT_YAW_P",
        "I": "ATC_RAT_YAW_I",
        "D": "ATC_RAT_YAW_D",
    },
}

# ArduCopter varsayılan PID değerleri
PID_DEFAULTS = {
    "Roll":  {"P": 0.135, "I": 0.135, "D": 0.0036},
    "Pitch": {"P": 0.135, "I": 0.135, "D": 0.0036},
    "Yaw":   {"P": 0.180, "I": 0.018, "D": 0.000},
}


class TuningView(QWidget):
    set_param = pyqtSignal(str, float)   # → mavlink_worker.set_param
    send_command = pyqtSignal(int, list) # → mavlink_worker.send_command

    def __init__(self, parent=None):
        super().__init__(parent)
        self._plot_data: dict = {"roll": [], "pitch": [], "yaw": []}
        self._build_ui()

        # 10 Hz grafik güncelleme timer'ı
        self._plot_timer = QTimer(self)
        self._plot_timer.setInterval(100)
        self._plot_timer.timeout.connect(self._refresh_plot)
        self._plot_timer.start()

    def _build_ui(self) -> None:
        self.setStyleSheet("color: #e0e0e0;")
        main = QHBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(12)

        # ── Sol: PID Ayarları ────────────────────────────────────────────────
        left = QVBoxLayout()

        pid_label = QLabel("PID Ayarları")
        pid_label.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        pid_label.setStyleSheet("color: #e94560;")
        left.addWidget(pid_label)

        self._pid_widgets: dict = {}
        for axis, params in PID_PARAMS.items():
            grp = QGroupBox(axis)
            grp.setStyleSheet("QGroupBox { border: 1px solid #0f3460; color: #aaa; font-weight: bold; }")
            form = QFormLayout(grp)
            self._pid_widgets[axis] = {}
            for term, param_name in params.items():
                spin = QDoubleSpinBox()
                spin.setRange(0.0, 10.0)
                spin.setSingleStep(0.01)
                spin.setDecimals(4)
                # valueChanged ile otomatik gönderme iptal edildi, güvenlik butonu eklendi.
                self._pid_widgets[axis][term] = spin
                form.addRow(f"{term} ({param_name}):", spin)
            left.addWidget(grp)

        btn_reset = QPushButton("🔄  Varsayılana Sıfırla")
        btn_reset.setToolTip("Tüm PID değerlerini ArduCopter fabrika varsayılanlarına döndürür")
        btn_reset.setStyleSheet(
            " color:#fff; padding:8px; border-radius:4px; font-weight:bold;"
        )
        btn_reset.clicked.connect(self._reset_pid_defaults)
        
        btn_write = QPushButton("⚠️ Değişiklikleri YAZ")
        btn_write.setToolTip("Ayarlanan PID parametrelerini onay alarak araca gönderir")
        btn_write.setStyleSheet(
            " color:#fff; background-color:#e94560; padding:8px; border-radius:4px; font-weight:bold;"
        )
        btn_write.clicked.connect(self._write_all_pids)
        
        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_reset)
        btn_row.addWidget(btn_write)
        left.addLayout(btn_row)

        left.addStretch()

        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setFixedWidth(320)
        main.addWidget(left_w)

        # ── Sağ: Grafik + Failsafe ───────────────────────────────────────────
        right = QVBoxLayout()

        plot_label = QLabel("Attitude Grafiği (10 Hz)")
        plot_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        right.addWidget(plot_label)

        if PG_AVAILABLE:
            self._plot_widget = pg.PlotWidget(title="Roll / Pitch / Yaw")
            self._plot_widget.setBackground("#1a1a2e")
            self._plot_widget.showGrid(x=True, y=True, alpha=0.2)
            self._plot_widget.addLegend()
            self._curve_roll = self._plot_widget.plot(
                pen=pg.mkPen("#e94560", width=2), name="Roll"
            )
            self._curve_pitch = self._plot_widget.plot(
                pen=pg.mkPen("#4ade80", width=2), name="Pitch"
            )
            self._curve_yaw = self._plot_widget.plot(
                pen=pg.mkPen("#f0a500", width=2), name="Yaw"
            )
            right.addWidget(self._plot_widget, stretch=2)
        else:
            no_plot = QLabel("pyqtgraph kurulu değil")
            no_plot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_plot.setStyleSheet("color: #555; font-size: 13px;")
            right.addWidget(no_plot, stretch=2)

        # Failsafe
        fs_grp = QGroupBox("Failsafe Ayarları")
        fs_grp.setStyleSheet("QGroupBox { border: 1px solid #0f3460; color: #aaa; font-weight: bold; }")
        fs_form = QFormLayout(fs_grp)

        self._fs_action = QComboBox()
        self._fs_action.addItems(["RTL (Eve Dön)", "Land (İniş)"])
        fs_form.addRow("Bağlantı Kopunca:", self._fs_action)

        self._geofence_radius = QDoubleSpinBox()
        self._geofence_radius.setRange(10, 10000)
        self._geofence_radius.setValue(200)
        self._geofence_radius.setSuffix(" m")
        fs_form.addRow("GeoFence Yarıçapı:", self._geofence_radius)

        btn_apply_fs = QPushButton("Failsafe Uygula")
        btn_apply_fs.clicked.connect(self._apply_failsafe)
        btn_apply_fs.setStyleSheet(" padding: 8px; border-radius: 4px;")
        fs_form.addRow(btn_apply_fs)

        right.addWidget(fs_grp, stretch=1)

        right_w = QWidget()
        right_w.setLayout(right)
        main.addWidget(right_w, stretch=1)

    def _refresh_plot(self) -> None:
        if not PG_AVAILABLE:
            return
        MAX_POINTS = 300
        for key in self._plot_data:
            self._plot_data[key] = self._plot_data[key][-MAX_POINTS:]
        self._curve_roll.setData(self._plot_data["roll"])
        self._curve_pitch.setData(self._plot_data["pitch"])
        self._curve_yaw.setData(self._plot_data["yaw"])

    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        import math
        self._plot_data["roll"].append(math.degrees(data.get("roll", 0.0)))
        self._plot_data["pitch"].append(math.degrees(data.get("pitch", 0.0)))
        self._plot_data["yaw"].append(math.degrees(data.get("yaw", 0.0)))

    @pyqtSlot(str, float)
    def update_param(self, name: str, value: float) -> None:
        for axis, params in PID_PARAMS.items():
            for term, param_name in params.items():
                if param_name == name:
                    spin = self._pid_widgets[axis][term]
                    spin.blockSignals(True)
                    spin.setValue(value)
                    spin.blockSignals(False)

    def _reset_pid_defaults(self) -> None:
        """Tüm PID değerlerini ArduCopter fabrika varsayılanlarına döndürür."""
        for axis, terms in PID_DEFAULTS.items():
            for term, value in terms.items():
                spin = self._pid_widgets[axis][term]
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
                param_name = PID_PARAMS[axis][term]
                # Sadece UI güncellenir, "Değişiklikleri YAZ" denilmeden araca gitmez.
        logger.info("PID değerleri UI üzerinde varsayılanlara sıfırlandı.")

    def _write_all_pids(self) -> None:
        reply = QMessageBox.warning(
            self, "PID Değişikliği Onayı",
            "PID parametrelerini değiştirmek uçuş kararlılığını tehlikeye atabilir.\nDevam etmek istediğinize emin misiniz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for axis, params in PID_PARAMS.items():
                for term, param_name in params.items():
                    val = self._pid_widgets[axis][term].value()
                    self.set_param.emit(param_name, val)
            logger.info("Yeni PID değerleri araca gönderildi.")

    def _apply_failsafe(self) -> None:
        action_idx = self._fs_action.currentIndex()
        # FS_THR_ENABLE: 1=Land, 2=RTL
        fs_mode = 2 if action_idx == 0 else 1
        self.set_param.emit("FS_THR_ENABLE", float(fs_mode))
        radius = self._geofence_radius.value()
        self.set_param.emit("FENCE_RADIUS", radius)
        logger.info("Failsafe uygulandı: mode=%s, fence=%s", fs_mode, radius)
