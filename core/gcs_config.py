"""
gcs_config.py — GCS Merkezi Bağlantı Konfigürasyonu

Tüm IP/port ayarları buradan okunur.
settings.json varsa üzerine yazar, yoksa varsayılanlar kullanılır.
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# PyInstaller paketlemesinde __file__ _MEIPASS geçici dizinine işaret eder
# (yazılamaz). Ayar dosyası her zaman %APPDATA%\ASHINA-YKI'da tutulur.
_APP_DATA_DIR  = Path(os.environ.get("APPDATA", Path.home())) / "ASHINA-YKI"
_APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
_SETTINGS_FILE = str(_APP_DATA_DIR / "settings.json")

# Geliştirme ortamı: proje kökündeki settings.json varsa onu oku ama
# kaydetme hedefi yine de APPDATA'dır.
_DEV_SETTINGS = os.path.join(os.path.dirname(__file__), "..", "settings.json")
if not os.path.exists(_SETTINGS_FILE) and os.path.exists(_DEV_SETTINGS):
    import shutil
    shutil.copy(_DEV_SETTINGS, _SETTINGS_FILE)

DEFAULTS: dict = {
    "companion_host":         "192.168.1.229",   # İHA RPi — yardımcı bilgisayar + video (8080/8081)
    "companion_mission_port": 5601,
    "companion_status_port":  5602,
    "companion_video_port":   8081,          # İşlenmiş video (görev sırasında, vision annotated)
    "companion_video_raw_port": 8080,        # Ham kamera (her zaman açık)
    "mavlink_host":           "192.168.1.229",   # MAVLink de aynı RPi üzerinden (port 5763)
    "mavlink_port":           5763,
    # FIX P1-4: Timeout değerleri artık configurable
    "ai_connect_timeout":     5.0,           # AISocketWorker TCP bağlantı timeout (s)
    "ai_response_timeout":    5.0,           # AISocketWorker yanıt bekleme timeout (s)
    "rpi_connect_timeout":    5.0,           # RpiStatusWorker TCP bağlantı timeout (s)
    "rpi_read_timeout":       3.0,           # RpiStatusWorker recv timeout (s)
    "video_open_timeout":     5.0,           # VideoStreamWorker HTTP bağlantı timeout (s)
    "video_retry_delay":      10,            # VideoStreamWorker yeniden deneme aralığı (s)

    # ── Anten Takip Sistemi (RUTX10 + Pixhawk 2.4.8 gimbal) ────────────────
    "antenna_tracker_enabled":  False,       # Anten takip aktif mi (kullanıcı açar)
    "antenna_pixhawk_conn":     "udp:0.0.0.0:14560",  # Anten Pixhawk MAVLink adresi (RUTX10 üzerinden)
    "antenna_tracker_mode":     "forward",   # "forward" (ArduPilot AntennaTracker) | "compute" (YKİ hesaplar)
    "antenna_home_lat":         0.0,         # Anten sabit konumu (0 → otomatik Pixhawk GPS)
    "antenna_home_lon":         0.0,
    "antenna_home_alt":         0.0,
    "antenna_min_distance_m":   3.0,         # Bu mesafeden yakında anteni oynatma (gürültü önleme)
    "antenna_update_hz":        5.0,         # Takip güncelleme hızı
}


class GCSConfig:
    def __init__(self):
        self._data: dict = dict(DEFAULTS)
        self._load()

    # ── Okuma / Yazma ────────────────────────────────────────────────
    def _load(self) -> None:
        path = os.path.abspath(_SETTINGS_FILE)
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:  # utf-8-sig: BOM varsa siler, yoksa normal okur
                saved = json.load(f)
            self._data.update({k: v for k, v in saved.items() if k in DEFAULTS})
            logger.info("Ayarlar yuklendi: %s", path)
        except Exception as e:
            logger.warning("Ayar dosyasi okunamadi: %s", e)

    def save(self) -> None:
        path = os.path.abspath(_SETTINGS_FILE)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:  # BOM'suz, Unix satır sonu
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            logger.info("Ayarlar kaydedildi: %s", path)
        except Exception as e:
            logger.error("Ayar dosyasi kaydedilemedi: %s", e)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        if key in DEFAULTS:
            self._data[key] = value

    def all(self) -> dict:
        return dict(self._data)

    # ── Kolaylık özellikleri ─────────────────────────────────────────
    @property
    def companion_host(self) -> str:
        return str(self._data["companion_host"])

    @property
    def companion_mission_port(self) -> int:
        return int(self._data["companion_mission_port"])

    @property
    def companion_status_port(self) -> int:
        return int(self._data["companion_status_port"])

    @property
    def companion_video_port(self) -> int:
        return int(self._data["companion_video_port"])

    @property
    def companion_video_raw_port(self) -> int:
        return int(self._data.get("companion_video_raw_port", 8080))

    # ── Anten Takip property'leri ─────────────────────────────────────────
    @property
    def antenna_tracker_enabled(self) -> bool:
        return bool(self._data.get("antenna_tracker_enabled", False))

    @property
    def antenna_pixhawk_conn(self) -> str:
        return str(self._data.get("antenna_pixhawk_conn", "udp:0.0.0.0:14560"))

    @property
    def antenna_tracker_mode(self) -> str:
        return str(self._data.get("antenna_tracker_mode", "forward"))

    @property
    def antenna_home(self) -> tuple:
        return (float(self._data.get("antenna_home_lat", 0.0)),
                float(self._data.get("antenna_home_lon", 0.0)),
                float(self._data.get("antenna_home_alt", 0.0)))

    @property
    def antenna_min_distance_m(self) -> float:
        return float(self._data.get("antenna_min_distance_m", 3.0))

    @property
    def antenna_update_hz(self) -> float:
        return float(self._data.get("antenna_update_hz", 5.0))

    # FIX P1-4: Timeout property'leri
    @property
    def ai_connect_timeout(self) -> float:
        return float(self._data.get("ai_connect_timeout", 5.0))

    @property
    def ai_response_timeout(self) -> float:
        return float(self._data.get("ai_response_timeout", 5.0))

    @property
    def rpi_connect_timeout(self) -> float:
        return float(self._data.get("rpi_connect_timeout", 5.0))

    @property
    def rpi_read_timeout(self) -> float:
        return float(self._data.get("rpi_read_timeout", 3.0))

    @property
    def video_open_timeout(self) -> float:
        return float(self._data.get("video_open_timeout", 5.0))

    @property
    def video_retry_delay(self) -> int:
        return int(self._data.get("video_retry_delay", 10))

    @property
    def mavlink_host(self) -> str:
        return str(self._data["mavlink_host"])

    @property
    def mavlink_port(self) -> int:
        return int(self._data["mavlink_port"])

    @property
    def mavlink_address(self) -> str:
        return f"{self.mavlink_host}:{self.mavlink_port}"

    @property
    def companion_status_address(self) -> str:
        return f"{self.companion_host}:{self.companion_status_port}"


# Tekil örnek — tüm modüller bunu import eder
config = GCSConfig()
