"""core/rtk_gps_worker.py — RTK GPS (u-blox F9P) canlı izleme + baz istasyonu aktarımı.

Aynı seri bağlantıdan (F9P tek port üzerinden NMEA + RTCM3'ü AYNI ANDA
üretebilir) iki şey okunur:
  1. NMEA (GGA/RMC/GSA) — "bu GPS şu an ne görüyor" durumu (fix türü, uydu
     sayısı, HDOP/PDOP, konum, hız/yön). SALT-OKUNUR izleme, UI'ya beslenir.
  2. RTCM3 çerçeveleri — F9P survey-in ile baz konumunu bulup TMODE'a
     geçtiğinde kendiliğinden üretmeye başlar. Bu worker bu çerçeveleri
     ayıklayıp `rtcm_ready` sinyaliyle yayınlar; main_window bunu
     mavlink_worker.inject_rtcm()'e bağlayarak İHA'ya MAVLink GPS_RTCM_DATA
     ile iletir (core/ntrip_worker.py'nin UZAK kaynak için kullandığı AYNI yol
     — kaynak burada yerel F9P, orada uzak NTRIP sunucusu).

NEDEN TEK PORT/TEK OKUYUCU: seri port aynı anda yalnızca bir işlem/handle
tarafından açılabilir (Linux'ta ikinci bir serial.Serial() "device busy"
hatası verir) — bu yüzden NMEA ve RTCM3 AYNI okuma döngüsünde, bayt
tamponundan çerçeve ayıklanarak işlenir (readline() ile satır bazlı okuma
BİNARY RTCM3 verisiyle uyumsuzdur — rastgele bir \\n baytı RTCM payload'ının
içinde de geçebilir).

F9P'yi survey-in baz moduna almak (TMODE3 + RTCM3 mesaj etkinleştirme) İÇİN
BU DOSYA KULLANILMAZ — o bir kerelik/seyrek yapılandırma işlemidir, bkz.
tools/f9p_base_config.py (pyubx2 ile UBX-CFG-VALSET gönderir).
"""
from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

_RECONNECT_DELAY_S = 3.0
_EMIT_MIN_INTERVAL_S = 0.4   # UI'ya en fazla ~2.5 Hz besle
# 2026-09-06 denetimde bulundu: F9P donup (USB enumere kalır ama veri
# üretmeyi bırakırsa) `serial.Serial(..., timeout=0.3)` istisna ATMADAN
# sürekli boş dönüyordu — panel "● bağlı / RTK FIXED" ve ESKİ koordinatları
# SONSUZA KADAR canlıymış gibi göstermeye devam ediyordu (izole testle
# doğrulandı). Aynı hata sınıfı `rpi_status_worker.py`'de bulunup
# `_STALE_TIMEOUT_S` ile düzeltilmişti, bu dosyaya hiç yayılmamıştı.
_STALE_TIMEOUT_S = 10.0
# Bu süreden uzun süre RTCM çerçevesi gelmezse baz istasyonu "pasif" sayılır
# (survey-in sürüyor, F9P henüz TMODE'a geçmedi, ya da bağlantı sorunlu).
_RTCM_ACTIVE_TIMEOUT_S = 5.0
# Senkron kaybında (ne NMEA ne RTCM3 başlangıcı bulunamıyor) tampon bu boyutu
# aşarsa zorla 1 bayt atlanır — gerçek bir çerçeve/satır asla bu kadar
# büyümez (RTCM3 azami ~1029 bayt, NMEA satırı ~120 bayt); güvenlik payı.
_RESYNC_MAX_BUF = 4096

_FIX_QUALITY = {
    "0": "FIX YOK", "1": "GPS (tekli)", "2": "DGPS",
    "3": "PPS", "4": "RTK FIXED", "5": "RTK FLOAT",
    "6": "DEAD RECKONING", "7": "MANUEL", "8": "SİMÜLASYON",
}
_FIX_MODE_2D3D = {"1": "FIX YOK", "2": "2D", "3": "3D"}


