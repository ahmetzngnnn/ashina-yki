# -*- coding: utf-8 -*-
"""Pervanesiz saha testi senaryo kataloğu — saha_testi_view tarafından okunur.

NEDEN AYRI DOSYA: bu liste sahada uygulanacak bir PROSEDÜRDÜR; arayüz kodu
değişirken yanlışlıkla bozulmasın diye ayrı tutuldu.

SÜRÜM NOTU (2026-09-04): A grubu (güç/kablo) ve C1-C4/C6, üç ajanlı bir
denetimin ArduPilot kaynağından (mode.h, motors.cpp, events.cpp,
GCS_Common.cpp) ve bu depodan doğrulanmasıyla YENİDEN YAZILDI. İlk sürümün
A grubundaki 4 senaryonun TAMAMI ve C grubunun büyük kısmı hatalı ya da
eksik bulundu — en önemlisi: pervanesiz tezgâhta "araç ARM'lı kalsın"
varsayımı gerçekçi değildi (ArduCopter gaz dipteyken 10 sn'de kendi kendine
disarm eder) ve "görev sürüyorsa" dalları kalkış hiç başaramadığı için
tezgâhta zaten hiç ulaşılamayan bir dalı anlatıyordu. B (temel), D (görev)
ve F (batarya) grupları oturum limiti yüzünden bu turda TAM denetlenemedi;
kod referanslarına dayanıyorlar ama D/pilota-devir dışındakiler ikinci bir
gözle sınanmadı — sahada temkinli kullanın.

ALANLAR
  grup     : başlık altında toplanır
  ad       : senaryo adı
  tetik    : operatörün FİZİKSEL olarak yapacağı şey (ön koşullar dahil)
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

    dict(grup="A · Temel", ad="ARM'lıyken gaz kolunu DİPTE tutma — 10 sn'de KENDİLİĞİNDEN disarm",
         tetik="ARM et, gaz kolunu dipte bırak ve kronometre başlat.",
         beklenen="Bu bir arıza DEĞİL, ArduCopter'ın kendi davranışıdır: araç yerde + "
                  "gaz dipte iken DISARM_DELAY (varsayılan 10 sn) sonunda otomatik "
                  "disarm eder. Companion/Pi ile İLGİSİZDİR — bir sonraki A/B "
                  "senaryolarında 'ARM'lı kalsın' beklerken bunu unutmayın.",
         gozle="~10 sn sonra motor sesi durur · FC 'Disarming motors' STATUSTEXT'i basar",
         geri="Tekrar ARM et.",
         tehlike="Bu satır, aşağıdaki güç/kablo testlerinin YANLIŞ YORUMLANMASINI "
                 "önlemek için var: motorlar durursa ÖNCE bu mesajı ara. Varsa "
                 "sebep güç/kablo kesintisi DEĞİL, bu zamanlayıcıdır."),

    dict(grup="A · Temel", ad="ARM'lıyken motoru GÜVENLE döndürme (rölanti bandı)",
         tetik="ARM'dan sonra gaz kolunu ölü bölgenin HEMEN ÜSTÜNE al — ORTANIN "
               "ALTINDA, dipte değil.",
         beklenen="control_in>0 olduğu için otomatik disarm sayacı sürekli sıfırlanır "
                  "ama istenen tırmanma hızı hâlâ negatif olduğu için spool "
                  "GROUND_IDLE'da kalır: motorlar RÖLANTİ devrinde döner, kalkış "
                  "denemesi BAŞLAMAZ.",
         gozle="Motor rölanti devrinde sabit döner, devir değişmez",
         geri="Gazı dipte bırakıp 10 sn bekle (yukarıdaki madde) ya da ACİL DISARM.",
         tehlike="GAZI ORTANIN ÜSTÜNE ÇIKARMA: ALT_HOLD/LOITER Takeoff durumuna "
                 "girer, spool THROTTLE_UNLIMITED olur ve pervanesiz araç "
                 "tırmanamadığı için irtifa kontrolcüsü gazı YUKARI SÜRER — motorlar "
                 "tam gaza doğru tırmanır. Bu senaryoyu içeren HER testte (B "
                 "grubundaki güç/kablo kesintileri dahil) gaz bu dar bantta kalsın."),

    # ── B. GÜÇ ve KABLO (2026-09-04 tamamen yeniden yazıldı) ─────────────
    dict(grup="B · Güç ve kablo", ad="B1 · Raspberry Pi GÜCÜ kesildi (motor rölantide, ARM'lı)",
         tetik="ÖN KOŞUL: (1) Pervaneler SÖKÜLÜ, araç tezgâha sabit; (2) Pi'nin güç "
               "yolunun FC'den BAĞIMSIZ olduğunu elle doğrula — ortak BEC'ten "
               "besleniyorsa kablo çekilince FC de ölür, bu SENARYO DEĞİŞİR; "
               "(3) KAVRAYICIYI DURDUR (aşağıya bak — sürekli dönüş motoru, güç "
               "kesilirken dönüyorsa DURMADAN dönmeye devam edebilir); (4) YKİ AÇIK "
               "ve RFD üzerinden FC'ye bağlı; (5) gazı yukarıdaki rölanti bandında "
               "tut. Sonra Pi'nin güç kablosunu ÇEK.",
         beklenen="FC HİÇBİR EYLEM ÜRETMEZ: motorlar rölantide dönmeye devam eder, "
                  "araç ARM'lı kalır, kip değişmez. Üç ayrı sebep: FS_GCS_ENABLE şu "
                  "an 0; companion FC'ye HİÇ heartbeat göndermiyor (yalnız YKİ RFD "
                  "üzerinden basıyor, bu yüzden FS_GCS_ENABLE=1 yazılsa BİLE YKİ "
                  "bağlıyken bu senaryo tetiklenmez); otomatik disarm yalnız "
                  "'gaz dipte' koşuluna bakar, companion'la ilgisi yok.",
         gozle="Video ~8 sn donuk kalır sonra silinip 'Yeniden bağlanıyor…' yazar · "
               "Dashboard'da 'Yardımcı Bilgisayar 🔴' ~10-12 sn SONRA düşer (ANINDA "
               "DEĞİL) · üst çubuktaki FC/drone linki YEŞİL KALIR (RFD'den, Pi'den "
               "bağımsız) · motor sesi/devri DEĞİŞMEZ.",
         geri="Gücü geri ver (bkz. B2).",
         tehlike="KAVRAYICI TUZAĞI: kavrayıcı sürekli-dönüş servodur ve 'DUR' komutu "
                 "yalnız Pi'nin çalışan görevinde yaşar; Pi ölürse FC son PWM'i "
                 "SÜRESİZ tutar — kavrama sırasında güç kesilirse motor DURMADAN "
                 "döner (ısınma/mekanik hasar). Bu testi kavrayıcı DURMUŞKEN yapın."),

    dict(grup="B · Güç ve kablo", ad="B2 · Pi gücü geri verildi — DISARM'LI başlayın",
         tetik="'ARM'lı kalsın' bekleme — SİLİNDİ: gaz dipteyse 10 sn'de zaten "
               "disarm olmuş olacaksın (A grubuna bak). Kesilen gücü DISARM'LI "
               "durumda geri tak ve kronometre başlat.",
         beklenen="ashina-iha servisi kendiliğinden başlar (~20-25 sn: ağ + FC "
                  "portu beklemeleri + Python içe aktarma), FC'ye yeniden bağlanır "
                  "ve AÇILIŞ HOMİNG'i çalışır: kavrayıcılar AÇILIR, yük bırakma "
                  "servoları BIRAKMA konumuna gider. Bu servolar OYNAR.",
         gozle="'Açılış homing tamamlandı' log satırı · YKİ'de yardımcı bilgisayar "
               "YEŞİLE döner · servo durumları güncellenir",
         geri="—",
         tehlike="AÇILIŞ HOMİNG SERVOLARI OYNATIR. Araçta yük varsa DÜŞEBİLİR — "
                 "bu testi YÜKSÜZ yapın. Homing kapısının 'ARM'lıysa dokunmam' "
                 "sözü kesin bir garanti DEĞİL (FC'den ilk heartbeat işlenene "
                 "kadar ~1 sn'lik bir pencerede kapı yanlışlıkla açık okunabilir) "
                 "— DISARM'lı başlamak bu belirsizliği tamamen ortadan kaldırır."),

    dict(grup="B · Güç ve kablo", ad="B3 · Görev başlamıyor: 'params' sağlık kapısı (FC parametre uyumsuzluğu)",
         tetik="Pi yeniden başladıktan sonra YKİ'den (Yard. PC düğmesiyle) görev "
               "göndermeyi dene. TEK DÜĞMEYE ASLA BASMA: '▶ Görev Başlat (FCU)' — "
               "o düğme companion'ı ATLAYIP doğrudan FC'ye GUIDED→ARM→gaz→"
               "NAV_TAKEOFF gönderir; sağlık kapısına tabi DEĞİLDİR ve pervanesiz "
               "aracı 40 sn tırmanma gazına sokar.",
         beklenen="FC'de FS_THR_ENABLE=1/FS_GCS_ENABLE=0 (beklenen 3/1) olduğu "
                  "sürece '▶ Görev Başlat (Yard. PC)' görevi REDDEDER: "
                  "'Sistem sağlığı FAILED — arızalı alt sistem(ler): params'. "
                  "Bu bir arıza DEĞİL, doğru davranıştır.",
         gozle="YKİ'de kırmızı 'GÖREV BAŞLATILMADI' bildirimi",
         geri="Mission Planner'dan FS_THR_ENABLE=3, FS_GCS_ENABLE=1 yaz.",
         tehlike="Parametre doğrulaması TEK ATIŞLIKTIR: Pi, Cube'dan ÖNCE açılırsa "
                 "ya da FC bağlantısı ilk 15 sn'de kurulmazsa kontrol BİR DAHA HİÇ "
                 "ÇALIŞMAZ ve kapı SESSİZCE AÇIK kalabilir. Pixhawk sonradan "
                 "takılırsa yeniden doğrulama İÇİN Pi'yi de yeniden başlatın."),

    dict(grup="B · Güç ve kablo", ad="B4 · Pixhawk ↔ Pi USB kablosu çekildi (motor rölantide)",
         tetik="ÖN KOŞUL: Cube batarya/güç modülünden besleniyor olmalı (yalnız Pi "
               "USB'sinden BESLENMİYORSA — öyleyse kablo çekmek FC güç kaybı "
               "demektir, ayrı bir test). Pervane yok, pilot verici elde ve devrede. "
               "Companion çalışırken (ARM'lı, gaz rölanti bandında) USB'yi Pi "
               "tarafından çek, en az 60 sn çıkarılı bırak.",
         beklenen="Companion oturumu <1 sn içinde düşürür ve SINIRSIZ yeniden "
                  "bağlanma dener (2, 5, 10, 30, 60 sn... aralıklarla). Motorlar "
                  "DÖNMEYE DEVAM EDER, kip DEĞİŞMEZ, RTL/LAND OLMAZ — FC tarafında "
                  "hiçbir yedek yoktur (FS_GCS_ENABLE=0, ve 1 olsa bile companion "
                  "zaten heartbeat basmıyordu).",
         gozle="~9 sn'de EKRANI BLOKLAYAN kırmızı 'KRİTİK FAILSAFE — MAVLINK' "
               "penceresi açılır (içindeki 'RTL gerçekleştiriyor olabilir' cümlesi "
               "BU SENARYODA YANLIŞTIR, kapatıp devam edin) · üst çubuk (RFD) "
               "YEŞİL KALIR ve GERÇEK ARM/gerilimi gösterir · Dashboard/companion "
               "paneli ise ARMED=hayır, batarya 0.00V gösterir (telemetri sözlüğü "
               "silindi) — bu YALANCI bir disarm görüntüsüdür, ARM gerçeğini "
               "yalnız üst çubuktan ve motor sesinden doğrulayın.",
         geri="Kabloyu geri tak (bkz. B5).",
         tehlike="Kablo çıkıkken companion aracı DURDURAMAZ — servo/abort/disarm "
                 "komutlarının HİÇBİRİ gitmez. Tek durdurma yolu kumandadır."),

    dict(grup="B · Güç ve kablo", ad="B5 · USB kablosu geri takıldı",
         tetik="Kabloyu geri tak, iki süreyi not et: çıkarılı kaldığı süre ve "
               "'Pixhawk bağlandı' satırına kadar geçen süre.",
         beklenen="Kendiliğinden toparlar ama ANINDA DEĞİL — yeniden bağlanma "
                  "yalnız sıradaki zamanlanmış denemede olur (merdiven: 2, 5, 10, "
                  "30, 60 sn, sonra hep 60 sn). En kötü durumda 60 sn'ye kadar "
                  "gecikebilir.",
         gozle="'MAVLink bağlanıyor' ardından heartbeat akışının başlaması",
         geri="—",
         tehlike="Bu testten BAĞIMSIZ olarak companion açılışından ~5 sn sonra "
                 "PARAMS arıza uyarısı gelir (bkz. B3) — bu B4/B5'in belirtisi "
                 "DEĞİLDİR, parametreler yazılana kadar zaten böyle davranır."),

    dict(grup="B · Güç ve kablo", ad="B6 · YKİ ↔ FC RFD900A kesildi (araç-companion sağlam)",
         tetik="Companion'a ve Pixhawk USB'sine DOKUNMA. Yalnız YKİ tarafındaki "
               "RFD900A'yı çıkar YA DA araç tarafındaki RFD'nin gücünü/antenini "
               "kes (gerçek uçuşta olan ikincisidir).",
         beklenen="Görev/companion KESİNTİSİZ devam eder — görev beyni araçtadır, "
                  "companion↔FC USB bağı el değmemiştir. FC'de yine hiçbir şey "
                  "olmaz (FS_GCS_ENABLE=0).",
         gozle="~3 sn sonra YKİ üst çubuğunun TAMAMI kırmızı olur, buton "
               "'Bağlan'a döner, pil/mod/GPS '—' olur · Dashboard (yardımcı "
               "bilgisayar YOLU) YEŞİL KALIR — B4'ten (Pixhawk kablosu) AYIRAN "
               "TEK GÖSTERGE BUDUR: B4'te companion paneli kırmızı+üst çubuk "
               "yeşildi, B6'da tam tersi.",
         geri="Radyoyu geri tak, üst çubuktan BAĞLAN.",
         tehlike=""),

    # ── C. SENSÖR ve GÖREV ARIZALARI ─────────────────────────────────────
    dict(grup="C · Sensör", ad="C0 · ÖN KOŞUL — 'görev çalışıyor olmalı' diyen HER senaryo",
         tetik="Aşağıdaki C1-C4 senaryolarının hiçbiri GERÇEKTEN çalışan bir görevi "
               "tezgâhta gösteremez: görev önce kalkışı dener "
               "(_arm_and_takeoff), pervanesiz araç irtifa kazanamadığı için 60 "
               "sn sonra kalkış BAŞARISIZ olur ve görev döner — tarama/hizalama "
               "koduna HİÇ GİRİLMEZ. '▶ Görev Başlat (FCU)' düğmesine ASLA BASMA "
               "(bkz. B3) — o companion'ı atlayıp gerçek tırmanma gazı verir.",
         beklenen="Tezgâhta gerçekten gözlenebilen tek şey KALKIŞ-BAŞARISIZ "
                  "yoludur: 'KRİTİK: Kalkış irtifasına ulaşılamadı → araç güvene "
                  "alınıyor' ardından 'Kalkış başarısız: araç havada (irtifa=0.0) "
                  "→ RTL' — bu RTL de pervanesizde bir şey yapmaz, yalnız kip "
                  "değişimini gözleyin.",
         gozle="YKİ görev durumu ERROR'a döner, kip RTL'e geçer",
         geri="Görevi yeniden yükle.",
         tehlike="DISARM BEKLEMEYİN — ArduCopter gaz rampası %90'a çıkınca aracı "
                 "yerdeyken bile 'havada' sayar, otomatik disarm devreye GİRMEZ. "
                 "Motorlar tam gaza yakın döner; ACİL DISARM'a hazır olun."),

    dict(grup="C · Sensör", ad="C1 · GPS / EKF kaybı (DISARM, görev YOK)",
         tetik="Aracı AÇIK GÖKYÜZÜ altında aç (hangarda EKF kontrolleri hiç "
               "geçmezse ilgili mesaj hiç basılmaz), 3D fix bekle, sonra anteni "
               "metal kapla ört. DISARM KAL — bu test ARM gerektirmez.",
         beklenen="EKF/GPS sağlık alanı bozulur ve companion hız komutlarını "
                  "bloke etme mantığına girer (görev yokken gözlenemez, yalnız "
                  "sağlık alanı görülür). FC LOITER gibi GPS gerektiren kipleri "
                  "reddeder.",
         gozle="Sağlık uyarısı yalnız DEĞİŞİM ANINDA bir kez basılır — paneli "
               "örtmeden ÖNCE açık tutun · 'GPS Glitch or Compass error' "
               "STATUSTEXT · uydu sayısı düşer",
         geri="Anteni aç, 3D fix ve ≥6 uydu bekle.",
         tehlike="ARM etmeyin — EKF bozukken ARM etmek ayrı bir failsafe dalını açar."),

    dict(grup="C · Sensör", ad="C2 · Kamera akışı kesildi (yalnız donanım testi)",
         tetik="Görev tarafı (bildirim zinciri, RTL) tezgâhta ÜRETİLEMEZ — zorlamak "
               "için görev BAŞLATMAYIN. Tek güvenli yol: aracı KAPAT, CSI şerit "
               "kablosunu çıkar, tekrar aç. CANLI SÖKME YAPMA — CSI konnektörü "
               "sıcak takmaya uygun değil, kamerayı/portu kalıcı bozabilir.",
         beklenen="Kamera modülü görülmez/başlamaz; vision sağlık alanı bozulur. "
                  "Görev kodu bu duruma yalnız görev İÇİNDEYKEN bakar (tezgâhta "
                  "denenemez).",
         gozle="Dashboard'da video hiç gelmez · vision sağlık alanı FAILED",
         geri="Aracı kapat, kabloyu tak, tekrar aç.",
         tehlike=""),

    dict(grup="C · Sensör", ad="C3 · TOF halkası — yalnız panel testi (görev mantığı tezgâhta denenemez)",
         tetik="Halkanın karar mantığı (yetki, veto) yalnız Görev 2/3 KOŞARKEN "
               "çalışır — pervanesiz denenemez. Tezgâhta yapılabilecek: "
               "`systemctl stop ashina-tof` (sinyal işleyici yok, segment BAYAT "
               "kalır) YA DA halkanın I2C/besleme kablosunu çek (yayıncı yaşar, "
               "tüm slotlar veri vermeyi keser).",
         beklenen="YKİ TOF panelindeki değerler donuyor (stop) ya da '—' oluyor "
                  "(kablo). İkisi ayrı sonuç verir, ayrı gözlemleyin.",
         gozle="YKİ TOF panelinde değerler '—' ya da bayat/sabit kalır",
         geri="Yayıncıyı başlat / kabloyu tak.",
         tehlike=""),

    dict(grup="C · Sensör", ad="C4 · Hedef bulunamazsa — TEZGÂHTA TARAMA FAZI KOŞULAMAZ",
         tetik="Bu senaryonun tarama/zaman-aşımı dalı pervanesiz denenemez (bkz. "
               "C0). Tezgâhta doğrulanabilecek olan yalnız YKİ panelindeki "
               "sayılardır: scan_timeout_s ve mission_time_budget_s alanlarının "
               "doğru girildiğini gözle kontrol et.",
         beklenen="—",
         gozle="Görev panelindeki sayısal alanlar",
         geri="—",
         tehlike=""),

    # ── D. GÖREV (bir sonraki turda ikinci denetim gerekiyor) ────────────
    dict(grup="D · Görev", ad="Operatör görevi İPTAL etti",
         tetik="Görev sürerken YKİ'den görevi iptal et.",
         beklenen="Görev durur ve doğrulanmış RTL denenir; üç denemede olmazsa LAND'e "
                  "düşülür ve operatöre kritik uyarı gider.",
         gozle="'GÖREV İPTAL EDİLDİ' log satırı · kip RTL/LAND'e döner",
         geri="Araç indikten sonra yeni görev yüklenebilir.",
         tehlike="Pervanesiz araçta RTL/LAND anlamlı bir hareket üretmez; "
                 "kip değişimini gözlemek yeterlidir. Görev C0'daki gibi kalkış "
                 "aşamasında takılıysa iptal denemesi de o dalı test eder."),

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
    dict(grup="E · Yük", ad="Kavrayıcı aç / kapa (YÜKSÜZ)",
         tetik="YKİ'den kavrayıcıya 'kapat', sonra 'aç' komutu ver. YÜKLÜ ELLE "
               "KAPATMA TESTİ YAPMA — aşağıya bak.",
         beklenen="İKİ kapak da birlikte hareket eder ve ikisi de limit switch ile "
                  "DOĞRULANIR. Bir taraf tamamlanmazsa 2 kez daha denenir; yine "
                  "olmazsa hangi tarafın kaldığı bildirilir.",
         gozle="'grabber_left/right: close DOĞRULANDI (limit switch, N s)' log satırları "
               "· YKİ'de kavrayıcı durumu",
         geri="Karşı komutu ver.",
         tehlike="Parmaklarınızı kavrayıcı ağzından uzak tutun. YKİ'den ELLE "
                 "verilen 'kapat' komutu, Görev 3'ün aksine YÜK TUTMA TORKU "
                 "UYGULAMAZ (yalnız görev akışı bu korumayı devreye sokar) — "
                 "torksuz bırakılan kapak ~30 sn'de limitinden kayar. Bu yüzden "
                 "YKİ'den elle kapatılmış bir kavrayıcıya YÜK EMANET ETMEYİN."),

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
