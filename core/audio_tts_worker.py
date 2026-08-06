"""
audio_tts_worker.py — Sesli Uyarı
pyttsx3 ayrı multiprocessing.Process içinde çalışır (Qt deadlock riski önlenir).
Ana süreç ile multiprocessing.Queue üzerinden haberleşir.
Kuyruk yapısı: FIFO; üst üste binen sesler önlenir.
"""
import logging
import multiprocessing
import time
from PyQt6.QtCore import QThread, pyqtSlot

logger = logging.getLogger(__name__)


def _tts_process_target(queue: multiprocessing.Queue) -> None:
    """Bu fonksiyon ayrı bir Process içinde çalışır."""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 160)
    except Exception as exc:
        print(f"[TTS Process] pyttsx3 başlatılamadı: {exc}")
        return

    while True:
        try:
            msg = queue.get(timeout=0.5)
            if msg is None:  # Durdurmak için None gönderilir
                break
            engine.say(msg)
            engine.runAndWait()
        except KeyboardInterrupt:
            break
        except Exception:
            pass  # timeout veya diğer hatalar sessizce geçilir


class AudioTTSWorker(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: multiprocessing.Queue = multiprocessing.Queue()
        self._process: multiprocessing.Process | None = None
        self.heartbeat_counter: int = 0
        self._last_spoken: str = ""
        self._last_spoken_time: float = 0.0
        self._DEDUP_SECS = 3.0   # aynı mesaj bu süre içinde tekrar çalınmaz

    def start_tts_process(self) -> None:
        self._process = multiprocessing.Process(
            target=_tts_process_target,
            args=(self._queue,),
            daemon=True,
        )
        self._process.start()
        logger.info("AudioTTS process başlatıldı (PID: %s)", self._process.pid)

    def run(self) -> None:
        # Bu QThread yalnızca watchdog heartbeat'ini artırır.
        while not self.isInterruptionRequested():
            self.heartbeat_counter += 1
            time.sleep(0.5)

    @pyqtSlot(str)
    def speak(self, message: str) -> None:
        now = time.time()
        # Aynı mesaj DEDUP_SECS içinde tekrar gelirse sessizce atla
        if (message == self._last_spoken
                and now - self._last_spoken_time < self._DEDUP_SECS):
            return
        self._last_spoken = message
        self._last_spoken_time = now
        try:
            self._queue.put_nowait(message)
        except Exception as exc:
            logger.warning("TTS kuyruğu doldu veya hata: %s", exc)

    def stop(self) -> None:
        self.requestInterruption()
        try:
            self._queue.put_nowait(None)  # Process'i durdur
        except Exception:
            pass
        if self._process and self._process.is_alive():
            self._process.join(timeout=3)
            if self._process.is_alive():
                self._process.terminate()
        self.wait()
