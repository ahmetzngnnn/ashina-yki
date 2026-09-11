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


# ── Papyon deseni — companion (İHA) ile BİREBİR AYNI ────────────────────────
# NEDEN BU BLOK VAR (2026-09-08, operatör bulgusu): companion'da Papyon
# deseni (PapyonTrajectory, IHA/missions/infinity_flight.py) VARDI ama bu
# dosyada hiç karşılığı YOKTU — önizleme her zaman Lemniscate/Dubins'in
# GENİŞ çemberlerini (R=D/2.75, D=150'de ≈55 m) çiziyordu, oysa operatör
# "🦋 Papyon deseni" kutusunu işaretlediğinde companion SIKI yay (r≈15 m) +
# düz tam gaz uçuyordu. Önizleme = fiilen uçulan rota sözleşmesi (bu
# dosyanın başlığı) bu yüzden BOZUKTU. Aşağıdaki iki fonksiyon,
# infinity_flight.py::papyon_parametreleri / PapyonTrajectory._segment_at
# ile BİREBİR AYNI formülleri kullanır — matematik değişirse İKİ tarafı da
# güncelleyin.

def compute_papyon_params(D: float, bank_deg: float = 10.0, v_tavan: float = 14.0,
                          a_hiz: float = 2.0, yaw_tavan_deg: float = 25.0) -> dict:
  """papyon_parametreleri (companion) ile BİREBİR AYNI türetme — bkz. oradaki
  docstring için ölçülmüş sabitlerin gerekçesi (r_taban/r_tavan/eşik 90 m)."""
  g = 9.81
  D = max(1.0, float(D))
  tanp = math.tan(math.radians(bank_deg))
  yaw = math.radians(max(1.0, yaw_tavan_deg))
  r_taban = g * tanp / (yaw * yaw)
  r_tavan = min(D / 2.1, 0.25 * D)
  r = min(max(0.10 * D, r_taban), r_tavan)
  r = max(r, 1.0)

  th = math.asin(max(-1.0, min(1.0, 2.0 * r / D))) if D > 2 * r else math.pi / 2
  L = math.sqrt(max(1e-9, D * D - 4.0 * r * r))
  A = r * (math.pi + 2.0 * th)
  lap = 2.0 * L + 2.0 * A
  v_yay = math.sqrt(g * r * tanp)
  v_ulasilir = math.sqrt(v_yay * v_yay + a_hiz * L)
  v_duz = max(v_yay, min(v_tavan, v_ulasilir))

  return {
    "papyon_uygun": bool(2.0 * L / lap >= 0.40 and D >= 90.0 * 0.99
                         and math.degrees(v_yay / r) <= yaw_tavan_deg + 0.5
                         and (L / 2.0) / max(0.1, v_yay) >= 4.6),
    "r": r, "v_duz": round(v_duz, 2), "v_yay": round(v_yay, 2),
    "lap": lap, "L": L, "A": A, "theta": th, "duz_oran": round(2.0 * L / lap, 3),
  }


