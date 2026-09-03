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

CHUNK_SIZE = 512


_GGA_RESEND_INTERVAL_S = 30.0   # Mission Planner CommsNTRIP.cs ile AYNI (kullanıcı talebi — "aynı mantıkla çalışsın")

# NTRIP başlık okuma bütçesi (2026-09-03, denetimde bulundu — YÜKSEK):
# eski kod bu süreyi HİÇ sınırlamıyordu, yalnız TEK bir recv()'i sock.settimeout(15)
# ile sınırlıyordu. Karşı taraf ara sıra 1 bayt damlatıp \r\n\r\n'i HİÇ
# göndermezse recv() her seferinde "başarıyla" döner, döngü isInterruptionRequested()'i
# HİÇ kontrol etmediği için SONSUZA KADAR sürer — stop()'un süresiz wait()'i ile
# birleşince YKİ kapanışı tamamen kilitleniyordu (deneyle üretildi).
_NTRIP_BASLIK_BUDGET_S = 15.0
_NTRIP_BASLIK_MAX_BAYT = 8192   # kardeş worker'ların _MAX_RESPONSE_BUF deseniyle aynı
_RESYNC_MAX_BUF = 4096  # senkron kaybında tampon büyümesi için güvenlik payı


def _crc24q(data: bytes) -> int:
    """RTCM3 çerçeve sağlaması — Qualcomm CRC-24Q (poly 0x1864CFB, init 0).
    core/rtk_gps_worker.py'deki İLE AYNI — bkz. _extract_rtcm NEDEN notu."""
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
        crc &= 0xFFFFFF
    return crc


def _extract_rtcm_frames(buf: bytearray) -> list[bytes]:
    """buf'tan TAM, CRC-doğrulanmış RTCM3 çerçevelerini ayıklar, tükettiği
    kısmı buf'tan siler. Çerçeve sınırına denk gelmeyen/bozuk baytları atar.

    NEDEN (2026-08-28, sahada bulundu — kritik): NTRIP soketinden ham
    `recv()` çıktısı doğrudan `rtcm_ready.emit()`e veriliyordu — bu, gerçek
    RTCM3 çerçeve sınırlarına HİÇ hizalı olmayan, rastgele boyutta parçalar
    demekti (TCP paket sınırı ≠ mesaj sınırı). F9P kaynağı (rtk_gps_worker.py)
    HER ZAMAN tek, TAM, doğrulanmış bir çerçeve gönderiyordu ve o kaynakla
    RTK DGPS'e güvenilir şekilde ulaşıyordu; NTRIP'le (rastgele parçalarla)
    RTCM sürekli akmasına RAĞMEN fix hiç DGPS'e bile geçmiyordu (5 dakika
    boyunca eph birebir sabit kaldı — sahada ölçüldü). Kök neden kesin
    kanıtlanmadı (ArduPilot'un DroneCAN GPS'e forward ederken parça
    sınırlarını nasıl işlediği kaynak koddan doğrulanmadı) ama F9P'nin
    ÇALIŞAN deseniyle BİREBİR aynı hizaya getirmek (tam çerçeve = tam
    `inject_rtcm()` çağrısı) hem daha doğru hem de gözlemlenen farkı ortadan
    kaldırıyor."""
    frames: list[bytes] = []
    while buf:
        idx = buf.find(b"\xd3")
        if idx == -1:
            if len(buf) > _RESYNC_MAX_BUF:
                del buf[:-2]
            break
        if idx > 0:
            del buf[:idx]
        if len(buf) < 3:
            break
        length = ((buf[1] & 0x03) << 8) | buf[2]
        frame_len = 3 + length + 3
        if len(buf) < frame_len:
            break
        frame = bytes(buf[:frame_len])
        expected = _crc24q(frame[:-3])
        received = (frame[-3] << 16) | (frame[-2] << 8) | frame[-1]
        if expected == received:
            frames.append(frame)
            del buf[:frame_len]
        else:
            # Sahte 0xD3 (rastgele bayt) — bir bayt atlayıp yeniden senkronlan.
            del buf[:1]
    return frames


def _nmea_checksum(sentence_no_dollar_no_star: str) -> str:
    cs = 0
    for c in sentence_no_dollar_no_star:
        cs ^= ord(c)
    return f"{cs:02X}"


