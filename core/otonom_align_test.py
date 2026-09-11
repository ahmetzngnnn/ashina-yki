"""
core/otonom_align_test.py — Görev 2 otonom hizalama testi (GERÇEK UÇUŞ)
========================================================================
NEDEN (2026-09-05 operatör talebi): sahada aracı bir hedefin (ör. kırmızı
üçgen) yanına koyup — TARAMA YAPMADAN, doğrudan hizala→bırak→GUIDED+LAND —
gerçek otonom mission kodunu (görevde kullanılanla AYNI parametrelerle)
çalıştırmak için. Aynı senaryo `workspace/sim/gazebo/ashina_task2_
yakin_baslangic.sdf` ile simde doğrulandı (2026-09-05): kırmızıya kilitlenip
~10-50 cm isabetle bıraktı, mavi/çeldirici görüş alanındayken doğru
reddetti, RTL'e hiç girmeden GUIDED+LAND ile döndü.

GÜNCEL KONUM (2026-09-05 düzeltme): bu modül eskiden GÜNCEL konumu okumak
için `config.mavlink_host/mavlink_port`'a kendi bağımsız `tcp:` pymavlink
bağlantısını açıyordu — bu yalnız SITL'de (TCP MAVLink sunucusu birden
çok istemci kabul eder) çalışıyordu. GERÇEK donanımda YKİ'nin MAVLink
bağlantısı SERİ (RFD900 radyo, `core/mavlink_worker.py`) ve bu port zaten
ana `mavlink_worker` tarafından açık tutuluyor — ikinci bir bağlantı ya
hiç TCP dinleyicisi bulamadığı için (bu tam olarak "güncel konum
okunamadı" hatasının sebebiydi) ya da seri portu paylaşarak veri
bozulmasına yol açardı. Artık GÜNCEL konum hiç yeni bağlantı açmadan,
çağıran taraftan (`GercekGorevPaneli`, ana `mavlink_worker`'ın
`telemetry_updated` akışından SÜREKLİ biriktirdiği son GPS örneğinden)
hazır `lat0`/`lon0` olarak alınır — tıpkı `main_window._last_gps_fix`'in
beslendiği mekanizmanın aynısı.

Bu modül YKİ'nin ana MAVLink bağlantısına hiç dokunmaz; yalnız companion'ın
mission soketine (`companion_host:companion_mission_port`, IHA/comms/
gcs_server.py protokolü: \\n sonlandırmalı JSON) doğrudan bağlanıp mission +
start gönderir. `human_guided_bench.py`/SSH ile İLGİSİ YOK — bu GERÇEK
otonom mission'ı tetikler, pervaneler TAKILI olmalı.

GÜVENLİK: yalnız mission JSON gönderir; ARM/kalkış kararı companion'ın
KENDİ health-gate'inden geçer (main.py FC param doğrulama + EKF/GPS
kapıları aynen uygulanır — bu modül onları ATLAMAZ).
"""
from __future__ import annotations

import json
import logging
import math
import socket

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

MISSION_TIMEOUT_S = 10.0

# Sim doğrulamasıyla BİREBİR aynı geometri (ashina_task2_yakin_baslangic.sdf
# testinde koşuldu) — yalnız orijin artık aracın GÜNCEL GPS konumu.
_SCAN_ALT_M = 15.0
_DROP_ALT_M = 2.0
# 2026-09-06 06:15 — D11 tanı turlarından GERÇEK GÖREV PROFİLİNE dönüldü:
# irtifa 3->15 m, kutu 2x2m'den (D11 tanısı için küçültülmüştü) gerçek
# alan boyutuna geri alındı — operatör aracı hedeflerin/çeldiricilerin
# ORTASINA koyup ikisini birden (kırmızı+mavi) test edecek.
_BOX_EN = [(-10.0, -15.0), (25.0, -15.0), (25.0, 15.0), (-10.0, 15.0)]


def _enu_to_ll(lat0: float, lon0: float, e: float, n: float) -> dict:
    lat = lat0 + (n / 111320.0)
    lon = lon0 + (e / (111320.0 * math.cos(math.radians(lat0))))
    return {"lat": round(lat, 7), "lon": round(lon, 7)}


