"""
test_telemetry.py — MAVLink Telemetri Debug Scripti
Kullanim: python test_telemetry.py
TCP 5762'den bağlanır, gelen mesajları ve hesaplanan telemetri değerlerini gösterir.
"""
import sys
import time
import math

try:
    from pymavlink import mavutil
except ImportError:
    print("HATA: pymavlink kurulu değil. pip install pymavlink")
    sys.exit(1)

# ── Bağlantı ─────────────────────────────────────────────────────────────────
HOST = "127.0.0.1"
PORT = 5762
print(f"Bağlanıyor: tcp:{HOST}:{PORT} ...")

try:
    mav = mavutil.mavlink_connection(f"tcp:{HOST}:{PORT}")
    print("Bağlantı nesnesi oluşturuldu — heartbeat bekleniyor...")
    mav.wait_heartbeat(timeout=10)
    print(f"✓ HEARTBEAT alındı — sysid={mav.target_system} compid={mav.target_component}")

    # ── SET_MESSAGE_INTERVAL ile veri akışı iste ─────────────────────────────
    # Modern ArduPilot REQUEST_DATA_STREAM (#66) yerine cmd 511 kullanır
    STREAMS = [
        (30,  100_000),   # ATTITUDE            10 Hz
        (74,  100_000),   # VFR_HUD             10 Hz
        (24,  200_000),   # GPS_RAW_INT          5 Hz
        (33,  200_000),   # GLOBAL_POSITION_INT  5 Hz
        (1,   500_000),   # SYS_STATUS           2 Hz
        (193, 500_000),   # EKF_STATUS_REPORT    2 Hz
        (65,  200_000),   # RC_CHANNELS          5 Hz
    ]
    print("Veri akışı isteniyor (SET_MESSAGE_INTERVAL)...")
    for msg_id, interval_us in STREAMS:
        mav.mav.command_long_send(
            mav.target_system, mav.target_component,
            511, 0,                       # MAV_CMD_SET_MESSAGE_INTERVAL
            float(msg_id), float(interval_us),
            0, 0, 0, 0, 0,
        )
    print(f"✓ {len(STREAMS)} mesaj tipi için akış istendi")
except Exception as e:
    print(f"HATA: {e}")
    sys.exit(1)

# ── İstenen mesaj tipleri ─────────────────────────────────────────────────────
WANTED = {
    "HEARTBEAT", "ATTITUDE", "VFR_HUD", "GPS_RAW_INT",
    "GLOBAL_POSITION_INT", "SYS_STATUS", "EKF_STATUS_REPORT", "STATUSTEXT",
}

_GPS_FIX = {0:"NO_GPS", 1:"NO_FIX", 2:"2D", 3:"3D_FIX", 4:"DGPS", 5:"RTK_FLOAT", 6:"RTK_FIXED"}
_COPTER_MODES = {
    0:"STABILIZE", 1:"ACRO", 2:"ALT_HOLD", 3:"AUTO", 4:"GUIDED",
    5:"LOITER",    6:"RTL",  7:"CIRCLE",   9:"LAND", 11:"DRIFT",
    13:"SPORT",    14:"FLIP", 15:"AUTOTUNE", 16:"POSHOLD", 17:"BRAKE",
    18:"THROW",    19:"AVOID_ADSB", 20:"GUIDED_NOGPS",
}

# Hesaplanan değerleri tutan sözlük
state: dict = {}

# ── Sayaçlar ──────────────────────────────────────────────────────────────────
msg_counts: dict = {}
last_print = time.time()
PRINT_INTERVAL = 2.0   # saniyede bir özet bas

print("\n─── Mesajlar Dinleniyor (Ctrl+C ile dur) ───\n")

