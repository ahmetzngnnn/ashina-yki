"""
core/antenna_tracker_worker.py — Anten Takip Sistemi Worker
============================================================

Antenin (RUTX10 üzerindeki 2 eksenli gimbal + Pixhawk 2.4.8) sürekli İHA'ya
bakmasını sağlar. İHA telemetrisinden (lat/lon/alt) anten hedef açılarını
(azimut/elevasyon) hesaplar ve anten Pixhawk'ına MAVLink ile iletir.

ÇALIŞMA MODLARI (config: antenna_tracker_mode):
  • "forward"  (ÖNERİLEN): İHA konumu anten Pixhawk'ına GLOBAL_POSITION_INT
       olarak iletilir. Anten Pixhawk'ında ArduPilot AntennaTracker firmware'i
       azimut/elevasyon hesabını ve servo sürüşünü kendisi yapar (en doğru/akıcı).
  • "compute": YKİ azimut/elevasyon hesaplar ve DO_MOUNT_CONTROL ile gönderir
       (firmware AntennaTracker değilse / mount tabanlı gimbal için).

BAĞLANTI MİMARİSİ:
  İHA → RFD900A(USB) → RPi → RUTX10(AP) → ağ → bu worker (anten Pixhawk MAVLink)
  İHA telemetrisi mavlink_worker.telemetry_updated sinyaliyle bu worker'a beslenir.

OTOMATİK BAŞLAMA:
  İHA enerji alıp geçerli koordinat göndermeye başladığı an (fix + lat/lon),
  anten home noktası (ayarlanmadıysa) ilk drone civarına/sabit değere kilitlenir
  ve takip otomatik başlar.
"""
from __future__ import annotations
import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal, pyqtSlot

from core import tracker_math as tm

logger = logging.getLogger(__name__)


