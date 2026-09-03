"""
mavlink_worker.py — Telemetri Motoru
MAVLink bağlantısı: Serial / TCP / UDP
Yakalanan paketler: HEARTBEAT, VFR_HUD, ATTITUDE, SYS_STATUS,
                    GLOBAL_POSITION_INT, PARAM_VALUE, LOG_ENTRY
"""
import queue
import threading
import time
import logging
from typing import Any, Optional
from PyQt6.QtCore import QThread, pyqtSignal, pyqtSlot
from pymavlink import mavutil
from core import local_storage
from core import batarya

logger = logging.getLogger(__name__)


class _BaglantiGecersizKilindi(Exception):
    """set_connection()/stop() bu bağlantı denemesini GEÇERSİZ KILDI.

    Gerçek bir arıza DEĞİLDİR — operatörün kendi isteğiyle (yeniden bağlan,
    kes) tetiklenen bir yan etkidir. run() bunu backoff/log OLMADAN ele alır;
    metin eşleştirmeye değil, jenerasyon sayacına dayanır (bkz. __init__
    içindeki NEDEN notu).
    """


_COPTER_MODES = {
    0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO",
    4: "GUIDED", 5: "LOITER", 6: "RTL", 7: "CIRCLE",
    9: "LAND", 11: "DRIFT", 13: "SPORT", 14: "FLIP",
    15: "AUTOTUNE", 16: "POSHOLD", 17: "BRAKE", 18: "THROW",
    19: "AVOID_ADSB", 20: "GUIDED_NOGPS", 21: "SMART_RTL",
    23: "FLOWHOLD", 24: "FOLLOW", 25: "ZIGZAG",
}

_GPS_FIX_NAMES = {0: "No GPS", 1: "No Fix", 2: "2D", 3: "3D", 4: "DGPS", 5: "RTK Float", 6: "RTK Fixed"}

# MAV_RESULT — FC'nin komut hükmü. Operatöre TÜRKÇE gösterilebilsin diye
# burada; ham sayı (ör. 4) saha ekibine hiçbir şey anlatmıyor.
_MAV_RESULT_ADLARI = {
    0: "KABUL EDİLDİ",
    1: "GEÇİCİ RET (araç şu an uygun değil)",
    2: "GEÇERSİZ PARAMETRE",
    3: "DESTEKLENMİYOR",
    4: "BAŞARISIZ (FC komutu reddetti)",
    5: "SÜRÜYOR",
    6: "İPTAL EDİLDİ",
}
# Ret sayılan sonuçlar — 0 (kabul) ve 5 (sürüyor) dışındaki her şey.
_MAV_RESULT_RET = (1, 2, 3, 4, 6)

# ArduPilot ivmeölçer kalibrasyonu — araçtan GCS'e gelen konum/sonuç kanalı.
_CMD_ACCELCAL_VEHICLE_POS = 42429
_ACCELCAL_POS_BASARILI = 16777215
_ACCELCAL_POS_BASARISIZ = 16777216
_ACCELCAL_POS_ADLARI = {
    1: "düz/yatay", 2: "sol yan", 3: "sağ yan", 4: "burun aşağı",
    5: "burun yukarı", 6: "sırtüstü",
    16777215: "BAŞARILI", 16777216: "BAŞARISIZ",
}

# ── Telemetri akış aralıkları (µs) — mesaj_id → aralık ────────────────────────
# Radyo (telemetri COM, ~57600 baud) bant genişliğine uygun seçildi. Eskiden
# REQUEST_DATA_STREAM(ALL@4Hz) + yüksek SET_MESSAGE_INTERVAL birlikte radyoyu
# doyuruyordu (mission indirme 500ms/WP). 100_000µs=10Hz, 500_000=2Hz, 1e6=1Hz.
_MSG_INTERVALS_NORMAL = {
    30:    100_000,    # ATTITUDE            10 Hz
    74:    200_000,    # VFR_HUD              5 Hz
    24:    250_000,    # GPS_RAW_INT          4 Hz
    33:    200_000,    # GLOBAL_POSITION_INT  5 Hz
    1:     500_000,    # SYS_STATUS           2 Hz
    147:   500_000,    # BATTERY_STATUS       2 Hz
    193: 1_000_000,    # EKF_STATUS_REPORT    1 Hz
    65:    250_000,    # RC_CHANNELS          4 Hz
    242: 2_000_000,    # HOME_POSITION        0.5 Hz
    11030: 500_000,    # ESC 1-4              2 Hz
    11031: 500_000,    # ESC 5-8             2 Hz
    11032: 500_000,    # ESC 9-12            2 Hz
    11033: 500_000,    # ESC 13-16           2 Hz
}
# Mission upload/download sırasında: ağır akışları kıs → radyo MISSION_ITEM'a açılır.
_MSG_INTERVALS_TRANSFER = {
    30:    500_000,    # ATTITUDE 2Hz (HUD yine de oynar)
    74:  1_000_000,    33:    500_000,   24: 1_000_000,
    1:   1_000_000,    147: 1_000_000,   193: 2_000_000,
    65:  2_000_000,    242: 2_000_000,
    11030: 2_000_000,  11031: 2_000_000, 11032: 2_000_000, 11033: 2_000_000,
}

# ── Yeniden bağlanma geri çekilme (backoff) merdiveni ─────────────────────────
# NEDEN: Seri port erişilemezken (USB çekilmiş / sürücü hatası, ör.
# "Bağlantı kurulamadı: Could not configure port: (5, 'Girdi/Çıktı hatası')")
# run() sabit ~8 s aralıkla AYNI hatayı üretip duruyordu: log dosyası ve
# audit_log şişiyor, port faydasız biçimde tekrar tekrar açılmaya çalışılıyor,
# üstteki bar "Bağlantı kesildi" derken worker arka planda dönmeye devam ettiği
# için operatör gerçek durumu göremiyordu. Üstel geri çekilme hem gürültüyü hem
# de faydasız deneme sıklığını düşürür; başarılı bağlantıda merdiven sıfırlanır.
_RECONNECT_BACKOFF_STEPS = (2.0, 4.0, 8.0, 15.0, 30.0)   # saniye, tavan 30 s
# Aynı hata metni ardışık tekrarlanırsa log/UI bildirimi bu kadar denemede bir
# yapılır (ilki her zaman yazılır) — log boğulmasını engeller.
_ERROR_REPEAT_NOTIFY_EVERY = 5
# Backoff beklemesi bu dilimlerle uyunur (interrupt + watchdog canlılığı için)
_BACKOFF_SLICE_S = 0.2


