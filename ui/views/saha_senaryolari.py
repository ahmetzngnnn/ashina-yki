# -*- coding: utf-8 -*-
"""Pervanesiz saha testi senaryo kataloğu — saha_testi_view tarafından okunur.

NEDEN AYRI DOSYA: bu liste sahada uygulanacak bir PROSEDÜRDÜR; arayüz kodu
değişirken yanlışlıkla bozulmasın diye ayrı tutuldu ve her maddesi kaynak
koddan doğrulanarak yazıldı.

ALANLAR
  grup     : başlık altında toplanır
  ad       : senaryo adı
  tetik    : operatörün FİZİKSEL olarak yapacağı şey
  beklenen : sistemin ne yapması gerektiği (koddan izlendi)
  gozle    : nereden görüleceği (YKİ göstergesi / log satırı / sesli uyarı)
  geri     : normale nasıl dönüleceği
  tehlike  : pervanesiz bile olsa dikkat edilecek şey ("" ise yok)
"""

SENARYOLAR = [
    # ── A. TEMEL ─────────────────────────────────────────────────────────
    dict(grup="A · Temel", ad="ARM / DISARM",
         tetik="Kumandadan ARM et (rudder sağa basılı) — motorlar dönmeye başlar.",
         beklenen="ArduPilot ön-arm kontrolleri AYNEN uygulanır; geçemezse ARM ETMEZ "
                  "ve sebebini STATUSTEXT ile söyler. Emniyet düğmesi basılı değilse "
                  "motorlar hiç dönmez (BRD_SAFETY_MASK=16368).",
         gozle="YKİ durum çubuğunda ARM göstergesi · 'PreArm:' mesajları · motor sesi",
         geri="Kumandadan DISARM (rudder sola) ya da paneldeki ACİL DISARM.",
         tehlike="Motorlar GERÇEKTEN döner. Kimse kolların üzerinde olmasın."),

    dict(grup="A · Temel", ad="Kip geçişleri (ALT_HOLD ↔ LOITER)",
         tetik="ARM'lıyken mod anahtarını 1 ↔ 4 arasında çevir.",
         beklenen="FC kipi değiştirir ve HEARTBEAT'te bildirir. LOITER için GPS/EKF "
                  "sağlıklı olmalı; değilse FC kipi REDDEDER.",
         gozle="YKİ'de uçuş kipi alanı · 'Flight mode changed' STATUSTEXT",
         geri="Anahtarı 1 (ALT_HOLD) konumuna al.",
         tehlike="LOITER'da gaz kolu alçalma hızını sürer — kol ORTADA olsun."),

    dict(grup="A · Temel", ad="LAND kipi (pervanesiz)",
         tetik="ARM'lıyken mod anahtarını 6 (LAND) yap.",
         beklenen="FC alçalma komutu üretir. Araç yerde durduğu için iniş algılayıcı "
                  "kısa sürede 'landed' der ve otomatik disarm edebilir.",
         gozle="Motor devrinin düşmesi · 'Land complete' / disarm bildirimi",
         geri="Anahtarı 1'e al, gerekirse tekrar ARM et.",
         tehlike=""),

    # ── B. GÜÇ ve KABLO ──────────────────────────────────────────────────
    dict(grup="B · Güç ve kablo", ad="Raspberry Pi GÜCÜ kesildi (ARM'lıyken)",
         tetik="Motorlar dönerken Pi'nin güç kablosunu ÇEK.",
         beklenen="FC uçuşu SÜRDÜRÜR — companion yalnız görev beynidir, uçuşu FC yapar. "
                  "Companion'dan gelen GUIDED hedefleri kesilir; FC son hedefte asılı "
                  "kalır. GCS failsafe (FS_GCS_ENABLE) 1 ise RTL tetiklenir — "
                  "ŞU AN 0 OLDUĞU İÇİN TETİKLENMEZ, bu senaryoda farkı gözleyin.",
         gozle="YKİ'de yardımcı bilgisayar bağlantısı KIRMIZI · durum yayını (1 Hz) durur "
               "· video akışı kesilir · FC telemetrisi RFD üzerinden SÜRER",
         geri="Gücü geri ver; servis kendiliğinden kalkar (~20-25 sn).",
         tehlike="Motorlar dönmeye DEVAM EDER. Pilot kumandayı elinde tutsun ve "
                 "gerekirse DISARM etsin."),

    dict(grup="B · Güç ve kablo", ad="Pi gücü geri verildi",
         tetik="Kesilen gücü geri tak, servisin kalkmasını bekle.",
         beklenen="ashina-iha kendiliğinden başlar, FC'ye yeniden bağlanır ve "
                  "AÇILIŞ HOMING'i yapar: kavrayıcılar AÇILIR, yük bırakma servoları "
                  "BIRAKMA konumuna gider.",
         gozle="'Açılış homing tamamlandı' log satırı · YKİ'de yardımcı bilgisayar YEŞİL "
               "· servo durumları güncellenir",
         geri="—",
         tehlike="AÇILIŞ HOMING SERVOLARI OYNATIR. Araçta yük varsa DÜŞEBİLİR — "
                 "bu testi YÜKSÜZ yapın."),

    dict(grup="B · Güç ve kablo", ad="Pixhawk ↔ Pi USB kablosu çekildi",
         tetik="Companion çalışırken FC'ye giden USB kablosunu ÇEK.",
         beklenen="Companion MAVLink oturumunu kaybeder ve yeniden bağlanma döngüsüne "
                  "girer. Sağlık MAVLINK alt sistemi bozulur; görev sürüyorsa iptal "
                  "edilir ve RTL denenir (komut FC'ye ULAŞAMAZ — FC kendi failsafe'ine kalır).",
         gozle="'MAVLink oturumu hatası ... yeniden deneniyor' log satırı · YKİ sağlık "
               "panelinde MAVLINK bozulur",
         geri="Kabloyu geri tak; companion kendiliğinden yeniden bağlanır.",
         tehlike="Kablo çekiliyken companion aracı DURDURAMAZ. Pilot devrede olsun."),

    dict(grup="B · Güç ve kablo", ad="YKİ ↔ FC telemetri (RFD900A) kesildi",
         tetik="RFD900A'yı YKİ tarafından çıkar ya da kapat.",
         beklenen="Görev DEVAM EDER — görev beyni araçtadır, YKİ yalnız izler. "
                  "FC'nin GCS failsafe'i devreye girerse (FS_GCS_ENABLE=1) RTL olur.",
         gozle="YKİ'de drone bağlantısı KIRMIZI · yardımcı bilgisayar YEŞİL kalır "
               "(bu ikisinin AYRI olduğunu gösterir)",
         geri="Radyoyu geri tak, üst çubuktan BAĞLAN.",
         tehlike=""),

    # ── C. SENSÖR ────────────────────────────────────────────────────────
    dict(grup="C · Sensör", ad="GPS / EKF kaybı",
         tetik="GPS antenini metal bir kapla ört ya da aracı iç mekâna al.",
         beklenen="EKF bozulunca companion hız/pozisyon komutlarını BLOKE eder "
                  "(yalnız durdurma komutu geçer) ve görevi iptal eder. FC ise "
                  "GPS gerektiren kipleri (LOITER) reddeder.",
         gozle="'EKF' sağlık uyarısı · 'GPS Glitch or Compass error' STATUSTEXT · "
               "YKİ'de uydu sayısı düşer",
         geri="Anteni aç, 3D fix ve ≥6 uydu bekle.",
         tehlike="Pilot ALT_HOLD'da uçurabilir; LOITER'a GÜVENMEYİN."),

    dict(grup="C · Sensör", ad="Kamera akışı kesildi",
         tetik="Kamera şerit kablosunu çıkar (araç kapalıyken tak/çıkar daha güvenli).",
         beklenen="Görüş hattı kare üretemez; görev hedef bulamaz ve tarama zaman "
                  "aşımına kadar sürer. Görev KİLİTLENMEZ.",
         gozle="Dashboard'da video donar/kesilir · vision sağlık alanı bozulur",
         geri="Kabloyu tak, servisi yeniden başlat.",
         tehlike=""),

    dict(grup="C · Sensör", ad="TOF halkası veri vermiyor",
         tetik="VL53L1X yayıncısını durdur ya da halka kablosunu çıkar.",
         beklenen="Halka YETKİSİZ duruma düşer ve görevi BLOKE ETMEZ; kavrama onayı "
                  "kamera tarafına kalır.",
         gozle="YKİ TOF panelinde değerler '—' olur · tof sağlık alanı",
         geri="Yayıncıyı başlat / kabloyu tak.",
         tehlike=""),

    # ── D. GÖREV ─────────────────────────────────────────────────────────
    dict(grup="D · Görev", ad="Operatör görevi İPTAL etti",
         tetik="Görev sürerken YKİ'den görevi iptal et.",
         beklenen="Görev durur ve doğrulanmış RTL denenir; üç denemede olmazsa LAND'e "
                  "düşülür ve operatöre kritik uyarı gider.",
         gozle="'GÖREV İPTAL EDİLDİ' log satırı · kip RTL/LAND'e döner",
         geri="Araç indikten sonra yeni görev yüklenebilir.",
         tehlike="Pervanesiz araçta RTL/LAND anlamlı bir hareket üretmez; "
                 "kip değişimini gözlemek yeterlidir."),

    dict(grup="D · Görev", ad="Pilot GUIDED'dan devraldı",
         tetik="Görev GUIDED'da koşarken mod anahtarını 4 (LOITER) yap.",
         beklenen="Companion FC kipinin GUIDED olmadığını görür, hedef akışını "
                  "DURDURUR (araçla kavga etmez) ve operatöre bildirir; kısa tolerans "
                  "sonunda görevi iptal eder.",
         gozle="'FC modu GUIDED değil ... hedef akışı duraklatıldı' bildirimi",
         geri="Görevi yeniden yükleyip başlat.",
         tehlike="Gaz kolu ORTADA olsun."),

    dict(grup="D · Görev", ad="Görev sonu — PİLOTA DEVİR",
         tetik="Görev 1'i 'Kalkış noktasına dön' KAPALI çalıştırıp bitmesini bekle.",
         beklenen="Araç GUIDED'da tutunur (çubuklar yok sayılır), YKİ ACİL DURUM ilan "
                  "eder ve onay ister. Onay verilince LOITER'a geçer. 180 sn onay "
                  "gelmezse OTONOM RTL.",
         gozle="Kırmızı durum çubuğu · sesli 'gaz kolunu ortaya alın' · onay penceresi",
         geri="Onay ver ya da 180 sn bekleyip RTL'i gözle.",
         tehlike="Onay verilir verilmez gaz kolu alçalma hızını sürmeye BAŞLAR."),

    # ── E. YÜK ───────────────────────────────────────────────────────────
    dict(grup="E · Yük", ad="Kavrayıcı aç / kapa",
         tetik="YKİ'den kavrayıcıya 'kapat', sonra 'aç' komutu ver.",
         beklenen="İKİ kapak da birlikte hareket eder ve ikisi de limit switch ile "
                  "DOĞRULANIR. Bir taraf tamamlanmazsa 2 kez daha denenir; yine "
                  "olmazsa hangi tarafın kaldığı bildirilir.",
         gozle="'grabber_left/right: close DOĞRULANDI (limit switch, N s)' log satırları "
               "· YKİ'de kavrayıcı durumu",
         geri="Karşı komutu ver.",
         tehlike="Parmaklarınızı kavrayıcı ağzından uzak tutun."),

    dict(grup="E · Yük", ad="Yük bırakma servoları",
         tetik="YKİ'den kırmızı ve mavi bırakma servolarına aç/kapa komutu ver.",
         beklenen="Servo komutu FC'ye gider ve ACK beklenir; onay alınamazsa 3 kez "
                  "denenir ve durum '?' kalır — panel YALANCI onay göstermez.",
         gozle="YKİ servo durumu · 'last_cmd_ok' alanı",
         geri="Karşı komutu ver.",
         tehlike="Yük takılıysa DÜŞER — bu testi yüksüz yapın."),

    # ── F. BATARYA ───────────────────────────────────────────────────────
    dict(grup="F · Batarya", ad="Düşük batarya eşiği (21.2 V)",
         tetik="Yarı boş bir paketle çalış ve gerilimin 21.2 V altına inmesini bekle "
               "(10 sn kesintisiz).",
         beklenen="FC RTL failsafe'ini tetikler; companion DEGRADED uyarısı basar.",
         gozle="'Batarya düşük: NN.NN V' bildirimi · sesli uyarı · kip RTL'e döner",
         geri="Paketi değiştir.",
         tehlike="Eşiği DENEMEK İÇİN parametre DEĞİŞTİRMEYİN — teknik kontrol sonrası "
                 "parametre değişikliği eleme sebebidir."),

    dict(grup="F · Batarya", ad="Kritik batarya (20.6 V) — mandal",
         tetik="Gerilimin 20.6 V altına 10 sn inmesini bekle.",
         beklenen="FC LAND tetikler. Companion MANDALLI kritik ilan eder: gerilim "
                  "toparlasa bile sağlık FAILED kalır ve YENİ GÖREV BAŞLATILAMAZ.",
         gozle="'Batarya KRİTİK' bildirimi · görev başlatma reddi",
         geri="Paketi değiştir VE companion servisini yeniden başlat (mandal ancak "
              "böyle açılır).",
         tehlike=""),
]
