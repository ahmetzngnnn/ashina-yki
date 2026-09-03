"""
ui/views/preflight_check_view.py — Uçuş Öncesi FC Parametre Denetimi
=====================================================================

İHA'nın (companion) görev başlatmadan önce yaptığı `fc_params_expected`
doğrulamasının YKİ'deki CANLI karşılığı: beklenen değerlerle FC'deki gerçek
değerleri yan yana koyar, uyumsuzları vurgular ve (operatör AÇIKÇA isterse)
doğru değeri yazıp GERİ OKUYARAK doğrular.

NEDEN bu panel gerekli:
IHA/missions/executor.py sağlık kapısı, fc_params_expected uyuşmazsa görevi
BAŞLATMAZ. Bugüne kadar operatörün elinde "hangi parametre neden uyuşmuyor"
sorusunu SAHADA yanıtlayacak bir araç yoktu — companion yalnızca "PARAMS=FAILED"
diyordu, düzeltme Mission Planner'da elle aranıyordu. Bu panel aynı listeyi
YKİ'de gösterir.

NEDEN liste burada KOPYA:
Bu depo (YKİ) ile İHA deposu AYRIDIR; ortak import YOKTUR (proje kuralı). Bu
yüzden `VARSAYILAN_BEKLENEN` aşağıda İHA'daki config.json'dan ELLE kopyalanmış
bir anlık görüntüdür. Kopyanın bayatlaması gerçek bir risk olduğu için panel
"İHA config.json'dan YÜKLE" düğmesiyle listeyi doğrudan asıl kaynaktan okuyabilir
ve seçilen yolu hatırlar. Ekranda hangi kaynağın kullanıldığı HER ZAMAN yazılıdır.

ŞARTNAME sınıfı:
FS_THR_ENABLE / FS_GCS_ENABLE / FS_GCS_TIMEOUT / BATT_FS_LOW_ACT / BATT_FS_CRT_ACT
şartname ve dokümante edilmiş arıza senaryolarından gelir (bkz. IHA/config.json
içindeki _comment_fs_thr, _comment_fs_gcs, _comment_batt_fs). Bunlar AYRI
vurgulanır: uyumsuzlukları yalnızca "ayar farkı" değil, UÇUŞ İZNİ sorunudur.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from core import theme
from core.apppaths import app_data_dir
from ui.widgets.panel_ortak import (
    MavKanal, ParamDeposu, ParamYazici, param_esit,
)

logger = logging.getLogger(__name__)

# Sınıflar
SINIF_SARTNAME = "ŞARTNAME"
SINIF_KRITIK = "KRİTİK"
SINIF_DONANIM = "DONANIM"

# ── Beklenen parametreler — IHA/config.json::fc_params_expected KOPYASI ────
# Kopya tarihi: 2026-09-02 (bu dosyanın yazıldığı gün). Değerler aynen alındı;
# yorumlar özetlendi. Kaynak değişirse "İHA config.json'dan YÜKLE" ile tazeleyin.
VARSAYILAN_BEKLENEN: list[tuple[str, float, str, str]] = [
    # (parametre, beklenen, sınıf, not)
    ("FRAME_CLASS",     1,     SINIF_DONANIM,
     "1 = Quad. Yanlışsa motor karışımı komple yanlış olur."),
    ("FRAME_TYPE",      1,     SINIF_DONANIM,
     "1 = X. Motor test sırası (A/B/C/D) buna bağlıdır."),
    ("SERVO1_FUNCTION", 33,    SINIF_DONANIM, "Motor 1 — fiziksel olarak doğrulandı (2026-08-25)."),
    ("SERVO2_FUNCTION", 34,    SINIF_DONANIM, "Motor 2."),
    ("SERVO3_FUNCTION", 35,    SINIF_DONANIM, "Motor 3."),
    ("SERVO4_FUNCTION", 36,    SINIF_DONANIM, "Motor 4."),
    ("MOT_PWM_TYPE",    0,     SINIF_DONANIM, "0 = normal PWM."),
    ("SERVO5_FUNCTION", 0,     SINIF_KRITIK,
     "Eski gimbal kanalı — gimbal 2026-08-30'da SÖKÜLDÜ. 2026-09-03: RCIN6 "
     "passthrough KAPATILDI, ölü kanalda RC yolu açık bırakmanın sebebi yok."),
    ("SERVO6_FUNCTION", 0,     SINIF_KRITIK,
     "KIRMIZI yük bırakma. 2026-09-03 OPERATÖR KARARI: eskiden 57 (RCIN7) idi, "
     "yani kumandanın Aux3 anahtarı kapağı DOĞRUDAN sürüyordu. Artık yalnızca "
     "uçuş yazılımı (DO_SET_SERVO). Manuel acil bırakma yedeği KALDIRILDI."),
    ("SERVO7_FUNCTION", 0,     SINIF_KRITIK,
     "MAVİ yük bırakma. Eskiden 58 (RCIN8) — kumandanın Aux4 anahtarı. "
     "Bkz. SERVO6_FUNCTION."),
    ("SERVO8_FUNCTION", 0,     SINIF_DONANIM,
     "Kavrayıcı sol — sürekli dönüş servosu, RC passthrough KAPALI olmalı."),
    ("SERVO9_FUNCTION", 0,     SINIF_DONANIM, "Kavrayıcı sağ — aynı gerekçe."),
    ("BRD_SAFETY_MASK", 16368, SINIF_KRITIK,
     "bit0-3 temiz → motorlar emniyet düğmesine UYAR; bit4-13 set → servo/yük "
     "kanalları düğmeyi bypass eder (yerde test edilebilir)."),
    ("CAN_P1_DRIVER",   1,     SINIF_DONANIM, "DroneCAN sürücüsü (GPS)."),
    ("GPS1_TYPE",       9,     SINIF_DONANIM, "9 = DroneCAN GPS."),
    ("ATC_ACCEL_R_MAX", 45000, SINIF_KRITIK, "Gerçek uçuş logunda ölçülen."),
    ("ATC_ACCEL_P_MAX", 45000, SINIF_KRITIK, "Gerçek uçuş logunda ölçülen."),
    ("ANGLE_MAX",       2500,  SINIF_KRITIK, "25° — gerçek logda ölçülen, kullanıcı onaylı."),
    ("INS_HNTCH_FREQ",  78.8,  SINIF_KRITIK, "Harmonik notch merkez frekansı."),
    ("ARMING_RUDDER",   2,     SINIF_KRITIK, "Rudder ile arm/disarm."),
    ("SERIAL1_BAUD",    115,   SINIF_KRITIK, "Companion MAVLink hattı (115200)."),
    ("FS_THR_ENABLE",   3,     SINIF_SARTNAME,
     "ŞARTNAME §9.2: radyo arıza emniyeti 'kontrollü iniş' OLMAK ZORUNDA. "
     "3 = Always Land. 1 (=RTL) ŞARTNAMEYE AYKIRIDIR ve teknik kontrolde "
     "yerde, verici kapatılarak sınanır."),
    ("FS_GCS_ENABLE",   1,     SINIF_SARTNAME,
     "GCS failsafe — İKİNCİ savunma hattı. Arıza senaryosu 7'de companion linki "
     "kopunca araç 20 m'de GUIDED'da asılı kaldı; 1 = RTL bunu önler. "
     "GERÇEK LOGDA 0 ÇIKMIŞTI — sahada mutlaka doğrulayın."),
    ("FS_GCS_TIMEOUT",  5,     SINIF_SARTNAME, "GCS failsafe tetikleme süresi (s)."),
    ("BATT_MONITOR",    4,     SINIF_KRITIK,
     "4 = analog gerilim + akım. Akım okunmazsa harcanan mAh sayılamaz ve "
     "yüzde tabanlı emniyet anlamsızlaşır."),
    ("BATT_CAPACITY",   4200,  SINIF_KRITIK,
     "Leopard 40C 6S LiPo 4200 mAh (operatör bildirdi 2026-09-03). Yanlışsa "
     "FC'nin bildirdiği kalan yüzde yanlış olur; İHA failsafe'i bu yüzdeyi "
     "kullanıyor (bridge.py: battery_remaining) ve %10'da MANDALLI kritik "
     "ilan edip görev kapısını kapatır."),
    ("BATT_LOW_VOLT",   21.0,  SINIF_KRITIK,
     "3.50 V/hücre x 6. RTL eşiği (BATT_FS_LOW_ACT=2)."),
    ("BATT_CRT_VOLT",   19.8,  SINIF_KRITIK,
     "3.30 V/hücre x 6. Land eşiği (BATT_FS_CRT_ACT=1). LOW ile arasında "
     "1.2 V BİLEREK bırakıldı: FC'de kayıtlı eski değerler (21.2 / 21.0) "
     "arasında yalnız 0.2 V vardı, yani RTL tetiklendikten hemen sonra Land "
     "de tetikleniyor ve araç eve dönemeden olduğu yere iniyordu."),
    ("BATT_FS_LOW_ACT", 2,     SINIF_SARTNAME,
     "2 = RTL. Companion'ın batarya RTL'i KALDIRILDI; batarya emniyeti artık "
     "TAMAMEN FC'de. Uymuyorsa görev BAŞLAMAZ."),
    ("BATT_FS_CRT_ACT", 1,     SINIF_SARTNAME, "1 = Land (kritik batarya)."),
    ("SERVO6_MIN",      1100,  SINIF_KRITIK,
     "pwm_open=400 ArduPilot tarafından SERVOn_MIN'e kırpılır — bu kırpma artık "
     "doğrulanan bir sözleşmedir, 800'e çekilirse servo dayanağa zorlanır."),
    ("SERVO6_MAX",      1900,  SINIF_KRITIK, "Yük kapağı kapalı ucu."),
    ("SERVO7_MIN",      1100,  SINIF_KRITIK, "Bkz. SERVO6_MIN."),
    ("SERVO7_MAX",      1900,  SINIF_KRITIK, "Yük kapağı kapalı ucu."),
    ("SERVO6_TRIM",     1900,  SINIF_KRITIK,
     "FC AÇILIŞ konumu = yükü TUTAR. 1100 olsaydı FC her açılışta kapağı açar, "
     "yüklü araçta yük düşerdi (2026-09-02 gerçek FC'den doğrulandı)."),
    ("SERVO7_TRIM",     1900,  SINIF_KRITIK, "Bkz. SERVO6_TRIM."),
]

# ── Yeniden başlatma gerektirdiği DOĞRULANMIŞ parametreler ────────────────
# NEDEN kısa liste: ArduPilot parametre meta verisinde @RebootRequired
# işaretini yalnızca aşağıdakiler için doğrulayabildim. Diğerleri için
# gereklilik BİLİNMİYOR — panel bunu açıkça yazar, tahmin etmez.
YENIDEN_BASLATMA_GEREKENLER = {
    "FRAME_CLASS", "FRAME_TYPE", "CAN_P1_DRIVER", "GPS1_TYPE",
    "SERIAL1_BAUD", "MOT_PWM_TYPE",
}

# Panel ayarlarının (seçilen config.json yolu) saklandığı dosya.
# NEDEN gcs_config DEĞİL: core/gcs_config.py yalnızca DEFAULTS içinde tanımlı
# anahtarları kaydeder ve o dosya bu görevin kapsamı dışında (bkz. rapor).
_AYAR_DOSYASI = app_data_dir() / "kalibrasyon_paneli.json"

_SUTUNLAR = ["Yaz", "Parametre", "Sınıf", "Beklenen", "FC'deki", "Durum", "Not"]
S_YAZ, S_AD, S_SINIF, S_BEKLENEN, S_FC, S_DURUM, S_NOT = range(7)


class PreflightCheckView(QWidget):
    """fc_params_expected'in FC'ye karşı canlı doğrulaması."""

    def __init__(self, kanal: MavKanal, depo: ParamDeposu,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kanal = kanal
        self._depo = depo
        self._yazici = ParamYazici(kanal, self)
        self._yazici.ilerleme.connect(self._on_yazma_ilerleme)
        self._yazici.sonuc.connect(self._on_yazma_sonuc)
        self._yazici.bitti.connect(self._on_yazma_bitti)

        self._beklenen = list(VARSAYILAN_BEKLENEN)
        self._kaynak = "YKİ içindeki kopya (2026-09-02)"
        self._satir_index: dict[str, int] = {}
        self._durum_token = "text_muted"
        # Son yazma turunda GERÇEKTEN gönderilen parametreler — bitiş mesajında
        # "yeniden başlatma gerekiyor" uyarısını YALNIZCA yazılanlar için verir.
        self._son_yazilanlar: set[str] = set()

        self._build_ui()
        self._tabloyu_kur()
        self._depo.degisti.connect(self.on_param)
        self._depo.durum.connect(self._depo_durum)
        self._kayitli_yolu_dene()
        self._tabloyu_tazele()
        self._kapilari_uygula()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ══════════════════════════════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════════════════════════════
    def _build_ui(self) -> None:
        kok = QVBoxLayout(self)
        kok.setContentsMargins(8, 8, 8, 8)
        kok.setSpacing(8)

        bilgi = QLabel(
            "Bu liste, İHA'nın görev başlatmadan önce doğruladığı "
            "fc_params_expected setidir. UYUMSUZ bir ŞARTNAME parametresi "
            "görevin BAŞLAMAMASI demektir. Değerler FC'den okunur — panel "
            "kendiliğinden HİÇBİR ŞEY YAZMAZ; yazmak için satırdaki 'Yaz' "
            "kutusunu işaretleyip aşağıdaki düğmeye basmanız gerekir."
        )
        bilgi.setObjectName("hintLabel")
        bilgi.setWordWrap(True)
        kok.addWidget(bilgi)

        kaynak_satir = QHBoxLayout()
        self._kaynak_lbl = QLabel()
        self._kaynak_lbl.setObjectName("mutedLabel")
        self._kaynak_lbl.setWordWrap(True)
        kaynak_satir.addWidget(self._kaynak_lbl, 1)
        btn_yukle = QPushButton("İHA config.json'dan YÜKLE…")
        btn_yukle.setMinimumHeight(28)
        btn_yukle.clicked.connect(self._on_config_yukle)
        kaynak_satir.addWidget(btn_yukle)
        kok.addLayout(kaynak_satir)

        self._ozet_lbl = QLabel("Özet: FC'den hiç parametre okunmadı")
        self._ozet_lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._ozet_lbl.setWordWrap(True)
        kok.addWidget(self._ozet_lbl)

        self._tablo = QTableWidget(0, len(_SUTUNLAR))
        self._tablo.setHorizontalHeaderLabels(_SUTUNLAR)
        self._tablo.verticalHeader().setVisible(False)
        self._tablo.setAlternatingRowColors(True)
        self._tablo.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hdr = self._tablo.horizontalHeader()
        hdr.setSectionResizeMode(S_YAZ, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(S_AD, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(S_SINIF, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(S_BEKLENEN, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(S_FC, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(S_DURUM, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(S_NOT, QHeaderView.ResizeMode.Stretch)
        kok.addWidget(self._tablo, 1)

        btn_satir = QHBoxLayout()
        self._btn_oku = QPushButton("FC'den OKU (tam parametre listesi)")
        self._btn_oku.setObjectName("primaryButton")
        self._btn_oku.setMinimumHeight(32)
        self._btn_oku.clicked.connect(self._on_oku)
        btn_satir.addWidget(self._btn_oku)

        btn_sec_uyumsuz = QPushButton("Uyumsuzların hepsini işaretle")
        btn_sec_uyumsuz.setMinimumHeight(32)
        btn_sec_uyumsuz.clicked.connect(lambda: self._toplu_isaretle(True))
        btn_satir.addWidget(btn_sec_uyumsuz)

        btn_temizle = QPushButton("İşaretleri temizle")
        btn_temizle.setMinimumHeight(32)
        btn_temizle.clicked.connect(lambda: self._toplu_isaretle(False))
        btn_satir.addWidget(btn_temizle)

        self._btn_yaz = QPushButton("İŞARETLİLERİ YAZ ve DOĞRULA")
        self._btn_yaz.setObjectName("warningButton")
        self._btn_yaz.setMinimumHeight(32)
        self._btn_yaz.clicked.connect(self._on_yaz)
        btn_satir.addWidget(self._btn_yaz)

        self._btn_iptal = QPushButton("İptal")
        self._btn_iptal.setObjectName("dangerButton")
        self._btn_iptal.setMinimumHeight(32)
        self._btn_iptal.setEnabled(False)
        self._btn_iptal.clicked.connect(self._on_iptal)
        btn_satir.addWidget(self._btn_iptal)
        kok.addLayout(btn_satir)

        self._ilerleme = QProgressBar()
        self._ilerleme.setRange(0, 100)
        self._ilerleme.setValue(0)
        self._ilerleme.setFormat("İşlem yok")
        kok.addWidget(self._ilerleme)

        self._durum = QLabel("Durum: hazır")
        self._durum.setWordWrap(True)
        kok.addWidget(self._durum)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        self._log.document().setMaximumBlockCount(300)
        kok.addWidget(self._log)

        self._apply_theme_colors()

    def _apply_theme_colors(self) -> None:
        self._durum.setStyleSheet(f"color:{theme.c(self._durum_token)};font-size:12px;")
        self._log.setStyleSheet(
            f"QTextEdit{{color:{theme.c('success')};"
            "font-family:'Courier New';font-size:11px;}"
        )
        # Tablo renkleri QBrush ile yazıldığı için tema değişince tazelenmeli.
        self._tabloyu_tazele()

    # ══════════════════════════════════════════════════════════════════════
    # Tablo
    # ══════════════════════════════════════════════════════════════════════
    def _tabloyu_kur(self) -> None:
        self._tablo.setRowCount(0)
        self._satir_index.clear()
        for ad, beklenen, sinif, not_metni in self._beklenen:
            satir = self._tablo.rowCount()
            self._tablo.insertRow(satir)
            self._satir_index[ad] = satir

            yaz_item = QTableWidgetItem("")
            yaz_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            yaz_item.setCheckState(Qt.CheckState.Unchecked)
            yaz_item.setToolTip("İşaretlenirse 'YAZ ve DOĞRULA' bu parametreyi yazar")
            self._tablo.setItem(satir, S_YAZ, yaz_item)

            ad_item = QTableWidgetItem(ad)
            ad_item.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
            self._tablo.setItem(satir, S_AD, ad_item)

            self._tablo.setItem(satir, S_SINIF, QTableWidgetItem(sinif))
            self._tablo.setItem(satir, S_BEKLENEN, QTableWidgetItem(f"{beklenen:g}"))
            self._tablo.setItem(satir, S_FC, QTableWidgetItem("—"))
            self._tablo.setItem(satir, S_DURUM, QTableWidgetItem("okunmadı"))

            ek = ""
            if ad in YENIDEN_BASLATMA_GEREKENLER:
                ek = "  [YAZILDIKTAN SONRA FC YENİDEN BAŞLATILMALI]"
            not_item = QTableWidgetItem(not_metni + ek)
            not_item.setToolTip(not_metni + ek)
            self._tablo.setItem(satir, S_NOT, not_item)

    def _tabloyu_tazele(self) -> None:
        """FC değerlerini, durumları ve renkleri tazeler + özeti yeniden yazar."""
        okunan = 0
        uyumsuz_sartname: list[str] = []
        uyumsuz_diger: list[str] = []
        for ad, beklenen, sinif, _not in self._beklenen:
            satir = self._satir_index.get(ad)
            if satir is None:
                continue
            fc = self._depo.get(ad)
            fc_item = self._tablo.item(satir, S_FC)
            durum_item = self._tablo.item(satir, S_DURUM)
            sinif_item = self._tablo.item(satir, S_SINIF)
            if fc_item is None or durum_item is None:
                continue

            if sinif == SINIF_SARTNAME and sinif_item is not None:
                sinif_item.setForeground(QBrush(QColor(theme.c("danger"))))
                sinif_item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))

            if fc is None:
                fc_item.setText("—")
                durum_item.setText("okunmadı")
                self._satir_renk(satir, "text_muted")
                continue
            okunan += 1
            fc_item.setText(f"{fc:g}")
            if param_esit(fc, beklenen):
                durum_item.setText("✓ UYUMLU")
                self._satir_renk(satir, "success")
            else:
                durum_item.setText("✗ UYUMSUZ")
                self._satir_renk(satir, "danger")
                (uyumsuz_sartname if sinif == SINIF_SARTNAME
                 else uyumsuz_diger).append(ad)

        toplam = len(self._beklenen)
        if okunan == 0:
            self._ozet_lbl.setText(
                "Özet: FC'den hiç parametre okunmadı — 'FC'den OKU'ya basın."
            )
            self._ozet_lbl.setStyleSheet(f"color:{theme.c('text_muted')};")
        elif uyumsuz_sartname:
            self._ozet_lbl.setText(
                f"⛔ ŞARTNAME UYUMSUZ ({len(uyumsuz_sartname)}): "
                + ", ".join(uyumsuz_sartname)
                + f"   |   diğer uyumsuz: {len(uyumsuz_diger)}"
                + f"   |   okunan {okunan}/{toplam}"
                + "\nBu hâlde İHA görevi BAŞLATMAZ ve teknik kontrolde "
                "arıza emniyeti sınavı GEÇİLEMEZ."
            )
            self._ozet_lbl.setStyleSheet(f"color:{theme.c('danger')};")
        elif uyumsuz_diger:
            self._ozet_lbl.setText(
                f"⚠ {len(uyumsuz_diger)} parametre uyumsuz "
                f"({', '.join(uyumsuz_diger[:6])}{'…' if len(uyumsuz_diger) > 6 else ''})"
                f"   |   okunan {okunan}/{toplam}   |   ŞARTNAME parametreleri UYUMLU"
            )
            self._ozet_lbl.setStyleSheet(f"color:{theme.c('warning')};")
        elif okunan < toplam:
            self._ozet_lbl.setText(
                f"Okunanların hepsi uyumlu ({okunan}/{toplam}) — "
                f"{toplam - okunan} parametre FC'den HİÇ GELMEDİ "
                "(bu parametreler FC'de olmayabilir; sürüm farkı olabilir)."
            )
            self._ozet_lbl.setStyleSheet(f"color:{theme.c('warning')};")
        else:
            self._ozet_lbl.setText(
                f"✓ {toplam}/{toplam} parametre BEKLENEN değerde — uçuş öncesi "
                "parametre kapısı geçildi."
            )
            self._ozet_lbl.setStyleSheet(f"color:{theme.c('success')};")

        self._kaynak_lbl.setText(f"Beklenen liste kaynağı: {self._kaynak}")

    def _satir_renk(self, satir: int, token: str) -> None:
        firca = QBrush(QColor(theme.c(token)))
        for sutun in (S_AD, S_BEKLENEN, S_FC, S_DURUM):
            item = self._tablo.item(satir, sutun)
            if item is not None:
                item.setForeground(firca)

    def _toplu_isaretle(self, uyumsuzlari: bool) -> None:
        for ad, beklenen, _sinif, _not in self._beklenen:
            satir = self._satir_index.get(ad)
            if satir is None:
                continue
            item = self._tablo.item(satir, S_YAZ)
            if item is None:
                continue
            if not uyumsuzlari:
                item.setCheckState(Qt.CheckState.Unchecked)
                continue
            fc = self._depo.get(ad)
            isaretle = fc is not None and not param_esit(fc, beklenen)
            item.setCheckState(
                Qt.CheckState.Checked if isaretle else Qt.CheckState.Unchecked
            )

    # ══════════════════════════════════════════════════════════════════════
    # Beklenen listenin kaynağı
    # ══════════════════════════════════════════════════════════════════════
    def _kayitli_yolu_dene(self) -> None:
        """Daha önce seçilmiş config.json varsa sessizce yükler."""
        try:
            if not _AYAR_DOSYASI.exists():
                return
            veri = json.loads(_AYAR_DOSYASI.read_text(encoding="utf-8"))
            yol = veri.get("iha_config_yolu")
            if yol and Path(yol).exists():
                self._config_yukle(Path(yol), sessiz=True)
        except Exception:
            logger.exception("Kayıtlı İHA config yolu okunamadı")

    def _yolu_kaydet(self, yol: Path) -> None:
        try:
            veri = {}
            if _AYAR_DOSYASI.exists():
                veri = json.loads(_AYAR_DOSYASI.read_text(encoding="utf-8"))
            veri["iha_config_yolu"] = str(yol)
            _AYAR_DOSYASI.write_text(
                json.dumps(veri, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            logger.exception("İHA config yolu kaydedilemedi")

    def _on_config_yukle(self) -> None:
        yol, _ = QFileDialog.getOpenFileName(
            self, "İHA config.json seçin", "", "JSON (*.json);;Tüm dosyalar (*)"
        )
        if not yol:
            return
        self._config_yukle(Path(yol), sessiz=False)

    def _config_yukle(self, yol: Path, sessiz: bool) -> None:
        """config.json::fc_params_expected'i okur ve tabloyu yeniden kurar."""
        try:
            veri = json.loads(yol.read_text(encoding="utf-8"))
            ham = veri.get("fc_params_expected")
            if not isinstance(ham, dict):
                raise ValueError("fc_params_expected bölümü bulunamadı")
            yeni: list[tuple[str, float, str, str]] = []
            # Eski notları koru: aynı parametre için elimizdeki açıklama daha
            # zengin (config'deki notlar ayrı _comment_* anahtarlarında).
            eski_notlar = {ad: (sinif, not_m)
                           for ad, _d, sinif, not_m in VARSAYILAN_BEKLENEN}
            for ad, deger in ham.items():
                if ad.startswith("_"):      # _comment* anahtarları veri değildir
                    continue
                if not isinstance(deger, (int, float)):
                    continue
                sinif, not_m = eski_notlar.get(ad, (SINIF_KRITIK, ""))
                yeni.append((str(ad), float(deger), sinif, not_m))
            if not yeni:
                raise ValueError("fc_params_expected içinde sayısal alan yok")
        except Exception as exc:
            logger.exception("İHA config.json okunamadı")
            if not sessiz:
                QMessageBox.warning(
                    self, "config.json okunamadı",
                    f"{yol}\n\nHata: {exc}\n\nYKİ içindeki kopya kullanılmaya "
                    "devam ediyor."
                )
            return

        # Bizde olup config'de olmayanları raporla — kopya bayatlamış olabilir
        eski_adlar = {ad for ad, _d, _s, _n in VARSAYILAN_BEKLENEN}
        yeni_adlar = {ad for ad, _d, _s, _n in yeni}
        kaybolan = sorted(eski_adlar - yeni_adlar)
        eklenen = sorted(yeni_adlar - eski_adlar)

        self._beklenen = yeni
        self._kaynak = f"{yol}  ({len(yeni)} parametre)"
        self._tabloyu_kur()
        self._tabloyu_tazele()
        self._yolu_kaydet(yol)
        self._logla(f"[KAYNAK] {yol} yüklendi — {len(yeni)} parametre")
        if kaybolan or eklenen:
            mesaj = (
                "İHA config.json ile YKİ içindeki kopya FARKLI:\n\n"
                + (f"Yalnız YKİ kopyasında: {', '.join(kaybolan)}\n" if kaybolan else "")
                + (f"Yalnız config.json'da: {', '.join(eklenen)}\n" if eklenen else "")
                + "\nArtık config.json'daki liste kullanılıyor. YKİ'deki kopyanın "
                "(preflight_check_view.py::VARSAYILAN_BEKLENEN) güncellenmesi gerekir."
            )
            self._logla("[UYARI] " + mesaj.replace("\n", " "))
            if not sessiz:
                QMessageBox.information(self, "Liste farkı", mesaj)

    # ══════════════════════════════════════════════════════════════════════
    # Dışarıdan beslenen
    # ══════════════════════════════════════════════════════════════════════
    @pyqtSlot(bool)
    def on_connection_changed(self, _bagli: bool) -> None:
        self._kapilari_uygula()

    @pyqtSlot(str, float)
    def on_param(self, ad: str, deger: float) -> None:
        self._yazici.param_geldi(ad, deger)
        if ad in self._satir_index:
            self._tabloyu_tazele()

    def _depo_durum(self, metin: str) -> None:
        self._durum_yaz(metin, "info")
        self._kapilari_uygula()

    # ══════════════════════════════════════════════════════════════════════
    # Eylemler
    # ══════════════════════════════════════════════════════════════════════
    def _durum_yaz(self, metin: str, token: str = "text_muted") -> None:
        self._durum_token = token
        self._durum.setText(metin)
        self._durum.setStyleSheet(f"color:{theme.c(token)};font-size:12px;")

    def _logla(self, metin: str) -> None:
        self._log.append(metin)

    def _kapilari_uygula(self) -> None:
        yaziyor = self._yazici.calisiyor
        neden_komut = self._kanal.neden_kapali()
        neden_param = self._kanal.neden_kapali(param_da_gerek=True)
        self._oku_dugmesini_guncelle(neden_komut)
        self._btn_yaz.setEnabled(not neden_param and not yaziyor)
        self._btn_yaz.setToolTip(neden_param)
        self._btn_iptal.setEnabled(yaziyor)
        if neden_param and not yaziyor:
            self._durum_yaz(f"KAPALI — {neden_param}", "warning")

    def _oku_dugmesini_guncelle(self, neden_komut: str) -> None:
        """'FC'den OKU' düğmesini indirme durumuna göre oku/bırak moduna alır."""
        if self._depo.indiriyor:
            self._btn_oku.setText("İzlemeyi BIRAK (indirme sürüyor)")
            self._btn_oku.setEnabled(True)
            self._btn_oku.setToolTip(
                "FC'nin gönderimi MAVLink'te iptal edilemez; yalnız YKİ'nin "
                "beklemesi bırakılır"
            )
        else:
            self._btn_oku.setText("FC'den OKU (tam parametre listesi)")
            self._btn_oku.setEnabled(not neden_komut)
            self._btn_oku.setToolTip(neden_komut)

    def _on_oku(self) -> None:
        """FC'den tam parametre listesini ister / süren indirmenin izlenmesini bırakır.

        NEDEN aynı düğme iki işi yapıyor: tam parametre indirmesi telemetri
        linkinde DAKİKALAR sürebilen bir işlem ve kullanıcıyı belirsiz bırakmak
        yasak. MAVLink'te başlamış bir PARAM_REQUEST_LIST akışını İPTAL EDEN bir
        mesaj YOK; dolayısıyla yapılabilecek tek dürüst "iptal", YKİ tarafındaki
        beklemeyi bırakmaktır — düğme de tam olarak bunu söylüyor.
        """
        if self._depo.indiriyor:
            self._depo.indirmeyi_durdur()
            self._logla("[OKU] İzleme bırakıldı (FC gönderimi kendiliğinden bitecek)")
        elif self._depo.indir():
            self._logla("[OKU] Tam parametre listesi istendi")
        self._kapilari_uygula()

    def _isaretliler(self) -> dict[str, float]:
        secili: dict[str, float] = {}
        for ad, beklenen, _sinif, _not in self._beklenen:
            satir = self._satir_index.get(ad)
            if satir is None:
                continue
            item = self._tablo.item(satir, S_YAZ)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                secili[ad] = float(beklenen)
        return secili

    def _on_yaz(self) -> None:
        neden = self._kanal.neden_kapali(param_da_gerek=True)
        if neden:
            self._durum_yaz(f"YAZILMADI — {neden}", "danger")
            return
        istekler = self._isaretliler()
        if not istekler:
            QMessageBox.information(
                self, "Seçim yok",
                "Hiçbir satırda 'Yaz' kutusu işaretli değil.\n"
                "'Uyumsuzların hepsini işaretle' düğmesini kullanabilirsiniz."
            )
            return

        reboot = sorted(ad for ad in istekler if ad in YENIDEN_BASLATMA_GEREKENLER)
        ozet = "\n".join(f"  {ad} = {d:g}" for ad, d in sorted(istekler.items()))
        uyari = (
            "İHA deposunun kendi notu şunu söylüyor: 'Bu liste FC'ye YAZILMAZ, "
            "yalnızca DOĞRULANIR.' Bu kural companion'ın SESSİZ/otomatik yazma "
            "yapmamasını sağlar. Buradaki yazma ise OPERATÖR TARAFINDAN, tek tek "
            "seçilerek ve onaylanarak yapılır — Mission Planner'la aynı iş.\n\n"
        )
        if reboot:
            uyari += (
                "⚠ Şunlar YAZILDIKTAN SONRA FC YENİDEN BAŞLATILMADAN ETKİ ETMEZ: "
                + ", ".join(reboot) + "\n\n"
            )
        cevap = QMessageBox.question(
            self, "FC parametreleri yazılacak",
            uyari + "Yazılacak parametreler:\n\n" + ozet +
            "\n\nHer biri yazıldıktan sonra FC'den GERİ OKUNARAK doğrulanacak.\n"
            "Devam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if cevap != QMessageBox.StandardButton.Yes:
            self._durum_yaz("İptal edildi — hiçbir parametre yazılmadı")
            return

        self._ilerleme.setRange(0, len(istekler))
        self._ilerleme.setValue(0)
        self._ilerleme.setFormat("%v / %m doğrulandı")
        self._son_yazilanlar = set(istekler)
        if not self._yazici.yaz(istekler):
            self._durum_yaz("YAZILAMADI — parametre kanalı bağlı değil", "danger")
            return
        self._durum_yaz(
            f"{len(istekler)} parametre yazıldı — FC'den geri okuma bekleniyor…",
            "info",
        )
        self._kapilari_uygula()

    def _on_iptal(self) -> None:
        self._yazici.iptal()
        self._durum_yaz(
            "İptal edildi — bekleyen doğrulamalar bırakıldı. GÖNDERİLMİŞ yazmalar "
            "geri ALINMADI; 'FC'den OKU' ile gerçek durumu doğrulayın.",
            "warning",
        )
        self._kapilari_uygula()

    # ── ParamYazici geri bildirimleri ────────────────────────────────────
    def _on_yazma_ilerleme(self, ad: str, durum: str) -> None:
        self._logla(f"[{ad}] {durum}")
        satir = self._satir_index.get(ad)
        if satir is None:
            return
        item = self._tablo.item(satir, S_DURUM)
        if item is not None:
            item.setText(durum)

    def _on_yazma_sonuc(self, ad: str, ok: bool, _beklenen: float, okunan: float) -> None:
        self._ilerleme.setValue(self._ilerleme.value() + 1)
        satir = self._satir_index.get(ad)
        if satir is None:
            return
        if ok:
            fc_item = self._tablo.item(satir, S_FC)
            if fc_item is not None:
                fc_item.setText(f"{okunan:g}")
            yaz_item = self._tablo.item(satir, S_YAZ)
            if yaz_item is not None:
                yaz_item.setCheckState(Qt.CheckState.Unchecked)
            self._satir_renk(satir, "success")
        else:
            self._satir_renk(satir, "danger")

    def _on_yazma_bitti(self, basarili: int, toplam: int) -> None:
        self._ilerleme.setFormat("İşlem yok")
        self._ilerleme.setRange(0, 100)
        self._ilerleme.setValue(0)
        reboot_yazildi = sorted(self._son_yazilanlar & YENIDEN_BASLATMA_GEREKENLER)
        if basarili == toplam:
            metin = (f"✓ {basarili}/{toplam} parametre yazıldı ve FC'den GERİ "
                     "OKUNARAK doğrulandı.")
            if reboot_yazildi:
                metin += ("  ⚠ Şunlar ETKİ ETMESİ İÇİN FC YENİDEN BAŞLATILMALI: "
                          + ", ".join(reboot_yazildi))
            self._durum_yaz(metin, "success")
        else:
            self._durum_yaz(
                f"✗ {basarili}/{toplam} doğrulandı — kalanların yazıldığı "
                "KANITLANAMADI. Satır durumlarına bakın.",
                "danger",
            )
        self._tabloyu_tazele()
        self._kapilari_uygula()
