#!/usr/bin/env python3
"""
tools/rfd_config.py — RFD900A (SiK firmware) telemetri radyosunu AT komut
moduyla okur/yapılandırır.

NEDEN: TEKNOFEST sahasında aynı 900 MHz ISM bandında birden fazla takımın
RFD900 telsizi aynı anda çalışır. Fabrika NETID (varsayılan genelde 25)
ve varsayılan frekans atlama deseni PAYLAŞILIYORSA çapraz-takım paketleri
birbirine karışabilir/çakışabilir. Bu araç:
  1. Mevcut EEPROM parametrelerini okur (ATI5),
  2. Kendi benzersiz NETID'imizi + en güvenilir/kesintisiz bağlantı için
     önerilen parametreleri GÖSTERİR (yazmadan önce),
  3. Onay/--apply verilirse EEPROM'a yazar (ATS../AT&W/ATZ).

Her iki uçtaki (istasyon + İHA) radyo AYNI NETID + AIR_SPEED + NUM_CHANNELS/
MIN_FREQ/MAX_FREQ + ECC değerlerine sahip olmalı — aksi halde eşleşemezler.

Kullanım:
    .venv/bin/python3 tools/rfd_config.py --port /dev/ttyUSB0 --baud 115200 --read
    .venv/bin/python3 tools/rfd_config.py --port /dev/ttyUSB0 --baud 115200 --apply --netid 4077
"""
from __future__ import annotations

import argparse
import sys
import time

DEFAULT_PORT = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"

# ATIx komutlarıyla RFD900/SiK EEPROM parametre eşlemesi (S-register no → isim).
# ATI5 çıktısı zaten "S<n>: <isim>=<deger>" biçiminde adları döker; burası
# yalnızca yazarken hangi ATS<n> komutunu kullanacağımızı bilmek için.
S_REG = {
    "FORMAT": 0, "SERIAL_SPEED": 1, "AIR_SPEED": 2, "NETID": 3,
    "TXPOWER": 4, "ECC": 5, "MAVLINK": 6, "OPPRESEND": 7,
    "MIN_FREQ": 8, "MAX_FREQ": 9, "NUM_CHANNELS": 10, "DUTY_CYCLE": 11,
    "LBT_RSSI": 12, "MANCHESTER": 13, "RTSCTS": 14, "MAX_WINDOW": 15,
}


def try_at_sequence(ser) -> bool:
    """Portu AÇMADAN/kapatmadan (DTR'yi tekrar tetiklemeden) bir +++ denemesi."""
    # Önce zaten AT komut modunda mı diye düz "AT" dene (önceki bir oturum
    # ATO ile çıkmadan kapanmışsa radyo hâlâ komut modunda kalmış olabilir).
    ser.reset_input_buffer()
    ser.write(b"AT\r\n")
    time.sleep(0.3)
    if b"OK" in ser.read(64):
        return True

    ser.reset_input_buffer()
    time.sleep(1.1)          # guard time (sessizlik) — +++'tan ÖNCE
    # NEDEN tek tek + karakteri: RFD900'ün YENİ firmware sürümleri "+++"
    # dizisindeki her '+' arasında ~50ms boşluk bekliyor — üç karakteri tek
    # write() ile göndermek (UART'ta mikrosaniyeler içinde art arda çıkar)
    # veri sanılıp reddediliyor, "OK" hiç gelmiyor (RFDesign belgeleri).
    ser.write(b"+")
    time.sleep(0.05)
    ser.write(b"+")
    time.sleep(0.05)
    ser.write(b"+")
    time.sleep(1.1)          # guard time — +++'tan SONRA, başka hiçbir şey yazma
    return b"OK" in ser.read(64)


def at_cmd(ser, cmd: str) -> str:
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(0.3)
    return ser.read(4096).decode(errors="replace")


def try_connect(port: str, bauds: list[int]):
    import serial
    for b in bauds:
        print(f"[{port}] port açılıyor (baud={b})...")
        ser = serial.Serial(port, b, timeout=2.0)
        # NEDEN: port her open()'da DTR hattı tetiklenebilir — bazı USB-seri
        # köprülerinde bu radyo/MCU'yu RESETLER. Portu TEK SEFER açıp aynı
        # bağlantı üzerinde birkaç kez +++ denemek, her denemede yeniden
        # açıp radyoyu tekrar tekrar resetlemekten (ve hiç toparlanma
        # fırsatı bulamamaktan) kaçınır. İlk açılıştan sonra reset olası
        # ihtimaline karşı 2.5 sn toparlanma payı bırakılır.
        time.sleep(2.5)
        for attempt in range(1, 6):
            print(f"  AT dizisi deneniyor (deneme {attempt}/5)...")
            if try_at_sequence(ser):
                print(f"  → bağlandı (baud={b})")
                return ser, b
            print(f"  → yanıt yok (OK gelmedi)")
            time.sleep(0.5)
        ser.close()
    return None, None