try:
    while True:
        msg = mav.recv_match(blocking=True, timeout=0.5)
        if msg is None:
            continue

        t = msg.get_type()
        if t == "BAD_DATA":
            continue

        msg_counts[t] = msg_counts.get(t, 0) + 1

        # ── Her mesaj tipini yorumla ──────────────────────────────────────
        if t == "HEARTBEAT":
            armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            mode  = _COPTER_MODES.get(msg.custom_mode, f"MODE_{msg.custom_mode}")
            state["armed"] = armed
            state["flight_mode"] = mode

        elif t == "ATTITUDE":
            state["roll_deg"]  = math.degrees(msg.roll)
            state["pitch_deg"] = math.degrees(msg.pitch)
            state["yaw_deg"]   = math.degrees(msg.yaw)
            # Radyan değerleri (HUD'a gönderilen):
            state["roll"]  = msg.roll
            state["pitch"] = msg.pitch
            state["yaw"]   = msg.yaw

        elif t == "VFR_HUD":
            state["airspeed"]    = msg.airspeed
            state["groundspeed"] = msg.groundspeed
            state["heading"]     = msg.heading
            state["throttle"]    = msg.throttle
            state["alt"]         = msg.alt
            state["climb"]       = msg.climb

        elif t == "GPS_RAW_INT":
            state["gps_fix"]  = _GPS_FIX.get(msg.fix_type, str(msg.fix_type))
            state["gps_sats"] = msg.satellites_visible
            state["gps_hdop"] = msg.eph / 100.0 if msg.eph != 65535 else 99.0

        elif t == "GLOBAL_POSITION_INT":
            lat = msg.lat / 1e7
            lon = msg.lon / 1e7
            state["lat"]     = lat
            state["lon"]     = lon
            state["rel_alt"] = msg.relative_alt / 1000.0

        elif t == "SYS_STATUS":
            state["batt_v"] = msg.voltage_battery / 1000.0
            state["batt_a"] = (msg.current_battery / 100.0
                               if msg.current_battery != -1 else 0.0)
            state["batt_%"] = msg.battery_remaining

        elif t == "EKF_STATUS_REPORT":
            flags = msg.flags
            ekf_ok = (flags & 0x1F) == 0x1F
            state["ekf_flags"] = f"0x{flags:04X} ({'OK' if ekf_ok else 'HATA'})"

        elif t == "STATUSTEXT":
            txt = msg.text
            if isinstance(txt, bytes):
                txt = txt.decode("utf-8", errors="replace")
            txt = txt.rstrip("\x00")
            SEV = {0:"EMRG",1:"ALRT",2:"CRIT",3:"ERR",4:"WARN",5:"NOTE",6:"INFO",7:"DBG"}
            print(f"  [{SEV.get(msg.severity,'MSG')}] {txt}")

        # ── Periyodik özet ────────────────────────────────────────────────
        now = time.time()
        if now - last_print >= PRINT_INTERVAL:
            last_print = now
            print("\n" + "═" * 60)
            print(f"  Mod: {state.get('flight_mode','?'):12s}  ARM: {state.get('armed','?')}")
            print(f"  Alt:{state.get('alt',0.0):7.1f}m   RelAlt:{state.get('rel_alt',0.0):7.1f}m")
            print(f"  Roll:{state.get('roll_deg',0.0):+7.2f}°  Pitch:{state.get('pitch_deg',0.0):+7.2f}°  "
                  f"Yaw:{state.get('yaw_deg',0.0):+8.2f}°")
            print(f"  AirSpd:{state.get('airspeed',0.0):6.1f}  GndSpd:{state.get('groundspeed',0.0):6.1f}  "
                  f"Hdg:{state.get('heading',0):3d}°  THR:{state.get('throttle',0):3d}%  "
                  f"VS:{state.get('climb',0.0):+5.1f}")

            lat = state.get("lat", None)
            lon = state.get("lon", None)
            if lat is not None:
                abs_ok = abs(lat) > 0.001
                print(f"  GPS: {state.get('gps_fix','?')}  Sat:{state.get('gps_sats',0)}  "
                      f"HDOP:{state.get('gps_hdop',99.0):.1f}")
                print(f"  Konum: {lat:.7f}, {lon:.7f}  {'→ Haritaya yansır' if abs_ok else '⚠ 0.0 — haritaya YANSıMAZ (abs<0.001)'}")

            print(f"  Batarya: {state.get('batt_v',0.0):.2f}V  {state.get('batt_a',0.0):.1f}A  "
                  f"{state.get('batt_%',0)}%")
            print(f"  EKF: {state.get('ekf_flags','?')}")
            print(f"  Mesaj sayıları: {dict(sorted(msg_counts.items()))}")
            print("═" * 60)

except KeyboardInterrupt:
    print("\n\n─── Test Sonlandırıldı ───")
    print(f"Toplam alınan mesaj tipleri: {sorted(msg_counts.keys())}")
    total = sum(msg_counts.values())
    print(f"Toplam mesaj: {total}")
    if "ATTITUDE" not in msg_counts:
        print("⚠ ATTITUDE hiç gelmedi — veri akışı yok veya mesaj rate 0!")
    if "GLOBAL_POSITION_INT" not in msg_counts:
        print("⚠ GLOBAL_POSITION_INT hiç gelmedi — GPS konumu yok, haritaya yansımaz!")
    if state.get("lat") is not None and abs(state["lat"]) < 0.001:
        print("⚠ Enlem değeri ~0.0 — SITL'de GPS fix olmadığı için konum gönderilmiyor")