class MavlinkWorker(QThread):
    # Sinyaller
    telemetry_updated = pyqtSignal(dict)      # 10 Hz
    param_received = pyqtSignal(str, float)
    log_entry_received = pyqtSignal(dict)
    connection_changed = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)
    status_text_received = pyqtSignal(str, int)   # text, severity
    mag_cal_progress = pyqtSignal(int, int, float)  # compass_id, pct, fitness
    mag_cal_report = pyqtSignal(int, int, float)    # compass_id, cal_status, fitness
    mission_upload_progress = pyqtSignal(int, int)  # seq, total
    mission_upload_done = pyqtSignal(bool, str)     # success, message
    mission_download_progress = pyqtSignal(int, int)  # current, total
    mission_download_done = pyqtSignal(bool, list, str) # success, waypoints, message
    flight_sequence_status = pyqtSignal(str)        # status text
    log_download_progress = pyqtSignal(int, int)    # bytes_received, total_bytes
    log_download_done = pyqtSignal(bool, str, bytes)  # success, filename, raw_data
    home_position_updated = pyqtSignal(float, float, float)  # lat, lon, alt_m
    # NEDEN (2026-09-03): FC'nin komut hükmü bugüne kadar HİÇ okunmuyordu.
    # Ölçüldü: operatör 18:56:39'da ivmeölçer kalibrasyonu başlattı, altı
    # konumu da gönderdi (log: CMD id=241/42429), FC hiçbir STATUSTEXT
    # döndürmedi. Komut kabul mü edildi red mi — COMMAND_ACK okunmadığı için
    # YKİ'nin bunu bilmesi İMKÂNSIZDI; panel sessizliği 'devam' sandı.
    command_ack_received = pyqtSignal(int, int)   # command_id, MAV_RESULT
    # NEDEN AYRI SİNYAL (2026-09-03, ArduPilot kaynağından doğrulandı):
    # ArduPilot 6-konum ivmeölçer kalibrasyonunda GCS'e STATUSTEXT ile
    # konuşmayı BIRAKIR. AP_AccelCal.cpp:402 gcs_vehicle_position() ilk
    # ACCELCAL_VEHICLE_POS komutunda _use_gcs_snoop=false yapar; ondan
    # sonra 'Place vehicle ...' istemleri ARTIK GÖNDERİLMEZ. Araç bunun
    # yerine GCS'e COMMAND_LONG(42429) yollar (GCS_Common.cpp:3441):
    #   param1 = 1..6 beklenen konum, 16777215 BAŞARILI, 16777216 BAŞARISIZ
    # ve bunu 1 Hz ile TEKRARLAR — tek atışlık STATUSTEXT'in aksine
    # kaybolmaz. Mission Planner'ın kullandığı kanal budur.
    accelcal_pos_received = pyqtSignal(int)   # 1..6 | 16777215 | 16777216

    def __init__(self, parent=None):
        super().__init__(parent)
        self.heartbeat_counter: int = 0
        self._connection_string: str = ""
        self._baud: int = 57600
        self._mav: Any = None
        # ── self._mav SENKRONİZASYONU (2026-09-03, canlıda yakalandı) ────
        # NEDEN: self._mav GUI thread'den (set_connection/stop) ve worker
        # thread'den (_connect_and_listen: wait_heartbeat + recv_match döngüsü)
        # KİLİTSİZ paylaşılıyordu. GUI thread bir bağlantıyı değiştirirken/
        # keserken worker AYNI ANDA o nesneyi okuyor/kullanıyor olabiliyordu.
        # CANLI ARAÇTA ÖLÇÜLDÜ (operatör drone bağlantısını elle kesip/kurarken):
        #   MavlinkWorker run() exception: 'NoneType' object has no attribute 'recv_match'
        #   MavlinkWorker run() exception: Bağlantı kurulamadı: Attempting to use a port that is not open
        # İkisi de İSTEĞE BAĞLI bağlantı değişiminin YAN ETKİSİYDİ, gerçek bir
        # arıza değildi — ama _note_connect_failure'daki "quiet" metin listesi
        # bu iki hata dizesini KAPSAMIYORDU, yani operatöre "Worker hatası" diye
        # tekrar tekrar gösteriliyordu (kes/bağlan her denendiğinde).
        # Jenerasyon sayacı + kilit: bağlantıyı GEÇERSİZ KILAN her eylem
        # (set_connection, stop) sayacı ilerletir; _connect_and_listen kendi
        # jenerasyonunu tutar, run() bir istisna yakaladığında sayaç değiştiyse
        # bunun İSTEĞE BAĞLI bir değişim olduğunu anlar — metin eşleştirmeye
        # değil, olgusal bir duruma bakar.
        self._mav_lock = threading.Lock()
        self._mav_generation: int = 0
        # queue.Queue thread-safe: UI thread put, worker thread get
        self._command_queue: queue.Queue = queue.Queue(maxsize=64)
        self._ned_vel_queue: queue.Queue = queue.Queue(maxsize=32)
        # Genel aksiyon kuyruğu — GUI thread'inden gelen slot çağrılarının (set_param,
        # inject_rtcm, request_parameters, send_manual_control, request_log_list) SOKETE
        # DOKUNAN gövdesi burada birikir ve run() döngüsünde (worker thread) çalışır.
        # ÖNCEDEN bu slotlar soketi doğrudan GUI thread'inde kullanıyordu ve run()'ın
        # recv_match'iyle AYNI bağlantıya eşzamanlı erişip MAVLink akışını bozabiliyordu.
        self._action_queue: queue.Queue = queue.Queue(maxsize=64)
        # 2026-08-26 DÜZELTME: inject_rtcm ÖNCEDEN _action_queue'yu set_param/
        # manual_control/request_log_list ile PAYLAŞIYORDU. Yerel bir F9P baz
        # istasyonundan (core/rtk_gps_worker.py) RTCM3 aktarımı devreye girince
        # bu paylaşılan kuyruk saniyede onlarca kez DOLDU ("Aksiyon kuyruğu dolu
        # — istek atlandı") — canlı ölçüldü. RTCM sürekli/patlamalı bir akış,
        # ARM/parametre/manuel kontrol gibi SEYREK AMA KRİTİK komutlarla aynı
        # sınırlı kuyruğu paylaşmamalı: bir drone bağlıyken RTCM baskını ARM
        # komutunu kuyruktan DÜŞÜREBİLİRDİ. Ayrı, daha büyük bir kuyruk —
        # RTCM'in kendi patlamaları kendi içinde kalır, diğer komutları asla
        # etkilemez.
        self._rtcm_queue: queue.Queue = queue.Queue(maxsize=256)
        self._rtcm_seq: int = 0  # bkz. _do_inject_rtcm — flags alanındaki 5-bit sequence ID
        self._last_heartbeat_time: float = 0.0
        # ── Yeniden bağlanma durumu (bkz. run() / _note_connect_failure) ──────
        # NEDEN: backoff merdiveninde nerede olduğumuzu ve aynı hatanın kaç kez
        # üst üste tekrarladığını bilmeden ne geri çekilme ne de log boğulma
        # koruması yapılabilir. Yalnızca worker thread'inden yazılır/okunur.
        self._reconnect_attempt: int = 0    # ardışık BAŞARISIZ deneme sayısı
        self._last_error_text: str = ""     # son bağlantı hatası metni
        self._error_repeat: int = 0         # aynı hatanın ardışık tekrar sayısı
        self._telemetry_accumulator: dict = {}
        self._telemetry_tick: int = 0
        # Zaman tabanlı telemetri throttle — mesaj seli olsa da UI ≤15Hz alır
        self._last_telemetry_emit: float = 0.0
        self._telemetry_min_interval: float = 1.0 / 15.0   # ~66 ms = 15 Hz
        # Gerçek autopilot kimliği (is_autopilot heartbeat'inden):
        # wait_heartbeat target_component=0 (broadcast) yakalayabilir;
        # bunu kullanırsak komutlar MAVProxy gibi ara komponentlerle de
        # işlenir → mission upload bozulur. Doğru hedef: autopilot=(1,1).
        self._autopilot_sys: int = 0
        self._autopilot_comp: int = 0
        self._upload_waypoints: Optional[list] = None
        self._download_mission_pending: bool = False
        self._flight_seq_pending: bool = False
        self._stored_flight_alt: float = 15.0
        self._log_download_pending: int = -1  # -1 = yok
        # Parametre cache — param_total dolunca batch DB yazımı tetiklenir
        self._param_cache: dict = {}
        self._param_total: int = 0   # PARAM_VALUE.param_count'tan doldurulur
        self._db_conn = None         # set_db_conn() ile atanır
        # 16 kanallık ESC tamponu — tüm ESC_TELEMETRY mesajları buraya yazılır
        self._esc_all = {
            'rpm':  [0]   * 16,
            'volt': [0.0] * 16,
            'curr': [0.0] * 16,
            'temp': [0]   * 16,
            'mah':  [0]   * 16,
        }

    def set_db_conn(self, db_conn) -> None:
        """SQLite bağlantısını atar — parametre cache DB'ye yazılır."""
        self._db_conn = db_conn

    def _invalidate_connection(self) -> None:
        """self._mav'i GÜVENLE kapatır ve jenerasyonu ilerletir.

        Kilit altında sadece REFERANS DEĞİŞİMİ yapılır (atomik); yavaş olan
        `.close()` I/O çağrısı kilidin DIŞINDA yapılır — GUI thread'i bir
        seri port kapanışı için bloke ETMEMELİDİR.
        set_connection(), stop() ve run()'ın hata yakalama bloğu artık HEPSİ
        bunu çağırır — üç ayrı yerde tekrarlanan kilitsiz close+None deseni
        tek noktaya toplandı.
        """
        with self._mav_lock:
            self._mav_generation += 1
            mav, self._mav = self._mav, None
        if mav is not None:
            try:
                mav.close()
            except Exception:
                pass

    def set_connection(self, conn_str: str, baud: int = 57600) -> None:
        """Yeni bağlantı dizisi + baudrate atar ve aktif bağlantıyı keser."""
        self._connection_string = conn_str
        self._baud = baud
        self._invalidate_connection()

    def set_connection_string(self, conn_str: str) -> None:
        """Geriye dönük uyumluluk — baudrate değişmez."""
        self.set_connection(conn_str, self._baud)

    def run(self) -> None:
        while not self.isInterruptionRequested():
            self.heartbeat_counter += 1  # watchdog: her döngüde artır
            _baslangic_nesli = self._mav_generation
            try:
                self._connect_and_listen()
            except _BaglantiGecersizKilindi:
                # set_connection()/stop() BU bağlantı denemesi sürerken
                # çağrıldı — istisna bunun DOĞRUDAN sonucu, gerçek bir arıza
                # değil. Ne log ne backoff: stop() ise isInterruptionRequested
                # zaten bir sonraki turda döngüyü bitirecek; set_connection()
                # ise self._connection_string ZATEN yeni hedefe güncellendi,
                # hemen (beklemeden) yeniden denenir.
                logger.debug(
                    "MAVLink bağlantısı değiştirildi/kesildi (operatör "
                    "isteği) — jenerasyon %d -> %d",
                    _baslangic_nesli, self._mav_generation)
                continue
            except Exception as exc:
                self.connection_changed.emit(False)
                # NEDEN generation ÖNCE okunur: _invalidate_connection() KENDİSİ
                # sayacı ilerletir — çağrıdan SONRA karşılaştırsaydık "değişti"
                # her zaman True çıkardı (kendi bumpumuzu görürdük).
                _nesil_degisti = (self._mav_generation != _baslangic_nesli)
                # ÖNCESİ: burada self._mav kilitsiz close+None ediliyordu —
                # tam da bu istisnayı üreten yarışın kaynağıydı. Artık aynı
                # kilitli/jenerasyonlu yol kullanılıyor (bkz. __init__ NEDEN).
                self._invalidate_connection()
                if _nesil_degisti:
                    # _BaglantiGecersizKilindi YALNIZCA bağlantı FAZINDA
                    # (_connect_and_listen başındaki üç kontrol) atılır;
                    # 350+ satırlık dinleme döngüsü içindeki self._mav
                    # kullanımları o denetimlerin DIŞINDA kalır ve ORADA
                    # oluşan bir yarış HAM bir istisna (AttributeError/
                    # PortNotOpenError) olarak buraya düşer. Jenerasyon
                    # kontrolü bu YEDEK KATMANDIR — metne bakmadan, olgusal
                    # olarak "bu istisna set_connection()/stop() yüzünden mi"
                    # sorusuna cevap verir. ÖLÇÜLDÜ: eklenmeden önce bu yol
                    # "MavlinkWorker run() exception: Attempting to use a port that is not open" diye ERROR logluyordu.
                    logger.debug(
                        "MAVLink bağlantısı değiştirildi/kesildi (operatör "
                        "isteği) sırasında beklenen istisna: %s", exc)
                    continue
                # NEDEN: Hata raporlaması ve üstel geri çekilme tek yerde
                # toplandı — log boğulma koruması ve backoff merdiveni birlikte
                # yürür (bkz. _note_connect_failure). Backoff SIFIRLAMASI artık
                # "_connect_and_listen temiz döndü" değil, GERÇEKTEN bağlanıldı
                # olayına bağlı (bağlantı dizesi boşken de temiz dönüyordu).
                delay = self._note_connect_failure(exc)
                self._backoff_sleep(delay)

    def _note_connect_failure(self, exc: Exception) -> float:
        """Bağlantı hatasını raporlar (log'u boğmadan) ve bekleme süresini döndürür.

        NEDEN (canlı gözlenen hata): seri port erişilemezken aynı istisna her
        denemede hem ERROR olarak log'lanıyor hem de error_occurred ile UI'ya
        gidiyordu (main_window her hatayı audit_log'a da yazıyor) → dosya ve
        veritabanı aynı satırla doluyordu. Artık:
          • İlk hata tam ERROR olarak yazılır,
          • Ardışık AYNI hata her _ERROR_REPEAT_NOTIFY_EVERY denemede bir
            "N denemedir aynı hata" özetiyle yazılır, aradakiler DEBUG,
          • UI'ya giden metin deneme numarası ve bir sonraki denemeye kalan
            süreyi içerir → operatör "worker sessizce dönüyor" sanmaz.
        Sinyal şeması KORUNDU: error_occurred hâlâ tek bir str yayar; yeni sinyal
        eklenmedi (main_window'a dokunulmuyor).
        """
        text = str(exc)
        # 10038 (WSAENOTSOCK) / 9 (EBADF): bağlantı MANUEL kesildiğinde olağandır
        # — kullanıcıya hata gibi gösterilmez, ama backoff yine de işletilir.
        quiet = ("10038" in text) or ("Bad file descriptor" in text)

        if text == self._last_error_text:
            self._error_repeat += 1
        else:
            self._last_error_text = text
            self._error_repeat = 1

        self._reconnect_attempt += 1
        idx = min(self._reconnect_attempt - 1, len(_RECONNECT_BACKOFF_STEPS) - 1)
        delay = _RECONNECT_BACKOFF_STEPS[idx]

        if quiet:
            return delay

        notify = (self._error_repeat == 1
                  or self._error_repeat % _ERROR_REPEAT_NOTIFY_EVERY == 0)

        if self._error_repeat == 1:
            logger.error("MavlinkWorker run() exception: %s", text)
        elif notify:
            logger.error("MavlinkWorker: %d denemedir aynı hata: %s",
                         self._error_repeat, text)
        else:
            logger.debug("MavlinkWorker bağlantı hatası tekrarı %d/%d: %s",
                         self._error_repeat, _ERROR_REPEAT_NOTIFY_EVERY, text)

        if notify:
            if self._error_repeat > 1:
                self.error_occurred.emit(
                    f"{text} — {self._error_repeat} denemedir aynı hata; "
                    f"yeniden deneniyor ({self._reconnect_attempt}. deneme, "
                    f"{delay:.0f}s sonra)"
                )
            else:
                self.error_occurred.emit(
                    f"{text} — yeniden deneniyor "
                    f"({self._reconnect_attempt}. deneme, {delay:.0f}s sonra)"
                )
        return delay

    def _backoff_sleep(self, delay: float) -> None:
        """Backoff beklemesi — küçük dilimlerle uyur.

        NEDEN: Tek parça `time.sleep(30)` iki şeyi bozardı:
          (a) stop() çağrılsa bile thread 30 s boyunca kapanmaz (uygulama kapanışı
              donar),
          (b) heartbeat_counter 30 s sabit kalır; main_window watchdog'u
              (5 s × 3 tick = 15 s) worker'ı "takıldı" sanıp YENİDEN BAŞLATIR —
              bu da backoff merdivenini sıfırlayıp düzeltmek istediğimiz hızlı
              hata döngüsünü aynen geri getirirdi.
        """
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if self.isInterruptionRequested():
                return
            self.heartbeat_counter += 1   # watchdog: bekleme sırasında da canlıyız
            time.sleep(_BACKOFF_SLICE_S)

    def _connect_and_listen(self) -> None:
        if not self._connection_string:
            time.sleep(0.5)
            return

        # Bu bağlantı denemesinin JENERASYONU — set_connection()/stop() bu
        # deneme sürerken çağrılırsa sayaç ilerler ve aşağıdaki kontroller
        # bunu YARIŞ HATASI değil, İSTEĞE BAĞLI geçersiz kılma olarak görür
        # (bkz. __init__ NEDEN notu + _BaglantiGecersizKilindi).
        my_nesil = self._mav_generation
        conn_str, baud = self._connection_string, self._baud
        logger.info("MAVLink bağlanıyor: %s  baud=%d", conn_str, baud)
        try:
            mav = mavutil.mavlink_connection(conn_str, baud=baud)
        except Exception as exc:
            raise ConnectionError(f"Bağlantı kurulamadı: {exc}") from exc

        with self._mav_lock:
            if self._mav_generation != my_nesil:
                # set_connection()/stop() nesne YARATILIRKEN çağrıldı —
                # henüz self._mav'e hiç atanmadı, kimse etkilenmedi.
                try:
                    mav.close()
                except Exception:
                    pass
                raise _BaglantiGecersizKilindi()
            self._mav = mav

        try:
            mav.wait_heartbeat(timeout=10)
        except Exception as exc:
            if self._mav_generation != my_nesil:
                # ÖLÇÜLDÜ (2026-09-03): set_connection()/stop() wait_heartbeat
                # SÜRERKEN çağrılırsa GUI thread bu AYNI nesneyi kapatır ve
                # pyserial "Attempting to use a port that is not open" atar.
                # Metin eşleştirmeye gerek yok — jenerasyon değişmiş olması
                # tek başına yeterli kanıt.
                raise _BaglantiGecersizKilindi() from exc
            raise ConnectionError(f"Bağlantı kurulamadı: {exc}") from exc

        if self._mav_generation != my_nesil:
            # wait_heartbeat TAM bu sırada tamamlandı ama hemen ardından
            # geçersiz kılındı — "bağlandı" diye duyurmadan sessizce çık.
            raise _BaglantiGecersizKilindi()

        # NEDEN: Backoff merdiveni ve hata tekrar sayacı YALNIZCA gerçekten
        # bağlanıldığında sıfırlanır. Eskiden `_connect_and_listen()` temiz
        # döndüğünde sıfırlanıyordu — bağlantı dizesi boşken de temiz döndüğü
        # için merdiven asla yükselemiyordu.
        self._reconnect_attempt = 0
        self._error_repeat = 0
        self._last_error_text = ""

        self.connection_changed.emit(True)
        self._last_heartbeat_time = time.time()

        # Veri akışı iste (SET_MESSAGE_INTERVAL — modern ArduPilot)
        try:
            self._request_streams()
        except Exception:
            pass

        # Bağlanma anında tüm parametreleri iste
        try:
            self._mav.mav.param_request_list_send(
                self._mav.target_system, self._mav.target_component
            )
        except Exception:
            pass

        while not self.isInterruptionRequested():
            # Watchdog heartbeat artır
            self.heartbeat_counter += 1

            # Sistemize heartbeat gönder (1 Hz)
            try:
                self._mav.mav.heartbeat_send(
                    mavutil.mavlink.MAV_TYPE_GCS,
                    mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                    0, 0, 0,
                )
            except Exception:
                pass

            # Komut kuyruğu işle (thread-safe drain)
            while True:
                try:
                    cmd = self._command_queue.get_nowait()
                    self._execute_command(cmd)
                except queue.Empty:
                    break

            # Genel aksiyon kuyruğu (set_param/manual_control/... gövdeleri) — worker
            # thread'de çalışır, soket erişimi recv_match ile serileşir.
            while True:
                try:
                    action = self._action_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    action()
                except Exception as exc:
                    logger.error("Aksiyon çalıştırılamadı: %s", exc)

            # RTCM3 düzeltme kuyruğu — AYRI (bkz. __init__ NEDEN notu), böylece
            # patlamalı/sık RTCM akışı yukarıdaki genel kuyruğu asla doldurmaz.
            while True:
                try:
                    rtcm_data = self._rtcm_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._do_inject_rtcm(rtcm_data)
                except Exception as exc:
                    logger.error("RTCM enjeksiyonu çalıştırılamadı: %s", exc)

            # NED hız komutları (yalnızca en güncel komutu gönder)
            last_vel = None
            while True:
                try:
                    last_vel = self._ned_vel_queue.get_nowait()
                except queue.Empty:
                    break
            if last_vel is not None:
                self._execute_ned_velocity(*last_vel)

            # Görev yükleme (bloklar — telemetri geçici olarak durur)
            if self._upload_waypoints is not None:
                wps = self._upload_waypoints
                self._upload_waypoints = None
                self._do_mission_upload(wps)
                continue
                
            # Görev indirme (bloklar — telemetri geçici olarak durur)
            if self._download_mission_pending:
                self._download_mission_pending = False
                self._do_mission_download()
                continue

            # Uçuş sekansı (bloklar ~20s)
            if self._flight_seq_pending:
                self._flight_seq_pending = False
                self._do_flight_sequence()
                continue

            # Log indirme
            if self._log_download_pending >= 0:
                log_id = self._log_download_pending
                self._log_download_pending = -1
                self._do_log_download(log_id)
                continue

            # Paket al
            msg = self._mav.recv_match(blocking=True, timeout=0.1)
            if msg is None:
                continue

            msg_type = msg.get_type()

            if msg_type == "HEARTBEAT":
                # Sadece autopilot'tan gelen heartbeat'leri işle.
                # Filtre:
                #   • srcSystem == target_system : doğru araç
                #   • msg.autopilot != MAV_AUTOPILOT_INVALID : gerçek otopilot
                #     (MAVProxy / GCS / Companion INVALID gönderir; sadece
                #      ArduPilot/PX4 gerçek autopilot ID'si gönderir.)
                #   • type != MAV_TYPE_GCS : GCS loopback koruması
                # Not: target_component'e BAKMIYORUZ — wait_heartbeat()
                # MAVProxy'nin (comp=0) heartbeat'ini yakalamış olabilir;
                # gerçek autopilot ise comp=1'dir.
                _src_sys  = msg.get_srcSystem()
                _src_comp = msg.get_srcComponent()
                is_autopilot = (
                    _src_sys == self._mav.target_system
                    and msg.type != mavutil.mavlink.MAV_TYPE_GCS
                    and getattr(msg, "autopilot", -1) != mavutil.mavlink.MAV_AUTOPILOT_INVALID
                )
                if is_autopilot:
                    self._last_heartbeat_time = time.time()
                    # Gerçek autopilot sys/comp — mission upload bunu kullanır
                    self._autopilot_sys  = _src_sys
                    self._autopilot_comp = _src_comp
                    armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                    mode_str = _COPTER_MODES.get(msg.custom_mode, f"MODE_{msg.custom_mode}")
                    self._telemetry_accumulator.update({
                        "flight_mode": mode_str,
                        "armed": armed,
                    })
            elif msg_type == "GPS_RAW_INT":
                self._telemetry_accumulator.update({
                    "gps_fix_type": msg.fix_type,
                    "gps_fix_name": _GPS_FIX_NAMES.get(msg.fix_type, "?"),
                    "gps_sats": msg.satellites_visible,
                    "gps_hdop": msg.eph / 100.0 if msg.eph != 65535 else 99.0,
                })
                logger.info("IHA_GPS_TESHIS: fix=%s sat=%d eph=%s epv=%s",
                            _GPS_FIX_NAMES.get(msg.fix_type, "?"), msg.satellites_visible,
                            msg.eph, msg.epv)
            elif msg_type == "EKF_STATUS_REPORT":
                self._telemetry_accumulator["ekf_flags"] = msg.flags
            elif msg_type == "COMMAND_LONG":
                # Araç → GCS yönünde gelen TEK komut bu; başka bir
                # COMMAND_LONG beklenmiyor, o yüzden dar süzgeç.
                if int(getattr(msg, "command", -1)) == _CMD_ACCELCAL_VEHICLE_POS:
                    _poz = int(getattr(msg, "param1", 0))
                    logger.info("ACCELCAL_POS: %d (%s)", _poz,
                                _ACCELCAL_POS_ADLARI.get(_poz, "konum"))
                    self.accelcal_pos_received.emit(_poz)
            elif msg_type == "COMMAND_ACK":
                _ack_cmd = int(getattr(msg, "command", -1))
                _ack_res = int(getattr(msg, "result", -1))
                logger.info("COMMAND_ACK: cmd=%d result=%d (%s)",
                            _ack_cmd, _ack_res,
                            _MAV_RESULT_ADLARI.get(_ack_res, "?"))
                self.command_ack_received.emit(_ack_cmd, _ack_res)
            elif msg_type == "STATUSTEXT":
                txt = msg.text
                if isinstance(txt, bytes):
                    txt = txt.decode("utf-8", errors="replace")
                txt = txt.rstrip("\x00")
                logger.info("STATUSTEXT_TESHIS: sev=%d %s", msg.severity, txt)
                self.status_text_received.emit(txt, msg.severity)
            elif msg_type == "RC_CHANNELS":
                self._telemetry_accumulator.update({
                    "rc_1": msg.chan1_raw, "rc_2": msg.chan2_raw,
                    "rc_3": msg.chan3_raw, "rc_4": msg.chan4_raw,
                    "rc_5": msg.chan5_raw, "rc_6": msg.chan6_raw,
                    "rc_7": msg.chan7_raw, "rc_8": msg.chan8_raw,
                    "rc_rssi": msg.rssi,
                })
            elif msg_type == "ATTITUDE":
                self._telemetry_accumulator.update({
                    "roll": msg.roll,
                    "pitch": msg.pitch,
                    "yaw": msg.yaw,
                    "rollspeed": msg.rollspeed,
                    "pitchspeed": msg.pitchspeed,
                    "yawspeed": msg.yawspeed,
                })
            elif msg_type == "VFR_HUD":
                self._telemetry_accumulator.update({
                    "airspeed":    msg.airspeed,
                    "groundspeed": msg.groundspeed,
                    "heading":     msg.heading,
                    "throttle":    msg.throttle,
                    "baro_alt":    msg.alt,   # barometrik irtifa — referans alınmaz
                    "climb":       msg.climb,
                })
            elif msg_type == "SYS_STATUS":
                # MAV_SYS_STATUS_SENSOR bit tanımları (common.xml)
                # 3D_ACCEL = 0x02, 3D_MAG = 0x04
                _sh = msg.onboard_control_sensors_health
                update: dict = {
                    "errors_comm":    msg.errors_comm,
                    # FC'nin bildirdiği GERÇEK haberleşme düşme oranı (MAVLink SYS_STATUS
                    # drop_rate_comm, birim c% = %×100). Paket Kaybı göstergesini besler.
                    "drop_rate_comm": msg.drop_rate_comm,
                    "accel_cal_done": bool(_sh & 0x02),
                    "compass_cal_done": bool(_sh & 0x04),
                }
                # BATTERY_STATUS yoksa SYS_STATUS'tan doldur (eski FC fallback)
                if "battery_voltage" not in self._telemetry_accumulator:
                    update["battery_voltage"]   = msg.voltage_battery / 1000.0
                    update["battery_current"]   = (msg.current_battery / 100.0
                                                   if msg.current_battery != -1 else 0.0)
                    update["battery_remaining"] = msg.battery_remaining
                self._telemetry_accumulator.update(update)
            elif msg_type == "BATTERY_STATUS":
                # ArduPilot 4.x tercih edilen batarya mesajı — birden fazla batarya destekler
                # İlk batarya (battery_id=0) kullanılır
                if getattr(msg, "id", 0) == 0:
                    voltages = getattr(msg, "voltages", [])
                    cell_mv = [v for v in voltages if v != 65535]
                    cell_v  = [v / 1000.0 for v in cell_mv]   # mV → V
                    total_v = sum(cell_v) if cell_v else 0.0
                    current_a = (msg.current_battery / 100.0
                                 if msg.current_battery != -1 else 0.0)
                    mah_used  = (msg.current_consumed
                                 if msg.current_consumed != -1 else 0)
                    remaining = (msg.battery_remaining
                                 if msg.battery_remaining != -1 else -1)
                    self._telemetry_accumulator.update({
                        "battery_voltage":        total_v,
                        "battery_current":         current_a,
                        "battery_mah_used":        mah_used,
                        "battery_remaining":       remaining,
                        "battery_cells":           len(cell_v),
                        "battery_cell_voltages":   cell_v,
                    })
            elif msg_type == "GLOBAL_POSITION_INT":
                # relative_alt = ev noktasına göre GPS-EKF fused irtifa (mm → m)
                # "alt" anahtarını da buradan güncelliyoruz; Mission Planner ile örtüşür
                rel = msg.relative_alt / 1000.0
                self._telemetry_accumulator.update({
                    "lat":          msg.lat / 1e7,
                    "lon":          msg.lon / 1e7,
                    "alt":          rel,        # ← birincil irtifa (relative, MP ile aynı)
                    "relative_alt": rel,
                    "vx":           msg.vx / 100.0,
                    "vy":           msg.vy / 100.0,
                    "vz":           msg.vz / 100.0,
                    "hdg":          msg.hdg / 100.0,
                })
            elif msg_type == "MAG_CAL_PROGRESS":
                self.mag_cal_progress.emit(
                    int(msg.compass_id),
                    int(msg.completion_pct),
                    float(getattr(msg, 'fitness', 0.0)),
                )
            elif msg_type == "MAG_CAL_REPORT":
                self.mag_cal_report.emit(
                    int(msg.compass_id),
                    int(msg.cal_status),
                    float(msg.fitness),
                )
            elif msg_type == "HOME_POSITION":
                home_lat = msg.latitude  / 1e7
                home_lon = msg.longitude / 1e7
                home_alt = msg.altitude  / 1000.0
                self._telemetry_accumulator.update({
                    "home_lat": home_lat,
                    "home_lon": home_lon,
                    "home_alt": home_alt,
                })
                self.home_position_updated.emit(home_lat, home_lon, home_alt)
            elif msg_type == "PARAM_VALUE":
                p_name  = msg.param_id.rstrip("\x00") if isinstance(msg.param_id, str) else msg.param_id
                p_value = float(msg.param_value)
                p_count = int(getattr(msg, "param_count", 0))
                self.param_received.emit(p_name, p_value)
                # Cache: tüm parametreler gelince toplu DB yazımı
                self._param_cache[p_name] = p_value
                if p_count > 0:
                    self._param_total = p_count
                if (self._param_total > 0
                        and len(self._param_cache) >= self._param_total
                        and self._db_conn is not None):
                    local_storage.save_params_batch(self._db_conn, self._param_cache)
                    self._param_total = 0  # tekrar tetiklenmesin
            elif msg_type == "LOG_ENTRY":
                self.log_entry_received.emit({
                    "id": msg.id,
                    "num_logs": msg.num_logs,
                    "last_log_num": msg.last_log_num,
                    "time_utc": msg.time_utc,
                    "size": msg.size,
                })
            elif msg_type in ("ESC_TELEMETRY_1_TO_4",   "ESC_TELEMETRY_5_TO_8",
                              "ESC_TELEMETRY_9_TO_12",  "ESC_TELEMETRY_13_TO_16"):
                _ESC_BASE = {"ESC_TELEMETRY_1_TO_4":    0, "ESC_TELEMETRY_5_TO_8":   4,
                              "ESC_TELEMETRY_9_TO_12":  8, "ESC_TELEMETRY_13_TO_16": 12}
                base = _ESC_BASE[msg_type]
                for i in range(4):
                    b = base + i
                    self._esc_all['rpm'][b]  = msg.rpm[i]
                    self._esc_all['volt'][b] = msg.voltage[i] / 100.0
                    self._esc_all['curr'][b] = msg.current[i] / 100.0
                    self._esc_all['temp'][b] = msg.temperature[i]
                    self._esc_all['mah'][b]  = msg.totalcurrent[i]
                # Bağlı ESC'leri bul: voltaj > 5 V olan slotlar (boşta bile batarya gerilimini gösterir)
                slots = [i for i in range(16) if self._esc_all['volt'][i] > 5.0][:4]
                if not slots:
                    slots = [9, 10, 11, 12]   # Fallback: kanal 10-13 (0-tabanlı indeks)
                self._telemetry_accumulator.update({
                    "esc_rpm":     [self._esc_all['rpm'][i]  for i in slots],
                    "esc_voltage": [self._esc_all['volt'][i] for i in slots],
                    "esc_current": [self._esc_all['curr'][i] for i in slots],
                    "esc_temp":    [self._esc_all['temp'][i] for i in slots],
                    "esc_mah":     [self._esc_all['mah'][i]  for i in slots],
                })
            elif msg_type == "VIBRATION":
                self._telemetry_accumulator["vib_x"] = getattr(msg, "vibration_x", 0.0)
                self._telemetry_accumulator["vib_y"] = getattr(msg, "vibration_y", 0.0)
                self._telemetry_accumulator["vib_z"] = getattr(msg, "vibration_z", 0.0)
            elif msg_type == "PID_TUNING":
                self._telemetry_accumulator["pid_err"] = getattr(msg, "pe", 0.0)

            # Telemetri yayını — ZAMAN TABANLI throttle (≤15 Hz).
            # Eskiden "her 5 mesaj"dı; mesaj seli (çift stream/yüksek hız) olunca
            # emit hızı kontrolsüz artıyordu. Artık mesaj hızından bağımsız sabit tavan.
            now = time.time()
            if (self._telemetry_accumulator
                    and now - self._last_telemetry_emit >= self._telemetry_min_interval):
                self._last_telemetry_emit = now
                data = dict(self._telemetry_accumulator)
                data["last_heartbeat_age"] = now - self._last_heartbeat_time
                # ── PİL YÜZDESİ — GERİLİM TABANLI (tek kaynak: core/batarya.py) ──
                # NEDEN burada, tek noktada: yüzde SYS_STATUS ve BATTERY_STATUS
                # dallarının İKİSİNDE de üretilebilirdi; o zaman aynı formül iki
                # kez yazılmış olurdu. Yayın anında birikmiş sözlükteki NİHAİ
                # `battery_voltage` üzerinden bir kez hesaplanır — hangi mesaj
                # gerilimi son yazdıysa o kullanılır.
                # NOT: FC'nin kendi `battery_remaining` alanı (mAh sayımı)
                # SİLİNMEZ; kayıt ve karşılaştırma için sözlükte kalır. Arayüz
                # artık aşağıdaki gerilim tabanlı yüzdeyi gösterir
                # (kullanıcı kararı 2026-09-02: 25.2 V=%100, 21.0 V=%0).
                # Gerilim yoksa değer None'dır — arayüz "veri yok" gösterir,
                # %0 GÖSTERMEZ.
                data[batarya.TELEMETRI_ANAHTARI] = batarya.yuzde_gerilimden(
                    data.get("battery_voltage", None))
                self.telemetry_updated.emit(data)

    def _request_streams(self) -> None:
        """Telemetri akışlarını ister (radyo-uyumlu hızlar).

        ArduPilot ≥4.x için SET_MESSAGE_INTERVAL (#511) asıl yöntem. Eski FC (≤3.x)
        fallback'i için HEDEFLİ REQUEST_DATA_STREAM gönderilir — ama 'ALL' (stream 0)
        ARTIK GÖNDERİLMEZ: ALL@4Hz, RAW_IMU/SCALED_PRESSURE/SERVO_OUTPUT gibi gereksiz
        mesajları akıtıp radyoyu doyuruyordu (mission indirme 500ms/WP nedeni).
        """
        if self._mav is None:
            return

        # ── Eski FC fallback: HEDEFLİ streamler (ALL YOK) ────────────────────
        #   2=EXT_STATUS 3=RC 6=POSITION 10=EXTRA1(ATTITUDE) 11=EXTRA2(VFR) 12=EXTRA3
        LEGACY_STREAMS = [(6, 4), (10, 8), (11, 5), (12, 1), (2, 2), (3, 4)]
        for stream_id, rate_hz in LEGACY_STREAMS:
            try:
                self._mav.mav.request_data_stream_send(
                    self._mav.target_system, self._mav.target_component,
                    stream_id, rate_hz, 1,
                )
            except Exception as exc:
                logger.warning("REQUEST_DATA_STREAM başarısız (stream=%d): %s", stream_id, exc)

        # ── Modern: SET_MESSAGE_INTERVAL (ArduPilot ≥4.x) ────────────────────
        self._apply_message_intervals(_MSG_INTERVALS_NORMAL)

    def _apply_message_intervals(self, intervals: dict) -> None:
        """Verilen {msg_id: interval_us} tablosunu SET_MESSAGE_INTERVAL ile uygular."""
        if self._mav is None:
            return
        sys_id = self._mav.target_system or 1
        for msg_id, interval_us in intervals.items():
            try:
                self._mav.mav.command_long_send(
                    sys_id, 1,      # comp=1: doğrudan autopilot
                    511,            # MAV_CMD_SET_MESSAGE_INTERVAL
                    0,
                    float(msg_id), float(interval_us),
                    0, 0, 0, 0, 0,
                )
            except Exception as exc:
                logger.warning("SET_MESSAGE_INTERVAL başarısız (msg=%d): %s", msg_id, exc)

    def _set_transfer_mode(self, on: bool) -> None:
        """Mission upload/download sırasında ağır telemetri akışlarını geçici kıs
        (radyo MISSION_ITEM'a açılır → indirme 3-4× hızlanır). Bitince normale döner."""
        try:
            self._apply_message_intervals(
                _MSG_INTERVALS_TRANSFER if on else _MSG_INTERVALS_NORMAL)
        except Exception as exc:
            logger.warning("Transfer modu akış ayarı başarısız: %s", exc)

        logger.info("Telemetri akışları talep edildi (legacy + modern)")

    def _ap_target(self) -> tuple:
        """Komutlar için gerçek autopilot (sys, comp) — ASLA comp=0 gönderme.

        wait_heartbeat() MAVProxy'nin comp=0 heartbeat'ini yakalamış olabilir;
        broadcast'e gönderim mission protokolünü ve diğer komutları bozar.
        Heartbeat handler is_autopilot=True olduğunda _autopilot_sys/comp'u
        günceller; yoksa ArduPilot konvansiyonu (1, 1)'e düşeriz.
        """
        sys_id  = self._autopilot_sys  or self._mav.target_system  or 1
        comp_id = self._autopilot_comp or 1
        if comp_id == 0:
            comp_id = 1
        return sys_id, comp_id

    def _execute_ned_velocity(self, vx: float, vy: float, vz: float) -> None:
        """SET_POSITION_TARGET_LOCAL_NED mesajını doğrudan gönder."""
        if self._mav is None:
            return
        try:
            sys_id, comp_id = self._ap_target()
            self._mav.mav.set_position_target_local_ned_send(
                0,                                    # time_boot_ms
                sys_id, comp_id,
                mavutil.mavlink.MAV_FRAME_LOCAL_NED,  # frame
                0b0000_1111_1100_0111,                # type_mask: yalnızca vx,vy,vz
                0.0, 0.0, 0.0,                        # x,y,z pos (kullanılmıyor)
                vx, vy, vz,                           # hız m/s
                0.0, 0.0, 0.0,                        # ivme (kullanılmıyor)
                0.0, 0.0,                             # yaw, yaw_rate
            )
        except Exception as exc:
            logger.error("NED velocity gönderilemedi: %s", exc)

    @pyqtSlot(float, float, float)
    def send_ned_velocity(self, vx: float, vy: float, vz: float) -> None:
        """Dış thread'den çağrılır — kuyruğa ekler (doluysa en eskiyi at)."""
        try:
            self._ned_vel_queue.put_nowait((vx, vy, vz))
        except queue.Full:
            try:
                self._ned_vel_queue.get_nowait()
            except queue.Empty:
                pass
            self._ned_vel_queue.put_nowait((vx, vy, vz))

    def _execute_command(self, cmd: dict) -> None:
        try:
            cmd_id = cmd.get("id")
            params = cmd.get("params", [0] * 7)
            while len(params) < 7:
                params.append(0)
            sys_id, comp_id = self._ap_target()
            self._mav.mav.command_long_send(
                sys_id, comp_id,
                cmd_id,
                0,
                *params[:7],
            )
            logger.info("CMD gönderildi: id=%d target=%d/%d params=%s",
                        cmd_id, sys_id, comp_id, params[:7])
        except Exception as exc:
            logger.error("Komut gönderilemedi: %s", exc)

    @pyqtSlot(int, list)
    def send_command(self, cmd_id: int, params: list) -> None:
        try:
            self._command_queue.put_nowait({"id": cmd_id, "params": params})
        except queue.Full:
            logger.warning("Komut kuyruğu dolu — cmd_id=%d atlandı", cmd_id)

    def _enqueue_action(self, fn) -> None:
        """GUI thread'inden gelen SOKET-ERİŞİMLİ işi worker thread'e aktarır.

        Aşağıdaki slotlar UI thread'inde çağrılır (worker QThread nesnesi UI
        thread'ine ait; yalnızca run() worker thread'de koşar). Soket işini
        doğrudan yapmak yerine kuyruğa koyarlar → run() döngüsü çalıştırır,
        recv_match ile aynı bağlantıya eşzamanlı erişim (C4) ortadan kalkar.
        """
        try:
            self._action_queue.put_nowait(fn)
        except queue.Full:
            logger.warning("Aksiyon kuyruğu dolu — istek atlandı")

    @pyqtSlot(str, float)
    def set_param(self, name: str, value: float) -> None:
        self._enqueue_action(lambda: self._do_set_param(name, value))

    def _do_set_param(self, name: str, value: float) -> None:
        if self._mav is None:
            return
        try:
            self._mav.mav.param_set_send(
                self._mav.target_system,
                self._mav.target_component,
                name.encode("utf-8"),
                value,
                mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
            )
        except Exception as exc:
            logger.error("Parametre gönderilemedi %s: %s", name, exc)

    @pyqtSlot(bytes)
    def inject_rtcm(self, data: bytes) -> None:
        # Ayrı kuyruk — bkz. __init__'teki _rtcm_queue NEDEN notu. _enqueue_action
        # (paylaşılan _action_queue) KASITLI OLARAK kullanılmıyor.
        try:
            self._rtcm_queue.put_nowait(data)
        except queue.Full:
            logger.warning("RTCM kuyruğu dolu — düzeltme çerçevesi atlandı "
                           "(bir sonraki çerçeve zaten yolda, RTK için zararsız)")

    def _do_inject_rtcm(self, data: bytes) -> None:
        if self._mav is None:
            return
        try:
            chunk_size = 180
            chunks = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)] or [b""]
            # NEDEN: GPS_RTCM_DATA.flags biçimi (MAVLink spec) — LSB=fragmented,
            # sonraki 2 bit=fragment_id, kalan 5 bit=sequence ID. flags HER ZAMAN
            # 0 gönderiliyordu; ArduPilot flags=0 gördüğünde çerçeveyi TEK BAŞINA
            # TAM bir RTCM mesajı sanıp GPS'e olduğu gibi iletiyor. 180 baytı aşan
            # (>1 parçalı) çerçevelerde bu, her parçayı ayrı ayrı bozuk/eksik bir
            # RTCM mesajı olarak GPS'e gönderiyordu — GPS'in CRC24Q kontrolü bunları
            # sessizce reddediyordu. Çoğu MSM gözlem mesajı (RTK Float/Fixed için
            # asıl gereken taşıyıcı-faz verisi) 180 bayttan büyük olduğundan, bu
            # sistematik olarak TAM OLARAK en kritik RTCM mesajlarını düşürüyordu
            # (küçük mesajlar — istasyon koordinatı vb. — tek parçada gittiği için
            # etkilenmiyordu, bu da DGPS seviyesinde kısmi düzeltmeyi açıklıyor).
            if len(chunks) > 4:
                logger.warning(
                    "RTCM çerçevesi çok büyük (%d bayt, %d parça) — GPS_RTCM_DATA "
                    "protokolü en fazla 4 parçayı destekliyor, çerçeve atlanıyor",
                    len(data), len(chunks))
                return
            seq = self._rtcm_seq
            self._rtcm_seq = (self._rtcm_seq + 1) % 32
            fragmented = len(chunks) > 1
            for frag_id, chunk in enumerate(chunks):
                n = len(chunk)
                # NEDEN dolgu: GPS_RTCM_DATA.data alanı SABİT 180 bayt —
                # pymavlink kısa bir bytearray verilince 180'e kadar
                # indekslemeye çalışıp "bytearray index out of range"
                # patlıyordu (RTCM çerçevesi 180'in tam katı olmadığında,
                # yani neredeyse her zaman son parçada). Gerçek uzunluk
                # (n) ayrı alanla iletildiği için dolgu baytları alıcı
                # tarafından yok sayılır, veri bütünlüğünü bozmaz.
                padded = bytes(chunk) + b"\x00" * (chunk_size - n)
                flags = (1 if fragmented else 0) | (frag_id << 1) | (seq << 3)
                self._mav.mav.gps_rtcm_data_send(flags, n, bytearray(padded))
            logger.info("RTCM_TESHIS: %d bayt gönderildi (İHA'ya, %d parça)",
                        len(data), len(chunks))
        except Exception as exc:
            logger.error("RTCM enjeksiyonu başarısız: %s", exc)

    @pyqtSlot()
    def request_parameters(self) -> None:
        self._enqueue_action(self._do_request_parameters)

    def _do_request_parameters(self) -> None:
        if self._mav is None:
            return
        try:
            self._mav.mav.param_request_list_send(
                self._mav.target_system, self._mav.target_component
            )
        except Exception as exc:
            logger.error("Parametre listesi isteği başarısız: %s", exc)

    @pyqtSlot(int, int, int, int, int)
    def send_manual_control(self, x: int, y: int, z: int, r: int, buttons: int) -> None:
        """MANUAL_CONTROL mesajı gönderir (joystick için). x,y,z,r: -1000..1000"""
        self._enqueue_action(lambda: self._do_manual_control(x, y, z, r, buttons))

    def _do_manual_control(self, x: int, y: int, z: int, r: int, buttons: int) -> None:
        if self._mav is None:
            return
        try:
            self._mav.mav.manual_control_send(
                self._mav.target_system,
                x, y, z, r, buttons,
            )
        except Exception as exc:
            logger.error("MANUAL_CONTROL gönderilemedi: %s", exc)

    @pyqtSlot()
    def request_log_list(self) -> None:
        """Drone'daki log listesini ister (LOG_REQUEST_LIST → LOG_ENTRY yanıtları)."""
        self._enqueue_action(self._do_request_log_list)

    def _do_request_log_list(self) -> None:
        if self._mav is None:
            return
        try:
            self._mav.mav.log_request_list_send(
                self._mav.target_system,
                self._mav.target_component,
                0,       # start: ilk log
                0xFFFF,  # end: son log
            )
            logger.info("LOG_REQUEST_LIST gönderildi")
        except Exception as exc:
            logger.error("Log listesi isteği başarısız: %s", exc)

    @pyqtSlot(int)
    def download_log(self, log_id: int) -> None:
        """Belirli bir log dosyasını indirir — ana döngüde _do_log_download() çalıştırır."""
        self._log_download_pending = log_id

    @pyqtSlot(dict, list)
    def upload_mission(self, params: dict, waypoints: list) -> None:
        """Görevi FC'ye yükler — AKTİF bağlantı çubuğu bağlantısını kullanır
        (COM/serial, TCP veya UDP — neye bağlıysan ona)."""
        if self._mav is None:
            self.mission_upload_done.emit(False, "Drone bağlı değil (bağlantı çubuğundan bağlanın)")
            return
        alt = float(params.get("flight_altitude_m", 15.0))
        if waypoints and str(waypoints[0].get("type", "")).upper() == "TAKEOFF":
            alt = float(waypoints[0].get("alt", alt))
        self._stored_flight_alt = alt
        self._upload_waypoints = list(waypoints)

    @pyqtSlot()
    def request_mission(self) -> None:
        """Drone'dan görevi indirir — AKTİF bağlantı çubuğu bağlantısını kullanır."""
        if self._mav is None:
            self.mission_download_done.emit(False, [], "Drone bağlı değil (bağlantı çubuğundan bağlanın)")
            return
        self._download_mission_pending = True

    @pyqtSlot()
    def start_flight_sequence(self) -> None:
        """GUIDED → ARM → TAKEOFF → AUTO sekansını başlatır."""
        if self._mav is None:
            self.flight_sequence_status.emit("Hata: Drone bağlı değil")
            return
        self._flight_seq_pending = True

    def _do_mission_upload(self, waypoints: list) -> None:
        """FC'ye MAVLink mission protokolü ile waypoint yükler."""
        count = len(waypoints)
        sys_id, comp_id = self._ap_target()
        self.mission_upload_progress.emit(0, count)
        logger.info("Görev yükleniyor: %d WP → sys=%d comp=%d", count, sys_id, comp_id)

        last_hb = time.time()

        def _hb():
            nonlocal last_hb
            # Uzun işlem (upload/download) run loop'u bloke eder; watchdog'un
            # worker'ı "takıldı" sanıp interrupt etmemesi için canlılık sayacını artır.
            self.heartbeat_counter += 1
            if time.time() - last_hb > 1.0:
                try:
                    self._mav.mav.heartbeat_send(
                        mavutil.mavlink.MAV_TYPE_GCS,
                        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                        0, 0, 0,
                    )
                except Exception:
                    pass
                last_hb = time.time()

        self._set_transfer_mode(True)   # ağır telemetriyi kıs → radyo upload'a açılır
        try:
            self._mav.mav.mission_clear_all_send(sys_id, comp_id)
            _hb()

            # ── BUG FIX 1: CLEAR_ALL ACK'ini önceden tüket ──────────────────
            # ArduPilot, MISSION_CLEAR_ALL'a MISSION_ACK(type=0) ile yanıt verir.
            # Bu ACK upload döngüsüne girerse "hiç WP gönderilmeden görev kabul edildi"
            # şeklinde yanlış başarı döner; drone WP bekler ama gelmez → sonraki
            # denemede drone yanıt vermez → timeout.
            _clr_dl = time.time() + 1.5
            while time.time() < _clr_dl:
                _hb()
                _clr_ack = self._mav.recv_match(
                    type=["MISSION_ACK"], blocking=True, timeout=0.3
                )
                if _clr_ack is not None:
                    logger.debug("CLEAR_ALL ACK tüketildi: type=%d", _clr_ack.type)
                    break
            time.sleep(0.2)

            self._mav.mav.mission_count_send(sys_id, comp_id, count)
            logger.info("MISSION_COUNT(%d) gönderildi", count)

            next_seq = 0
            retries = 0
            wp_sent_count = 0   # BUG FIX 2: Hiç WP gönderilmeden gelen ACK'i yoksay
            deadline = time.time() + max(count * 5 + 30, 60)

            while time.time() < deadline:
                _hb()
                msg = self._mav.recv_match(
                    type=["MISSION_REQUEST", "MISSION_REQUEST_INT", "MISSION_ACK"],
                    blocking=True,
                    timeout=1.0
                )
                if msg is None:
                    retries += 1
                    # BUG FIX 3: Retry limiti 5 → 10 (ağ gecikmelerine karşı tolerans)
                    if retries >= 10:
                        self.mission_upload_done.emit(
                            False,
                            "MISSION_REQUEST zaman aşımı — drone yanıt vermiyor.\n"
                            "Bağlantıyı ve drone ARM durumunu kontrol edin."
                        )
                        return
                    logger.warning("Upload timeout (%d/10) — MISSION_COUNT tekrar gönderiliyor", retries)
                    self._mav.mav.mission_count_send(sys_id, comp_id, count)
                    continue

                retries = 0
                mtype = msg.get_type()

                # NOT: recv_match(type=[...]) filtresi HEARTBEAT döndürmez;
                # önceki HEARTBEAT dalı ölü koddu — kaldırıldı (BUG FIX).

                if mtype == "MISSION_ACK":
                    # BUG FIX 2: Hiç WP gönderilmemişse bu ACK CLEAR_ALL'dan kalan
                    # artık olabilir (gecikmeli paket). Yoksay, upload'ı sürdür.
                    if wp_sent_count == 0:
                        logger.debug(
                            "Erken MISSION_ACK (type=%d) yoksayıldı "
                            "— CLEAR_ALL artığı, henüz WP gönderilmedi", msg.type
                        )
                        continue
                    if msg.type == mavutil.mavlink.MAV_MISSION_ACCEPTED:
                        logger.info("Görev kabul edildi: %d WP", count)
                        self.mission_upload_done.emit(True, f"{count} WP başarıyla yüklendi")
                        return
                    logger.error("Görev reddedildi: MISSION_ACK type=%d", msg.type)
                    self.mission_upload_done.emit(False, f"Görev reddedildi (type={msg.type})")
                    return

                if mtype not in ("MISSION_REQUEST", "MISSION_REQUEST_INT"):
                    continue

                req_seq = getattr(msg, "seq", -1)
                if req_seq < 0 or req_seq >= count:
                    continue

                wp = waypoints[req_seq]
                wp_type = str(wp.get("type", "WAYPOINT")).upper()

                if wp_type == "TAKEOFF":
                    cmd, p1, p2, p3, p4 = 22, 0.0, 0.0, 0.0, float("nan")
                elif wp_type == "LAND":
                    cmd, p1, p2, p3, p4 = 21, 0.0, 0.0, 0.0, float("nan")
                elif wp_type == "RTL":
                    cmd, p1, p2, p3, p4 = 20, 0.0, 0.0, 0.0, 0.0
                elif wp_type == "LOITER":
                    cmd, p1, p2, p3, p4 = 17, 0.0, 0.0, 0.0, float("nan")
                elif wp_type == "SPLINE":
                    cmd = 16
                    p1 = float(wp.get("hold", 0))
                    p2 = float(wp.get("accept_radius", 2))
                    p3, p4 = 0.0, float("nan")
                else:
                    cmd = 16
                    p1 = float(wp.get("hold", 0))
                    p2 = float(wp.get("accept_radius", 2))
                    p3 = float(wp.get("pass_radius", 0))
                    p4 = float(wp.get("yaw", float("nan")))

                lat = float(wp.get("lat", 0))
                lon = float(wp.get("lon", 0))
                alt = float(wp.get("alt", 10))
                current = 1 if req_seq == 0 else 0

                if mtype == "MISSION_REQUEST_INT":
                    self._mav.mav.mission_item_int_send(
                        sys_id, comp_id, req_seq,
                        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                        cmd, current, 1,
                        p1, p2, p3, p4,
                        int(lat * 1e7), int(lon * 1e7), alt,
                        mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
                    )
                else:
                    self._mav.mav.mission_item_send(
                        sys_id, comp_id, req_seq,
                        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                        cmd, current, 1,
                        p1, p2, p3, p4,
                        lat, lon, alt,
                    )

                wp_sent_count += 1
                logger.debug("WP[%d] gönderildi: %s lat=%.6f lon=%.6f alt=%.1f",
                             req_seq, wp_type, lat, lon, alt)
                self.mission_upload_progress.emit(req_seq + 1, count)
                if req_seq >= next_seq:
                    next_seq = req_seq + 1

            self.mission_upload_done.emit(False, "Yükleme zaman aşımı")

        except Exception as exc:
            logger.error("Mission upload hatası: %s", exc)
            self.mission_upload_done.emit(False, str(exc))
        finally:
            self._set_transfer_mode(False)   # normal telemetri hızlarına dön

    def _do_mission_download(self) -> None:
        """FC'den görev listesini indirir."""
        sys_id, comp_id = self._ap_target()
        logger.info("Görev indiriliyor (MISSION_REQUEST_LIST) sys=%d comp=%d", sys_id, comp_id)

        last_hb = time.time()
        def _hb():
            nonlocal last_hb
            # Uzun işlem (upload/download) run loop'u bloke eder; watchdog'un
            # worker'ı "takıldı" sanıp interrupt etmemesi için canlılık sayacını artır.
            self.heartbeat_counter += 1
            if time.time() - last_hb > 1.0:
                try:
                    self._mav.mav.heartbeat_send(
                        mavutil.mavlink.MAV_TYPE_GCS,
                        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                        0, 0, 0,
                    )
                except Exception:
                    pass
                last_hb = time.time()

        self._set_transfer_mode(True)   # ağır telemetriyi kıs → radyo MISSION_ITEM'a açılır
        try:
            self._mav.mav.mission_request_list_send(sys_id, comp_id)

            # MISSION_COUNT bekle
            msg = self._mav.recv_match(type=["MISSION_COUNT"], blocking=True, timeout=5.0)
            if not msg:
                self.mission_download_done.emit(False, [], "MISSION_COUNT alınamadı (zaman aşımı).")
                return

            count = msg.count
            logger.info("Drone %d WP bildirdi. İndirme başlıyor...", count)

            if count == 0:
                self.mission_download_done.emit(True, [], "Görev listesi boş.")
                return

            self.mission_download_progress.emit(0, count)   # ilerleme: başlangıç
            waypoints = []
            for seq in range(count):
                retries = 0
                while retries < 5:
                    _hb()
                    self._mav.mav.mission_request_int_send(sys_id, comp_id, seq)
                    item_msg = self._mav.recv_match(type=["MISSION_ITEM_INT", "MISSION_ITEM"], blocking=True, timeout=2.0)
                    
                    if item_msg and getattr(item_msg, "seq", -1) == seq:
                        wp_lat = getattr(item_msg, "x", 0)
                        wp_lon = getattr(item_msg, "y", 0)
                        if item_msg.get_type() == "MISSION_ITEM_INT":
                            wp_lat = wp_lat / 1e7
                            wp_lon = wp_lon / 1e7
                            
                        wp = {
                            "idx": seq + 1,
                            "lat": wp_lat,
                            "lon": wp_lon,
                            "alt": getattr(item_msg, "z", 0),
                            "cmd_id": getattr(item_msg, "command", 16),
                            "delay": getattr(item_msg, "param1", 0)  # Delay genellikle param1'de tutulur
                        }
                        waypoints.append(wp)
                        logger.debug("WP[%d] alındı", seq)
                        self.mission_download_progress.emit(seq + 1, count)
                        break
                    else:
                        retries += 1
                        logger.warning("WP[%d] alınamadı, tekrar deneniyor (%d/5)", seq, retries)
                
                if retries >= 5:
                    self.mission_download_done.emit(False, [], f"WP[{seq}] alınamadı, iptal ediliyor.")
                    return

            self._mav.mav.mission_ack_send(sys_id, comp_id, mavutil.mavlink.MAV_MISSION_ACCEPTED)
            self.mission_download_done.emit(True, waypoints, f"{count} WP başarıyla indirildi.")

        except Exception as exc:
            logger.error("Mission download hatası: %s", exc)
            self.mission_download_done.emit(False, [], str(exc))
        finally:
            self._set_transfer_mode(False)   # normal telemetri hızlarına dön

    def _do_flight_sequence(self) -> None:
        """GUIDED → ARM → TAKEOFF → bekle → AUTO sekansı."""
        sys_id, comp_id = self._ap_target()
        alt = self._stored_flight_alt
        logger.info("Uçuş sekansı: GUIDED → ARM → TAKEOFF(%.0fm) → AUTO", alt)

        last_hb = time.time()

        def _hb():
            nonlocal last_hb
            # Uzun işlem (upload/download) run loop'u bloke eder; watchdog'un
            # worker'ı "takıldı" sanıp interrupt etmemesi için canlılık sayacını artır.
            self.heartbeat_counter += 1
            if time.time() - last_hb > 1.0:
                try:
                    self._mav.mav.heartbeat_send(
                        mavutil.mavlink.MAV_TYPE_GCS,
                        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                        0, 0, 0,
                    )
                except Exception:
                    pass
                last_hb = time.time()

        def _cmd(cmd_id, p1=0.0, p2=0.0, p3=0.0, p4=0.0, p5=0.0, p6=0.0, p7=0.0):
            self._mav.mav.command_long_send(
                sys_id, comp_id, cmd_id, 0,
                p1, p2, p3, p4, p5, p6, p7,
            )

        try:
            self.flight_sequence_status.emit("GUIDED moduna geçiliyor...")
            _cmd(mavutil.mavlink.MAV_CMD_DO_SET_MODE,
                 float(mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED), 4.0)
            time.sleep(1.5)

            self.flight_sequence_status.emit("ARM komutu gönderiliyor...")
            _cmd(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 1.0)
            time.sleep(1.5)

            self.flight_sequence_status.emit("Motorlara takeoff öncesi hafif gaz veriliyor...")
            try:
                self._mav.mav.rc_channels_override_send(
                    sys_id, comp_id,
                    0, 0, 1150, 0, 0, 0, 0, 0
                )
                time.sleep(1.0)
                # RC override iptal
                self._mav.mav.rc_channels_override_send(
                    sys_id, comp_id,
                    0, 0, 0, 0, 0, 0, 0, 0
                )
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"Gaz verme basarisiz: {e}")

            self.flight_sequence_status.emit(f"Kalkış ({alt:.0f}m) komutu gönderiliyor...")
            _cmd(mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, p7=float(alt))

            # İrtifa bekle (maks 40s, alt > %85 hedef)
            self.flight_sequence_status.emit(f"İrtifa bekleniyor ({alt:.0f}m)...")
            deadline = time.time() + 40
            while time.time() < deadline:
                _hb()
                m = self._mav.recv_match(blocking=True, timeout=0.5)
                if m and m.get_type() == "GLOBAL_POSITION_INT":
                    if m.relative_alt / 1000.0 >= alt * 0.85:
                        break

            time.sleep(1.0)

            self.flight_sequence_status.emit("AUTO moduna geçiliyor...")
            for attempt in range(1, 4):
                _cmd(mavutil.mavlink.MAV_CMD_DO_SET_MODE,
                     float(mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED), 3.0)
                # 2s içinde AUTO onayı bekle
                dl = time.time() + 2.0
                while time.time() < dl:
                    _hb()
                    m = self._mav.recv_match(blocking=True, timeout=0.2)
                    if m and m.get_type() == "HEARTBEAT":
                        src = m.get_srcSystem()
                        if (src == self._mav.target_system
                                and getattr(m, "autopilot", -1) != mavutil.mavlink.MAV_AUTOPILOT_INVALID):
                            if _COPTER_MODES.get(m.custom_mode, "") == "AUTO":
                                self.flight_sequence_status.emit("Görev başladı — AUTO modu aktif")
                                logger.info("AUTO modu onaylandı — görev aktif")
                                return
                logger.warning("AUTO modu yok (deneme %d/3)", attempt)

            self.flight_sequence_status.emit("UYARI: AUTO moduna geçilemedi")
            logger.error("AUTO moduna geçilemedi (3 deneme)")

        except Exception as exc:
            logger.error("Uçuş sekansı hatası: %s", exc)
            self.flight_sequence_status.emit(f"Hata: {exc}")

    def _do_log_download(self, log_id: int) -> None:
        """MAVLink LOG_REQUEST_DATA / LOG_DATA protokolü ile log indirir."""
        if self._mav is None:
            self.log_download_done.emit(False, "", b"")
            return

        sys_id, comp_id = self._ap_target()
        logger.info("Log indiriliyor: id=%d", log_id)

        # Önce log listesinden boyutu öğrenmek için LOG_REQUEST_LIST gönder
        try:
            self._mav.mav.log_request_list_send(sys_id, comp_id, log_id, log_id)
        except Exception as exc:
            logger.error("Log listesi isteği başarısız: %s", exc)
            self.log_download_done.emit(False, "", b"")
            return

        total_size = 0
        deadline = time.time() + 5.0
        while time.time() < deadline:
            m = self._mav.recv_match(blocking=True, timeout=0.5)
            if m and m.get_type() == "LOG_ENTRY" and getattr(m, "id", -1) == log_id:
                total_size = m.size
                break

        if total_size == 0:
            self.log_download_done.emit(False, f"log_{log_id}.bin", b"")
            return

        # LOG_REQUEST_DATA ile veri iste
        CHUNK = 90   # MAVLink LOG_DATA max payload (90 byte)
        buf = bytearray(total_size)
        received_offsets: set = set()

        try:
            self._mav.mav.log_request_data_send(
                sys_id, comp_id, log_id, 0, total_size
            )
        except Exception as exc:
            logger.error("LOG_REQUEST_DATA başarısız: %s", exc)
            self.log_download_done.emit(False, f"log_{log_id}.bin", b"")
            return

        deadline = time.time() + max(total_size / 1000 + 30, 60)
        while time.time() < deadline and not self.isInterruptionRequested():
            m = self._mav.recv_match(blocking=True, timeout=0.5)
            if m is None:
                continue
            if m.get_type() == "LOG_DATA" and getattr(m, "id", -1) == log_id:
                # NEDEN yerel try/except: ofs/m.count FC'den gelen ham telemetri —
                # bozuk/aralık dışı bir paket (ör. ofs > total_size) buf[ofs:end]
                # slice atamasında ValueError/başka istisna atabilirdi. Eskiden bu
                # hata burada yakalanmadığı için _do_log_download'ı çağıran run()
                # döngüsündeki GENEL except Exception'a düşüyor, TÜM MAVLink
                # bağlantısı kapatılıp yeniden bağlanmaya (full reconnect) yol
                # açıyordu — tek bozuk log paketi için orantısız bir sonuç.
                # Kardeş worker'lardaki (rpi_status_worker, video_stream_worker)
                # savunmacı desen: paketi atla/logla, işleme (burada: indirmeye)
                # devam et.
                try:
                    ofs  = m.ofs
                    data = bytes(m.data[:m.count])
                    end  = min(ofs + len(data), total_size)
                    buf[ofs:end] = data[:end - ofs]
                    received_offsets.add(ofs)
                except Exception as exc:
                    logger.warning(
                        "Log paketi atlandı (bozuk/aralık dışı ofs=%s count=%s): %s",
                        getattr(m, "ofs", "?"), getattr(m, "count", "?"), exc,
                    )
                    continue
                self.log_download_progress.emit(len(received_offsets) * CHUNK, total_size)
                if end >= total_size:
                    break

        # Log bitiş sinyali
        try:
            self._mav.mav.log_request_end_send(sys_id, comp_id)
        except Exception:
            pass

        filename = f"log_{log_id:04d}.bin"
        self.log_download_done.emit(True, filename, bytes(buf))
        logger.info("Log indirildi: %s (%d bayt)", filename, total_size)

    def stop(self) -> None:
        self.requestInterruption()
        # Socket'i hemen kapat → recv_match(blocking) bloke çözülür.
        # _invalidate_connection() KİLİTLİ + JENERASYONLU yol (bkz. __init__
        # NEDEN) — run() bu kapanışı bir arıza değil, isteğe bağlı kesme
        # olarak tanır ve sessizce çıkar.
        self._invalidate_connection()
        self.wait(5000)  # Maks 5 saniye bekle; donmuşsa devam et
