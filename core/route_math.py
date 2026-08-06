"""
route_math.py — ∞ (Sonsuz) rota üretimi + coğrafi yardımcılar
=============================================================
ÖNCEDEN bu saf fonksiyonlar mission_panel_view.py (god-object, 1700 satır) içindeydi
ve IHA/missions/infinity_flight.py ile ELLE senkron kopyaydı. Buraya taşındı → tek
yerde, test edilebilir, sözleşme net.

SENKRON SÖZLEŞMESİ: Bu matematik IHA/missions/infinity_flight.py üretimiyle BİREBİR
AYNI olmalıdır (WP İndir ile Mission Planner'da test edilen rota = araca uçuşta
gidenle aynı). Matematik değişirse İKİ tarafı da güncelleyin.
"""
from __future__ import annotations
import math
import logging

logger = logging.getLogger(__name__)


# ── Coğrafi Yardımcılar ───────────────────────────────────────────────────────
def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  R = 6371000.0
  p = math.pi / 180
  a = (
    math.sin((lat2 - lat1) * p / 2) ** 2
    + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
  )
  return 2 * R * math.asin(math.sqrt(max(0.0, a)))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  """İki nokta arasındaki başlangıç yönü (bearing) — derece, 0=K 90=D 180=G 270=B."""
  p = math.pi / 180
  dlon = (lon2 - lon1) * p
  la1, la2 = lat1 * p, lat2 * p
  x = math.sin(dlon) * math.cos(la2)
  y = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
  return (math.degrees(math.atan2(x, y)) + 360) % 360


def _offset_point(lat: float, lon: float, bearing_deg: float, dist_m: float):
  """(lat, lon) noktasını bearing_deg yönünde dist_m kadar kaydır."""
  R = 6371000.0
  d = dist_m / R
  la1 = math.radians(lat)
  lo1 = math.radians(lon)
  brng = math.radians(bearing_deg)
  la2 = math.asin(math.sin(la1) * math.cos(d) +
          math.cos(la1) * math.sin(d) * math.cos(brng))
  lo2 = lo1 + math.atan2(math.sin(brng) * math.sin(d) * math.cos(la1),
              math.cos(d) - math.sin(la1) * math.sin(la2))
  return math.degrees(la2), math.degrees(lo2)


# ── ∞ (Sonsuz) Rota Üretici — companion (İHA) ile BİREBİR AYNI ────────────────
# NOT: Bu fonksiyonlar IHA/missions/infinity_flight.py içindeki üretimin aynısıdır.
# Amaç: "WP İndir" ile Mission Planner'da test edilen rota = araca uçuşta gidenle
# birebir aynı olsun. Matematik değişirse İKİ tarafı da güncelle.

def _gen_lemniscate_pts(p1: dict, p2: dict, num_laps: int, turn_radius_m: float,
                        max_chord_m: float = 10.0, max_turn_deg: float = 18.0,
                        min_chord_m: float = 5.0,
                        min_pts: int = 10, max_pts: int = 120) -> list:
  """
  Gerono lemniskatı, EĞRİLİĞE UYARLI örnekleme ("kalemle sekiz çizer gibi").
  Ardışık noktalar arası ne mesafe max_chord_m'yi ne dönüş açısı max_turn_deg'i
  aşar → apekste (dar viraj) köşe kesme, merkez geçişinde uzun düz bacak olmaz.
  min_chord_m: FC'nin WP_RADIUS_M kabul yarıçapının (~2 m) güvenli katı — apeks
  bölgesinde noktalar bundan yakınlaşmaz, kabul çemberleri üst üste binmez.
  → [(lat,lon), ...]. (IHA/missions/infinity_flight.py ile birebir aynı algoritma.)
  """
  lat1, lon1 = float(p1["lat"]), float(p1["lon"])
  lat2, lon2 = float(p2["lat"]), float(p2["lon"])
  mid_lat = (lat1 + lat2) / 2.0
  mid_lon = (lon1 + lon2) / 2.0
  D = _haversine(lat1, lon1, lat2, lon2)
  c = D / 2.0
  R = min(turn_radius_m, D / 2.1)
  a = c + R
  s = c / a
  b_exact = R / (s * math.sqrt(1 - s ** 2))
  b = max(b_exact, math.sqrt(R * a))
  beta_deg = _bearing(lat1, lon1, lat2, lon2)  # derece (kuzey=0)

  def _xy(t):
    return a * math.sin(t), b * math.sin(t) * math.cos(t)

  def _ang_diff(x):
    while x > math.pi:
      x -= 2 * math.pi
    while x < -math.pi:
      x += 2 * math.pi
    return x

  DENSE = 2880
  max_turn_rad = math.radians(max_turn_deg)
  tg = [(k / DENSE) * 2 * math.pi for k in range(DENSE + 1)]
  xg = [_xy(t) for t in tg]
  thg = [math.atan2(b * math.cos(2 * t), a * math.cos(t)) for t in tg]
  cum = [0.0]
  for k in range(1, DENSE + 1):
    dx = xg[k][0] - xg[k - 1][0]
    dy = xg[k][1] - xg[k - 1][1]
    ds = math.hypot(dx, dy)
    dth = abs(_ang_diff(thg[k] - thg[k - 1]))
    raw = ds / max_chord_m + dth / max_turn_rad
    cum.append(cum[-1] + min(raw, ds / min_chord_m))
  total_budget = cum[-1]

  n = int(round(total_budget))
  n = max(min_pts, min(max_pts, n))
  if n % 2:
    n += 1

  def _t_at(target):
    lo, hi = 0, DENSE
    while lo < hi:
      m = (lo + hi) // 2
      if cum[m] < target:
        lo = m + 1
      else:
        hi = m
    if lo == 0:
      return 0.0
    c0, c1 = cum[lo - 1], cum[lo]
    fr = 0.0 if c1 == c0 else (target - c0) / (c1 - c0)
    return tg[lo - 1] + fr * (tg[lo] - tg[lo - 1])

  pts = [(mid_lat, mid_lon)]  # M (merkez)
  for _ in range(num_laps):
    for i in range(1, n + 1):
      t = _t_at((i / n) * total_budget)
      x, y = _xy(t)
      r = math.hypot(x, y)
      brng = beta_deg + math.degrees(math.atan2(y, x))
      wlat, wlon = _offset_point(mid_lat, mid_lon, brng, r)
      pts.append((wlat, wlon))
  return pts


