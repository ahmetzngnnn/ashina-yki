"""
autotune_view.py — ArduCopter AUTOTUNE paneli.

NEDEN AYRI PANEL (2026-09-03 operatör isteği): AUTOTUNE'u eksen eksen
koşmak isteniyor. ArduPilot bunu AUTOTUNE_AXES bit maskesiyle destekler ama
maske elle hesaplanan bir sayıdır (Roll=1, Pitch=2, Yaw=4, YawD=8) ve
"PID Tuning" sekmesinde hiç görünmüyordu; operatör Mission Planner'a geçmek
zorunda kalıyordu. Panel maskeyi kutulardan üretir, değeri FC'ye yazar ve
adımları ekranda tutar.

NEDEN TEK EKSEN ÖNERİLİYOR: her eksen ayrı koşulduğunda uçuş süresi kısa
kalır (tek batarya), bir eksen bozulursa hangisinin bozduğu kesin bilinir ve
kötü sonuç yalnız o ekseni etkiler. ArduPilot hepsini birden de kabul eder.
"""
import logging

from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QFormLayout,
    QDoubleSpinBox, QPushButton, QCheckBox, QMessageBox, QTextEdit, QFrame,
)

from core import theme
from ui.widgets.panel_ortak import MavKanal, TekerlekKorumasi

logger = logging.getLogger(__name__)

# ArduCopter uçuş modu numarası. flight_modes_view.COPTER_MODLARI ile aynı
# kaynak: (15, "AUTOTUNE").
MOD_AUTOTUNE = 15

# AUTOTUNE_AXES bit maskesi — ArduPilot tanımı.
EKSENLER = [
    ("Roll",  1, "Yatış ekseni. Genelde ilk koşulan eksendir."),
    ("Pitch", 2, "Yunuslama ekseni."),
    ("Yaw",   4, "Sapma ekseni. Rüzgârda en çok bozulan eksen budur."),
    ("Yaw D", 8, "Sapma D terimi. ArduCopter 4.2+ ister; emin değilseniz kapalı bırakın."),
]

ADIMLAR = [
    "Aracı GENİŞ ve AÇIK bir alana götürün. AUTOTUNE agresif manevralar "
    "yapar; yerden en az 10 m, engellerden en az 20 m boşluk bırakın.",
    "Rüzgâr mümkün olduğunca DÜŞÜK olsun. Rüzgârlı havada AUTOTUNE ya "
    "başarısız olur ya da kötü kazanç üretir.",
    "Bu panelden ekseni seçip 'Parametreleri FC'ye Yaz'a basın.",
    "Kumandadan kalkış yapın, ALT_HOLD veya LOITER modunda 5-10 m'de "
    "dengeli askıda kalın.",
    "AUTOTUNE moduna geçin (kumandanın mod anahtarı ya da aşağıdaki düğme).",
    "Araç kendi kendine sert manevralar yapmaya başlayacak. Kumandaya "
    "DOKUNMAYIN — çubuğa dokunmak ayarı duraklatır.",
    "Araç yerinden kayarsa çubukla merkeze çekin, sonra bırakın; ayar "
    "kaldığı yerden sürer.",
    "FC 'AutoTune: Success' dediğinde ayar bitmiştir.",
    "KAYDETMEK İÇİN: AUTOTUNE modundayken indirin ve DISARM edin. Yeni "
    "kazançlar o anda kalıcı yazılır.",
    "Kaydetmeden vazgeçmek için: inmeden önce başka bir moda geçin ve "
    "orada disarm edin — eski kazançlar korunur.",
    "Bir eksen bitince bu paneli tekrar açıp SONRAKİ ekseni seçin.",
]