def _gen_papyon_pts(p1: dict, p2: dict, num_laps: float, turn_radius_m: float,
                    sense: int = 1, n_per_lap: int = 90) -> list:
  """Papyon (sıkı yay + iç teğet düz) desenini örnekler → [(lat,lon), ...].

  PapyonTrajectory._segment_at (IHA/missions/infinity_flight.py) ile birebir
  aynı kapalı-form geometri; yalnızca (x,y) pozisyonu üretir (önizleme çizimi
  için heading/eğrilik gerekmez). M-demirli: s=0 direklerin orta noktası,
  M→direk2 yayı→M→direk1 yayı→M sırasıyla."""
  lat1, lon1 = float(p1["lat"]), float(p1["lon"])
  lat2, lon2 = float(p2["lat"]), float(p2["lon"])
  mid_lat = (lat1 + lat2) / 2.0
  mid_lon = (lon1 + lon2) / 2.0
  D = _haversine(lat1, lon1, lat2, lon2)
  beta_deg = _bearing(lat1, lon1, lat2, lon2)

  r = max(8.0, min(float(turn_radius_m), 40.0))
  if D > 1e-6:
    r = min(r, D / 2.1)
  c = D / 2.0
  theta = math.asin(max(-1.0, min(1.0, r / c))) if c > 1e-6 else 0.0
  L = math.sqrt(max(1e-9, D * D - 4.0 * r * r))
  A = r * (math.pi + 2.0 * theta)
  lap_length = 2.0 * L + 2.0 * A

  d_t = math.sqrt(max(1e-9, c * c - r * r))
  ct, st = math.cos(theta), math.sin(theta)
  T2a = (d_t * ct, d_t * st)
  T2b = (d_t * ct, -d_t * st)
  T1a = (-d_t * ct, d_t * st)
  T1b = (-d_t * ct, -d_t * st)
  C2 = (c, 0.0)
  C1 = (-c, 0.0)
  phi2 = math.atan2(T2a[1], T2a[0] - c)
  phi1 = math.atan2(T1a[1], T1a[0] + c)

  h = L / 2.0
  b = (h, h + A, h + A + L, h + 2.0 * A + L, lap_length)

  def _segment_xy(s):
    if s < b[0]:
      return s * math.cos(theta), s * math.sin(theta)
    if s < b[1]:
      u = (s - b[0]) / r
      phi = phi2 - u
      return C2[0] + r * math.cos(phi), C2[1] + r * math.sin(phi)
    if s < b[2]:
      u = s - b[1]
      hd = math.pi - theta
      return T2b[0] + u * math.cos(hd), T2b[1] + u * math.sin(hd)
    if s < b[3]:
      u = (s - b[2]) / r
      phi = phi1 + u
      return C1[0] + r * math.cos(phi), C1[1] + r * math.sin(phi)
    u = s - b[3]
    return T1b[0] + u * math.cos(theta), T1b[1] + u * math.sin(theta)

  # NEDEN num_laps FLOAT: gerçek uçuşta pist çizgisi (finish_a/b) tanımlıysa
  # companion deseni YARIM TUR uzatır (infinity_flight.py: "total_length =
  # num_laps*lap_length + lap_length/2.0") — direk 2'nin son kez dışından
  # dolanıp M'ye dönmesi için. Önizleme bunu YANSITMAZSA operatör turların
  # NEREDE bittiğini yanlış görür ve dönüş bacağı gerçekte hiç uçulmayan bir
  # yerden (eski, kaba 4-noktalı şematik tahminden) başlıyormuş gibi çizilir.
  sgn = 1 if int(sense) >= 0 else -1
  pts = [(mid_lat, mid_lon)]  # M (merkez) — Lemniscate üreticiyle AYNI sözleşme
  n_per = max(20, int(n_per_lap))
  toplam_n = max(1, round(n_per * float(num_laps)))
  toplam_s = float(num_laps) * lap_length
  for i in range(1, toplam_n + 1):
    s = (i / toplam_n) * toplam_s
    x, y = _segment_xy(s % lap_length)
    if sgn < 0:
      y = -y
    rr = math.hypot(x, y)
    brng = beta_deg + math.degrees(math.atan2(y, x))
    wlat, wlon = _offset_point(mid_lat, mid_lon, brng, rr)
    pts.append((wlat, wlon))
  return pts


def compute_optimal_turn_radius(D: float, turn_radius_override: float = 0.0) -> float:
  """R'yi DOĞRUDAN direk mesafesinden (D) türetir — companion
  (missions/infinity_flight.py::InfinityFlightMission._TARGET_D_R_RATIO) ile
  AYNI oran/taban olmalı, iki taraf BAĞIMSIZ birer kopya tutuyor (2026-08-25
  çakışma incelemesinde bulundu — tek kaynağa taşınmadı çünkü YKİ ve İHA ayrı
  repo/süreç, ortak import yok). Biri değişirse diğeri BİLEREK güncellenmeli.
  D/R≈2.75, 8-60 m taban/tavan: HENÜZ ÖLÇÜLMEMİŞ bir geometrik sezgiseldir
  (gerçek şartname direk mesafesi ~150 m'de doğrulanmadı)."""
  TARGET_D_R_RATIO = 2.75
  if turn_radius_override > 0:
    return turn_radius_override
  return max(8.0, min(60.0, D / TARGET_D_R_RATIO))


