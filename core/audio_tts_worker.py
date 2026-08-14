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


def _piper_yukle():
    """Türkçe nöral sesi (Piper) yükler. Yoksa None döner.

    NEDEN Piper: espeak-ng ücretsiz ve çevrimdışı ama sesi belirgin biçimde
    robotik; uzun görev anlatımını dinlemek yorucu. Piper nöral bir model
    kullanır, yine ücretsiz ve TAMAMEN ÇEVRİMDIŞIDIR (saha şartı). Türkçe
    model assets/tts/ altında aranır; yoksa sistem sessizce eski motora düşer
    — model dosyası depoya konmadığı için kurulum yapmayan makinede de YKİ
    çalışmaya devam eder.
    """
    try:
        from pathlib import Path
        from piper import PiperVoice
    except Exception:
        return None
    kok = Path(__file__).resolve().parent.parent / "assets" / "tts"
    for onnx in sorted(kok.glob("*.onnx")):
        if not onnx.with_suffix(".onnx.json").exists():
            continue
        try:
            return PiperVoice.load(str(onnx))
        except Exception as exc:
            print(f"[TTS] Piper modeli yüklenemedi ({onnx.name}): {exc}")
    return None


def _wav_cal(yol: str) -> None:
    """WAV dosyasını sistemin ses aracıyla çalar (Linux: aplay/paplay,
    Windows: winsound). Araç yoksa sessizce geçilir — ses çıkmaması
    uygulamayı durdurmamalı."""
    import platform
    import shutil
    import subprocess
    if platform.system() == "Windows":
        try:
            import winsound
            winsound.PlaySound(yol, winsound.SND_FILENAME)
        except Exception:
            pass
        return
    for arac in ("paplay", "aplay", "pw-play", "ffplay"):
        exe = shutil.which(arac)
        if not exe:
            continue
        cmd = [exe, yol]
        if arac == "aplay":
            cmd = [exe, "-q", yol]
        elif arac == "ffplay":
            cmd = [exe, "-nodisp", "-autoexit", "-loglevel", "quiet", yol]
        try:
            subprocess.run(cmd, check=False, timeout=60)
            return
        except Exception:
            continue


def _tts_process_target(queue: multiprocessing.Queue) -> None:
    """Bu fonksiyon ayrı bir Process içinde çalışır.

    Önce Piper (Türkçe nöral ses) denenir; yoksa pyttsx3/espeak'e düşülür.
    Model yükleme ~3 saniye sürdüğü için süreç başında BİR KEZ yapılır."""
    import os
    import tempfile
    import wave

    voice = _piper_yukle()
    engine = None
    if voice is not None:
        print("[TTS] Türkçe nöral ses (Piper) etkin.")
    else:
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", 160)
            print("[TTS] Piper bulunamadı — espeak/pyttsx3 kullanılıyor.")
        except Exception as exc:
            print(f"[TTS Process] Hiçbir ses motoru başlatılamadı: {exc}")
            return

    while True:
        try:
            msg = queue.get(timeout=0.5)
            if msg is None:  # Durdurmak için None gönderilir
                break
            if voice is not None:
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp.close()
                try:
                    with wave.open(tmp.name, "wb") as w:
                        voice.synthesize_wav(msg, w)
                    _wav_cal(tmp.name)
                finally:
                    try:
                        os.unlink(tmp.name)
                    except OSError:
                        pass
            else:
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
