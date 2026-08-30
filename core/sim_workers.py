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
import random
import time

from PyQt6.QtGui import QImage, QPainter, QColor, QPolygon, QFont
from PyQt6.QtCore import QPoint

from core.mavlink_worker import MavlinkWorker
from core.video_stream_worker import VideoStreamWorker
from core.rpi_status_worker import RpiStatusWorker

# Sim başlangıç konumu (sim_start.sh ile aynı varsayılan)
_HOME_LAT, _HOME_LON, _HOME_ALT = 39.925018, 32.836956, 850.0

# ── Sim telemetri sabitleri (gerçek şema ile birebir uyum için) ───────────────
# NEDEN (F14/E10): FakeMavlinkWorker aşağıdaki alanları HİÇ üretmiyordu; gerçek
# MavlinkWorker ise EKF_STATUS_REPORT / SYS_STATUS / RC_CHANNELS / ESC_TELEMETRY /
# VIBRATION / PID_TUNING / BATTERY_STATUS mesajlarından üretiyor. Sonuç: sim'de
# Test Paneli kontrol listesi asla yeşile dönmüyor (ekf_ok/compass_ok/accel_ok),
# ESC telemetri hep sıfır, Paket Kaybı barı hiç güncellenmiyor, EKF göstergesi gri
# kalıyordu — demo sırasında sistem ARIZALI görünüyordu. Alan adları ve TİPLERİ
# mavlink_worker.py'deki _telemetry_accumulator.update(...) çağrılarıyla eşlenir.

# EKF_STATUS_REPORT.flags — sağlıklı uçuş maskesi.
#   bit0 ATTITUDE | bit1 VELOCITY_HORIZ | bit2 VELOCITY_VERT
#   bit3 POS_HORIZ_REL | bit4 POS_HORIZ_ABS | bit5 POS_VERT_ABS | bit6 POS_VERT_AGL
#   bit7 CONST_POS_MODE (GPS'siz kilitli mod — SAĞLIKLI durumda SET DEĞİLDİR)
#   bit8 PRED_POS_HORIZ_REL | bit9 PRED_POS_HORIZ_ABS
# 0x37F = bit7 hariç hepsi set → top_bar/test_panel'in aradığı
# (flags & 0x1F) == 0x1F koşulunu sağlar ("EKF İYİ").
_EKF_FLAGS_HEALTHY = 0x37F

_SIM_BATTERY_CELLS = 6          # 6S paket (gerçek worker BATTERY_STATUS'tan sayar)
_SIM_MOTOR_COUNT   = 4          # quad — gerçek worker 4 slot yayınlar