def compute_ideal_cruise(R: float, bank_design_deg: float = 10.0) -> float:
  """Verilen R'nin bank_design_deg bankta karşılığı olan ideal tur hızı (m/s).

  NEDEN 10.0 (ÖNCEDEN 20.0 — İHA ile ÇAKIŞIYORDU): companion tarafı
  (missions/infinity_flight.py::_BANK_DESIGN_DEG) 10° kullanıyor; bu önizleme
  fonksiyonu 20° varsayılanla FARKLI bir hız hesaplıyordu — operatör
  önizlemede gördüğü hız ile gerçek uçuşta olacak hız BİRBİRİNİ TUTMUYORDU.
  2026-08-25 çakışma incelemesinde bulundu, senkronize edildi."""
  return math.sqrt(9.81 * R * math.tan(math.radians(bank_design_deg)))


def build_infinity_route(p1: dict, p2: dict, cruise_ms: float, num_laps: int,
                         algorithm: str = "DUBINS",
                         max_chord_m: float = 10.0,
                         max_turn_deg: float = 18.0,
                         min_chord_m: float = 5.0,
                         turn_radius_override: float = 0.0,
                         cross_speed_mult: float = 1.35,
                         bank_design_deg: float = 10.0,
                         decel_mps2: float = 2.0) -> tuple:
  """
  _build_route (companion) ile AYNI parametre seçimi:
  R DOĞRUDAN direk mesafesinden (D) türetilir (D/R≈2.75 sweet spot) — girilen
  mesafe ne olursa olsun şekil aynı oranda kalır. Bu R'nin bank_design_deg
  bankta karşılığı olan İDEAL DÖNGÜ hızı (loop_speed) hesaplanır ve döngü
  boyunca DAİMA kullanılır. DUBINS'te ayrıca düz geçişte daha hızlı cross_speed
  hedeflenir (loop_speed ile loop_speed×cross_speed_mult arasında, cruise_ms
  sadece bunu kelepçeler) ve yavaşlama komutu frenleme mesafesi kadar erken
  (decel_mps2'ye göre) eklenir.

  NEDEN varsayılanlar companion (missions/infinity_flight.py) ile AYNI
  (bank_design_deg=10°, cross_speed_mult=1.35 — ÖNCEDEN sırasıyla 20°/1.3
  idi, 2026-08-25 çakışma incelemesinde bulunup düzeltildi; cross_speed_mult
  ayrıca AYNI oturumda D=150 m'de 45 koşuluk süpürmeyle 1.35'in en hızlı
  VE güvenli seçenek olduğu doğrulanıp companion'da da uygulandı): bu
  fonksiyon yalnızca ÖNİZLEME/QGC export ürettiği için, varsayılanları
  companion'dan sapınca operatör önizlemede gördüğünden FARKLI bir gerçek
  uçuş görüyordu.

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
  loop_speed = compute_ideal_cruise(turn_radius, bank_design_deg)
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


# ── GÖREV 2: KAPLAMA (boustrophedon) TARAMA ROTASI ───────────────────────────
# NOT: Bu blok IHA/missions/task2/geometry.py içindeki
# latlon_to_local / local_to_latlon / generate_coverage_path fonksiyonlarının
# BİREBİR aynısıdır (Görev 1'deki _gen_papyon_pts ↔ infinity_flight.py
# eşleşmesiyle aynı desen). Amaç: YKİ haritasında gösterilen tarama rotası =
# aracın GERÇEKTEN uçacağı rota. MATEMATİK DEĞİŞİRSE İKİ TARAFI DA GÜNCELLE.
# Firmware çağrısı: teknofest_task2.py::_coverage_scan →
#   geo.generate_coverage_path(area_wps, strip, align_long_axis=True)
# strip = _swath_width_m() = 2·alt·tan(HFOV/2)·(1−COVERAGE_OVERLAP), taban 3.0
_R_EARTH_COV = 6371000.0
COVERAGE_OVERLAP = 0.40      # teknofest_task2.py::COVERAGE_OVERLAP ile AYNI


def _ll_to_local(lat, lon, lat0, lon0):
  x = math.radians(lon - lon0) * _R_EARTH_COV * math.cos(math.radians(lat0))
  y = math.radians(lat - lat0) * _R_EARTH_COV
  return x, y


def _local_to_ll(x, y, lat0, lon0):
  lat = lat0 + math.degrees(y / _R_EARTH_COV)
  lon = lon0 + math.degrees(x / (_R_EARTH_COV * math.cos(math.radians(lat0))))
  return lat, lon


def swath_width_m(alt_m: float, hfov_deg: float) -> float:
  """Kaplama şerit genişliği — teknofest_task2.py::_swath_width_m ile AYNI."""
  hfov = max(10.0, min(170.0, float(hfov_deg)))
  alt = max(1.0, float(alt_m))
  footprint = 2.0 * alt * math.tan(math.radians(hfov / 2.0))
  return max(3.0, footprint * (1.0 - COVERAGE_OVERLAP))


def gen_coverage_path(kose_pts, strip_width_m: float, align_long_axis: bool = True):
  """Boustrophedon (çim biçme) kaplama rotası. kose_pts: [(lat,lon), ...]

  IHA geometry.generate_coverage_path'in aynısı; overlap zaten strip
  içinde olduğu için burada 0 kabul edilir (firmware de öyle çağırıyor).
  Dönüş: [(lat, lon), ...] — çizilecek çoklu-çizgi.
  """
  pts = [(float(a), float(o)) for a, o in kose_pts]
  if len(pts) < 3:
    return []
  lat0 = sum(p[0] for p in pts) / len(pts)
  lon0 = sum(p[1] for p in pts) / len(pts)
  poly = [_ll_to_local(la, lo, lat0, lon0) for la, lo in pts]

  # DÖNÜŞ (2026-09-10 düzeltmesi): eskiden burada x/y ARALIĞI (coğrafi
  # Doğu-Batı / Kuzey-Güney izdüşümü) karşılaştırılıp yalnız 90° swap
  # yapılıyordu. BU YANLIŞ — bir dikdörtgenin gerçek uzun/kısa kenarı
  # coğrafi eksenlere değil, KENDİ dönüşüne bağlıdır. Direk hattı köşegen
  # bir bearing'deyse (ölçüldü: 136.7°, gerçek Malatya profili) her iki
  # kenar de coğrafi eksenlere BENZER miktarda izdüşüm yapar — swap kararı
  # gürültüye bağlı hale gelip YANLIŞ ekseni seçebiliyordu (ölçüldü: x
  # aralığı 101.2 m, y aralığı 103.8 m, %2 fark — 9 şerit yerine olması
  # gereken 4 şerit üretti, operatör bunu "çapraz tarıyor" diye fark etti).
  # Doğrusu: poligonun EN UZUN KENARININ gerçek açısını bulup TÜM poligonu
  # o açıya göre döndürmek — böylece en uzun kenar HER ZAMAN yeni x
  # eksenine hizalanır, kısa boyut GARANTİ y ekseninde kalır (adım atma
  # ekseni), 90°'nin katı olmayan açılarda da doğru çalışır.
  don_ac = 0.0
  if align_long_axis and len(poly) >= 2:
    n_ = len(poly)
    en_uzun, en_uzun_ac = -1.0, 0.0
    for i in range(n_):
      x0, y0 = poly[i]; x1, y1 = poly[(i + 1) % n_]
      uzunluk = math.hypot(x1 - x0, y1 - y0)
      if uzunluk > en_uzun:
        en_uzun, en_uzun_ac = uzunluk, math.atan2(y1 - y0, x1 - x0)
    if en_uzun > 0.0:
      don_ac = en_uzun_ac
      ca, sa = math.cos(-don_ac), math.sin(-don_ac)
      poly = [(x * ca - y * sa, x * sa + y * ca) for x, y in poly]

  ys = [p[1] for p in poly]
  y_min, y_max = min(ys), max(ys)
  if strip_width_m <= 0.1:
    strip_width_m = 5.0
  step = strip_width_m          # overlap=0 (firmware çağrısıyla aynı)
  half = strip_width_m / 2.0

  if (y_max - y_min) <= strip_width_m:
    centers = [(y_min + y_max) / 2.0]
  else:
    centers = []
    y = y_min + half
    y_last = y_max - half
    while y < y_last - 1e-6:
      centers.append(y)
      y += step
    centers.append(y_last)

  yol = []
  direction = 1
  for y in centers:
    xints = []
    n = len(poly); j = n - 1
    for i in range(n):
      xi, yi = poly[i]; xj, yj = poly[j]
      if (yi > y) != (yj > y):
        xints.append(xi + (xj - xi) * (y - yi) / (yj - yi + 1e-12))
      j = i
    xints.sort()
    spans = [(xints[k], xints[k + 1]) for k in range(0, len(xints) - 1, 2)]
    if not spans:
      continue
    if direction < 0:
      spans = [(b, a) for a, b in reversed(spans)]
    for xa, xb in spans:
      ya = yb = y
      if don_ac != 0.0:
        # Ters döndür (+don_ac) — dönüşün tersi, gerçek K-D koordinatlarına
        # geri getirir. Eski kod burada yalnız 90°'lik swap yapıyordu; genel
        # açı artık aynı satırda kapsanıyor (swap onun 90° özel hali).
        ca2, sa2 = math.cos(don_ac), math.sin(don_ac)
        xa, ya = xa * ca2 - ya * sa2, xa * sa2 + ya * ca2
        xb, yb = xb * ca2 - yb * sa2, xb * sa2 + yb * ca2
      yol.append(_local_to_ll(xa, ya, lat0, lon0))
      yol.append(_local_to_ll(xb, yb, lat0, lon0))
    direction *= -1
  return yol


# ── Direk kaçınmalı dönüş rotası + tek-hat tarama (2026-09-10) ───────────
# İHA missions/task2/geometry.py'nin BİREBİR PORTU — operatör "kalkıştan
# inişe kadar TÜM rotayı önizle" istedi. O ana kadar YKİ yalnız kaplama
# desenini biliyordu; direk turu, tek-hat giriş/çıkışı ve bitiş bacağının
# direk-kaçınma mantığı SADECE İHA'daydı. Tek kaynak/parite ilkesi
# (gen_coverage_path'te olduğu gibi) korunuyor — burada YENİ bir formül
# İCAT EDİLMEZ, geometry.py'deki ile matematik BİREBİR AYNI.

def _seg_nokta_uzaklik(a, b, p) -> float:
  """a→b PARÇASININ p noktasına en kısa uzaklığı (yerel metrik düzlem)."""
  ax, ay = a; bx, by = b; px, py = p
  dx, dy = bx - ax, by - ay
  uzun2 = dx * dx + dy * dy
  if uzun2 < 1e-9:
    return math.hypot(px - ax, py - ay)
  t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / uzun2))
  return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def segment_pole_dist_m(a_ll, b_ll, pole_ll) -> float:
  """a→b DOĞRU PARÇASININ direğe en kısa yatay mesafesi (m)."""
  o = (a_ll[0], a_ll[1])
  a = (0.0, 0.0)
  b = _ll_to_local(b_ll[0], b_ll[1], o[0], o[1])
  p = _ll_to_local(pole_ll[0], pole_ll[1], o[0], o[1])
  return _seg_nokta_uzaklik(a, b, p)


def pole_clear_route(start_ll, dest_ll, poles, clear_m: float,
                     yan_ipucu: int = 0) -> list:
  """start→dest için direklerden `clear_m` uzak duran ARA NOKTA listesi.

  Dönüş: ARA noktalar (dest DAHİL DEĞİL). Boş liste: düz hat zaten temiz
  YA DA çözüm bulunamadı — çağıran gerekirse düz hatta düşer.
  """
  if not poles or clear_m <= 0:
    return []
  o = (start_ll[0], start_ll[1])
  A = (0.0, 0.0)
  B = _ll_to_local(dest_ll[0], dest_ll[1], o[0], o[1])
  P = [_ll_to_local(q[0], q[1], o[0], o[1]) for q in poles]

  _kacinilmaz = [q for q in P
                 if math.hypot(*q) < clear_m
                 or math.hypot(q[0] - B[0], q[1] - B[1]) < clear_m]
  P = [q for q in P if q not in _kacinilmaz]
  if not P:
    return []

  def ihlal_var(u, v):
    return any(_seg_nokta_uzaklik(u, v, q) < clear_m for q in P)

  if not ihlal_var(A, B):
    return []                          # düz hat zaten temiz
  en_kotu = min(P, key=lambda q: _seg_nokta_uzaklik(A, B, q))
  dx, dy = B[0] - A[0], B[1] - A[1]
  uzun = math.hypot(dx, dy)
  if uzun < 1e-6:
    return []
  nx, ny = -dy / uzun, dx / uzun         # hatta dik birim vektör
  yanlar = (1.0,) if yan_ipucu > 0 else (-1.0,) if yan_ipucu < 0 else (1.0, -1.0)
  adaylar = []
  for isaret in yanlar:
    for kat in (1.15, 1.4, 1.8, 2.5):
      ofs = clear_m * kat
      W = (en_kotu[0] + isaret * nx * ofs, en_kotu[1] + isaret * ny * ofs)
      if not ihlal_var(A, W) and not ihlal_var(W, B):
        adaylar.append((math.hypot(W[0] - A[0], W[1] - A[1])
                        + math.hypot(B[0] - W[0], B[1] - W[1]), W))
        break
  if not adaylar:
    return []                          # çözülemedi — çağıran bilir
  _, W = min(adaylar, key=lambda t: t[0])
  return [_local_to_ll(W[0], W[1], o[0], o[1])]


def compute_scanline(waypoints):
  """Tarama alanının (poligon köşeleri) ortasından uzun kenara paralel tek
  hat. `waypoints`: [{"lat","lon"}, ...] (İHA'nın _area_wps'iyle AYNI şekil).

  Dönüş: {"entry","exit","yaw_deg","length_m"} veya None.

  DÖNÜŞ (rotasyon-tabanlı eksen seçimi, gen_coverage_path ile AYNI mantık):
  poligon (≥3 köşe) varsa en uzun kenarın açısına göre döndürülüp entry/exit
  bu düzlemde (garanti doğru eksende) hesaplanır — coğrafi K-D bounding-box
  karşılaştırması köşegen bearing'lerde yanlış eksen seçebiliyordu.
  """
  pts = [(w["lat"], w["lon"]) for w in waypoints
         if w.get("lat") is not None and w.get("lon") is not None]
  if len(pts) < 2:
    return None
  lat0 = sum(p[0] for p in pts) / len(pts)
  lon0 = sum(p[1] for p in pts) / len(pts)
  loc = [_ll_to_local(la, lo, lat0, lon0) for la, lo in pts]

  don_ac = 0.0
  if len(loc) >= 3:
    n_ = len(loc)
    en_uzun, en_uzun_ac = -1.0, 0.0
    for i in range(n_):
      x0, y0 = loc[i]; x1, y1 = loc[(i + 1) % n_]
      uzunluk = math.hypot(x1 - x0, y1 - y0)
      if uzunluk > en_uzun:
        en_uzun, en_uzun_ac = uzunluk, math.atan2(y1 - y0, x1 - x0)
    if en_uzun > 0.0:
      don_ac = en_uzun_ac
      ca, sa = math.cos(-don_ac), math.sin(-don_ac)
      loc = [(x * ca - y * sa, x * sa + y * ca) for x, y in loc]

  xs = [p[0] for p in loc]; ys = [p[1] for p in loc]
  min_x, max_x = min(xs), max(xs); min_y, max_y = min(ys), max(ys)
  cx = (min_x + max_x) / 2.0; cy = (min_y + max_y) / 2.0
  if (max_x - min_x) >= (max_y - min_y):
    entry_l, exit_l = (min_x, cy), (max_x, cy)
  else:
    entry_l, exit_l = (cx, min_y), (cx, max_y)

  if don_ac != 0.0:
    ca2, sa2 = math.cos(don_ac), math.sin(don_ac)
    ex, ey = entry_l; xx, xy = exit_l
    entry_l = (ex * ca2 - ey * sa2, ex * sa2 + ey * ca2)
    exit_l = (xx * ca2 - xy * sa2, xx * sa2 + xy * ca2)

  de = exit_l[0] - entry_l[0]; dn = exit_l[1] - entry_l[1]
  yaw_deg = math.degrees(math.atan2(de, dn)) % 360.0
  e_lat, e_lon = _local_to_ll(entry_l[0], entry_l[1], lat0, lon0)
  x_lat, x_lon = _local_to_ll(exit_l[0], exit_l[1], lat0, lon0)
  return {
    "entry": {"lat": e_lat, "lon": e_lon},
    "exit":  {"lat": x_lat, "lon": x_lon},
    "yaw_deg": yaw_deg,
    "length_m": math.hypot(de, dn),
  }
