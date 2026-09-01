import logging
from PyQt6.QtWidgets import QWidget, QGridLayout, QLabel, QVBoxLayout, QFrame, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QColor

from core import theme

logger = logging.getLogger(__name__)

# Sensör köşe renkleri — SENSÖR SEMANTİĞİ (her köşe kendi rengiyle anılır,
# sahada kablo/etiket rengiyle eşleşir): temadan bağımsız sabit kalır.
C_WHITE  = "rgb(255, 255, 255)"
C_RED    = "rgb(204, 25, 25)"
C_GREEN  = "rgb(25, 204, 25)"
C_BLUE   = "rgb(51, 102, 230)"
C_YELLOW = "rgb(204, 204, 25)"

class TofWidget(QFrame):
    """
    Görev 3 (Yük Alma) TOF halkasını görselleştirir — 5×VL53L1X, ARTI düzeni.

    2026-08-31 GÜNCELLEMESİ — panel ÖLÜYDÜ ve yanlış geometriyi çiziyordu:
      * Widget 4 KÖŞE sensörü (L1/L2/L4/L5) ve 240 mm beklenen mesafe
        varsayıyordu. Araçta böyle bir düzen YOK: 5 sensör ARTI düzeninde
        (merkez + ön/arka/sağ/sol, gövde merkezinden ±42.5 mm) ve temas
        anında sensör düzlemi zeminden 60 mm yukarıda.
      * Ayrıca zincir KOPUKTU: görev yalnız `tof_dist_mm` (merkez) yayınlıyor,
        status_server ise `tof_distances` anahtarını arıyordu — o anahtarı
        KİMSE doldurmuyordu. Panel hiç veri almıyordu.

    Artık görev 5 slotun tamamını + geometri sabitlerini gönderiyor; panel
    kendi kopyasını TUTMAZ (renk bantlarında yaşanan "üç kopya tuzağı"
    burada tekrarlanmasın diye eşikler telden gelir).

    OKUMA SEMANTİĞİ: sensör yükü görüyorsa okuma ZEMİNDEN yük yüksekliği
    kadar KISADIR (basamak ≈ 23 mm). Yani "kısa okuyan kenar" yükün olduğu
    yönü gösterir ve araç O YÖNE gitmelidir.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setObjectName("tofWidget")

        # Geometri TELDEN gelir (görev yayınlar); bunlar yalnız ilk kare
        # için makul varsayılanlar — tuning.py ile aynı değerler.
        self.temas_mm = 60.0        # temasta sensör düzleminin zeminden yüksekliği
        self.basamak_mm = 23.0      # yük kalınlığı = zemin/yük okuma farkı
        self.gecerli_min_mm = 40.0  # VL53L1X ölü bölgesi
        self.gecerli_maks_mm = 4000.0
        self.halka_acik = True
        # Son durum metni/token'ı — tema değişince aynı durum yeniden boyanır.
        self._status_token = "accent"

        self._build_ui()
        self._apply_theme_colors()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Durum Başlığı
        self.lbl_status = QLabel("[ DURUM MAKİNESİ ]\nBEKLENİYOR")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.lbl_status)

        # 3x3 Grid
        self.grid = QGridLayout()
        self.grid.setSpacing(5)

        # Sensör renkleri modül düzeyinde (semantik sabit); boş hücre zemini
        # temadan gelir — koyu temada koyu, açık temada açık olmalı.
        self.C_WHITE = C_WHITE
        self.C_RED = C_RED
        self.C_GREEN = C_GREEN
        self.C_BLUE = C_BLUE
        self.C_YELLOW = C_YELLOW

        self.labels = {}
        # Hücrelerin son (metin, renk) durumu — tema değişince yeniden çizilir.
        self._cell_state: dict = {}
        for row in range(3):
            for col in range(3):
                lbl = QLabel()
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setMinimumSize(60, 60)
                lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                self.grid.addWidget(lbl, row, col)
                self.labels[(row, col)] = lbl

        # ARTI düzeni: merkez + ön/arka/sağ/sol (gövde çerçevesi).
        # ÖN = aracın burnu yönü; ızgarada yukarı.
        self._set_cell(0, 1, "ÖN\nBekleniyor...", None)
        self._set_cell(1, 0, "SOL\nBekleniyor...", None)
        self._set_cell(1, 1, "MERKEZ\nBekleniyor...", None)
        self._set_cell(1, 2, "SAĞ\nBekleniyor...", None)
        self._set_cell(2, 1, "ARKA\nBekleniyor...", None)

        main_layout.addLayout(self.grid)

    @property
    def C_BLACK(self) -> str:
        """Boş hücre zemini — 'siyah' değil, aktif temanın kuyu rengi.

        Eski sabit koyu ton açık temada kara delik gibi duruyordu; çağıran
        kod (`_set_cell(..., self.C_BLACK)`) değişmeden temaya uyar."""
        return theme.c("well")

    def _set_cell(self, row, col, text, bg_color, is_target=False):
        lbl = self.labels[(row, col)]
        lbl.setText(text)
        # bg_color None → boş/nötr hücre (tema zemini, tema metni)
        self._cell_state[(row, col)] = (text, bg_color)
        if bg_color is None or bg_color == self.C_BLACK:
            lbl.setStyleSheet(
                f"background-color:{theme.c('well')}; color:{theme.c('text_muted')};"
                f"font-weight:bold; font-size:10px; border:1px solid {theme.c('border')};"
            )
            return
        # Sensör renkleri doygun ve koyu — üzerinde beyaz metin her iki temada okunur.
        text_color = "black" if bg_color == self.C_WHITE else "white"
        lbl.setStyleSheet(
            f"background-color:{bg_color}; color:{text_color};"
            f"font-weight:bold; font-size:10px; border:1px solid {theme.c('border_strong')};"
        )

    def _apply_theme_colors(self) -> None:
        """Tema değişince: kapsayıcı, durum metni ve tüm hücreler yeniden boyanır."""
        self.setStyleSheet(
            f"QFrame#tofWidget {{ background-color:{theme.c('panel')};"
            f"border:1px solid {theme.c('border')}; border-radius:6px; }}"
        )
        self.lbl_status.setStyleSheet(
            f"color:{theme.c(self._status_token)}; font-size:14px; font-weight:bold;"
        )
        for (row, col), (text, bg) in list(self._cell_state.items()):
            self._set_cell(row, col, text, bg)

    def _set_status(self, text: str, token: str) -> None:
        self._status_token = token
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(
            f"color:{theme.c(token)}; font-size:14px; font-weight:bold;"
        )

    @pyqtSlot(dict)
    def update_tof_data(self, data: dict):
        """5 slotlu TOF halkasını çiz.

        Beklenen veri (görev yayınlar — teknofest_task2.live_status):
            {"merkez","on","arka","sag","sol": mm | None,
             "temas_mm","basamak_mm","gecerli_min_mm","gecerli_maks_mm",
             "halka_acik"}

        RENK SEMANTİĞİ:
          beyaz : sensör YÜKÜ görüyor (okuma zeminden ≥ yarım basamak kısa)
          mavi  : sensör ZEMİNİ görüyor (yük o yönde değil)
          sarı  : okuma menzil dışı — ölü bölge (<40 mm) ya da >4 m
          gri   : veri yok

        Kısa okuyan kenar yükün YÖNÜNÜ gösterir; araç o yöne kaymalıdır.
        """
        if not isinstance(data, dict) or not data:
            self._set_status("VERİ YOK\nGörev 3 çalışmıyor olabilir", "text_muted")
            return

        # Geometri TELDEN gelir — panel kendi kopyasını tutmaz (renk
        # bantlarında yaşanan "üç kopya tuzağı" burada tekrarlanmasın).
        # dict.get(k, varsayılan) anahtar VARSA ama değeri None ise varsayılanı
        # KULLANMAZ → float(None) TypeError. Firmware bugün hep float
        # gönderiyor (teknofest_task2.live_status), ama bu bir EKRAN yolu:
        # kısmi/bozuk bir mesaj paneli Qt slot'unun içinde düşürmemeli —
        # üstelik tam da halka arızalıyken, yani operatörün panele en çok
        # ihtiyaç duyduğu anda. Sayıya çevrilemeyen değer = "gelmedi".
        def _sayi(anahtar, mevcut):
            v = data.get(anahtar)
            try:
                return float(v) if v is not None else float(mevcut)
            except (TypeError, ValueError):
                return float(mevcut)

        self.temas_mm = _sayi("temas_mm", self.temas_mm)
        self.basamak_mm = _sayi("basamak_mm", self.basamak_mm)
        self.gecerli_min_mm = _sayi("gecerli_min_mm", self.gecerli_min_mm)
        self.gecerli_maks_mm = _sayi("gecerli_maks_mm", self.gecerli_maks_mm)
        self.halka_acik = bool(data.get("halka_acik", self.halka_acik))

        yerler = {"on": (0, 1, "ÖN"), "sol": (1, 0, "SOL"),
                  "merkez": (1, 1, "MERKEZ"), "sag": (1, 2, "SAĞ"),
                  "arka": (2, 1, "ARKA")}
        okuma = {k: data.get(k) for k in yerler}

        def _gecerli(v):
            return (isinstance(v, (int, float)) and v
                    and self.gecerli_min_mm <= float(v) <= self.gecerli_maks_mm)

        # Zemin referansı: KENARLARIN EN BÜYÜĞÜ. Yükü gören sensör KISA okur,
        # dolayısıyla en uzun geçerli kenar okuması zemini temsil eder
        # (firmware'de tof_ring._zemin_referansi ile AYNI kural).
        kenar = [okuma[k] for k in ("on", "arka", "sag", "sol")]
        gec = [float(v) for v in kenar if _gecerli(v)]
        zemin = max(gec) if gec else None
        esik = (zemin - self.basamak_mm * 0.5) if zemin is not None else None

        for r in range(3):
            for c in range(3):
                self._set_cell(r, c, "", self.C_BLACK)

        goren = []
        for ad, (r, c, etiket) in yerler.items():
            v = okuma[ad]
            if not isinstance(v, (int, float)) or not v:
                self._set_cell(r, c, f"{etiket}\nveri yok", None)
                continue
            v = float(v)
            if not _gecerli(v):
                self._set_cell(r, c, f"{etiket}\n{v:.0f} mm\nmenzil dışı",
                               self.C_YELLOW)
                continue
            yuk = esik is not None and v < esik
            if yuk and ad != "merkez":
                goren.append(etiket)
            self._set_cell(r, c, f"{etiket}\n{v:.0f} mm",
                           self.C_WHITE if yuk else self.C_BLUE)

        if not self.halka_acik:
            self._set_status("HALKA KAPALI\nsalt gözlem — kamera yolu sürüyor",
                             "text_muted")
            return
        if zemin is None:
            self._set_status("GEÇERLİ OKUMA YOK\nsensör/menzil kontrolü", "danger")
            return

        yukseklik = zemin - self.temas_mm
        mv = okuma["merkez"]
        merkez_yuk = (_gecerli(mv) and esik is not None and float(mv) < esik)
        # TEMAS ANI: zemin temas yüksekliğine yaklaşınca yük ÜSTÜ sensörün
        # ölü bölgesine (<40 mm) düşer. Firmware bunu ARIZA değil YÜK DELİLİ
        # sayar (tof_ring: kor_yuk_say). Panel de aynı semantiği kullanmalı,
        # yoksa en kritik anda "yük görünmüyor" yazıp operatörü yanıltır.
        _kor_beklenen = (zemin - self.basamak_mm) < self.gecerli_min_mm
        merkez_kor = (_kor_beklenen and isinstance(mv, (int, float)) and mv
                      and float(mv) < self.gecerli_min_mm)
        if merkez_kor:
            self._set_status(
                f"TEMAS\nmerkez ölü bölgede — yük altta ({float(mv):.0f} mm)",
                "success")
            return
        if goren:
            self._set_status("KAYDIR → " + "+".join(goren)
                             + f"\nzeminden {yukseklik:+.0f} mm", "accent")
        elif merkez_yuk:
            self._set_status(f"MERKEZDE\nyük altta · zeminden {yukseklik:+.0f} mm",
                             "success")
        else:
            self._set_status(f"YÜK GÖRÜNMÜYOR\nzeminden {yukseklik:+.0f} mm",
                             "warning")
