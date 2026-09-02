"""
acilis_izleme.py — YKİ'nin açılış süresini ÖLÇER ve "hazırım" der.

NEDEN (operatör isteği, 2026-09-03):
Kiosk açılışında Plymouth teması 133 ms'de bitiyor (lightdm başlar başlamaz),
ardından Raspberry masaüstü görünüyor ve YKİ penceresi gelene kadar öyle
kalıyor. Operatör bu boşluğu görmek istemiyor; ayrıca yükleme çubuğunda
GERÇEK bir yüzde istiyor.

Yüzde uydurulamaz: "kaç saniye sürer" makineye, disk hızına, ağa göre değişir.
Bu modül HER AÇILIŞIN süresini kaydeder; açılış ekranı da geçmişin ORTANCASINI
kullanarak ilerlemeyi sürer. Yani yüzde ölçülmüş veriye dayanır, tahmine değil.

Sözleşme (açılış ekranı bunlara bakar):
  HAZIR_DOSYA  — YKİ ana penceresi gösterildiği anda yazılır (mtime = o an)
  SURE_DOSYA   — son N açılışın süreleri (saniye), JSON liste
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# NEDEN buraya: settings.json ile aynı dizin — kullanıcıya ait, yazılabilir,
# yeniden başlatmada korunur. /tmp OLMAZ: geçmiş her açılışta silinirdi ve
# yüzde asla öğrenemezdi.
try:
    from core.apppaths import app_data_dir
    _DIZIN = Path(app_data_dir())
except Exception:                                    # apppaths yoksa makul yedek
    _DIZIN = Path(os.path.expanduser("~/.local/share/ASHINA-YKI"))

HAZIR_DOSYA = _DIZIN / "yki_hazir"
SURE_DOSYA = _DIZIN / "acilis_sureleri.json"

# Kaç açılış saklansın — ortanca için yeterli, dosya küçük kalsın.
GECMIS_N = 15
# Hiç geçmiş yoksa kullanılacak ilk tahmin (s). İlk açılıştan sonra ölçüme döner.
VARSAYILAN_SURE_S = 20.0
# Makul olmayan ölçümler geçmişi bozmasın (ör. uyandırma, elle durdurma).
EN_KISA_S, EN_UZUN_S = 2.0, 180.0

_T0 = time.monotonic()


def sifirla_baslangic() -> None:
    """Süre sayacını şimdiye al (main.py en başta çağırır)."""
    global _T0
    _T0 = time.monotonic()


def _sureleri_oku() -> list:
    try:
        v = json.loads(SURE_DOSYA.read_text(encoding="utf-8"))
        return [float(x) for x in v if EN_KISA_S <= float(x) <= EN_UZUN_S]
    except Exception:
        return []


def beklenen_sure_s() -> float:
    """Açılışın NE KADAR SÜRECEĞİ — geçmiş ölçümlerin ortancası.

    Ortanca, ortalamadan iyi: tek bir yavaş açılış (ağ zaman aşımı gibi)
    tahmini kalıcı olarak şişirmesin.
    """
    g = _sureleri_oku()
    if not g:
        return VARSAYILAN_SURE_S
    g = sorted(g)
    n = len(g)
    return g[n // 2] if n % 2 else (g[n // 2 - 1] + g[n // 2]) / 2.0


def hazir_bildir() -> float:
    """Ana pencere GÖSTERİLDİ — işareti yaz, süreyi geçmişe ekle.

    Dönüş: ölçülen açılış süresi (s).
    """
    sure = time.monotonic() - _T0
    try:
        _DIZIN.mkdir(parents=True, exist_ok=True)
        # İşaret dosyası: içeriği değil MTIME'ı önemli — açılış ekranı
        # "kendi başlangıcımdan SONRA yazılmış mı" diye bakıyor, böylece
        # önceki açılıştan kalan bayat işaret onu yanıltmıyor.
        HAZIR_DOSYA.write_text(f"{time.time():.3f} {sure:.2f}\n", encoding="utf-8")

        if EN_KISA_S <= sure <= EN_UZUN_S:
            g = _sureleri_oku()
            g.append(round(sure, 2))
            g = g[-GECMIS_N:]
            gecici = SURE_DOSYA.with_suffix(".json.tmp")
            gecici.write_text(json.dumps(g), encoding="utf-8")
            os.replace(gecici, SURE_DOSYA)   # ATOMİK — yarım dosya kalmasın
        logger.info("YKİ hazır — açılış %.2f s (beklenen %.2f s)", sure, beklenen_sure_s())
    except Exception as exc:
        logger.warning("Açılış işareti yazılamadı: %s", exc)
    return sure


def hazir_mi(sonra: float) -> bool:
    """İşaret `sonra` (epoch) zamanından SONRA yazılmış mı?"""
    try:
        return HAZIR_DOSYA.stat().st_mtime >= sonra
    except Exception:
        return False
