"""
main_window.py — ASHİNA YKİ Ana Pencere
Tüm view'leri barındırır; signal/slot bağlamalarını yapar.
Watchdog QTimer (5 sn) ile worker heartbeat'lerini izler.
"""
import logging
import re
from PyQt6.QtCore import QTimer, pyqtSlot, Qt
from PyQt6.QtGui import QFont, QShortcut, QKeySequence
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QTabWidget,
    QStatusBar, QLabel,
)

from core.global_state import state
from core.mavlink_worker import MavlinkWorker
from core.ai_socket_worker import AISocketWorker
from core.ntrip_worker import NtripWorker
# JoystickWorker sistemden kaldırıldı
from core.video_stream_worker import VideoStreamWorker
from core.audio_tts_worker import AudioTTSWorker
from core.rpi_status_worker import RpiStatusWorker
from core.gcs_config import config
from core import local_storage
from core import theme

from ui.top_bar import TopBar
from ui.auth_manager import AuthManager
from ui.views.dashboard_view import DashboardView
from ui.views.mission_panel_view import MissionPanelView
from ui.views.mission_view import MissionPlanView
from ui.views.test_panel_view import TestPanelView
from ui.views.calibration_view import CalibrationView
from ui.views.tuning_view import TuningView
from ui.views.advanced_view import AdvancedView
from ui.views.system_log_view import SystemLogView
from ui.views.flight_analysis_view import FlightAnalysisView
from ui.views.autotune_view import AutoTuneView
from ui.views.settings_view import SettingsView
from ui.views.iha_test_panel_view import IHATestPanelView
from ui.views.saha_testi_view import SahaTestiView
from core.telemetry_recorder import TelemetryRecorder

logger = logging.getLogger(__name__)

WATCHDOG_INTERVAL_MS = 5000
# Kaç ardışık "sayaç değişmedi" örneğinden sonra worker GERÇEKTEN "takıldı"
# sayılsın. ÖNCEDEN tek örnekte (5s) karar veriliyordu — ama AISocketWorker'ın
# tek bir gönderimi (connect+response timeout, varsayılan 5+5=10s) veya
# MavlinkWorker'ın ilk bağlantıdaki wait_heartbeat(timeout=10) çağrısı gibi
# MEŞRU bloklayan işlemler bu pencereyi rahatça aşıyor — tam da "flaky bağlantı"
# senaryosunda (kullanıcının asıl endişesi) sahte "takıldı/yeniden başlatıldı"
# alarmı üretiyordu. 3 ardışık örnek (15s) bu meşru gecikmeleri güvenle tolere
# ederken gerçek bir kilitlenmeyi hâlâ makul sürede yakalar.
HANG_CONFIRM_TICKS = 3



def _video_urlleri(cfg, host: str) -> tuple:
    """(ham_url, islenmis_url) — taşıyıcıya göre.

    NEDEN seçenek: UDP canlı görüntü için doğru taşıyıcı (gerçek linkte
    ölçüldü: ham akışta kare arası en kötü gecikme HTTP'de 77 ms, UDP'de
    41 ms). Ama İHA her iki sunucuyu da açık tutuyor; sahada UDP'de bir
    sorun çıkarsa ayarlardan "http" yazıp anında geri dönülebilir.
    """
    if getattr(cfg, "video_transport", "udp") == "udp":
        p = cfg.companion_video_udp_port
        return (f"udp://{host}:{p}/0", f"udp://{host}:{p}/1")
    return (f"http://{host}:{cfg.companion_video_raw_port}/video",
            f"http://{host}:{cfg.companion_video_port}/video")

