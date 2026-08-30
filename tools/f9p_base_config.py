#!/usr/bin/env python3
"""
tools/f9p_base_config.py — u-blox F9P'yi RTK BAZ İSTASYONU (survey-in) moduna alır.

NE YAPAR
--------
F9P'ye UBX-CFG-VALSET (mesaj İNŞASI için pyubx2 kullanılır) göndererek:
  0. USB portunda UBX çıkışını açar (bkz. aşağıdaki NEDEN — bazı F9P
     kurulumlarında fabrika ayarı yalnızca NMEA çıkışına izin verir, ACK/
     NAV-SVIN gibi UBX yanıtları bu durumda hiç GÖRÜNMEZ).
  1. TMODE'u survey-in'e alır (CFG_TMODE_MODE=1) — F9P kendi konumunu
     `--svin-min-dur` saniye boyunca ölçüp `--svin-acc-limit` metre hedef
     doğruluğa ulaşınca o noktayı SABİT baz konumu olarak kilitler.
  2. Standart RTCM3 MSM4 gözlem + baz konumu mesajlarını USB portunda
     etkinleştirir (1005, 1077, 1087, 1097, 1127, 1230 — GPS/GLONASS/
     Galileo/BeiDou + baz konumu + GLONASS kod-faz önyargıları).
  3. Ayarları RAM+BBR+Flash katmanlarına yazar (layers=7) — güç kesintisi/
     yeniden başlatmadan SONRA da korunur.

NEDEN "SABİT KONUM" DEĞİL SURVEY-IN: RTK düzeltmesinin doğruluğu doğrudan
baz konumunun bilinme doğruluğuna bağlıdır. Yaklaşık/tek-GPS-fix'li bir
konum (ör. Pixhawk GPS'i, birkaç metre hatalı) "sabit" olarak girilirse bu
hata TÜM düzeltmelere sabit bir öteleme olarak yansır — rover hep aynı
yönde birkaç metre kayar, ama RTK FIXED gösterip "kesin doğru" SANDIRIR
(en tehlikeli hata türü: sessiz ve kendinden emin). Survey-in, F9P'nin
KENDİ ölçümüyle santimetre mertebesinde bir referans üretir; elde santimetre
hassasiyetinde bağımsız bir jeodezik ölçüm YOKSA tek doğru seçenek budur.

NEDEN YANITLAR HAM BAYT TARAMASIYLA OKUNUYOR (pyubx2.UBXReader DEĞİL):
UBXReader'ın sürekli akış ayrıştırıcısı bu portta (F9P'nin varsayılan
karışık NMEA çıktısı + henüz-açılmamış UBX/RTCM3 çıktısı üstüne) "Unknown
protocol header" / "invalid checksum" gürültüsü üretti — kaynağı port
paylaşımıydı (bu betik + ashina-yki.service AYNI ANDA porta bağlıydı, bkz.
"TEK SÜREÇ" uyarısı aşağıda), ama sorunu KESİN ayıklamak için (ve tek bir
10 baytlık ACK/NAK çerçevesini bulmak için 3 protokollü bir akış
ayrıştırıcıya hiç ihtiyaç olmadığından) bu betik kendi minimal, bağımsız
bayt-tarayıcısını kullanır (core/rtk_gps_worker.py'deki RTCM3 çerçeveleme
YAKLAŞIMIYLA aynı desen).

TEK SÜREÇ UYARISI: seri port aynı anda yalnızca BİR işlem tarafından
açılabilir. Bu betiği çalıştırmadan önce ashina-yki.service'i durdurun:
    sudo systemctl stop ashina-yki
    python3 tools/f9p_base_config.py --watch
    sudo systemctl start ashina-yki

KULLANIM
--------
    # Önce ne gönderileceğini gör (donanıma DOKUNMAZ):
    python3 tools/f9p_base_config.py --dry-run

    # Yapılandır ve hemen survey-in ilerlemesini izle:
    python3 tools/f9p_base_config.py --watch

    # Sadece mevcut survey-in durumunu izle (yeniden yapılandırma YAPMA):
    python3 tools/f9p_base_config.py --watch-only

    # Farklı port/eşik:
    python3 tools/f9p_base_config.py --port /dev/ttyACM0 --svin-min-dur 300 --svin-acc-limit 2.0

DOĞRULAMA: her UBX-CFG-VALSET gönderiminden sonra F9P'nin ACK-ACK/ACK-NAK
yanıtı BEKLENİR — "gönderildi" ile "kabul edildi" ayrı şeylerdir, sessiz
başarısızlık riskini kapatmak için bu script hiçbir zaman "muhtemelen oldu"
demez, ACK'i görmeden başarı raporlamaz.
"""
from __future__ import annotations

