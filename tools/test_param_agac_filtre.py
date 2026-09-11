"""Parametre ağacı — arama/filtre ve eksik-tamamlama davranış sınaması.

NEDEN VAR (operatör bildirimi 2026-09-04): "parametreyi yazıyorum çıkmıyor,
var olsa bile bulamıyorum; parametrelerin tamamı da inmiyor."

İki kök neden vardı ve ikisi de burada korunuyor:
  • Ağaç İKİ SEVİYELİ (kategori → parametre). QSortFilterProxyModel varsayılanı,
    ÜST satır eşleşmezse ALTINDAKİLERİ hiç değerlendirmez → arama pratikte
    yalnız kategori adlarında çalışıyordu. Düzeltme: recursive filtering.
  • Gelişmiş görünüm worker'ın `param_ilerleme` / `request_param_index`
    yeteneklerine bağlı DEĞİLDİ → eksik inen liste sessizce eksik kalıyordu.

Çalıştırma:  YKI/.venv/bin/python tools/test_param_agac_filtre.py   (çıkış 0 = temiz)
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from ui.views.advanced_view import AdvancedView  # noqa: E402

hata = 0


def bekle(ad, gercek, beklenen):
    global hata
    ok = gercek == beklenen
    if not ok:
        hata += 1
    print("  %-52s -> %-8s %s" % (ad, gercek, "OK" if ok else "KALDI (bekle %s)" % (beklenen,)))


def gorunen_parametreler(v):
    """Süzgeçten geçip görünen ÇOCUK (parametre) satırlarının adları."""
    out = []
    p = v._proxy_model
    for r in range(p.rowCount()):
        kat = p.index(r, 0)
        for c in range(p.rowCount(kat)):
            out.append(p.index(c, 0, kat).data())
    return sorted(x for x in out if x)


v = AdvancedView()

# Gerçekçi bir alt küme: farklı kategorilere düşen adlar
for ad, deg in [("TKOFF_THR_MAX", 0.9), ("TKOFF_SLEW_TIME", 2.0),
                ("MOT_THST_HOVER", 0.177), ("INS_HNTCH_REF", 0.193),
                ("FENCE_ENABLE", 0.0), ("BATT_LOW_VOLT", 21.2)]:
    v.update_param(ad, deg)

print("\n-- ARAMA (asıl şikâyet) --")
bekle("süzgeçsiz tüm parametreler görünüyor", len(gorunen_parametreler(v)), 6)

v._on_search("TKOFF")
bekle("'TKOFF' iki parametreyi buluyor",
      gorunen_parametreler(v), ["TKOFF_SLEW_TIME", "TKOFF_THR_MAX"])

v._on_search("hover")
bekle("küçük harf 'hover' MOT_THST_HOVER'ı buluyor",
      gorunen_parametreler(v), ["MOT_THST_HOVER"])

v._on_search("FENCE_ENABLE")
bekle("tam ad ile arama çalışıyor", gorunen_parametreler(v), ["FENCE_ENABLE"])

v._on_search("YOKBOYLEBIRSEY")
bekle("olmayan parametre 0 sonuç", gorunen_parametreler(v), [])

v._on_search("")
bekle("arama temizlenince hepsi geri geliyor", len(gorunen_parametreler(v)), 6)

print("\n-- EKSİK TAMAMLAMA --")
bekle("toplam bilinmeden eksik listesi boş", v._eksik_indeksler(), [])
bekle("toplam bilinmeden buton kapalı", v._btn_eksik.isEnabled(), False)

# FC "toplam 6" diyor; 0,1,2 geldi, 3/4/5 KAYIP
for i in range(3):
    v.on_param_progress(i, 6)
bekle("eksik indeksler doğru", v._eksik_indeksler(), [3, 4, 5])
bekle("eksik varken buton açık", v._btn_eksik.isEnabled(), True)
bekle("durum bandı EKSİK diyor", "EKSİK" in v._param_status.text(), True)

istenen = []
v.request_param_index.connect(lambda i: istenen.append(i))
v._on_request_missing()
bekle("eksikler sıra numarasıyla istendi", sorted(istenen), [3, 4, 5])

# Kalanlar gelince tamam demeli
for i in (3, 4, 5):
    v.on_param_progress(i, 6)
bekle("hepsi gelince eksik kalmadı", v._eksik_indeksler(), [])
bekle("hepsi gelince buton kapandı", v._btn_eksik.isEnabled(), False)
bekle("durum bandı TAM diyor", "TAM" in v._param_status.text(), True)

print("\nSONUC:", "TEMIZ" if hata == 0 else "%d KALDI" % hata)
raise SystemExit(1 if hata else 0)