def _nmea_to_deg(val: str, hemi: str) -> float | None:
    if not val:
        return None
    try:
        v = float(val)
    except ValueError:
        return None
    deg = int(v / 100)
    minutes = v - deg * 100
    d = deg + minutes / 60.0
    if hemi in ("S", "W"):
        d = -d
    return d


def _f(val: str) -> float | None:
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _crc24q(data: bytes) -> int:
    """RTCM3 çerçeve sağlaması — Qualcomm CRC-24Q (poly 0x1864CFB, init 0)."""
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
        crc &= 0xFFFFFF
    return crc


def _ubx_checksum(data: bytes) -> bytes:
    """UBX çerçeve sağlaması (8-bit Fletcher varyantı) — class'tan itibaren
    (sync HARİÇ) hesaplanır."""
    ck_a = ck_b = 0
    for b in data:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return bytes([ck_a, ck_b])


def _build_ubx_cfg_rst_coldstart() -> bytes:
    """UBX-CFG-RST (class=0x06 id=0x04): navBbrMask=0xFFFF (cold start —
    eph/alm/health/klob/pos/clkd/osc/utc/rtc dahil TÜM navigasyon verisi
    silinir), resetMode=0x02 (Controlled Software reset, GNSS ONLY — USB/
    UART port bağlantısı KORUNUR, tam donanım resetlemez).

    NEDEN struct ile ELLE (pyubx2 KULLANILMIYOR): tools/f9p_base_config.py
    ile ilk denemede pyubx2.UBXMessage'a navBbrMask=0xFFFF verilince üretilen
    çerçevede bu alan GERÇEKTE 0x0000 çıktı (hex dökümle yakalandı) — API
    kullanımı/parametre adı yanlıştı. Format sabit ve basit (12 bayt) olduğu
    için doğrudan inşa etmek belirsizliği tamamen ortadan kaldırır."""
    import struct
    payload = struct.pack("<HBB", 0xFFFF, 0x02, 0x00)
    body = bytes([0x06, 0x04]) + struct.pack("<H", len(payload)) + payload
    return bytes([0xB5, 0x62]) + body + _ubx_checksum(body)