class MainWindow(QMainWindow):
    def __init__(self, db_conn, auth_manager: AuthManager, parent=None):
        super().__init__(parent)
        self._db_conn = db_conn
        self._auth = auth_manager
        self._hb_snapshots: dict = {}
        self._hb_stale_count: dict = {}   # ardışık "değişmedi" örnek sayısı (bkz. HANG_CONFIRM_TICKS)
        # Kurtarılamayan (takılı ve durdurulamayan) worker adları. NEDEN set:
        # uyarı yalnızca DURUM DEĞİŞİMİNDE bir kez verilsin, her tick'te değil
        # (bkz. _report_worker_unrecoverable).
        self._hb_unrecoverable: set = set()
        self._prev_rpi_state: dict = {}   # önceki RPi durumu — değişim tespiti için
        # Son uygulanan RBAC rolü — çalışma anındaki rol değişimini yakalamak için
        # (bkz. _check_rbac_change).
        self._last_rbac_role: int = -1
        # Durum çubuğu sağ etiketinin SON uygulanan tema token'ı + kalınlığı.
        # Renk çalışma anında değişiyor (bağlantı/görev/failsafe); tema değişince
        # aynı DURUMU yeni paletle yeniden boyayabilmek için saklanır
        # (bkz. _apply_rpi_status_style / _apply_theme_colors).
        self._rpi_status_token: str = "text_muted"
        self._rpi_status_bold: bool = False

        self.setWindowTitle("ASHİNA YKİ  |  Taktiksel Yer Kontrol İstasyonu v2.0")
        self.setMinimumSize(1280, 768)
        self._apply_style()

        self._init_workers()
        self._build_ui()
        self._connect_signals()
        # refresh_rbac (yalın _apply_rbac değil) → _last_rbac_role da damgalanır,
        # böylece ilk watchdog tick'i sahte bir "rol değişti" tespiti yapmaz.
        self.refresh_rbac()
        # NEDEN ertelenmiş: _start_workers() gerçek bağlantılar açar (anten
        # Pixhawk seri bağlantısı, RTK GPS, TTS motoru — ONNX model yükleme)
        # ve bunlar gerçek zaman alır. Doğrudan çağrılırsa pencere app.exec()
        # başlayana kadar hiç BOYANMAZ (main.py: showFullScreen() → app.exec()
        # sırası) — kullanıcı ~18s boyunca hiçbir şey görmez. singleShot(0),
        # ilk event loop turunda (pencere zaten çizildikten hemen sonra) çalışır.
        QTimer.singleShot(0, self._start_workers)

        # Tema değişince satır içi yazılmış renkler bayat kalır — yeniden yazdır.
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

        # Tam ekrandan çıkış / tam ekrana geçiş (kiosk modunda pencere çerçevesi yok)
        self._fullscreen_shortcut = QShortcut(QKeySequence("F11"), self)
        self._fullscreen_shortcut.activated.connect(self._toggle_fullscreen)

        # Watchdog
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(WATCHDOG_INTERVAL_MS)
        self._watchdog.timeout.connect(self._check_workers)
        self._watchdog.start()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            # Açılış splash'ı (splash_loading.py) hiç kendiliğinden kapanmaz
            # (bkz. o dosyanın kendi notu) — YKİ zaten üstünde durduğu için
            # normalde görünmez, ama operatör F11 ile masaüstüne inince
            # ALTTA duran splash görünür olur. Gerçek masaüstünü görebilsin
            # diye burada kapatılır; YKİ'yi tekrar F11 ile açmak yeterli,
            # splash'a geri dönmeye gerek yok.
            import subprocess
            try:
                subprocess.run(["pkill", "-f", "splash_loading.py"],
                               timeout=2, capture_output=True)
            except Exception:
                pass
        else:
            self.showFullScreen()

    def _apply_style(self) -> None:
        # Global style.qss üzerinden yönetiliyor.
        pass

    # ── Tema ─────────────────────────────────────────────────────────────────
    def _apply_rpi_status_style(self, token: str, bold: bool = False) -> None:
        """Durum çubuğu sağ etiketinin rengini tema token'ından uygular.

        Metne DOKUNMAZ (yalnız renk) — bazı çağrılar (failsafe uyarısı) mevcut
        metni korumak ister. Kullanılan token saklanır; tema değişince
        _apply_theme_colors aynı token'ı yeni paletle tekrar yazar.
        """
        self._rpi_status_token = token
        self._rpi_status_bold = bold
        weight = "font-weight: bold; " if bold else ""
        self._rpi_status_label.setStyleSheet(
            f"color: {theme.c(token)}; {weight}padding: 0 8px;"
        )

    def _apply_theme_colors(self) -> None:
        """Tema değişince kurulumda yazılmış satır içi renkleri yeniden yazar.

        Bu pencerede satır içi renk YALNIZCA durum çubuğu sağ etiketinde var ve
        değeri duruma bağlı; en son uygulanan token/kalınlık saklandığı için
        mevcut durum yeni paletle birebir yeniden boyanır.
        """
        self._apply_rpi_status_style(self._rpi_status_token, self._rpi_status_bold)

    # ── Worker Başlatma ──────────────────────────────────────────────────────
    def _init_workers(self) -> None:
        # mode=sim → sahte worker'lar (gerçek İHA/SITL olmadan arayüzü canlı gör).
        # Fake sınıflar gerçek worker'ları alt-sınıflar; imzalar aynı, sinyaller aynı.
        if config.is_sim:
            from core.sim_workers import (
                FakeMavlinkWorker as _MavCls,
                FakeVideoWorker as _VidCls,
                FakeRpiStatusWorker as _RpiCls,
            )
            logger.info("YKİ SİMÜLASYON modu — sahte worker'lar (mavlink/video/rpi) aktif")
        else:
            _MavCls, _VidCls, _RpiCls = MavlinkWorker, VideoStreamWorker, RpiStatusWorker

        # Video kaynağı değişiminde (_apply_video_source) yeniden yaratmak için sakla.
        self._video_cls = _VidCls

        self.mavlink_worker = _MavCls(self)
        self.mavlink_worker.set_db_conn(self._db_conn)
        # FIX P1-4: Timeout değerlerini config'den al
        self.ai_worker = AISocketWorker(
            host=config.companion_host,
            port=config.companion_mission_port,
            connect_timeout=config.ai_connect_timeout,
            response_timeout=config.ai_response_timeout,
            auth_token=config.companion_auth_token,
            parent=self,
        )
        # NTRIP: kullanıcı başlatana kadar None — otomatik başlamaz
        self.ntrip_worker: NtripWorker | None = None
        # Yer istasyonu F9P'sinin son bilinen NMEA konumu — NTRIP VRS mountpoint'i
        # GGA bootstrap için ister (bkz. _on_rtk_position_update/_on_start_ntrip).
        self._last_gps_fix: tuple[float, float, float] | None = None
        # İki video kaynağı: ham (görev yokken) ve işlenmiş (görev sırasında).
        # Kamera YALNIZCA Yardımcı Bilgisayar'a bağlanınca açılır (otomatik denemez).
        self._video_url_raw, self._video_url_processed = _video_urlleri(config, config.companion_host)
        self._current_video_source = "none"
        # Kullanıcı diyalogdan Ham/İşlenmiş seçerse görev-bazlı otomatik geçiş durur.
        # "Otomatik" seçilince tekrar açılır.
        self._video_manual_override = False
        self._last_mission_running = False   # son RPi status'tan görev aktif mi
        self.video_worker = _VidCls(
            source="none",
            open_timeout=config.video_open_timeout,
            retry_delay=config.video_retry_delay,
            parent=self,
        )
        self.tts_worker = AudioTTSWorker(parent=self)
        self.rpi_worker = _RpiCls(
            host=config.companion_host,
            port=config.companion_status_port,
            connect_timeout=config.rpi_connect_timeout,
            read_timeout=config.rpi_read_timeout,
            auth_token=config.companion_auth_token,
            parent=self,
        )
        self.telemetry_recorder = TelemetryRecorder(self._db_conn, parent=self)
        # Önceki oturum temiz kapanmadıysa (çökme/güç kesintisi) uçuş yalnızca
        # .autosave.json olarak kalır ve "Kayıtlar" sekmesinde GÖRÜNMEZ.
        # Açılışta bunları kalıcı kayda çevir.
        try:
            _rec_n = self.telemetry_recorder.recover_orphan_autosaves()
            if _rec_n:
                logger.info("Yarım kalmış %d uçuş kaydı kurtarıldı", _rec_n)
        except Exception as exc:
            logger.warning("Uçuş kaydı kurtarma başarısız: %s", exc)

        # Sesli otopilot: telemetriyi insan diline çeviren anlatıcı
        # (Qt'siz saf mantık — core/flight_narrator.py)
        from core.flight_narrator import FlightNarrator
        self._narrator = FlightNarrator()

        # RTK GPS izleme paneli (salt-okunur, F9P NMEA)
        # NOT: sinyal bağlantısı _connect_signals()'ta.
        self.rtk_gps_worker = None
        if config.rtk_gps_enabled:
            from core.rtk_gps_worker import RtkGpsWorker
            self.rtk_gps_worker = RtkGpsWorker(
                port=config.rtk_gps_port, baud=config.rtk_gps_baud, parent=self)

    # ── Otomatik bağlanma yardımcıları ───────────────────────────────────
    @staticmethod
    def _telemetri_radyosu_bul() -> str:
        """/dev/serial/by-id altındaki ilk TELEMETRİ radyosunu döndürür.

        GNSS alıcıları (u-blox F9P) BİLEREK elenir: o RTK tabanıdır, MAVLink
        radyosu değil. Ona bağlanmak MAVLink akışı hiç gelmeden "bağlandı"
        görüntüsü verirdi. by-id kullanılır çünkü /dev/ttyUSBx numaraları
        fişten çekip takınca kayar (bkz. top_bar.py başındaki not).
        """
        import os
        d = "/dev/serial/by-id"
        try:
            adaylar = sorted(os.listdir(d))
        except OSError:
            return ""
        for ad in adaylar:
            dusuk = ad.lower()
            if "u-blox" in dusuk or "ublox" in dusuk or "gnss" in dusuk:
                continue
            return os.path.join(d, ad)
        return ""

    def _oto_drone_baglantisi(self):
        """Otomatik bağlanma için (tip, adres, baud); bulunamazsa None."""
        import os
        tip = config.drone_link_type
        if tip in ("tcp", "udp"):
            return tip, config.mavlink_address, 57600
        port = config.drone_serial_port
        if not port or not os.path.exists(port):
            port = self._telemetri_radyosu_bul()
        if not port:
            return None
        return "serial", port, config.drone_serial_baud

    def _start_workers(self) -> None:
        self.tts_worker.start_tts_process()
        self.tts_worker.start()
        self.mavlink_worker.start()
        self.ai_worker.start()
        self.video_worker.start()
        self.rpi_worker.start()
        if self.rtk_gps_worker is not None:
            self.rtk_gps_worker.start()
        # ntrip_worker: varsayılan olarak kullanıcı isteyince başlar (None durumda),
        # AMA config.ntrip_autostart=True + host/mountpoint doluysa YKİ açılışında
        # otomatik bağlanır (2026-08-28, kullanıcı talebi — TUSAGA-Aktif aboneliği).
        if config.ntrip_autostart and config.ntrip_host and config.ntrip_mountpoint:
            self._on_start_ntrip(config.ntrip_host, config.ntrip_port,
                                 config.ntrip_mountpoint, config.ntrip_username,
                                 config.ntrip_password)
        # ── AÇILIŞTA KENDİLİĞİNDEN BAĞLAN (2026-09-03, operatör talebi) ───
        # Worker'lar başladıktan SONRA: ikisi de kendi thread'inde bağlantı
        # dizesini bekliyor. Buraya kadar kimse set_connection/enable
        # çağırmadığı için YKİ, operatör iki düğmeye basana dek BOŞTA
        # duruyordu.
        # İLK DENEMENİN BOŞA DÜŞMESİ BEKLENEN BİR DURUMDUR: araç ve yardımcı
        # bilgisayar açılırken YKİ önce hazır olabilir. Her iki worker da
        # sonsuza kadar yeniden dener ve mavlink_worker bağlantı dizesini
        # hata hâlinde korur — araç geri geldiğinde kendiliğinden bağlanır.
        # Elle "Kes" denirse geri bağlanmaz (bkz. _on_disconnect_requested:
        # worker boşaltılıyor) — operatörle inatlaşmamak için bilerek.
        # ── DRONE (RFD) — AYRI ANAHTAR, VARSAYILAN KAPALI ─────────────────
        # Operatör talebi (2026-09-03): drone linki ELLE kurulacak. Yardımcı
        # Bilgisayar otomatik bağlanmasından BAĞIMSIZ — biri diğerini etkilemez.
        if config.drone_auto_connect:
            # NEDEN try DIŞINDA ilklenir: aşağıdaki log satırı _drone'u okuyor.
            # try içinde kalsaydı _oto_drone_baglantisi() bir istisna attığında
            # isim hiç bağlanmaz ve log satırı NameError verirdi.
            _drone = None
            try:
                _drone = self._oto_drone_baglantisi()
                if _drone is None:
                    logger.warning("Otomatik drone bağlantısı ATLANDI — telemetri "
                                   "radyosu bulunamadı (/dev/serial/by-id boş). "
                                   "Radyo takılınca üst çubuktan elle bağlanın.")
                else:
                    self._on_connect_requested(_drone[0], _drone[1], _drone[2], "drone")
                    # NEDEN gerçekten kullanılan bağlantı yazılıyor: seri olsa
                    # bile config.mavlink_address'i (TCP) basmak, kurulmayan
                    # bir bağlantıyı kurulmuş gibi gösterirdi.
                    _d = ("%s @ %s" % (_drone[1], _drone[2]) if _drone[0] == "serial"
                          else "%s:%s" % (_drone[0], _drone[1]))
                    logger.info("Otomatik drone bağlantısı AÇIK — %s", _d)
            except Exception:
                logger.exception("Otomatik drone bağlantısı başlatılamadı")
        else:
            logger.info("Otomatik drone bağlantısı KAPALI (drone_auto_connect=false) "
                        "— üst çubuktan elle bağlanın.")

        # ── YARDIMCI BİLGİSAYAR — ayrı anahtar (auto_connect) ─────────────
        if config.auto_connect:
            try:
                self._on_rpi_connect_requested(config.companion_host,
                                               config.companion_status_port)
                logger.info("Otomatik Yardımcı Bilgisayar bağlantısı AÇIK — %s:%d",
                            config.companion_host, config.companion_status_port)
            except Exception:
                logger.exception("Otomatik Yardımcı Bilgisayar bağlantısı başlatılamadı")
        else:
            logger.info("Otomatik Yardımcı Bilgisayar bağlantısı KAPALI (auto_connect=false)")
        logger.info("Worker thread'leri başlatıldı (NTRIP isteğe bağlı)")

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # TopBar
        self.top_bar = TopBar(self)
        root.addWidget(self.top_bar)

        # Sekmeler
        self._tabs = QTabWidget()
        self._tabs.setFont(QFont("Segoe UI", 10))
        # Tüm sekmeler tek satıra sığsın: kaydırma oklarını kapat, kısaltma yapma
        self._tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self._tabs.setUsesScrollButtons(False)
        self._tabs.tabBar().setExpanding(False)

        self._dashboard      = DashboardView()
        self._mission_view   = MissionPlanView()
        self._mission_panel  = MissionPanelView()
        self._test_panel     = TestPanelView(self._db_conn)
        self._iha_test_panel = IHATestPanelView()     # YENİ: İHA Test Paneli
        self._saha_testi_view = SahaTestiView()       # YENİ: insan-rehberli bench testi
        self._calib_view     = CalibrationView()
        self._tuning_view    = TuningView()
        self._advanced_view  = AdvancedView()
        self._syslog_view    = SystemLogView(self._db_conn)
        self._flight_analysis_view = FlightAnalysisView(self._db_conn)
        self._settings_view  = SettingsView()
        self._autotune_view  = AutoTuneView()

        self._tabs.addTab(self._dashboard,      "Dashboard")
        self._tabs.addTab(self._mission_view,   "Görev Planlama")
        self._tabs.addTab(self._mission_panel,  "Özel Görevler")
        self._tabs.addTab(self._iha_test_panel, "🔧 Devreye Alma")   # YENİ sekme
        self._tabs.addTab(self._saha_testi_view, "🧪 Saha Testi")    # YENİ sekme
        self._tabs.addTab(self._test_panel,     "Test Paneli")
        self._tabs.addTab(self._calib_view,     "Kalibrasyon")
        self._tabs.addTab(self._tuning_view,    "PID Tuning")
        self._tabs.addTab(self._advanced_view,  "Gelişmiş")
        self._tabs.addTab(self._syslog_view,    "Sistem/Log")
        self._tabs.addTab(self._flight_analysis_view, "Kayıtlar")
        self._tabs.addTab(self._autotune_view,  "AutoTune")
        self._tabs.addTab(self._settings_view,  "Ayarlar")

        # GÜVENLİK: Kalibrasyon sekmesinden çıkılırken motor testi DURDURULUR.
        # CalibrationView.hideEvent zaten bunu yapıyor; bu bağlantı ikinci
        # emniyettir — gizleme olayının gelmediği bir durumda bile (ör. sekme
        # RBAC ile devre dışı bırakılıp zorla değiştirildiğinde) motorlar durur.
        self._tabs.currentChanged.connect(self._on_sekme_degisti)

        root.addWidget(self._tabs, stretch=1)

        # Durum çubuğu
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._status_label = QLabel("Hazır")
        self._statusbar.addWidget(self._status_label)
        self._rpi_status_label = QLabel("Yardımcı Bilgisayar: bağlı değil")
        self._apply_rpi_status_style("text_muted")
        self._statusbar.addPermanentWidget(self._rpi_status_label)

    # ── Sinyal Bağlamaları ───────────────────────────────────────────────────
    def _connect_signals(self) -> None:
        # ── MAVLink worker ↔ UI bağlantıları (TEK KAYNAK) ────────────────────
        # Hem burada hem worker yeniden yaratılınca (_on_disconnect_requested)
        # _wire_mavlink_worker() çağrılır → iki ayrı liste tutarsızlığı imkânsız.
        self._wire_mavlink_worker()

        # ── MAVLink dışı, kalıcı bağlantılar (yalnızca bir kez) ──────────────
        # Ayarlar
        self._settings_view.settings_changed.connect(self._on_settings_changed)

        # TopBar ↔ bağlan/kes
        self.top_bar.connect_requested.connect(self._on_connect_requested)
        self.top_bar.disconnect_requested.connect(self._on_disconnect_requested)

        if self.rtk_gps_worker is not None:
            self.rtk_gps_worker.gps_updated.connect(self._dashboard.rtk_status_updated)
            self.rtk_gps_worker.connected_changed.connect(self._dashboard.rtk_connection_changed)
            # Yer istasyonu F9P'sinin NMEA konumu — NTRIP (VRS mountpoint) GGA
            # bootstrap/tazeleme için kullanılır, bkz. _on_rtk_position_update.
            self.rtk_gps_worker.gps_updated.connect(self._on_rtk_position_update)
            # F9P baz istasyonu → RTCM3 → İHA (MAVLink GPS_RTCM_DATA). AYNI hedef
            # (inject_rtcm), ntrip_worker'ın UZAK kaynak için kullandığı yol —
            # burada kaynak YEREL F9P (tools/f9p_base_config.py ile survey-in'e
            # alınmış olmalı, bkz. o dosyanın NEDEN notu).
            # GEÇİCİ OLARAK DEVRE DIŞI (2026-08-28, kullanıcı talebi): TUSAGA-Aktif
            # NTRIP kaynağı denenirken F9P'nin RTCM'i AYNI ANDA gönderilmemeli —
            # ikisi birden aktifse İHA'nın GPS'i iki FARKLI baz konumundan (F9P'nin
            # kendi surveyed konumu vs. TUSAGA'nın gerçek CORS istasyon konumu)
            # çakışan düzeltmeler alır, RTK'yı düzeltmez kötüleştirir. F9P'nin NMEA
            # tabanlı konum izlemesi (NTRIP GGA için, yukarıdaki satır) ETKİLENMEDİ
            # — yalnızca RTCM aktarımı kesildi. NTRIP denemesi bitince BU SATIRI
            # GERİ AÇIN (yorumdan çıkarın).
            # self.rtk_gps_worker.rtcm_ready.connect(self.mavlink_worker.inject_rtcm)
        # Sesli otopilot → TTS (kuyruklu, üst üste binmez)
        self._dashboard.speak_requested.connect(self.tts_worker.speak)

        # Dashboard → AI worker (servo) — mavlink değil
        self._dashboard.send_servo_action.connect(self.ai_worker.send_servo_command)

        # Görev oku/yaz ilerleme kutusu (UI→UI, worker'dan bağımsız)
        self._mission_view.read_mission.connect(self._on_read_mission)

        # RPi Görev (companion)
        self._mission_panel.mission_ready_to_send.connect(self.ai_worker.send_mission_with_waypoints)
        # Görev panelindeki hedef tespit bildirimi ÖNCEDEN emit ediliyor ama hiçbir
        # yere bağlı değildi (sinyal boşa gidiyordu). Dashboard'ın tespit kartlarına
        # yönlendiriliyor — İHA'dan gelen tespitle aynı yolu kullanır.
        self._mission_panel.target_detected.connect(self._on_ai_detection)

        # TopBar — görev başlatma companion'a (ai_worker)
        self.top_bar.start_mission_requested.connect(self.ai_worker.send_start_command)
        # ── GÖREV KRONOMETRESİ (2026-08-30 şartname denetimi) ────────────
        # NEDEN: şartname uçuş süresini sınırlar (Görev 1: 5 dk, Görev 2:
        # 10 dk) ve sınırı aşan uçuş BAŞARISIZ sayılır. Saat, operatörün
        # başlat ONAYI anında telemetriden bağımsız başlar — her iki
        # başlatma yolu (companion + FCU) da aynı saati sürer. Abort saati
        # durdurur; companion görev durumu kenar geçişleri _on_rpi_status
        # içinde ayrıca bağlıdır (görev kendiliğinden bitince de durur).
        self.top_bar.start_mission_requested.connect(
            self._mission_panel.notify_mission_started)
        self.top_bar.fcu_start_mission_requested.connect(
            self._mission_panel.notify_mission_started)
        self.top_bar.abort_mission_requested.connect(
            self._mission_panel.notify_mission_stopped)
        # E3/E4: görevi durdur (companion abort + doğrudan RTL) ve manuel bırakma
        # — handler'a yönlendirilir ki worker yeniden yaratılsa da güncel olanı kullansın
        self.top_bar.abort_mission_requested.connect(self._on_abort_mission)
        self.top_bar.drop_now_requested.connect(self._on_drop_now)

        # AI Worker ↔ UI
        self.ai_worker.ai_detection_alert.connect(self._on_ai_detection)
        self.ai_worker.mission_accepted.connect(lambda: self.top_bar.set_mission_ready(True))
        self.ai_worker.error_occurred.connect(self._on_error)
        self.ai_worker.mission_rejected.connect(self._on_mission_rejected)

        # NTRIP (worker başlatılınca _on_start_ntrip'te de bağlanır)
        self._advanced_view.start_ntrip.connect(self._on_start_ntrip)
        self._advanced_view.stop_ntrip.connect(self._on_stop_ntrip)

        # Video
        self.video_worker.frame_ready.connect(self._dashboard.update_video)
        self.video_worker.error_occurred.connect(self._on_error)
        self._dashboard.video_source_changed.connect(self._on_video_source_changed)

        # RPi Companion
        self.top_bar.rpi_connect_requested.connect(self._on_rpi_connect_requested)
        self.top_bar.rpi_disconnect_requested.connect(self._on_rpi_disconnect_requested)
        self.top_bar.rpi_battery_config.connect(self.ai_worker.send_battery_config)
        self.rpi_worker.rpi_connected.connect(self.top_bar.set_rpi_connection_status)
        self.rpi_worker.rpi_connected.connect(self._on_rpi_connection_changed)
        self.rpi_worker.rpi_status_updated.connect(self._on_rpi_status)
        self.rpi_worker.health_alert.connect(self._on_health_alert)
        self.rpi_worker.mission_notification.connect(self._on_mission_notification)
        self._iha_test_panel.run_test_requested.connect(self.ai_worker.send_run_test)
        # "⏹ Durdur" butonu ÖNCEDEN hiçbir yere bağlı değildi: operatör çalışan
        # bir test dizisini (gerçek servo/motor hareketi içerir) durduramıyordu.
        # İHA tarafına abort_test komutu eklendi; buraya bağlanıyor.
        self._iha_test_panel.abort_test_requested.connect(self.ai_worker.send_abort_test)
        self.rpi_worker.rpi_status_updated.connect(self._iha_test_panel.update_test_status)
        self.rpi_worker.mission_notification.connect(self._iha_test_panel.append_log)

        # Auth
        self._auth.logout_required.connect(self._on_logout_required)
        # Rol oturum içinde değişirse (JWT yenileme, çıkış) sekme kilitleri ANINDA
        # tazelensin — yoklamayı beklemeden. Yoklama emniyet ağı olarak kalır.
        self._auth.role_changed.connect(lambda _r: self.refresh_rbac())

        # Log indirme (UI→handler; request_log_list→worker _wire'da)
        self._syslog_view.download_log.connect(self._on_download_log)

    def _wire_mavlink_worker(self) -> None:
        """TÜM MAVLink worker ↔ UI bağlantıları TEK YERDE.

        Hem ilk kurulumda (_connect_signals) hem worker yeniden yaratıldığında
        (_on_disconnect_requested) çağrılır. Böylece iki ayrı bağlantı listesi
        arasındaki tutarsızlıktan doğan hatalar (çift gönderim, ölü oku/yaz butonu,
        bağlanmayan progress vb.) kökten imkânsız hale gelir.

        NOT: Yalnızca BİR ucu self.mavlink_worker olan bağlantılar buraya girer.
        UI→UI veya UI→ai_worker gibi worker'dan bağımsız bağlantılar _connect_signals'ta.
        """
        w = self.mavlink_worker
        w.set_db_conn(self._db_conn)

        # worker → UI
        # Telemetri TEK dispatcher'a gider → sürekli tüketiciler + YALNIZCA görünür
        # sekme güncellenir (gizli sekmelerin haritası/HUD'u boşa çalışmaz).
        w.telemetry_updated.connect(self._dispatch_telemetry)
        w.connection_changed.connect(self.top_bar.set_connection_status)
        w.connection_changed.connect(self._on_connection_changed)
        w.param_received.connect(self._advanced_view.update_param)
        w.param_received.connect(self._tuning_view.update_param)
        w.log_entry_received.connect(self._syslog_view.on_log_entry)
        w.error_occurred.connect(self._on_error)
        w.mag_cal_progress.connect(self._calib_view.on_mag_cal_progress)
        w.mag_cal_report.connect(self._calib_view.on_mag_cal_report)
        # Kalibrasyon sekmesinin YENİ alt panelleri (Uçuş Modları / Uçuş Öncesi
        # Kontrol / Motor Test) FC'deki GERÇEK parametre değerlerini görmek
        # zorunda: uçuş modu slotlarını okumak, fc_params_expected'i doğrulamak
        # ve yazma sonrası GERİ OKUMAYLA teyit etmek için. PARAM_SET'in MAVLink
        # ACK'i yoktur; tek doğrulama kanalı budur.
        w.param_received.connect(self._calib_view.on_param_received)
        # Bağlantı durumu: "bağlantı yoksa buton KAPALI ve nedeni YAZILI"
        # kuralı için alt panellerin link durumunu bilmesi gerekir. Ayrıca
        # link koparsa süren motor testinin durumu artık DOĞRULANAMAZ.
        w.connection_changed.connect(self._calib_view.on_connection_changed)
        w.status_text_received.connect(self._dashboard.add_status_message)
        w.status_text_received.connect(self._syslog_view.on_status_text)
        # İvmeölçer kalibrasyonu FC'nin STATUSTEXT yanıtına ("Place vehicle…",
        # "Calibration successful/FAILED") göre ilerler. Bu bağlantı olmadan panel
        # adımları körlemesine sayar ve doğrulanmamış "tamamlandı" gösterirdi.
        w.status_text_received.connect(self._calib_view.on_status_text)
        # NEDEN (2026-09-03): FC'nin komut hükmü (COMMAND_ACK) bugüne kadar
        # hiç okunmuyordu. Ölçüldü: ivmeölçer kalibrasyonunda altı konum da
        # gönderildi, FC tek STATUSTEXT döndürmedi; komutun reddedilip
        # edilmediğini görmek İMKÂNSIZDI. Bu bağlantı olmadan panel yine kör.
        w.command_ack_received.connect(self._calib_view.on_command_ack)
        # AutoTune paneli: eksen maskesi/agresiflik FC'ye YAZILIR ve FC'nin
        # gerçek değerleri geri okunur; AUTOTUNE ilerlemesi STATUSTEXT'ten gelir.
        w.connection_changed.connect(self._autotune_view.on_connection_changed)
        w.status_text_received.connect(self._autotune_view.on_status_text)
        w.param_received.connect(self._autotune_view.on_param_received)
        self._autotune_view.send_command.connect(w.send_command)
        self._autotune_view.set_param.connect(w.set_param)
        self._autotune_view.request_params.connect(w.request_parameters)
        w.mission_download_done.connect(self._on_mission_download_done)
        w.mission_download_progress.connect(self._on_mission_download_progress)
        w.mission_upload_progress.connect(self._on_mission_upload_progress)
        w.mission_upload_done.connect(self._on_mission_upload_done)
        w.flight_sequence_status.connect(self._status_label.setText)
        w.log_download_done.connect(self._on_log_download_done)
        w.log_download_progress.connect(self._syslog_view.on_download_progress)

        # UI → worker
        self._calib_view.send_command.connect(w.send_command)
        # Radyo kalibrasyonu yakalanan uçları RC{n}_MIN/MAX olarak YAZAR — bu
        # bağlantı olmadan "Kaydet" hiçbir şey kaydetmez (panel bunu algılayıp
        # kırmızı "KAYDEDİLMEDİ" gösterir, ama asıl çözüm bağlantının olması).
        self._calib_view.set_param.connect(w.set_param)
        # Kalibrasyon alt panellerinin "FC'den OKU" düğmesi. mavlink_worker'da
        # TEK parametre okuyan bir slot yok (yalnız tam liste) — bu yüzden
        # istek de tam liste isteğidir; paneller bunu kullanıcıya yazıyor.
        self._calib_view.request_params.connect(w.request_parameters)
        self._tuning_view.send_command.connect(w.send_command)
        self._tuning_view.set_param.connect(w.set_param)
        self._advanced_view.set_param.connect(w.set_param)
        self._advanced_view.request_params.connect(w.request_parameters)
        self._dashboard.send_command.connect(w.send_command)
        self._dashboard.ned_velocity_cmd.connect(w.send_ned_velocity)
        self._mission_view.write_mission.connect(w.upload_mission)
        self._mission_view.read_mission.connect(w.request_mission)
        self._mission_panel.send_command.connect(w.send_command)
        # NEDEN: HOME_POSITION FC'nin OTORİTE kalkış noktasıdır (ArduPilot
        # GPS kilidi/arm anında kendi belirler). Bugüne kadar mavlink_worker
        # bunu yayınlıyordu ama arayüzde KİMSE dinlemiyordu; görev paneli
        # bunun yerine gördüğü İLK telemetri konumuna kilitleniyordu — ve
        # telemetri yalnız AKTİF sekmeye gittiği için o an 'aracın kalkış
        # anı' değil 'operatörün sekmeyi açtığı an' oluyordu.
        w.home_position_updated.connect(self._mission_panel.on_home_position)
        self._test_panel.send_command.connect(w.send_command)
        self.top_bar.fcu_start_mission_requested.connect(w.start_flight_sequence)
        self._syslog_view.request_log_list.connect(w.request_log_list)

        # NTRIP → worker (varsa)
        if self.ntrip_worker is not None:
            self.ntrip_worker.rtcm_ready.connect(w.inject_rtcm)

    @pyqtSlot(dict)
    def _dispatch_telemetry(self, data: dict) -> None:
        """Telemetriyi dağıt: SÜREKLİ tüketiciler her zaman + YALNIZCA görünür sekme.

        Performans: 3 web-harita + HUD repaint'leri eskiden gizli sekmelerde de
        20Hz çalışıyordu. Artık yalnızca aktif sekmenin update_telemetry'si çağrılır.
        """
        # Sürekli gerekli olanlar (gizli sekmede de): durum çubuğu, kayıt
        self.top_bar.update_telemetry(data)
        self.telemetry_recorder.on_telemetry(data)
        self._syslog_view.update_telemetry_for_analysis(data)  # kendi içinde gate'li

        # İHA'nın kendi GPS konumu — NTRIP VRS GGA için TERCİH EDİLEN kaynak
        # (2026-08-28, sahada bulundu): F9P yer istasyonunda takılı OLMAYABİLİR
        # (o an yoktu) ama VRS mantığı zaten düzeltmenin uygulandığı ROVER'a
        # (İHA) yakın bir sanal referans hesaplar — F9P'nin sabit yer istasyonu
        # konumundan daha DOĞRU kaynak budur, ayrıca İHA hareket ettikçe otomatik
        # güncellenir. F9P varsa o da AYNI mekanizmayı besler (_on_rtk_position_update)
        # — hangisi son güncellenirse o kullanılır, ikisi de zararsız.
        lat, lon = data.get("lat"), data.get("lon")
        if lat is not None and lon is not None and not (abs(lat) < 1e-6 and abs(lon) < 1e-6):
            self._last_gps_fix = (lat, lon, data.get("alt") or 0.0)
            if self.ntrip_worker is not None:
                self.ntrip_worker.set_position(lat, lon, data.get("alt") or 0.0)

        # Sesli otopilot / görev anlatıcısı — gizli sekmede de çalışmalı:
        # operatör başka sekmedeyken de batarya/EKF uyarısını duymalı.
        try:
            for olay in self._narrator.update(data):
                d = olay.__dict__.copy()
                d["faz"] = getattr(self._narrator, "_faz", "")
                self._dashboard.add_narration(d)
        except Exception:
            logger.exception("Görev anlatıcısı hatası")

        # Yalnızca AKTİF sekme (pahalı: harita JS / HUD repaint)
        cur = self._tabs.currentWidget()
        fn = getattr(cur, "update_telemetry", None)
        if callable(fn):
            fn(data)

    @pyqtSlot(int)
    def _on_sekme_degisti(self, _index: int) -> None:
        """Kalibrasyon sekmesi terk edilince motor testini durdurur.

        NEDEN: Motor testi paneli motorları GERÇEKTEN döndürür. Operatör başka
        bir sekmeye geçtiğinde paneli göremez; dönen bir motoru fark edemez ve
        ACİL DUR düğmesine ulaşamaz. Şartname gereği panelden çıkışta motorlar
        durdurulmak zorunda.
        """
        if self._tabs.currentWidget() is self._calib_view:
            return
        fn = getattr(self._calib_view, "acil_motor_durdur", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                logger.exception("Sekme değişiminde motor durdurma hatası")

    # ── RBAC Erişim Kontrolü ─────────────────────────────────────────────────
    def _apply_rbac(self) -> None:
        """Rol tabanlı sekme + uçuş komutu kilitleri (C3 — KRİTİK).

        NEDEN: Eski kod SABİT sekme indeksleri kullanıyordu (1..8) ama gerçek
        sekme sırası 12 sekmeye çıkmıştı. İndeksler kaymış olduğu için:
          • İndeks 6 "Sys/Log: OPERATOR+" yorumuyla PID Tuning'i açıyordu,
          • Gerçek Sistem/Log (indeks 8 — içinde KULLANICI YÖNETİMİ var) ve
            Gelişmiş (indeks 7 — tam parametre yazma) hiç kısıtlanmıyor,
          • Kayıtlar / Anten Takip / Ayarlar (9/10/11) hiç kısıtlanmıyor,
          • Dashboard (0 — ARM/TAKEOFF/RTL/yük bırakma) hiç kısıtlanmıyordu.
        Sonuç: doğrudan yetki yükseltme. Artık indeks yerine WIDGET REFERANSI
        üzerinden (self._tabs.indexOf(...)) kilitlenir; sekme sırası değişse
        bile eşleme bozulmaz.
        """
        role = state.get_rbac_role()

        # Widget → gereken minimum rol (1=VIEWER, 2=OPERATOR, 3=ADMIN)
        tab_roles = [
            (self._dashboard,             1),   # Dashboard: görünür kalır (komutlar ayrıca kilitli)
            (self._mission_view,          2),   # Görev Planlama: OPERATOR+
            (self._mission_panel,         2),   # Özel Görevler: OPERATOR+
            (self._iha_test_panel,        2),   # Devreye Alma: OPERATOR+
            (self._saha_testi_view,       2),   # Saha Testi (ARM/motor içerir): OPERATOR+
            (self._test_panel,            2),   # Test Paneli: OPERATOR+
            (self._calib_view,            3),   # Kalibrasyon: ADMIN
            (self._tuning_view,           3),   # PID Tuning: ADMIN
            (self._advanced_view,         3),   # Gelişmiş (tam param yazma): ADMIN
            (self._syslog_view,           3),   # Sistem/Log (Kullanıcı Yönetimi): ADMIN
            (self._flight_analysis_view,  1),   # Kayıtlar: VIEWER+
            (self._settings_view,         3),   # Ayarlar: ADMIN
        ]
        for widget, min_role in tab_roles:
            idx = self._tabs.indexOf(widget)
            if idx >= 0:
                self._tabs.setTabEnabled(idx, role >= min_role)

        # Kilitli bir sekme aktifse Dashboard'a düş (Qt devre dışı sekmeyi
        # kendiliğinden terk etmez — içerik görünür kalabilirdi).
        cur = self._tabs.currentIndex()
        if cur >= 0 and not self._tabs.isTabEnabled(cur):
            dash_idx = self._tabs.indexOf(self._dashboard)
            if dash_idx >= 0:
                self._tabs.setCurrentIndex(dash_idx)

        self._apply_dashboard_rbac(role)

    def _apply_dashboard_rbac(self, role: int) -> None:
        """Dashboard uçuş komutlarını rol ile kilitler (C3).

        NEDEN: Dashboard sekmesi herkese görünür olmalı (durum izleme), ancak
        VIEWER (rol 1) ARM/DISARM/TAKEOFF/LAND/RTL/mod değiştirme/GUIDED hız
        komutu/yük bırakma YAPAMAMALIDIR. dashboard_view.py'de bu kontrolleri
        kapatan public bir metot YOK ve butonlar yerel değişken (self.* değil),
        bu yüzden dosyayı DEĞİŞTİRMEDEN kapsayıcı widget'lar üzerinden
        (QGroupBox "Hızlı Komutlar", QFrame#servoPanel, GuidedControlWidget)
        devre dışı bırakılır. Kapsayıcıyı kapatmak içindeki tüm butonları da
        kapatır ve sekme sırası/buton eklenmesinden etkilenmez.
        """
        from PyQt6.QtWidgets import QGroupBox
        can_command = role >= 2   # OPERATOR ve üstü
        try:
            # 1) "Hızlı Komutlar" grubu → ARM/DISARM/TAKEOFF/LAND/RTL/mod butonları
            for grp in self._dashboard.findChildren(QGroupBox):
                if grp.title().strip() == "Hızlı Komutlar":
                    grp.setEnabled(can_command)
                    if not can_command:
                        grp.setToolTip("Uçuş komutları için OPERATOR yetkisi gerekir")

            # 2) Yük bırakma / grabber / gimbal servo paneli (QFrame#servoPanel)
            for w in self._dashboard.findChildren(QWidget):
                if w.objectName() == "servoPanel":
                    w.setEnabled(can_command)
                    if not can_command:
                        w.setToolTip("Yük bırakma için OPERATOR yetkisi gerekir")

            # 3) GUIDED hız/yaw kumandası (GuidedControlWidget — doğrudan
            #    ned_velocity_cmd üretir, uçuş komutudur)
            gc = getattr(self._dashboard, "_guided_ctrl", None)
            if gc is not None:
                gc.setEnabled(can_command)
                if not can_command:
                    gc.setToolTip("Manuel yönlendirme için OPERATOR yetkisi gerekir")
        except Exception as exc:   # UI kilidi uygulanamazsa sessizce geçme — logla
            logger.error("Dashboard RBAC kilidi uygulanamadı: %s", exc)

        # TopBar'daki görev başlat / durdur / manuel bırakma da uçuş komutudur.
        # NOT: Yalnızca KAPATIRIZ, asla açmayız — bu butonların "hazır" durumu
        # (set_mission_ready) ayrı bir mantıkla yönetilir; burada True yapmak
        # görev yüklenmeden Başlat'ı açardı.
        if not can_command:
            for attr in ("_btn_start", "_btn_start_fcu", "_btn_abort", "_btn_drop"):
                btn = getattr(self.top_bar, attr, None)
                if btn is not None:
                    btn.setEnabled(False)
            logger.info("RBAC: rol=%s — Dashboard uçuş komutları kilitlendi (salt görüntüleme)", role)

    def _require_command_role(self, islem: str) -> bool:
        """Uçuş komutu için OPERATOR (rol >= 2) zorunlu (C3).

        NEDEN: UI kilidi tek savunma hattı olmamalıdır — komutu gönderen
        slot'ta da rol doğrulanır (defense in depth).
        """
        role = state.get_rbac_role()
        if role < 2:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Yetki Yok",
                f"{islem} için OPERATOR yetkisi gerekir.\n\nMevcut rol seviyeniz: {role}",
            )
            logger.warning("Yetkisiz uçuş komutu denemesi: %s (rol=%s)", islem, role)
            return False
        return True

    def refresh_rbac(self) -> None:
        """Rol değiştiğinde (giriş/çıkış/JWT yenileme) dışarıdan çağrılabilir.

        NEDEN: _apply_rbac yalnızca __init__ içinde bir kez çağrılıyordu; rol
        çalışma anında değişirse kilitler güncellenmiyordu.
        """
        self._apply_rbac()
        self._last_rbac_role = state.get_rbac_role()

    def _check_rbac_change(self) -> None:
        """Rol çalışma anında değiştiyse RBAC kilitlerini tazele.

        Birincil yol artık SİNYAL: auth_manager.role_changed → refresh_rbac
        (bkz. _connect_signals). Bu yoklama yalnızca EMNİYET AĞI olarak duruyor —
        rolü auth_manager dışından değiştiren bir kod yolu eklenirse (doğrudan
        state.set_rbac_role çağrısı) sinyal yayılmaz ve kilitler eskir.
        Maliyeti iki tam sayı karşılaştırması; zaten çalışan watchdog tick'inde.
        """
        role = state.get_rbac_role()
        if role == self._last_rbac_role:
            return
        logger.info("RBAC rolü değişti: %s → %s — erişim kilitleri yenileniyor",
                    self._last_rbac_role, role)
        self._last_rbac_role = role
        self._apply_rbac()
        # Rol düştüyse (ör. çıkış) acil kontroller de kapanmalı; yükseldiyse bir
        # sonraki companion durum paketi (_on_rpi_status) doğru durumu geri yazar.
        if role < 2:
            try:
                self.top_bar.set_mission_active(False)
            except Exception:
                pass

    # ── Slot'lar ──────────────────────────────────────────────────────────────
    @pyqtSlot(str, str, int, str)
    def _on_connect_requested(self, conn_type: str, addr: str, baud: int, data_type: str) -> None:
        if conn_type == "serial":
            conn_str = addr
        elif conn_type == "tcp":
            conn_str = f"tcp:{addr}" if ":" in addr else f"tcp:{addr}:5762"
        else:
            conn_str = f"udp:{addr}" if ":" in addr else f"udp:0.0.0.0:{addr}"

        if data_type == "gimbal":
            # NEDEN: Kod tabanında `gimbal_worker` diye bir nesne YOK — eski kod
            # getattr ile onu arıyor, bulamayınca yalnızca DEBUG/WARNING loglayıp
            # geçiyordu; ama durum çubuğuna "Gimbal: {adres}" yazdığı için işlem
            # BAŞARILI görünüyordu. Operatör gimbal'ın bağlandığını sanıyordu.
            # Artık: bağlantı kurulmadığı açıkça söylenir, durum çubuğuna başarı
            # yazılmaz. (Seçenek top_bar'da bırakıldı; tooltip'te de uyarılıyor.)
            logger.warning(
                "Gimbal bağlantısı istendi ancak bu sürümde desteklenmiyor "
                "(gimbal worker yok): %s @ %d", conn_str, baud
            )
            self._status_label.setText("Gimbal bağlantısı bu sürümde DESTEKLENMİYOR")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Gimbal Desteklenmiyor",
                "Gimbal bağlantısı bu sürümde desteklenmiyor — hiçbir bağlantı "
                "kurulmadı.\n\n"
                f"İstenen port: {conn_str} @ {baud}\n\n"
                "Drone telemetrisi için veri tipini 'Drone' seçip tekrar deneyin.",
            )
        else:
            self.mavlink_worker.set_connection(conn_str, baud)
            logger.info("Drone bağlantısı: %s  baud=%d", conn_str, baud)
            self._status_label.setText(f"Bağlanıyor: {conn_str} @ {baud}")

    @pyqtSlot()
    def _on_disconnect_requested(self) -> None:
        # Eski worker sinyallerini kopar — kapanırken UI'ye yanlış sinyal gitmesin
        old_worker = self.mavlink_worker
        try:
            old_worker.connection_changed.disconnect(self.top_bar.set_connection_status)
            old_worker.connection_changed.disconnect(self._on_connection_changed)
        except Exception:
            pass

        # Worker'ı durdur (socket zaten kapatılır, maks 5s bekler)
        old_worker.stop()
        old_worker.deleteLater()  # Qt parent-child zincirinden temizle

        # Yeni boş worker — bağlantı dizesi yok, sadece bekler
        self.mavlink_worker = MavlinkWorker(self)
        self._wire_mavlink_worker()   # TÜM worker↔UI bağlantıları (tek kaynak)
        self.mavlink_worker.start()

        # UI'yi hemen güncelle — yeni worker sinyal atmayacak
        self.top_bar.set_connection_status(False)
        state.set_connection_status(False)
        self._status_label.setText("Bağlantı kesildi")

    @pyqtSlot(bool, str)
    def _on_mission_upload_done(self, success: bool, message: str) -> None:
        self._close_mission_progress()
        self.top_bar.set_mission_ready(success)
        self._status_label.setText(f"Görev yükleme: {message}")
        if success:
            self.tts_worker.speak("Görev yüklendi")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Görev Yüklendi", message)
        else:
            self.tts_worker.speak("Görev yüklenemedi")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Görev Yükleme Hatası", message)

    @pyqtSlot(bool, list, str)
    def _on_mission_download_done(self, success: bool, waypoints: list, message: str) -> None:
        self._close_mission_progress()
        self._status_label.setText(f"Görev indirme: {message}")
        if success:
            self._mission_view.load_from_drone(waypoints)
            self.tts_worker.speak("Görev indirildi")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Görev Okundu", message)
        else:
            self.tts_worker.speak("Görev indirilemedi")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Görev Okuma Hatası", message)

    @pyqtSlot()
    def _on_abort_mission(self) -> None:
        """E3: Görevi durdur — companion abort + doğrudan FC RTL.
        İki başlatma yolunu da (companion / FCU-direct) kapsar."""
        # C3: Buton kilidi tek başına yeterli değil — top_bar butonların
        # etkinliğini kendi mantığıyla da değiştirebiliyor. Yetki, komutun
        # gönderildiği yerde de doğrulanır.
        if not self._require_command_role("Görev durdurma / RTL"):
            return
        logger.warning("GÖREV DURDUR/RTL — companion abort + doğrudan RTL")
        try:
            self.ai_worker.send_abort()          # companion görevini iptal et
        except Exception as exc:
            logger.error("Companion abort gönderilemedi: %s", exc)
        try:
            # MAV_CMD_DO_SET_MODE=176, base=1(CUSTOM_MODE_ENABLED), custom=6(ArduCopter RTL)
            self.mavlink_worker.send_command(176, [1, 6, 0, 0, 0, 0, 0])
        except Exception as exc:
            logger.error("Doğrudan RTL gönderilemedi: %s", exc)
        self._status_label.setText("GÖREV DURDURULDU — RTL gönderildi")
        try:
            self.tts_worker.speak("Görev durduruldu, eve dönülüyor")
        except Exception:
            pass

    @pyqtSlot()
    def _on_drop_now(self) -> None:
        """E4: Manuel yük bırakma komutu (companion'a)."""
        # C3: VIEWER (rol 1) yük bırakamaz — komut noktasında doğrulanır.
        if not self._require_command_role("Manuel yük bırakma"):
            return
        logger.info("Manuel bırakma (drop_now) gönderiliyor")
        try:
            self.ai_worker.send_drop_now()
        except Exception as exc:
            logger.error("drop_now gönderilemedi: %s", exc)
        self._status_label.setText("Manuel bırakma komutu gönderildi")

    @pyqtSlot()
    def _on_read_mission(self) -> None:
        """Drone Oku tıklandı — ilerleme kutusunu hemen aç (busy)."""
        logger.info("Drone'dan görev okunuyor...")
        self._status_label.setText("Drone'dan görev okunuyor...")
        self._show_mission_progress("Görev İndiriliyor", "Drone'dan görev okunuyor...")

    # ── Görev İlerleme Kutusu (oku/yaz) ──────────────────────────────────────
    def _show_mission_progress(self, title: str, label: str) -> None:
        from PyQt6.QtWidgets import QProgressDialog
        from PyQt6.QtCore import Qt
        old = getattr(self, "_mission_progress_dlg", None)
        if old is not None:
            old.close()
        dlg = QProgressDialog(label, "", 0, 0, self)   # 0,0 = belirsiz (başlangıç)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setCancelButton(None)        # indirme/yükleme kesilemez — iptal yok
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        dlg.show()
        self._mission_progress_dlg = dlg

    def _update_mission_progress(self, current: int, total: int, what: str) -> None:
        dlg = getattr(self, "_mission_progress_dlg", None)
        if dlg is None:
            self._show_mission_progress("Görev", f"{what}...")
            dlg = self._mission_progress_dlg
        if total > 0:
            if dlg.maximum() != total:
                dlg.setMaximum(total)
            pct = int(current / total * 100)
            dlg.setLabelText(f"{what}: {current}/{total}  (%{pct})")
            dlg.setValue(current)

    def _close_mission_progress(self) -> None:
        dlg = getattr(self, "_mission_progress_dlg", None)
        if dlg is not None:
            dlg.close()
            self._mission_progress_dlg = None

    @pyqtSlot(int, int)
    def _on_mission_download_progress(self, current: int, total: int) -> None:
        self._update_mission_progress(current, total, "Görev indiriliyor")

    @pyqtSlot(int, int)
    def _on_mission_upload_progress(self, current: int, total: int) -> None:
        self._update_mission_progress(current, total, "Görev yükleniyor")

    @pyqtSlot(bool)
    def _on_connection_changed(self, connected: bool) -> None:
        state.set_connection_status(connected)
        msg = "Drone bağlandı" if connected else "Bağlantı kesildi"
        self._status_label.setText(msg)
        local_storage.write_audit(self._db_conn, "INFO", msg)
        if connected:
            self.tts_worker.speak("Bağlantı kuruldu")
        else:
            self.tts_worker.speak("Bağlantı kesildi")

    # Kritik komut hatası sayılan anahtar kelimeler (M10). Bunlar görev iptali /
    # yük bırakma gibi geri alınamaz komutlarla ilgilidir; sessizce kaybolmamalı.
    # NEDEN kelime sınırı (\b): düz `in` araması "alarm"ı ARM, "dropped frame"i
    # yük bırakma sanıp video worker'ının rutin hatalarında modal açardı.
    _CRITICAL_ERROR_PATTERN = re.compile(
        r"\b(abort|drop_now|rtl|arm|disarm|failsafe)\b|bırak|görev durdur",
        re.IGNORECASE,
    )

    @pyqtSlot(str)
    def _on_error(self, msg: str) -> None:
        """Worker hatası (M10 — ORTA).

        NEDEN: Eskiden hata YALNIZCA 80 karaktere kırpılıp durum çubuğuna
        yazılıyordu ve bir sonraki mesaj (telemetri/görev bildirimi) üzerine
        yazarak siliyordu → operatör hatayı hiç görmeden kaybediyordu. Artık:
          1) Tam metin Sistem/Log'a kalıcı yazılır (root logger → SystemLogView
             "YKİ Sistem Logları" kutusu) ve audit_log tablosuna işlenir,
          2) Kritik komut hataları ayrıca modal QMessageBox.critical gösterir.
        """
        logger.error("Worker hatası: %s", msg)
        self._status_label.setText(f"HATA: {msg[:80]}")

        # 1) Kalıcı kayıt — audit_log (Sistem/Log ekranındaki canlı log root
        #    logger üzerinden yukarıdaki logger.error ile zaten besleniyor).
        try:
            local_storage.write_audit(self._db_conn, "ERROR", msg)
        except Exception as exc:
            logger.debug("Hata audit_log'a yazılamadı: %s", exc)

        # 2) Kritik komut hataları → gözden kaçmayacak modal uyarı.
        #    Aynı mesaj arka arkaya gelirse (retry döngüsü) tek kutu gösterilir;
        #    aksi halde modal yığını UI'yi kilitler.
        if self._CRITICAL_ERROR_PATTERN.search(msg):
            if msg == getattr(self, "_last_critical_error", None):
                return
            self._last_critical_error = msg
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self, "KRİTİK KOMUT HATASI",
                "Uçuş güvenliğini ilgilendiren bir komut BAŞARISIZ oldu:\n\n"
                f"{msg}\n\n"
                "Komutun araca ulaştığını doğrulayın; gerekirse RC ile manuel "
                "müdahale edin.",
            )

    @pyqtSlot(dict)
    def _on_settings_changed(self, new_settings: dict) -> None:
        """Ayarlar kaydedilince companion adreslerini YERİNDE güncelle.

        ÖNEMLİ: ai_worker'ı artık YENİDEN YARATMIYORUZ. Eski kod yeniden yaratıyor
        ama bazı sinyalleri (send_servo_action, run_test_requested) yeni worker'a
        BAĞLAMIYORDU → servo/gimbal/grabber komutları durdurulmuş eski worker'a
        gidip kuyruğunu dolduruyor, "kuyruk dolu" hatası veriyordu ve hiç
        gönderilmiyordu. _send_mission her gönderimde self._host/self._port
        okuduğu için yerinde güncelleme yeterli, güvenli ve tüm sinyaller korunur.
        """
        from core.gcs_config import config as cfg
        host = cfg.companion_host
        # Komut worker'ını (5601) yerinde yeni adrese yönlendir
        self.ai_worker._host            = host
        self.ai_worker._port            = cfg.companion_mission_port
        self.ai_worker._connect_timeout = cfg.ai_connect_timeout
        self.ai_worker._response_timeout = cfg.ai_response_timeout
        self.ai_worker._auth_token      = cfg.companion_auth_token
        # Durum worker'ını (5602) da yeni adrese yönlendir (bağlıysa) — auth_token
        # da reconnect'te yeni bağlantıya gönderilsin diye burada güncellenir.
        self.rpi_worker._auth_token = cfg.companion_auth_token
        try:
            self.rpi_worker.reconnect(host, cfg.companion_status_port)
        except Exception:
            pass
        # Video kaynak URL'lerini yeni host'a göre tazele
        self._video_url_raw, self._video_url_processed = _video_urlleri(cfg, host)
        logger.info("Ayarlar değişti — companion adresleri güncellendi: komut %s:%d, "
                    "durum %s:%d", host, cfg.companion_mission_port,
                    host, cfg.companion_status_port)

        # ── 4b. Video URL'lerini yeni host'a güncelle ─────────────────────
        self._video_url_raw, self._video_url_processed = _video_urlleri(cfg, host)
        # Aktif video kaynağı eski host'u gösteriyorsa yeni host'a taşı
        # (override durumunu değiştirme — sadece adresi güncelle)
        if self._current_video_source not in ("none", ""):
            # Hangi akıştaydık? HTTP'de port, UDP'de adres sonundaki /1 ayırıyor.
            # NEDEN böyle: eski kod yalnız ":8081/video" sonekine bakıyordu ve
            # UDP adreslerinde (udp://konak:5600/1) HER ZAMAN "ham"a düşerdi —
            # host değişince operatör sessizce işlenmiş akışı kaybederdi.
            _onceki = self._current_video_source
            _islenmisti = (_onceki.endswith(f":{cfg.companion_video_port}/video")
                           or _onceki.endswith("/1"))
            new_src = self._video_url_processed if _islenmisti else self._video_url_raw
            self._apply_video_source(new_src)

        # ── 5. RPi durum worker'ını güncelle ─────────────────────────────
        self.rpi_worker.reconnect(host, cfg.companion_status_port)

        # ── 6. TopBar RPi adres listesini güncelle ────────────────────────
        status_addr = cfg.companion_status_address
        combo = self.top_bar._rpi_addr
        if combo.findText(status_addr) == -1:
            combo.insertItem(0, status_addr)
        combo.setCurrentText(status_addr)

        self._status_label.setText(f"Ayarlar uygulandı: Görev Bilgisayarı={host}")

    @pyqtSlot(dict)
    def _on_ai_detection(self, data: dict) -> None:
        # Görev yükleme onayı: {"status":"ok", "mission_type":"..."}
        # → mission_accepted sinyali zaten butonu aktifleştiriyor, burada sadece logla
        if data.get("status") == "ok" and data.get("mission_type"):
            logger.info("Companion görev yükledi: %s", data["mission_type"])
            return
        # Gerçek hedef tespit alarmı — label veya detected_targets içeriyorsa
        if data.get("label") or data.get("detected_targets"):
            label = data.get("label", "hedef")
            self.tts_worker.speak(f"Hedef tespit edildi: {label}")
            self._mission_panel.update_detection_status(data)
            self._dashboard.add_ai_detection(data)

    @pyqtSlot(dict)
    def _on_rtk_position_update(self, gps: dict) -> None:
        """F9P'nin NMEA konumu güncellendi — NTRIP VRS mountpoint'inin GGA
        bootstrap/tazelemesi için sakla ve (NTRIP aktifse) canlı ilet."""
        lat, lon, alt = gps.get("lat"), gps.get("lon"), gps.get("alt_m")
        if lat is None or lon is None:
            return
        if abs(lat) < 1e-6 and abs(lon) < 1e-6:
            return
        self._last_gps_fix = (lat, lon, alt or 0.0)
        if self.ntrip_worker is not None:
            self.ntrip_worker.set_position(lat, lon, alt or 0.0)

    @pyqtSlot(str, int, str, str, str)
    def _on_start_ntrip(self, host: str, port: int, mountpoint: str, user: str, passwd: str) -> None:
        """Kullanıcı 'NTRIP Başlat' butonuna bastı — worker yarat ve başlat."""
        # Önceki worker varsa durdur
        if self.ntrip_worker is not None:
            self.ntrip_worker.stop()

        self.ntrip_worker = NtripWorker(host, port, mountpoint, user, passwd, parent=self)
        if self._last_gps_fix is not None:
            self.ntrip_worker.set_position(*self._last_gps_fix)
        else:
            logger.warning("NTRIP başlatılıyor ama henüz F9P konumu yok — VRS "
                           "mountpoint'i GGA alana kadar RTCM göndermeyebilir.")
        self.ntrip_worker.rtcm_ready.connect(self.mavlink_worker.inject_rtcm)
        self.ntrip_worker.connection_changed.connect(self._advanced_view.on_ntrip_connection)
        self.ntrip_worker.connection_changed.connect(self._dashboard.ntrip_connection_changed)
        self.ntrip_worker.error_occurred.connect(self._on_error)
        self.ntrip_worker.start()
        logger.info("NTRIP başlatıldı: %s:%d/%s", host, port, mountpoint)

    @pyqtSlot()
    def _on_stop_ntrip(self) -> None:
        """Kullanıcı 'NTRIP Durdur' butonuna bastı — worker yok et."""
        if self.ntrip_worker is not None:
            self.ntrip_worker.stop()
            self.ntrip_worker = None
        # NEDEN elle: worker temiz durdurulduğunda connection_changed(False)
        # YAYINLAMAZ (yalnızca run() içindeki hata dalında yayınlanır) — dashboard
        # etiketi worker sinyaline güvenirse "bağlı" göstermeye devam ederdi.
        self._dashboard.ntrip_connection_changed(False)
        logger.info("NTRIP durduruldu")

    @pyqtSlot(int)
    def _on_download_log(self, log_id: int) -> None:
        logger.info("Log indirme başlatılıyor: id=%d", log_id)
        self._status_label.setText(f"Log {log_id} indiriliyor...")
        self.mavlink_worker.download_log(log_id)

    @pyqtSlot(bool, str, bytes)
    def _on_log_download_done(self, success: bool, filename: str, data: bytes) -> None:
        if not success:
            self._status_label.setText("Log indirme başarısız")
            return
        import os
        from PyQt6.QtWidgets import QFileDialog
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Log Kaydet", filename, "Binary Log (*.bin);;Tüm Dosyalar (*)"
        )
        if save_path:
            try:
                with open(save_path, "wb") as f:
                    f.write(data)
                self._status_label.setText(f"Log kaydedildi: {os.path.basename(save_path)}")
                logger.info("Log kaydedildi: %s (%d bayt)", save_path, len(data))
            except Exception as exc:
                logger.error("Log kaydetme hatası: %s", exc)
                self._status_label.setText(f"Log kaydetme hatası: {exc}")
        else:
            self._status_label.setText("Log indirme iptal edildi")

    @pyqtSlot(str)
    def _on_video_source_changed(self, source: str, _user: bool = True) -> None:
        """Video kaynağını değiştir — UI thread'ini bloklamadan worker'ı yeniden başlat.

        source == "auto"  → görev-bazlı otomatik geçişi yeniden aç.
        Aksi halde kullanıcı seçimi → manuel override (otomatik geçiş durur).
        _user=False yalnızca otomatik geçiş mantığı tarafından çağrılır (override'ı bozmaz).
        """
        # "Otomatik" seçimi: override'ı kapat, mevcut görev durumuna göre kaynağı uygula
        if source == "auto":
            self._video_manual_override = False
            logger.info("Video kaynağı: OTOMATİK mod (görev durumuna göre geçiş)")
            desired = self._video_url_processed if self._mission_video_active() else self._video_url_raw
            self._apply_video_source(desired)
            return

        # Kullanıcı diyalogdan elle seçti → otomatik geçişi durdur
        if _user:
            self._video_manual_override = True

        self._apply_video_source(source)

    def _mission_video_active(self) -> bool:
        """Son RPi status'a göre görev çalışıyor mu (otomatik video geçişi için)."""
        return self._last_mission_running

    def _apply_video_source(self, source: str) -> None:
        """Video worker'ı verilen kaynağa geçir (gerçek değişim)."""
        if source == self._current_video_source and self.video_worker.isRunning():
            return
        logger.info("Video kaynağı değiştiriliyor: %s", source)
        self._current_video_source = source
        old_worker = self.video_worker
        old_worker.requestInterruption()
        # UI thread'ini bloke etme — eski worker'ı arka planda bitir
        old_worker.finished.connect(old_worker.deleteLater)
        # sim modunda FakeVideoWorker kullanılmalı — _init_workers'ta seçilen sınıfı
        # kullan (önceden burada sabit VideoStreamWorker vardı → sim'de gerçek worker
        # ağa bağlanmaya çalışıyordu).
        self.video_worker = self._video_cls(source=source, parent=self)
        self.video_worker.frame_ready.connect(self._dashboard.update_video)
        self.video_worker.error_occurred.connect(self._on_error)
        self.video_worker.start()
        self._status_label.setText(f"Video: {source}")

    @pyqtSlot(bool)
    def _on_rpi_connection_changed(self, connected: bool) -> None:
        """RPi status port bağlantı değişimi.
        Video akışı status porttan BAĞIMSIZ çalışır — status port kopsa da video durdurmaz.
        Video yalnızca kullanıcı manuel bağlantıyı kesince durur (_on_disconnect_requested).

        NEDEN (durum etiketi): Etiket ÖNCEDEN yalnızca _on_rpi_status içinde ve
        SABİT "🟢" ikonuyla güncelleniyordu. Companion çökerse yeni durum paketi
        hiç gelmez → durum çubuğu son CPU/RAM/Sıcaklık değerleriyle ve YEŞİL
        ikonla SONSUZA KADAR donuk kalıyordu; operatör sistemi sağlıklı sanıyordu.
        Artık bağlantı kopunca etiket açıkça "bağlantı yok" durumuna geçer.
        """
        if connected:
            from core.gcs_config import config as _cfg
            if _cfg.companion_auth_token:
                self._apply_rpi_status_style("accent")
                self._rpi_status_label.setText("Yardımcı Bilgisayar 🟢: bağlandı, durum bekleniyor")
            else:
                # 2026-08-26: auth_token boşken (komut VE durum kanalı
                # doğrulamasız) bağlantı sessizce "her şey normal" gibi
                # görünüyordu — artık bağlantı durum etiketinin kendisinde,
                # göz ardı edilemez şekilde uyarılıyor.
                logger.warning(
                    "GÜVENLİK: companion_auth_token BOŞ — komut (5601) ve durum "
                    "(5602) kanalları kimlik doğrulamasız. Ayarlar'dan bir "
                    "anahtar girin (companion'daki config.json ile AYNI).")
                self._apply_rpi_status_style("warning", bold=True)
                self._rpi_status_label.setText(
                    "Yardımcı Bilgisayar 🟢: bağlandı ⚠ KİMLİK DOĞRULAMA KAPALI")
            return

        logger.warning("Yardımcı Bilgisayar status bağlantısı kesildi — "
                       "durum göstergeleri geçersiz (video etkilenmez)")
        self._apply_rpi_status_style("danger", bold=True)
        self._rpi_status_label.setText("Yardımcı Bilgisayar 🔴: bağlantı yok")
        self._reset_rpi_gauges()
        # NEDEN acil kontrollere DOKUNULMUYOR: companion bağlantısı kopmuş olsa
        # da araç hâlâ uçuyor olabilir. "DURDUR / RTL" companion'dan BAĞIMSIZ
        # olarak FC'ye doğrudan RTL de gönderir (_on_abort_mission) — tam da
        # companion'ın kaybolduğu anda kapatılmaması gereken buton budur.
        # Video burada DURDURULMAZ — VideoStreamWorker kendi retry'ını yapar

    def _reset_rpi_gauges(self) -> None:
        """Dashboard'daki companion CPU/RAM/Sıcaklık/Ping göstergelerini "—" yapar.

        NEDEN buradan: dashboard_view.py'de bu göstergeleri sıfırlayan PUBLIC bir
        metot YOK (yalnızca özel _hw_set) ve o dosyayı değiştirmek yasak. Bu
        yüzden yalnızca Qt'nin kendi public QProgressBar API'si (setValue/
        setFormat) kullanılıyor — dashboard_view'in ilk kurulumdaki "—" biçimiyle
        birebir aynı görünüm. Kalıcı çözüm: dashboard_view'a
        reset_rpi_status()/set_rpi_offline() gibi public bir metot eklemek.
        """
        for attr in ("_cpu_bar", "_ram_bar", "_temp_bar", "_ping_bar"):
            bar = getattr(self._dashboard, attr, None)
            if bar is None:
                continue
            try:
                bar.setValue(0)
                bar.setFormat("—")
            except Exception as exc:
                logger.debug("Gösterge %s sıfırlanamadı: %s", attr, exc)

    @pyqtSlot(dict)
    def _on_rpi_status(self, data: dict) -> None:
        # Ondalık taşmasını önle: yüzdeler tam sayı, sıcaklık tek ondalık.
        try:
            cpu = int(round(float(data.get("cpu_pct", 0.0))))
        except (TypeError, ValueError):
            cpu = 0
        try:
            mem = int(round(float(data.get("mem_pct", 0.0))))
        except (TypeError, ValueError):
            mem = 0
        try:
            temp = f"{float(data.get('temp_c', 0.0)):.1f}"
        except (TypeError, ValueError):
            temp = "0.0"

        mission_data = data.get("mission", {})
        m_active = mission_data.get("active", False)
        m_state = mission_data.get("state", "BEKLEMEDE")

        # ── Video kaynağı otomatik geçiş ─────────────────────────────────────
        # Görev aktifse işlenmiş video (8081), değilse ham kamera (8080).
        # Kullanıcı diyalogdan elle Ham/İşlenmiş seçtiyse (override) dokunma.
        mission_running = bool(m_active) and str(m_state).upper() not in (
            "COMPLETED", "ABORTED", "IDLE", "BEKLEMEDE", "ERROR"
        )
        _was_running = self._last_mission_running
        self._last_mission_running = mission_running

        # ── GÖREV KRONOMETRESİ: companion görev durumu kenar geçişleri ───
        # NEDEN: görev bitince/iptal olunca sayaç DURMALIDIR; ayrıca YKİ
        # görevi kendisi başlatmadıysa (çökme sonrası yeniden açılış,
        # otonom başlatma) yükselen kenar saati yine başlatır.
        # set_mission_running True kenarında zaten çalışan saati yeniden
        # BAŞLATMAZ (başlat butonu birkaç sn önce başlatmıştır).
        if mission_running != _was_running:
            try:
                self._mission_panel.set_mission_running(mission_running)
            except Exception as exc:
                logger.error("Görev kronometresi güncellenemedi: %s", exc)

        # ── KRİTİK: Acil kontrolleri GERÇEK göreve bağla ─────────────────────
        # NEDEN: "DURDUR / RTL" ve "ŞİMDİ BIRAK" ÖNCEDEN yalnızca YKİ'nin BU
        # OTURUMDA görev başlatmasıyla aktifleşiyordu. Görev uçarken YKİ çökse /
        # yeniden başlatılsa, ya da görevi companion otonom başlatsa acil
        # durdurma butonu DEVRE DIŞI kalıyor ve operatör görevi durduramıyordu.
        # Artık companion durum akışındaki mission.active/mission.state bilgisi
        # butonları sürer (top_bar bunu kendi oturum bayrağıyla VEYA'lar).
        # RBAC burada uygulanır: VIEWER (rol < 2) zaten komut gönderemez
        # (_require_command_role), butonu da açmıyoruz — tek yetki kaynağı burası.
        try:
            self.top_bar.set_mission_active(
                mission_running and state.get_rbac_role() >= 2
            )
        except Exception as exc:
            logger.error("Acil kontrol durumu güncellenemedi: %s", exc)

        if not self._video_manual_override:
            desired_video = self._video_url_processed if mission_running else self._video_url_raw
            if desired_video != self._current_video_source:
                logger.info("Video kaynağı otomatik değişiyor → %s (görev durumu=%s)",
                            "İŞLENMİŞ" if mission_running else "HAM", m_state)
                self._apply_video_source(desired_video)

        # Durum çubuğu etiketi (sağ köşe)
        # NEDEN "🟢" burada doğru: bu slot yalnızca TAZE bir durum paketi geldiğinde
        # çalışır, yani bağlantı o an gerçekten canlıdır. Kopma durumu artık ayrıca
        # _on_rpi_connection_changed içinde "🔴 bağlantı yok" olarak işleniyor —
        # etiketin eski değerlerle donup yeşil kalması bu sayede mümkün değil.
        conn_icon = "🟢"
        color_token = "success" if m_active else "accent"
        state_str = str(m_state).upper() if m_state else "HAZIR"
        self._apply_rpi_status_style(color_token)
        self._rpi_status_label.setText(
            f"Yardımcı Bilgisayar {conn_icon}: {state_str} | CPU:%{cpu} | RAM:%{mem} | Sıc:{temp}C"
        )

        # İHA canlı durumu (payload/servo/vision/TOF) → Dashboard'da görselleştir
        # (E5: eskiden yalnızca DEBUG'a yazılıyordu; veri zaten geliyordu)
        try:
            self._dashboard.update_rpi_status(data)
        except Exception as exc:
            logger.debug("Dashboard İHA durumu güncellenemedi: %s", exc)

        # E2: FC görev belleği companion tarafından değiştiyse operatörü uyar
        # (manuel yüklenen görevin üzerine yazılmış olabilir)
        fc = data.get("fc_mission") or {}
        fc_ts = fc.get("ts")
        if fc_ts and fc_ts != getattr(self, "_prev_fc_mission_ts", None):
            self._prev_fc_mission_ts = fc_ts
            msg = (f"FC görevi güncellendi: {fc.get('type','?')} "
                   f"{fc.get('wp_count','?')} WP (companion yükledi)")
            self._dashboard.add_rpi_mission_message(msg)
            self._status_label.setText(msg)

        # Değişim tespiti ➔ Yardımcı Bilgisayar Görev paneline mesaj yaz
        prev = getattr(self, "_prev_rpi_state", {})
        msgs = []

        if m_active != prev.get("active"):
            msgs.append(f"Görev Durumu: {'AKTİF' if m_active else 'DURDURULDU'}")

        if m_state and m_state != prev.get("state"):
            msgs.append(f"Aşama: {m_state}")

        for msg in msgs:
            self._dashboard.add_rpi_mission_message(msg)

        self._prev_rpi_state = {
            "active": m_active,
            "state": m_state
        }

    @pyqtSlot(str, int)
    def _on_rpi_connect_requested(self, host: str, port: int) -> None:
        self.rpi_worker.enable(host, port)
        logger.info("Yardımcı Bilgisayar bağlantısı etkinleştirildi: %s:%d", host, port)

    @pyqtSlot()
    def _on_rpi_disconnect_requested(self) -> None:
        self.rpi_worker.disable()
        # NEDEN: Kullanıcı elle kestiğinde de göstergeler donmuş eski değerlerle
        # kalmasın (bkz. _on_rpi_connection_changed).
        self._apply_rpi_status_style("text_muted")
        self._rpi_status_label.setText("Yardımcı Bilgisayar ⚪: bağlantı kapatıldı")
        self._reset_rpi_gauges()
        # Bağlantı kesildi: manuel override'ı sıfırla, videoyu durdur (yeniden
        # bağlanınca otomatik mod devam etsin)
        self._video_manual_override = False
        self._apply_video_source("none")
        logger.info("Yardımcı Bilgisayar bağlantısı devre dışı bırakıldı")

    @pyqtSlot(str)
    def _on_logout_required(self, msg: str) -> None:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Oturum Uyarısı", msg)

    @pyqtSlot(str)
    def _on_mission_rejected(self, reason: str) -> None:
        """FIX P0-1/P0-2: Companion görev reddedince Başlat butonunu devre dışı bırak ve kullanıcıyı uyar."""
        from PyQt6.QtWidgets import QMessageBox
        self.top_bar.set_mission_ready(False)
        self._status_label.setText(f"Görev reddedildi: {reason[:80]}")
        QMessageBox.warning(self, "Görev Reddedildi", f"Yardımcı bilgisayar görevi kabul etmedi:\n\n{reason}")

    @pyqtSlot(str, str, str)
    def _on_health_alert(self, subsystem: str, level: str, message: str) -> None:
        """Companion'dan gelen FAILED/CRITICAL failsafe uyarısı."""
        from PyQt6.QtWidgets import QMessageBox
        logger.warning("FAILSAFE [%s] %s: %s", level, subsystem, message)

        self._apply_rpi_status_style(
            "danger" if level == "CRITICAL" else "warning", bold=True
        )
        self._status_label.setText(f"FAILSAFE {level}: {subsystem} — {message[:70]}")

        # TTS uyarı
        self.tts_worker.speak(f"Uyarı: {subsystem} {level.lower()}")

        # CRITICAL → dialog
        if level == "CRITICAL":
            QMessageBox.critical(
                self,
                f"KRİTİK FAILSAFE — {subsystem.upper()}",
                f"Yardımcı bilgisayar kritik bir durum bildirdi:\n\n"
                f"Sistem: {subsystem}\n"
                f"Mesaj:  {message}\n\n"
                f"Drone güvenli RTL gerçekleştiriyor olabilir.",
            )
        elif level == "FAILED":
            # Statusbar'da görünür hata, dialog çıkarmıyoruz (dikkat dağıtmasın)
            self._dashboard.add_status_message(f"[FAILSAFE] {subsystem}: {message}", 3)

    @pyqtSlot(str)
    def _on_mission_notification(self, msg: str) -> None:
        """Görevden gelen anlık bildirim — dashboard mesaj paneline yaz."""
        logger.info("Görev bildirimi: %s", msg)
        self._status_label.setText(f"Görev: {msg[:80]}")
        try:
            self._dashboard.add_rpi_mission_message(msg)
        except Exception:
            pass

    # ── Watchdog ─────────────────────────────────────────────────────────────
    def _check_workers(self) -> None:
        # Rol çalışma anında değişmiş olabilir — kilitleri tazele (bkz. metot
        # açıklaması: auth_manager'da role_changed sinyali olmadığı için yoklama).
        self._check_rbac_change()

        # critical = çalışıyor olması şart olan worker'lar
        # optional = ayarsız/pasif olabilir, yalnızca gerçekten çökmüşse yeniden başlat
        critical_workers = {
            "mavlink": self.mavlink_worker,
            "ai":      self.ai_worker,
            "video":   self.video_worker,
            "tts":     self.tts_worker,
        }
        # NTRIP opsiyonel — None ise watchdog'a dahil etme
        # rpi_worker ÖNCEDEN watchdog'a hiç dahil değildi — gerçek bir thread
        # çökmesi (Python seviyesinde run()'dan kaçan istisna) fark edilmiyordu.
        # Her zaman oluşturulur (kullanıcı etkinleştirmese de thread "idle" halde
        # isRunning()=True kalır) — bu yüzden ntrip gibi None kontrolüne gerek
        # yok, sadece çöküş tespiti (hang değil) yeterli.
        optional_workers = {
            "rpi":     self.rpi_worker,
        }
        if self.ntrip_worker is not None:
            optional_workers["ntrip"] = self.ntrip_worker

        all_workers = {**critical_workers, **optional_workers}

        for name, worker in all_workers.items():
            try:
                current = worker.heartbeat_counter
            except AttributeError:
                current = 0

            last = self._hb_snapshots.get(name, -1)
            unchanged = last != -1 and current == last

            if name in critical_workers:
                # Kritik worker: takılma VE çökme tespiti. Takılma tespiti
                # HANG_CONFIRM_TICKS ardışık örnek gerektirir (bkz. üstteki not) —
                # tek bir meşru uzun bloklama (ör. flaky bağlantıda 10s'lik bir
                # gönderim) artık sahte "takıldı" alarmı üretmiyor.
                if unchanged and worker.isRunning():
                    self._hb_stale_count[name] = self._hb_stale_count.get(name, 0) + 1
                else:
                    self._hb_stale_count[name] = 0
                hung    = self._hb_stale_count.get(name, 0) >= HANG_CONFIRM_TICKS
                crashed = last != -1 and not worker.isRunning()
            else:
                # Opsiyonel worker: sadece çöküş tespiti (idle = normal)
                hung    = False
                crashed = last != -1 and not worker.isRunning()

            if hung or crashed:
                reason = "takıldı" if hung else "çöktü"
                # Kurtarma DENEMESİ: kesme iste + kısa bekle. UI thread'ini uzun
                # süre bloke etme (takılı worker zaten timeout'a kadar bitmez).
                stopped = False
                try:
                    worker.requestInterruption()
                    stopped = bool(worker.wait(800))
                except Exception as exc:
                    logger.debug("Worker %s durdurulamadı: %s", name, exc)
                    stopped = False

                if not stopped and worker.isRunning():
                    # NEDEN: QThread::start() ZATEN ÇALIŞAN bir thread üzerinde
                    # SESSİZCE no-op'tur — yalnızca "Thread is already running"
                    # uyarısı basar, istisna ATMAZ. Eski kod bunu başarı sayıp her
                    # 15 sn'de bir "yeniden başlatılıyor" logluyordu; operatör
                    # sistemin kendini toparladığını sanıyordu. Takılı thread'i
                    # burada kurtarmanın bir yolu YOK — bunu dürüstçe söylüyoruz.
                    self._report_worker_unrecoverable(name, reason)
                else:
                    logger.warning("Worker %s: %s — yeniden başlatılıyor", name, reason)
                    try:
                        worker.start()
                    except Exception as exc:
                        logger.error("Worker %s yeniden başlatılamadı: %s", name, exc)
                    # TTS sadece kritik çöküşlerde (idle restart'larda konuşmasın)
                    if crashed:
                        self.tts_worker.speak(f"Alt sistem yeniden başlatıldı: {name}")
                    # Gerçekten yeniden başlattık → "kurtarılamıyor" damgasını kaldır
                    self._hb_unrecoverable.discard(name)
                    # Yeniden başlattık — sayaç sıfırlansın.
                    # NEDEN yalnızca BU dalda: kurtarılamayan worker'da sayacı
                    # sıfırlamak `hung`'ı 3 tick boyunca False yapar ve aşağıdaki
                    # "tekrar yanıt veriyor" dalını heartbeat hiç ilerlemeden
                    # tetikler → operatöre YANLIŞ "toparlandı" bilgisi giderdi.
                    self._hb_stale_count[name] = 0

            elif not unchanged and name in self._hb_unrecoverable:
                # Worker GERÇEKTEN toparladı — heartbeat sayacı yeniden ilerliyor
                # (yalnızca `hung` bayrağının düşmesi yeterli DEĞİL, bkz. yukarı):
                # "manuel müdahale gerekli" uyarısı ekranda asılı kalmasın.
                self._hb_unrecoverable.discard(name)
                msg = f"Worker '{name}' tekrar yanıt veriyor — otomatik kurtarma tamamlandı"
                logger.warning(msg)
                self._status_label.setText(msg)
                try:
                    self._dashboard.add_status_message(f"[WATCHDOG] {msg}", 1)
                except Exception:
                    pass

            self._hb_snapshots[name] = current

    def _report_worker_unrecoverable(self, name: str, reason: str) -> None:
        """Takılı worker kurtarılamadı — operatörü DÜRÜSTÇE ve BİR KEZ uyar.

        NEDEN tek sefer: uyarı her watchdog tick'inde (5 sn) tekrarlanırsa durum
        çubuğu ve Sistem/Log spam'lenir, gerçek mesajlar kaybolur. Bu yüzden
        yalnızca DURUM DEĞİŞİMİNDE (sağlıklı → kurtarılamaz) bir kez bildirilir;
        worker toparlarsa damga silinir ve gerekirse tekrar uyarılabilir.
        """
        if name in self._hb_unrecoverable:
            return   # aynı durum sürüyor — tekrar uyarma
        self._hb_unrecoverable.add(name)

        msg = (f"Worker '{name}' yanıt vermiyor ({reason}) — OTOMATİK KURTARMA "
               f"BAŞARISIZ, MANUEL MÜDAHALE GEREKLİ")
        # Sistem/Log ekranı root logger'dan beslenir → kalıcı kayıt buradan gider
        logger.error("%s. Thread hâlâ çalışıyor; QThread.start() bu durumda etkisiz. "
                     "YKİ'yi yeniden başlatmanız gerekebilir.", msg)
        self._status_label.setText(msg)
        try:
            local_storage.write_audit(self._db_conn, "ERROR", msg)
        except Exception as exc:
            logger.debug("Watchdog uyarısı audit_log'a yazılamadı: %s", exc)
        try:
            self._dashboard.add_status_message(f"[WATCHDOG] {msg}", 3)
        except Exception:
            pass
        try:
            self.tts_worker.speak(f"Alt sistem yanıt vermiyor: {name}")
        except Exception:
            pass

    # ── Kapatma ──────────────────────────────────────────────────────────────
    def closeEvent(self, event) -> None:
        logger.info("Uygulama kapatılıyor — tüm worker'lar durduruluyor")
        # GÜVENLİK: worker'lar durmadan ÖNCE motor testi durdurulur. Sıra
        # önemli — mavlink_worker durduktan sonra DUR komutu gönderilemez.
        try:
            fn = getattr(self._calib_view, "acil_motor_durdur", None)
            if callable(fn):
                fn()
        except Exception:
            logger.exception("Kapanışta motor durdurma hatası")
        self._watchdog.stop()
        # Uçuş kaydı ÖNCE kapatılmalı: worker'lar durunca telemetri kesilir ve
        # kayıt yalnız armed→disarmed geçişinde yazıldığı için uçuş kaybolurdu.
        # finalize() senkron yazar (daemon thread kapanışta öldürülebilir).
        try:
            self.telemetry_recorder.finalize("uygulama kapanışı")
        except Exception as exc:
            logger.warning("Uçuş kaydı kapatılamadı: %s", exc)
        workers_to_stop = [
            self.mavlink_worker, self.ai_worker,
            self.video_worker, self.tts_worker, self.rpi_worker,
            self._saha_testi_view.video_worker,
        ]
        if self.ntrip_worker is not None:
            workers_to_stop.append(self.ntrip_worker)
        if self.rtk_gps_worker is not None:
            workers_to_stop.append(self.rtk_gps_worker)
        for worker in workers_to_stop:
            try:
                worker.stop()
            except Exception:
                pass
        event.accept()