def _build_gga(lat: float, lon: float, alt_m: float) -> bytes:
    """VRS mountpoint'lerine (nmea=1) yaklaşık konum bildiren GGA cümlesi.
    Milimetre hassasiyeti GEREKMEZ — CORS ağı bu konuma en yakın sanal
    referans istasyonunu bu kaba konumla hesaplar (bkz. NEDEN notu aşağıda)."""
    t = time.gmtime()
    hhmmss = f"{t.tm_hour:02d}{t.tm_min:02d}{t.tm_sec:02d}.00"
    lat_hemi = "N" if lat >= 0 else "S"
    lat = abs(lat)
    lat_deg = int(lat)
    lat_min = (lat - lat_deg) * 60
    lon_hemi = "E" if lon >= 0 else "W"
    lon = abs(lon)
    lon_deg = int(lon)
    lon_min = (lon - lon_deg) * 60
    body = (
        f"GPGGA,{hhmmss},{lat_deg:02d}{lat_min:07.4f},{lat_hemi},"
        f"{lon_deg:03d}{lon_min:07.4f},{lon_hemi},1,08,1.0,{alt_m:.1f},M,35.0,M,,"
    )
    return f"${body}*{_nmea_checksum(body)}\r\n".encode()


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
        # NEDEN: TUSAGA-Aktif gibi VRS (Virtual Reference Station) mountpoint'leri
        # (sourcetable'da nmea=1) istemciden yaklaşık konum İSTER — göndermeyen
        # istemciye bağlantıyı "200 OK" ile kabul edip SESSİZCE hiç RTCM
        # göndermez (2026-08-28, sahada doğrulandı: GGA'sız 0 bayt, GGA'lı
        # saniyede ~1000 bayt canlı RTCM3). set_position() ile main_window
        # tarafından (anten home / ground station konumu) beslenir.
        self._lat: float | None = None
        self._lon: float | None = None
        self._alt: float = 0.0
        self.heartbeat_counter: int = 0

    def configure(self, host: str, port: int, mountpoint: str, username: str, password: str) -> None:
        self._host = host
        self._port = port
        self._mountpoint = mountpoint
        self._username = username
        self._password = password

    def set_position(self, lat: float, lon: float, alt_m: float = 0.0) -> None:
        """GGA bootstrap/tazeleme konumunu ayarlar — bkz. __init__ NEDEN notu."""
        self._lat = lat
        self._lon = lon
        self._alt = alt_m

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

        # Yanıtı bekle (ICY 200 OK = akış başladı; SOURCETABLE = mountpoint
        # tanınmadı, caster kaynak listesini döktü — İKİSİ de "200 OK" alt
        # dizesini içerir, bu yüzden İLK SATIRI birebir kontrol etmek gerekir
        # — eski kod yalnızca "200 OK" arıyordu ve SOURCETABLE yanıtını
        # YANLIŞLIKLA başarı sanıyordu (2026-08-28, sahada TUSAGA-Aktif ile
        # yakalandı: yanlış mountpoint adı "200 OK" içerdiği için "bağlandı"
        # loglandı ama hiç RTCM gelmedi).
        #
        # BAŞLIK OKUMA — SINIRLANDIRILDI (2026-09-03, denetimde bulundu —
        # YÜKSEK): eski döngü ne toplam süreye ne isInterruptionRequested()'e
        # bakıyordu; sock.settimeout(15) yalnız TEK recv()'i sınırlıyordu.
        # Karşı taraf \r\n\r\n'i HİÇ göndermeden ara sıra bayt akıtırsa
        # (captive portal / bozuk proxy) recv() hep "başarıyla" dönüp döngü
        # SONSUZA KADAR sürüyordu — stop()'un (aşağıda düzeltilen) süresiz
        # wait()'iyle birleşince YKİ kapanışı TAMAMEN kilitleniyordu.
        # DENEYLE ÜRETİLDİ: sahte caster \r\n\r\n'i asla göndermeyince
        # stop() 30 saniyede bile dönmedi (worker hâlâ çalışıyordu).
        # Artık: kısa recv dilimi (kesme bayrağı sık kontrol edilsin),
        # toplam bütçe (_NTRIP_BASLIK_BUDGET_S), tampon tavanı (kardeş
        # worker'ların _MAX_RESPONSE_BUF deseniyle aynı).
        sock.settimeout(1.0)
        _baslik_bitis = time.monotonic() + _NTRIP_BASLIK_BUDGET_S
        response_buf = b""
        while b"\r\n\r\n" not in response_buf:
            if self.isInterruptionRequested():
                raise InterruptedError("NTRIP başlık okuma kesildi (stop() istendi)")
            if time.monotonic() > _baslik_bitis:
                raise ConnectionError(
                    f"NTRIP sunucu başlığı {_NTRIP_BASLIK_BUDGET_S:.0f} s içinde "
                    "tamamlanmadı")
            if len(response_buf) > _NTRIP_BASLIK_MAX_BAYT:
                raise ConnectionError("NTRIP sunucu başlığı çok büyük — "
                                      "bozuk/kötücül yanıt olabilir")
            try:
                chunk = sock.recv(256)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("NTRIP sunucu yanıtı alınamadı")
            response_buf += chunk
        sock.settimeout(15)   # geri: aşağıdaki GGA gönderimi eski davranışla aynı

        header_bytes, _, leftover = response_buf.partition(b"\r\n\r\n")
        header = header_bytes.decode(errors="replace")
        first_line = header.split("\r\n", 1)[0].strip()
        if first_line not in ("ICY 200 OK", "HTTP/1.0 200 OK", "HTTP/1.1 200 OK"):
            raise ConnectionError(f"NTRIP bağlantı reddedildi/mountpoint tanınmadı: {header[:150]}")

        logger.info("NTRIP bağlandı: %s/%s", self._host, self._mountpoint)
        self.connection_changed.emit(True)
        rtcm_buf = bytearray(leftover)
        for frame in _extract_rtcm_frames(rtcm_buf):
            self.rtcm_ready.emit(frame)

        # VRS mountpoint'leri (nmea=1) yaklaşık konum İSTER — göndermeyen
        # istemciye bağlantıyı kabul edip SESSİZCE hiç veri göndermezler
        # (2026-08-28, sahada doğrulandı, bkz. _build_gga NEDEN notu).
        # Konum yoksa (henüz F9P fix'i gelmediyse) atlanır — mountpoint GGA
        # gerektirmiyorsa zaten sorun değil, gerektiriyorsa ilk _on_rtk_position_update
        # ile main_window zaten set_position() çağırana kadar veri gelmeyebilir.
        last_gga_sent = 0.0
        if self._lat is not None and self._lon is not None:
            sock.sendall(_build_gga(self._lat, self._lon, self._alt))
            last_gga_sent = time.time()

        # Artık bağlantı canlı; RTCM3 baytlarını oku
        sock.settimeout(1.0)
        try:
            while not self.isInterruptionRequested():
                self.heartbeat_counter += 1
                now = time.time()
                if (self._lat is not None and self._lon is not None
                        and now - last_gga_sent >= _GGA_RESEND_INTERVAL_S):
                    sock.sendall(_build_gga(self._lat, self._lon, self._alt))
                    last_gga_sent = now
                try:
                    data = sock.recv(CHUNK_SIZE)
                    if not data:
                        raise ConnectionError("NTRIP bağlantısı kapandı")
                    rtcm_buf += data
                    for frame in _extract_rtcm_frames(rtcm_buf):
                        self.rtcm_ready.emit(frame)
                except socket.timeout:
                    continue
        finally:
            sock.close()

    def stop(self) -> None:
        # SÜRESİZ wait() KALDIRILDI (2026-09-03, denetimde bulundu — YÜKSEK):
        # kardeş worker'ların hepsi sınırlı bekliyor (mavlink_worker.wait(5000),
        # rpi_status_worker/video_stream_worker/rtk_gps_worker.wait(3000));
        # ntrip_worker TEK argümansız olanıydı. main_window bunu closeEvent
        # ve "NTRIP Durdur" düğmesinden GUI THREAD'İNDEN çağırıyor — worker
        # başlık okuma döngüsünde (yukarıda düzeltildi) ya da bağlanma
        # aşamasında takılırsa uygulama SÜRESİZ donuyordu.
        # 5000 ms: başlık döngüsü artık en geç _NTRIP_BASLIK_BUDGET_S (15 s)
        # içinde kesme bayrağını görür ve genelde çok daha hızlı (kısa recv
        # dilimi) çıkar; 5 s "en azından donmuş görünmesin, devam et" bandıdır
        # — kardeş worker'ların "donmuşsa devam et" felsefesiyle aynı.
        self.requestInterruption()
        self.wait(5000)
