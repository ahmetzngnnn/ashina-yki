"""
top_bar.py — Global Durum Çubuğu
Sabit QWidget; tüm view'lerde görünür.
İçerir: Bağlantı paneli, Batarya, EKF, Uçuş Modu, GPS.
"""
import logging
import os
import time
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

# Linux'ta /dev/serial/by-id — USB seri adaptörleri (RFD telemetri vb.) fişten
# çekilip takıldığında /dev/ttyUSB0 → ttyUSB1 şeklinde isim değiştirir. by-id
# yolu ise cihazın VID/PID/seri numarasına bağlı olduğundan SABİT kalır. Bu
# sabit yolu bağlantı hedefi olarak kullanınca, kısa USB kopmalarından sonra
# otomatik yeniden bağlanma doğru cihazı bulur.
_SERIAL_BY_ID_DIR = "/dev/serial/by-id"


def _stable_serial_path(device: str) -> str:
    """Verilen /dev/ttyUSBx için varsa kararlı /dev/serial/by-id/... yolunu döndürür.
    Bulunamazsa (veya Linux değilse) orijinal cihaz yolunu döndürür."""
    try:
        if not os.path.isdir(_SERIAL_BY_ID_DIR):
            return device
        target = os.path.realpath(device)
        for name in os.listdir(_SERIAL_BY_ID_DIR):
            link = os.path.join(_SERIAL_BY_ID_DIR, name)
            if os.path.realpath(link) == target:
                return link
    except Exception:
        pass
    return device

HEARTBEAT_TIMEOUT_SECS = 3


