"""
tuning_view.py — Modül 4
PID Tuning + pyqtgraph PlotWidget + Failsafe ayarları.
"""
import logging
import math
import os as _os
from PyQt6.QtCore import pyqtSignal, pyqtSlot, QTimer, Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGroupBox, QFormLayout, QDoubleSpinBox, QComboBox,
    QPushButton, QMessageBox,
)

from core import theme

try:
    _os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt6")
    import pyqtgraph as pg
    PG_AVAILABLE = True
except ImportError:
    PG_AVAILABLE = False
    logging.warning("pyqtgraph kurulu değil — PID grafiği devre dışı")

logger = logging.getLogger(__name__)

# ── Attitude eğri renkleri ───────────────────────────────────────────────────
# aviyonik semantik — temadan bağımsız. Roll/Pitch/Yaw eksen renkleri uçuş
# aleti geleneğidir (kırmızı/yeşil/amber), tema değişse de aynı kalır.
# Tek koşul: hem koyu grafik zemininde (#0f161c) hem beyaz zeminde okunabilmesi.
# Eski parlak tonlar (yeşil #4ade80 → beyazda 1.7:1, amber #f0a500 → 2.1:1)
# açık temada kayboluyordu; hue korunarak koyulaştırıldı — yeni değerler her
# iki zeminde de ≥3:1 (WCAG grafik nesnesi eşiği) kontrast verir.
CURVE_ROLL  = "#e94560"   # kırmızı — beyaz 3.8:1 / koyu 4.8:1
CURVE_PITCH = "#1f9d4d"   # yeşil   — beyaz 3.5:1 / koyu 5.2:1
CURVE_YAW   = "#c47f00"   # amber   — beyaz 3.3:1 / koyu 5.5:1

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
        # NEDEN (C1 — KRİTİK): Spin kutuları araçtan parametre okunmadan 0.0000
        # durumunda kalıyor. Eski kod "Değişiklikleri YAZ" ile bu 0'ları doğrudan
        # araca gönderiyordu → 9 PID sıfırlanıp araç uçuşa elverişsiz kalıyordu.
        # _params_loaded: araçtan EN AZ BİR gerçek PID parametresi geldi mi?
        # _loaded_values: araçtan gelen son değerler — yalnızca DEĞİŞMİŞ (dirty)
        # parametreleri yazmak için referans. İkisi birlikte kazara sıfırlamayı
        # ve gereksiz yazma trafiğini engeller.
        self._params_loaded: bool = False
        self._loaded_values: dict[str, float] = {}
        self._build_ui()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

        # 10 Hz grafik güncelleme timer'ı
        self._plot_timer = QTimer(self)
        self._plot_timer.setInterval(100)
        self._plot_timer.timeout.connect(self._refresh_plot)
        self._plot_timer.start()

    def _build_ui(self) -> None:
        main = QHBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(8)

        # ── Sol: PID Ayarları ────────────────────────────────────────────────
        left = QVBoxLayout()

        pid_label = QLabel("PID Ayarları")
        pid_label.setObjectName("sectionTitle")
        left.addWidget(pid_label)

        self._pid_widgets: dict = {}
        for axis, params in PID_PARAMS.items():
            grp = QGroupBox(axis)
            form = QFormLayout(grp)
            form.setSpacing(6)
            self._pid_widgets[axis] = {}
            for term, param_name in params.items():
                spin = QDoubleSpinBox()
                spin.setRange(0.0, 10.0)
                spin.setSingleStep(0.01)
                spin.setDecimals(4)
                spin.setMaximumWidth(140)
                # valueChanged ile otomatik gönderme iptal edildi, güvenlik butonu eklendi.
                self._pid_widgets[axis][term] = spin
                form.addRow(f"{term} ({param_name}):", spin)
            left.addWidget(grp)

        btn_reset = QPushButton("🔄  Varsayılana Sıfırla")
        btn_reset.setToolTip("Tüm PID değerlerini ArduCopter fabrika varsayılanlarına döndürür")
        btn_reset.setMinimumHeight(32)
        btn_reset.clicked.connect(self._reset_pid_defaults)

        # NEDEN (C1): Buton, araçtan parametre okunana kadar KAPALI başlar.
        # Böylece operatör hiç okuma yapmadan 0 değerlerini yazamaz; parametre
        # geldiğinde update_param() butonu aktifleştirir.
        self._btn_write = QPushButton("⚠️ Değişiklikleri YAZ")
        btn_write = self._btn_write
        btn_write.setToolTip("Önce Gelişmiş sekmesinden parametreleri okuyun — "
                             "ardından yalnızca değiştirdiğiniz PID'ler araca gönderilir")
        btn_write.setObjectName("warningButton")
        btn_write.setMinimumHeight(32)
        btn_write.setEnabled(False)
        btn_write.clicked.connect(self._write_all_pids)

        # 320px'lik dar panelde yan yana iki buton etiketi kırpıyordu; alt alta
        # dizilince her biri tam genişlikte, kırpılmadan sığar.
        btn_row = QVBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addWidget(btn_reset)
        btn_row.addWidget(btn_write)
        left.addLayout(btn_row)

        left.addStretch()

        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setMaximumWidth(340)
        main.addWidget(left_w)

        # ── Sağ: Grafik + Failsafe ───────────────────────────────────────────
        right = QVBoxLayout()

        plot_label = QLabel("Attitude Grafiği (10 Hz)")
        plot_label.setObjectName("sectionTitle")
        right.addWidget(plot_label)

        if PG_AVAILABLE:
            self._plot_widget = pg.PlotWidget(title="Roll / Pitch / Yaw")
            self._plot_widget.showGrid(x=True, y=True, alpha=0.2)
            self._plot_widget.addLegend()
            self._curve_roll = self._plot_widget.plot(
                pen=pg.mkPen(CURVE_ROLL, width=2), name="Roll"
            )
            self._curve_pitch = self._plot_widget.plot(
                pen=pg.mkPen(CURVE_PITCH, width=2), name="Pitch"
            )
            self._curve_yaw = self._plot_widget.plot(
                pen=pg.mkPen(CURVE_YAW, width=2), name="Yaw"
            )
            # Grafik zemini/ekseni tema token'larından gelir (eğriler hariç).
            self._apply_plot_theme()
            right.addWidget(self._plot_widget, stretch=2)
        else:
            no_plot = QLabel("pyqtgraph kurulu değil")
            no_plot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_plot.setObjectName("mutedLabel")
            right.addWidget(no_plot, stretch=2)

        # Failsafe
        fs_grp = QGroupBox("Failsafe Ayarları")
        fs_form = QFormLayout(fs_grp)
        fs_form.setSpacing(6)
        fs_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self._fs_action = QComboBox()
        self._fs_action.addItems(["RTL (Eve Dön)", "Land (İniş)"])
        self._fs_action.setMaximumWidth(260)
        fs_form.addRow("Bağlantı Kopunca:", self._fs_action)

        self._geofence_radius = QDoubleSpinBox()
        self._geofence_radius.setRange(10, 10000)
        self._geofence_radius.setValue(200)
        self._geofence_radius.setSuffix(" m")
        self._geofence_radius.setMaximumWidth(140)
        fs_form.addRow("GeoFence Yarıçapı:", self._geofence_radius)

        btn_apply_fs = QPushButton("Failsafe Uygula")
        btn_apply_fs.setObjectName("warningButton")
        btn_apply_fs.setMinimumHeight(30)
        btn_apply_fs.clicked.connect(self._apply_failsafe)
        fs_btn_row = QHBoxLayout()
        fs_btn_row.addStretch()
        fs_btn_row.addWidget(btn_apply_fs)
        fs_form.addRow(fs_btn_row)

        right.addWidget(fs_grp, stretch=1)

        right_w = QWidget()
        right_w.setLayout(right)
        main.addWidget(right_w, stretch=1)

    def _apply_plot_theme(self) -> None:
        """Grafiğin zemin/eksen/başlık renklerini aktif temadan yazar.

        NEDEN: pyqtgraph'ın `foreground` ayarı GLOBAL'dir (başka bir view
        değiştirebilir), bu yüzden eksen ve başlık renkleri bu widget üzerinde
        AÇIKÇA verilir — aksi halde açık temada beyaz zemin üstünde açık gri
        eksen yazıları okunmaz kalıyordu."""
        if not PG_AVAILABLE or not hasattr(self, "_plot_widget"):
            return
        fg = theme.c("chart_fg")
        self._plot_widget.setBackground(theme.c("chart_bg"))
        plot_item = self._plot_widget.getPlotItem()
        for axis_name in ("left", "bottom", "top", "right"):
            axis = plot_item.getAxis(axis_name)
            if axis is None:
                continue
            axis.setPen(fg)
            axis.setTextPen(fg)
        plot_item.setTitle("Roll / Pitch / Yaw", color=fg)
        legend = plot_item.legend
        if legend is not None:
            legend.setLabelTextColor(fg)

    def _apply_theme_colors(self) -> None:
        """Tema değişince çalışma anında yazılmış renkleri yeniden uygular.

        Bu view'da tek inline renk kaynağı pyqtgraph grafiğidir; eğri renkleri
        aviyonik semantik olduğu için sabit kalır, zemin/eksen tazelenir."""
        self._apply_plot_theme()

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
                    # NEDEN (C1): Araçtan GERÇEK değer geldi — artık yazma
                    # güvenli. Değeri sakla ki yalnızca değiştirilen (dirty)
                    # parametreler geri gönderilsin.
                    self._loaded_values[param_name] = float(value)
                    if not self._params_loaded:
                        self._params_loaded = True
                        self._btn_write.setEnabled(True)
                        self._btn_write.setToolTip(
                            "Yalnızca değiştirdiğiniz PID parametrelerini onay alarak araca gönderir"
                        )
                        logger.info("PID parametreleri araçtan alındı — yazma butonu aktif.")

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
        # ── GÜVENLİK KAPISI (C1 — KRİTİK) ───────────────────────────────────
        # NEDEN: Parametreler araçtan okunmadıysa spin kutuları 0.0000'dır.
        # Eski kod bu 0'ları doğrudan araca yazıyordu → 9 PID sıfırlanıyor,
        # araç uçuşa elverişsiz kalıyordu. Okuma yapılmadan YAZ engellenir.
        if not self._params_loaded:
            QMessageBox.warning(
                self, "Parametreler Okunmadı",
                "PID parametreleri araçtan HENÜZ OKUNMADI.\n\n"
                "Sıfır (0) değerlerin araca yazılması aracı uçuşa elverişsiz bırakır.\n"
                "Önce Gelişmiş sekmesinden parametreleri okuyun.",
            )
            return

        # NEDEN: Ek emniyet — tüm kutular 0 ise bu asla geçerli bir PID seti
        # değildir (okuma yarım kalmış olabilir). Yazma yapılmaz.
        all_values = [
            self._pid_widgets[axis][term].value()
            for axis, params in PID_PARAMS.items() for term in params
        ]
        if all(abs(v) < 1e-9 for v in all_values):
            QMessageBox.warning(
                self, "Geçersiz PID Değerleri",
                "Tüm PID değerleri 0 — bu geçerli bir ayar değildir.\n\n"
                "Önce Gelişmiş sekmesinden parametreleri okuyun.",
            )
            return

        # NEDEN: Yalnızca DEĞİŞTİRİLMİŞ (dirty) parametreleri yazmak, dokunulmamış
        # PID'lerin gereksiz yere yeniden yazılması riskini ortadan kaldırır.
        dirty: list[tuple[str, float]] = []
        for axis, params in PID_PARAMS.items():
            for term, param_name in params.items():
                val = self._pid_widgets[axis][term].value()
                ref = self._loaded_values.get(param_name)
                if ref is None or abs(val - ref) > 1e-6:
                    dirty.append((param_name, val))

        if not dirty:
            QMessageBox.information(
                self, "Değişiklik Yok",
                "PID değerlerinde araçtan okunana göre bir değişiklik yok.",
            )
            return

        detay = "\n".join(f"  • {n}: {self._loaded_values.get(n, 0.0):.4f} → {v:.4f}"
                          for n, v in dirty)
        reply = QMessageBox.warning(
            self, "PID Değişikliği Onayı",
            "PID parametrelerini değiştirmek uçuş kararlılığını tehlikeye atabilir.\n\n"
            f"Yazılacak parametreler ({len(dirty)} adet):\n{detay}\n\n"
            "Devam etmek istediğinize emin misiniz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for param_name, val in dirty:
                self.set_param.emit(param_name, val)
                # Yazılan değer artık yeni referans — aynı değer tekrar gönderilmesin.
                self._loaded_values[param_name] = val
            logger.info("Değiştirilen %d PID parametresi araca gönderildi: %s",
                        len(dirty), ", ".join(n for n, _ in dirty))

    def _apply_failsafe(self) -> None:
        action_idx = self._fs_action.currentIndex()
        # FS_THR_ENABLE: 1=Land, 2=RTL
        fs_mode = 2 if action_idx == 0 else 1
        radius = self._geofence_radius.value()

        # NEDEN onay: Failsafe/GeoFence parametrelerini değiştirmek, bağlantı
        # koptuğunda veya araç sınırı aştığında aracın NE yapacağını belirler —
        # yanlış bir değer acil durumda beklenmeyen davranışa yol açabilir.
        # Aynı desen _write_all_pids'te kullanılıyor.
        reply = QMessageBox.warning(
            self, "Failsafe/GeoFence Değişikliği Onayı",
            "Failsafe ve GeoFence parametrelerini değiştirmek, bağlantı kopması "
            "veya sınır aşımı gibi acil durumlarda aracın davranışını etkiler.\n\n"
            f"  • Bağlantı Kopunca: {self._fs_action.currentText()}\n"
            f"  • GeoFence Yarıçapı: {radius:.0f} m\n\n"
            "Devam etmek istediğinize emin misiniz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.set_param.emit("FS_THR_ENABLE", float(fs_mode))
        self.set_param.emit("FENCE_RADIUS", radius)
        logger.info("Failsafe uygulandı: mode=%s, fence=%s", fs_mode, radius)
