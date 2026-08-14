"""
core/flight_narrator.py — Sesli Otopilot / Görev Anlatıcısı
============================================================

Telemetriyi okuyup görevi İNSAN DİLİYLE anlatan saf mantık katmanı. Qt'ye ve
sese bağımlılığı YOKTUR: `update(telem)` çağrılır, geriye anlatılacak olaylar
döner. Böylece kolayca test edilir ve arayüzden bağımsız çalışır.

NE ANLATIR
  • Uçuş kontrol kartının durumu ("uçuşa hazır", "GPS kilidi yok" ...)
  • Faz geçişleri (kalkış, tırmanma, seyir, askıda, alçalma, iniş)
  • Aracın yönelimi ("kuzeydoğuya 8 metre/saniye ile ilerliyor")
  • Görev olayları (hedef bulundu, yük bırakıldı, eve dönüş)
  • RÜZGÂR: askıdayken aracın eğiminden tahmin (aşağıya bakınız)
  • Kritik durumlar (batarya, EKF, GPS, bağlantı)

RÜZGÂR TAHMİNİ — NASIL
  Çok rotorlu araç konumunu koruyabilmek için rüzgâra doğru EĞİLİR; eğim
  açısı rüzgâr kuvvetiyle artar. Kuvvet dengesi:
        tan(eğim) = sürükleme / (kütle × yerçekimi)
        sürükleme = ½ × ρ × Cd × A × hız²
    ⇒   hız ≈ k × √(tan(eğim))          k = √(2·m·g / (ρ·Cd·A))
  `k` araç geometrisine bağlıdır (wind_coeff ile ayarlanır); bu yüzden
  metre/saniye değeri YAKLAŞIKTIR ve öyle söylenir. Eğim açısına dayalı
  derecelendirme (sakin/hafif/orta/kuvvetli) ise güvenilirdir.

  YÖN: Araç rüzgârın GELDİĞİ yöne eğilir. Gövde eksenindeki eğim, yaw ile
  yer eksenine döndürülür; sonuç meteorolojik kural gereği rüzgârın GELDİĞİ
  yön olarak bildirilir ("kuzeybatıdan").

  GEÇERLİLİK KAPISI (kritik): Eğim yalnızca araç KONUM KORURKEN rüzgârı
  gösterir. Araç hızlanıyor/ilerliyorsa eğim komutlanan ivmeden gelir ve
  rüzgâr sanılırsa tamamen yanlış olur. Bu yüzden tahmin sadece
  armed + yeterli irtifa + düşük yer hızı koşullarında yapılır.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field

# ── Yön adları (meteorolojik: rüzgârın GELDİĞİ yön) ─────────────────────────
_YONLER = ("kuzeyden", "kuzeydoğudan", "doğudan", "güneydoğudan",
           "güneyden", "güneybatıdan", "batıdan", "kuzeybatıdan")
_GIDIS  = ("kuzeye", "kuzeydoğuya", "doğuya", "güneydoğuya",
           "güneye", "güneybatıya", "batıya", "kuzeybatıya")


def yon_adi(bearing_deg: float, gidis: bool = False) -> str:
    """Açıyı 8 ana yöne çevirir. gidis=True → 'kuzeye', False → 'kuzeyden'."""
    i = int((bearing_deg % 360.0) / 45.0 + 0.5) % 8
    return (_GIDIS if gidis else _YONLER)[i]


# ── Rüzgâr ───────────────────────────────────────────────────────────────────
# Eğim açısına göre derecelendirme. Metre/saniye tahmini araç katsayısına
# bağlı olduğundan derecelendirme EĞİME dayanır — bu kalibrasyonsuz da doğrudur.
_RUZGAR_SINIF = (
    (1.5,  "sakin"),
    (4.0,  "hafif"),
    (8.0,  "orta"),
    (13.0, "kuvvetli"),
    (999.0, "çok kuvvetli"),
)


def ruzgar_sinifi(tilt_deg: float, onceki: str = "", marj: float = 0.8) -> str:
    """Eğimi rüzgâr sınıfına çevirir — SINIR SALINIMINA karşı histerezisli.

    NEDEN histerezis: eğim ortalaması sınırın (ör. 8,0°) iki yanında
    gezindiğinde sınıf "orta"↔"kuvvetli" diye zıplar ve sesli asistan aynı
    şeyi saniyede bir tekrar eder. Sınıf değişimi için sınırın `marj` kadar
    ÖTESİNE geçilmesi şartı konur; sınır bölgesinde önceki sınıf korunur.
    """
    def ham(t: float) -> str:
        for esik, ad in _RUZGAR_SINIF:
            if t < esik:
                return ad
        return "çok kuvvetli"

    yeni = ham(tilt_deg)
    if not onceki or yeni == onceki:
        return yeni
    # Sınıf değişecekse: yön ne olursa olsun marj kadar aşılmalı
    adlar = [ad for _e, ad in _RUZGAR_SINIF]
    try:
        i_yeni, i_onc = adlar.index(yeni), adlar.index(onceki)
    except ValueError:
        return yeni
    if i_yeni > i_onc:                      # yukarı çıkıyor
        esik = _RUZGAR_SINIF[i_onc][0]
        return yeni if tilt_deg >= esik + marj else onceki
    esik = _RUZGAR_SINIF[i_yeni][0]         # aşağı iniyor
    return yeni if tilt_deg <= esik - marj else onceki


def ruzgar_hizi_tahmini(tilt_deg: float, k: float = 25.0) -> float:
    """Eğimden yaklaşık rüzgâr hızı (m/s). k araç geometrisine bağlı katsayı."""
    t = math.tan(math.radians(max(0.0, min(45.0, tilt_deg))))
    return k * math.sqrt(max(0.0, t))


def egim_yer_ekseninde(roll_deg: float, pitch_deg: float, yaw_deg: float) -> tuple:
    """Gövde eğimini yer eksenine çevirir.

    Dönüş: (egim_buyuklugu_deg, egim_yonu_bearing_deg)
    Eğimin YÖNÜ, aracın yattığı yöndür = rüzgârın geldiği yön.

    Kural: pozitif pitch = burun YUKARI (ArduPilot), yani öne yatma = -pitch.
    Pozitif roll = sağ kanat aşağı = sağa yatma.
    """
    ileri = -math.radians(pitch_deg)      # +: öne yatık
    saga  = math.radians(roll_deg)        # +: sağa yatık
    psi   = math.radians(yaw_deg)
    # Gövde ileri yönü psi'ye, sağ yönü psi+90'a bakar
    kuzey = ileri * math.cos(psi) - saga * math.sin(psi)
    dogu  = ileri * math.sin(psi) + saga * math.cos(psi)
    buyukluk = math.degrees(math.hypot(kuzey, dogu))
    bearing = (math.degrees(math.atan2(dogu, kuzey)) + 360.0) % 360.0
    return buyukluk, bearing


@dataclass
class Olay:
    """Anlatılacak tek bir olay."""
    metin: str
    onem: str = "bilgi"          # "bilgi" | "uyari" | "kritik"
    seslendir: bool = True
    etiket: str = ""             # tekrar bastırma anahtarı
    zaman: float = field(default_factory=time.time)


class FlightNarrator:
    """Telemetri akışını olay/anlatı akışına çevirir.

    Kullanım:
        n = FlightNarrator()
        for olay in n.update(telem): ...
    """

    # Periyodik anlatım aralıkları (saniye)
    RUZGAR_PERIYOT   = 45.0
    YONELIM_PERIYOT  = 30.0
    # Rüzgâr tahmini geçerlilik kapısı
    MIN_IRTIFA_M     = 2.0
    MAX_YER_HIZI_MS  = 1.5

    def __init__(self, wind_coeff: float = 25.0) -> None:
        self._k = wind_coeff
        self._ilk = True
        self._onceki: dict = {}
        self._son_soylenen: dict[str, float] = {}
        self._egim_pencere: deque = deque(maxlen=40)   # ~4 sn @10Hz
        self._son_ruzgar = 0.0
        self._son_yonelim = 0.0
        self._son_ruzgar_sinif = ""
        self._faz = "bekliyor"

    # ── Yardımcılar ──────────────────────────────────────────────────────
    def _bir_kez(self, etiket: str, periyot: float = 0.0) -> bool:
        """Aynı etiketli anlatımı periyot boyunca tekrar etme."""
        now = time.time()
        if now - self._son_soylenen.get(etiket, -1e9) < periyot:
            return False
        self._son_soylenen[etiket] = now
        return True

    # ── Ana giriş ────────────────────────────────────────────────────────
    def update(self, t: dict) -> list:
        """Telemetri sözlüğünü işler; anlatılacak olayları döndürür."""
        olaylar: list = []
        onc = self._onceki

        armed = bool(t.get("armed", False))
        mod = str(t.get("flight_mode", "—"))
        irtifa = float(t.get("relative_alt", t.get("alt", 0.0)) or 0.0)
        yer_hizi = float(t.get("groundspeed", 0.0) or 0.0)
        tirmanis = float(t.get("climb", 0.0) or 0.0)
        yaw = float(t.get("yaw_deg", math.degrees(float(t.get("yaw", 0.0) or 0.0))))
        roll = float(t.get("roll_deg", math.degrees(float(t.get("roll", 0.0) or 0.0))))
        pitch = float(t.get("pitch_deg", math.degrees(float(t.get("pitch", 0.0) or 0.0))))
        bat = float(t.get("battery_remaining", 100.0) or 100.0)
        sats = int(t.get("gps_sats", 0) or 0)
        ekf = t.get("ekf_flags")

        # ── Uçuşa hazırlık durumu (ilk bağlantıda ve değiştikçe) ─────────
        if not armed:
            hazir, sebep = self._hazir_mi(sats, ekf, bat)
            if hazir and self._bir_kez("hazir", 120.0):
                olaylar.append(Olay("Uçuş kontrol kartı hazır, sistemler normal. Kalkışa hazır.",
                                    etiket="hazir"))
            elif not hazir and sebep and self._bir_kez(f"hazir_degil:{sebep}", 60.0):
                olaylar.append(Olay(f"Uçuşa hazır değil: {sebep}.", onem="uyari",
                                    etiket="hazir_degil"))

        # ── Arm / Disarm ─────────────────────────────────────────────────
        if armed != onc.get("armed") and not self._ilk:
            olaylar.append(Olay("Motorlar çalıştırıldı, araç arm edildi." if armed
                                else "Motorlar durduruldu, araç disarm edildi.",
                                onem="uyari" if armed else "bilgi", etiket="arm"))

        # ── Uçuş modu ────────────────────────────────────────────────────
        if mod != onc.get("flight_mode") and not self._ilk:
            olaylar.append(Olay(f"Uçuş modu {mod} olarak değişti.", etiket="mod"))

        # ── Faz tespiti ve anlatımı ──────────────────────────────────────
        faz = self._faz_bul(armed, irtifa, tirmanis, yer_hizi)
        if faz != self._faz:
            self._faz = faz
            metin = {
                "kalkis":   "Kalkış yapılıyor.",
                "tirmanma": f"Tırmanıyor, irtifa {irtifa:.0f} metre.",
                "seyir":    f"Seyir halinde, {yon_adi(yaw, gidis=True)} {yer_hizi:.0f} metre saniye.",
                "askida":   f"Askıda bekliyor, irtifa {irtifa:.0f} metre.",
                "alcalma":  f"Alçalıyor, irtifa {irtifa:.0f} metre.",
                "yerde":    "Araç yerde.",
            }.get(faz)
            if metin:
                olaylar.append(Olay(metin, etiket=f"faz:{faz}"))
            # Faz anonsu yönelimi zaten söyledi; periyodik yönelim cümlesi
            # hemen ardından tekrar etmesin (üst üste iki aynı bilgi).
            self._son_yonelim = time.time()

        # ── Yönelim (periyodik, seyirdeyken) ─────────────────────────────
        if faz == "seyir" and time.time() - self._son_yonelim > self.YONELIM_PERIYOT:
            self._son_yonelim = time.time()
            olaylar.append(Olay(
                f"{yon_adi(yaw, gidis=True)} ilerliyor, hız {yer_hizi:.0f} metre saniye, "
                f"irtifa {irtifa:.0f} metre.", etiket="yonelim", seslendir=True))

        # ── Rüzgâr ───────────────────────────────────────────────────────
        olaylar += self._ruzgar(armed, irtifa, yer_hizi, roll, pitch, yaw)

        # ── Kritik eşikler ───────────────────────────────────────────────
        olaylar += self._kritikler(bat, sats, ekf, onc)

        self._onceki = {"armed": armed, "flight_mode": mod, "battery_remaining": bat,
                        "gps_sats": sats, "ekf_flags": ekf}
        self._ilk = False
        return olaylar

    # ── Alt mantıklar ────────────────────────────────────────────────────
    def _hazir_mi(self, sats: int, ekf, bat: float) -> tuple:
        if sats < 6:
            return False, f"GPS kilidi yetersiz, {sats} uydu"
        if ekf is not None and (int(ekf) & 0x1F) != 0x1F:
            return False, "EKF henüz oturmadı"
        if bat < 30:
            return False, f"batarya düşük, yüzde {bat:.0f}"
        return True, ""

    def _faz_bul(self, armed: bool, irtifa: float, tirmanis: float, hiz: float) -> str:
        if not armed:
            return "yerde"
        if irtifa < 1.0:
            return "kalkis" if tirmanis > 0.3 else "yerde"
        if tirmanis > 0.8:
            return "tirmanma"
        if tirmanis < -0.8:
            return "alcalma"
        if hiz > 1.5:
            return "seyir"
        return "askida"

    def _ruzgar(self, armed, irtifa, yer_hizi, roll, pitch, yaw) -> list:
        """Askıdayken eğimden rüzgâr tahmini (bkz. modül başlığı)."""
        gecerli = (armed and irtifa >= self.MIN_IRTIFA_M
                   and yer_hizi <= self.MAX_YER_HIZI_MS)
        if not gecerli:
            self._egim_pencere.clear()
            return []
        buyukluk, bearing = egim_yer_ekseninde(roll, pitch, yaw)
        self._egim_pencere.append((buyukluk, bearing))
        if len(self._egim_pencere) < self._egim_pencere.maxlen:
            return []   # pencere dolmadan konuşma (anlık salınım rüzgâr değildir)

        # Ortalama: yön dairesel olduğu için vektörel ortalama alınır
        kx = sum(b * math.cos(math.radians(a)) for b, a in self._egim_pencere)
        ky = sum(b * math.sin(math.radians(a)) for b, a in self._egim_pencere)
        n = len(self._egim_pencere)
        ort_buyukluk = math.hypot(kx, ky) / n
        ort_yon = (math.degrees(math.atan2(ky, kx)) + 360.0) % 360.0
        sinif = ruzgar_sinifi(ort_buyukluk, self._son_ruzgar_sinif)

        simdi = time.time()
        sinif_degisti = sinif != self._son_ruzgar_sinif
        periyot_doldu = simdi - self._son_ruzgar > self.RUZGAR_PERIYOT
        if not (sinif_degisti or periyot_doldu):
            return []
        self._son_ruzgar = simdi
        self._son_ruzgar_sinif = sinif

        if sinif == "sakin":
            return [Olay("Rüzgâr sakin.", etiket="ruzgar")]
        hiz = ruzgar_hizi_tahmini(ort_buyukluk, self._k)
        onem = "uyari" if ort_buyukluk >= 13.0 else "bilgi"
        return [Olay(
            f"Rüzgâr {sinif}, {yon_adi(ort_yon)} esiyor. "
            f"Yaklaşık {hiz:.0f} metre saniye, araç {ort_buyukluk:.0f} derece yatıyor.",
            onem=onem, etiket="ruzgar")]

    def _kritikler(self, bat, sats, ekf, onc) -> list:
        out = []
        for esik, onem in ((20, "kritik"), (30, "uyari"), (50, "bilgi")):
            if bat <= esik < float(onc.get("battery_remaining", 101)):
                out.append(Olay(f"Batarya yüzde {bat:.0f}.", onem=onem,
                                etiket=f"bat{esik}"))
                break
        if sats < 6 <= int(onc.get("gps_sats", 99)) and self._bir_kez("gps_kayip", 30.0):
            out.append(Olay(f"GPS zayıfladı, {sats} uydu.", onem="uyari", etiket="gps"))
        if ekf is not None and onc.get("ekf_flags") is not None:
            iyi_now = (int(ekf) & 0x1F) == 0x1F
            iyi_onc = (int(onc["ekf_flags"]) & 0x1F) == 0x1F
            if iyi_onc and not iyi_now and self._bir_kez("ekf_bozuldu", 30.0):
                out.append(Olay("EKF bozuldu, konum tahmini güvenilmez.",
                                onem="kritik", etiket="ekf"))
        return out

    # ── Dışarıdan beslenen olaylar (görev akışı) ─────────────────────────
    def gorev_olayi(self, metin: str, onem: str = "bilgi") -> Olay:
        return Olay(metin, onem=onem, etiket="gorev")
