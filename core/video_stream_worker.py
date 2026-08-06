"""
video_stream_worker.py — Video Alıcı (YKI tarafı sıfır işlem)
==============================================================
YKI yalnızca RPi'dan gelen JPEG'leri alır ve Qt'ye verir.
Hiçbir görüntü işlemi (renk dönüşümü, cv2, numpy) yapılmaz.

Desteklenen kaynaklar:
  - "none" / boş   : Pasif
  - "http://..."   : MJPEG HTTP stream (Content-Length tabanlı parser)
  - "0", "1" ...   : Yerel webcam (cv2, yalnızca test için)
"""
import logging
import time
import urllib.request
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui  import QImage

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

logger = logging.getLogger(__name__)

_MAX_RETRY_DELAY = 10
_MIN_RETRY_DELAY = 1
_READ_CHUNK      = 65536   # 64 KB
_MAX_BUF         = 524288  # 512 KB — taşma koruması


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
        self._retry_delay  = retry_delay
        self.heartbeat_counter: int = 0

    def set_source(self, source: str) -> None:
        self._source = source

    def run(self) -> None:
        retry = _MIN_RETRY_DELAY
        while not self.isInterruptionRequested():
            self.heartbeat_counter += 1
            src = self._source.strip()
            if not src or src.lower() == "none":
                retry = _MIN_RETRY_DELAY
                time.sleep(1)
                continue
            try:
                if src.startswith("http://") or src.startswith("https://"):
                    self._recv_mjpeg(src)
                else:
                    self._recv_webcam(src)
                retry = _MIN_RETRY_DELAY
            except Exception as exc:
                logger.warning("Video bağlanamadı (%s): %s — %ds sonra tekrar",
                               src, exc, retry)
                self.error_occurred.emit(str(exc))
                self.frame_ready.emit(QImage())
                for _ in range(retry):
                    if self.isInterruptionRequested():
                        return
                    time.sleep(1)
                retry = min(retry * 2, _MAX_RETRY_DELAY)

    # ── HTTP MJPEG Alıcı — Content-Length tabanlı, sıfır işlem ──────────────
    def _recv_mjpeg(self, url: str) -> None:
        """
        MJPEG stream'den JPEG baytlarını alır.
        Content-Length başlığı varsa tam frame boyutunu kullanır (güvenilir).
        YKI tarafında hiçbir piksel işlemi yapılmaz — Qt kendi JPEG decoder'ını kullanır.
        """
        try:
            req = urllib.request.urlopen(url, timeout=self._open_timeout)
        except Exception as e:
            raise ConnectionError(f"HTTP bağlanamadı: {e}")

        logger.info("MJPEG alıcı başladı: %s", url)
        buf = b""
        _HEND = b"\r\n\r\n"

        while not self.isInterruptionRequested():
            self.heartbeat_counter += 1
            try:
                chunk = req.read(_READ_CHUNK)
            except Exception:
                self.frame_ready.emit(QImage())
                break
            if not chunk:
                self.frame_ready.emit(QImage())
                break
            buf += chunk

            # Taşma koruması — en son SOI'den başa sar
            if len(buf) > _MAX_BUF:
                soi = buf.rfind(b"\xff\xd8")
                buf = buf[soi:] if soi >= 0 else b""
                continue

            # Tampondaki tüm tam kareleri işle
            while True:
                h_end = buf.find(_HEND)
                if h_end < 0:
                    break

                # Content-Length'i ayrıştır
                header_raw = buf[:h_end]
                cl = 0
                for line in header_raw.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        try:
                            cl = int(line.split(b":", 1)[1].strip())
                        except ValueError:
                            pass
                        break

                frame_start = h_end + 4  # \r\n\r\n'yi atla

                if cl > 0:
                    # Tam veri geldi mi?
                    if len(buf) < frame_start + cl:
                        break
                    jpeg = buf[frame_start:frame_start + cl]
                    buf  = buf[frame_start + cl:]
                    if buf.startswith(b"\r\n"):
                        buf = buf[2:]
                else:
                    # Fallback: JPEG SOI/EOI işaretçileri
                    soi = buf.find(b"\xff\xd8", frame_start)
                    if soi < 0:
                        break
                    eoi = buf.find(b"\xff\xd9", soi + 2)
                    if eoi < 0:
                        break
                    jpeg = buf[soi:eoi + 2]
                    buf  = buf[eoi + 2:]

                # Piksel işlemi yok — Qt JPEG decoder doğrudan
                q = QImage()
                if q.loadFromData(jpeg):
                    self.frame_ready.emit(q)

    # ── Yerel Webcam (yalnızca test, cv2 gerektirir) ─────────────────────────
    def _recv_webcam(self, source: str) -> None:
        if not CV2_AVAILABLE:
            time.sleep(1)
            return
        import numpy as np
        try:
            cap_src = int(source)
        except ValueError:
            cap_src = source
        cap = cv2.VideoCapture(cap_src)
        if not cap.isOpened():
            raise ConnectionError(f"Webcam açılamadı: {source}")
        logger.info("Webcam başladı: %s", source)
        interval = 1.0 / 30
        last_t   = time.monotonic()
        try:
            while not self.isInterruptionRequested():
                self.heartbeat_counter += 1
                ret, frame_bgr = cap.read()
                if not ret:
                    break
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                q = QImage(bytes(rgb.data), w, h, ch * w,
                           QImage.Format.Format_RGB888).copy()
                self.frame_ready.emit(q)
                s = interval - (time.monotonic() - last_t)
                if s > 0:
                    time.sleep(s)
                last_t = time.monotonic()
        finally:
            cap.release()

    def stop(self) -> None:
        self.requestInterruption()
        if not self.wait(3000):
            self.terminate()