class RtkGpsWorker(QThread):
    """F9P'den NMEA + RTCM3 okur; NMEA durumunu ve RTCM3 çerçevelerini ayrı
    sinyallerle yayar."""

    gps_updated = pyqtSignal(dict)
    connected_changed = pyqtSignal(bool)
    # RTCM3 baz-istasyonu aktarımı — main_window bunu mavlink_worker.inject_rtcm'e bağlar.
    rtcm_ready = pyqtSignal(bytes)
    # Baz istasyonu özet durumu (aktif mi, kaç mesaj/bayt gitti, son mesajdan bu yana geçen süre).
    base_status = pyqtSignal(dict)

    def __init__(self, port: str = "/dev/ttyACM0", baud: int = 38400, parent=None):
        super().__init__(parent)
        self._port = port
        self._baud = baud
        self._state: dict = {}
        self._last_emit = 0.0
        self._last_base_emit = 0.0
        self._rtcm_msg_count = 0
        self._rtcm_bytes_total = 0
        self._rtcm_bad_frames = 0
        self._last_rtcm_ts: float | None = None
        # NEDEN (2026-08-28, kullanıcı talebi): F9P baz istasyonu modunda
        # (TMODE3) bir survey-in TAMAMLANDIKTAN sonra kendi konumunu SABİT
        # kabul edip GERÇEK ZAMANLI GPS çözümü yapmayı BIRAKIR — F9P fiziksel
        # olarak TAŞINSA bile eski (artık yanlış) konumu tekrar tekrar
        # yayınlamaya devam eder (sahada ÖLÇÜLDÜ ve doğrulandı: 15 saniye
        # boyunca birebir aynı koordinat). YKİ her başladığında OTOMATİK
        # cold start (UBX-CFG-RST, navBbrMask=0xFFFF, GNSS-only) göndermek bu
        # sorunu HER OTURUMUN başında kendiliğinden çözer — TMODE3
        # konfigürasyonu (flash'ta kalıcı) SİLİNMEZ, yalnızca navigasyon/
        # pozisyon verisi sıfırlanır, bu da otomatik olarak YENİ bir
        # survey-in sürecini tetikler.
        # NEDEN yalnızca BİR KEZ (her reconnect'te DEĞİL): geçici bir USB
        # titremesi worker'ı yeniden bağlanmaya zorlarsa, HER seferinde cold
        # start göndermek F9P'nin fix kazanma sürecini SÜREKLİ yarıda keser,
        # asla stabil bir konuma ULAŞAMAZ. Bayrak, bu worker NESNESİNİN
        # ömrü boyunca (yani YKİ'nin bir çalışma oturumu boyunca) tek
        # seferliktir — yeni bir YKİ başlangıcı (servis restart) yeni bir
        # worker nesnesi yaratır, bayrak sıfırlanır, yeni cold start gider.
        self._cold_start_sent = False

    def stop(self) -> None:
        self.requestInterruption()
        self.wait(3000)

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                self._connect_and_read()
            except Exception as exc:
                logger.debug("RTK GPS bağlantı hatası (%s): %s", self._port, exc)
                self.connected_changed.emit(False)
            for _ in range(int(_RECONNECT_DELAY_S * 10)):
                if self.isInterruptionRequested():
                    return
                time.sleep(0.1)

    def _send_cold_start(self, ser) -> None:
        """F9P'ye UBX-CFG-RST cold start gönderir — bkz. __init__/
        _cold_start_sent NEDEN notu. ACK BEKLENMEZ (bu UBX komut türü yanıt
        vermez); hata olursa RTK akışını DURDURMAZ, yalnızca loglanır."""
        try:
            frame = _build_ubx_cfg_rst_coldstart()
            ser.write(frame)
            ser.flush()
            logger.info("F9P'ye otomatik cold start gönderildi (UBX-CFG-RST, "
                       "navBbrMask=0xFFFF, GNSS-only) — birkaç saniye 'fix yok' "
                       "görülmesi normaldir, ardından yeniden survey-in başlar.")
        except Exception as exc:
            logger.warning("F9P cold start gönderilemedi (RTK akışı yine de sürüyor): %s", exc)

    def _connect_and_read(self) -> None:
        import serial   # pyserial — sistem paketi (bkz. requirements-linux.txt)

        with serial.Serial(self._port, self._baud, timeout=0.3) as s:
            logger.info("RTK GPS bağlandı: %s @ %d", self._port, self._baud)
            self.connected_changed.emit(True)
            if not self._cold_start_sent:
                self._send_cold_start(s)
                self._cold_start_sent = True
            buf = bytearray()
            last_data_ts = time.monotonic()
            while not self.isInterruptionRequested():
                try:
                    chunk = s.read(4096)
                except Exception:
                    break
                if chunk:
                    last_data_ts = time.monotonic()
                    buf += chunk
                    self._drain_buffer(buf)
                elif time.monotonic() - last_data_ts > _STALE_TIMEOUT_S:
                    logger.warning("RTK GPS %.0f s süredir sessiz — bağlantı "
                                   "koptu sayılıp yeniden bağlanılıyor", _STALE_TIMEOUT_S)
                    break
                self._maybe_emit()
                self._maybe_emit_base_status()
        self.connected_changed.emit(False)

    # ── Bayt tamponundan çerçeve ayıklama (NMEA satırı VEYA RTCM3 çerçevesi) ──
    def _drain_buffer(self, buf: bytearray) -> None:
        while buf:
            b0 = buf[0]
            if b0 == 0xD3:
                consumed = self._extract_rtcm(buf)
                if consumed == 0:
                    break   # çerçeve tam değil — daha fazla veri bekleniyor
                continue
            if b0 == ord("$"):
                nl = buf.find(b"\n")
                if nl == -1:
                    if len(buf) > _RESYNC_MAX_BUF:
                        del buf[:1]   # satır sonu hiç gelmiyor — senkron kaybı
                        continue
                    break   # satır tamamlanmadı — daha fazla veri bekleniyor
                line = bytes(buf[:nl + 1])
                del buf[:nl + 1]
                self._handle_nmea_line(line.decode(errors="replace").strip())
                continue
            # Ne NMEA ne RTCM3 başlangıcı — bir bayt atlayıp yeniden senkronlan.
            del buf[:1]

    def _extract_rtcm(self, buf: bytearray) -> int:
        """buf[0]==0xD3 varsayılır. Tam çerçeve varsa işleyip TÜKETİLEN bayt
        sayısını döndürür (>0); tampon henüz yetersizse 0 döndürür (bekle)."""
        if len(buf) < 3:
            return 0
        length = ((buf[1] & 0x03) << 8) | buf[2]
        frame_len = 3 + length + 3
        if len(buf) < frame_len:
            return 0
        frame = bytes(buf[:frame_len])
        del buf[:frame_len]
        expected = _crc24q(frame[:-3])
        received = (frame[-3] << 16) | (frame[-2] << 8) | frame[-1]
        if expected != received:
            # Sahte 0xD3 (NMEA/gürültü içinde rastgele denk gelmiş olabilir) —
            # İHA'ya BOZUK düzeltme GÖNDERME, yalnızca say ve at.
            self._rtcm_bad_frames += 1
            logger.debug("RTCM3 sağlama hatası (bozuk çerçeve atlandı, toplam=%d)",
                         self._rtcm_bad_frames)
            return frame_len
        self._rtcm_msg_count += 1
        self._rtcm_bytes_total += len(frame)
        self._last_rtcm_ts = time.monotonic()
        self.rtcm_ready.emit(frame)
        return frame_len

    def _handle_nmea_line(self, line: str) -> None:
        if line.startswith(("$GNGGA", "$GPGGA")):
            self._parse_gga(line)
        elif line.startswith(("$GNRMC", "$GPRMC")):
            self._parse_rmc(line)
        elif line.startswith(("$GNGSA", "$GPGSA")):
            self._parse_gsa(line)

    def _parse_gga(self, line: str) -> None:
        f = line.split(",")
        if len(f) < 15:
            return
        self._state.update({
            "lat": _nmea_to_deg(f[2], f[3]),
            "lon": _nmea_to_deg(f[4], f[5]),
            "fix_quality": f[6],
            "fix_quality_str": _FIX_QUALITY.get(f[6], f"? ({f[6]})"),
            "num_sats": f[7],
            "hdop": _f(f[8]),
            "alt_m": _f(f[9]),
            "geoid_sep_m": _f(f[11]),
            "diff_age_s": _f(f[13]) if f[13] else None,
            "diff_station_id": f[14] if f[14] else None,
        })

    def _parse_rmc(self, line: str) -> None:
        f = line.split(",")
        if len(f) < 10:
            return
        speed_kt = _f(f[7])
        self._state.update({
            "speed_kmh": (speed_kt * 1.852) if speed_kt is not None else None,
            "course_deg": _f(f[8]),
            "rmc_status": f[2],   # "A"=valid "V"=invalid
        })

    def _parse_gsa(self, line: str) -> None:
        f = line.split(",")
        if len(f) < 18:
            return
        self._state.update({
            "fix_mode_2d3d": _FIX_MODE_2D3D.get(f[2], "?"),
            "pdop": _f(f[15]),
            "hdop_gsa": _f(f[16]),
            "vdop": _f(f[17].split("*")[0]) if f[17] else None,
        })

    def _maybe_emit(self) -> None:
        now = time.monotonic()
        if now - self._last_emit < _EMIT_MIN_INTERVAL_S:
            return
        self._last_emit = now
        self.gps_updated.emit(dict(self._state))

    def _maybe_emit_base_status(self) -> None:
        now = time.monotonic()
        if now - self._last_base_emit < _EMIT_MIN_INTERVAL_S:
            return
        self._last_base_emit = now
        since = (now - self._last_rtcm_ts) if self._last_rtcm_ts is not None else None
        self.base_status.emit({
            "active": since is not None and since < _RTCM_ACTIVE_TIMEOUT_S,
            "msg_count": self._rtcm_msg_count,
            "bytes_total": self._rtcm_bytes_total,
            "bad_frames": self._rtcm_bad_frames,
            "seconds_since_last": since,
        })
