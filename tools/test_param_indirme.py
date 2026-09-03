"""Eksik parametre tamamlama — davranış sınaması (regresyon).

NEDEN VAR: RFD900A (57600 baud) üzerinden tam parametre listesi HEP EKSİK
kalıyordu. İki sebep vardı ve ikisi de burada korunuyor:
  • İndirme 3 sn'lik SESSİZLİK tahminiyle "bitti" sayılıyordu; akış
    ortasında 3 sn boşluk bu linkte olağan.
  • Kaybolan PARAM_VALUE'lar hiç yeniden istenmiyordu — ArduPilot kayıp
    paketi KENDİLİĞİNDEN TEKRARLAMAZ, GCS sıra numarasıyla sormak zorunda.
Artık durma ölçütü SÜRE değil, İLERLEMENİN DURMASI.

Çalıştırma:  python3 tools/test_param_indirme.py   (çıkış 0 = temiz)
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/home/ash/ASHİNA IHA/YKI")
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from ui.widgets.panel_ortak import ParamDeposu
import time as _t

hata = 0
def bekle(ad, g, b):
    global hata
    ok = (g == b)
    if not ok: hata += 1
    print("  %-42s -> %-10s %s" % (ad, g, "OK" if ok else "KALDI (bekle %s)" % (b,)))

istenen = []
istek_sayaci = {"tam": 0}
d = ParamDeposu(istek_gonder=lambda: istek_sayaci.__setitem__("tam", istek_sayaci["tam"]+1),
                istek_yolu_denetle=lambda: True,
                tek_param_iste=lambda i: istenen.append(i))
d.SESSIZLIK_S = 0.0          # test hizlansin
bitti = []
d.indirme_bitti.connect(lambda n: bitti.append(n))

d.indir()
bekle("tam liste istegi gonderildi", istek_sayaci["tam"], 1)

# FC toplam 10 diyor; 0..6 geldi, 7/8/9 KAYIP
for i in range(7):
    d.ilerleme_geldi(i, 10); d.param_geldi("P%d"%i, float(i))
bekle("eksik sayisi", d.eksik_sayisi(), 3)

# Sessizlik denetimi -> BITMEMELI, eksikleri ISTEMELI
d._sessizlik_denetle()
bekle("erken 'bitti' demedi", bitti, [])
bekle("eksikler yeniden istendi", sorted(set(istenen)), [7,8,9])

# Eksikler gelince TAMAM demeli
for i in (7,8,9):
    d.ilerleme_geldi(i, 10); d.param_geldi("P%d"%i, float(i))
d._sessizlik_denetle()
bekle("hepsi gelince bitti", bitti, [10])
bekle("eksik kalmadi", d.eksik_sayisi(), 0)

# Hic yanit gelmezse: ilerlemesiz tur sinirinda DURMALI (sonsuz donmemeli)
istenen.clear(); bitti.clear()
d2 = ParamDeposu(istek_gonder=lambda: None, istek_yolu_denetle=lambda: True,
                 tek_param_iste=lambda i: istenen.append(i))
d2.SESSIZLIK_S = 0.0
d2.indirme_bitti.connect(lambda n: bitti.append(n))
d2.indir()
for i in range(5):
    d2.ilerleme_geldi(i, 8); d2.param_geldi("Q%d"%i, 1.0)
for _ in range(d2.ILERLEMESIZ_TUR_SINIRI + 2):
    d2._sessizlik_denetle()
bekle("ilerleme yoksa durdu", len(bitti) >= 1, True)
bekle("durmadan once tekrar denedi", len(istenen) > 0, True)

# Toplam bilinmiyorsa eski sessizlik olcutune dusmeli
d3 = ParamDeposu(istek_gonder=lambda: None, istek_yolu_denetle=lambda: True)
d3.SESSIZLIK_S = 0.0
b3 = []
d3.indirme_bitti.connect(lambda n: b3.append(n))
d3.indir(); d3.param_geldi("X", 1.0); d3._sessizlik_denetle()
bekle("toplam yoksa sessizlikle bitti", len(b3), 1)

print("SONUC:", "TEMIZ" if hata == 0 else "%d KALDI" % hata)
raise SystemExit(1 if hata else 0)
