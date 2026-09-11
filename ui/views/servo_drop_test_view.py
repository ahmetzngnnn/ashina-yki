"""
ui/views/servo_drop_test_view.py — Yük Bırakma Servosu PWM Testi
===================================================================

⚠ BU PANEL YÜK BIRAKMA SERVOLARINI (kırmızı/mavi) GERÇEKTEN HAREKET ETTİRİR.

NEDEN VAR (2026-09-04): "pim çok fazla çekiyor, birkaç derece eksik çeksin"
şikâyeti sahada test edilirken 1150/1125 µs'de yük HİÇ bırakılamadığı,
1100 µs'de (orijinal, kırpma tabanı) her zaman güvenilir bıraktığı görüldü —
1100-1125 arası yalnızca 25 µs'lik bantta "bırakır" → "hiç bırakmaz" geçişi
var. Bu marjı operatörün KENDİSİ, küçük adımlarla (1105/1110/1115...) ve
her adımda gerçek bırakma gözlemiyle taraması gerekiyor — her denemede bana
(Claude'a) SSH ile config yazdırıp servisi yeniden başlatmak hem yavaş hem
her seferinde "az önce çalışıyordu şimdi neden çalışmıyor" karışıklığına
(bkz. DEVIR_2026-09-04) yol açan bir ekstra değişken katıyordu.

MİMARİ — companion (IHA) BYPASS EDİLİR, motor_test_view.py ile AYNI desen:
FC'ye giden tek yol `MavKanal` (doğrudan RFD MAVLink linki, DO_SET_SERVO).
config.json'a YAZILMAZ — yalnızca ANLIK ham PWM denenir. Operatör iyi bir
değer bulunca bunu bana/config.json'a KALICI yazma isteğiyle bildirir; bu
panel kalıcı yazma YAPMAZ (sahada dikkatsizce config bozma riskini almamak
için bilinçli tercih — bkz. modül üstü not).

Yapılandırılmış (config.json) pwm_open/pwm_close DEĞERLERİ salt-okunur
gösterilir — kaynak: companion'ın status yayını (comms/status_server.py
servo_state.{red,blue}_drop_pwm_{open,close}), main_window üzerinden
CalibrationView.update_rpi_status ile beslenir.

GÜVENLİK:
  • Üstte göz ardı edilemez uyarı + "yük TAKILI DEĞİL / bırakılması
    risksiz" onay kutusu — işaretlenmeden test butonları KAPALI.
  • Araç ARM'lıyken test butonları KAPALI (motor_test_view ile aynı
    gerekçe: gerçek bir sürprizi önlemek, bu bir uçuş kontrolü değil ama
    ARM'lı bir araçta hiçbir servo denemesi yapılmamalı).
  • "Tut (kapat)" butonu HER ZAMAN etkin (kapatmak/tutmak asla riskli
    değildir — motor testindeki ACİL DUR ile aynı felsefe).
  • Panel terk edilince/gizlenince onay kutusu DÜŞER (PervaneKapisi ile
    aynı gerekçe: bir önceki onay aracın ŞU ANKİ durumuna dair değildir).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)
from pymavlink import mavutil

from core import theme
from ui.widgets.panel_ortak import MavKanal, TekerlekKorumasi, tekerlek_korumasi_kur

logger = logging.getLogger(__name__)

# Ham PWM aralığı — Dashboard'daki mevcut manuel slider ile AYNI (200-2000,
# bkz. dashboard_view.py red/blue_drop_slider). 2026-09-04: PWM_MIN=900 idi,
# operatörün kalibrasyon için ihtiyaç duyduğu bilinen-çalışan değer (400)
# bu tabanın ALTINDA kaldığı için hiç test edilemiyordu — "kırpma" sanılan
# şey ArduPilot'ta değil, buradaki yapay tavandı (ArduPilot DO_SET_SERVO'yu
# HİÇ kırpmıyor, bkz. SRV_Channel::set_output_pwm — doğrudan atama).
PWM_MIN = 200
PWM_MAX = 2000
PWM_ADIM = 5           # µs — "birkaç derece" ölçeğinde ince tarama için
PWM_VARSAYILAN = 1500  # gerçek config değeri gelene kadar nötr gösterge

_SERVOLAR = [
    ("red_drop", "🔴 Kırmızı Yük Bırakma", 6),
    ("blue_drop", "🔵 Mavi Yük Bırakma", 7),
]


class ServoDropTestView(QWidget):
    """Yük bırakma servolarının ham PWM'ini canlı test eden panel.

    Kendi pyqtSignal'i YOKTUR — bkz. panel_ortak.MavKanal NEDEN(2/2).
    """

    def __init__(self, kanal: MavKanal, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kanal = kanal
        self._armed = False
        # ad -> {"channel": int|None, "pwm_open": int|None, "pwm_close": int|None}
        self._config_pwm: dict[str, dict] = {ad: {} for ad, _, _ in _SERVOLAR}
        self._son_gonderilen: dict[str, tuple[int, str]] = {}  # ad -> (pwm, saat)

        self._wheel_guard = TekerlekKorumasi()
        self._build_ui()
        self._kapilari_uygula()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ══════════════════════════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════════════════════════
    def _build_ui(self) -> None:
        kok = QVBoxLayout(self)
        kok.setContentsMargins(8, 8, 8, 8)
        kok.setSpacing(8)

        # ── Güvenlik kapısı ──────────────────────────────────────────
        kapi = QFrame()
        kapi.setObjectName("cardFrame")
        kl = QVBoxLayout(kapi)
        kl.setContentsMargins(12, 10, 12, 10)
        kl.setSpacing(6)
        baslik = QLabel("⚠  YÜK BIRAKMA SERVOSU TESTİ  ⚠")
        baslik.setObjectName("dangerText")
        baslik.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        baslik.setAlignment(Qt.AlignmentFlag.AlignCenter)
        kl.addWidget(baslik)
        aciklama = QLabel(
            "Bu panel yük bırakma servolarını (kırmızı/mavi) GERÇEKTEN "
            "hareket ettirir — config.json'a YAZMAZ, yalnız ANLIK PWM dener.\n"
            "  1) Yük TAKILI DEĞİLSE, ya da takılıysa şu an düşmesinin "
            "sorun olmadığını doğrulayın,\n"
            "  2) Aracın ARM OLMADIĞINDAN emin olun (armlıyken test kapalı),\n"
            "  3) Küçük adımlarla ilerleyin — davranış DÜZ BİR ÇİZGİ DEĞİL: "
            "400 µs tam bırakıyor, 900 µs bırakmıyor, 1100 µs tam bırakıyor, "
            "1125 µs hiç bırakmıyor (ÖLÇÜLDÜ, 2026-09-04). ArduPilot bu "
            "PWM'i KIRPMIYOR (SRV_Channel::set_output_pwm doğrudan atar) — "
            "gördüğünüz davranış servonun kendisinden geliyor, aradaki her "
            "değeri TEK TEK ölçmeden sonuç tahmin ETMEYİN."
        )
        aciklama.setWordWrap(True)
        aciklama.setObjectName("hintLabel")
        kl.addWidget(aciklama)
        self._kutu = QCheckBox(
            "Yükün TAKILI OLMADIĞINI ya da bırakılmasının ŞU AN risksiz "
            "olduğunu doğruladım"
        )
        self._kutu.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._kutu.toggled.connect(self._on_kapi_degisti)
        kl.addWidget(self._kutu)
        kok.addWidget(kapi)

        # ── Durum satırı ─────────────────────────────────────────────
        self._durum = QLabel("—")
        self._durum_token = "text_muted"
        kok.addWidget(self._durum)

        # ── Servo kartları ───────────────────────────────────────────
        satir = QHBoxLayout()
        satir.setSpacing(10)
        self._kartlar: dict[str, dict] = {}
        for ad, etiket, kanal_no in _SERVOLAR:
            satir.addWidget(self._servo_karti(ad, etiket, kanal_no))
        kok.addLayout(satir)
        kok.addStretch(1)

        tekerlek_korumasi_kur(self, self._wheel_guard)

    def _servo_karti(self, ad: str, etiket: str, kanal_no: int) -> QGroupBox:
        grp = QGroupBox(f"{etiket} (CH{kanal_no})")
        gl = QVBoxLayout(grp)
        gl.setSpacing(8)

        lbl_cfg = QLabel("Yapılandırılmış (config.json): open=— close=— — okunmadı")
        lbl_cfg.setWordWrap(True)
        lbl_cfg.setObjectName("hintLabel")
        gl.addWidget(lbl_cfg)

        grid = QGridLayout()
        grid.addWidget(QLabel("Test PWM (µs):"), 0, 0)
        spin = QSpinBox()
        spin.setRange(PWM_MIN, PWM_MAX)
        spin.setSingleStep(PWM_ADIM)
        spin.setValue(PWM_VARSAYILAN)
        spin.setSuffix(" µs")
        grid.addWidget(spin, 0, 1)
        gl.addLayout(grid)

        btn_gonder = QPushButton("▶ Bu PWM'i Gönder (test)")
        btn_gonder.setObjectName("primaryButton")
        btn_gonder.clicked.connect(lambda _=False, a=ad: self._gonder_test(a))
        gl.addWidget(btn_gonder)

        btn_tut = QPushButton("🔒 Tut / Kapat (ANINDA — config'teki close)")
        btn_tut.clicked.connect(lambda _=False, a=ad: self._gonder_tut(a))
        gl.addWidget(btn_tut)

        # ── Yavaş/kontrollü kapanma (2026-09-04, operatör talebi) ──
        # NEDEN: yük ELLE yerleştirilirken ani kapanma parmak sıkışması/yük
        # kayması riski taşıyor. Süre panelden ayarlanır — üretimdeki
        # `close_ramp_s` (config.json) BURADAN bağımsız bir değerdir; burada
        # bulunan iyi süre operatör isterse config'e de yazılır.
        ramp_grid = QGridLayout()
        ramp_grid.addWidget(QLabel("Yavaş kapanma süresi (sn):"), 0, 0)
        ramp_spin = QDoubleSpinBox()
        ramp_spin.setRange(0.5, 10.0)
        ramp_spin.setSingleStep(0.5)
        ramp_spin.setValue(2.0)
        ramp_spin.setSuffix(" sn")
        ramp_grid.addWidget(ramp_spin, 0, 1)
        gl.addLayout(ramp_grid)

        btn_yavas = QPushButton("🐢 Yavaş Kapat (rampalı, kontrollü)")
        btn_yavas.clicked.connect(lambda _=False, a=ad: self._yavas_kapat(a))
        gl.addWidget(btn_yavas)

        lbl_son = QLabel("Son gönderilen: —")
        lbl_son.setObjectName("hintLabel")
        gl.addWidget(lbl_son)

        ramp_timer = QTimer(self)
        ramp_timer.timeout.connect(lambda a=ad: self._ramp_tick(a))

        self._kartlar[ad] = {
            "channel": kanal_no,
            "lbl_cfg": lbl_cfg,
            "spin": spin,
            "btn_gonder": btn_gonder,
            "btn_tut": btn_tut,
            "ramp_spin": ramp_spin,
            "btn_yavas": btn_yavas,
            "ramp_timer": ramp_timer,
            "ramp_active": False,
            "lbl_son": lbl_son,
        }
        return grp

    def _apply_theme_colors(self) -> None:
        self._durum_yaz(self._durum.text(), self._durum_token)

    # ══════════════════════════════════════════════════════════════════
    # Dış besleme — main_window/calibration_view çağırır
    # ══════════════════════════════════════════════════════════════════
    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        armed = bool(data.get("armed", False))
        if armed != self._armed:
            self._armed = armed
            self._kapilari_uygula()

    def update_rpi_status(self, servo_state: dict) -> None:
        """companion status yayınından gelen servo_state bloğu.

        NEDEN AYRI (update_telemetry DEĞİL): pwm_open/pwm_close config.json'da
        yaşar ve yalnız companion'ın RPi status akışında (port 5602) taşınır —
        doğrudan MAVLink telemetrisinde (armed vb.) YOKTUR.
        """
        if not isinstance(servo_state, dict):
            return
        for ad, _, _ in _SERVOLAR:
            k = self._kartlar.get(ad)
            if k is None:
                continue
            ch = servo_state.get(f"{ad}_channel")
            po = servo_state.get(f"{ad}_pwm_open")
            pc = servo_state.get(f"{ad}_pwm_close")
            self._config_pwm[ad] = {"channel": ch, "pwm_open": po, "pwm_close": pc}
            if po is None and pc is None:
                continue
            k["lbl_cfg"].setText(
                f"Yapılandırılmış (config.json): open={self._s(po)} "
                f"close={self._s(pc)}  (kanal {self._s(ch)})"
            )

    @staticmethod
    def _s(v) -> str:
        return "—" if v is None else str(v)

    def on_connection_changed(self, bagli: bool) -> None:
        self._kapilari_uygula()

    # ══════════════════════════════════════════════════════════════════
    # Kapı / durum
    # ══════════════════════════════════════════════════════════════════
    def _kapali_nedeni(self) -> str:
        neden = self._kanal.neden_kapali()
        if neden:
            return neden
        if not self._kutu.isChecked():
            return ("Güvenlik onayı verilmedi — 'yük takılı değil / risksiz' "
                    "kutusunu işaretleyin")
        if self._armed:
            return "Araç ARM'lı — servo testi kapalı. Önce DISARM edin"
        return ""

    def _kapilari_uygula(self, durum_yaz: bool = True) -> None:
        neden = self._kapali_nedeni()
        acik = not neden
        for ad, k in self._kartlar.items():
            k["btn_gonder"].setEnabled(acik)
            k["btn_gonder"].setToolTip(neden)
            # "Tut/Kapat" (ANINDA) HER ZAMAN etkin — kapatmak asla riskli
            # değildir (ACİL DUR ile aynı felsefe, motor_test_view'a bkz.).
            # "Yavaş Kapat" ise Test Et ile AYNI kapıya tabidir (yeni bir
            # kasıtlı hareket başlatıyor, acil kurtarma değil).
            k["btn_yavas"].setEnabled(acik and not k["ramp_active"])
            k["btn_yavas"].setToolTip(neden)
            # Kapı KAPANDIYSA (onay geri çekildi/ARM oldu) süren bir rampa
            # varsa güvenli konuma (kapalı) ANINDA atlanır — yarım açık
            # bırakılmaz.
            if neden and k["ramp_active"]:
                self._ramp_iptal_ve_kapat(ad, neden)
        if not durum_yaz:
            return
        if neden:
            self._durum_yaz(f"KAPALI — {neden}", "warning")
        else:
            self._durum_yaz("HAZIR — küçük adımlarla test edin.", "success")

    def _on_kapi_degisti(self, _acik: bool) -> None:
        self._kapilari_uygula()

    def _durum_yaz(self, metin: str, token: str = "text_muted") -> None:
        self._durum_token = token
        self._durum.setText(metin)
        self._durum.setStyleSheet(
            f"color:{theme.c(token)};font-size:12px;font-weight:bold;"
        )

    # ══════════════════════════════════════════════════════════════════
    # Komut gönderimi
    # ══════════════════════════════════════════════════════════════════
    @staticmethod
    def _ml():
        if getattr(mavutil, "mavlink", None) is None:
            mavutil.set_dialect("ardupilotmega")
        return mavutil.mavlink

    def _gonder_pwm(self, ad: str, pwm: int, etiket: str) -> None:
        k = self._kartlar[ad]
        ch = k["channel"]
        gonderildi = self._kanal.komut(
            self._ml().MAV_CMD_DO_SET_SERVO, [ch, int(pwm), 0, 0, 0, 0, 0]
        )
        saat = time.strftime("%H:%M:%S")
        if gonderildi:
            self._son_gonderilen[ad] = (int(pwm), saat)
            k["lbl_son"].setText(f"Son gönderilen: {pwm} µs ({etiket}, {saat})")
            self._durum_yaz(f"[{saat}] {ad} → CH{ch} = {pwm} µs gönderildi", "success")
        else:
            k["lbl_son"].setText(
                f"Son gönderilen: ⚠ GİTMEDİ — {self._kanal.neden_kapali()}"
            )
            self._durum_yaz(
                f"✗ Komut GÖNDERİLEMEDİ — {self._kanal.neden_kapali()}", "danger"
            )

    def _gonder_test(self, ad: str) -> None:
        neden = self._kapali_nedeni()
        if neden:
            self._durum_yaz(f"KAPALI — {neden}", "danger")
            return
        pwm = self._kartlar[ad]["spin"].value()
        self._gonder_pwm(ad, pwm, "test")

    def _gonder_tut(self, ad: str) -> None:
        """Kapatmak/tutmak HER ZAMAN denenir — kapı kontrolü YOK (ACİL DUR felsefesi)."""
        if self._kartlar[ad]["ramp_active"]:
            self._ramp_durdur(ad)
        if not self._kanal.komut_yolu_var():
            self._durum_yaz("✗ Komut kanalı bağlı değil", "danger")
            return
        pc = self._config_pwm.get(ad, {}).get("pwm_close")
        pwm = int(pc) if pc is not None else 1900
        self._gonder_pwm(ad, pwm, "tut/kapat")

    # ══════════════════════════════════════════════════════════════════
    # Yavaş/kontrollü kapanma — elle yük yerleştirirken (2026-09-04)
    # ══════════════════════════════════════════════════════════════════
    def _yavas_kapat(self, ad: str) -> None:
        neden = self._kapali_nedeni()
        if neden:
            self._durum_yaz(f"KAPALI — {neden}", "danger")
            return
        k = self._kartlar[ad]
        if k["ramp_active"]:
            return  # zaten süren bir rampa var
        hedef = self._config_pwm.get(ad, {}).get("pwm_close")
        if hedef is None:
            self._durum_yaz(
                f"✗ {ad}: config'teki pwm_close henüz okunmadı — bekleyin", "danger")
            return
        baslangic = self._config_pwm.get(ad, {}).get("pwm_open")
        if baslangic is None:
            baslangic = k["spin"].value()  # yedek: mevcut test kutusu değeri
        sure_s = k["ramp_spin"].value()
        adim = max(2, int(round(sure_s * 10)))  # ~100 ms adım
        k["ramp_active"] = True
        k["ramp_kalan"] = adim
        k["ramp_su_an"] = float(baslangic)
        k["ramp_delta"] = (float(hedef) - float(baslangic)) / adim
        k["ramp_hedef"] = int(hedef)
        k["btn_yavas"].setEnabled(False)
        k["btn_yavas"].setText("🐢 Kapanıyor...")
        self._durum_yaz(
            f"[{time.strftime('%H:%M:%S')}] {ad} yavaş kapanma başladı "
            f"({baslangic:.0f}→{hedef:.0f} µs, {sure_s:.1f} sn)", "success")
        k["ramp_timer"].start(int(sure_s * 1000 / adim))

    def _ramp_tick(self, ad: str) -> None:
        k = self._kartlar[ad]
        if not k["ramp_active"]:
            return
        k["ramp_kalan"] -= 1
        k["ramp_su_an"] += k["ramp_delta"]
        son_adim = k["ramp_kalan"] <= 0
        pwm = k["ramp_hedef"] if son_adim else int(round(k["ramp_su_an"]))
        self._kanal.komut(self._ml().MAV_CMD_DO_SET_SERVO,
                          [k["channel"], pwm, 0, 0, 0, 0, 0])
        if son_adim:
            self._ramp_bitir(ad, pwm, tamamlandi=True)

    def _ramp_durdur(self, ad: str) -> None:
        """Süren rampayı OLDUĞU yerde durdurur (ör. Tut/Kapat anında basıldı)."""
        k = self._kartlar[ad]
        if k["ramp_active"]:
            k["ramp_timer"].stop()
            self._ramp_bitir(ad, None, tamamlandi=False)

    def _ramp_iptal_ve_kapat(self, ad: str, neden: str) -> None:
        """Kapı kapanınca (onay geri çekildi/ARM oldu) rampayı GÜVENLİ konuma
        (kapalı) anında tamamlar — yarım açık bırakılmaz."""
        k = self._kartlar[ad]
        k["ramp_timer"].stop()
        hedef = k.get("ramp_hedef") or self._config_pwm.get(ad, {}).get("pwm_close") or 1900
        self._kanal.komut(self._ml().MAV_CMD_DO_SET_SERVO,
                          [k["channel"], int(hedef), 0, 0, 0, 0, 0])
        self._ramp_bitir(ad, int(hedef), tamamlandi=False)
        self._durum_yaz(
            f"⚠ {ad} rampası KESİLDİ ({neden}) — güvenli (kapalı) konuma atlandı", "warning")

    def _ramp_bitir(self, ad: str, son_pwm, tamamlandi: bool) -> None:
        k = self._kartlar[ad]
        k["ramp_active"] = False
        k["btn_yavas"].setText("🐢 Yavaş Kapat (rampalı, kontrollü)")
        self._kapilari_uygula(durum_yaz=False)
        if son_pwm is not None:
            saat = time.strftime("%H:%M:%S")
            etiket = "yavaş kapat tamam" if tamamlandi else "yavaş kapat DURDU"
            k["lbl_son"].setText(f"Son gönderilen: {son_pwm} µs ({etiket}, {saat})")
            if tamamlandi:
                self._durum_yaz(f"[{saat}] {ad} yavaş kapanma TAMAMLANDI ({son_pwm} µs)",
                                "success")

    # ══════════════════════════════════════════════════════════════════
    # Panel terk edilince
    # ══════════════════════════════════════════════════════════════════
    def panel_terk_edildi(self) -> None:
        """Onayı düşürür — PervaneKapisi ile aynı gerekçe (bkz. modül üstü not)."""
        if self._kutu.isChecked():
            self._kutu.setChecked(False)
        self._kapilari_uygula()

    def hideEvent(self, event):  # noqa: N802 (Qt API)
        try:
            self.panel_terk_edildi()
        except Exception:
            logger.exception("Servo bırakma test paneli terk edilirken hata")
        super().hideEvent(event)