def parse_ati5(text: str) -> dict:
    vals = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        # örnek satır: "S3: NETID=25"
        try:
            name_val = line.split(":", 1)[1] if ":" in line else line
            name, val = name_val.split("=", 1)
            vals[name.strip().upper()] = val.strip()
        except ValueError:
            continue
    return vals


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--baud", type=int, default=None,
                     help="Belirtilmezse 115200 ve 57600 sırayla denenir")
    ap.add_argument("--netid", type=int, default=None,
                     help="Uygulanacak benzersiz NETID (0-65535). Vermezseniz önerilmez/yazılmaz.")
    ap.add_argument("--apply", action="store_true",
                     help="Önerilen değerleri GERÇEKTEN EEPROM'a yaz (yoksa yalnız okur+önerir)")
    args = ap.parse_args()

    bauds = [args.baud] if args.baud else [115200, 57600]
    ser, used_baud = try_connect(args.port, bauds)
    if ser is None:
        print("HATA: Radyo AT komut moduna girmedi (hiçbir baud'da). "
              "Kablo/port/güç kontrol edin.")
        return 1

    print("\n--- ATI (firmware) ---")
    print(at_cmd(ser, "ATI").strip())

    print("\n--- ATI5 (mevcut EEPROM parametreleri) ---")
    ati5_raw = at_cmd(ser, "ATI5")
    print(ati5_raw.strip())
    current = parse_ati5(ati5_raw)

    print(f"\n=== ÖZET: MEVCUT DURUM (baud={used_baud}) ===")
    for name in S_REG:
        print(f"  {name:14s} = {current.get(name, '?')}")

    print("\n=== ÖNERİLEN DEĞERLER (en güvenilir/kesintisiz + çapraz-takım çakışmasına dirençli) ===")
    proposed = {
        "NETID": args.netid if args.netid is not None else "SİZİN SEÇECEĞİNİZ benzersiz bir sayı (ör. 4077) — HENÜZ BELİRLENMEDİ",
        "TXPOWER": "30 (dBm, RFD900A azami — mevzuat/bant izniniz varsa; menzil/güvenilirlik için artırılır)",
        "ECC": "1 (Golay hata düzeltme AÇIK — gürültülü/paraziti ortamda paket kaybını büyük ölçüde azaltır)",
        "MAVLINK": "1 (MAVLink çerçeveleme AÇIK — GCS uyumluluğu ve düşük gecikme için)",
        "LBT_RSSI": "1 (Listen-Before-Talk AÇIK — kanal meşgulse beklemesini sağlar, başka takımların "
                    "telsizleriyle nazik paylaşım/çakışma azaltma)",
        "NUM_CHANNELS": "50 (varsayılan aralık genellikle zaten böyle — geniş frekans atlama, tek "
                        "kanal parazitine karşı dayanıklılık)",
        "AIR_SPEED": "64 (kbps — düşük hava hızı = daha uzun menzil + daha az bit hatası; MAVLink "
                     "telemetri bant genişliği ihtiyacı düşük olduğundan throughput kaybı önemsiz)",
        "DUTY_CYCLE": "100 (nokta-nokta bağlantıda kısıtlama gereksiz)",
    }
    for name, val in proposed.items():
        print(f"  {name:14s} → {val}")

    if not args.apply:
        print("\nBu bir ÖNERİ listesidir — hiçbir şey yazılmadı (--apply verilmedi).")
        ser.close()
        return 0

    if args.netid is None:
        print("\nHATA: --apply ile --netid birlikte verilmeli.")
        ser.close()
        return 1

    to_write = {"NETID": args.netid, "ECC": 1, "LBT_RSSI": 1}
    print(f"\n=== YAZILIYOR: {to_write} ===")
    for name, val in to_write.items():
        reg = S_REG[name]
        cmd = f"ATS{reg}={val}"
        resp = at_cmd(ser, cmd).strip()
        print(f"  {cmd}  → {resp!r}")

    print("  AT&W (EEPROM'a kaydet)")
    print("  →", at_cmd(ser, "AT&W").strip())
    print("  ATZ (radyoyu yeniden başlat, yeni ayarlar devreye girer)")
    ser.write(b"ATZ\r\n")
    time.sleep(1.5)

    ser.close()
    print("\n[doğrulama] radyo yeniden bağlanıyor...")
    time.sleep(1.0)
    ser2, b2 = try_connect(args.port, [args.baud] if args.baud else [115200, 57600])
    if ser2 is None:
        print("UYARI: yazma sonrası AT moduna tekrar girilemedi — radyo yeniden "
              "başlamış/normal veri moduna geçmiş olabilir, bu BEKLENEN durumdur.")
        return 0
    ati5_after = at_cmd(ser2, "ATI5")
    print(ati5_after.strip())
    after = parse_ati5(ati5_after)
    ok = all(str(after.get(k)) == str(v) for k, v in to_write.items())
    print(f"\n{'✓ Doğrulandı' if ok else '⚠ UYUŞMAZLIK'}: {after}")
    ser2.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
