"""core/rpi_status_worker.py — RPi companion durum akışı alıcısı."""
import json
import logging
import socket
import threading
import time
from collections import deque

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

_RECONNECT_DELAY     = 5.0
# FIX P1-5: Ardışık JSON hata eşiği — bu kadar hatadan sonra reconnect
_JSON_ERROR_THRESHOLD = 5
# Bu kadar süre GEÇERLİ veri (başarılı JSON parse) gelmezse bağlantı "donmuş"
# sayılır ve yeniden kurulur. Önceden `except socket.timeout: continue` sonsuza
# dek dönüyordu — soket açık kalıp companion sessizce yanıt vermeyi kessin
# (ör. companion süreci kilitlendi/çöktü ama TCP bağlantısı hâlâ ayakta), UI
# son bilinen değerleri CANLIYMIŞ GİBİ göstermeye devam ediyordu. Broadcaster
# normalde ~1s'de bir yayın yapar (status_server.py); 10s bu değerin güvenli
# bir katı — normal jitter'ı tolere eder ama gerçek donmayı hızlı yakalar.
_STALE_TIMEOUT_S = 10.0


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

    def __init__(self, host: str = "192.168.1.10", port: int = 5602,
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
        # Aynı failsafe mesajının tekrar tekrar gösterilmesini engelle
        self._alert_last_emit: dict = {}   # key=(subsystem,level,message) → timestamp
        self._ALERT_COOLDOWN = 30.0        # saniye
        # LAN gecikme göstergesi (ping_ms).
        # KRİTİK DÜZELTME: ÖNCEDEN her 8 saniyede aynı 5602 DURUM portuna İKİNCİ
        # bir TCP bağlantısı açılıp kapatılarak RTT ölçülüyordu. Companion
        # status_server her YENİ istemciye anında durum üretiyor ve o üretimde
        # görev bildirimleri TAHRİBATLI okunuyordu (get_notifications → clear)
        # → bildirim bu kısa ömürlü ölçüm bağlantısına yazılıp ÇÖPE gidiyor,
        # operatör kritik bildirimi KAYBEDİYORDU.
        # ÇÖZÜM: ölçüm için ARTIK YENİ BAĞLANTI AÇILMIYOR. Zaten açık olan durum
        # soketinde ardışık mesajlar arası gecikme ölçülüyor; yayın periyodunun
        # üzerine binen EK gecikme "ping_ms" olarak raporlanıyor (sinyal adı ve
        # alan şeması KORUNDU — UI bağlı: dashboard_view.update_rpi_status).
        self._last_ping_ms: float | None = None
        self._last_msg_ts:  float | None = None      # son geçerli mesaj zamanı
        self._delta_hist:   deque = deque(maxlen=30) # son mesaj aralıkları (ms)

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
            # Yeni bağlantı → gecikme ölçümü geçmişini sıfırla (kopukluk süresi
            # yanlışlıkla "gecikme" olarak raporlanmasın).
            self._last_msg_ts = None
            self._delta_hist.clear()
            # FIX P1-5: Ardışık JSON hata sayacı
            json_error_count = 0
            # Sessiz-donma tespiti: son GEÇERLİ veri ne zaman geldi (bkz. _STALE_TIMEOUT_S)
            last_data_ts = time.monotonic()

            while not self.isInterruptionRequested():
                with self._addr_lock:
                    if not self._enabled:
                        break

                self.heartbeat_counter += 1

                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    if time.monotonic() - last_data_ts > _STALE_TIMEOUT_S:
                        logger.warning(
                            "RPi status bağlantısı %.0fs süredir veri vermiyor "
                            "(soket açık ama sessiz) — yeniden bağlanılıyor",
                            _STALE_TIMEOUT_S)
                        self.error_occurred.emit(
                            f"RPi bağlantısı {_STALE_TIMEOUT_S:.0f}s süredir sessiz — yeniden bağlanılıyor")
                        break   # dış döngü reconnect yapar (self._active=False + rpi_connected(False) asagida)
                    continue

                if not chunk:
                    break

                buf += chunk

                # Tampon taşma koruması. NEDEN: yanlış porta bağlanılırsa
                # (ör. video akışı 8080) gelen veride hiç "\n" olmayabilir →
                # buf sınırsız büyür (bellek şişer) ve satır işlenmediği için
                # "sessiz-donma" koruması da devreye GİRMEZ (recv veri aldığı
                # sürece timeout oluşmaz). 1 MB'ı aşarsa tamponu at ve yeniden bağlan.
                if len(buf) > 1_000_000:
                    msg = ("RPi durum akışında 1 MB'tan fazla satırsız veri — "
                           "yanlış port olabilir, tampon atılıp yeniden bağlanılıyor")
                    logger.warning(msg)
                    self.error_occurred.emit(msg)
                    buf = b""
                    break   # dış döngü reconnect yapar

                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        json_error_count = 0          # Başarılı parse → sayacı sıfırla
                        last_data_ts = time.monotonic()  # sessiz-donma tespiti için
                        # LAN gecikmesi: MEVCUT soketteki veri geliş aralığından
                        # türetilir — ayrı bağlantı AÇILMAZ (bkz. __init__ notu).
                        self._update_link_latency(time.monotonic())
                        if self._last_ping_ms is not None:
                            data["ping_ms"] = self._last_ping_ms
                        self.rpi_status_updated.emit(data)

                        # ── Health Alerts (FAILED/CRITICAL) ──────────────────
                        now = time.monotonic()
                        active_keys: set = set()
                        for alert in data.get("health_alerts", []):
                            lvl = alert.get("level", "")
                            if lvl in ("FAILED", "CRITICAL"):
                                sub = alert.get("subsystem", "?")
                                msg = alert.get("message", "")
                                key = (sub, lvl, msg)
                                active_keys.add(key)
                                last = self._alert_last_emit.get(key, 0.0)
                                if now - last >= self._ALERT_COOLDOWN:
                                    self._alert_last_emit[key] = now
                                    self.health_alert.emit(sub, lvl, msg)
                        # Düzelen alertlerin cache'ini temizle (tekrar oluşursa anında göster)
                        for key in list(self._alert_last_emit):
                            if key not in active_keys:
                                del self._alert_last_emit[key]

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

    def _update_link_latency(self, now: float) -> None:
        """Bağlantı gecikmesini MEVCUT durum soketinden türet (ms).

        Companion sabit periyotla yayın yapar (status_server, ~1 s). Gözlenen
        en küçük mesaj aralığı pratikte bu nominal periyottur; anlık aralığın
        nominalin ÜZERİNE binen kısmı = hattın eklediği gecikme/jitter.
        Bu ölçüm için EK TCP BAĞLANTISI AÇILMAZ — eski ping ölçümü 5602'ye
        ikinci bir bağlantı açtığı için görev bildirimlerini çalıyordu.
        Alan adı ("ping_ms") ve sinyal şeması korunur (UI bağlı).
        """
        prev = self._last_msg_ts
        self._last_msg_ts = now
        if prev is None:
            return
        delta_ms = (now - prev) * 1000.0
        if delta_ms <= 0:
            return
        self._delta_hist.append(delta_ms)
        baseline = min(self._delta_hist)          # ≈ companion yayın periyodu
        self._last_ping_ms = round(max(0.0, delta_ms - baseline), 1)

    def stop(self) -> None:
        self.requestInterruption()
        self.wait(3000)
