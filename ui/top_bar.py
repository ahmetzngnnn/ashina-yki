"""
top_bar.py — Global Durum Çubuğu
Sabit QWidget; tüm view'lerde görünür.
İçerir: Bağlantı paneli, Batarya, EKF, Uçuş Modu, GPS.
"""
import logging
from PyQt6.QtCore import pyqtSignal, pyqtSlot, QTimer, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QComboBox, QFrame,
)

try:
    import serial.tools.list_ports as _list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_SECS = 3


class TopBar(QWidget):
    # Drone bağlantı sinyalleri
    connect_requested = pyqtSignal(str, str, int, str)
    disconnect_requested = pyqtSignal()
    # Görev başlatma
    start_mission_requested = pyqtSignal()
    fcu_start_mission_requested = pyqtSignal()
    # RPi companion bağlantı sinyalleri
    rpi_connect_requested    = pyqtSignal(str, int)   # host, port
    rpi_disconnect_requested = pyqtSignal()
    rpi_battery_config       = pyqtSignal(int)        # hücre sayısı

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        self._connected = False
        self._rpi_connected = False
        self._last_hb_secs: float = 0.0

        self._build_ui()

        # Heartbeat timeout kontrolü
        self._hb_timer = QTimer(self)
        self._hb_timer.setInterval(500)
        self._hb_timer.timeout.connect(self._check_heartbeat)
        self._hb_timer.start()

    def _build_ui(self) -> None:
        self.setStyleSheet("TopBar { background-color: #151b23; border-bottom: 1px solid #30363d; }")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(10)

        # ── Bağlantı Paneli ──────────────────────────────────────────────────
        self._conn_type = QComboBox()
        self._conn_type.addItems(["Serial", "TCP", "UDP"])
        self._conn_type.setFixedWidth(80)
        self._conn_type.currentIndexChanged.connect(self._on_conn_type_changed)

        # Adres combo (Serial → COM listesi, TCP → port seçimi)
        self._conn_addr = QComboBox()
        self._conn_addr.setEditable(True)
        self._conn_addr.setFixedWidth(220)
        self._conn_addr.lineEdit().setPlaceholderText("Bağlantı adresi")  # type: ignore[union-attr]

        # Baudrate seçici (yalnızca Serial modunda görünür)
        self._baud_combo = QComboBox()
        self._baud_combo.addItems(["57600", "115200", "9600", "19200", "38400", "230400"])
        self._baud_combo.setCurrentText("57600")
        self._baud_combo.setFixedWidth(88)
        self._baud_combo.setToolTip("Baudrate")

        # Veri tipi seçici (yalnızca Serial modunda görünür)
        self._data_type_combo = QComboBox()
        self._data_type_combo.addItems(["Drone", "Gimbal"])
        self._data_type_combo.setFixedWidth(80)
        self._data_type_combo.setToolTip("Bu porttaki veri tipi")

        # Seri port yenile butonu
        self._btn_refresh = QPushButton("↺")
        self._btn_refresh.setMinimumWidth(32)
        self._btn_refresh.setToolTip("Portları Tara")
        self._btn_refresh.clicked.connect(self._refresh_ports)

        self._btn_connect = QPushButton("Bağlan")
        self._btn_connect.clicked.connect(self._on_connect_click)

        # ── GÖREVİ BAŞLAT — yüklü görev için arm + AUTO ──────────────────────
        self._btn_start = QPushButton("▶ YARDIMCI BİLGİSAYAR\nGÖREV BAŞLAT")
        
        self._btn_start.setEnabled(False)
        self._btn_start.setObjectName("successButton")
        self._btn_start.setToolTip(
            "Yüklü görevi görev bilgisayarı üzerinden başlatır: ARM → GUIDED/AUTO → uçuş.\n"
            "Görev başarıyla yüklendiğinde aktifleşir."
        )
        self._btn_start.clicked.connect(self._on_start_click)

        self._btn_start_fcu = QPushButton("▶ FCU\nGÖREV BAŞLAT")
        
        self._btn_start_fcu.setEnabled(False)
        self._btn_start_fcu.setObjectName("successButton")
        self._btn_start_fcu.setToolTip(
            "Yüklü görevi FCU üzerinden başlatır: ARM → HAFİF GAZ → TAKEOFF → AUTO uçuş.\n"
            "Görev başarıyla yüklendiğinde aktifleşir."
        )
        self._btn_start_fcu.clicked.connect(self._on_fcu_start_click)


        layout.addWidget(QLabel("Bağlantı:"))
        layout.addWidget(self._conn_type)
        layout.addWidget(self._conn_addr)
        layout.addWidget(self._baud_combo)
        layout.addWidget(self._data_type_combo)
        layout.addWidget(self._btn_refresh)
        layout.addWidget(self._btn_connect)
        layout.addWidget(self._btn_start_fcu)
        layout.addWidget(self._btn_start)
        layout.addStretch(1)

        # İlk durum: TCP seçeneği için varsayılan dolgu
        self._on_conn_type_changed(0)

        layout.addWidget(self._separator())

        # ── RPi Companion Bağlantı ────────────────────────────────────────────
        layout.addWidget(QLabel("Yardımcı Bilgisayar:"))

        self._rpi_addr = QComboBox()
        self._rpi_addr.setEditable(True)
        self._rpi_addr.addItems([
            "192.168.10.138:5602",   # Companion (Ubuntu / RPi)
            "127.0.0.1:5602",        # Localhost
        ])
        self._rpi_addr.setFixedWidth(165)
        self._rpi_addr.lineEdit().setPlaceholderText("Yardımcı PC IP:Port")  # type: ignore[union-attr]
        self._rpi_addr.setToolTip("Yardımcı Bilgisayar durum akışı adresi (StatusBroadcaster TCP)")

        self._rpi_cells = QComboBox()
        self._rpi_cells.addItems(["2S", "3S", "4S", "5S", "6S", "7S", "8S"])
        self._rpi_cells.setCurrentText("4S")
        self._rpi_cells.setFixedWidth(52)
        self._rpi_cells.setToolTip(
            "Drone batarya hücre sayısı.\n"
            "Failsafe voltaj eşikleri buna göre otomatik hesaplanır:\n"
            "  Uyarı = cells × 3.70V\n"
            "  RTL   = cells × 3.60V\n"
            "  LAND  = cells × 3.40V"
        )

        self._btn_rpi_connect = QPushButton("Yardımcı Bilgisayar Bağlan")
        self._btn_rpi_connect.clicked.connect(self._on_rpi_connect_click)

        self._rpi_light = QLabel("●")
        self._rpi_light.setStyleSheet("color: #555; font-size: 14px;")
        self._rpi_light.setToolTip("Yardımcı Bilgisayar bağlantı durumu")

        layout.addWidget(self._rpi_addr)
        layout.addWidget(self._rpi_cells)
        layout.addWidget(self._btn_rpi_connect)
        layout.addWidget(self._rpi_light)

        layout.addWidget(self._separator())

        # ── Batarya ──────────────────────────────────────────────────────────
        self._bat_label = QLabel("BAT")
        self._bat_bar = QProgressBar()
        self._bat_bar.setFixedWidth(90)
        self._bat_bar.setFixedHeight(18)
        self._bat_bar.setRange(0, 100)
        self._bat_bar.setValue(0)
        self._bat_bar.setTextVisible(True)
        layout.addWidget(self._bat_label)
        layout.addWidget(self._bat_bar)

        layout.addWidget(self._separator())

        # ── EKF ─────────────────────────────────────────────────────────────
        self._ekf_indicator = QLabel("EKF ●")
        self._ekf_indicator.setStyleSheet("color: #888;")
        layout.addWidget(self._ekf_indicator)

        layout.addWidget(self._separator())

        # ── Uçuş Modu ────────────────────────────────────────────────────────
        self._mode_label = QLabel("MODU: —")
        self._mode_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        layout.addWidget(self._mode_label)

        layout.addWidget(self._separator())

        # ── GPS ──────────────────────────────────────────────────────────────
        self._gps_label = QLabel("GPS: —")
        layout.addWidget(self._gps_label)

        layout.addStretch()

        # ── Bağlantı durumu ışığı ─────────────────────────────────────────────
        self._status_light = QLabel("●")
        self._status_light.setStyleSheet("color: #555; font-size: 16px;")
        layout.addWidget(self._status_light)

    def _separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #30363d;")
        return sep

    # ── Slot: Telemetri ───────────────────────────────────────────────────────
    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        bat = int(data.get("battery_remaining", 0))
        self._bat_bar.setValue(max(0, min(100, bat)))
        if bat <= 20:
            self._bat_bar.setStyleSheet("QProgressBar::chunk { background: #e94560; }")
        elif bat <= 50:
            self._bat_bar.setStyleSheet("QProgressBar::chunk { background: #f0a500; }")
        else:
            self._bat_bar.setStyleSheet("QProgressBar::chunk { background: #4ade80; }")

        # GPS
        sats = data.get("gps_sats", "—")
        fix = data.get("gps_fix_name", "—")
        self._gps_label.setText(f"GPS: {fix} [{sats}sat]")

        # Mod + Arm durumu
        mode = data.get("flight_mode", "—")
        armed = data.get("armed", False)
        arm_txt = " ▲ARM" if armed else " ▽DSM"
        self._mode_label.setText(f"{mode}{arm_txt}")
        if armed:
            self._mode_label.setStyleSheet("color:#e94560; font-weight:bold; font-size:11px;")
        else:
            self._mode_label.setStyleSheet("color:#4ade80; font-weight:bold; font-size:11px;")

        # EKF
        ekf_flags = data.get("ekf_flags", None)
        if ekf_flags is None:
            self._ekf_indicator.setText("EKF ●")
            self._ekf_indicator.setStyleSheet("color:#888;")
            self._ekf_indicator.setToolTip("EKF verisi yok")
        elif (ekf_flags & 0x1F) == 0x1F:   # attitude+vel_h+vel_v+pos_h+pos_v
            self._ekf_indicator.setText("EKF ●")
            self._ekf_indicator.setStyleSheet("color:#4ade80;")
            self._ekf_indicator.setToolTip(f"EKF İYİ (0x{ekf_flags:04x})")
        elif ekf_flags & 0x01:
            self._ekf_indicator.setText("EKF ●")
            self._ekf_indicator.setStyleSheet("color:#f0a500;")
            self._ekf_indicator.setToolTip(f"EKF KISMI (0x{ekf_flags:04x}): attitude OK, pos eksik")
        else:
            self._ekf_indicator.setText("EKF ●")
            self._ekf_indicator.setStyleSheet("color:#e94560;")
            self._ekf_indicator.setToolTip(f"EKF HATA (0x{ekf_flags:04x})")

        hb_age = data.get("last_heartbeat_age", 0)
        self._last_hb_secs = float(hb_age)

    @pyqtSlot(bool)
    def set_connection_status(self, connected: bool) -> None:
        self._connected = connected
        if connected:
            self._status_light.setStyleSheet("color: #4ade80; font-size: 16px;")
            self._btn_connect.setText("Kes")
        else:
            self._status_light.setStyleSheet("color: #e94560; font-size: 16px;")
            self._btn_connect.setText("Bağlan")
            self._bat_bar.setValue(0)
            self._mode_label.setText("MODU: —")
            self._gps_label.setText("GPS: —")

    def _check_heartbeat(self) -> None:
        if not self._connected:
            return
        if self._last_hb_secs > HEARTBEAT_TIMEOUT_SECS:
            self.setStyleSheet(
                self.styleSheet().replace("border-bottom: 2px solid #0f3460;", "") +
                "TopBar { border-bottom: 3px solid #e94560; background: #2a0a10; }"
            )
        # Normal durumda renk zaten doğru


    def _on_fcu_start_click(self) -> None:
        """FCU Görevi başlat butonu — onay diyaloğu sonra sinyali yayar."""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "FCU Görevi Başlat",
            "FCU'ya görev başlatma komutu gönderilecek.\n"
            "Sırasıyla: GUIDED -> ARM -> Hafif Gaz (Anti-disarm) -> TAKEOFF -> AUTO moduna geçilecek.\n\n"
            "Başlatılsın mı?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.fcu_start_mission_requested.emit()
            self._btn_start_fcu.setEnabled(False)
            self._btn_start_fcu.setText("▶ BAŞLATILIYOR...")

    def _on_start_click(self) -> None:
        """Görevi başlat butonu — onay diyaloğu sonra sinyali yayar."""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Görevi Başlat",
            "Yardımcı bilgisayarna görev başlatma komutu gönderilecek.\n"
            "Drone ARM edilecek ve görevi otomatik uçacak.\n\n"
            "Başlatılsın mı?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.start_mission_requested.emit()
            self._btn_start.setEnabled(False)
            self._btn_start.setText("▶ BAŞLATILIYOR...")

    @pyqtSlot(bool)
    def set_mission_ready(self, ready: bool) -> None:
        """Görev yükleme sonucuna göre Başlat butonunu aktive et."""
        self._btn_start.setEnabled(ready)
        self._btn_start_fcu.setEnabled(ready)
        if ready:
            self._btn_start.setText("▶ YARDIMCI BİLGİSAYAR\nGÖREV BAŞLAT")
            self._btn_start_fcu.setText("▶ FCU\nGÖREV BAŞLAT")
        else:
            self._btn_start.setText("▶ YARDIMCI BİLGİSAYAR\nGÖREV BAŞLAT")
            self._btn_start_fcu.setText("▶ FCU\nGÖREV BAŞLAT")

    def _on_rpi_connect_click(self) -> None:
        if self._rpi_connected:
            self.rpi_disconnect_requested.emit()
        else:
            addr = self._rpi_addr.currentText().strip()
            if ":" in addr:
                host, port_str = addr.rsplit(":", 1)
            else:
                host, port_str = addr, "5602"
            try:
                port = int(port_str)
            except ValueError:
                return
            if not host:
                return
            cells = int(self._rpi_cells.currentText().rstrip("S"))
            self.rpi_connect_requested.emit(host, port)
            self.rpi_battery_config.emit(cells)

    @pyqtSlot(bool)
    def set_rpi_connection_status(self, connected: bool) -> None:
        self._rpi_connected = connected
        if connected:
            self._rpi_light.setStyleSheet("color: #4ade80; font-size: 14px;")
            self._btn_rpi_connect.setText("Yardımcı PC Kes")
        else:
            self._rpi_light.setStyleSheet("color: #555; font-size: 14px;")
            self._btn_rpi_connect.setText("Yardımcı Bilgisayar Bağlan")

    def _on_connect_click(self) -> None:
        if self._connected:
            self.disconnect_requested.emit()
        else:
            conn_type = self._conn_type.currentText().lower()
            if conn_type == "serial":
                idx = self._conn_addr.currentIndex()
                addr = self._conn_addr.itemData(idx, Qt.ItemDataRole.UserRole)
                if not addr:
                    addr = (self._conn_addr.currentText() or "").strip()
            else:
                addr = (self._conn_addr.currentText() or "").strip()
            if not addr:
                return
            baud = int(self._baud_combo.currentText()) if conn_type == "serial" else 115200
            data_type = self._data_type_combo.currentText().lower() if conn_type == "serial" else "drone"
            self.connect_requested.emit(conn_type, addr, baud, data_type)

    def _on_conn_type_changed(self, idx: int) -> None:
        """Bağlantı tipine göre adres listesini doldur."""
        self._conn_addr.clear()
        typ = self._conn_type.itemText(idx).lower()
        if typ == "serial":
            self._fill_serial_ports()
            self._baud_combo.setVisible(True)
            self._data_type_combo.setVisible(True)
            self._btn_refresh.setVisible(True)
        elif typ == "tcp":
            # Otomatik önerilen portlar
            self._conn_addr.addItems([
                "192.168.10.138:5762",   # Linux PC SITL
                "127.0.0.1:5762",
                "127.0.0.1:5763",
            ])
            self._baud_combo.setVisible(False)
            self._data_type_combo.setVisible(False)
            self._btn_refresh.setVisible(False)
        else:  # UDP
            self._conn_addr.addItems([
                "127.0.0.1:14551",   # companion (aynı makine)
                "127.0.0.1:14550",   # QGC standart
                "0.0.0.0:14550",
                "0.0.0.0:14551",
            ])
            self._baud_combo.setVisible(False)
            self._data_type_combo.setVisible(False)
            self._btn_refresh.setVisible(False)

    def _refresh_ports(self) -> None:
        typ = self._conn_type.currentText().lower()
        if typ == "serial":
            self._conn_addr.clear()
            self._fill_serial_ports()

    def _fill_serial_ports(self) -> None:
        if SERIAL_AVAILABLE:
            ports = list(_list_ports.comports())
        else:
            ports = []
        if ports:
            for p in sorted(ports, key=lambda x: x.device):
                desc = (p.description or "").strip()
                # Bazı sürücüler açıklamada zaten cihaz adını içerir
                if desc and desc.lower() not in ("n/a", p.device.lower()):
                    display = f"{p.device}  —  {desc}"
                else:
                    display = p.device
                self._conn_addr.addItem(display, userData=p.device)
        else:
            self._conn_addr.addItem("COM3", userData="COM3")
            self._conn_addr.lineEdit().setPlaceholderText(  # type: ignore[union-attr]
                "COM port bulunamadı — manuel girin"
            )

    def get_command_widgets(self) -> list:
        return [self._btn_connect]