def _gen_dubins_pts(p1: dict, p2: dict, num_laps: int, turn_radius_m: float,
                    max_chord_m: float = 10.0, max_turn_deg: float = 18.0,
                    min_chord_m: float = 5.0,
                    loop_speed_ms: float = None, cross_speed_ms: float = None,
                    decel_mps2: float = 2.0) -> list:
  """
  İKİ ÇEMBER + İÇ TEĞET tabanlı "yuvarlak sekiz" →
  [{"cmd":82,"lat":..,"lon":..}, {"cmd":178,"params":(p1,p2,p3,p4)}, ...].
  (IHA/missions/infinity_flight.py generate_dubins_waypoints ile birebir aynı
  algoritma — bkz. oradaki docstring için tasarım gerekçesi.)

  loop_speed_ms verilirse WP-bazlı hız profili eklenir: döngü boyunca sabit
  loop_speed, düz geçişte daha hızlı cross_speed; yavaşlama komutu döngü
  başlamadan frenleme mesafesi kadar erken eklenir (bkz. companion tarafı).
  """
  lat1, lon1 = float(p1["lat"]), float(p1["lon"])
  lat2, lon2 = float(p2["lat"]), float(p2["lon"])
  mid_lat = (lat1 + lat2) / 2.0
  mid_lon = (lon1 + lon2) / 2.0
  D = _haversine(lat1, lon1, lat2, lon2)
  R = min(turn_radius_m, D / 2.1)
  alpha = math.acos(2 * R / D)
  beta = math.radians(_bearing(lat1, lon1, lat2, lon2))
  sweep = 2 * math.pi - 2 * alpha
  arc_len = R * sweep
  tangent_len = math.sqrt(max(0.0, D * D - 4 * R * R))

  def _n_for(length, lo=3):
    if length <= 0:
      return lo
    raw = length / max_chord_m
    cap_low = length / min_chord_m
    return max(lo, min(round(cap_low), max(round(raw), lo)))

  def _n_for_arc(length, sweep_rad, lo=6):
    if length <= 0:
      return lo
    raw_chord = length / max_chord_m
    raw_turn = math.degrees(sweep_rad) / max_turn_deg
    raw_n = max(raw_chord, raw_turn) + 1
    cap_low = length / min_chord_m + 1
    return max(lo, min(round(cap_low), round(raw_n)))

  n_arc = _n_for_arc(arc_len, sweep, lo=6)
  n_cross_full = _n_for(tangent_len, lo=2)
  n_cross_half = _n_for(tangent_len / 2.0, lo=1)

  def _arc(clat, clon, start, cw):
    out = []
    for i in range(n_arc):
      frac = i / (n_arc - 1) if n_arc > 1 else 0
      ang = start + frac * sweep if cw else start - frac * sweep
      wlat, wlon = _offset_point(clat, clon, math.degrees(ang), R)
      out.append((wlat, wlon))
    return out

  def _cross(flat, flon, tlat, tlon, n):
    out = []
    for i in range(1, n):
      frac = i / n
      out.append((flat + (tlat - flat) * frac, flon + (tlon - flon) * frac))
    return out

  pts = [(mid_lat, mid_lon)]  # M
  tags = ["cross"]
  ta2 = _offset_point(lat2, lon2, math.degrees(beta + math.pi - alpha), R)
  ta1 = _offset_point(lat1, lon1, math.degrees(beta + alpha), R)

  def _addp(new_pts, tag):
    pts.extend(new_pts)
    tags.extend([tag] * len(new_pts))

  for lap in range(num_laps):
    if lap == 0:
      _addp(_cross(mid_lat, mid_lon, ta2[0], ta2[1], n_cross_half), "cross")
    p2_arc = _arc(lat2, lon2, beta + math.pi - alpha, cw=False)
    _addp(p2_arc, "arc")
    _addp(_cross(p2_arc[-1][0], p2_arc[-1][1], ta1[0], ta1[1], n_cross_full), "cross")
    p1_arc = _arc(lat1, lon1, beta + alpha, cw=True)
    _addp(p1_arc, "arc")
    if lap < num_laps - 1:
      _addp(_cross(p1_arc[-1][0], p1_arc[-1][1], mid_lat, mid_lon, n_cross_half), "cross")
      _addp([(mid_lat, mid_lon)], "cross")

  nav_items = [{"cmd": 82, "lat": la, "lon": lo} for (la, lo) in pts]

  if loop_speed_ms is None:
    return nav_items

  cross_speed_ms = max(cross_speed_ms or loop_speed_ms, loop_speed_ms)
  n = len(pts)
  brake_dist = max(0.0, (cross_speed_ms ** 2 - loop_speed_ms ** 2) / (2.0 * decel_mps2))
  insertions = {0: cross_speed_ms}
  for i in range(1, n):
    if tags[i] == "arc" and tags[i - 1] == "cross":
      back = i
      acc = 0.0
      while back > 0 and acc < brake_dist:
        acc += _haversine(pts[back - 1][0], pts[back - 1][1], pts[back][0], pts[back][1])
        back -= 1
      insertions[back] = loop_speed_ms
    elif tags[i] == "cross" and tags[i - 1] == "arc":
      insertions[i] = cross_speed_ms

  def _speed_item(speed):
    return {"cmd": 178, "params": (1.0, float(speed), -1.0, 0.0)}

  out = []
  for idx in range(n):
    if idx in insertions:
      out.append(_speed_item(insertions[idx]))
    out.append(nav_items[idx])
  return out