def _jitter(scale: float) -> float:
    """Küçük simetrik gürültü — sim değerleri 'ölü sabit' görünmesin."""
    return random.uniform(-scale, scale)


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
            yaw_deg = math.degrees(t * 0.15) % 360.0
            batt = max(30.0, 100.0 - t * 0.2)
            roll_rad  = math.radians(3.0 * math.sin(t))
            pitch_rad = math.radians(2.0 * math.cos(t))

            # ── Gaz: ESC/titreşim değerleri buna KORELE üretilir ─────────────
            throttle = 42.0 + 6.0 * math.sin(t * 0.4)

            # ── Batarya: hücre gerilimleri ve paket gerilimi TUTARLI ─────────
            # Gerçek worker BATTERY_STATUS'ta battery_voltage = Σ(hücre) yapar;
            # sim'de de aynı ilişkiyi kuruyoruz (eskiden sabit 22.2 V idi ve
            # battery_cell_voltages hiç üretilmiyordu → hücre paneli boştu).
            cell_nom = 3.50 + 0.70 * (batt / 100.0)          # %100→4.20 V, %30→3.71 V
            cells = [round(cell_nom + _jitter(0.012), 3) for _ in range(_SIM_BATTERY_CELLS)]
            pack_v = round(sum(cells), 2)
            pack_a = round(9.0 + 0.22 * throttle + _jitter(0.4), 2)

            # ── ESC telemetri: 4 motor, gaz ile korelasyonlu ─────────────────
            # Tipler gerçek worker ile aynı: rpm int, volt/curr float, temp int, mah int.
            esc_rpm, esc_v, esc_a, esc_t, esc_mah = [], [], [], [], []
            for i in range(_SIM_MOTOR_COUNT):
                bias = 1.0 + 0.02 * (i - 1.5)                 # motorlar birebir aynı olmaz
                esc_rpm.append(int((2600 + 148.0 * throttle) * bias + _jitter(60)))
                esc_v.append(round(pack_v - 0.25 - _jitter(0.05), 2))   # ESC'de hafif sag
                esc_a.append(round(pack_a / _SIM_MOTOR_COUNT * bias + _jitter(0.3), 2))
                esc_t.append(int(34 + 0.45 * throttle + _jitter(1.5)))
                esc_mah.append(int(t * pack_a / _SIM_MOTOR_COUNT * 1000 / 3600))

            # ── RC kanalları: makul PWM (1000-2000 µs) ───────────────────────
            # 1=roll 2=pitch 3=throttle 4=yaw, 5-8 anahtarlar (gerçek RC_CHANNELS).
            rc_1 = int(1500 + 60 * math.sin(t * 0.5))
            rc_2 = int(1500 + 45 * math.cos(t * 0.4))
            rc_3 = int(1000 + 10.0 * throttle)                # %42-48 gaz → ~1420-1480
            rc_4 = int(1500 + 30 * math.sin(t * 0.25))

            # ATTITUDE sözleşmesi RADYAN (gerçek MavlinkWorker msg.yaw radyan gönderir;
            # dashboard math.degrees uygular). heading/hdg ise tam derece (VFR_HUD int).
            data = {
                "lat": lat, "lon": lon, "alt": alt,
                "relative_alt": alt, "baro_alt": alt,
                "roll": roll_rad,
                "pitch": pitch_rad,
                "yaw": math.radians(yaw_deg),
                "heading": int(yaw_deg), "hdg": int(yaw_deg),
                "rollspeed": 0.0, "pitchspeed": 0.0, "yawspeed": 0.0,
                "groundspeed": 6.0 + math.sin(t), "airspeed": 6.0,
                "climb": math.cos(t * 0.3), "throttle": int(throttle),
                "armed": True, "flight_mode": "GUIDED",
                "battery_voltage": pack_v, "battery_remaining": int(batt),
                "battery_current": pack_a, "battery_mah_used": int(t * 3),
                "battery_cells": _SIM_BATTERY_CELLS,
                "battery_cell_voltages": cells,
                "gps_fix_type": 6, "gps_fix_name": "RTK Fixed",
                "gps_sats": 18, "gps_hdop": 0.8,
                "home_lat": _HOME_LAT, "home_lon": _HOME_LON, "home_alt": _HOME_ALT,
                "vx": 0.0, "vy": 0.0, "vz": 0.0,

                # ── EKF / kalibrasyon (Test Paneli kontrol listesi + top_bar) ──
                "ekf_flags": _EKF_FLAGS_HEALTHY,
                "accel_cal_done": True,      # SYS_STATUS sensör sağlık biti 0x02
                "compass_cal_done": True,    # SYS_STATUS sensör sağlık biti 0x04

                # ── RC (test paneli rc_ok: rssi > 50; dashboard rssi/254*100) ──
                # NEDEN: rc_rssi ESKİDEN 90 idi → dashboard barında %35 gibi
                # "zayıf link" görünüyordu. RC_CHANNELS.rssi 0-254 ölçeğindedir;
                # sağlıklı bir alıcı ~200 (≈%79) bildirir.
                "rc_rssi": 200,
                "rc_1": rc_1, "rc_2": rc_2, "rc_3": rc_3, "rc_4": rc_4,
                "rc_5": 1500, "rc_6": 1100, "rc_7": 1900, "rc_8": 1500,

                # ── ESC telemetri (4 motor) ───────────────────────────────────
                "esc_rpm": esc_rpm, "esc_voltage": esc_v, "esc_current": esc_a,
                "esc_temp": esc_t, "esc_mah": esc_mah,

                # ── Titreşim: düşük gürültü (ArduPilot VIBE, m/s²; <30 sağlıklı) ──
                "vib_x": round(4.2 + 0.5 * math.sin(t * 1.7) + _jitter(0.4), 2),
                "vib_y": round(4.0 + 0.5 * math.cos(t * 1.3) + _jitter(0.4), 2),
                "vib_z": round(6.1 + 0.7 * math.sin(t * 0.9) + _jitter(0.5), 2),

                # ── PID_TUNING.pe — sıfır etrafında küçük hata ────────────────
                "pid_err": round(_jitter(0.05), 3),

                # ── Haberleşme kalitesi (SYS_STATUS) ─────────────────────────
                # drop_rate_comm birimi c% (%×100): dashboard drop/100 ile % yapar.
                # Sağlıklı radyoda %0.0-0.4 → 0-40.
                "drop_rate_comm": int(max(0, 12 + 10 * math.sin(t * 0.13) + _jitter(6))),
                "errors_comm": 0,

                # ── Bağlantı tazeliği (gerçek worker: now - son heartbeat) ────
                # Heartbeat 1 Hz olduğu için yaş 0-1 s arasında salınır.
                "last_heartbeat_age": round((t % 1.0) * 0.9, 3),
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
            # NEDEN (F14/E10): Aşağıdaki üç alan gerçek companion şemasından
            # (IHA/comms/status_server.py _build_status) SAPIYORDU:
            #   • mission.active/pending: bool üretiliyordu; gerçekte str|None
            #     (aktif görev SINIF ADI / bekleyen görev TİPİ). mission.wp_index
            #     gerçek şemada HİÇ YOK — kaldırıldı.
            #   • fc_mission: {"count": 0} idi; dashboard_view type/wp_count/source
            #     anahtarlarını, main_window ise ts'yi okuyor → panel "? ? WP (?)"
            #     gösteriyordu.
            #   • mav.battery: float (22.2) idi; gerçek şema dict {"v":…, "pct":…}
            #     (IHA/mav/bridge.py) → dashboard batt.get("v") ile okuduğu için
            #     sim'de gerilim HİÇ yazılmıyordu (AttributeError riski).
            #   • health: "OK" str idi; gerçekte failsafe summary() dict'i
            #     {"overall": <Health adı>, "subsystems": {alt_sistem: <Health adı>}}.
            data = {
                "cpu_pct": 12.0 + 4.0 * abs(math.sin(t * 0.5)),
                "mem_pct": 38.0, "temp_c": 46.0 + 3.0 * math.sin(t * 0.2),
                "disk_pct": 41.0, "uptime_s": int(t), "ping_ms": 3,
                "mission": {"active": None, "state": "IDLE", "pending": None},
                "fc_mission": {"type": "teknofest_task1", "wp_count": 6,
                               "source": "companion", "ts": int(t0)},
                "payload_loaded": True,
                "servo_state": {"gimbal_angle": 90.0, "grabber": "close",
                                "red_drop": "close", "blue_drop": "close",
                                "last_cmd_ok": {}},
                "vision_status": {"connected": True, "target_detected": (int(t) % 6 < 2)},
                "tof_distances": {"L1": 500.0, "L2": 500.0, "L5": 500.0, "L4": 500.0},
                "mav": {"connected": True, "armed": True, "mode": "GUIDED",
                        "battery": {"v": 22.2, "pct": 78},
                        "altitude": 30.0,
                        "gps_raw": {"fix_type": 6, "satellites": 18}},
                "health": {
                    "overall": "NOMINAL",
                    "subsystems": {
                        "mavlink": "NOMINAL", "serial": "NOMINAL",
                        "vision": "NOMINAL", "tof": "NOMINAL",
                        "battery": "NOMINAL", "ekf": "NOMINAL",
                        "gps": "NOMINAL", "servo": "NOMINAL",
                        "params": "NOMINAL",
                    },
                },
                "health_alerts": [], "notifications": [],
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
