"""
core/tracker_math.py — Anten Takip Geometrisi (saf matematik)
=============================================================

Yan etkisiz fonksiyonlar — antenin drona bakması için gereken azimut (pan) ve
elevasyon (tilt) açılarını hesaplar. Kolay test edilir, donanımdan bağımsızdır.

Koordinat kuralı:
  • azimut (azimuth): kuzeyden saat yönü, 0..360°  (anten PAN ekseni)
  • elevasyon (elevation): yataydan yukarı, -90..+90°  (anten TILT ekseni)
  • irtifa: TÜM irtifalar AMSL (deniz seviyesi) metre — göreli (relative_alt)
    irtifa ASLA doğrudan verilmemeli; çağıran taraf AMSL'e çevirmelidir.
"""
from __future__ import annotations
import math

_R_EARTH = 6_371_000.0  # m


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """İki GPS noktası arası yer (yatay) mesafe — metre."""
    r = math.radians
    dlat = r(lat2 - lat1); dlon = r(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * _R_EARTH * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """P1'den P2'ye azimut (kuzeyden saat yönü, 0..360°)."""
    r = math.radians
    dlon = r(lon2 - lon1)
    x = math.sin(dlon) * math.cos(r(lat2))
    y = math.cos(r(lat1)) * math.sin(r(lat2)) - math.sin(r(lat1)) * math.cos(r(lat2)) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def elevation_deg(ground_dist_m: float, alt_diff_m: float) -> float:
    """Yatay mesafe ve irtifa farkından elevasyon açısı (-90..+90°)."""
    if ground_dist_m <= 0.01:
        return 90.0 if alt_diff_m > 0 else (-90.0 if alt_diff_m < 0 else 0.0)
    return math.degrees(math.atan2(alt_diff_m, ground_dist_m))


def compute_aim(ant_lat: float, ant_lon: float, ant_alt_amsl: float,
                drone_lat: float, drone_lon: float, drone_alt_amsl: float) -> dict:
    """
    Antenin drona bakması için hedef açıları hesaplar.

    ⚠ İRTİFA REFERANSI — ZORUNLU: her iki irtifa da **AMSL** (deniz seviyesinden
    metre) olmalıdır. Karışık referans (ör. drone `relative_alt` + anten GPS AMSL)
    doğrudan yanlış elevasyona yol açar: 850 m rakımda 30 m irtifada uçan bir İHA
    için 30 - 850 = -820 m fark → elevasyon ≈ -76° → anten YERE bakar, link kopar.
    Drone tarafında `relative_alt` kullanılacaksa çağırmadan ÖNCE
    `drone_amsl = home_alt(AMSL) + relative_alt` dönüşümü yapılmalıdır
    (bkz. core/antenna_tracker_worker.py:update_drone_position).

    ant_*   : anten konumu (lat, lon, irtifa m **AMSL**)
    drone_* : drone konumu (lat, lon, irtifa m **AMSL**)

    Dönüş: {
      "azimuth":   pan açısı (0..360°),
      "elevation": tilt açısı (-90..+90°),
      "ground_distance_m": yatay mesafe,
      "slant_distance_m":  doğrudan (eğik) mesafe,
      "alt_diff_m": irtifa farkı (drone - anten), her ikisi de AMSL,
    }
    """
    ground = haversine_m(ant_lat, ant_lon, drone_lat, drone_lon)
    alt_diff = drone_alt_amsl - ant_alt_amsl   # aynı referans (AMSL) şart
    az = bearing_deg(ant_lat, ant_lon, drone_lat, drone_lon)
    el = elevation_deg(ground, alt_diff)
    slant = math.hypot(ground, alt_diff)
    return {
        "azimuth": round(az, 2),
        "elevation": round(el, 2),
        "ground_distance_m": round(ground, 1),
        "slant_distance_m": round(slant, 1),
        "alt_diff_m": round(alt_diff, 1),
    }


def shortest_yaw_delta(current_deg: float, target_deg: float) -> float:
    """İki azimut arası en kısa dönüş farkı (-180..+180°)."""
    d = (target_deg - current_deg + 540.0) % 360.0 - 180.0
    return d


def wrap_safe_target(current_deg: float, bearing_deg_: float,
                     min_deg: float = -180.0, max_deg: float = 180.0) -> float | None:
    """Kablo sarmasına karşı güvenli hedef açı — mekanik limitler içinde.

    NEDEN GEREKLİ: Anten koaksiyel kabloyla bağlıdır ve sonsuz dönemez.
    `shortest_yaw_delta` her zaman EN KISA yolu seçer; kuzeyi tekrar tekrar
    geçen bir İHA'yı izlerken bu, anteni hep aynı yöne çevirip **kabloyu
    sarar** (birkaç turda kablo kopar ya da konnektör zorlanır).

    Bu fonksiyon, hedef pusula açısının (0..360) mekanik olarak eşdeğer
    tüm karşılıklarını (bearing + 360·k) üretir, limitler içinde kalanları
    seçer ve bunlardan mevcut konuma EN YAKIN olanı döndürür. Böylece
    eksen fiziksel sınırın dışına hiç çıkmaz.

    Eksen konumu 0..360 ile SINIRLI DEĞİLDİR: -180..+180 aralığında çalışan
    bir eksende "350° pusula" hedefi -10° olarak döner.

    Dönüş: hedef eksen açısı (derece) veya limitler içinde karşılığı yoksa
    None — bu durumda anten oraya bakamaz, çağıran tarafın operatörü
    uyarması gerekir (kör açı).
    """
    if max_deg < min_deg:
        min_deg, max_deg = max_deg, min_deg
    best: float | None = None
    # bearing + 360k'nın limitlere düşebileceği tüm k değerleri
    k_lo = math.floor((min_deg - bearing_deg_) / 360.0)
    k_hi = math.ceil((max_deg - bearing_deg_) / 360.0)
    eps = 1e-9
    for k in range(k_lo, k_hi + 1):
        cand = bearing_deg_ + 360.0 * k
        if (min_deg - eps) <= cand <= (max_deg + eps):
            if best is None or abs(cand - current_deg) < abs(best - current_deg):
                best = cand
    return best
