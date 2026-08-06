"""
sim_workers.py — Sahte (mock) worker'lar
=========================================
YKİ'yi GERÇEK İHA/SITL olmadan, tamamen bu bilgisayarda çalıştırmak için.
`mode=sim` iken main_window._init_workers bu sınıfları gerçek worker'ların yerine
kurar (bkz. gcs_config.mode).

Tasarım: her sınıf GERÇEK worker'ı ALT-SINIFLAR ve yalnızca `run()`'ı override eder.
Böylece tüm sinyaller (telemetry_updated, rpi_status_updated, frame_ready …), slotlar
ve _wire_mavlink_worker bağlantıları AYNEN çalışır — UI hiçbir farkı bilmez.

NOT: Bu, uçtan-uca sim (SITL + IHA-sim + gerçek YKİ worker'ları, network.json ile
127.0.0.1'e yönlendirilmiş) ile KARIŞTIRILMAMALI. Orada bu dosyaya gerek yok; gerçek
worker'lar 127.0.0.1'e bağlanır. Buradaki fake'ler "hiç backend yok, sadece arayüzü
canlı gör" senaryosu içindir.
"""
from __future__ import annotations

import math
import time

from PyQt6.QtGui import QImage, QPainter, QColor, QPolygon, QFont
from PyQt6.QtCore import QPoint

from core.mavlink_worker import MavlinkWorker
from core.video_stream_worker import VideoStreamWorker
from core.rpi_status_worker import RpiStatusWorker

# Sim başlangıç konumu (sim_start.sh ile aynı varsayılan)
_HOME_LAT, _HOME_LON, _HOME_ALT = 39.925018, 32.836956, 850.0


class FakeMavlinkWorker(MavlinkWorker):
    """SITL yerine sentetik telemetri üretir (home etrafında yörünge)."""

    def run(self) -> None:
        self.connection_changed.emit(True)
        try:
            self.home_position_updated.emit(_HOME_LAT, _HOME_LON, _HOME_ALT)
        except Exception:
            pass
        t0 = time.time()
        while not self.isInterruptionRequested():
            self.heartbeat_counter += 1
            t = time.time() - t0
            R = 0.00045                      # ~50 m derece cinsinden
            lat = _HOME_LAT + R * math.sin(t * 0.15)
            lon = _HOME_LON + R * math.cos(t * 0.15)
            alt = 30.0 + 5.0 * math.sin(t * 0.3)
            yaw = math.degrees(t * 0.15) % 360.0
            batt = max(30.0, 100.0 - t * 0.2)
            data = {
                "lat": lat, "lon": lon, "alt": alt,
                "relative_alt": alt, "baro_alt": alt,
                "roll": 3.0 * math.sin(t), "pitch": 2.0 * math.cos(t),
                "yaw": yaw, "heading": yaw, "hdg": yaw,
                "rollspeed": 0.0, "pitchspeed": 0.0, "yawspeed": 0.0,
                "groundspeed": 6.0 + math.sin(t), "airspeed": 6.0,
                "climb": math.cos(t * 0.3), "throttle": 45,
                "armed": True, "flight_mode": "GUIDED",
                "battery_voltage": 22.2, "battery_remaining": int(batt),
                "battery_current": 12.0, "battery_mah_used": int(t * 3),
                "gps_fix_type": 6, "gps_fix_name": "RTK Fixed",
                "gps_sats": 18, "gps_hdop": 0.8,
                "home_lat": _HOME_LAT, "home_lon": _HOME_LON, "home_alt": _HOME_ALT,
                "vx": 0.0, "vy": 0.0, "vz": 0.0, "rc_rssi": 90,
            }
            self.telemetry_updated.emit(data)
            self.msleep(66)                  # ~15 Hz
        self.connection_changed.emit(False)


class FakeRpiStatusWorker(RpiStatusWorker):
    """İHA yardımcı bilgisayar durum yayınını (5602) taklit eder."""

    def run(self) -> None:
        self.rpi_connected.emit(True)
        t0 = time.time()
        while not self.isInterruptionRequested():
            self.heartbeat_counter += 1
            t = time.time() - t0
            data = {
                "cpu_pct": 12.0 + 4.0 * abs(math.sin(t * 0.5)),
                "mem_pct": 38.0, "temp_c": 46.0 + 3.0 * math.sin(t * 0.2),
                "disk_pct": 41.0, "uptime_s": int(t), "ping_ms": 3,
                "mission": {"active": False, "state": "IDLE",
                            "pending": False, "wp_index": 0},
                "fc_mission": {"count": 0},
                "payload_loaded": True,
                "servo_state": {"gimbal_angle": 30.0, "grabber": "close",
                                "red_drop": "close", "blue_drop": "close"},
                "vision_status": {"connected": True, "target_detected": (int(t) % 6 < 2)},
                "tof_distances": {"L1": 500.0, "L2": 500.0, "L5": 500.0, "L4": 500.0},
                "mav": {"connected": True, "armed": True, "mode": "GUIDED",
                        "battery": 22.2, "altitude": 30.0,
                        "gps_raw": {"fix_type": 6, "satellites": 18}},
                "health": "OK", "health_alerts": [], "notifications": [],
                "test_status": {"running": None, "results": {}, "summary": {}},
            }
            self.rpi_status_updated.emit(data)
            self.msleep(1000)                # 1 Hz (gerçek status server gibi)
        self.rpi_connected.emit(False)


class FakeVideoWorker(VideoStreamWorker):
    """Sentetik video karesi üretir (hareketli kırmızı üçgen + mavi altıgen)."""

    def run(self) -> None:
        W, H = 640, 480
        t0 = time.time()
        while not self.isInterruptionRequested():
            t = time.time() - t0
            img = QImage(W, H, QImage.Format.Format_RGB888)
            img.fill(QColor(60, 90, 60))
            p = QPainter(img)
            # Kırmızı üçgen
            cx = int(W * (0.5 + 0.28 * math.sin(t * 0.6)))
            cy = int(H * (0.5 + 0.22 * math.cos(t * 0.5)))
            s = int(min(W, H) * 0.11)
            tri = QPolygon([QPoint(cx, cy - s), QPoint(cx - s, cy + s), QPoint(cx + s, cy + s)])
            p.setBrush(QColor(210, 40, 40)); p.setPen(QColor(210, 40, 40))
            p.drawPolygon(tri)
            # Mavi altıgen
            bx = int(W * (0.5 - 0.30 * math.sin(t * 0.4 + 1.0)))
            by = int(H * (0.5 - 0.20 * math.cos(t * 0.7)))
            r = int(min(W, H) * 0.10)
            hexp = QPolygon([QPoint(int(bx + r * math.cos(math.radians(60 * k))),
                                    int(by + r * math.sin(math.radians(60 * k)))) for k in range(6)])
            p.setBrush(QColor(30, 120, 200)); p.setPen(QColor(30, 120, 200))
            p.drawPolygon(hexp)
            p.setPen(QColor(230, 230, 230))
            p.setFont(QFont("Sans", 10))
            p.drawText(10, H - 12, "ASHINA SIM VIDEO  t=%.1fs" % t)
            p.end()
            self.frame_ready.emit(img)
            self.msleep(66)                  # ~15 Hz
