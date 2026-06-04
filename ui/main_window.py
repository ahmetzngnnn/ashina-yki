"""
main_window.py — ASHİNA YKİ Ana Pencere
Tüm view'leri barındırır; signal/slot bağlamalarını yapar.
Watchdog QTimer (5 sn) ile worker heartbeat'lerini izler.
"""
import logging
from PyQt6.QtCore import QTimer, pyqtSlot
from PyQt6.QtGui import QFont
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
from ui.views.settings_view import SettingsView
from ui.views.iha_test_panel_view import IHATestPanelView
from core.telemetry_recorder import TelemetryRecorder

logger = logging.getLogger(__name__)

WATCHDOG_INTERVAL_MS = 5000


class MainWindow(QMainWindow):
    def __init__(self, db_conn, auth_manager: AuthManager, parent=None):
        super().__init__(parent)
        self._db_conn = db_conn
        self._auth = auth_manager
        self._hb_snapshots: dict = {}
        self._prev_rpi_state: dict = {}   # önceki RPi durumu — değişim tespiti için

        self.setWindowTitle("ASHİNA YKİ  |  Taktiksel Yer Kontrol İstasyonu v2.0")
        self.setMinimumSize(1280, 768)
        self._apply_style()

        self._init_workers()
        self._build_ui()
        self._connect_signals()
        self._apply_rbac()
        self._start_workers()

        # Watchdog
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(WATCHDOG_INTERVAL_MS)
        self._watchdog.timeout.connect(self._check_workers)
        self._watchdog.start()

    def _apply_style(self) -> None:
        # Global style.qss üzerinden yönetiliyor.
        pass

    # ── Worker Başlatma ──────────────────────────────────────────────────────
    def _init_workers(self) -> None:
        self.mavlink_worker = MavlinkWorker(self)
        self.mavlink_worker.set_db_conn(self._db_conn)
        # FIX P1-4: Timeout değerlerini config'den al
        self.ai_worker = AISocketWorker(
            host=config.companion_host,
            port=config.companion_mission_port,
            connect_timeout=config.ai_connect_timeout,
            response_timeout=config.ai_response_timeout,
            parent=self,
        )
        # NTRIP: kullanıcı başlatana kadar None — otomatik başlamaz
        self.ntrip_worker: NtripWorker | None = None
        # İki video kaynağı: ham (görev yokken) ve işlenmiş (görev sırasında).
        # Kamera YALNIZCA Yardımcı Bilgisayar'a bağlanınca açılır (otomatik denemez).
        self._video_url_raw = f"http://{config.companion_host}:{config.companion_video_raw_port}/video"
        self._video_url_processed = f"http://{config.companion_host}:{config.companion_video_port}/video"
        self._current_video_source = "none"
        self.video_worker = VideoStreamWorker(
            source="none",
            open_timeout=config.video_open_timeout,
            retry_delay=config.video_retry_delay,
            parent=self,
        )
        self.tts_worker = AudioTTSWorker(parent=self)
        self.rpi_worker = RpiStatusWorker(
            host=config.companion_host,
            port=config.companion_status_port,
            connect_timeout=config.rpi_connect_timeout,
            read_timeout=config.rpi_read_timeout,
            parent=self,
        )
        self.telemetry_recorder = TelemetryRecorder(self._db_conn, parent=self)

        # ── Anten Takip Sistemi (RUTX10 + Pixhawk gimbal) ────────────────────
        # İHA telemetrisinden anten azimut/elevasyon hesaplayıp anten Pixhawk'ına iletir.
        from core.antenna_tracker_worker import AntennaTrackerWorker
        _ah_lat, _ah_lon, _ah_alt = config.antenna_home
        self.antenna_tracker = AntennaTrackerWorker(
            conn_string=config.antenna_pixhawk_conn,
            mode=config.antenna_tracker_mode,
            home_lat=_ah_lat, home_lon=_ah_lon, home_alt=_ah_alt,
            min_distance_m=config.antenna_min_distance_m,
            update_hz=config.antenna_update_hz,
            parent=self,
        )

    def _start_workers(self) -> None:
        self.tts_worker.start_tts_process()
        self.tts_worker.start()
        self.mavlink_worker.start()
        self.ai_worker.start()
        self.video_worker.start()
        self.rpi_worker.start()
        # Anten takip worker thread'i çalışır; takip yalnızca enable() ile aktifleşir
        self.antenna_tracker.start()
        if config.antenna_tracker_enabled:
            self.antenna_tracker.enable()
        # ntrip_worker: kullanıcı isteyince başlar (None durumda)
        logger.info("Worker thread'leri başlatıldı (NTRIP + Anten Takip isteğe bağlı)")

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
        self._tabs.setFont(QFont("Segoe UI", 11))

        self._dashboard      = DashboardView()
        self._mission_view   = MissionPlanView()
        self._mission_panel  = MissionPanelView()
        self._test_panel     = TestPanelView(self._db_conn)
        self._iha_test_panel = IHATestPanelView()     # YENİ: İHA Test Paneli
        self._calib_view     = CalibrationView()
        self._tuning_view    = TuningView()
        self._advanced_view  = AdvancedView()
        self._syslog_view    = SystemLogView(self._db_conn)
        self._flight_analysis_view = FlightAnalysisView(self._db_conn)
        self._settings_view  = SettingsView()
        from ui.views.antenna_tracker_view import AntennaTrackerView
        self._antenna_view   = AntennaTrackerView()

        self._tabs.addTab(self._dashboard,      "Dashboard")
        self._tabs.addTab(self._mission_view,   "Görev Planlama (Telemetri)")
        self._tabs.addTab(self._mission_panel,  "Özel Görevler (Yardımcı Bilgisayar)")
        self._tabs.addTab(self._iha_test_panel, "🔧 İHA Devreye Alma")   # YENİ sekme
        self._tabs.addTab(self._test_panel,     "Test Paneli")
        self._tabs.addTab(self._calib_view,     "Kalibrasyon")
        self._tabs.addTab(self._tuning_view,    "PID Tuning")
        self._tabs.addTab(self._advanced_view,  "Gelişmiş")
        self._tabs.addTab(self._syslog_view,    "Sistem / Log")
        self._tabs.addTab(self._flight_analysis_view, "Kayıtlar & Analiz")
        self._tabs.addTab(self._antenna_view,   "📡 Anten Takip")
        self._tabs.addTab(self._settings_view,  "Ayarlar")

        root.addWidget(self._tabs, stretch=1)

        # Durum çubuğu
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._status_label = QLabel("Hazır")
        self._statusbar.addWidget(self._status_label)
        self._rpi_status_label = QLabel("Yardımcı Bilgisayar: bağlı değil")
        self._rpi_status_label.setStyleSheet("color: #8b949e; padding: 0 8px;")
        self._statusbar.addPermanentWidget(self._rpi_status_label)

    # ── Sinyal Bağlamaları ───────────────────────────────────────────────────
    def _connect_signals(self) -> None:
        # Ayarlar → Worker'ları yeniden yapılandır
        self._settings_view.settings_changed.connect(self._on_settings_changed)

        # TopBar ↔ MAVLink
        self.top_bar.connect_requested.connect(self._on_connect_requested)
        self.top_bar.disconnect_requested.connect(self._on_disconnect_requested)

        # MAVLink → UI
        self.mavlink_worker.telemetry_updated.connect(self.top_bar.update_telemetry)
        self.mavlink_worker.telemetry_updated.connect(self._dashboard.update_telemetry)
        self.mavlink_worker.telemetry_updated.connect(self._tuning_view.update_telemetry)
        # Anten takip: İHA konumunu tracker'a besle (otomatik takip)
        self.mavlink_worker.telemetry_updated.connect(self.antenna_tracker.update_drone_position)
        self.antenna_tracker.error_occurred.connect(self._on_error)
        # Anten Takip Paneli ↔ Worker
        self.antenna_tracker.tracker_status.connect(self._antenna_view.update_status)
        self.antenna_tracker.connection_changed.connect(self._antenna_view.set_connection)
        self._antenna_view.enable_requested.connect(self._on_antenna_enable)
        self._antenna_view.disable_requested.connect(self._on_antenna_disable)
        self._antenna_view.set_home_from_drone.connect(self._on_antenna_home_drone)
        self._antenna_view.set_home_manual.connect(self.antenna_tracker.set_antenna_home)
        self.mavlink_worker.connection_changed.connect(self.top_bar.set_connection_status)
        self.mavlink_worker.connection_changed.connect(self._on_connection_changed)
        self.mavlink_worker.param_received.connect(self._advanced_view.update_param)
        self.mavlink_worker.param_received.connect(self._tuning_view.update_param)
        self.mavlink_worker.log_entry_received.connect(self._syslog_view.on_log_entry)
        self.mavlink_worker.error_occurred.connect(self._on_error)
        self.mavlink_worker.mag_cal_progress.connect(self._calib_view.on_mag_cal_progress)
        self.mavlink_worker.mag_cal_report.connect(self._calib_view.on_mag_cal_report)

        # UI → MAVLink
        self._calib_view.send_command.connect(self.mavlink_worker.send_command)
        self._tuning_view.send_command.connect(self.mavlink_worker.send_command)
        self._tuning_view.set_param.connect(self.mavlink_worker.set_param)
        self._advanced_view.set_param.connect(self.mavlink_worker.set_param)
        self._advanced_view.request_params.connect(self.mavlink_worker.request_parameters)
        self._dashboard.send_command.connect(self.mavlink_worker.send_command)
        self._dashboard.ned_velocity_cmd.connect(self.mavlink_worker.send_ned_velocity)
        self._dashboard.send_servo_action.connect(self.ai_worker.send_servo_command)

        # Görev Paneli — JSON companion'a, MAVLink yüklemesi devre dışı
        # MissionView bağlantısı (Telemetri)
        self._mission_view.write_mission.connect(self.mavlink_worker.upload_mission)
        self._mission_view.read_mission.connect(self.mavlink_worker.request_mission)
        self.mavlink_worker.mission_download_done.connect(self._on_mission_download_done)
        self.mavlink_worker.telemetry_updated.connect(self._mission_view.update_telemetry)

        # RPi Görev bağlantısı
        self._mission_panel.mission_ready_to_send.connect(self.ai_worker.send_mission_with_waypoints)
        self._mission_panel.send_command.connect(self.mavlink_worker.send_command)
        self.mavlink_worker.telemetry_updated.connect(self._mission_panel.update_telemetry)
        self.mavlink_worker.mission_upload_done.connect(self._on_mission_upload_done)
        self.mavlink_worker.flight_sequence_status.connect(self._status_label.setText)

        # TopBar — görev başlatma companion'a gider (ai_worker)
        self.top_bar.start_mission_requested.connect(self.ai_worker.send_start_command)
        # BUG FIX: fcu_start_mission_requested hiç bağlanmamıştı → FCU butonu çalışmıyordu
        self.top_bar.fcu_start_mission_requested.connect(self.mavlink_worker.start_flight_sequence)

        # Test Paneli
        self._test_panel.send_command.connect(self.mavlink_worker.send_command)
        self.mavlink_worker.telemetry_updated.connect(self._test_panel.update_telemetry)

        # AI Worker ↔ UI
        self.ai_worker.ai_detection_alert.connect(self._on_ai_detection)
        self.ai_worker.mission_accepted.connect(lambda: self.top_bar.set_mission_ready(True))
        self.ai_worker.error_occurred.connect(self._on_error)
        # FIX P0-1/P0-2: Companion görevi reddedince kullanıcıya bildir
        self.ai_worker.mission_rejected.connect(self._on_mission_rejected)

        # NTRIP: sinyal bağlantıları (worker başlatılınca _on_start_ntrip'te kurulur)
        self._advanced_view.start_ntrip.connect(self._on_start_ntrip)
        self._advanced_view.stop_ntrip.connect(self._on_stop_ntrip)

        # Video
        self.video_worker.frame_ready.connect(self._dashboard.update_video)
        self.video_worker.error_occurred.connect(self._on_error)
        self._dashboard.video_source_changed.connect(self._on_video_source_changed)

        # Drone mesajları + Kalibrasyon telemetri
        self.mavlink_worker.status_text_received.connect(self._dashboard.add_status_message)
        self.mavlink_worker.status_text_received.connect(self._syslog_view.on_status_text)
        self.mavlink_worker.telemetry_updated.connect(self._calib_view.update_telemetry)
        self.mavlink_worker.telemetry_updated.connect(self._syslog_view.update_telemetry_for_analysis)
        self.mavlink_worker.telemetry_updated.connect(self.telemetry_recorder.on_telemetry)

        # RPi Companion Bağlantı
        self.top_bar.rpi_connect_requested.connect(self._on_rpi_connect_requested)
        self.top_bar.rpi_disconnect_requested.connect(self._on_rpi_disconnect_requested)
        self.top_bar.rpi_battery_config.connect(self.ai_worker.send_battery_config)
        self.rpi_worker.rpi_connected.connect(self.top_bar.set_rpi_connection_status)
        self.rpi_worker.rpi_connected.connect(self._on_rpi_connection_changed)
        self.rpi_worker.rpi_status_updated.connect(self._on_rpi_status)
        # Yeni: Failsafe & Görev bildirimleri
        self.rpi_worker.health_alert.connect(self._on_health_alert)
        self.rpi_worker.mission_notification.connect(self._on_mission_notification)
        # İHA Test Paneli bağlantıları
        self._iha_test_panel.run_test_requested.connect(self.ai_worker.send_run_test)
        self.rpi_worker.rpi_status_updated.connect(self._iha_test_panel.update_test_status)
        self.rpi_worker.mission_notification.connect(self._iha_test_panel.append_log)

        # Auth
        self._auth.logout_required.connect(self._on_logout_required)

        # Log indirme
        self._syslog_view.download_log.connect(self._on_download_log)
        self._syslog_view.request_log_list.connect(self.mavlink_worker.request_log_list)
        self.mavlink_worker.log_download_done.connect(self._on_log_download_done)
        self.mavlink_worker.log_download_progress.connect(self._syslog_view.on_download_progress)

    # ── RBAC Erişim Kontrolü ─────────────────────────────────────────────────
    def _apply_rbac(self) -> None:
        role = state.get_rbac_role()
        # Tab indeksleri
        self._tabs.setTabEnabled(1, role >= 2)   # Görev Paneli: OPERATOR+
        self._tabs.setTabEnabled(2, role >= 2)   # Test Paneli: OPERATOR+
        self._tabs.setTabEnabled(3, role >= 3)   # Calib: ADMIN
        self._tabs.setTabEnabled(4, role >= 2)   # Tuning: OPERATOR+
        self._tabs.setTabEnabled(5, role >= 3)   # Advanced: ADMIN
        self._tabs.setTabEnabled(6, role >= 2)   # Sys/Log: OPERATOR+
        self._tabs.setTabEnabled(7, role >= 2)   # Uçuş Analizi: OPERATOR+
        self._tabs.setTabEnabled(8, role >= 2)   # Ayarlar: OPERATOR+

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
            logger.info("Gimbal bağlantısı: %s  baud=%d", conn_str, baud)
            self._status_label.setText(f"Gimbal: {conn_str} @ {baud}")
            # Gimbal worker'ı başlat (varsa) — getattr ile Pylance uyarısı engellenir
            _gw = getattr(self, "gimbal_worker", None)
            if _gw is not None:
                _gw.set_connection(conn_str, baud)
            else:
                logger.warning("Gimbal worker mevcut değil — bağlantı yoksayıldı")
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
        self._reconnect_mavlink_signals()
        self.mavlink_worker.start()

        # UI'yi hemen güncelle — yeni worker sinyal atmayacak
        self.top_bar.set_connection_status(False)
        state.set_connection_status(False)
        self._status_label.setText("Bağlantı kesildi")

    def _reconnect_mavlink_signals(self) -> None:
        self.mavlink_worker.set_db_conn(self._db_conn)
        self.mavlink_worker.telemetry_updated.connect(self.top_bar.update_telemetry)
        self.mavlink_worker.telemetry_updated.connect(self._dashboard.update_telemetry)
        self.mavlink_worker.telemetry_updated.connect(self._tuning_view.update_telemetry)
        self.mavlink_worker.connection_changed.connect(self.top_bar.set_connection_status)
        self.mavlink_worker.connection_changed.connect(self._on_connection_changed)
        self.mavlink_worker.param_received.connect(self._advanced_view.update_param)
        self.mavlink_worker.param_received.connect(self._tuning_view.update_param)
        self.mavlink_worker.log_entry_received.connect(self._syslog_view.on_log_entry)
        self.mavlink_worker.error_occurred.connect(self._on_error)
        self.mavlink_worker.mag_cal_progress.connect(self._calib_view.on_mag_cal_progress)
        self.mavlink_worker.mag_cal_report.connect(self._calib_view.on_mag_cal_report)
        self._calib_view.send_command.connect(self.mavlink_worker.send_command)
        self._tuning_view.send_command.connect(self.mavlink_worker.send_command)
        self._tuning_view.set_param.connect(self.mavlink_worker.set_param)
        self._advanced_view.set_param.connect(self.mavlink_worker.set_param)
        self._advanced_view.request_params.connect(self.mavlink_worker.request_parameters)
        self._dashboard.send_command.connect(self.mavlink_worker.send_command)
        self._dashboard.ned_velocity_cmd.connect(self.mavlink_worker.send_ned_velocity)
        self._dashboard.send_servo_action.connect(self.ai_worker.send_servo_command)
        self.mavlink_worker.status_text_received.connect(self._dashboard.add_status_message)
        self.mavlink_worker.status_text_received.connect(self._syslog_view.on_status_text)
        self.mavlink_worker.telemetry_updated.connect(self._calib_view.update_telemetry)
        self.mavlink_worker.telemetry_updated.connect(self._syslog_view.update_telemetry_for_analysis)
        self.mavlink_worker.telemetry_updated.connect(self.telemetry_recorder.on_telemetry)
        self.mavlink_worker.telemetry_updated.connect(self._mission_panel.update_telemetry)
        self.mavlink_worker.telemetry_updated.connect(self._test_panel.update_telemetry)
        self._mission_panel.send_command.connect(self.mavlink_worker.send_command)
        self._mission_panel.mission_ready_to_send.connect(self.ai_worker.send_mission_with_waypoints)
        self.mavlink_worker.mission_upload_done.connect(self._on_mission_upload_done)
        self.mavlink_worker.flight_sequence_status.connect(self._status_label.setText)
        # BUG FIX: FCU başlatma sinyali reconnect'te de bağlanmalı
        self.top_bar.fcu_start_mission_requested.connect(self.mavlink_worker.start_flight_sequence)
        self._test_panel.send_command.connect(self.mavlink_worker.send_command)
        # NTRIP sinyali yalnızca _on_start_ntrip içinde bağlanır
        if self.ntrip_worker is not None:
            self.ntrip_worker.rtcm_ready.connect(self.mavlink_worker.inject_rtcm)
        self.mavlink_worker.log_download_done.connect(self._on_log_download_done)
        self.mavlink_worker.log_download_progress.connect(self._syslog_view.on_download_progress)
        self._syslog_view.request_log_list.connect(self.mavlink_worker.request_log_list)

    @pyqtSlot(bool, str)
    def _on_mission_upload_done(self, success: bool, message: str) -> None:
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
    def _on_read_mission(self) -> None:
        """Drone bellekten görevi okur."""
        logger.info("Drone'dan görev okunuyor...")
        self._status_label.setText("Drone'dan görev okunuyor...")

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

    @pyqtSlot(str)
    def _on_error(self, msg: str) -> None:
        logger.error("Worker hatası: %s", msg)
        self._status_label.setText(f"HATA: {msg[:80]}")

    @pyqtSlot(dict)
    def _on_settings_changed(self, new_settings: dict) -> None:
        """Ayarlar kaydedilince worker'ları yeni adreslerle yeniden yapılandır."""
        from core.gcs_config import config as cfg
        host = cfg.companion_host
        logger.info("Ayarlar değişti — AI worker yeniden başlatılıyor: %s:%d",
                    host, cfg.companion_mission_port)

        # ── 1. Eski worker'ın sinyallerini kopar ─────────────────────────
        old = self.ai_worker
        for disconnect in [
            lambda: self._mission_panel.mission_ready_to_send.disconnect(old.send_mission_with_waypoints),
            lambda: self.top_bar.start_mission_requested.disconnect(old.send_start_command),
            lambda: self.top_bar.rpi_battery_config.disconnect(old.send_battery_config),
            lambda: old.ai_detection_alert.disconnect(self._on_ai_detection),
            lambda: old.error_occurred.disconnect(self._on_error),
        ]:
            try:
                disconnect()
            except Exception:
                pass  # Zaten bağlı değilse sessizce geç

        # mission_accepted lambda ile bağlandığı için tüm bağlantıları kes
        try:
            old.mission_accepted.disconnect()
        except Exception:
            pass

        # ── 2. Eski worker'ı durdur ───────────────────────────────────────
        old.requestInterruption()
        old.wait(2000)

        # ── 3. Yeni worker'ı oluştur ve başlat ───────────────────────────
        from core.ai_socket_worker import AISocketWorker
        self.ai_worker = AISocketWorker(
            host=host,
            port=cfg.companion_mission_port,
            connect_timeout=cfg.ai_connect_timeout,
            response_timeout=cfg.ai_response_timeout,
            parent=self,
        )
        self.ai_worker.start()

        # ── 4. Sinyalleri yeni worker'a bağla ────────────────────────────
        self._mission_panel.mission_ready_to_send.connect(self.ai_worker.send_mission_with_waypoints)
        self.top_bar.start_mission_requested.connect(self.ai_worker.send_start_command)
        self.top_bar.rpi_battery_config.connect(self.ai_worker.send_battery_config)
        self.ai_worker.ai_detection_alert.connect(self._on_ai_detection)
        self.ai_worker.mission_accepted.connect(lambda: self.top_bar.set_mission_ready(True))
        self.ai_worker.error_occurred.connect(self._on_error)
        self.ai_worker.mission_rejected.connect(self._on_mission_rejected)

        logger.info("AI worker yeniden başlatıldı: %s:%d", host, cfg.companion_mission_port)

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

    @pyqtSlot(str, int, str, str, str)
    def _on_start_ntrip(self, host: str, port: int, mountpoint: str, user: str, passwd: str) -> None:
        """Kullanıcı 'NTRIP Başlat' butonuna bastı — worker yarat ve başlat."""
        # Önceki worker varsa durdur
        if self.ntrip_worker is not None:
            self.ntrip_worker.stop()

        self.ntrip_worker = NtripWorker(host, port, mountpoint, user, passwd, parent=self)
        self.ntrip_worker.rtcm_ready.connect(self.mavlink_worker.inject_rtcm)
        self.ntrip_worker.connection_changed.connect(self._advanced_view.on_ntrip_connection)
        self.ntrip_worker.error_occurred.connect(self._on_error)
        self.ntrip_worker.start()
        logger.info("NTRIP başlatıldı: %s:%d/%s", host, port, mountpoint)

    @pyqtSlot()
    def _on_stop_ntrip(self) -> None:
        """Kullanıcı 'NTRIP Durdur' butonuna bastı — worker yok et."""
        if self.ntrip_worker is not None:
            self.ntrip_worker.stop()
            self.ntrip_worker = None
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
    def _on_video_source_changed(self, source: str) -> None:
        """Video kaynağını değiştir — worker'ı yeniden başlat."""
        if source == self._current_video_source and self.video_worker.isRunning():
            return   # zaten bu kaynakta — gereksiz yeniden başlatma yok
        logger.info("Video kaynağı değiştiriliyor: %s", source)
        self._current_video_source = source
        self.video_worker.requestInterruption()
        self.video_worker.wait(2000)
        self.video_worker = VideoStreamWorker(source=source, parent=self)
        self.video_worker.frame_ready.connect(self._dashboard.update_video)
        self.video_worker.error_occurred.connect(self._on_error)
        self.video_worker.start()
        self._status_label.setText(f"Video: {source}")

    # ── Anten Takip Paneli handler'ları ──────────────────────────────────────
    @pyqtSlot()
    def _on_antenna_enable(self) -> None:
        self.antenna_tracker.enable()
        self._antenna_view.set_tracking_active(True)
        logger.info("Anten takip başlatıldı (kullanıcı)")

    @pyqtSlot()
    def _on_antenna_disable(self) -> None:
        self.antenna_tracker.disable()
        self._antenna_view.set_tracking_active(False)
        logger.info("Anten takip durduruldu (kullanıcı)")

    @pyqtSlot()
    def _on_antenna_home_drone(self) -> None:
        ok = self.antenna_tracker.set_home_from_current_drone()
        msg = "Anten home = İHA konumu ayarlandı" if ok else "İHA konumu yok — anten home ayarlanamadı"
        self._status_label.setText(msg)
        logger.info(msg)

    @pyqtSlot(bool)
    def _on_rpi_connection_changed(self, connected: bool) -> None:
        """Yardımcı Bilgisayar bağlantısı koptuğunda kamerayı kapat.
        Bağlanınca kamera _on_rpi_status (gelen ilk status) ile otomatik açılır."""
        if not connected:
            logger.info("Yardımcı Bilgisayar bağlantısı kesildi → kamera kapatılıyor")
            self._on_video_source_changed("none")

    @pyqtSlot(dict)
    def _on_rpi_status(self, data: dict) -> None:
        cpu = data.get("cpu_pct", 0.0)
        mem = data.get("mem_pct", 0.0)
        temp = data.get("temp_c", 0.0)

        mission_data = data.get("mission", {})
        m_active = mission_data.get("active", False)
        m_state = mission_data.get("state", "BEKLEMEDE")

        # ── Video kaynağı otomatik geçiş ─────────────────────────────────────
        # Görev aktifse işlenmiş video (8080), değilse ham kamera (8081).
        mission_running = bool(m_active) and str(m_state).upper() not in (
            "COMPLETED", "ABORTED", "IDLE", "BEKLEMEDE", "ERROR"
        )
        desired_video = self._video_url_processed if mission_running else self._video_url_raw
        if desired_video != self._current_video_source:
            logger.info("Video kaynağı otomatik değişiyor → %s (görev durumu=%s)",
                        "İŞLENMİŞ" if mission_running else "HAM", m_state)
            self._on_video_source_changed(desired_video)

        # Durum çubuğu etiketi (sağ köşe)
        conn_icon = "🟢"
        color = "#00e676" if m_active else "#00e5ff"
        state_str = str(m_state).upper() if m_state else "HAZIR"
        self._rpi_status_label.setStyleSheet(f"color: {color}; padding: 0 8px;")
        self._rpi_status_label.setText(
            f"Yardımcı Bilgisayar {conn_icon}: {state_str} | CPU:%{cpu} | RAM:%{mem} | Sıc:{temp}C"
        )

        # Yeni durum alanları (payload_loaded, servo_state, vision_status, tof_distances, mav)
        # Bu alanlar log'a kaydedilir ancak şimdilik UI'de görüntülenmez
        payload_loaded = data.get("payload_loaded", False)
        servo_state = data.get("servo_state", {})
        vision_status = data.get("vision_status", {})
        tof_distances = data.get("tof_distances", {})
        mav = data.get("mav", {})

        if payload_loaded or servo_state or vision_status or tof_distances or mav:
            logger.debug(
                "RPi yardımcı durum: payload_loaded=%s, servo_state=%s, vision=%s, tof=%s, mav=%s",
                payload_loaded, servo_state, vision_status, tof_distances, mav
            )

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
        self._rpi_status_label.setText("Yardımcı Bilgisayar: bağlı değil")
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

        color = "#ef233c" if level == "CRITICAL" else "#ff8700"
        self._rpi_status_label.setStyleSheet(f"color: {color}; font-weight: bold; padding: 0 8px;")
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
            self._dashboard.add_status_message(f"[FAILSAFE] {subsystem}: {message}")

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
        # critical = çalışıyor olması şart olan worker'lar
        # optional = ayarsız/pasif olabilir, yalnızca gerçekten çökmüşse yeniden başlat
        critical_workers = {
            "mavlink": self.mavlink_worker,
            "ai":      self.ai_worker,
            "video":   self.video_worker,
            "tts":     self.tts_worker,
        }
        # NTRIP opsiyonel — None ise watchdog'a dahil etme
        optional_workers = {}
        if self.ntrip_worker is not None:
            optional_workers["ntrip"] = self.ntrip_worker

        all_workers = {**critical_workers, **optional_workers}

        for name, worker in all_workers.items():
            try:
                current = worker.heartbeat_counter
            except AttributeError:
                current = 0

            last = self._hb_snapshots.get(name, -1)

            if name in critical_workers:
                # Kritik worker: takılma VE çökme tespiti
                hung    = last != -1 and current == last and worker.isRunning()
                crashed = last != -1 and not worker.isRunning()
            else:
                # Opsiyonel worker: sadece çöküş tespiti (idle = normal)
                hung    = False
                crashed = last != -1 and not worker.isRunning()

            if hung or crashed:
                reason = "takıldı" if hung else "çöktü"
                logger.warning("Worker %s: %s — yeniden başlatılıyor", name, reason)
                try:
                    worker.requestInterruption()
                    worker.wait(3000)
                except Exception:
                    pass
                try:
                    worker.start()
                except Exception as exc:
                    logger.error("Worker %s yeniden başlatılamadı: %s", name, exc)
                # TTS sadece kritik çöküşlerde (idle restart'larda konuşmasın)
                if crashed:
                    self.tts_worker.speak(f"Alt sistem yeniden başlatıldı: {name}")

            self._hb_snapshots[name] = current

    # ── Kapatma ──────────────────────────────────────────────────────────────
    def closeEvent(self, event) -> None:
        logger.info("Uygulama kapatılıyor — tüm worker'lar durduruluyor")
        self._watchdog.stop()
        workers_to_stop = [
            self.mavlink_worker, self.ai_worker,
            self.video_worker, self.tts_worker, self.rpi_worker,
            self.antenna_tracker,
        ]
        if self.ntrip_worker is not None:
            workers_to_stop.append(self.ntrip_worker)
        for worker in workers_to_stop:
            try:
                worker.stop()
            except Exception:
                pass
        event.accept()
