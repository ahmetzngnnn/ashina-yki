import logging
import time
import json
import os
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSlot

from core import local_storage
from core.global_state import state

logger = logging.getLogger(__name__)

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

        import math
        
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

    def _stop_recording(self):
        if not self._is_recording:
            return
        self._is_recording = False
        ts_end = int(time.time())
        
        if len(self._buffer) < 10:
            logger.info("Çok kısa uçuş (10 noktadan az), kaydedilmedi.")
            return
            
        logger.info(f"Telemetri kaydı bitti. {len(self._buffer)} nokta.")
        
        # Diske yaz
        filename = f"flight_{self._ts_start}.json"
        filepath = self._save_dir / filename
        
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self._buffer, f)
                
            # Veritabanına kaydet
            min_v = self._min_voltage if self._min_voltage < 999 else 0.0
            
            local_storage.save_flight_analysis(
                self._db_conn,
                self._ts_start,
                ts_end,
                self._mission_type,
                self._max_speed,
                min_v,
                self._max_roll,
                self._max_pitch,
                str(filepath)
            )
            logger.info(f"Uçuş analizi DB'ye eklendi: {filepath}")
        except Exception as e:
            logger.error(f"Uçuş analizi kaydedilemedi: {e}")
