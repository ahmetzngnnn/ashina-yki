"""
video_stream_worker.py — Video İşleyici
Desteklenen kaynaklar:
  - "none" veya boş  : Pasif mod
  - "0", "1" ...     : /dev/videoN webcam index
  - "http://..."     : MJPEG HTTP stream
  - GStreamer pipeline string
"""
import io
import logging
import time
import urllib.request
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logging.warning("opencv-python kurulu değil — Video devre dışı")

logger = logging.getLogger(__name__)

TARGET_FPS          = 30
_MAX_RETRY_DELAY    = 30   # saniye
_MIN_RETRY_DELAY    = 2    # saniye
_BUF_OVERFLOW_BYTES = 262144  # 256 KB


class VideoStreamWorker(QThread):
    frame_ready    = pyqtSignal(QImage)
    error_occurred = pyqtSignal(str)

    def __init__(self, source: str = "none",
                 open_timeout: float = 5.0,
                 retry_delay: int = 10,
                 parent=None):
        super().__init__(parent)
        self._source       = source
        self._open_timeout = open_timeout
        # FIX P1-4: retry_delay parametre (gcs_config'den beslenebilir)
        self._retry_delay  = retry_delay
        self.heartbeat_counter: int = 0

    def set_source(self, source: str) -> None:
        self._source = source

    def run(self) -> None:
        # FIX P1-1: Exponential backoff — bağlantı sorunlarında giderek artan bekleme
        retry_delay = _MIN_RETRY_DELAY

        while not self.isInterruptionRequested():
            self.heartbeat_counter += 1
            src = self._source.strip()
            if not src or src.lower() == "none":
                retry_delay = _MIN_RETRY_DELAY   # kaynak temizlenince sıfırla
                time.sleep(1)
                continue
            try:
                if src.startswith("http://") or src.startswith("https://"):
                    self._capture_http(src)
                else:
                    self._capture_cv(src)
                # Bağlantı başarılı kapandı (interrupt veya kaynak değişimi) → sıfırla
                retry_delay = _MIN_RETRY_DELAY
            except Exception as exc:
                logger.warning("Video bağlanamadı (%s): %s — %ds sonra tekrar",
                               src, exc, retry_delay)
                self.error_occurred.emit(str(exc))
                # FIX P1-1: Exponential backoff ile bekle
                for _ in range(retry_delay):
                    if self.isInterruptionRequested():
                        return
                    time.sleep(1)
                # Sonraki deneme için süreyi iki katına çıkar (max'a kadar)
                retry_delay = min(retry_delay * 2, _MAX_RETRY_DELAY)

    # ── HTTP MJPEG Parser ──────────────────────────────────────────────────────
    def _capture_http(self, url: str) -> None:
        if not CV2_AVAILABLE:
            time.sleep(1)
            return

        try:
            req = urllib.request.urlopen(url, timeout=self._open_timeout)
        except Exception as e:
            raise ConnectionError(f"HTTP bağlanamadı: {e}")

        ct = req.headers.get("Content-Type", "")
        boundary = b""
        if "boundary=" in ct:
            bnd = ct.split("boundary=")[1].strip().split(";")[0].strip()
            boundary = ("--" + bnd).encode()

        logger.info("HTTP MJPEG akışı başladı: %s (boundary=%r)", url, boundary)
        buf = b""
        interval = 1.0 / TARGET_FPS
        last_frame_t = time.monotonic()

        while not self.isInterruptionRequested():
            self.heartbeat_counter += 1
            try:
                chunk = req.read(32768)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk

            # Buffer overflow koruması
            if len(buf) > _BUF_OVERFLOW_BYTES:
                last_sof = buf.rfind(b"\xff\xd8")
                if last_sof > 0:
                    buf = buf[last_sof:]

            while True:
                start = buf.find(b"\xff\xd8")
                if start < 0:
                    break
                end = buf.find(b"\xff\xd9", start + 2)
                if end < 0:
                    break
                end += 2
                jpeg = buf[start:end]
                buf  = buf[end:]

                arr   = np.frombuffer(jpeg, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                q_img = QImage(bytes(rgb.data), w, h, ch * w,
                               QImage.Format.Format_RGB888).copy()
                self.frame_ready.emit(q_img)

                now     = time.monotonic()
                sleep_t = interval - (now - last_frame_t)
                if sleep_t > 0:
                    time.sleep(sleep_t)
                last_frame_t = time.monotonic()

    # ── OpenCV Yerel Kaynak ───────────────────────────────────────────────────
    def _capture_cv(self, source: str) -> None:
        if not CV2_AVAILABLE:
            time.sleep(1)
            return

        try:
            cap_src = int(source)
        except ValueError:
            cap_src = source

        cap = cv2.VideoCapture(cap_src)
        if not cap.isOpened():
            raise ConnectionError(f"Video kaynağı açılamadı: {source}")

        logger.info("Video akışı başladı: %s", source)
        interval     = 1.0 / TARGET_FPS
        last_frame_t = time.monotonic()
        try:
            while not self.isInterruptionRequested():
                self.heartbeat_counter += 1
                ret, frame_bgr = cap.read()
                if not ret:
                    logger.warning("Kare alınamadı — yeniden bağlanılıyor")
                    break
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                q_img = QImage(bytes(rgb.data), w, h, ch * w,
                               QImage.Format.Format_RGB888).copy()
                self.frame_ready.emit(q_img)
                sleep_t = interval - (time.monotonic() - last_frame_t)
                if sleep_t > 0:
                    time.sleep(sleep_t)
                last_frame_t = time.monotonic()
        finally:
            cap.release()

    def stop(self) -> None:
        self.requestInterruption()
        if not self.wait(3000):
            self.terminate()