import argparse
import struct
import sys
import time

# pyubx2: F9P'nin UBX-CFG-VALSET/NAV-SVIN-POLL mesajlarını doğru anahtar
# id'leriyle İNŞA etmek için — bkz. requirements-linux.txt. Gelen yanıtları
# OKUMAK için KULLANILMIYOR (bkz. dosya başlığı NEDEN notu) — o taraf ham
# bayt taramasıyla, bu dosya içinde yapılır.
from pyubx2 import POLL, UBXMessage

DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_BAUD = 38400

UBX_SYNC = bytes([0xB5, 0x62])

# Standart RTCM3 MSM4 gözlem setleri + baz konumu (1005) + GLONASS kod-faz
# önyargıları (1230). Rover tarafı hangi takımyıldızları kullanıyorsa
# hepsi burada — fazlası zararsız (rover kullanmadığını yok sayar).
RTCM_MSGS = ("1005", "1077", "1087", "1097", "1127", "1230")


def build_usbprot_message() -> tuple["UBXMessage", list]:
    """USB portunda UBX (giriş+çıkış) etkinleştirir — ACK-ACK/NAV-SVIN gibi
    yanıtları görebilmek için ZORUNLU. NMEA/RTCM3X çıkışını da AÇIK bırakır
    (rtk_gps_worker.py'nin NMEA izlemesi ve RTCM3 aktarımı buna bağımlı —
    yalnızca UBX'i açıp diğerlerini KAPATMAK istemeden mevcut işlevi bozardı).
    """
    cfg_data = [
        ("CFG_USBOUTPROT_UBX", 1), ("CFG_USBOUTPROT_NMEA", 1), ("CFG_USBOUTPROT_RTCM3X", 1),
        ("CFG_USBINPROT_UBX", 1), ("CFG_USBINPROT_NMEA", 1),
    ]
    return UBXMessage.config_set(layers=7, transaction=0, cfgData=cfg_data), cfg_data


def build_config_message(svin_min_dur: int, svin_acc_limit_m: float) -> tuple["UBXMessage", list]:
    cfg_data = [
        ("CFG_TMODE_MODE", 1),   # 0=kapalı 1=survey-in 2=sabit konum
        ("CFG_TMODE_SVIN_MIN_DUR", int(svin_min_dur)),
        ("CFG_TMODE_SVIN_ACC_LIMIT", int(round(svin_acc_limit_m * 10000))),  # 0.1 mm birim
    ]
    for m in RTCM_MSGS:
        cfg_data.append((f"CFG_MSGOUT_RTCM_3X_TYPE{m}_USB", 1))
    # layers: bit0=RAM(1) bit1=BBR(2) bit2=Flash(4) → 7 = üçü de (kalıcı)
    return UBXMessage.config_set(layers=7, transaction=0, cfgData=cfg_data), cfg_data


# ── Ham bayt düzeyinde UBX yanıt tarayıcı (pyubx2.UBXReader KULLANILMAZ) ──────
def _read_ubx_frames(ser, buf: bytearray, max_frames: int = 20) -> list[tuple[int, int, bytes]]:
    """buf'a mevcut girişten okunabilen tüm baytları ekler, İÇİNDE bulduğu
    TAM UBX çerçevelerini (sync hariç: cls, id, payload) döndürür ve
    tükettiği kısmı buf'tan siler. NMEA/RTCM3 baytlarını (UBX sync'e denk
    gelene kadar) sessizce atlar — bu betiğin tek ilgisi UBX yanıtlarıdır."""
    chunk = ser.read(4096)
    if chunk:
        buf += chunk
    frames = []
    while len(frames) < max_frames:
        idx = buf.find(UBX_SYNC)
        if idx == -1:
            if len(buf) > 8192:
                del buf[:-2]   # senkron hiç bulunamadıysa tamponu küçült
            break
        del buf[:idx]   # sync'ten ÖNCEKİ (NMEA/RTCM3/gürültü) baytları at
        if len(buf) < 8:
            break   # sync var ama class/id/length henüz tam gelmedi
        cls_id, msg_id = buf[2], buf[3]
        length = struct.unpack_from("<H", buf, 4)[0]
        frame_len = 6 + length + 2   # sync(2)+cls(1)+id(1)+len(2) + payload + ck(2)
        if len(buf) < frame_len:
            break   # çerçeve tam değil, daha fazla veri bekleniyor
        payload = bytes(buf[6:6 + length])
        del buf[:frame_len]
        frames.append((cls_id, msg_id, payload))
    return frames


