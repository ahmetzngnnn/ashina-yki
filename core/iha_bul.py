"""
iha_bul.py — İHA'yı ADRESİNİ BİLMEDEN bulur.

NEDEN (2026-09-03, sahada yaşandı):
Aracın IP'si tek bir gün içinde iki kez değişti — dahili WiFi 192.168.1.109
iken AC1200 dongle birincil yapılınca 192.168.1.229 oldu. Sahada DHCP kirası
her açılışta farklı gelebilir. Operatörün YKİ ayarlarına girip IP yazması
hem hata kaynağı hem de şartnamenin 8 dakikalık "uçuşa hazırlık" saatinden
(satır 1011) yiyen bir iş.

ÇÖZÜM: adres yerine İSİM. Araçta avahi-daemon çalışıyor ve hostname
`ashina-iha` yapıldı → `ashina-iha.local` hangi arayüzden/hangi kiradan
bağlanırsa bağlansın çözülür. Bu modül önce ismi dener, olmazsa bilinen
adreslere ve son çare alt ağ taramasına düşer.

TASARIM KURALI: hiçbir yol BLOKLAMAZ. Her deneme kısa zaman aşımlı;
çağıran arayüz donmaz (Qt ana ipliğinden çağrılabilir olmalı).
"""
from __future__ import annotations

import ipaddress
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional

# Aracın mDNS adı — hostname `ashina-iha`, avahi bunu .local altında yayınlar.
MDNS_AD = "ashina-iha.local"

# Eski/bilinen adresler. SIRA ÖNEMLİ: en son çalışan başa.
# .229 = AC1200 dongle (birincil, 2026-09-03'ten beri)
# .109 = Pi 5 dahili WiFi (yedek; dongle profili kapatılırsa)
BILINEN_ADRESLER = ("192.168.1.229", "192.168.1.109")

# Durum sunucusu portu — canlılık kanıtı olarak bunu kullanıyoruz çünkü
# ping ICMP'ye bağlı ve router'lar sınırlayabiliyor (ölçüldü: router ICMP'yi
# kısıtlarken TCP sorunsuzdu).
VARSAYILAN_PORT = 5602


def sina(host: str, port: int = VARSAYILAN_PORT, zaman_asimi: float = 1.2) -> bool:
    """Adres GERÇEKTEN cevap veriyor mu — TCP el sıkışmasıyla.

    NEDEN ping değil: router ICMP'yi kısıtlayabiliyor. TCP el sıkışması
    "servis ayakta" sorusunun doğrudan cevabı.
    """
    if not host:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=zaman_asimi):
            return True
    except Exception:
        return False


def _mdns_coz(ad: str = MDNS_AD, zaman_asimi: float = 2.0) -> Optional[str]:
    """mDNS adını IP'ye çevir. Çözülemezse None."""
    try:
        socket.setdefaulttimeout(zaman_asimi)
        return socket.gethostbyname(ad)
    except Exception:
        return None
    finally:
        socket.setdefaulttimeout(None)


def _yerel_alt_aglar() -> list:
    """Bu makinenin bağlı olduğu /24 alt ağları döndür (tarama için)."""
    aglar = []
    try:
        cikti = subprocess.run(["ip", "-4", "-brief", "addr", "show", "scope", "global"],
                               capture_output=True, text=True, timeout=3).stdout
        for satir in cikti.splitlines():
            for parca in satir.split():
                if "/" in parca:
                    try:
                        ag = ipaddress.ip_interface(parca).network
                        if ag.num_addresses <= 256:
                            aglar.append(ag)
                    except ValueError:
                        pass
    except Exception:
        pass
    return aglar


def tara(port: int = VARSAYILAN_PORT, zaman_asimi: float = 0.35,
         azami_konak: int = 254) -> list:
    """Son çare: yerel /24'lerde durum portunu dinleyen konakları ara.

    NEDEN paralel: 254 adresi seri denemek dakikalar sürer. 64 iş parçacığıyla
    tipik bir /24 birkaç saniyede taranıyor.
    """
    adaylar = []
    for ag in _yerel_alt_aglar():
        adaylar.extend(str(a) for a in ag.hosts())
    adaylar = adaylar[:azami_konak * 4]
    if not adaylar:
        return []
    bulunan = []
    with ThreadPoolExecutor(max_workers=64) as havuz:
        isler = {havuz.submit(sina, a, port, zaman_asimi): a for a in adaylar}
        for i in as_completed(isler):
            try:
                if i.result():
                    bulunan.append(isler[i])
            except Exception:
                pass
    return sorted(bulunan)


def adaylar(mevcut: str = "") -> list:
    """Denenecek adresleri ÖNCELİK SIRASIYLA döndür (ağ trafiği ÜRETMEZ)."""
    sira = []
    for a in (mevcut, MDNS_AD, *BILINEN_ADRESLER, "127.0.0.1"):
        a = (a or "").strip()
        if a and a not in sira:
            sira.append(a)
    return sira


def bul(mevcut: str = "", port: int = VARSAYILAN_PORT,
        tarama_yap: bool = True) -> tuple:
    """İHA'yı bul. Dönüş: (adres|None, aciklama).

    Sıra: mevcut ayar → mDNS adı → bilinen adresler → localhost → alt ağ taraması.
    """
    for a in adaylar(mevcut):
        if sina(a, port):
            if a == MDNS_AD:
                ip = _mdns_coz() or "?"
                return a, f"mDNS ile bulundu ({ip}) — adres değişse de çalışır"
            return a, f"{a} cevap verdi"

    if not tarama_yap:
        return None, "Bilinen adreslerin hiçbiri cevap vermedi"

    bulunanlar = tara(port)
    if len(bulunanlar) == 1:
        return bulunanlar[0], f"Alt ağ taramasıyla bulundu: {bulunanlar[0]}"
    if len(bulunanlar) > 1:
        return bulunanlar[0], (f"Alt ağda {len(bulunanlar)} aday var "
                               f"({', '.join(bulunanlar[:4])}) — ilki seçildi, DOĞRULAYIN")
    return None, ("Araç bulunamadı. Kontrol: araç açık mı, aynı ağda mı "
                  "(ASH-001), ashina-iha servisi çalışıyor mu")