def compute_optimal_turn_radius(D: float, turn_radius_override: float = 0.0) -> float:
  """R'yi DOĞRUDAN direk mesafesinden (D) türetir — companion _build_route ile
  AYNI mantık. D/R≈2.75 sweet spot: en akıcı + en çok sekize benzeyen oran."""
  TARGET_D_R_RATIO = 2.75
  if turn_radius_override > 0:
    return turn_radius_override
  return max(8.0, min(60.0, D / TARGET_D_R_RATIO))


def compute_ideal_cruise(R: float, bank_design_deg: float = 20.0) -> float:
  """Verilen R'nin bank_design_deg bankta karşılığı olan ideal tur hızı (m/s)."""
  return math.sqrt(9.81 * R * math.tan(math.radians(bank_design_deg)))


def build_infinity_route(p1: dict, p2: dict, cruise_ms: float, num_laps: int,
                         algorithm: str = "DUBINS",
                         max_chord_m: float = 10.0,
                         max_turn_deg: float = 18.0,
                         min_chord_m: float = 5.0,
                         turn_radius_override: float = 0.0,
                         cross_speed_mult: float = 1.3,
                         decel_mps2: float = 2.0) -> tuple:
  """
  _build_route (companion) ile AYNI parametre seçimi:
  R DOĞRUDAN direk mesafesinden (D) türetilir (D/R≈2.75 sweet spot) — girilen
  mesafe ne olursa olsun şekil aynı oranda kalır. Bu R'nin 20° bankta karşılığı
  olan İDEAL DÖNGÜ hızı (loop_speed) hesaplanır ve döngü boyunca DAİMA kullanılır.
  DUBINS'te ayrıca düz geçişte daha hızlı cross_speed hedeflenir (loop_speed ile
  loop_speed×cross_speed_mult arasında, cruise_ms sadece bunu kelepçeler) ve
  yavaşlama komutu frenleme mesafesi kadar erken (decel_mps2'ye göre) eklenir.

  Dönüş: (route_items, loop_speed, cross_speed, turn_radius)
  route_items: [{"cmd":82,"lat":..,"lon":..}, {"cmd":178,"params":(..)}, ...]

  Varsayılan DUBINS ("yuvarlak sekiz"): sabit eğrilikli döngüler → AKICI uçuş,
  önceliği tam matematiksel ∞ şekli değil. LEMNISCATE isteğe bağlı (daha keskin
  apeks ama gerçek Gerono ∞ eğrisi; WP-bazlı hız profili LEMNISCATE'te uygulanmaz).
  """
  lat1, lon1 = float(p1["lat"]), float(p1["lon"])
  lat2, lon2 = float(p2["lat"]), float(p2["lon"])
  D = _haversine(lat1, lon1, lat2, lon2)
  turn_radius = compute_optimal_turn_radius(D, turn_radius_override)
  loop_speed = compute_ideal_cruise(turn_radius)
  cross_speed_ceiling = loop_speed * cross_speed_mult
  cruise_requested = cruise_ms if cruise_ms > 0 else cross_speed_ceiling
  cross_speed = min(max(cruise_requested, loop_speed), cross_speed_ceiling)

  if algorithm == "LEMNISCATE":
    pts = _gen_lemniscate_pts(p1, p2, num_laps, turn_radius, max_chord_m, max_turn_deg, min_chord_m)
    route = [{"cmd": 82, "lat": la, "lon": lo} for (la, lo) in pts]
    return route, loop_speed, loop_speed, turn_radius

  route = _gen_dubins_pts(p1, p2, num_laps, turn_radius, max_chord_m, max_turn_deg, min_chord_m,
                          loop_speed_ms=loop_speed, cross_speed_ms=cross_speed, decel_mps2=decel_mps2)
  return route, loop_speed, cross_speed, turn_radius


