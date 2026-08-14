"""
top_bar.py — Global Durum Çubuğu
Sabit QWidget; tüm view'lerde görünür.
İçerir: Bağlantı paneli, Batarya, EKF, Uçuş Modu, GPS.
"""
import logging
import os
import time
from PyQt6.QtCore import pyqtSignal, pyqtSlot, QTimer, Qt
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QProgressBar, QComboBox, QFrame, QSizePolicy,
)

from core import theme

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
        # İki satırlı araç çubuğu — tüm kontroller kırpılmadan sığsın diye
        # yükseklik 84px (şartname §4).
        self.setFixedHeight(84)
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
        # ── Acil kontrol (DURDUR/RTL, ŞİMDİ BIRAK) etkinlik durumu ───────────
        # NEDEN: Bu butonlar ÖNCEDEN yalnızca "bu YKİ oturumunda Başlat'a
        # basıldıysa" aktifleşiyordu. Görev uçarken YKİ çöker/yeniden başlarsa
        # ya da görev companion tarafından otonom başlatılırsa acil durdurma
        # butonu DEVRE DIŞI kalıyor, operatör görevi durduramıyordu.
        # Artık iki KAYNAK birleştirilir (VEYA):
        #   _mission_active   → companion durum akışındaki GERÇEK görev durumu
        #                       (main_window._on_rpi_status → set_mission_active)
        #   _session_started  → bu oturumda Başlat'a basıldı (companion durumu
        #                       daha gelmeden geçen birkaç saniyeyi kapatır)
        # Böylece oturum geçmişi yalnızca EK bir kaynak; tek dayanak değil.
        self._mission_active = False
        self._session_started = False
        # ── Görsel durum belleği (tema değişince renkleri yeniden kurmak için) ──
        # NEDEN: Kurulumda yazılan her satır-içi renk, tema değişince BAYAT kalır.
        # Renkleri yeniden hesaplayabilmek için son bilinen DURUM saklanır; tema
        # değişince aynı yardımcılar tekrar çağrılır. Telemetri sözlüğünü olduğu
        # gibi tekrar update_telemetry'ye vermek YANLIŞ olurdu — o metot heartbeat
        # zaman damgasını da tazeliyor, yani kopuk bağlantıyı taze gösterirdi.
        self._bat_percent: int | None = None    # None → hiç telemetri gelmedi
        self._armed: bool | None = None         # None → mod bilinmiyor (renksiz)
        self._ekf_flags: int | None = None      # None → EKF verisi yok
        self._link_state: bool | None = None    # None → bağlantı durumu bilinmiyor
        self._separators: list[QFrame] = []
        self._base_style = self._make_base_style()

        self._build_ui()

        # Heartbeat timeout kontrolü
        self._hb_timer = QTimer(self)
        self._hb_timer.setInterval(500)
        self._hb_timer.timeout.connect(self._check_heartbeat)
        self._hb_timer.start()

        # Tema değişince satır-içi renkler yeniden yazılır
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _build_ui(self) -> None:
        self._apply_link_style()

        # İki satırlı yerleşim: ÜST satır bağlantı + Yardımcı PC kontrolleri,
        # ALT satır görev butonları + durum göstergeleri. Tek satırda tüm içerik
        # ~2000px sürüyordu ve 1600/1366px'de metinler kırpılıyordu; iki satıra
        # bölünce her şey 1366px'e kadar kırpılmadan sığar.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(4)

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        outer.addLayout(row1)
        outer.addLayout(row2)

        # ── Bağlantı Paneli ──────────────────────────────────────────────────
        self._conn_type = QComboBox()
        self._conn_type.addItems(["Serial", "TCP", "UDP"])
        self._conn_type.setMinimumWidth(78)
        self._conn_type.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._conn_type.setToolTip("Bağlantı tipi")
        self._conn_type.currentIndexChanged.connect(self._on_conn_type_changed)

        # Adres combo (Serial → COM listesi, TCP → port seçimi)
        self._conn_addr = QComboBox()
        self._conn_addr.setEditable(True)
        self._conn_addr.setMinimumWidth(180)
        self._conn_addr.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._conn_addr.setToolTip("Bağlantı adresi / port")
        self._conn_addr.lineEdit().setPlaceholderText("Bağlantı adresi")  # type: ignore[union-attr]

        # Baudrate seçici (yalnızca Serial modunda görünür)
        self._baud_combo = QComboBox()
        self._baud_combo.addItems(["57600", "115200", "9600", "19200", "38400", "230400"])
        self._baud_combo.setCurrentText("57600")
        self._baud_combo.setMinimumWidth(90)
        self._baud_combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._baud_combo.setToolTip("Baudrate")

        # Veri tipi seçici (yalnızca Serial modunda görünür)
        self._data_type_combo = QComboBox()
        self._data_type_combo.addItems(["Drone", "Gimbal"])
        self._data_type_combo.setMinimumWidth(92)
        self._data_type_combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        # NEDEN: "Gimbal" seçeneğinin arkasında çalışan bir worker YOK
        # (kod tabanında gimbal_worker tanımlı değil). Seçenek kaldırılmadı ki
        # ileride desteklenince yer değişmesin; ancak seçilirse main_window
        # _on_connect_requested içinde açık bir "desteklenmiyor" uyarısı verir ve
        # durum çubuğuna BAŞARI yazmaz. Kullanıcı bunu tıklamadan önce görsün:
        self._data_type_combo.setToolTip(
            "Bu porttaki veri tipi.\n"
            "UYARI: 'Gimbal' bu sürümde DESTEKLENMİYOR — seçilirse bağlantı kurulmaz."
        )

        # Seri port yenile butonu
        self._btn_refresh = QPushButton("↺")
        self._btn_refresh.setFixedWidth(32)
        self._btn_refresh.setToolTip("Portları Tara")
        self._btn_refresh.clicked.connect(self._refresh_ports)

        self._btn_connect = QPushButton("Bağlan")
        self._btn_connect.setObjectName("primaryButton")
        self._btn_connect.setMinimumWidth(76)
        self._btn_connect.clicked.connect(self._on_connect_click)

        # ── Yardımcı PC (RPi companion) bağlantı kontrolleri ─────────────────
        self._rpi_addr = QComboBox()
        self._rpi_addr.setEditable(True)
        self._rpi_addr.addItems([
            "192.168.1.10:5602",     # Companion (RPi) — kanonik (bkz. gcs_config)
            "127.0.0.1:5602",        # Localhost (sim)
        ])
        self._rpi_addr.setMinimumWidth(180)
        self._rpi_addr.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._rpi_addr.lineEdit().setPlaceholderText("Yardımcı PC IP:Port")  # type: ignore[union-attr]
        self._rpi_addr.setToolTip("Yardımcı Bilgisayar durum akışı adresi (StatusBroadcaster TCP)")

        self._btn_rpi_connect = QPushButton("Yard. PC Bağlan")
        self._btn_rpi_connect.setMinimumWidth(140)
        self._btn_rpi_connect.clicked.connect(self._on_rpi_connect_click)

        self._rpi_light = QLabel("●")
        self._rpi_light.setToolTip("Yardımcı Bilgisayar bağlantı durumu")
        self._apply_rpi_light_style()

        # Üst satırı kur: drone bağlantı grubu SOLDA, esnek boşluk ortada,
        # Yardımcı PC grubu SAĞDA (şartname §4 — satır dengeli dolar,
        # pencere genişleyince boşluk ortada açılır, iki uç da kırpılmaz).
        row1.addWidget(QLabel("Bağlantı:"))
        row1.addWidget(self._conn_type)
        row1.addWidget(self._conn_addr)
        row1.addWidget(self._baud_combo)
        row1.addWidget(self._data_type_combo)
        row1.addWidget(self._btn_refresh)
        row1.addWidget(self._btn_connect)
        row1.addStretch(1)
        row1.addWidget(QLabel("Yard. PC:"))
        row1.addWidget(self._rpi_addr)
        row1.addWidget(self._btn_rpi_connect)
        row1.addWidget(self._rpi_light)

        # İlk durum: bağlantı tipine göre adres listesini doldur
        self._on_conn_type_changed(0)

        # ── GÖREVİ BAŞLAT — yüklü görev için arm + AUTO ──────────────────────
        # Tek satırlı başlıklar; dört görev butonu da AYNI (30px) yükseklikte,
        # genişlik içerikten gelir (şartname §3).
        self._btn_start = QPushButton("▶ Görev Başlat (Yard. PC)")
        self._btn_start.setMinimumHeight(30)
        self._btn_start.setEnabled(False)
        self._btn_start.setObjectName("successButton")
        self._btn_start.setToolTip(
            "Yüklü görevi görev bilgisayarı üzerinden başlatır: ARM → GUIDED/AUTO → uçuş.\n"
            "Görev başarıyla yüklendiğinde aktifleşir."
        )
        self._btn_start.clicked.connect(self._on_start_click)

        self._btn_start_fcu = QPushButton("▶ Görev Başlat (FCU)")
        self._btn_start_fcu.setMinimumHeight(30)
        self._btn_start_fcu.setEnabled(False)
        self._btn_start_fcu.setObjectName("successButton")
        self._btn_start_fcu.setToolTip(
            "Yüklü görevi FCU üzerinden başlatır: ARM → HAFİF GAZ → TAKEOFF → AUTO uçuş.\n"
            "Görev başarıyla yüklendiğinde aktifleşir."
        )
        self._btn_start_fcu.clicked.connect(self._on_fcu_start_click)

        # ── DURDUR / RTL — çalışan görevi iptal et (companion abort + doğrudan RTL) ──
        self._btn_abort = QPushButton("■ Durdur / RTL")
        self._btn_abort.setMinimumHeight(30)
        self._btn_abort.setObjectName("dangerButton")
        self._btn_abort.setEnabled(False)
        self._btn_abort.setToolTip(
            "Çalışan görevi durdurur: Yardımcı Bilgisayar görevini iptal eder VE\n"
            "FC'yi doğrudan RTL (Eve Dön) moduna alır.\n"
            "Görev GERÇEKTEN çalışıyorken aktiftir — görevi bu YKİ oturumu\n"
            "başlatmamış olsa bile (otonom başlatma / YKİ yeniden başlatma)."
        )
        self._btn_abort.clicked.connect(self._on_abort_click)

        # ── ŞİMDİ BIRAK — manuel yük bırakma (autonomous_drop=False modunda) ──
        self._btn_drop = QPushButton("⬇ Şimdi Bırak")
        self._btn_drop.setMinimumHeight(30)
        self._btn_drop.setObjectName("warningButton")
        self._btn_drop.setEnabled(False)
        self._btn_drop.setToolTip(
            "Manuel yük bırakma (Görev 2/3 manuel mod): hedef üzerinde kilitliyken\n"
            "operatör bu butonla bırakır. Görev GERÇEKTEN çalışıyorken aktiftir.\n"
            "Onay diyaloğu ile korunur (yanlış tıklama yükü havada bırakmasın)."
        )
        self._btn_drop.clicked.connect(self._on_drop_click)

        # ── Durum göstergeleri (batarya / EKF / mod / GPS) ───────────────────
        self._bat_label = QLabel("BAT")
        self._bat_bar = QProgressBar()
        self._bat_bar.setFixedWidth(90)
        self._bat_bar.setFixedHeight(16)
        self._bat_bar.setRange(0, 100)
        self._bat_bar.setValue(0)
        self._bat_bar.setTextVisible(True)

        # Durum etiketleri: ilk kurulumdaki font, update_telemetry'nin bastığı
        # inline stille AYNI (11px bold) — bağlantı öncesi/sonrası zıplamasın.
        self._ekf_indicator = QLabel("EKF ●")
        self._apply_ekf_style()

        self._mode_label = QLabel("MODU: —")
        self._apply_mode_style()

        self._gps_label = QLabel("GPS: —")
        self._gps_label.setStyleSheet("font-size:11px; font-weight:bold;")

        self._status_light = QLabel("●")
        self._apply_status_light_style()

        # Alt satırı kur: görev butonları solda, esnek boşluk, durum
        # göstergeleri sağa yaslı (esnek boşluk sayesinde en sağdaki BAT/EKF/
        # MOD/GPS çipleri asla ekran dışına itilmez ve tam görünür).
        # Durum göstergeleri ve ayraçlar 30px'lik buton satırına göre dikeyde
        # ORTALANIR (şartname §4 — iki dengeli satır, hiza kaymasın).
        vcenter = Qt.AlignmentFlag.AlignVCenter
        row2.addWidget(self._btn_start_fcu)
        row2.addWidget(self._btn_start)
        row2.addWidget(self._btn_abort)
        row2.addWidget(self._btn_drop)
        row2.addStretch(1)
        row2.addWidget(self._separator(), 0, vcenter)
        row2.addWidget(self._bat_label, 0, vcenter)
        row2.addWidget(self._bat_bar, 0, vcenter)
        row2.addWidget(self._separator(), 0, vcenter)
        row2.addWidget(self._ekf_indicator, 0, vcenter)
        row2.addWidget(self._separator(), 0, vcenter)
        row2.addWidget(self._mode_label, 0, vcenter)
        row2.addWidget(self._separator(), 0, vcenter)
        row2.addWidget(self._gps_label, 0, vcenter)
        row2.addWidget(self._status_light, 0, vcenter)

    def _separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        # AlignVCenter ile eklendiğinde sizeHint'e büzülmesin diye sabit boy
        sep.setFixedHeight(18)
        sep.setStyleSheet(f"color: {theme.c('border')};")
        # Tema değişince rengi tazelenebilsin diye referansı sakla
        self._separators.append(sep)
        return sep

    # ── Tema: satır-içi renk yardımcıları ─────────────────────────────────────
    # Her biri SON BİLİNEN duruma bakıp rengi yeniden hesaplar; hem kurulumda
    # hem telemetri geldiğinde hem de tema değişiminde aynı yol kullanılır.

    def _make_base_style(self) -> str:
        """TopBar'ın normal (sağlıklı bağlantı) zemin/kenar stili."""
        return (
            f"TopBar {{ background-color: {theme.c('toolbar')}; "
            f"border-bottom: 1px solid {theme.c('border_soft')}; }}"
        )

    def _apply_link_style(self) -> None:
        """Bağlantı sağlığına göre TopBar zeminini uygular (stale → kırmızı)."""
        self._base_style = self._make_base_style()
        if self._link_stale:
            self.setStyleSheet(
                self._base_style +
                f"TopBar {{ border-bottom: 3px solid {theme.c('danger')}; "
                f"background: {theme.c('danger_bg')}; }}"
            )
        else:
            self.setStyleSheet(self._base_style)

    def _apply_battery_style(self) -> None:
        """Batarya çubuğu: <=%20 danger, <=%50 warning, üstü success."""
        if self._bat_percent is None:
            return   # hiç telemetri gelmedi → QSS varsayılan chunk rengi kalsın
        if self._bat_percent <= 20:
            color = theme.c("danger")
        elif self._bat_percent <= 50:
            color = theme.c("warning")
        else:
            color = theme.c("success")
        self._bat_bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; }}")

    def _apply_mode_style(self) -> None:
        """Mod etiketi: ARM ise danger, DSM ise success (durum bilinmiyorsa renksiz)."""
        base = "font-size:11px; font-weight:bold;"
        if self._armed is None:
            self._mode_label.setStyleSheet(base)
        elif self._armed:
            self._mode_label.setStyleSheet(f"color:{theme.c('danger')}; {base}")
        else:
            self._mode_label.setStyleSheet(f"color:{theme.c('success')}; {base}")

    def _apply_ekf_style(self) -> None:
        """EKF ışığı: tam çözüm success, kısmi warning, hata danger, veri yok idle.

        NOT: font-size/weight ilk kurulumla AYNI tutulur — yalnız renk değişir
        (çalışma anında değişen renk için minimal inline stil istisnası)."""
        flags = self._ekf_flags
        if flags is None:
            color = theme.c("idle")
        elif (flags & 0x1F) == 0x1F:   # attitude+vel_h+vel_v+pos_h+pos_v
            color = theme.c("success")
        elif flags & 0x01:
            color = theme.c("warning")
        else:
            color = theme.c("danger")
        self._ekf_indicator.setStyleSheet(f"color:{color}; font-size:11px; font-weight:bold;")

    def _apply_status_light_style(self) -> None:
        """Durum ışığı: bağlı success / kopuk danger / bilinmiyor idle."""
        if self._link_state is None:
            color = theme.c("idle")
        else:
            color = theme.c("success") if self._link_state else theme.c("danger")
        self._status_light.setStyleSheet(f"color: {color}; font-size: 16px;")

    def _apply_rpi_light_style(self) -> None:
        """Yardımcı PC ışığı: bağlı success / bağlı değil idle."""
        color = theme.c("success") if self._rpi_connected else theme.c("idle")
        self._rpi_light.setStyleSheet(f"color: {color}; font-size: 14px;")

    def _apply_theme_colors(self) -> None:
        """Tema değişince TÜM satır-içi renkleri son duruma göre yeniden yazar."""
        self._apply_link_style()
        self._apply_battery_style()
        self._apply_mode_style()
        self._apply_ekf_style()
        self._apply_status_light_style()
        self._apply_rpi_light_style()
        for sep in self._separators:
            sep.setStyleSheet(f"color: {theme.c('border')};")

    # ── Slot: Telemetri ───────────────────────────────────────────────────────
    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        bat = int(data.get("battery_remaining", 0))
        self._bat_bar.setValue(max(0, min(100, bat)))
        self._bat_percent = bat
        self._apply_battery_style()

        # GPS
        sats = data.get("gps_sats", "—")
        fix = data.get("gps_fix_name", "—")
        self._gps_label.setText(f"GPS: {fix} [{sats}sat]")

        # Mod + Arm durumu
        mode = data.get("flight_mode", "—")
        armed = data.get("armed", False)
        arm_txt = " ▲ARM" if armed else " ▽DSM"
        self._mode_label.setText(f"{mode}{arm_txt}")
        self._armed = bool(armed)
        self._apply_mode_style()

        # EKF
        ekf_flags = data.get("ekf_flags", None)
        self._ekf_flags = ekf_flags
        self._ekf_indicator.setText("EKF ●")
        self._apply_ekf_style()
        if ekf_flags is None:
            self._ekf_indicator.setToolTip("EKF verisi yok")
        elif (ekf_flags & 0x1F) == 0x1F:   # attitude+vel_h+vel_v+pos_h+pos_v
            self._ekf_indicator.setToolTip(f"EKF İYİ (0x{ekf_flags:04x})")
        elif ekf_flags & 0x01:
            self._ekf_indicator.setToolTip(f"EKF KISMI (0x{ekf_flags:04x}): attitude OK, pos eksik")
        else:
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
        self._apply_link_style()
        self._link_state = bool(connected)
        self._apply_status_light_style()
        if connected:
            self._btn_connect.setText("Kes")
        else:
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
            self._apply_link_style()
        elif not stale and self._link_stale:
            self._link_stale = False
            self._apply_link_style()

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
            self._btn_start_fcu.setText("▶ Başlatılıyor...")
            # Companion "görev aktif" durumu birkaç saniye sonra gelir; o boşlukta
            # acil kontroller kapalı kalmasın diye oturum bayrağı da işaretlenir.
            self._session_started = True
            self._sync_mission_controls()

    def _on_start_click(self) -> None:
        """Görevi başlat butonu — onay diyaloğu sonra sinyali yayar."""
        if not self.link_is_healthy():
            self._warn_link_unhealthy("Bağlantı Sağlıksız — Başlatılamıyor")
            return
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Görevi Başlat",
            "Yardımcı bilgisayara görev başlatma komutu gönderilecek.\n"
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
            self._btn_start.setText("▶ Başlatılıyor...")
            # Bkz. _on_fcu_start_click — oturum bayrağı yalnızca ek kaynaktır.
            self._session_started = True
            self._sync_mission_controls()

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
            # Görev durdu → başlat butonları tekrar kullanılabilir
            self._btn_start.setEnabled(True)
            self._btn_start_fcu.setEnabled(True)
            self._btn_start.setText("▶ Görev Başlat (Yard. PC)")
            self._btn_start_fcu.setText("▶ Görev Başlat (FCU)")
            # NEDEN: Durdur/bırak butonları ARTIK körü körüne kapatılmıyor.
            # Oturum bayrağı düşer, ama companion hâlâ "görev aktif" bildiriyorsa
            # DURDUR/RTL açık kalır — ilk abort komutu araca ulaşmamış olabilir ve
            # operatörün tekrar deneyebilmesi gerekir. Görev gerçekten bitince
            # set_mission_active(False) ile kendiliğinden pasifleşir.
            self._session_started = False
            self._sync_mission_controls()

    def _on_drop_click(self) -> None:
        """ŞİMDİ BIRAK — manuel yük bırakma komutu (companion'a).

        NEDEN onay: Yük bırakma GERİ ALINAMAZ bir komut — yanlış tıklama yükü
        havada, hedef dışında bırakır. DURDUR/RTL'de zaten onay diyaloğu vardı;
        aynı desen (Türkçe metin, varsayılan buton 'Hayır') buraya da uygulandı.
        """
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Yükü Şimdi Bırak",
            "Yük ŞİMDİ bırakılacak. Bu işlem GERİ ALINAMAZ.\n\n"
            "Aracın hedef üzerinde ve doğru irtifada olduğunu doğrulayın.\n\n"
            "Onaylıyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.drop_now_requested.emit()

    @pyqtSlot(bool)
    def set_mission_ready(self, ready: bool) -> None:
        """Görev yükleme sonucuna göre Başlat butonunu aktive et."""
        self._btn_start.setEnabled(ready)
        self._btn_start_fcu.setEnabled(ready)
        self._btn_start.setText("▶ Görev Başlat (Yard. PC)")
        self._btn_start_fcu.setText("▶ Görev Başlat (FCU)")

    @pyqtSlot(bool)
    def set_mission_active(self, active: bool) -> None:
        """Companion durum akışındaki GERÇEK görev durumunu bildirir (KRİTİK).

        main_window._on_rpi_status her durum paketinde çağırır. Acil kontroller
        (DURDUR/RTL, ŞİMDİ BIRAK) artık bu GERÇEK duruma bağlıdır; "bu oturumda
        Başlat'a basıldı mı" geçmişine değil. Böylece:
          • görev uçarken YKİ yeniden başlatılsa,
          • ya da görevi companion otonom başlatsa,
        operatör yine de görevi durdurabilir.

        NOT: Çağıran taraf RBAC'i uygular (VIEWER için False geçirilir) — burada
        rol kontrolü yapılmaz, tek sorumluluk kaynağı main_window'dur.
        """
        self._mission_active = bool(active)
        self._sync_mission_controls()

    def mission_controls_live(self) -> bool:
        """Acil kontrollerin şu an etkin olup olmadığı (test/teşhis amaçlı)."""
        return self._mission_active or self._session_started

    def _sync_mission_controls(self) -> None:
        """Acil kontrollerin etkinliğini iki kaynağın BİRLEŞİMİNE göre ayarlar.

        Etkin = (companion 'görev aktif' diyor) VEYA (bu oturum görevi başlattı).
        VEYA kullanılır çünkü iki kaynak da tek başına eksiktir: companion durumu
        gecikmeli/kopuk olabilir, oturum geçmişi ise YKİ yeniden başlarsa silinir.
        """
        live = self.mission_controls_live()
        self._btn_abort.setEnabled(live)
        self._btn_drop.setEnabled(live)

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
        self._apply_rpi_light_style()
        if connected:
            self._btn_rpi_connect.setText("Yard. PC Kes")
        else:
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
                "192.168.1.10:5763",     # RPi MAVLink — kanonik (bkz. gcs_config)
                "127.0.0.1:5763",        # SITL / laptop sim (tcpin:5763)
                "127.0.0.1:5762",
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
