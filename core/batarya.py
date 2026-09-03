"""
batarya.py — Pil durum kestirimi (YKİ'nin TEK DOĞRU KAYNAĞI)
=============================================================

YKİ'nin ekranda gösterdiği pil yüzdesi BURADA hesaplanır. `ui/top_bar.py`,
`ui/views/test_panel_view.py` ve `ui/views/system_log_view.py` bu modülü
çağırır — üç ayrı kopya YOKTUR. Eşiği/eğriyi değiştirmek isteyen tek bu
dosyaya dokunur.

── NE DEĞİŞTİ ve NEDEN ────────────────────────────────────────────────────
ÖNCEKİ DAVRANIŞ: YKİ, MAVLink `SYS_STATUS.battery_remaining` (ve ArduPilot
4.x'te `BATTERY_STATUS.battery_remaining`) alanını yüzde olarak gösteriyordu.
O alan ArduPilot'un KAPASİTE SAYIMIDIR: BATT_CAPACITY (bu araçta 4200 mAh)
eksi akım entegrasyonuyla ölçülen tüketilen mAh. Yani gerilimle DOĞRUDAN
ilgisi yoktur; tam dolu ama yaşlanmış (kapasitesi düşmüş) bir pakette
iyimser, uçuş ortasında takılan bir pakette ise tamamen anlamsızdır
(sayaç sıfırdan başlar).

KULLANICI KARARI (2026-09-02, birebir): "25.2 V full %100, 21 volt %0".
Bu 6S LiPo için 25.2 V = 6 × 4.20 V (hücre başına tam dolu),
21.0 V = 6 × 3.50 V (boş kabul). Artık gösterilen yüzde budur.

── FİZİK NOTU — BU EŞLEMENİN SINIRLARI (davranış BİLEREK böyle) ──────────
1) LiPo boşalma eğrisi DOĞRUSAL DEĞİLDİR. Gerçek bir LiPo hücresi, dinlenme
   geriliminde kabaca şöyle davranır: 4.20 V ≈ %100, 3.85 V ≈ %50,
   3.70 V ≈ %25, 3.50 V ≈ %5-10. Yani gerilimin ORTA bölgesi (3.7-3.9 V)
   şarjın çok büyük bir kısmını kapsayan DÜZ bir platodur; uçlar ise
   dik iner. Doğrusal eşleme bu yüzden:
     • plato bölgesinde (≈3.8 V/hücre = 22.8 V = %43) gerçek şarjı
       OLDUĞUNDAN AZ gösterir (gerçekte ≈%40-50 civarı, yakın ama kayar),
     • yüksek uçta (4.0-4.2 V) gerçek şarjı OLDUĞUNDAN ÇOK gösterir,
     • düşük uçta (3.5-3.6 V) gerçek şarjı OLDUĞUNDAN ÇOK gösterir —
       3.5 V'a ulaşan bir paket pratikte %0 değil, zaten bitmiştir.
   ÖLÇÜLMEDİ: bu aracın kendi 6S 4200 mAh paketinin gerçek OCV-SoC eğrisi
   çıkarılmadı. Yukarıdaki değerler genel LiPo literatürüdür, bu pakete ait
   ÖLÇÜM DEĞİLDİR.
2) YÜK ALTINDA SAG (gerilim çökmesi): FC'de BATT_FS_VOLTSRC=0, yani ham
   gerilim kullanılıyor, iç direnç telafisi YOK. Hover'da çekilen akımda
   paket gerilimi anlık olarak düşer; motor gaza yüklendiğinde daha da
   düşer. Bu yüzden UÇARKEN gösterilen yüzde, aynı paketin yere inip
   dinlendiğinde göstereceğinden DÜŞÜKTÜR. Bu yönde hata EMNİYETLİDİR
   (erken uyarır), ama operatör "indim, yüzde yükseldi" davranışını
   görecektir — bu bir arıza değil, fiziktir.
3) İSTENİRSE EĞRİ TABANLI NASIL YAPILIR (bu dosyada TEK yer değişir):
   `_EGRI` diye (gerilim_hucre, yuzde) çiftlerinden oluşan artan bir tablo
   tanımlanır (ör. (3.50,0) (3.65,10) (3.75,25) (3.85,50) (3.95,70)
   (4.05,85) (4.20,100)) ve `yuzde_gerilimden` doğrusal formül yerine bu
   tabloda parça-doğrusal ara değer bulur. Kalibrasyonu şöyle ÖLÇMEK
   gerekir: dolu paketi sabit akımla (ör. 5 A) boşaltıp her 5 dakikada
   10 dakika DİNLENDİRİP açık devre gerilimini ve çekilen mAh'i kaydetmek.
   Bu ölçüm YAPILMADI; o yüzden burada eğri YOK.

── "VERİ YOK" ≠ "PİL BOŞ" ────────────────────────────────────────────────
Gerilim gelmiyorsa (telemetri kopuk, sensör yok, 0 V geliyor) bu fonksiyonlar
`None` döner ve arayüz "—/veri yok" gösterir. ASLA %0 gösterilmez: depo
"kanıt yokluğu ≠ olumsuz kanıt" ilkesiyle yazılmıştır ve %0, operatörü
paketi değiştirmeye/uçuşu iptale iten GERÇEK bir bilgidir. Bunu telemetri
sessizliğiyle karıştırmak, ya sahte panik ya da (tersi durumda) sahte
güven üretir.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

# ── Paket tanımı ───────────────────────────────────────────────────────────
# ASHİNA aracı: Leopard 40C 6S LiPo 4200 mAh (operatör doğruladı 2026-09-03).
# FC'de OLMASI GEREKEN (bkz. IHA/config.json::fc_params_expected — uçuş öncesi
# kontrol bunları denetler):
#   BATT_MONITOR=4 (analog gerilim+akım), BATT_CAPACITY=4200,
#   BATT_LOW_VOLT=21.0 (3.50 V/hücre), BATT_CRT_VOLT=19.8 (3.30 V/hücre).
#
# 2026-09-03 DEĞİŞTİ: burada eskiden LOW=21.2 / CRT=21.0 yazıyordu; ikisi
# arasında yalnız 0.2 V vardı. LOW → RTL, CRT → Land olduğu için araç RTL'e
# geçtikten saniyeler sonra Land'e düşüyor ve eve dönmeye fırsat bulamadan
# olduğu yere iniyordu. Yarışmada bu, bırakma alanının dışına inmek demektir.
HUCRE_SAYISI = 6
HUCRE_DOLU_V = 4.20      # tam dolu LiPo hücresi (üretici üst sınırı)
HUCRE_BOS_V  = 3.50      # "boş" kabulü — kullanıcı kararı (bkz. dosya başlığı)

PAKET_DOLU_V = round(HUCRE_SAYISI * HUCRE_DOLU_V, 2)   # 25.20 V → %100
PAKET_BOS_V  = round(HUCRE_SAYISI * HUCRE_BOS_V,  2)   # 21.00 V → %0

# NEDEN: 6S bir paket, hücreleri kalıcı hasar görmeden bile 6×2.5 = 15.0 V'un
# altına inemez; bunun da altındaki bir okuma (0.0 V, 3.2 V gibi) PAKET DEĞİL,
# telemetri/sensör artığıdır (alan hiç doldurulmamış, bölücü kopmuş, mesaj
# gelmemiş). Böyle bir değeri %0 diye göstermek "veri yok"u "pil bitti"ye
# çevirir. Eşik 5.0 V: 15 V'un çok altında, yani gerçek bir 6S okumasını asla
# elemez; buna karşılık 0/1-2 V'luk artıkları kesin yakalar.
GECERSIZ_ESIK_V = 5.0

# ── Renk/uyarı eşikleri ────────────────────────────────────────────────────
# NEDEN bu iki sayı DEĞİŞMEDİ ama artık ANLAMLARI ölçülebilir:
# Yeni ölçekte yüzde ↔ gerilim birebir çevrilebilir (yuzdeden_gerilim):
#   %50 → 23.10 V = 3.85 V/hücre  → LiPo eğrisinde gerçek SoC kabaca %50-60,
#         yani "yarıyı geçtin, dönüş planını düşün" için doğru yer.
#   %20 → 21.84 V = 3.64 V/hücre  → gerçek SoC kabaca %20-30; paketin dik
#         iniş bölgesine GİRDİĞİ yer, kırmızı burada doğru.
# EMNİYET PAYI KONTROLÜ (asıl gerekçe): FC'nin KENDİ eşikleri bu ölçekte
#   BATT_LOW_VOLT=21.2 V → %4.8   ve   BATT_CRT_VOLT=21.0 V → %0.
# Yani YKİ %20'de kırmızıya döndüğünde ArduPilot'un batarya failsafe'ine
# hâlâ 0.64 V (≈%15 gösterge) mesafe vardır. Uyarı, FC eylem almadan ÇOK
# ÖNCE operatöre ulaşır — istenen davranış budur, eşikler bu yüzden kaldı.
RENK_KRITIK_YUZDE = 20
RENK_UYARI_YUZDE  = 50

# NEDEN: Uçuş öncesi kontrol listesi ("Batarya > %50") eskiden FC'nin mAh
# sayacına bakıyordu. Yeni ölçekte %50 = 23.10 V; sahada yeni şarj edilmiş
# bir paket 25.0-25.2 V ile gelir, yani bu kapı taze paketi geçirir, yarı
# kullanılmışı geçirmez. Kontrol listesi metni ile bu sabit AYNI yerden
# beslenmelidir; ikisi ayrışırsa liste yalan söyler.
UCUS_ONCESI_ASGARI_YUZDE = 50

# Telemetri sözlüğünde bu modülün ürettiği yüzdenin anahtarı.
# NEDEN ayrı ad: `battery_remaining` FC'nin mAh tabanlı alanıdır ve hâlâ
# telemetride durur (kayıt/analiz için değerlidir). Gerilim tabanlı yüzde
# ONUN YERİNE GEÇMEZ, YANINA yazılır — iki farklı kestirimi aynı anahtara
# koymak, sonradan logu okuyanın hangisine baktığını bilememesi demektir.
TELEMETRI_ANAHTARI = "battery_pct_volt"


def yuzde_gerilimden(gerilim_v: Optional[float]) -> Optional[int]:
    """Paket gerilimini (V) yüzdeye çevirir. Bilinmiyorsa None.

    Doğrusal eşleme: PAKET_BOS_V → %0, PAKET_DOLU_V → %100, 0-100'e kelepçeli.
    Doğrusallığın sınırları için dosya başlığındaki FİZİK NOTU'na bakın.

    None döner (yüzde BİLİNMİYOR):
      • gerilim alanı hiç yoksa / None ise,
      • sayıya çevrilemiyorsa,
      • GECERSIZ_ESIK_V altındaysa (0.0 dahil) — bkz. o sabitin NEDEN'i.
    """
    if gerilim_v is None:
        return None
    try:
        v = float(gerilim_v)
    except (TypeError, ValueError):
        return None
    # NaN kendine eşit değildir — float("nan") buradan sessizce geçmesin.
    if v != v:
        return None
    if v < GECERSIZ_ESIK_V:
        return None

    oran = (v - PAKET_BOS_V) / (PAKET_DOLU_V - PAKET_BOS_V)
    yuzde = int(round(oran * 100.0))
    # Kelepçe: 25.2 V üstü şarj cihazında/soğukta görülebilir, 21 V altı ise
    # bitmiş paket. İkisi de ekranda 0-100 dışına TAŞMAMALI.
    return max(0, min(100, yuzde))


def yuzdeden_gerilim(yuzde: float) -> float:
    """Yüzdenin karşılık geldiği paket gerilimi (V) — eşik belgeleme/ipucu için.

    yuzde_gerilimden'in tersidir; eşiklerin kaç volt ettiğini arayüzde
    göstermek ya da yorumda doğrulamak için kullanılır.
    """
    y = max(0.0, min(100.0, float(yuzde)))
    return PAKET_BOS_V + (PAKET_DOLU_V - PAKET_BOS_V) * (y / 100.0)


def yuzde_telemetriden(data: Optional[Mapping[str, Any]]) -> Optional[int]:
    """Telemetri sözlüğünden gerilim tabanlı pil yüzdesi. Bilinmiyorsa None.

    Üretici (mavlink_worker) yüzdeyi zaten hesaplamışsa o kullanılır; sim
    worker'ı gibi bu alanı doldurmayan kaynaklarda `battery_voltage`'tan
    hesaplanır. NEDEN iki yol: telemetri YKİ'ye iki ayrı yoldan geliyor
    (core/mavlink_worker.py gerçek FC, core/sim_workers.py simülasyon) ve
    arayüz ikisini ayırt etmez; tek yola bel bağlamak simde ya da gerçek
    uçuşta göstergenin sessizce boş kalması demekti.
    """
    if not data:
        return None
    onceden = data.get(TELEMETRI_ANAHTARI, None)
    if isinstance(onceden, (int, float)) and not isinstance(onceden, bool):
        return max(0, min(100, int(onceden)))
    return yuzde_gerilimden(data.get("battery_voltage", None))


def gerilim_telemetriden(data: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Telemetriden paket gerilimi (V). Geçersiz/yok ise None."""
    if not data:
        return None
    try:
        v = float(data.get("battery_voltage", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if v != v or v < GECERSIZ_ESIK_V:
        return None
    return v


def renk_tokeni(yuzde: Optional[int]) -> Optional[str]:
    """Yüzdeye karşılık gelen TEMA TOKEN ADI (ham hex DEĞİL).

    None yüzde → None token: çağıran "veri yok"u kendi nötr rengiyle
    (idle/text_muted) göstermelidir; success/danger seçmek zorunda kalmasın.
    """
    if yuzde is None:
        return None
    if yuzde <= RENK_KRITIK_YUZDE:
        return "danger"
    if yuzde <= RENK_UYARI_YUZDE:
        return "warning"
    return "success"


def ozet_metni(data: Optional[Mapping[str, Any]]) -> str:
    """Log/ipucu satırları için kısa özet: '%73 (23.9 V)' ya da 'veri yok'."""
    yuzde = yuzde_telemetriden(data)
    volt  = gerilim_telemetriden(data)
    if yuzde is None:
        # NEDEN "veri yok" yazısı: burada "%0" yazmak, boş pil ile sessiz
        # telemetriyi aynı görüntüye indirger (bkz. dosya başlığı).
        return "veri yok"
    if volt is None:
        return f"%{yuzde}"
    return f"%{yuzde} ({volt:.1f} V)"


def ipucu_metni(data: Optional[Mapping[str, Any]]) -> str:
    """Batarya göstergesinin ipucu (tooltip) metni — ölçeği açıkça yazar."""
    yuzde = yuzde_telemetriden(data)
    volt  = gerilim_telemetriden(data)
    olcek = (f"Ölçek: {PAKET_DOLU_V:.1f} V = %100, {PAKET_BOS_V:.1f} V = %0 "
             f"({HUCRE_SAYISI}S, doğrusal)")
    if yuzde is None:
        return ("Pil verisi YOK (gerilim gelmiyor) — %0 ile karıştırmayın.\n"
                + olcek)
    fc_notu = ("Yük altında gerilim düşer; uçarken gösterge, yerdeki "
               "dinlenmiş değerden DÜŞÜK okur.")
    volt_txt = f"{volt:.2f} V" if volt is not None else "gerilim yok"
    return f"Pil: %{yuzde}  ({volt_txt})\n{olcek}\n{fc_notu}"