class AntennaTrackerWorker(QThread):
    # azimuth, elevation, ground_dist, slant_dist, alt_diff, tracking
    tracker_status     = pyqtSignal(dict)
    connection_changed = pyqtSignal(bool)
    error_occurred     = pyqtSignal(str)

    def __init__(self,
                 conn_string: str = "udp:0.0.0.0:14560",
                 mode: str = "forward",
                 home_lat: float = 0.0, home_lon: float = 0.0, home_alt: float = 0.0,
                 min_distance_m: float = 3.0,
                 update_hz: float = 5.0,
                 parent=None):
        super().__init__(parent)
        self._conn_string = conn_string      # anten Pixhawk MAVLink adresi
        self._mode        = mode             # "forward" | "compute"
        # Anten konumu: (0,0,0) ise ilk geçerli drone fix'inde / Pixhawk GPS'iyle ayarlanır
        self._home = {"lat": home_lat, "lon": home_lon, "alt": home_alt}
        self._home_locked = bool(home_lat and home_lon)
        self._min_dist = min_distance_m
        self._period   = 1.0 / max(0.5, update_hz)

        self._enabled = False
        self._conn = None
        self._drone = None                   # son drone konumu {lat,lon,alt,fix}
        self._last_send = 0.0

    # ── Kontrol (ana thread'den) ───────────────────────────────────────────
    def enable(self) -> None:
        self._enabled = True
        logger.info("Anten takip etkinleştirildi (mod=%s, hedef=%s)", self._mode, self._conn_string)

    def disable(self) -> None:
        self._enabled = False
        self.connection_changed.emit(False)
        logger.info("Anten takip devre dışı.")

    def set_antenna_home(self, lat: float, lon: float, alt: float) -> None:
        """Anten (sabit) konumunu elle ayarla — örn. saha kurulumunda ölçülen GPS."""
        self._home = {"lat": lat, "lon": lon, "alt": alt}
        self._home_locked = True
        logger.info("Anten home ayarlandı: %.6f, %.6f, %.1f m", lat, lon, alt)

    def set_home_from_current_drone(self) -> bool:
        """Anten home'unu anlık İHA konumuna ayarla (saha kalibrasyonu).
        Anten ile İHA aynı noktadayken (kalkış öncesi) çağrılır. True=başarılı."""
        if self._drone is None:
            logger.warning("Anten home ayarlanamadı — İHA konumu henüz yok.")
            return False
        d = self._drone
        self.set_antenna_home(d["lat"], d["lon"], d["alt"])
        return True

    @pyqtSlot(dict)
    def update_drone_position(self, telem: dict) -> None:
        """mavlink_worker.telemetry_updated → İHA konumu beslemesi."""
        lat = telem.get("lat"); lon = telem.get("lon")
        alt = telem.get("alt", telem.get("relative_alt", 0.0))
        if lat is None or lon is None:
            return
        # Geçersiz/sıfır fix'i ele
        if abs(lat) < 1e-6 and abs(lon) < 1e-6:
            return
        self._drone = {"lat": float(lat), "lon": float(lon), "alt": float(alt or 0.0)}

    # ── Worker döngüsü ─────────────────────────────────────────────────────
    def run(self) -> None:
        from pymavlink import mavutil
        while not self.isInterruptionRequested():
            if not self._enabled:
                time.sleep(0.3)
                continue

            # 1) Anten Pixhawk'ına bağlan
            if self._conn is None:
                try:
                    logger.info("Anten Pixhawk'ına bağlanılıyor: %s", self._conn_string)
                    self._conn = mavutil.mavlink_connection(self._conn_string, source_system=251)
                    self._conn.wait_heartbeat(timeout=10)
                    self.connection_changed.emit(True)
                    logger.info("Anten Pixhawk bağlandı (sys=%s).",
                                self._conn.target_system)
                except Exception as exc:
                    self.error_occurred.emit(f"Anten Pixhawk bağlanamadı: {exc}")
                    self._conn = None
                    time.sleep(3.0)
                    continue

            # 2) Anten home'u otomatik kilitle (ayarlanmadıysa) — anten Pixhawk GPS'i
            if not self._home_locked:
                self._try_lock_home_from_pixhawk()

            # 3) Drone konumu varsa hedef açıları hesapla + ilet
            try:
                if self._drone is not None:
                    now = time.time()
                    if now - self._last_send >= self._period:
                        self._track_once()
                        self._last_send = now
                # gelen mesajları boşalt (heartbeat canlı tut)
                self._conn.recv_match(blocking=False)
            except Exception as exc:
                self.error_occurred.emit(f"Anten takip hatası: {exc}")
                self._conn = None
                self.connection_changed.emit(False)
                time.sleep(2.0)
            time.sleep(0.02)

        # temizlik
        if self._conn:
            try: self._conn.close()
            except Exception: pass

    # ── İç yardımcılar ─────────────────────────────────────────────────────
    def _try_lock_home_from_pixhawk(self) -> None:
        """Anten Pixhawk GPS'inden home konumunu otomatik al."""
        try:
            msg = self._conn.recv_match(type="GLOBAL_POSITION_INT", blocking=False)
            if msg and msg.lat != 0:
                self._home = {"lat": msg.lat / 1e7, "lon": msg.lon / 1e7,
                              "alt": msg.alt / 1000.0}
                self._home_locked = True
                logger.info("Anten home Pixhawk GPS'inden alındı: %.6f, %.6f",
                            self._home["lat"], self._home["lon"])
        except Exception:
            pass

    def _track_once(self) -> None:
        from pymavlink import mavutil
        d = self._drone
        h = self._home
        aim = tm.compute_aim(h["lat"], h["lon"], h["alt"],
                             d["lat"], d["lon"], d["alt"])

        tracking = aim["ground_distance_m"] >= self._min_dist
        status = dict(aim); status["tracking"] = tracking
        self.tracker_status.emit(status)
        if not tracking:
            return   # çok yakın — anteni oynatma (gürültü)

        ts = self._conn.target_system or 1
        tc = self._conn.target_component or 1

        if self._mode == "forward":
            # İHA konumunu anten Pixhawk'ına ilet — AntennaTracker firmware hesaplar
            self._conn.mav.global_position_int_send(
                int(time.time() * 1000) & 0xFFFFFFFF,
                int(d["lat"] * 1e7), int(d["lon"] * 1e7),
                int(d["alt"] * 1000), int(d["alt"] * 1000),
                0, 0, 0, 0,
            )
        else:  # "compute": YKİ hesaplar, mount kontrolü ile yönlendir
            mav = mavutil.mavlink
            # DO_MOUNT_CONTROL: pitch=elevasyon, yaw=azimut (derece)
            self._conn.mav.command_long_send(
                ts, tc, mav.MAV_CMD_DO_MOUNT_CONTROL, 0,
                aim["elevation"], 0.0, aim["azimuth"], 0, 0, 0,
                mav.MAV_MOUNT_MODE_MAVLINK_TARGETING,
            )

    def stop(self) -> None:
        self.requestInterruption()
        self.wait(3000)
