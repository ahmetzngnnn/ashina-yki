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
# (yazılamaz). Ayar dosyası platforma göre yazılabilir veri klasöründe tutulur
# (Win:%APPDATA%, Linux:XDG ~/.local/share, macOS:App Support).
from core.apppaths import app_data_dir
_APP_DATA_DIR  = app_data_dir()
_SETTINGS_FILE = str(_APP_DATA_DIR / "settings.json")

# Geliştirme ortamı: proje kökündeki settings.json varsa onu oku ama
# kaydetme hedefi yine de APPDATA'dır.
_DEV_SETTINGS = os.path.join(os.path.dirname(__file__), "..", "settings.json")
if not os.path.exists(_SETTINGS_FILE) and os.path.exists(_DEV_SETTINGS):
    import shutil
    shutil.copy(_DEV_SETTINGS, _SETTINGS_FILE)

DEFAULTS: dict = {
    # Çalışma modu: "real" (gerçek İHA/SITL'e bağlan) | "sim" (sahte worker'lar —
    # backend olmadan sadece arayüzü canlı gör). Env ASHINA_YKI_MODE veya --sim
    # bayrağı override eder. Uçtan-uca sim (SITL+IHA-sim) için "real" + 127.0.0.1 host.
    "mode":                   "real",
    "ui_theme":               "dark",        # Arayüz teması: "dark" | "light"
    "companion_host":         "192.168.1.229",  # İHA RPi — yardımcı bilgisayar + video (8080/8081)
    "companion_mission_port": 5601,
    # GÜVENLİK (2026-08-25): companion'daki config.json gcs_server.auth_token
    # ile BİREBİR AYNI olmalı. Boş ("") = kimlik doğrulama KAPALI — port 5601
    # aynı WiFi/telemetri ağındaki HERKESE açık kalır (companion 0.0.0.0'da
    # dinliyor, yalnızca localhost değil). Gerçek uçuş/yarışma öncesi rastgele
    # bir dizgiyle DOLDURUN ve companion tarafında da AYNI değeri girin.
    "companion_auth_token":   "",
    "companion_status_port":  5602,
    "companion_video_port":   8081,          # İşlenmiş video (görev sırasında, vision annotated)
    "companion_video_raw_port": 8080,        # Ham kamera (her zaman açık)
    # tools/human_guided_bench.py'nin MJPEG portu (--port, varsayılan 8090) —
    # normal uçuş kamerasından (8080/8081) AYRI, saha testi paneli bunu kullanır.
    "companion_bench_port":  8090,
    "mavlink_host":           "192.168.1.229",  # MAVLink de aynı RPi üzerinden (port 5763)
    "mavlink_port":           5763,
    # FIX P1-4: Timeout değerleri artık configurable
    "ai_connect_timeout":     5.0,           # AISocketWorker TCP bağlantı timeout (s)
    "ai_response_timeout":    5.0,           # AISocketWorker yanıt bekleme timeout (s)
    "rpi_connect_timeout":    5.0,           # RpiStatusWorker TCP bağlantı timeout (s)
    "rpi_read_timeout":       3.0,           # RpiStatusWorker recv timeout (s)
    "video_open_timeout":     5.0,           # VideoStreamWorker HTTP bağlantı timeout (s)
    "video_retry_delay":      10,            # VideoStreamWorker yeniden deneme aralığı (s)

    # ── RTK GPS izleme paneli (u-blox F9P, NMEA üzerinden salt-okunur) ──────
    # NOT: Bu YALNIZCA canlı durum göstergesidir — İHA'ya RTCM düzeltme
    # AKTARMAZ (o ayrı bir özellik, ntrip_worker + MAVLink GPS_RTCM_DATA).
    "rtk_gps_enabled":  True,             # panel worker'ı başlasın mı
    # NEDEN by-id path (2026-08-28, kullanıcı talebi — F9P u-center'da RTCM3
    # USB moduna alındı + 115200 baud): İLK denemede RFD900A telemetri
    # radyosunun CP2102 USB-UART köprüsü YANLIŞLIKLA F9P sanılmıştı (ikisi de
    # o an bağlıydı, RFD'nin akışı ArduPilot parametre listesi — EK3_* —
    # çıkarınca fark edildi). RFD çıkarılıp SADECE F9P bağlıyken doğru port
    # bulundu: F9P kendi NATIVE USB'sinde (u-blox CDC-ACM, /dev/ttyACM0),
    # ham baytlarla DOĞRULANDI (0xD3 RTCM3 preamble'ı + \xb5\x62 UBX sync +
    # okunabilir $GNRMC NMEA cümlesi). by-id path /dev/ttyACM* numarasının
    # değişmesine karşı dayanıklı (Pixhawk bağlantısıyla AYNI desen).
    "rtk_gps_port":     "/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_GNSS_receiver-if00",
    "rtk_gps_baud":     115200,

    # ── NTRIP (TUSAGA-Aktif) ─────────────────────────────────────────────
    # NEDEN varsayılan dolu (2026-08-28, kullanıcı talebi): "Gelişmiş → RTK/
    # NTRIP" sekmesi her seferinde elle doldurulmasın diye kullanıcının
    # kendi TUSAGA-Aktif abonelik bilgileri buraya varsayılan olarak
    # gömüldü, VE ntrip_autostart=True ile YKİ açılışında otomatik
    # bağlanır (bkz. main_window._start_workers). Mountpoint BÜYÜK HARF
    # olmalı ("VRSRTCM34") — sourcetable'da böyle kayıtlı, küçük harfle
    # caster SOURCETABLE yanıtı döner (bkz. RTK_DEVIR.md).
    # GÜVENLİK NOTU: bu, gerçek bir abonelik şifresini düz metin olarak
    # kaynak koduna gömer — bu repo git'e push edilirse şifre repo
    # geçmişinde kalır. Kullanıcı bilerek bu kolaylığı istedi.
    "ntrip_autostart":  True,
    "ntrip_host":       "212.156.70.42",
    "ntrip_port":       2101,
    "ntrip_mountpoint": "VRSRTCM34",
    "ntrip_username":   "NTRIP_KULLANICI_BURAYA",
    "ntrip_password":   "NTRIP_SIFRE_BURAYA",
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
    def mode(self) -> str:
        """'real' | 'sim'. Env ASHINA_YKI_MODE öncelikli (CLI --sim bunu ayarlar)."""
        env = os.environ.get("ASHINA_YKI_MODE", "").strip().lower()
        if env in ("real", "sim"):
            return env
        return str(self._data.get("mode", "real")).lower()

    @property
    def is_sim(self) -> bool:
        return self.mode == "sim"

    @property
    def ui_theme(self) -> str:
        """'dark' | 'light'. Tanınmayan değer koyu temaya düşer."""
        value = str(self._data.get("ui_theme", "dark")).strip().lower()
        return value if value in ("dark", "light") else "dark"

    @property
    def companion_host(self) -> str:
        # ASHINA_YKI_HOST env → tek düğmeyle host override (laptop_e2e.sh sim'de
        # 127.0.0.1'e çevirir). M3: dağınık IP default'ları tek kaynağa indirildi.
        env = os.environ.get("ASHINA_YKI_HOST", "").strip()
        return env or str(self._data["companion_host"])

    @property
    def companion_mission_port(self) -> int:
        return int(self._data["companion_mission_port"])

    @property
    def companion_auth_token(self) -> str:
        return str(self._data.get("companion_auth_token", "") or "")

    @property
    def companion_status_port(self) -> int:
        return int(self._data["companion_status_port"])

    @property
    def companion_video_port(self) -> int:
        return int(self._data["companion_video_port"])

    @property
    def companion_video_raw_port(self) -> int:
        return int(self._data.get("companion_video_raw_port", 8080))

    @property
    def companion_bench_port(self) -> int:
        return int(self._data.get("companion_bench_port", 8090))

    # ── RTK GPS izleme paneli ────────────────────────────────────────────────
    @property
    def rtk_gps_enabled(self) -> bool:
        return bool(self._data.get("rtk_gps_enabled", True))

    @property
    def rtk_gps_port(self) -> str:
        return str(self._data.get("rtk_gps_port", "/dev/ttyACM0"))

    @property
    def rtk_gps_baud(self) -> int:
        return int(self._data.get("rtk_gps_baud", 38400))

    # ── NTRIP ─────────────────────────────────────────────────────────────
    @property
    def ntrip_autostart(self) -> bool:
        return bool(self._data.get("ntrip_autostart", False))

    @property
    def ntrip_host(self) -> str:
        return str(self._data.get("ntrip_host", ""))

    @property
    def ntrip_port(self) -> int:
        return int(self._data.get("ntrip_port", 2101))

    @property
    def ntrip_mountpoint(self) -> str:
        return str(self._data.get("ntrip_mountpoint", ""))

    @property
    def ntrip_username(self) -> str:
        return str(self._data.get("ntrip_username", ""))

    @property
    def ntrip_password(self) -> str:
        return str(self._data.get("ntrip_password", ""))

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
        env = os.environ.get("ASHINA_YKI_HOST", "").strip()
        return env or str(self._data["mavlink_host"])

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
