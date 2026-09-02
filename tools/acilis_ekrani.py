#!/usr/bin/env python3
"""
acilis_ekrani.py — YKİ açılış ekranı (kiosk boşluğunu kapatır).

NEDEN (operatör isteği, 2026-09-03):
Kiosk açılışında Plymouth teması 133 ms'de bitiyor (lightdm başlar başlamaz,
ölçüldü: plymouth-quit-wait 133ms) ve YKİ penceresi gelene kadar Raspberry
MASAÜSTÜ görünüyor. Plymouth'u uzatmak çözüm değil — o, kullanıcı oturumu
başlamadan biter. Bu boşluğu ancak oturum içinde çalışan tam ekran bir
pencere kapatabilir.

YÜZDE NEREDEN GELİYOR: uydurulmuyor. core/acilis_izleme.py her açılışın
GERÇEK süresini kaydediyor; buradaki ilerleme geçmişin ORTANCASINA göre
sürülüyor. İlk açılışta geçmiş yok → varsayılan tahmin, sonraki açılışlarda
makinenin kendi ölçümü.

DÜRÜSTLÜK KURALI: yüzde, YKİ gerçekten hazır olmadan %100 GÖSTERMEZ.
Tahmin dolduğunda %95'te bekler ("son hazırlıklar"), çünkü %100 gösterip
beklemek operatöre yalan söylemektir.

Kapanma: YKİ ana penceresini gösterdiğinde core/acilis_izleme.hazir_bildir()
işaret dosyasını yazar; bu ekran onu görüp kapanır. İşaret hiç gelmezse
AZAMI_BEKLEME sonunda yine de kapanır — açılış ekranı ekranı ESİR ALAMAZ.
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

_KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_KOK))

from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

# İşaret hiç gelmezse ekranı bırak (s). YKİ çökse bile masaüstüne dönülmeli.
AZAMI_BEKLEME_S = 150.0
# Tahmin dolduğunda takılacağımız tavan — %100 yalnız GERÇEK hazır sinyaliyle.
TAVAN_YUZDE = 95.0

LOGO_ADAYLARI = (
    "/usr/share/plymouth/themes/ashina/logo.png",
    str(Path.home() / "ASHINA_YKI/assets/logo.png"),
    str(_KOK / "assets/logo.png"),
)


def _palet() -> dict:
    """Renkleri YKİ'nin KENDİ temasından al — açılıştan uygulamaya geçiş
    görsel olarak kopmasın. Tema okunamazsa ölçülü bir yedek kullanılır."""
    try:
        from core import theme
        p = theme.palette(theme.DARK)
        return {
            "zemin":  p.get("bg", "#0d1117"),
            "yazi":   p.get("text", "#e6edf3"),
            "soluk":  p.get("text_muted", "#8996a3"),
            "vurgu":  p.get("accent", p.get("success", "#40c463")),
            "cizgi":  p.get("border", "#232a33"),
        }
    except Exception:
        return {"zemin": "#0d1117", "yazi": "#e6edf3", "soluk": "#8996a3",
                "vurgu": "#4f9d69", "cizgi": "#232a33"}


class AcilisEkrani(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._renk = _palet()
        self._t0 = time.monotonic()
        self._baslangic_epoch = time.time()
        self._aci = 0.0
        self._yuzde = 0.0
        self._hazir = False
        self._kapaniyor = False

        try:
            from core import acilis_izleme
            self._izleme = acilis_izleme
            self._beklenen = max(3.0, acilis_izleme.beklenen_sure_s())
        except Exception:
            self._izleme = None
            self._beklenen = 20.0

        self._logo = QPixmap()
        for y in LOGO_ADAYLARI:
            if os.path.exists(y):
                self._logo = QPixmap(y)
                if not self._logo.isNull():
                    break

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool          # görev çubuğunda görünmesin
        )
        self.setWindowTitle("ASHİNA YKİ")
        self.setCursor(Qt.CursorShape.BlankCursor)

        # 25 Hz: dönen yay akıcı görünsün ama CPU'yu YKİ'den çalmasın —
        # açılış sırasında CPU en kıymetli anında.
        self._zamanlayici = QTimer(self)
        self._zamanlayici.timeout.connect(self._tik)
        self._zamanlayici.start(40)

    # ── döngü ──────────────────────────────────────────────────────────────
    def _tik(self) -> None:
        gecen = time.monotonic() - self._t0
        self._aci = (self._aci + 4.2) % 360.0
        # ASHINA_SPLASH_DEBUG=1 → her ~2 s'de bir durum bas. Kalıcı teşhis:
        # açılış ekranı kapanmazsa (yaşandı) sorunun timer'da mı, işaret
        # algısında mı olduğunu ayırt etmenin tek yolu bu.
        self._tik_say = getattr(self, "_tik_say", 0) + 1
        if os.environ.get("ASHINA_SPLASH_DEBUG") == "1" and self._tik_say % 50 == 0:
            m = -1.0
            try:
                m = self._izleme.HAZIR_DOSYA.stat().st_mtime if self._izleme else -2.0
            except Exception as _e:
                m = -3.0
            print(f"[splash] tik={self._tik_say} gecen={gecen:.1f} yuzde={self._yuzde:.0f} "
                  f"hazir={self._hazir} izleme={'VAR' if self._izleme else 'YOK'} "
                  f"baslangic={self._baslangic_epoch:.3f} isaret_mtime={m:.3f}",
                  file=sys.stderr, flush=True)

        if not self._hazir and self._izleme is not None:
            if self._izleme.hazir_mi(self._baslangic_epoch):
                self._hazir = True

        if self._hazir:
            self._yuzde = min(100.0, self._yuzde + 3.5)     # %100'e yumuşak koş
            if self._yuzde >= 100.0 and not self._kapaniyor:
                self._kapaniyor = True
                QTimer.singleShot(450, self._kapat)         # bir an "tamam" göster
        else:
            hedef = min(TAVAN_YUZDE, 100.0 * gecen / self._beklenen)
            self._yuzde = max(self._yuzde, hedef)           # ASLA geri gitme

        if gecen > AZAMI_BEKLEME_S and not self._kapaniyor:
            self._kapaniyor = True
            self._kapat()
        self.update()

    def _kapat(self) -> None:
        """Pencereyi kapat VE uygulamadan çık.

        NEDEN quit() ŞART (2026-09-03, teşhisle bulundu): pencere
        Qt.WindowType.Tool ile oluşturuluyor ve Qt, Tool pencerelerini
        "birincil pencere" SAYMIYOR. Dolayısıyla quitOnLastWindowClosed
        tetiklenmiyor: close() pencereyi gizliyor ama app.exec() hiç dönmüyor
        ve süreç sonsuza kadar yaşıyor. Ölçüldü — hazir=True, yuzde=100
        olmasına rağmen splash süreci ayakta kalıyordu ve masaüstünü
        kapatmaya devam ediyordu.
        """
        try:
            self.close()
        finally:
            app = QApplication.instance()
            if app is not None:
                app.quit()

    # ── çizim ──────────────────────────────────────────────────────────────
    def paintEvent(self, _evt) -> None:  # noqa: N802
        r = self._renk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(r["zemin"]))

        merkez_y = int(h * 0.42)

        # logo
        if not self._logo.isNull():
            en = min(int(w * 0.22), 420)
            lg = self._logo.scaledToWidth(
                en, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((w - lg.width()) // 2,
                         merkez_y - lg.height() // 2, lg)

        # ── ince halka + dönen yay ────────────────────────────────────────
        cap = min(int(w * 0.085), 132)
        hy = merkez_y + int(min(h * 0.20, 230))
        kutu = QRectF((w - cap) / 2.0, hy - cap / 2.0, float(cap), float(cap))

        p.setPen(QPen(QColor(r["cizgi"]), 3))
        p.drawEllipse(kutu)

        # ilerleme yayı — saat yönünde, 12'den başlar
        kalem = QPen(QColor(r["vurgu"]), 3)
        kalem.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(kalem)
        p.drawArc(kutu, 90 * 16, int(-self._yuzde / 100.0 * 360 * 16))

        # dönen ince iz — "çalışıyor" hissi (ilerleme durduğunda da döner)
        iz = QPen(QColor(r["vurgu"]), 2)
        iz.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(iz)
        dis = kutu.adjusted(-9, -9, 9, 9)
        p.drawArc(dis, int(-self._aci * 16), 46 * 16)

        # ortadaki yüzde
        f = QFont(self.font()); f.setPointSize(max(13, cap // 5)); f.setBold(True)
        p.setFont(f); p.setPen(QColor(r["yazi"]))
        p.drawText(kutu, Qt.AlignmentFlag.AlignCenter, f"%{int(self._yuzde)}")

        # ── metinler ──────────────────────────────────────────────────────
        f2 = QFont(self.font()); f2.setPointSize(15)
        p.setFont(f2); p.setPen(QColor(r["yazi"]))
        p.drawText(0, hy + cap, w, 46, Qt.AlignmentFlag.AlignHCenter,
                   "ASHİNA Yer Kontrol İstasyonu Yükleniyor")

        f3 = QFont(self.font()); f3.setPointSize(10)
        p.setFont(f3); p.setPen(QColor(r["soluk"]))
        if self._hazir:
            alt = "Hazır"
        elif self._yuzde >= TAVAN_YUZDE:
            alt = "Son hazırlıklar…"
        else:
            kalan = max(0.0, self._beklenen - (time.monotonic() - self._t0))
            alt = f"yaklaşık {kalan:.0f} sn"
        p.drawText(0, hy + cap + 44, w, 26, Qt.AlignmentFlag.AlignHCenter, alt)
        p.end()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        # Kaçış kapısı: takılırsa operatör ESC ile geçebilsin.
        if e.key() == Qt.Key.Key_Escape:
            self._kapat()


def main() -> int:
    app = QApplication(sys.argv)
    e = AcilisEkrani()
    e.showFullScreen()
    e.raise_()
    e.activateWindow()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
