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

⚠ İRTİFA REFERANSI (KRİTİK):
  Bu worker'daki TÜM irtifalar **AMSL** (deniz seviyesinden metre) tutulur.
  • Anten home irtifası: anten Pixhawk GPS'inden GLOBAL_POSITION_INT.alt → AMSL.
  • İHA irtifası: telemetride "alt"/"relative_alt" GÖRELİ'dir (kalkışa göre);
    HOME_POSITION'dan gelen "home_alt" (AMSL) ile toplanarak AMSL'e çevrilir:
        drone_amsl = home_alt + relative_alt
  Karıştırılırsa (örn. 850 m rakımda 30 m göreli irtifa) alt_diff = 30-850 = -820
  → elevasyon ≈ -76° → anten yere bakar ve link kopar. Bu yüzden home_alt yoksa
  takip BAŞLATILMAZ; error_occurred sinyali yayınlanır.
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
                 drive: str = "pixhawk",
                 mount=None,
                 parent=None):
        super().__init__(parent)
        self._conn_string = conn_string      # anten Pixhawk MAVLink adresi
        self._mode        = mode             # "forward" | "compute"

        # ── Step motor direği (NEMA17 + TB6600) ──────────────────────────
        # drive="stepper" iken açı komutları MAVLink yerine doğrudan
        # core/stepper_driver.TrackerMount'a gider.
        self._drive = drive if drive in ("pixhawk", "stepper") else "pixhawk"
        self._mount = mount                  # TrackerMount | None
        # Eksen açısı ile GERÇEK pusula açısı arasındaki fark:
        #     gerçek_yön = eksen_açısı + _az_offset
        # Referanslama sırasında (anten mekanik limitte, motorlar enerjisiz)
        # manyetometre okunarak hesaplanır. None → henüz referanslanmadı.
        self._az_offset: float | None = None
        # Anten Pixhawk'ından gelen son ölçümler (referanslama ve teşhis için)
        self._ant_heading_deg: float | None = None   # manyetometre yönü
        self._ant_pitch_deg: float | None = None     # IMU eğimi
        # Anten konumu: (0,0,0) ise ilk geçerli drone fix'inde / Pixhawk GPS'iyle ayarlanır
        self._home = {"lat": home_lat, "lon": home_lon, "alt": home_alt}
        self._home_locked = bool(home_lat and home_lon)
        self._min_dist = min_distance_m
        self._period   = 1.0 / max(0.5, update_hz)

        self._enabled = False
        self._conn = None
        # son drone konumu {lat, lon, alt_amsl, rel_alt} — alt_amsl AMSL referanslı
        self._drone = None
        self._last_send = 0.0
        # NEDEN: İHA telemetrisi göreli irtifa verir; AMSL'e çevirmek için
        # HOME_POSITION'dan gelen home_alt (AMSL) gerekir. Gelene kadar takip
        # yapılamaz — referans uyuşmazlığı anteni yere baktırır.
        self._drone_home_alt_amsl = None     # AMSL, HOME_POSITION.altitude
        # Uyarı kısıtlama (spam engeli) — SEBEP BAŞINA ayrı sayaç.
        # NEDEN ayrı: tek ortak sayaç kullanılırsa, 3 sn önce "irtifa referansı
        # yok" uyarısı çıktığı için hemen ardından gelen "kör açı" uyarısı
        # SUSTURULUR ve operatör antenin neden durduğunu öğrenemez.
        self._warn_at: dict[str, float] = {}
        # Kalibrasyon ölçümü: {"az": {"min": adım, "max": adım}} — limit
        # damgalanmadan ÖNCEKİ ham sayaç. İki limit arası motorla sürüldüyse
        # fark, gerçek açıklığın kaç adım ettiğini verir.
        self._limit_marks: dict[str, dict[str, int]] = {}

    # ── Kontrol (ana thread'den) ───────────────────────────────────────────
    def enable(self) -> None:
        self._enabled = True
        logger.info("Anten takip etkinleştirildi (mod=%s, hedef=%s)", self._mode, self._conn_string)

    def disable(self) -> None:
        self._enabled = False
        self.connection_changed.emit(False)
        logger.info("Anten takip devre dışı.")

    def set_antenna_home(self, lat: float, lon: float, alt: float) -> None:
        """Anten (sabit) konumunu elle ayarla — örn. saha kurulumunda ölçülen GPS.

        alt: **AMSL** metre (deniz seviyesi). Göreli irtifa VERİLMEMELİDİR.
        """
        self._home = {"lat": lat, "lon": lon, "alt": alt}
        self._home_locked = True
        logger.info("Anten home ayarlandı: %.6f, %.6f, %.1f m AMSL", lat, lon, alt)

    def set_home_from_current_drone(self) -> bool:
        """Anten home'unu anlık İHA konumuna ayarla (saha kalibrasyonu).
        Anten ile İHA aynı noktadayken (kalkış öncesi) çağrılır. True=başarılı.

        NEDEN: İHA'nın AMSL irtifası kullanılır (göreli değil) — anten home'u da
        AMSL referansında tutulmalı, aksi halde compute_aim referans karıştırır."""
        if self._drone is None:
            logger.warning("Anten home ayarlanamadı — İHA konumu henüz yok.")
            return False
        d = self._drone
        if d.get("alt_amsl") is None:
            msg = ("Anten home ayarlanamadı — İHA AMSL irtifası bilinmiyor "
                   "(HOME_POSITION/home_alt telemetride yok).")
            logger.warning(msg)
            self.error_occurred.emit(msg)
            return False
        self.set_antenna_home(d["lat"], d["lon"], float(d["alt_amsl"]))
        return True

    # ── Referanslama (mekanik limit + manyetometre) ────────────────────────
    def reference_at_limit(self, edge: str = "min", axis: str = "az") -> dict:
        """Anten ELLE mekanik limite dayandırıldıktan sonra çağrılır.

        YÖNTEM (operatör adımları):
          1. Motorlar enerjisizken anten elle azimut mekanik dayanağına itilir.
          2. Bu düğmeye basılır.

        Mekanik dayanak TEKRARLANABİLİR bir sıfır verir; anten Pixhawk'ının
        manyetometresi de o sıfırın COĞRAFİ karşılığını söyler. Böylece:
          gerçek_yön = eksen_açısı + _az_offset
        Ölçüm motorlar DURURKEN ve ENERJİSİZKEN alınır — NEMA 17'lerin kalıcı
        mıknatısları ve TB6600'ün anahtarlama akımı manyetometreyi bozar;
        tek seferlik ölçüm bu girişimden kaçınmanın en basit yoludur.
        Bundan sonrası saf adım sayımıdır (açık çevrim).

        edge: "min" | "max" — hangi mekanik dayanağa gidildiği.
        Dönüş: {"ok": bool, "mesaj": str, ...}
        """
        if self._mount is None:
            return {"ok": False, "mesaj": "Step motor direği bağlı değil."}
        ax = self._mount.az if axis == "az" else self._mount.el
        limit_deg = ax.cfg.min_deg if edge == "min" else ax.cfg.max_deg

        # Referanslama sırasında motor kesinlikle hareket etmemeli
        ax.set_enabled(False)
        ax.set_current_angle(limit_deg)

        if axis != "az":
            return {"ok": True, "eksen": axis, "eksen_acisi": limit_deg,
                    "mesaj": f"{axis} ekseni {limit_deg:.1f}° mekanik limitte referanslandı."}

        hdg = self._ant_heading_deg
        if hdg is None:
            self._az_offset = None
            return {"ok": False, "eksen_acisi": limit_deg,
                    "mesaj": ("Eksen sıfırlandı ama manyetometre yönü YOK — anten "
                              "Pixhawk'ından heading gelmiyor. Coğrafi hizalama "
                              "yapılamadı, takip başlatılamaz.")}
        self._az_offset = (hdg - limit_deg) % 360.0
        msg = (f"Referans alındı: mekanik limit {limit_deg:.1f}° = pusula {hdg:.1f}°  "
               f"(ofset {self._az_offset:.1f}°). Bundan sonrası adım sayımı.")
        logger.info(msg)
        return {"ok": True, "eksen_acisi": limit_deg, "pusula": hdg,
                "ofset": round(self._az_offset, 2), "mesaj": msg}

    # ── Arayüzden gelen direk komutları ────────────────────────────────────
    mount_feedback = pyqtSignal(dict)   # {"tur": "referans"|"kalibrasyon"|"geometri", ...}

    @pyqtSlot(str, str)
    def on_reference_limit(self, axis: str, edge: str) -> None:
        """'Sol/Sağ/Alt/Üst limit' düğmeleri.

        Azimutta İKİ limit de alınırsa aradaki adım farkı saklanır; operatör
        'Redüksiyonu Hesapla' derse bu ölçüm kullanılır (bkz. on_calibrate_span).
        """
        if self._mount is None:
            self.mount_feedback.emit({"tur": "referans", "ok": False,
                                      "mesaj": "Step motor direği yok."})
            return
        ax = self._mount.az if axis == "az" else self._mount.el
        steps_before = ax.raw_steps
        res = self.reference_at_limit(edge=edge, axis=axis)
        # Kalibrasyon ölçümü — DİKKAT: referanslama sayacı limit açısına
        # DAMGALAR, yani sayaç sıçrar. Bu yüzden hem damgadan ÖNCEKİ hem
        # SONRAKİ değer saklanır. Gidilen gerçek yol:
        #     ikinci_limit.oncesi − birinci_limit.sonrasi
        # (Sadece "öncesi" saklanırsa ilk damganın sıçraması yolun yarısını
        # yutar ve redüksiyon iki kat küçük çıkar — testte yakalandı.)
        self._limit_marks.setdefault(axis, {})[edge] = {
            "oncesi": steps_before, "sonrasi": ax.raw_steps, "t": time.time(),
        }
        res["tur"] = "referans"
        self.mount_feedback.emit(res)
        self._emit_geometry()

    @pyqtSlot(str, float)
    def on_jog(self, axis: str, delta_deg: float) -> None:
        """Elle döndürme. Motorlar enerjisizse önce enerjilendirilir —
        aksi halde düğme 'ölü' görünürdü."""
        if self._mount is None:
            return
        ax = self._mount.az if axis == "az" else self._mount.el
        ax.set_enabled(True)
        ax.jog(delta_deg)

    @pyqtSlot(float, float)
    def on_set_gear_ratio(self, az_ratio: float, el_ratio: float) -> None:
        """Operatörün girdiği redüksiyon oranlarını uygular ve kaydeder."""
        if self._mount is None:
            return
        try:
            self._mount.az.set_gear_ratio(az_ratio)
            self._mount.el.set_gear_ratio(el_ratio)
        except ValueError as exc:
            self.mount_feedback.emit({"tur": "geometri", "ok": False,
                                      "mesaj": f"Geçersiz oran: {exc}"})
            return
        self._persist_gear_ratios()
        self._emit_geometry()

    @pyqtSlot()
    def on_calibrate_span(self) -> None:
        """Azimutta iki limit arası MOTORLA sürüldüyse redüksiyonu türetir."""
        if self._mount is None:
            return
        marks = self._limit_marks.get("az", {})
        if "min" not in marks or "max" not in marks:
            self.mount_feedback.emit({
                "tur": "kalibrasyon", "ok": False,
                "mesaj": ("Önce SOL ve SAĞ limitlerin ikisi de referanslanmalı "
                          "(arada motorla sürerek).")})
            return
        span_deg = abs(self._mount.az.cfg.max_deg - self._mount.az.cfg.min_deg)
        # Hangi limit ÖNCE damgalandıysa onun "sonrası", sonra damgalananın
        # "öncesi" ile karşılaştırılır — aradaki fark motorla gidilen yoldur.
        ilk, son = ("min", "max") if marks["min"]["t"] <= marks["max"]["t"] else ("max", "min")
        measured = marks[son]["oncesi"] - marks[ilk]["sonrasi"]
        res = self._mount.az.calibrate_from_span(measured, span_deg)
        res["tur"] = "kalibrasyon"
        if res.get("ok"):
            self._persist_gear_ratios()
            # Ölçüm tüketildi; yeni kalibrasyon için limitler tekrar alınmalı.
            self._limit_marks.pop("az", None)
        self.mount_feedback.emit(res)
        self._emit_geometry()

    def _persist_gear_ratios(self) -> None:
        """Girilen oranları settings.json'a yazar (yeniden açılışta korunur)."""
        try:
            from core.gcs_config import config as _cfg
            for name, ax in (("antenna_axis_az", self._mount.az),
                             ("antenna_axis_el", self._mount.el)):
                d = dict(_cfg.get(name) or {})
                d["gear_ratio"] = round(float(ax.cfg.gear_ratio), 6)
                _cfg.set(name, d)
            _cfg.save()
        except Exception:
            logger.exception("Redüksiyon oranı kaydedilemedi")

    def _emit_geometry(self) -> None:
        if self._mount is None:
            return
        az, el = self._mount.az, self._mount.el
        self.mount_feedback.emit({
            "tur": "geometri", "ok": True,
            "az_reduksiyon": az.cfg.gear_ratio, "el_reduksiyon": el.cfg.gear_ratio,
            "mesaj": (f"Azimut {az.steps_per_degree():.1f} adım/derece "
                      f"({az.cfg.min_deg:.0f}..{az.cfg.max_deg:.0f}°)  ·  "
                      f"Elevasyon {el.steps_per_degree():.1f} adım/derece "
                      f"({el.cfg.min_deg:.0f}..{el.cfg.max_deg:.0f}°)"),
        })

    def _should_warn(self, sebep: str, period_s: float = 10.0) -> bool:
        """Aynı sebepli uyarıyı en fazla period_s'de bir yayınla (sebep başına)."""
        now = time.time()
        if now - self._warn_at.get(sebep, 0.0) < period_s:
            return False
        self._warn_at[sebep] = now
        return True

    def is_referenced(self) -> bool:
        return self._az_offset is not None

    def clear_reference(self) -> None:
        self._az_offset = None

    def _capture_antenna_attitude(self, msg) -> None:
        """Anten Pixhawk'ından yön/eğim ölçümlerini yakalar (referans için)."""
        t = msg.get_type()
        if t == "VFR_HUD":
            self._ant_heading_deg = float(msg.heading) % 360.0
        elif t == "GLOBAL_POSITION_INT" and getattr(msg, "hdg", 65535) != 65535:
            self._ant_heading_deg = (msg.hdg / 100.0) % 360.0
        elif t == "ATTITUDE":
            import math as _m
            self._ant_pitch_deg = _m.degrees(msg.pitch)
            if self._ant_heading_deg is None:
                self._ant_heading_deg = _m.degrees(msg.yaw) % 360.0

    @pyqtSlot(dict)
    def update_drone_position(self, telem: dict) -> None:
        """mavlink_worker.telemetry_updated → İHA konumu beslemesi.

        NEDEN (KRİTİK DÜZELTME): telemetrideki "alt"/"relative_alt" GÖRELİ irtifadır
        (kalkış noktasına göre), anten home irtifası ise Pixhawk GPS'inden AMSL
        gelir. İkisi doğrudan çıkarılırsa (drone_alt - ant_alt) rakım kadar hatalı
        negatif fark oluşur ve anten yere bakar. Burada göreli irtifa,
        HOME_POSITION'dan gelen home_alt (AMSL) ile toplanarak AMSL'e çevrilir.
        """
        lat = telem.get("lat"); lon = telem.get("lon")
        rel = telem.get("relative_alt", telem.get("alt"))
        if lat is None or lon is None:
            return
        # Geçersiz/sıfır fix'i ele
        if abs(lat) < 1e-6 and abs(lon) < 1e-6:
            return

        # İHA kalkış noktası irtifası (AMSL) — HOME_POSITION.altitude
        home_amsl = telem.get("home_alt", telem.get("home_altitude"))
        if home_amsl is not None:
            self._drone_home_alt_amsl = float(home_amsl)

        rel_f = float(rel or 0.0)
        alt_amsl = (self._drone_home_alt_amsl + rel_f
                    if self._drone_home_alt_amsl is not None else None)

        self._drone = {"lat": float(lat), "lon": float(lon),
                       "rel_alt": rel_f, "alt_amsl": alt_amsl}

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
                # Gelen mesajları boşalt (heartbeat canlı tut) ve anten
                # yön/eğim ölçümlerini yakala — referanslama bunları kullanır.
                # NEDEN döngü: tek recv_match çağrısı kuyruğu boşaltmaz;
                # VFR_HUD/ATTITUDE mesajları arada kaybolur ve referans
                # alınmak istendiğinde "manyetometre yönü yok" hatası verir.
                for _ in range(20):
                    m = self._conn.recv_match(blocking=False)
                    if m is None:
                        break
                    try:
                        self._capture_antenna_attitude(m)
                    except Exception:
                        pass
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
        """Anten Pixhawk GPS'inden home konumunu otomatik al.

        GLOBAL_POSITION_INT.alt AMSL (mm) → anten home irtifası AMSL olarak saklanır;
        compute_aim'in beklediği referans budur."""
        try:
            msg = self._conn.recv_match(type="GLOBAL_POSITION_INT", blocking=False)
            if msg and msg.lat != 0:
                self._home = {"lat": msg.lat / 1e7, "lon": msg.lon / 1e7,
                              "alt": msg.alt / 1000.0}   # AMSL (mm→m)
                self._home_locked = True
                logger.info("Anten home Pixhawk GPS'inden alındı: %.6f, %.6f, %.1f m AMSL",
                            self._home["lat"], self._home["lon"], self._home["alt"])
        except Exception:
            pass

    def _track_once(self) -> None:
        from pymavlink import mavutil
        d = self._drone
        h = self._home

        # NEDEN: Referans uyuşmazlığı koruması. İHA AMSL irtifası bilinmiyorsa
        # (HOME_POSITION henüz gelmediyse) göreli irtifayı AMSL anten irtifasından
        # çıkarmak anteni yere baktırır → takibi başlatma, kullanıcıyı uyar.
        drone_amsl = d.get("alt_amsl")
        if drone_amsl is None:
            if self._should_warn("irtifa_referansi"):
                self.error_occurred.emit(
                    "Anten takip bekliyor: İHA irtifa referansı belirsiz "
                    "(HOME_POSITION/home_alt gelmedi). Göreli irtifa AMSL anten "
                    "irtifasıyla karşılaştırılamaz — takip duraklatıldı."
                )
            self.tracker_status.emit({
                "azimuth": 0.0, "elevation": 0.0,
                "ground_distance_m": 0.0, "slant_distance_m": 0.0,
                "alt_diff_m": 0.0, "tracking": False,
                "error": "irtifa referansı yok (home_alt)",
            })
            return

        # Her iki irtifa da AMSL — compute_aim sözleşmesi (bkz. tracker_math.py)
        aim = tm.compute_aim(h["lat"], h["lon"], h["alt"],
                             d["lat"], d["lon"], float(drone_amsl))

        tracking = aim["ground_distance_m"] >= self._min_dist
        status = dict(aim); status["tracking"] = tracking
        self.tracker_status.emit(status)
        if not tracking:
            return   # çok yakın — anteni oynatma (gürültü)

        # ── Step motor direği ────────────────────────────────────────────
        if self._drive == "stepper":
            self._drive_stepper(aim, status)
            return

        ts = self._conn.target_system or 1
        tc = self._conn.target_component or 1

        if self._mode == "forward":
            # İHA konumunu anten Pixhawk'ına ilet — AntennaTracker firmware hesaplar
            # NEDEN: GLOBAL_POSITION_INT'in 4. alanı `alt` = AMSL (mm), 5. alanı
            # `relative_alt` = yere/home'a göre (mm). Önceden her iki alana da göreli
            # irtifa yazılıyordu; AntennaTracker firmware AMSL alanını kullandığı için
            # elevasyon hesabı bozuluyordu. Artık doğru alanlara doğru referans.
            self._conn.mav.global_position_int_send(
                int(time.time() * 1000) & 0xFFFFFFFF,
                int(d["lat"] * 1e7), int(d["lon"] * 1e7),
                int(float(drone_amsl) * 1000),      # alt: AMSL (mm)
                int(float(d.get("rel_alt", 0.0)) * 1000),  # relative_alt (mm)
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

    def _drive_stepper(self, aim: dict, status: dict) -> None:
        """Hesaplanan azimut/elevasyonu step motor direğine uygular.

        İki eksen de burada sürülür: azimut yatay yönü, elevasyon ise
        irtifa farkından gelen yukarı açıyı takip eder (compute_aim
        ikisini de AMSL irtifalardan üretir).
        """
        if self._mount is None:
            return
        if self._az_offset is None:
            if self._should_warn("referans_yok"):
                self.error_occurred.emit(
                    "Anten takip bekliyor: direk REFERANSLANMADI. Anteni elle "
                    "mekanik dayanağa itip 'Limitte Referans Al' düğmesine basın."
                )
            status["error"] = "referans yok"
            status["tracking"] = False
            self.tracker_status.emit(status)
            return

        az_axis_wanted = aim["azimuth"] - self._az_offset
        az_cfg = self._mount.az.cfg
        target_axis = tm.wrap_safe_target(self._mount.az.angle, az_axis_wanted,
                                          az_cfg.min_deg, az_cfg.max_deg)
        if target_axis is None:
            # Hedef, mekanik limitlerin kapsamadığı bir yönde (kör açı).
            if self._should_warn("kor_aci"):
                self.error_occurred.emit(
                    f"İHA anten direğinin KÖR AÇISINDA (pusula {aim['azimuth']:.0f}°). "
                    f"Eksen {az_cfg.min_deg:.0f}..{az_cfg.max_deg:.0f}° arasında "
                    "çalışıyor — direği yeniden konumlandırmak gerekebilir."
                )
            status["error"] = "kör açı"
            status["tracking"] = False
            self.tracker_status.emit(status)
            return

        # NEDEN burada enerjilendirme: reference_at_limit motorları BİLEREK
        # enerjisiz bırakır (operatör anteni elle dayanağa iterken motor
        # tutmasın). Takip başladığında kimse geri açmazsa direk hedefi alır
        # ama HİÇ KIPIRDAMAZ ve sebebini de söylemez — sahada teşhisi zor bir
        # arıza. Takip komutu verilmeden önce enerji garanti altına alınır.
        self._mount.enable(True)
        az_cmd, el_cmd = self._mount.aim(target_axis, aim["elevation"])
        st = self._mount.status()
        status.update({
            "az_eksen_hedef": round(az_cmd, 2),
            "az_eksen_anlik": round(st["az"]["aci"], 2),
            "el_eksen_hedef": round(el_cmd, 2),
            "el_eksen_anlik": round(st["el"]["aci"], 2),
            "az_ofset": round(self._az_offset, 2),
            "az_limitte": st["az"]["limitte"],
            "el_limitte": st["el"]["limitte"],
            "surus": "stepper",
        })
        self.tracker_status.emit(status)

    def stop(self) -> None:
        self.requestInterruption()
        self.wait(3000)
        if self._mount is not None:
            try:
                self._mount.stop()
            except Exception:
                logger.exception("Step motor direği durdurulamadı")
