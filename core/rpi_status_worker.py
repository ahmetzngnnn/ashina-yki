"""core/rpi_status_worker.py — RPi companion durum akışı alıcısı."""
import json
import logging
import socket
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

_RECONNECT_DELAY     = 5.0
# FIX P1-5: Ardışık JSON hata eşiği — bu kadar hatadan sonra reconnect
_JSON_ERROR_THRESHOLD = 5


class RpiStatusWorker(QThread):
    """
    RPi StatusBroadcaster'a (TCP JSON, port 5602) bağlanır.
    Kullanıcı 'RPi Bağlan' butonuna basana kadar bağlantı denemez.
    """

    rpi_status_updated = pyqtSignal(dict)
    rpi_connected      = pyqtSignal(bool)
    error_occurred     = pyqtSignal(str)
    # Yeni: Failsafe seviyesinde uyarılar (FAILED/CRITICAL) ayrı sinyal
    health_alert       = pyqtSignal(str, str, str)  # (subsystem, level, message)
    # Yeni: Görevden anlık bildiri mesajları
    mission_notification = pyqtSignal(str)

    def __init__(self, host: str = "192.168.10.138", port: int = 5602,
                 connect_timeout: float = 5.0, read_timeout: float = 3.0,
                 parent=None):
        super().__init__(parent)
        # FIX P0-3: _host/_port/_reconnect_flag paylaşımlı → Lock ile koru
        self._addr_lock      = threading.Lock()
        self._host           = host
        self._port           = port
        # FIX P1-4: Timeout değerleri parametre
        self._connect_timeout = connect_timeout
        self._read_timeout    = read_timeout
        self._enabled        = False
        self._active         = False
        self._reconnect_flag = False
        self.heartbeat_counter: int = 0

    # ── Kontrol metodları (main thread'den çağrılır) ───────────────────────────

    def enable(self, host: str, port: int) -> None:
        """Kullanıcı 'RPi Bağlan' butonuna bastı — bağlanmaya başla."""
        with self._addr_lock:
            self._host    = host
            self._port    = port
            self._enabled = True
        logger.info("RPi status etkinleştirildi: %s:%d", host, port)

    def disable(self) -> None:
        """Kullanıcı 'RPi Kes' butonuna bastı — bağlantıyı durdur."""
        with self._addr_lock:
            self._enabled = False
            self._active  = False
        self.rpi_connected.emit(False)
        logger.info("RPi status devre dışı bırakıldı.")

    def reconnect(self, host: str, port: int) -> None:
        """
        Ayarlar değişince yeni adrese güncelle.
        FIX P0-3: Lock altında host/port ve flag aynı anda güncellenir.
        """
        with self._addr_lock:
            self._host           = host
            self._port           = port
            self._reconnect_flag = True
        logger.info("RPi status hedefi güncellendi: %s:%d", host, port)

    # ── Worker döngüsü ─────────────────────────────────────────────────────────

    def run(self) -> None:
        while not self.isInterruptionRequested():
            self.heartbeat_counter += 1

            with self._addr_lock:
                enabled = self._enabled

            if not enabled:
                time.sleep(0.5)
                continue

            try:
                self._connect_and_read()
            except Exception as exc:
                if self._active:
                    self._active = False
                    self.rpi_connected.emit(False)
                with self._addr_lock:
                    still_enabled = self._enabled
                if still_enabled:
                    logger.warning(
                        "RPi status bağlantısı kesildi (%s:%d): %s — %.0fs sonra yeniden deneniyor",
                        self._host, self._port, exc, _RECONNECT_DELAY,
                    )

            # Reconnect bekleme — her 100 ms'de interrupt/disable kontrolü
            for _ in range(int(_RECONNECT_DELAY * 10)):
                if self.isInterruptionRequested():
                    return
                with self._addr_lock:
                    if not self._enabled:
                        break
                self.heartbeat_counter += 1
                time.sleep(0.1)

    def _connect_and_read(self) -> None:
        # FIX P0-3: Bağlantı anında adresi Lock altında al — reconnect sırasında tutarlı
        with self._addr_lock:
            host = self._host
            port = self._port

        with socket.create_connection((host, port), timeout=self._connect_timeout) as sock:
            logger.info("RPi StatusBroadcaster bağlandı: %s:%d", host, port)
            self._active = True
            self.rpi_connected.emit(True)
            sock.settimeout(self._read_timeout)

            buf = b""
            # FIX P1-5: Ardışık JSON hata sayacı
            json_error_count = 0

            while not self.isInterruptionRequested():
                with self._addr_lock:
                    if not self._enabled:
                        break

                self.heartbeat_counter += 1

                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue

                if not chunk:
                    break

                buf += chunk

                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        json_error_count = 0          # Başarılı parse → sayacı sıfırla
                        self.rpi_status_updated.emit(data)

                        # ── Health Alerts (FAILED/CRITICAL) ──────────────────
                        for alert in data.get("health_alerts", []):
                            lvl = alert.get("level", "")
                            if lvl in ("FAILED", "CRITICAL"):
                                self.health_alert.emit(
                                    alert.get("subsystem", "?"),
                                    lvl,
                                    alert.get("message", ""),
                                )

                        # ── Mission Notifications ─────────────────────────────
                        for notif in data.get("notifications", []):
                            msg = notif.get("msg", "") if isinstance(notif, dict) else str(notif)
                            if msg:
                                self.mission_notification.emit(msg)

                    except json.JSONDecodeError as exc:
                        json_error_count += 1
                        logger.debug("JSON parse hatası (%d/%d): %s",
                                     json_error_count, _JSON_ERROR_THRESHOLD, exc)
                        # FIX P1-5: Eşik aşılırsa bağlantıyı yenile
                        if json_error_count >= _JSON_ERROR_THRESHOLD:
                            msg = (f"RPi veri akışında {json_error_count} ardışık JSON hatası "
                                   f"— yeniden bağlanılıyor")
                            logger.warning(msg)
                            self.error_occurred.emit(msg)
                            return   # _connect_and_read'den çık → dış döngü reconnect yapar

                # FIX P0-3: Flag'i Lock altında güvenli oku
                with self._addr_lock:
                    should_reconnect = self._reconnect_flag
                    if should_reconnect:
                        self._reconnect_flag = False
                        host = self._host   # yeni adresi al
                        port = self._port

                if should_reconnect:
                    logger.info("RPi reconnect bayrağı — socket kapatılıyor")
                    break

        self._active = False
        self.rpi_connected.emit(False)

    def stop(self) -> None:
        self.requestInterruption()
        self.wait(3000)