class TopBar(QWidget):
    # Drone bağlantı sinyalleri
    connect_requested = pyqtSignal(str, str, int, str)
    disconnect_requested = pyqtSignal()
    # Görev başlatma
    start_mission_requested = pyqtSignal()
    fcu_start_mission_requested = pyqtSignal()
    abort_mission_requested = pyqtSignal()   # E3: görevi durdur + RTL
    drop_now_requested      = pyqtSignal()   # E4: manuel yük bırakma
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
        # GERÇEK duvar-saati zaman damgası — son telemetri NE ZAMAN geldiğini
        # takip eder. Önceden sadece FC'nin bildirdiği last_heartbeat_age alanı
        # kullanılıyordu; bağlantı sessizce donarsa (soket açık ama veri akmıyor
        # — "kablo temassızlığı") telemetri hiç gelmez, o değer DONUP kalır ve
        # asla eşiği aşmaz. Bu yüzden yerel Qt timer'ın kendi geçen süresini
        # ölçmesi gerekiyor — veri akmasa bile bu ilerlemeye devam eder.
        self._last_telemetry_wallclock: float = 0.0
        self._link_stale = False
        self._base_style = "TopBar { background-color: #151b23; border-bottom: 1px solid #30363d; }"

        self._build_ui()

        # Heartbeat timeout kontrolü
        self._hb_timer = QTimer(self)
        self._hb_timer.setInterval(500)
        self._hb_timer.timeout.connect(self._check_heartbeat)
        self._hb_timer.start()

    def _build_ui(self) -> None:
        self.setStyleSheet(self._base_style)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(6)

        # ── Bağlantı Paneli ──────────────────────────────────────────────────
        self._conn_type = QComboBox()
        self._conn_type.addItems(["Serial", "TCP", "UDP"])
        self._conn_type.setFixedWidth(68)
        self._conn_type.currentIndexChanged.connect(self._on_conn_type_changed)

        # Adres combo (Serial → COM listesi, TCP → port seçimi)
        self._conn_addr = QComboBox()
        self._conn_addr.setEditable(True)
        self._conn_addr.setFixedWidth(160)
        self._conn_addr.lineEdit().setPlaceholderText("Bağlantı adresi")  # type: ignore[union-attr]

        # Baudrate seçici (yalnızca Serial modunda görünür)
        self._baud_combo = QComboBox()
        self._baud_combo.addItems(["57600", "115200", "9600", "19200", "38400", "230400"])
        self._baud_combo.setCurrentText("57600")
        self._baud_combo.setFixedWidth(78)
        self._baud_combo.setToolTip("Baudrate")

        # Veri tipi seçici (yalnızca Serial modunda görünür)
        self._data_type_combo = QComboBox()
        self._data_type_combo.addItems(["Drone", "Gimbal"])
        self._data_type_combo.setFixedWidth(70)
        self._data_type_combo.setToolTip("Bu porttaki veri tipi")

        # Seri port yenile butonu
        self._btn_refresh = QPushButton("↺")
        self._btn_refresh.setFixedWidth(28)
        self._btn_refresh.setToolTip("Portları Tara")
        self._btn_refresh.clicked.connect(self._refresh_ports)

        self._btn_connect = QPushButton("Bağlan")
        self._btn_connect.clicked.connect(self._on_connect_click)

        # ── GÖREVİ BAŞLAT — yüklü görev için arm + AUTO ──────────────────────
        self._btn_start = QPushButton("▶ YARD. PC\nGÖREV BAŞLAT")
        self._btn_start.setMinimumWidth(112)
        self._btn_start.setEnabled(False)
        self._btn_start.setObjectName("successButton")
        self._btn_start.setToolTip(
            "Yüklü görevi görev bilgisayarı üzerinden başlatır: ARM → GUIDED/AUTO → uçuş.\n"
            "Görev başarıyla yüklendiğinde aktifleşir."
        )
        self._btn_start.clicked.connect(self._on_start_click)

        self._btn_start_fcu = QPushButton("▶ FCU\nGÖREV BAŞLAT")
        self._btn_start_fcu.setMinimumWidth(104)
        self._btn_start_fcu.setEnabled(False)
        self._btn_start_fcu.setObjectName("successButton")
        self._btn_start_fcu.setToolTip(
            "Yüklü görevi FCU üzerinden başlatır: ARM → HAFİF GAZ → TAKEOFF → AUTO uçuş.\n"
            "Görev başarıyla yüklendiğinde aktifleşir."
        )
        self._btn_start_fcu.clicked.connect(self._on_fcu_start_click)

        # ── DURDUR / RTL — çalışan görevi iptal et (companion abort + doğrudan RTL) ──
        self._btn_abort = QPushButton("■ DURDUR / RTL")
        self._btn_abort.setMinimumWidth(116)
        self._btn_abort.setObjectName("dangerButton")
        self._btn_abort.setEnabled(False)
        self._btn_abort.setToolTip(
            "Çalışan görevi durdurur: Yardımcı Bilgisayar görevini iptal eder VE\n"
            "FC'yi doğrudan RTL (Eve Dön) moduna alır. Görev başlatılınca aktifleşir."
        )
        self._btn_abort.clicked.connect(self._on_abort_click)

        # ── ŞİMDİ BIRAK — manuel yük bırakma (autonomous_drop=False modunda) ──
        self._btn_drop = QPushButton("⬇ ŞİMDİ BIRAK")
        self._btn_drop.setMinimumWidth(112)
        self._btn_drop.setEnabled(False)
        self._btn_drop.setToolTip(
            "Manuel yük bırakma (Görev 2/3 manuel mod): hedef üzerinde kilitliyken\n"
            "operatör bu butonla bırakır. Görev başlatılınca aktifleşir."
        )
        self._btn_drop.clicked.connect(self._on_drop_click)


        layout.addWidget(QLabel("Bağlantı:"))
        layout.addWidget(self._conn_type)
        layout.addWidget(self._conn_addr)
        layout.addWidget(self._baud_combo)
        layout.addWidget(self._data_type_combo)
        layout.addWidget(self._btn_refresh)
        layout.addWidget(self._btn_connect)
        layout.addWidget(self._btn_start_fcu)
        layout.addWidget(self._btn_start)
        layout.addWidget(self._btn_abort)
        layout.addWidget(self._btn_drop)

        # İlk durum: TCP seçeneği için varsayılan dolgu
        self._on_conn_type_changed(0)

        layout.addWidget(self._separator())

        # ── RPi Companion Bağlantı ────────────────────────────────────────────
        layout.addWidget(QLabel("Yard. PC:"))

        self._rpi_addr = QComboBox()
        self._rpi_addr.setEditable(True)
        self._rpi_addr.addItems([
            "192.168.10.229:5602",   # Companion (RPi)
            "127.0.0.1:5602",        # Localhost
        ])
        self._rpi_addr.setFixedWidth(150)
        self._rpi_addr.lineEdit().setPlaceholderText("Yardımcı PC IP:Port")  # type: ignore[union-attr]
        self._rpi_addr.setToolTip("Yardımcı Bilgisayar durum akışı adresi (StatusBroadcaster TCP)")

        self._btn_rpi_connect = QPushButton("Yard. PC Bağlan")
        self._btn_rpi_connect.setMinimumWidth(130)
        self._btn_rpi_connect.clicked.connect(self._on_rpi_connect_click)

        self._rpi_light = QLabel("●")
        self._rpi_light.setStyleSheet("color: #555; font-size: 14px;")
        self._rpi_light.setToolTip("Yardımcı Bilgisayar bağlantı durumu")

        layout.addWidget(self._rpi_addr)
        layout.addWidget(self._btn_rpi_connect)
        layout.addWidget(self._rpi_light)

        # Tek esnek boşluk: kontroller solda, durum göstergeleri sağda toplanır
        layout.addStretch(1)

        layout.addWidget(self._separator())

        # ── Batarya ──────────────────────────────────────────────────────────
        self._bat_label = QLabel("BAT")
        self._bat_bar = QProgressBar()
        self._bat_bar.setFixedWidth(80)
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
        self._last_telemetry_wallclock = time.monotonic()

    @pyqtSlot(bool)
    def set_connection_status(self, connected: bool) -> None:
        self._connected = connected
        # Yeni bağlantı/kopma durumunda eski "stale" damgasını temizle — aksi
        # halde yeniden bağlanınca ilk birkaç yüz ms boyunca hâlâ eski
        # zaman damgasına göre "stale" görünebilir ya da kırmızı kenarlık
        # bağlantı kesme stiliyle çakışabilir.
        self._link_stale = False
        self._last_telemetry_wallclock = 0.0
        self.setStyleSheet(self._base_style)
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
        # GERÇEK geçen süre (yerel Qt timer'a göre) — FC'nin bildirdiği
        # last_heartbeat_age alanına değil, çünkü telemetri hiç gelmiyorsa
        # (sessiz donma) o alan asla güncellenmez ve sonsuza dek eski kalır.
        elapsed = (time.monotonic() - self._last_telemetry_wallclock
                   if self._last_telemetry_wallclock > 0 else 999.0)
        stale = elapsed > HEARTBEAT_TIMEOUT_SECS or self._last_hb_secs > HEARTBEAT_TIMEOUT_SECS
        if stale and not self._link_stale:
            self._link_stale = True
            self.setStyleSheet(
                self._base_style +
                "TopBar { border-bottom: 3px solid #e94560; background: #2a0a10; }"
            )
        elif not stale and self._link_stale:
            self._link_stale = False
            self.setStyleSheet(self._base_style)

    def link_is_healthy(self) -> bool:
        """
        Bağlantının GERÇEKTEN sağlıklı olup olmadığını döndürür — sadece
        "connect edildi mi" değil, telemetri hâlâ akıyor mu da kontrol eder.
        Görev BAŞLAT butonları buna göre kilitlenir (bkz. _on_start_click).
        """
        return self._connected and not self._link_stale


    def _warn_link_unhealthy(self, title: str) -> None:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            self, title,
            "MAVLink bağlantısı şu an sağlıksız (bağlı değil ya da telemetri "
            f"{HEARTBEAT_TIMEOUT_SECS} saniyeden uzun süredir gelmiyor).\n\n"
            "Bağlantı bu durumdayken görev başlatma komutu drona ulaşmayabilir "
            "veya beklenmeyen bir şekilde davranabilir. Önce bağlantıyı "
            "doğrulayın (gerekirse 'Kes' edip yeniden 'Bağlan') ve tekrar deneyin."
        )

    def _on_fcu_start_click(self) -> None:
        """FCU Görevi başlat butonu — onay diyaloğu sonra sinyali yayar."""
        if not self.link_is_healthy():
            self._warn_link_unhealthy("Bağlantı Sağlıksız — Başlatılamıyor")
            return
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
            # E1: tek başlatma yolu — diğer başlatmayı da kilitle, durdur/bırak aç
            self._btn_start_fcu.setEnabled(False)
            self._btn_start.setEnabled(False)
            self._btn_start_fcu.setText("▶ BAŞLATILIYOR...")
            self._btn_abort.setEnabled(True)
            self._btn_drop.setEnabled(True)

    def _on_start_click(self) -> None:
        """Görevi başlat butonu — onay diyaloğu sonra sinyali yayar."""
        if not self.link_is_healthy():
            self._warn_link_unhealthy("Bağlantı Sağlıksız — Başlatılamıyor")
            return
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
            # E1: tek başlatma yolu — FCU başlatmayı da kilitle, durdur/bırak aç
            self._btn_start.setEnabled(False)
            self._btn_start_fcu.setEnabled(False)
            self._btn_start.setText("▶ BAŞLATILIYOR...")
            self._btn_abort.setEnabled(True)
            self._btn_drop.setEnabled(True)

    def _on_abort_click(self) -> None:
        """DURDUR / RTL — çalışan görevi iptal et + drone'u eve döndür."""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Görevi Durdur",
            "Çalışan görev DURDURULACAK ve drone RTL (Eve Dön) yapacak.\n\n"
            "Onaylıyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.abort_mission_requested.emit()
            # Görev durdu → başlat butonları tekrar kullanılabilir, durdur/bırak pasif
            self._btn_abort.setEnabled(False)
            self._btn_drop.setEnabled(False)
            self._btn_start.setEnabled(True)
            self._btn_start_fcu.setEnabled(True)
            self._btn_start.setText("▶ YARD. PC\nGÖREV BAŞLAT")
            self._btn_start_fcu.setText("▶ FCU\nGÖREV BAŞLAT")

    def _on_drop_click(self) -> None:
        """ŞİMDİ BIRAK — manuel yük bırakma komutu (companion'a)."""
        self.drop_now_requested.emit()

    @pyqtSlot(bool)
    def set_mission_ready(self, ready: bool) -> None:
        """Görev yükleme sonucuna göre Başlat butonunu aktive et."""
        self._btn_start.setEnabled(ready)
        self._btn_start_fcu.setEnabled(ready)
        self._btn_start.setText("▶ YARD. PC\nGÖREV BAŞLAT")
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
            self.rpi_connect_requested.emit(host, port)

    @pyqtSlot(bool)
    def set_rpi_connection_status(self, connected: bool) -> None:
        self._rpi_connected = connected
        if connected:
            self._rpi_light.setStyleSheet("color: #4ade80; font-size: 14px;")
            self._btn_rpi_connect.setText("Yard. PC Kes")
        else:
            self._rpi_light.setStyleSheet("color: #555; font-size: 14px;")
            self._btn_rpi_connect.setText("Yard. PC Bağlan")

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
                "192.168.10.229:5763",   # RPi MAVLink
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
            # USB seri adaptörleri (RFD telemetri vb., VID/PID'i olan cihazlar)
            # dahili UART portlarından (ör. ttyAMA*) önce listelenir.
            for p in sorted(ports, key=lambda x: (x.vid is None, x.device)):
                desc = (p.description or "").strip()
                # Bazı sürücüler açıklamada zaten cihaz adını içerir
                if desc and desc.lower() not in ("n/a", p.device.lower()):
                    display = f"{p.device}  —  {desc}"
                else:
                    display = p.device
                # USB cihazlar (VID'i olanlar) için kararlı by-id yolunu bağlantı
                # hedefi yap → USB yeniden numaralanınca (ttyUSB0→ttyUSB1) bağlantı kopmaz.
                target = _stable_serial_path(p.device) if p.vid is not None else p.device
                self._conn_addr.addItem(display, userData=target)
        else:
            self._conn_addr.addItem("COM3", userData="COM3")
            self._conn_addr.lineEdit().setPlaceholderText(  # type: ignore[union-attr]
                "COM port bulunamadı — manuel girin"
            )

    def get_command_widgets(self) -> list:
        return [self._btn_connect]