def infinity_to_qgc_wpl(tkf_lat: float, tkf_lon: float, route_items: list,
                        alt_m: float, return_to_home: bool = True) -> str:
  """
  ∞ rotasından Mission Planner uyumlu QGC WPL 110 metni üretir.
  Sıra: HOME(0) → TAKEOFF(1) → [SPLINE(82) | DO_CHANGE_SPEED(178)]×N → [RTL(20)].
  route_items: build_infinity_route'un döndürdüğü zengin liste —
  {"cmd":82,"lat":..,"lon":..} (NAV) veya {"cmd":178,"params":(p1,p2,p3,p4)} (DO_).
  NOT: HOME ve TAKEOFF yalnızca MP'de tek başına test edilebilsin diye eklenir;
  companion uçuşta kalkışı GUIDED yapar, rota noktaları ise birebir aynıdır.
  """
  lines = ["QGC WPL 110"]
  seq = 0
  # HOME (kalkış noktası) — frame 0 (GLOBAL), cmd 16
  lines.append(f"{seq}\t1\t0\t16\t0\t0\t0\t0\t"
               f"{tkf_lat:.8f}\t{tkf_lon:.8f}\t0.000000\t1")
  seq += 1
  # TAKEOFF — frame 3 (GLOBAL_RELATIVE_ALT), cmd 22
  lines.append(f"{seq}\t0\t3\t22\t0\t0\t0\t0\t0.00000000\t0.00000000\t"
               f"{alt_m:.6f}\t1")
  seq += 1
  # ∞ rota maddeleri — NAV (82) veya DO_CHANGE_SPEED (178)
  for item in route_items:
    if item["cmd"] == 178:
      p1, p2, p3, p4 = item["params"]
      lines.append(f"{seq}\t0\t3\t178\t{p1:.6f}\t{p2:.6f}\t{p3:.6f}\t{p4:.6f}\t"
                   f"0.00000000\t0.00000000\t0.000000\t1")
    else:
      lines.append(f"{seq}\t0\t3\t{item['cmd']}\t0\t0\t0\t0\t"
                   f"{item['lat']:.8f}\t{item['lon']:.8f}\t{alt_m:.6f}\t1")
    seq += 1
  # RTL — cmd 20
  if return_to_home:
    lines.append(f"{seq}\t0\t3\t20\t0\t0\t0\t0\t0.00000000\t0.00000000\t0.000000\t1")
    seq += 1
  return "\n".join(lines) + "\n"


def _estimate_battery(wps: list, cruise_ms: float, avg_A: float, bat_mah: float) -> dict:
  if len(wps) < 2:
    return {}
  dist = sum(
    _haversine(wps[i - 1]["lat"], wps[i - 1]["lon"], wps[i]["lat"], wps[i]["lon"])
    for i in range(1, len(wps))
  )
  max_alt = max(w["alt"] for w in wps) if wps else 0
  total_s = max_alt / 3.0 + dist / max(cruise_ms, 0.1) + max_alt / 1.5 + 30
  consumed = avg_A * (total_s / 3600) * 1000 # mAh
  required = consumed / 0.8          # %20 rezerv dahil
  return {
    "dist_m": dist,
    "time_s": total_s,
    "consumed_mah": consumed,
    "required_mah": required,
    "sufficient": (bat_mah >= required) if bat_mah > 0 else None,
  }
