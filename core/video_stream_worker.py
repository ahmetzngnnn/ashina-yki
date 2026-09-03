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
                if src.startswith("udp://"):
                    self._recv_udp(src)
                elif src.startswith("http://") or src.startswith("https://"):
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

    # ── UDP Alıcı (2026-09-03) ─────────────────────────────────────────────
    # NEDEN UDP: canlı görüntüde TCP yanlış taşıyıcı. Bir paket kaybolunca TCP
    # arkasındaki her şeyi bekletir (sıra başı tıkanması), ESKİ kareyi yeniden
    # gönderir (canlı görüntüde çöp) ve kablosuz kaybı tıkanıklık sanıp hızı
    # kısar. Sahadaki "takılıyor sonra eski kareler hızla akıyor" davranışı
    # TCP tamponunun birikip boşalmasıydı. UDP'de kayıp kare ATLANIR.
    #
    # PROTOKOL — İHA tarafındaki karşılığı: IHA/comms/udp_video.py
    # İki depo ayrı olduğu için bu sabitler ZORUNLU OLARAK İKİ KOPYA.
    # Değiştirirsen ORADA DA değiştir, yoksa görüntü sessizce gelmez.
    _UDP_SIHIR = b"AVID"
    _UDP_BASLIK = "!4sBBIHH"          # sihir, akış, bayrak, kare_no, parça, toplam
    _UDP_HELLO = b"ASHINA-VIDEO-HELLO"
    _UDP_HELLO_ARALIK_S = 1.0         # araç 3 s sessizlikte yayını kesiyor

    def _recv_udp(self, src: str) -> None:
        """udp://host:port — parçalı JPEG karelerini toplar, eksikse ATAR."""
        import socket as _sock
        import struct as _struct

        # Biçim: udp://konak:port/AKIS   (AKIS: 0=ham, 1=işlenmiş)
        # NEDEN adreste: iki akış AYNI porttan geliyor, HELLO'nun son ekiyle
        # seçiliyor. Portla ayırmak yerine adrese koymak, İHA tarafında tek
        # soket/tek yayıncı tutmayı sağlıyor.
        adres = src[len("udp://"):]
        yol_akis = "1"
        if "/" in adres:
            adres, _, yol_akis = adres.partition("/")
            yol_akis = (yol_akis or "1").strip()
        akis_bayt = b"0" if yol_akis == "0" else b"1"
        konak, _, port = adres.partition(":")
        port = int(port or 5600)
        basl_boyut = _struct.calcsize(self._UDP_BASLIK)

        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        # NEDEN büyük alım tamponu: bir kare ~100 datagram halinde ANINDA
        # geliyor; tampon küçükse çekirdek paket düşürür ve kare hep eksik
        # kalır — bağlantı iyi olsa bile görüntü gelmez.
        try:
            s.setsockopt(_sock.SOL_SOCKET, _sock.SO_RCVBUF, 1 << 21)
        except OSError:
            pass
        s.settimeout(1.0)
        try:
            s.bind(("0.0.0.0", 0))
            logger.info("UDP video alıcı başladı: %s:%d", konak, port)
            parcalar: dict = {}
            aktif_kare = -1
            son_hello = 0.0
            bos_tur = 0
            while not self.isInterruptionRequested():
                simdi = time.monotonic()
                if simdi - son_hello >= self._UDP_HELLO_ARALIK_S:
                    # Abonelik: araç kareleri BU datagramın geldiği adrese
                    # yollar. Böylece YKİ'nin IP'si değişse de yapılandırma
                    # gerekmez ve YKİ kapanınca yayın kendiliğinden durur.
                    try:
                        s.sendto(self._UDP_HELLO + akis_bayt, (konak, port))
                    except OSError:
                        pass
                    son_hello = simdi
                try:
                    veri, _ = s.recvfrom(65535)
                except _sock.timeout:
                    bos_tur += 1
                    self.heartbeat_counter += 1
                    if bos_tur >= 8:      # ~8 s hiç kare yok → üst katman yeniden dener
                        raise ConnectionError("UDP video: 8 s kare gelmedi")
                    continue
                except OSError as exc:
                    raise ConnectionError(f"UDP soket hatası: {exc}")
                bos_tur = 0
                if len(veri) < basl_boyut or not veri.startswith(self._UDP_SIHIR):
                    continue
                _, _akis, _bayrak, kare_no, parca_no, toplam = _struct.unpack(
                    self._UDP_BASLIK, veri[:basl_boyut])
                if kare_no != aktif_kare:
                    # Yeni kare başladı: yarım kalan ESKİ kareyi AT, bekleme.
                    # Canlı görüntüde eski kare çöptür; gecikme birikmemeli.
                    aktif_kare = kare_no
                    parcalar = {}
                parcalar[parca_no] = veri[basl_boyut:]
                if len(parcalar) != toplam:
                    continue
                # NEDEN sozluk yerine acik kontrol: bozuk ya da parca_no >=
                # toplam gelen TEK bir datagram len(parcalar)==toplam yapip
                # indeksi eksik birakabilir; b"".join(... range(toplam)) o an
                # KeyError atar, alici is parcacigi duser ve video yeniden
                # baglanmaya gider (gorunur takilma). Eksikse kareyi ATLA.
                try:
                    govde = b"".join(parcalar[i] for i in range(toplam))
                except KeyError:
                    parcalar = {}
                    continue
                parcalar = {}
                self.heartbeat_counter += 1
                q = QImage()
                if q.loadFromData(govde, "JPG"):
                    self.frame_ready.emit(q)
        finally:
            try:
                s.close()
            except Exception:
                pass

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