def _wait_ack(ser, buf: bytearray, expect_cls: int, expect_id: int,
             timeout: float = 3.0) -> bool | None:
    """expect_cls/expect_id için ACK-ACK(0x05,0x01)/ACK-NAK(0x05,0x00) bekler.
    Dönüş: True=kabul, False=RED, None=zaman aşımı (hiç UBX yanıtı gelmedi)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        for cls_id, msg_id, payload in _read_ubx_frames(ser, buf):
            if cls_id == 0x05 and len(payload) >= 2 and payload[0] == expect_cls and payload[1] == expect_id:
                return msg_id == 0x01   # 0x01=ACK-ACK, 0x00=ACK-NAK
        time.sleep(0.05)
    return None


def _send(ser, msg: "UBXMessage") -> None:
    ser.write(msg.serialize())
    ser.flush()


def configure_base(port: str, baud: int, svin_min_dur: int, svin_acc_limit_m: float,
                    dry_run: bool = False) -> bool:
    usbprot_msg, usbprot_data = build_usbprot_message()
    msg, cfg_data = build_config_message(svin_min_dur, svin_acc_limit_m)

    print("1. adım — USB port protokolleri (UBX yanıtlarını GÖREBİLMEK için):")
    for k, v in usbprot_data:
        print(f"  {k} = {v}")
    print("2. adım — asıl baz istasyonu ayarları:")
    for k, v in cfg_data:
        print(f"  {k} = {v}")
    print(f"UBX-CFG-VALSET #2 ({len(msg.serialize())} bayt): {msg.serialize().hex()}")

    if dry_run:
        print("(--dry-run: donanıma dokunulmadı, hiçbir şey gönderilmedi)")
        return True

    import serial   # yalnızca gerçek gönderimde gerekli — dry-run donanımsız çalışsın diye burada import

    print(f"\nF9P'ye bağlanılıyor: {port} @ {baud}")
    try:
        ser = serial.Serial(port, baud, timeout=0.2)
    except Exception as exc:
        print(f"✗ Seri port açılamadı: {exc}")
        return False

    buf = bytearray()
    try:
        # ── 1. adım: USB UBX çıkışını aç ─────────────────────────────────
        # Bu adımın KENDİ ACK'i de aynı sebeple (UBX çıkışı henüz kapalı
        # olabileceğinden) kaçabilir — FATAL SAYILMAZ, yalnızca bilgi
        # verilip 2. adıma geçilir (asıl doğrulama 2. adımın ACK'i).
        _send(ser, usbprot_msg)
        usbprot_result = _wait_ack(ser, buf, 0x06, 0x8A, timeout=2.0)
        if usbprot_result is True:
            print("  ✓ USB/UBX çıkışı onaylandı (ACK-ACK)")
        elif usbprot_result is False:
            print("  ✗ USB/UBX çıkışı REDDEDİLDİ (ACK-NAK) — devam ediliyor, 2. adım yine de denenecek")
        else:
            print("  (yanıt görünmedi — port muhtemelen UBX çıkışı kapalıydı, tam bu adım onu açıyor; devam ediliyor)")
        time.sleep(0.3)   # F9P'nin port ayarını uygulaması için kısa pay

        # ── 2. adım: asıl baz istasyonu ayarı — ASIL DOĞRULAMA burada ────
        _send(ser, msg)
        result = _wait_ack(ser, buf, 0x06, 0x8A, timeout=3.0)

        if result is None:
            # Hiç UBX çerçevesi görülmediyse teşhis için ham veriyi göster.
            extra = ser.read(512)
            if extra:
                buf += extra
            if buf:
                print(f"  [teşhis] portta UBX-dışı/tanınmayan {len(buf)} bayt görüldü, "
                      f"ilk 80'i: {bytes(buf[:80])!r}")
            else:
                print("  [teşhis] porttan HİÇ bayt gelmedi (2. adım penceresinde) — "
                      "kablo/port/baud kontrol edilmeli.")
    finally:
        ser.close()

    if result is True:
        print("\n✓ F9P onayladı (ACK-ACK) — survey-in başladı, RTCM3 mesajları etkinleştirildi, "
              "ayarlar Flash'a yazıldı (güç kesintisinde de kalıcı).")
        return True
    if result is False:
        print("\n✗ F9P REDDETTİ (ACK-NAK) — bir anahtar/değer geçersiz olabilir "
              "(firmware sürümü uyumsuz olabilir). Hiçbir ayar UYGULANMADI.")
        return False
    print("\n✗ ZAMAN AŞIMI — F9P'den UBX yanıtı gelmedi. Yukarıdaki [teşhis] satırına bakın "
          "— hiç bayt gelmiyorsa port/baud/kablo sorunu, NMEA/başka bayt geliyorsa UBX çıkışı "
          "hâlâ kapalı demektir. Ayarların uygulanıp uygulanmadığı BELİRSİZ — "
          "--watch-only ile NAV-SVIN yanıtı gelip gelmediğine bakın.")
    return False


def watch_survey_in(port: str, baud: int, poll_interval: float = 2.0) -> None:
    """Survey-in ilerlemesini NAV-SVIN poll ederek canlı gösterir. Ctrl+C ile çık."""
    import serial

    try:
        ser = serial.Serial(port, baud, timeout=0.2)
    except Exception as exc:
        print(f"✗ Seri port açılamadı: {exc}")
        return

    poll_msg = UBXMessage("NAV", "NAV-SVIN", POLL)
    buf = bytearray()
    print("Survey-in izleniyor (Ctrl+C ile çık)...")
    try:
        while True:
            _send(ser, poll_msg)
            t0 = time.time()
            got = False
            while time.time() - t0 < poll_interval:
                for cls_id, msg_id, payload in _read_ubx_frames(ser, buf):
                    if cls_id == 0x01 and msg_id == 0x3B and len(payload) >= 40:   # NAV-SVIN
                        got = True
                        (_version, _res1, _iTOW, dur, _mx, _my, _mz,
                         _mxhp, _myhp, _mzhp, _res2, mean_acc, obs,
                         valid, active, _res3) = struct.unpack("<B3sIIiiibbbBIIBB2s", payload)
                        acc_mm = mean_acc * 0.1   # ham birim 0.1mm → mm
                        durum = "TAMAMLANDI ✓" if valid else ("sürüyor..." if active else "PASİF (survey-in başlamadı)")
                        print(f"  [{dur:>4}s] durum={durum}  gözlem={obs}  "
                              f"ort.doğruluk≈{acc_mm:.0f} mm ({acc_mm/1000:.2f} m)")
                        if valid:
                            print("\nSurvey-in TAMAMLANDI — F9P artık RTCM3 baz istasyonu olarak yayında.")
                            return
                time.sleep(0.05)
            if not got:
                print("  (NAV-SVIN yanıtı gelmedi — bağlantı/port kontrol edilmeli, tekrar deneniyor)")
    except KeyboardInterrupt:
        print("\nİzleme durduruldu (F9P yapılandırması etkin kalmaya devam eder).")
    finally:
        ser.close()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=DEFAULT_PORT, help=f"F9P seri portu (varsayılan: {DEFAULT_PORT})")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud hızı (varsayılan: {DEFAULT_BAUD})")
    ap.add_argument("--svin-min-dur", type=int, default=300,
                    help="Survey-in asgari süre, saniye (varsayılan: 300)")
    ap.add_argument("--svin-acc-limit", type=float, default=2.0,
                    help="Survey-in hedef doğruluk, metre (varsayılan: 2.0)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Yalnızca gönderilecek UBX mesajını göster, donanıma DOKUNMA")
    ap.add_argument("--watch", action="store_true",
                    help="Yapılandırdıktan sonra survey-in ilerlemesini canlı izle")
    ap.add_argument("--watch-only", action="store_true",
                    help="Yapılandırma GÖNDERME, yalnızca mevcut survey-in durumunu izle")
    args = ap.parse_args()

    if args.watch_only:
        watch_survey_in(args.port, args.baud)
        return 0

    ok = configure_base(args.port, args.baud, args.svin_min_dur, args.svin_acc_limit,
                        dry_run=args.dry_run)
    if not ok:
        return 1
    if args.watch and not args.dry_run:
        watch_survey_in(args.port, args.baud)
    return 0


if __name__ == "__main__":
    sys.exit(main())
