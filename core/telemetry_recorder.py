import logging
import math
import threading
import time
import json
import os
from pathlib import Path
from PyQt6.QtCore import QObject, QTimer, pyqtSlot

from core import local_storage
from core.global_state import state

logger = logging.getLogger(__name__)

# Bu kadar saniyede bir, uçuş sırasında buffer diske "kurtarma" dosyasına
# yazılır. Önceden buffer SADECE temiz bir armed→disarmed geçişinde diske
# yazılıyordu — bağlantı kaybı/çökme (tam olarak kaza öncesi en değerli anlar)
# durumunda bellekteki tüm veri kaybolurdu. Artık en kötü ihtimalle bu kadar
# saniyelik veri kaybedilir.
AUTOSAVE_INTERVAL_MS = 20_000


class TelemetryRecorder(QObject):
    def __init__(self, db_conn, parent=None):
        super().__init__(parent)
        self._db_conn = db_conn
        self._is_recording = False
        self._ts_start = 0
        self._mission_type = "UNKNOWN"
        self._buffer = []

        self._max_speed = 0.0
        self._min_voltage = 999.0
        self._max_roll = 0.0
        self._max_pitch = 0.0

        # Save directory
        self._save_dir = Path(os.environ.get("APPDATA", Path.home())) / "ASHINA-YKI" / "telemetry_logs"
        self._save_dir.mkdir(parents=True, exist_ok=True)

        self._last_armed = False

        # Periyodik autosave — sadece kayıt sırasında çalışır (bkz. _start/_stop_recording)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.timeout.connect(self._autosave)

    @pyqtSlot(dict)
    def on_telemetry(self, data: dict):
        # Otomatik Başlatma/Durdurma
        armed = data.get("armed", False)
        if armed and not self._last_armed:
            self._start_recording(data.get("flight_mode", "AUTO"))
        elif not armed and self._last_armed:
            self._stop_recording()
        
        self._last_armed = armed

        if not self._is_recording:
            return

        # Zaman (relatif sn)
        ts_rel = time.time() - self._ts_start
        
        # Değerleri al (doğru key'lerle)
        spd = data.get("groundspeed", 0.0)
        volt = data.get("battery_voltage", 0.0)
        
        # Roll ve Pitch radyan gelir, dereceye çevir
        r_rad = data.get("roll", 0.0)
        p_rad = data.get("pitch", 0.0)
        roll = abs(math.degrees(r_rad))
        pitch = abs(math.degrees(p_rad))
        
        alt = data.get("alt", 0.0)
        
        vib_x = data.get("vib_x", 0.0)
        vib_y = data.get("vib_y", 0.0)
        vib_z = data.get("vib_z", 0.0)
        pid_err = data.get("pid_err", 0.0)
        
        esc_rpm = data.get("esc_rpm", [0, 0, 0, 0])
        esc_temp = data.get("esc_temp", [0, 0, 0, 0])
        
        # Min/Max güncelle
        self._max_speed = max(self._max_speed, spd)
        if volt > 0:
            self._min_voltage = min(self._min_voltage, volt)
        self._max_roll = max(self._max_roll, roll)
        self._max_pitch = max(self._max_pitch, pitch)
        
        # Grafik verisi
        point = {
            "ts": round(ts_rel, 2),
            "spd": round(spd, 2),
            "volt": round(volt, 2),
            "alt": round(alt, 2),
            "roll": round(math.degrees(r_rad), 2),
            "pitch": round(math.degrees(p_rad), 2),
            "vib_x": round(vib_x, 2),
            "vib_y": round(vib_y, 2),
            "vib_z": round(vib_z, 2),
            "pid_err": round(pid_err, 2),
            "esc_rpm": esc_rpm[:4] if len(esc_rpm) >= 4 else [0,0,0,0],
            "esc_temp": esc_temp[:4] if len(esc_temp) >= 4 else [0,0,0,0]
        }
        self._buffer.append(point)

    def _start_recording(self, mission_type: str):
        logger.info(f"Telemetri kaydı başladı: {mission_type}")
        self._is_recording = True
        self._ts_start = int(time.time())
        self._mission_type = mission_type
        self._buffer.clear()
        self._max_speed = 0.0
        self._min_voltage = 999.0
        self._max_roll = 0.0
        self._max_pitch = 0.0
        self._autosave_timer.start()

    def _autosave_path(self) -> Path:
        return self._save_dir / f"flight_{self._ts_start}.autosave.json"

    def _autosave(self) -> None:
        """
        Periyodik kurtarma yazımı — bağlantı kopması/çökme gibi TEMİZ disarm
        olmayan bir bitişte bile en fazla AUTOSAVE_INTERVAL_MS kadar veri
        kaybedilsin diye. Diske yazma arka plan thread'inde yapılır (UI
        thread'ini bloklamasın).
        """
        if not self._is_recording or len(self._buffer) < 10:
            return
        snapshot = list(self._buffer)   # sığ kopya — sadece append edildiği için güvenli
        path = self._autosave_path()

        def _write():
            try:
                tmp = path.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f)
                os.replace(tmp, path)   # atomik değiştirme — yarım yazılmış dosya kalmasın
            except Exception as exc:
                logger.warning(f"Telemetri autosave yazılamadı: {exc}")

        threading.Thread(target=_write, daemon=True, name="telemetry-autosave").start()

    def _stop_recording(self):
        if not self._is_recording:
            return
        self._is_recording = False
        self._autosave_timer.stop()
        ts_end = int(time.time())

        if len(self._buffer) < 10:
            logger.info("Çok kısa uçuş (10 noktadan az), kaydedilmedi.")
            self._cleanup_autosave()
            return

        logger.info(f"Telemetri kaydı bitti. {len(self._buffer)} nokta.")

        filename = f"flight_{self._ts_start}.json"
        filepath = self._save_dir / filename
        snapshot = list(self._buffer)
        min_v = self._min_voltage if self._min_voltage < 999 else 0.0
        ts_start, mission_type = self._ts_start, self._mission_type
        max_speed, max_roll, max_pitch = self._max_speed, self._max_roll, self._max_pitch

        def _write():
            # F2 düzeltmesi: bu yazma önceden UI thread'inde SENKRON çalışıyordu —
            # tam disarm/iniş anında (operatörün en çok tepkisel UI istediği an)
            # büyük bir buffer için json.dump + DB yazımı arayüzü kilitleyebiliyordu.
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f)

                local_storage.save_flight_analysis(
                    self._db_conn,
                    ts_start,
                    ts_end,
                    mission_type,
                    max_speed,
                    min_v,
                    max_roll,
                    max_pitch,
                    str(filepath)
                )
                logger.info(f"Uçuş analizi DB'ye eklendi: {filepath}")
                self._cleanup_autosave()
            except Exception as e:
                logger.error(f"Uçuş analizi kaydedilemedi: {e}")

        threading.Thread(target=_write, daemon=True, name="telemetry-final-save").start()

    def _cleanup_autosave(self) -> None:
        """Uçuş düzgün bitip kalıcı dosya yazıldıktan sonra kurtarma dosyasına gerek kalmaz."""
        try:
            self._autosave_path().unlink(missing_ok=True)
        except Exception:
            pass
