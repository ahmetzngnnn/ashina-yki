"""
core/yki_yeniden_baslat.py — YKİ'yi (yer istasyonu uygulamasını) yeniden başlatır
==================================================================================
Operatör talebi (2026-09-05): Dashboard'dan tek düğmeyle YKİ yeniden açılsın ve
ARTAKALAN süreçler temizlensin.

NEDEN AYRI/TRANSIENT BİR BİRİM (systemd-run) GEREKİYOR
------------------------------------------------------
YKİ, `ashina-yki.service` (systemd --user) altında koşar. Yeniden başlatmayı
yapan yardımcıyı uygulamanın kendisi doğrudan çatallarsa o yardımcı SERVİSİN
CGROUP'UNDA kalır — `systemctl --user stop` cgroup'un TAMAMINI öldürdüğü için
yardımcı da ölür ve servis bir daha AÇILMAZ. `systemd-run --user` ise ayrı,
geçici bir birim yaratır: servis durdurulunca yardımcı hayatta kalır ve
sırayla durdur → artıkları temizle → başlat adımlarını tamamlar.

NEDEN pkill DESENİNDE KÖŞELİ PARANTEZ
--------------------------------------
`pkill -f` TÜM komut satırında arar. Yardımcının kendi komut satırı da deseni
İÇERDİĞİ için desen düz yazılsaydı yardımcı KENDİNİ öldürürdü (servis bir daha
başlamazdı). `mai[n]` yazımı gerçek süreçle ("python main.py") eşleşir ama
yardımcının kendi satırıyla ("python mai[n].py") EŞLEŞMEZ.

SIRA ÖNEMLİ: önce servis durdurulur, SONRA artıklar temizlenir, EN SON servis
başlatılır — böylece taze örnek asla temizliğe yakalanmaz.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time

logger = logging.getLogger(__name__)

SERVIS = "ashina-yki"
# run.sh içinde `exec python main.py` çalıştığı için ana sürecin komut satırı
# tam olarak budur. Köşeli parantez için yukarıdaki NEDEN'e bakın.
_PKILL_DESENI = "python mai[n].py"

# Adımlar arası bekleme — servisin cgroup'u tamamen sönsün, port/kamera
# tutamakları bırakılsın diye. Toplam ~3 s.
_BEKLE_S = 1


def systemd_var() -> bool:
    return shutil.which("systemctl") is not None


def systemd_run_var() -> bool:
    return shutil.which("systemd-run") is not None


def kabuk_betigi() -> str:
    """Geçici birimde koşacak kabuk betiği (durdur → temizle → başlat)."""
    return (
        f"sleep {_BEKLE_S}; "
        f"systemctl --user stop {SERVIS}; "
        f"sleep {_BEKLE_S}; "
        f"pkill -u \"$(id -u)\" -f '{_PKILL_DESENI}' 2>/dev/null; "
        f"sleep {_BEKLE_S}; "
        f"systemctl --user start {SERVIS}"
    )


def komut_uret() -> list[str]:
    """Çalıştırılacak komutu üretir.

    systemd-run varsa artık temizliğini de yapan geçici birim; yoksa yalnızca
    servisi yeniden başlatan yalın komut (artık temizliği yapılamaz ama en
    azından uygulama yenilenir).
    """
    if systemd_run_var():
        return [
            "systemd-run", "--user", "--collect", "--quiet",
            f"--unit=ashina-yki-yeniden-{int(time.time())}",
            "bash", "-c", kabuk_betigi(),
        ]
    return ["systemctl", "--user", "restart", SERVIS]


def yeniden_baslat() -> tuple[bool, str]:
    """Yeniden başlatmayı tetikler. Dönüş: (başarılı_mı, operatöre mesaj).

    Başarılı olursa bu süreç birkaç saniye içinde ÖLDÜRÜLECEĞİ için çağıran
    tarafın dönüşe göre uzun iş yapmaması beklenir — mesaj yalnızca hata
    durumunda operatöre gösterilebilsin diye döner.
    """
    if not systemd_var():
        return False, ("systemctl bulunamadı — YKİ bu makinede systemd servisi "
                       "olarak çalışmıyor olabilir. Elle yeniden başlatın.")
    cmd = komut_uret()
    try:
        sonuc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except Exception as exc:
        logger.exception("YKİ yeniden başlatma komutu çalıştırılamadı")
        return False, f"Komut çalıştırılamadı: {exc}"

    if sonuc.returncode != 0:
        hata = (sonuc.stderr or sonuc.stdout or "").strip() or f"rc={sonuc.returncode}"
        logger.error("YKİ yeniden başlatma başarısız: %s", hata)
        return False, f"Yeniden başlatılamadı: {hata}"

    logger.warning("YKİ YENİDEN BAŞLATILIYOR (operatör talebi) — komut: %s",
                   " ".join(cmd))
    return True, "YKİ yeniden başlatılıyor — ekran birkaç saniye içinde kapanıp açılacak."