class AutoTuneView(QWidget):
    """Eksen eksen AUTOTUNE kurulum ve yönlendirme paneli."""

    send_command = pyqtSignal(int, list)
    set_param = pyqtSignal(str, float)
    request_params = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._armed = False
        self._kanal = MavKanal(
            komut_gonder=self.send_command.emit,
            param_yaz=self.set_param.emit,
            komut_yolu_denetle=lambda: self._signal_connected(self.send_command),
            param_yolu_denetle=lambda: self._signal_connected(self.set_param),
        )
        self._wheel_guard = TekerlekKorumasi(self)   # referans tutulmazsa GC'ye gider
        self._kutular: list[tuple[QCheckBox, int]] = []
        self._build_ui()
        self._durumu_tazele()

    # ── yardımcılar ──────────────────────────────────────────────────────
    def _signal_connected(self, signal) -> bool:
        try:
            return self.receivers(signal) > 0
        except (TypeError, RuntimeError):
            return False

    def _maske(self) -> int:
        return sum(bit for kutu, bit in self._kutular if kutu.isChecked())

    # ── arayüz ───────────────────────────────────────────────────────────
    def _build_ui(self):
        kok = QVBoxLayout(self)
        kok.setContentsMargins(10, 10, 10, 10)

        baslik = QLabel("AUTOTUNE — Eksen Eksen PID Otomatik Ayarı")
        baslik.setStyleSheet("font-size:16px; font-weight:600;")
        kok.addWidget(baslik)

        uyari = QLabel(
            "⚠ AUTOTUNE aracı SERT manevralara sokar ve mevcut PID değerlerini "
            "DEĞİŞTİRİR. Yarışma teknik kontrolünden SONRA çalıştırmayın — "
            "kontrol sonrası yazılım/parametre değişikliği eleme sebebidir."
        )
        uyari.setWordWrap(True)
        uyari.setStyleSheet(f"color:{theme.c('danger')}; font-weight:600;")
        kok.addWidget(uyari)

        orta = QHBoxLayout()

        # ── Sol: eksen ve ayar ───────────────────────────────────────────
        sol = QVBoxLayout()

        eksen_grp = QGroupBox("Ayarlanacak Eksen(ler)")
        eg = QVBoxLayout(eksen_grp)
        ipucu = QLabel(
            "ÖNERİ: tek seferde TEK eksen. Böylece uçuş kısa kalır, bozulan "
            "eksen kesin bilinir ve kötü sonuç yalnız o ekseni etkiler."
        )
        ipucu.setWordWrap(True)
        ipucu.setObjectName("mutedLabel")
        eg.addWidget(ipucu)
        self._maske_lbl = QLabel("")
        self._maske_lbl.setObjectName("mutedLabel")
        for ad, bit, aciklama in EKSENLER:
            kutu = QCheckBox(f"{ad}  (bit değeri {bit})")
            kutu.setToolTip(aciklama)
            kutu.toggled.connect(self._durumu_tazele)
            eg.addWidget(kutu)
            self._kutular.append((kutu, bit))
        # Varsayılan: yalnız Roll. Sinyal kapalı — _durumu_tazele arayüz tam
        # kurulmadan çalışmasın (etiket/düğmeler henüz yok).
        self._kutular[0][0].blockSignals(True)
        self._kutular[0][0].setChecked(True)
        self._kutular[0][0].blockSignals(False)
        eg.addWidget(self._maske_lbl)
        sol.addWidget(eksen_grp)

        ayar_grp = QGroupBox("Ayar Parametreleri")
        af = QFormLayout(ayar_grp)
        self._aggr = QDoubleSpinBox()
        self._aggr.setRange(0.05, 0.10)
        self._aggr.setSingleStep(0.005)
        self._aggr.setDecimals(3)
        self._aggr.setValue(0.075)
        self._aggr.setToolTip(
            "AUTOTUNE_AGGR — ayarın ne kadar agresif kazanç arayacağı.\n"
            "0.075 ArduPilot varsayılanı ve çoğu araç için doğrudur.\n"
            "Düşük değer daha yumuşak/güvenli, yüksek değer daha keskin tepki."
        )
        af.addRow("Agresiflik (AUTOTUNE_AGGR):", self._aggr)

        self._min_d = QDoubleSpinBox()
        self._min_d.setRange(0.0001, 0.0100)
        self._min_d.setSingleStep(0.0001)
        self._min_d.setDecimals(4)
        self._min_d.setValue(0.0005)
        self._min_d.setToolTip(
            "AUTOTUNE_MIN_D — aramanın ineceği en küçük D kazancı.\n"
            "Varsayılanı bırakın; yalnız titreşim sorunu yaşarsanız değiştirin."
        )
        af.addRow("Asgari D (AUTOTUNE_MIN_D):", self._min_d)
        sol.addWidget(ayar_grp)

        btn_row = QHBoxLayout()
        self._btn_yaz = QPushButton("Parametreleri FC'ye Yaz")
        self._btn_yaz.setObjectName("primaryButton")
        self._btn_yaz.setMinimumHeight(32)
        self._btn_yaz.clicked.connect(self._on_yaz)
        btn_row.addWidget(self._btn_yaz)

        self._btn_oku = QPushButton("FC'den OKU")
        self._btn_oku.setMinimumHeight(32)
        self._btn_oku.setToolTip(
            "FC'deki GERÇEK değerleri getirir. mavlink_worker'da tek parametre "
            "okuyan yol yok — bu düğme TÜM parametre listesini ister."
        )
        self._btn_oku.clicked.connect(self._on_oku)
        btn_row.addWidget(self._btn_oku)
        sol.addLayout(btn_row)

        self._btn_mod = QPushButton("AUTOTUNE Moduna Geç")
        self._btn_mod.setObjectName("dangerButton")
        self._btn_mod.setMinimumHeight(32)
        self._btn_mod.setToolTip(
            "Aracı AUTOTUNE uçuş moduna alır. YALNIZ araç havadayken ve "
            "dengeli askıdayken basın. Kumandanın mod anahtarı da aynı işi yapar."
        )
        self._btn_mod.clicked.connect(self._on_mod)
        sol.addWidget(self._btn_mod)

        self._neden_lbl = QLabel("")
        self._neden_lbl.setWordWrap(True)
        sol.addWidget(self._neden_lbl)
        sol.addStretch(1)
        orta.addLayout(sol, 1)

        # ── Sağ: adımlar ve FC mesajları ─────────────────────────────────
        sag = QVBoxLayout()
        adim_grp = QGroupBox("Adım Adım Ne Yapılacak")
        ag = QVBoxLayout(adim_grp)
        metin = QTextEdit()
        metin.setReadOnly(True)
        metin.setHtml(
            "<ol style='margin-left:-18px; line-height:1.5;'>"
            + "".join(f"<li>{a}</li>" for a in ADIMLAR)
            + "</ol>"
        )
        ag.addWidget(metin)
        sag.addWidget(adim_grp, 2)

        cizgi = QFrame()
        cizgi.setFrameShape(QFrame.Shape.HLine)
        sag.addWidget(cizgi)

        log_grp = QGroupBox("FC Mesajları (AUTOTUNE)")
        lg = QVBoxLayout(log_grp)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText(
            "AUTOTUNE sırasında FC'den gelen mesajlar burada görünür "
            "(AutoTune: Started / Success / Failed ...)."
        )
        lg.addWidget(self._log)
        sag.addWidget(log_grp, 1)
        orta.addLayout(sag, 1)

        kok.addLayout(orta)

    # ── durum ────────────────────────────────────────────────────────────
    def _durumu_tazele(self):
        maske = self._maske()
        adlar = [ad for (kutu, _b), (ad, _bit, _a) in zip(self._kutular, EKSENLER)
                 if kutu.isChecked()]
        self._maske_lbl.setText(
            f"AUTOTUNE_AXES = {maske}" + (f"  ({', '.join(adlar)})" if adlar else "  — eksen seçilmedi")
        )
        neden = self._kanal.neden_kapali(param_da_gerek=True)
        if not neden and maske == 0:
            neden = "En az bir eksen seçin."
        self._btn_yaz.setEnabled(not neden)

        mod_neden = self._kanal.neden_kapali()
        if not mod_neden and not self._armed:
            mod_neden = ("Araç ARM'lı DEĞİL — AUTOTUNE moduna yerde geçmeyin. "
                         "Önce kalkıp dengeli askıya alın.")
        self._btn_mod.setEnabled(not mod_neden)
        self._neden_lbl.setText(neden or mod_neden or "")
        self._neden_lbl.setStyleSheet(f"color:{theme.c('warning')};")

    # ── eylemler ─────────────────────────────────────────────────────────
    def _on_yaz(self):
        maske = self._maske()
        if maske == 0:
            return
        if self._kanal.neden_kapali(param_da_gerek=True):
            return
        yazildi = [
            self._kanal.param("AUTOTUNE_AXES", float(maske)),
            self._kanal.param("AUTOTUNE_AGGR", float(self._aggr.value())),
            self._kanal.param("AUTOTUNE_MIN_D", float(self._min_d.value())),
        ]
        if all(yazildi):
            self._log.append(
                f"[YAZILDI] AUTOTUNE_AXES={maske}, AGGR={self._aggr.value():.3f}, "
                f"MIN_D={self._min_d.value():.4f}")
            self._log.append(
                "[BİLGİ] Değerlerin FC'ye GERÇEKTEN yazıldığını 'FC'den OKU' "
                "ile doğrulayın.")
        else:
            self._log.append("[HATA] Parametreler yazılamadı — link/kanal yok.")

    def _on_oku(self):
        if self._signal_connected(self.request_params):
            self.request_params.emit()
            self._log.append("[OKU] Tüm parametre listesi istendi — FC yanıtı bekleniyor.")
        else:
            self._log.append("[HATA] Parametre okuma yolu bağlı değil.")

    def _on_mod(self):
        if self._kanal.neden_kapali() or not self._armed:
            return
        yanit = QMessageBox.question(
            self, "AUTOTUNE Moduna Geç",
            "Araç AUTOTUNE moduna alınacak.\n\n"
            "• Araç KENDİ KENDİNE sert manevralar yapmaya başlayacak.\n"
            "• Yalnız havadayken ve dengeli askıdayken onaylayın.\n"
            "• Kumanda her an kontrolü geri alabilir (mod anahtarı).\n\n"
            "Devam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if yanit != QMessageBox.StandardButton.Yes:
            return
        # MAV_CMD_DO_SET_MODE=176, base_mode=1 (CUSTOM_MODE_ENABLED), custom=15
        if self._kanal.komut(176, [1, MOD_AUTOTUNE, 0, 0, 0, 0, 0]):
            self._log.append("[MOD] AUTOTUNE moduna geçiş komutu gönderildi.")
        else:
            self._log.append("[HATA] Mod komutu gönderilemedi (link yok).")

    # ── dış girdiler ─────────────────────────────────────────────────────
    @pyqtSlot(bool)
    def on_connection_changed(self, bagli: bool) -> None:
        self._kanal.link_ayarla(bagli)
        self._durumu_tazele()

    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        armed = bool(data.get("armed", False))
        if armed != self._armed:
            self._armed = armed
            self._durumu_tazele()

    @pyqtSlot(str, int)
    def on_status_text(self, text: str, severity: int) -> None:
        if not text:
            return
        if "autotune" in text.lower():
            self._log.append(f"[FC] {text.strip()}")

    @pyqtSlot(str, float)
    def on_param_received(self, ad: str, deger: float) -> None:
        """FC'den gelen GERÇEK değerleri kutulara yansıtır."""
        if ad == "AUTOTUNE_AXES":
            maske = int(deger)
            for kutu, bit in self._kutular:
                kutu.blockSignals(True)
                kutu.setChecked(bool(maske & bit))
                kutu.blockSignals(False)
            self._durumu_tazele()
            self._log.append(f"[FC] AUTOTUNE_AXES = {maske}")
        elif ad == "AUTOTUNE_AGGR":
            self._aggr.setValue(max(self._aggr.minimum(),
                                    min(self._aggr.maximum(), float(deger))))
            self._log.append(f"[FC] AUTOTUNE_AGGR = {deger:.3f}")
        elif ad == "AUTOTUNE_MIN_D":
            self._min_d.setValue(max(self._min_d.minimum(),
                                     min(self._min_d.maximum(), float(deger))))
            self._log.append(f"[FC] AUTOTUNE_MIN_D = {deger:.4f}")