class OtonomAlignTestWorker(QThread):
    """Verilen güncel konumdan mission JSON üretir, companion'a gönderir + start eder."""

    ilerleme = pyqtSignal(str)
    basarili = pyqtSignal(str)
    hata = pyqtSignal(str)

    def __init__(self, companion_host: str, mission_port: int,
                 lat0: float, lon0: float,
                 renk: str = "red", parent=None) -> None:
        super().__init__(parent)
        self._chost = companion_host
        self._cport = mission_port
        self._lat0 = lat0
        self._lon0 = lon0
        self._renk = renk

    def run(self) -> None:
        mission = self._mission_json(self._lat0, self._lon0)
        self.ilerleme.emit(
            f"Konum: {self._lat0:.7f},{self._lon0:.7f} — mission hazırlandı "
            f"(irtifa {_SCAN_ALT_M:.0f} m, KIRMIZI+MAVİ (ikisi de), GUIDED+LAND)."
        )

        try:
            self.ilerleme.emit(f"Mission gönderiliyor ({self._chost}:{self._cport})…")
            yanit = self._gonder(mission)
            self.ilerleme.emit(f"Mission yanıtı: {yanit}")
        except Exception as exc:
            self.hata.emit(f"Mission gönderilemedi: {exc}")
            return
        # 2026-09-06 denetimde bulundu: yanıt hiç PARSE edilmiyordu — companion
        # {"status":"error","message":...} dönse bile (ör. mission JSON
        # reddedildi) kod bunu farketmeden "start" adımına geçiyordu. Aynı hata
        # sınıfı gcs_server.py'de "Arıza senaryosu 4"te bulunup düzeltilmişti
        # (last_error gerçek sebebi taşısın diye) — o düzeltme burada hiç
        # OKUNMUYORDU.
        if not self._status_ok(yanit):
            self.hata.emit(f"Mission REDDEDİLDİ: {self._hata_mesaji(yanit)}")
            return

        try:
            self.ilerleme.emit("Start komutu gönderiliyor…")
            yanit = self._gonder({"action": "start"})
            self.ilerleme.emit(f"Start yanıtı: {yanit}")
        except Exception as exc:
            self.hata.emit(f"Start gönderilemedi: {exc}")
            return
        if not self._status_ok(yanit):
            self.hata.emit(f"Start REDDEDİLDİ: {self._hata_mesaji(yanit)}")
            return

        self.basarili.emit(
            "Otonom görev başlatıldı — companion kendi health-gate'inden "
            "geçtikten sonra ARM/kalkış yapacak. Video panelinden ve "
            "olay günlüğünden izleyin."
        )

    @staticmethod
    def _status_ok(yanit: str) -> bool:
        try:
            return json.loads(yanit).get("status") == "ok"
        except (json.JSONDecodeError, AttributeError):
            return False

    @staticmethod
    def _hata_mesaji(yanit: str) -> str:
        try:
            return str(json.loads(yanit).get("message", yanit))
        except (json.JSONDecodeError, AttributeError):
            return yanit

    def _mission_json(self, lat0: float, lon0: float) -> dict:
        waypoints = [{"lat": lat0, "lon": lon0, "alt": _SCAN_ALT_M, "type": "TAKEOFF"}]
        for e, n in _BOX_EN:
            p = _enu_to_ll(lat0, lon0, e, n)
            waypoints.append({"lat": p["lat"], "lon": p["lon"],
                               "alt": _SCAN_ALT_M, "type": "WAYPOINT"})
        # 2026-09-06 06:15 — HER İKİ RENK: operatör aynı anda kırmızı+mavi
        # görüp önceliklendirmeyi (en yakın hedef kazanır, öteki kuyruğa
        # tohumlanır — bkz. alignment.py:574-599) test edecek. Eskiden
        # yalnızca `self._renk` (tek renk) alan alıyordu — çeldiricilerle
        # birlikte gerçek çift-hedef senaryosu için ikisi de gerekli.
        _alan = [_enu_to_ll(lat0, lon0, e, n) for e, n in _BOX_EN]
        target_areas = {"red": _alan, "blue": _alan}
        return {
            "mission_type": "TEKNOFEST_TASK2",
            "waypoints": waypoints,
            "target_areas": target_areas,
            "scan_altitude_m": _SCAN_ALT_M,
            "drop_altitude_m": _DROP_ALT_M,
            # 2026-09-06 gece — D11: search_plan.py'nin KENDİ ayrı irtifa
            # tabanı (search_alt_min_m, varsayılan 10.0) scan_altitude_m'den
            # BAĞIMSIZ — _region_scan onu kullanıyordu, operatörün istediği
            # 3 m'yi görmezden geliyordu. Açıkça override edilmezse mission
            # JSON'da bu alan yoktu; artık tuning.py'nin param_map'i üzerinden
            # doğrudan geçiliyor.
            "search_alt_min_m": _SCAN_ALT_M,
            # approach.py'nin "erken kimlik doğrulama irtifası" (floor +
            # approach_identity_margin_m, varsayılan 3.0) da ayrı bir taban —
            # kilitli hedefe yaklaşırken irtifa bırakma tabanının (~2 m)
            # 3 m ÜSTÜNDE tutulup bekletiliyordu. 1.0'e indirildi ki toplam
            # ~3 m civarında kalsın (operatör: "ısrarla 3 metre istiyorum").
            "approach_identity_margin_m": 1.0,
            "decoy_layout": "mixed",
            "finish_land_not_rtl": True,
            "bonus_mission": False,
            # Eskiden yalnız self._renk (tek renk) — artık ikisi de aranıyor,
            # bkz. target_areas NEDEN'i az yukarıda.
            "only_colors": ["red", "blue"],
        }

    def _gonder(self, payload: dict) -> str:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        with socket.create_connection((self._chost, self._cport), timeout=MISSION_TIMEOUT_S) as s:
            s.settimeout(MISSION_TIMEOUT_S)
            s.sendall(data)
            raw = b""
            while b"\n" not in raw:
                chunk = s.recv(4096)
                if not chunk:
                    break
                raw += chunk
        return raw.split(b"\n")[0].decode("utf-8", "replace")
