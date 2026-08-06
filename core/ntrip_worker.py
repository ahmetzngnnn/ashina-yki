"""
ntrip_worker.py — RTK RTCM İnjeksiyonu
Ham TCP socket ile NTRIP/1.0 protokolü (requests kullanılmaz).
El ile HTTP/1.0 GET + Basic Auth header.
"""
import base64
import logging
import socket
import time
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

CHUNK_SIZE = 255


class NtripWorker(QThread):
    rtcm_ready = pyqtSignal(bytes)
    connection_changed = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        host: str = "",
        port: int = 2101,
        mountpoint: str = "",
        username: str = "",
        password: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._host = host
        self._port = port
        self._mountpoint = mountpoint
        self._username = username
        self._password = password
        self.heartbeat_counter: int = 0

    def configure(self, host: str, port: int, mountpoint: str, username: str, password: str) -> None:
        self._host = host
        self._port = port
        self._mountpoint = mountpoint
        self._username = username
        self._password = password

    def run(self) -> None:
        while not self.isInterruptionRequested():
            # Watchdog'un "takıldı" zannetmemesi için her döngüde artır
            self.heartbeat_counter += 1
            try:
                self._connect_and_stream()
            except Exception as exc:
                logger.error("NtripWorker exception: %s", exc)
                self.error_occurred.emit(str(exc))
                self.connection_changed.emit(False)
                time.sleep(2)

    def _connect_and_stream(self) -> None:
        if not self._host or not self._mountpoint:
            time.sleep(1)   # Host ayarlı değil — bekle, heartbeat dışarıda artıyor
            return

        credentials = base64.b64encode(
            f"{self._username}:{self._password}".encode()
        ).decode()

        request = (
            f"GET /{self._mountpoint} HTTP/1.0\r\n"
            f"Host: {self._host}\r\n"
            f"Ntrip-Version: Ntrip/1.0\r\n"
            f"User-Agent: ASHINA-YKI/2.0\r\n"
            f"Authorization: Basic {credentials}\r\n"
            f"\r\n"
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(15)
        sock.connect((self._host, self._port))
        sock.sendall(request.encode())

        # "ICY 200 OK" yanıtını bekle
        response_buf = b""
        while b"\r\n\r\n" not in response_buf:
            chunk = sock.recv(256)
            if not chunk:
                raise ConnectionError("NTRIP sunucu yanıtı alınamadı")
            response_buf += chunk

        header = response_buf.split(b"\r\n\r\n")[0].decode(errors="replace")
        if "ICY 200 OK" not in header and "200 OK" not in header:
            raise ConnectionError(f"NTRIP bağlantı reddedildi: {header[:100]}")

        logger.info("NTRIP bağlandı: %s/%s", self._host, self._mountpoint)
        self.connection_changed.emit(True)

        # Artık bağlantı canlı; RTCM3 baytlarını oku
        sock.settimeout(5)
        try:
            while not self.isInterruptionRequested():
                self.heartbeat_counter += 1
                try:
                    data = sock.recv(CHUNK_SIZE)
                    if not data:
                        raise ConnectionError("NTRIP bağlantısı kapandı")
                    self.rtcm_ready.emit(data)
                except socket.timeout:
                    continue
        finally:
            sock.close()

    def stop(self) -> None:
        self.requestInterruption()
        self.wait()
