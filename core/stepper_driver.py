"""
core/stepper_driver.py — TB6600 + NEMA 17 step motor sürüş katmanı
==================================================================

Anten takip direğinin iki ekseni (azimut/pan ve elevasyon/tilt) NEMA 17 step
motorlarla, TB6600 sürücüler üzerinden döndürülür. Bu modül YKİ'nin koştuğu
Raspberry Pi 4'ün GPIO pinlerinden STEP/DIR/ENA sinyallerini üretir.

TASARIM KARARLARI
-----------------
1) **Bloklamayan set-point takipçisi.** Takip döngüsü ~5 Hz'de "hedef açı"
   yazar; her eksenin kendi thread'i sürekli o hedefe doğru adımlar. Böylece
   arayüz hiçbir zaman motor hareketini beklemez ve hedef hareket halindeyken
   güncellenebilir (klasik bloklayan `move_to` bir takip sisteminde yanlıştır:
   İHA hareket ederken anten eski hedefe gitmeyi bitirmeye çalışır).

2) **Arka uç soyutlaması.** `pigpio` (tercih edilen, DMA ile donanım
   zamanlamalı), `RPi.GPIO` (yedek, yazılım döngüsü) ve `sim` (donanımsız).
   Sim arka ucu sayesinde YKİ dizüstünde de sorunsuz çalışır; mekanik henüz
   yokken mantık geliştirilip test edilebilir.

   NEDEN pigpio: Python'un GPIO'yu döngüyle çevirmesi işletim sistemi yükü
   altında titrer (jitter) ve step motor **adım kaçırır**. Açık çevrimde
   kaçan adım kalıcı konum hatası demektir. pigpio darbe dizisini DMA ile
   üretir; Python'un o an ne yaptığı darbe genişliğini etkilemez.

3) **Yumuşak limitler zorunlu.** Anten koaksiyel kabloyla bağlı olduğundan
   sonsuz dönemez (bkz. tracker_math.wrap_safe_target). Limit dışına komut
   verilemez; hedef kırpılır ve durum bayrağı set edilir.

4) **Rampalı hız.** Step motor ani hız komutunda tork üretemez ve adım
   kaçırır. Hız her tikte `accel_dps2` ile sınırlanarak değiştirilir.

GEOMETRİ
--------
    adım/derece = motor_adım/tur × mikroadım × redüksiyon / 360

Örnek: 200 adım/tur NEMA 17, TB6600'de 1/8 mikroadım, 1:50 sonsuz dişli
        → 200 × 8 × 50 / 360 = 222.2 adım/derece

Mekanik HENÜZ YOK: `gear_ratio` ayarlanabilir bırakıldı; dişli takıldığında
yalnızca ayar dosyası değişir, kod değişmez.

TB6600 BAĞLANTI NOTU
--------------------
TB6600'ün PUL-/DIR-/ENA- uçları ortak GND'ye, PUL+/DIR+/ENA+ uçları Pi'nin
3.3 V GPIO pinlerine bağlanır (TB6600 girişleri 3.3 V ile tetiklenir; hat
uzunsa optokuplör/level-shifter önerilir). ENA genelde **aktif düşük**tür ve
boşta bırakılırsa motor sürekli enerjilidir.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# TB6600 veri sayfası: minimum darbe genişliği ~2.2 µs. Güvenli taban 5 µs.
_MIN_PULSE_US = 5
# DIR sinyali STEP kenarından önce kararlı olmalı (setup time).
_DIR_SETUP_S = 20e-6


# ── Arka uçlar ───────────────────────────────────────────────────────────────
class _Backend:
    """GPIO arka ucu arayüzü. `pulse` N adımlık darbe dizisini üretir."""
    name = "base"

    def setup(self, step_pin: int, dir_pin: int, ena_pin: int | None) -> None: ...
    def set_dir(self, dir_pin: int, level: int) -> None: ...
    def set_enable(self, ena_pin: int | None, on: bool, active_low: bool) -> None: ...
    def pulse(self, step_pin: int, count: int, period_us: int) -> None: ...
    def cleanup(self) -> None: ...


class SimBackend(_Backend):
    """Donanımsız arka uç — YKİ dizüstünde çalışırken kullanılır.

    Gerçek zamanı taklit eder (darbe süresi kadar uyur) ki üst katmandaki
    hız/rampa mantığı gerçekçi koşullarda test edilebilsin."""
    name = "sim"

    def __init__(self, realtime: bool = True) -> None:
        self.realtime = realtime
        self.total_steps = 0

    def setup(self, step_pin, dir_pin, ena_pin): pass
    def set_dir(self, dir_pin, level): pass
    def set_enable(self, ena_pin, on, active_low): pass

    def pulse(self, step_pin, count, period_us):
        self.total_steps += count
        if self.realtime:
            time.sleep(count * period_us / 1e6)

    def cleanup(self): pass


class PigpioBackend(_Backend):
    """pigpio + DMA dalga biçimi — Pi'de tercih edilen yol (titremesiz)."""
    name = "pigpio"
    _MAX_PULSES = 5000   # tek dalgaya sığdırılacak azami darbe (2 pulse/adım)

    def __init__(self) -> None:
        import pigpio  # noqa: F401  (yoksa ImportError → çağıran yedeğe düşer
        self._pigpio = pigpio
        self._pi = pigpio.pi()
        if not self._pi.connected:
            raise RuntimeError("pigpio daemon çalışmıyor (sudo systemctl start pigpiod)")

    def setup(self, step_pin, dir_pin, ena_pin):
        for p in (step_pin, dir_pin, ena_pin):
            if p is not None:
                self._pi.set_mode(p, self._pigpio.OUTPUT)
                self._pi.write(p, 0)

    def set_dir(self, dir_pin, level):
        self._pi.write(dir_pin, 1 if level else 0)
        time.sleep(_DIR_SETUP_S)

    def set_enable(self, ena_pin, on, active_low):
        if ena_pin is None:
            return
        self._pi.write(ena_pin, (0 if on else 1) if active_low else (1 if on else 0))

    def pulse(self, step_pin, count, period_us):
        if count <= 0:
            return
        high = max(_MIN_PULSE_US, period_us // 2)
        low = max(_MIN_PULSE_US, period_us - high)
        mask = 1 << step_pin
        while count > 0:
            n = min(count, self._MAX_PULSES)
            self._pi.wave_clear()
            train = []
            for _ in range(n):
                train.append(self._pigpio.pulse(mask, 0, high))
                train.append(self._pigpio.pulse(0, mask, low))
            self._pi.wave_add_generic(train)
            wid = self._pi.wave_create()
            self._pi.wave_send_once(wid)
            while self._pi.wave_tx_busy():
                time.sleep(0.0005)
            self._pi.wave_delete(wid)
            count -= n

    def cleanup(self):
        try:
            self._pi.wave_clear()
            self._pi.stop()
        except Exception:
            pass


class RpiGpioBackend(_Backend):
    """RPi.GPIO yazılım döngüsü — pigpio yoksa yedek. Yük altında titrer."""
    name = "rpigpio"

    def __init__(self) -> None:
        import RPi.GPIO as GPIO  # noqa: N814
        self._g = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

    def setup(self, step_pin, dir_pin, ena_pin):
        for p in (step_pin, dir_pin, ena_pin):
            if p is not None:
                self._g.setup(p, self._g.OUT, initial=self._g.LOW)

    def set_dir(self, dir_pin, level):
        self._g.output(dir_pin, self._g.HIGH if level else self._g.LOW)
        time.sleep(_DIR_SETUP_S)

    def set_enable(self, ena_pin, on, active_low):
        if ena_pin is None:
            return
        lvl = (self._g.LOW if on else self._g.HIGH) if active_low else \
              (self._g.HIGH if on else self._g.LOW)
        self._g.output(ena_pin, lvl)

    def pulse(self, step_pin, count, period_us):
        half = period_us / 2e6
        g, hi, lo = self._g, self._g.HIGH, self._g.LOW
        for _ in range(count):
            g.output(step_pin, hi); time.sleep(half)
            g.output(step_pin, lo); time.sleep(half)

    def cleanup(self):
        try:
            self._g.cleanup()
        except Exception:
            pass


def make_backend(prefer: str = "auto") -> _Backend:
    """İstenen arka ucu kurar; başarısızsa sırayla yedeğe düşer.

    'auto' → pigpio → RPi.GPIO → sim. Donanımsız makinede sessizce sim'e
    düşmek DOĞRU davranıştır: YKİ dizüstünde de açılmalıdır. Ancak hangi
    arka ucun seçildiği AÇIKÇA loglanır ki sahada 'motor dönmüyor' derken
    sim'de olduğu fark edilsin."""
    order = {"auto": ("pigpio", "rpigpio", "sim"),
             "pigpio": ("pigpio", "sim"),
             "rpigpio": ("rpigpio", "sim"),
             "sim": ("sim",)}.get(prefer, ("pigpio", "rpigpio", "sim"))
    for name in order:
        try:
            if name == "pigpio":
                b = PigpioBackend()
            elif name == "rpigpio":
                b = RpiGpioBackend()
            else:
                b = SimBackend()
            logger.info("Step motor arka ucu: %s", b.name.upper())
            return b
        except Exception as exc:
            logger.info("Step arka ucu '%s' kullanılamadı (%s) — sıradaki deneniyor", name, exc)
    return SimBackend()


# ── Eksen ────────────────────────────────────────────────────────────────────
@dataclass
class AxisConfig:
    """Tek eksenin donanım ve hareket tanımı."""
    name: str = "az"
    step_pin: int = 0
    dir_pin: int = 0
    ena_pin: int | None = None
    ena_active_low: bool = True
    invert_dir: bool = False

    motor_steps_per_rev: int = 200      # NEMA 17 = 1.8°/adım
    microstep: int = 8                  # TB6600 DIP anahtarları
    gear_ratio: float = 1.0             # mekanik redüksiyon (sonsuz dişli vb.)

    min_deg: float = -180.0             # yumuşak limit (kablo sarmasını önler)
    max_deg: float = 180.0
    max_speed_dps: float = 20.0         # azami hız (derece/sn, çıkış milinde)
    accel_dps2: float = 40.0            # ivme (derece/sn²)
    backlash_deg: float = 0.0           # bilgi amaçlı (şu an telafi edilmiyor)

    def steps_per_degree(self) -> float:
        return (self.motor_steps_per_rev * self.microstep * self.gear_ratio) / 360.0


class StepperAxis:
    """Bir ekseni hedef açıya sürekli süren, bloklamayan takipçi.

    Kullanım:
        ax = StepperAxis(cfg, backend); ax.start()
        ax.set_target(123.4)      # istediğin kadar sık çağır
        ax.angle                  # anlık açı (adım sayımından)
        ax.stop()
    """

    def __init__(self, cfg: AxisConfig, backend: _Backend,
                 tick_hz: float = 50.0) -> None:
        self.cfg = cfg
        self._b = backend
        self._spd = cfg.steps_per_degree()
        if self._spd <= 0:
            raise ValueError(f"{cfg.name}: adım/derece hesabı sıfır veya negatif")
        self._tick = 1.0 / max(5.0, tick_hz)

        self._lock = threading.Lock()
        self._pos_steps = 0            # referans alınmış konum (adım)
        self._target_steps = 0
        self._speed_dps = 0.0          # anlık hız (rampa için)
        self._enabled = False
        self._limited = False          # son hedef limitle kırpıldı mı
        self._run = False
        self._thr: threading.Thread | None = None

        self._b.setup(cfg.step_pin, cfg.dir_pin, cfg.ena_pin)
        self.set_enabled(False)

    # ── Durum ────────────────────────────────────────────────────────────
    @property
    def angle(self) -> float:
        with self._lock:
            return self._pos_steps / self._spd

    @property
    def target_angle(self) -> float:
        with self._lock:
            return self._target_steps / self._spd

    @property
    def at_limit(self) -> bool:
        with self._lock:
            return self._limited

    @property
    def moving(self) -> bool:
        with self._lock:
            return self._pos_steps != self._target_steps

    def status(self) -> dict:
        with self._lock:
            return {
                "eksen": self.cfg.name,
                "aci": round(self._pos_steps / self._spd, 3),
                "hedef": round(self._target_steps / self._spd, 3),
                "hiz_dps": round(self._speed_dps, 2),
                "enerjili": self._enabled,
                "limitte": self._limited,
                "adim_derece": round(self._spd, 3),
                "arka_uc": self._b.name,
            }

    # ── Komut ────────────────────────────────────────────────────────────
    def set_enabled(self, on: bool) -> None:
        """Motor sürücüsünü enerjilendirir/keser.

        Enerji kesilince sonsuz dişli yoksa anten rüzgârda savrulabilir —
        bu yüzden takip aktifken enerji KESİLMEZ."""
        self._b.set_enable(self.cfg.ena_pin, on, self.cfg.ena_active_low)
        with self._lock:
            self._enabled = on

    def set_target(self, deg: float) -> float:
        """Hedef açıyı yazar (limitlere kırpılır). Kırpılmış hedefi döndürür."""
        lo, hi = self.cfg.min_deg, self.cfg.max_deg
        clamped = max(lo, min(hi, deg))
        with self._lock:
            self._limited = abs(clamped - deg) > 1e-6
            self._target_steps = int(round(clamped * self._spd))
        return clamped

    @property
    def raw_steps(self) -> int:
        """Ham adım sayacı — kalibrasyon ölçümü için (iki limit arası)."""
        with self._lock:
            return self._pos_steps

    def set_gear_ratio(self, ratio: float) -> float:
        """Redüksiyon oranını çalışma anında değiştirir; AÇIYI korur.

        Saha kurulumunda dişli takıldığında operatör oranı arayüzden girer.
        Adım sayacı doğrudan bırakılırsa açı değişmiş görünürdü (aynı adım,
        farklı adım/derece); bu yüzden konum, YENİ ölçeğe göre yeniden
        hesaplanır — anten fiziksel olarak nerede duruyorsa açısı odur.

        Dönüş: yeni adım/derece.
        """
        if ratio <= 0:
            raise ValueError("Redüksiyon oranı pozitif olmalı")
        with self._lock:
            angle_now = self._pos_steps / self._spd
            target_now = self._target_steps / self._spd
            self.cfg.gear_ratio = float(ratio)
            self._spd = self.cfg.steps_per_degree()
            self._pos_steps = int(round(angle_now * self._spd))
            self._target_steps = int(round(target_now * self._spd))
            return self._spd

    def steps_per_degree(self) -> float:
        with self._lock:
            return self._spd

    def set_current_angle(self, deg: float) -> None:
        """Referanslama: 'şu an bu açıdasın' der (uçuş kontrol kartının
        manyetometresinden gelen gerçek yönle senkronlamak için).

        Hedefi de aynı yere çeker ki eksen referanslama anında fırlamasın."""
        with self._lock:
            self._pos_steps = int(round(deg * self._spd))
            self._target_steps = self._pos_steps
            self._speed_dps = 0.0

    def jog(self, delta_deg: float) -> float:
        """Elle döndürme: hedefi mevcut AÇIDAN itibaren delta kadar öteler.

        Hedefe göre değil ANLIK AÇIYA göre ötelenir; art arda basılan jog
        düğmeleri, motor henüz varmadan hedefi biriktirip anteni limite
        fırlatmasın diye."""
        with self._lock:
            base = self._pos_steps / self._spd
        return self.set_target(base + delta_deg)

    def calibrate_from_span(self, steps_measured: int, span_deg: float) -> dict:
        """İki mekanik dayanak arasında sayılan adımdan redüksiyonu türetir.

        Azimutta iki katı dayanak arası açıklık BİLİNİYOR (270°). Operatör bir
        limitten diğerine MOTORLA giderse (jog ile), sürülen adım sayısı
        gerçek adım/derece oranını verir:

            adım/derece = |ölçülen adım| / açıklık
            redüksiyon  = adım/derece × 360 / (adım/tur × mikroadım)

        Böylece dişli oranı elle hesaplanmak zorunda kalmaz; sistem kendi
        parametresini günceller. Dişli kayması/kaçan adım varsa da burada
        ortaya çıkar (beklenenden sapma raporlanır).

        UYARI: Ölçüm yalnızca hareket MOTORLA yapıldıysa geçerlidir. Anten
        elle çevrildiyse adım sayacı ilerlemez — bu durumda ölçüm anlamsızdır
        ve fonksiyon uyarı döndürür.
        """
        span = abs(float(span_deg))
        n = abs(int(steps_measured))
        if span < 1e-6:
            return {"ok": False, "mesaj": "Açıklık sıfır olamaz."}
        if n == 0:
            return {"ok": False, "mesaj": ("Adım sayacı ilerlememiş — anten elle mi "
                                           "çevrildi? Kalibrasyon için iki limit arası "
                                           "MOTORLA (jog ile) sürülmelidir.")}
        spd_new = n / span
        denom = self.cfg.motor_steps_per_rev * self.cfg.microstep
        ratio_new = spd_new * 360.0 / denom
        ratio_old = self.cfg.gear_ratio
        self.set_gear_ratio(ratio_new)
        sapma = (ratio_new / ratio_old - 1.0) * 100.0 if ratio_old else float("nan")
        return {"ok": True, "adim": n, "aciklik": span,
                "adim_derece": round(spd_new, 3),
                "reduksiyon": round(ratio_new, 4),
                "onceki_reduksiyon": round(ratio_old, 4),
                "sapma_yuzde": round(sapma, 1),
                "mesaj": (f"{n} adım / {span:.1f}° → {spd_new:.2f} adım/derece, "
                          f"redüksiyon {ratio_new:.3f} (önceki {ratio_old:.3f}, "
                          f"sapma %{sapma:+.1f})")}

    def nudge_steps(self, steps: int) -> None:
        """Konum sayacını kaydırmadan hedefi adım cinsinden ötele — kapalı
        çevrim düzeltmesi için (kaçan adımların telafisi)."""
        with self._lock:
            self._target_steps += int(steps)

    # ── Takipçi thread ───────────────────────────────────────────────────
    def start(self) -> None:
        if self._thr and self._thr.is_alive():
            return
        self._run = True
        self._thr = threading.Thread(target=self._loop, name=f"stepper-{self.cfg.name}",
                                     daemon=True)
        self._thr.start()

    def stop(self, disable: bool = True) -> None:
        self._run = False
        t = self._thr
        if t and t.is_alive():
            t.join(timeout=2.0)
        if disable:
            self.set_enabled(False)

    def _loop(self) -> None:
        while self._run:
            t0 = time.monotonic()
            try:
                self._step_once()
            except Exception as exc:
                logger.exception("%s ekseni adım hatası: %s", self.cfg.name, exc)
                time.sleep(0.2)
            dt = time.monotonic() - t0
            if dt < self._tick:
                time.sleep(self._tick - dt)

    def _step_once(self) -> None:
        with self._lock:
            remaining = self._target_steps - self._pos_steps
            speed = self._speed_dps
            enabled = self._enabled
        if remaining == 0 or not enabled:
            with self._lock:
                self._speed_dps = 0.0
            return

        cfg = self._tick, self.cfg
        tick, c = cfg
        # Trapez hız profili: kalan mesafede durabilmek için gereken hız
        # sınırı (v² = 2·a·s) ile azami hızın küçüğü hedeflenir.
        remaining_deg = abs(remaining) / self._spd
        v_stop = math.sqrt(max(0.0, 2.0 * c.accel_dps2 * remaining_deg))
        v_target = min(c.max_speed_dps, v_stop)
        # Rampa: hızı bu tikte ivme kadar değiştir
        dv = c.accel_dps2 * tick
        speed = min(v_target, speed + dv) if speed < v_target else max(v_target, speed - dv)
        speed = max(0.0, speed)

        steps_this_tick = int(round(speed * self._spd * tick))
        if steps_this_tick <= 0:
            # Çok yavaş: en az bir adım at ki kalan mesafe kapansın
            steps_this_tick = 1 if remaining_deg * self._spd >= 1 else 0
        steps_this_tick = min(steps_this_tick, abs(remaining))
        if steps_this_tick <= 0:
            return

        direction = 1 if remaining > 0 else -1
        level = (direction > 0) != bool(c.invert_dir)
        self._b.set_dir(c.dir_pin, 1 if level else 0)

        period_us = max(2 * _MIN_PULSE_US,
                        int(1e6 / max(1.0, speed * self._spd)) if speed > 0 else 1000)
        self._b.pulse(c.step_pin, steps_this_tick, period_us)

        with self._lock:
            self._pos_steps += direction * steps_this_tick
            self._speed_dps = speed


@dataclass
class TrackerMount:
    """İki eksenli anten direği (azimut + elevasyon)."""
    az: StepperAxis
    el: StepperAxis
    backend: _Backend = field(repr=False, default=None)  # type: ignore[assignment]

    def start(self) -> None:
        self.az.start(); self.el.start()

    def enable(self, on: bool) -> None:
        self.az.set_enabled(on); self.el.set_enabled(on)

    def aim(self, az_deg: float, el_deg: float) -> tuple[float, float]:
        return self.az.set_target(az_deg), self.el.set_target(el_deg)

    def status(self) -> dict:
        return {"az": self.az.status(), "el": self.el.status()}

    def stop(self) -> None:
        self.az.stop(); self.el.stop()
        if self.backend is not None:
            self.backend.cleanup()


def build_mount(cfg: dict, prefer_backend: str = "auto") -> TrackerMount:
    """Ayar sözlüğünden iki eksenli direği kurar.

    cfg örneği (gcs_config anahtarlarıyla beslenir):
      {"az": {"step_pin":17,"dir_pin":27,"ena_pin":22,"gear_ratio":50,...},
       "el": {"step_pin":23,"dir_pin":24,"ena_pin":25,"gear_ratio":50,...}}
    """
    backend = make_backend(prefer_backend)
    axes = {}
    for name in ("az", "el"):
        raw = dict(cfg.get(name) or {})
        raw["name"] = name
        valid = {f for f in AxisConfig.__dataclass_fields__}
        unknown = set(raw) - valid
        if unknown:
            logger.warning("%s ekseninde bilinmeyen ayar yok sayıldı: %s", name, sorted(unknown))
        axes[name] = StepperAxis(AxisConfig(**{k: v for k, v in raw.items() if k in valid}),
                                 backend)
    return TrackerMount(az=axes["az"], el=axes["el"], backend=backend)
