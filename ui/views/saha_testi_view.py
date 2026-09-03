"""
ui/views/saha_testi_view.py — Saha Testi (PERVANESİZ, GERÇEK) Paneli
=====================================================================

AMAÇ (2026-09-03 operatör talebi): gerçek uçuşta yaşanabilecek HER senaryoyu
uçmadan önce, PERVANESİZ ama başka her şey gerçekken denemek. Motorlar döner,
araç ARM olur, Raspberry Pi'nin gücü kesilebilir, Pixhawk kablosu çekilebilir.

Panel bir SENARYO KOŞUCUSUDUR:
  • her senaryo için operatörün ne yapacağı, sistemin ne yapması gerektiği ve
    nereden gözleneceği yazılıdır (bkz. ui/views/saha_senaryolari.py — her
    madde kaynak koddan doğrulanarak yazıldı),
  • canlı durum tahtası ARM/kip/batarya/GPS/sağlık/bağlantıları gösterir,
  • olay günlüğü kip ve sağlık geçişlerini, FC mesajlarını zaman damgasıyla
    KENDİLİĞİNDEN kaydeder — operatör not tutmakla uğraşmaz,
  • sonuçlar Markdown rapor olarak kaydedilebilir.

GÜVENLİK
  • PervaneKapisi onayı verilmeden senaryo düğmeleri AÇILMAZ. Onay panel
    terk edilince DÜŞER (bkz. o sınıfın NEDEN notu).
  • ACİL DISARM her koşulda ETKİNDİR — durdurmak her zaman güvenlidir.
    MAV_CMD_COMPONENT_ARM_DISARM(400), param1=0, param2=21196 (ArduPilot
    magic_force_arm_disarm_value, GCS.h:789) → ön kontroller ATLANIR.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, pyqtSlot, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QGroupBox, QSplitter, QTextEdit, QApplication,
    QScrollArea, QMessageBox, QFileDialog,
)

from core import theme
from core.gcs_config import config
from core.video_stream_worker import VideoStreamWorker
from ui.widgets.panel_ortak import PervaneKapisi
from ui.views.saha_senaryolari import SENARYOLAR

# ArduPilot: ön kontrolleri ATLAYAN zorla-disarm (GCS.h:789).
_ZORLA_DISARM = 21196.0
_CMD_ARM_DISARM = 400


class SahaTestiView(QWidget):
    """Pervanesiz, gerçek donanımda arıza senaryosu koşucusu."""

    # ACİL DISARM için — main_window bunu mavlink_worker.send_command'a bağlar.
    send_command = pyqtSignal(int, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._armed = False
        self._mode = "?"
        self._link_fc = False
        self._link_companion = False
        self._sonuc: dict = {}          # senaryo adı -> "gecti"/"kaldi"
        self._notlar: dict = {}
        self._olaylar: list = []

        self.video_worker = VideoStreamWorker(source="none")
        self.video_worker.frame_ready.connect(self._on_frame)
        self.video_worker.error_occurred.connect(self._on_video_error)
        self.video_worker.start()

        self._build_ui()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())
        self._kapi_degisti(False)

    # ── Arayüz ────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        baslik = QLabel("🧪  SAHA TESTİ — PERVANESİZ, GERÇEK SENARYOLAR")
        baslik.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        root.addWidget(baslik)

        self._kapi = PervaneKapisi(
            ek_uyari="  5) Bu panel aracı ARM ETTİRİR ve motorları DÖNDÜRÜR. "
                     "Güvenlik pilotu kumanda başında olsun.\n"
                     "  6) ÜST ÇUBUKTAKİ '▶ Görev Başlat (FCU)' DÜĞMESİNE ASLA "
                     "BASMAYIN — o companion'ı atlayıp doğrudan FC'ye ARM+tırmanma "
                     "gazı gönderir, sağlık kapısına tabi DEĞİLDİR. Yalnız "
                     "'▶ Görev Başlat (Yard. PC)' kullanın.")
        self._kapi.durum_degisti.connect(self._kapi_degisti)
        root.addWidget(self._kapi)

        acil_row = QHBoxLayout()
        self._btn_disarm = QPushButton("⛔  ACİL DISARM  (zorla — ön kontroller atlanır)")
        self._btn_disarm.setObjectName("dangerButton")
        self._btn_disarm.setMinimumHeight(44)
        self._btn_disarm.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._btn_disarm.clicked.connect(self._acil_disarm)
        acil_row.addWidget(self._btn_disarm)
        root.addLayout(acil_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── SOL: canlı durum + video ──────────────────────────────────
        sol = QWidget(); sl = QVBoxLayout(sol)
        sl.setContentsMargins(0, 0, 6, 0)

        durum_box = QGroupBox("Canlı durum")
        dg = QGridLayout(durum_box)
        self._d = {}
        alanlar = [("arm", "ARM"), ("mode", "Uçuş kipi"), ("batt", "Batarya"),
                   ("gps", "GPS"), ("saglik", "Sağlık"), ("fc", "FC linki"),
                   ("comp", "Yardımcı bilg.")]
        for i, (k, ad) in enumerate(alanlar):
            dg.addWidget(QLabel(f"{ad}:"), i // 2, (i % 2) * 2)
            lb = QLabel("—"); lb.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            self._d[k] = lb
            dg.addWidget(lb, i // 2, (i % 2) * 2 + 1)
        sl.addWidget(durum_box)

        conn = QFrame(); cl = QHBoxLayout(conn)
        cl.addWidget(QLabel("Bench video IP:"))
        self._ip_edit = QLineEdit(config.companion_host)
        self._ip_edit.setMaximumWidth(130); cl.addWidget(self._ip_edit)
        cl.addWidget(QLabel("Port:"))
        self._port_edit = QLineEdit("8090"); self._port_edit.setMaximumWidth(60)
        cl.addWidget(self._port_edit)
        self._btn_connect = QPushButton("▶ Bağlan")
        self._btn_connect.clicked.connect(self._on_connect_clicked)
        cl.addWidget(self._btn_connect)
        self._status_lbl = QLabel("● bağlı değil"); cl.addWidget(self._status_lbl)
        cl.addStretch(1)
        sl.addWidget(conn)

        self._video_label = QLabel("Bench videosu (isteğe bağlı)")
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setMinimumHeight(190)
        self._video_label.setObjectName("cardFrame")
        sl.addWidget(self._video_label, 1)

        log_box = QGroupBox("Olay günlüğü (kendiliğinden kaydedilir)")
        lgl = QVBoxLayout(log_box)
        self._log = QTextEdit(); self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 8))
        lgl.addWidget(self._log)
        btn_row = QHBoxLayout()
        b_temizle = QPushButton("Günlüğü temizle")
        b_temizle.clicked.connect(lambda: (self._log.clear(), self._olaylar.clear()))
        btn_row.addWidget(b_temizle)
        b_rapor = QPushButton("💾 Raporu kaydet")
        b_rapor.clicked.connect(self._rapor_kaydet)
        btn_row.addWidget(b_rapor)
        lgl.addLayout(btn_row)
        sl.addWidget(log_box, 1)
        splitter.addWidget(sol)

        # ── SAĞ: senaryolar ───────────────────────────────────────────
        kaydir = QScrollArea(); kaydir.setWidgetResizable(True)
        ic = QWidget(); il = QVBoxLayout(ic)
        il.setContentsMargins(4, 4, 4, 4)
        self._senaryo_btn: list = []
        son_grup = None
        for sn in SENARYOLAR:
            if sn["grup"] != son_grup:
                son_grup = sn["grup"]
                gl = QLabel(son_grup)
                gl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
                gl.setStyleSheet(f"color:{theme.c('info')};margin-top:6px;")
                il.addWidget(gl)
            il.addWidget(self._senaryo_karti(sn))
        il.addStretch(1)
        kaydir.setWidget(ic)
        splitter.addWidget(kaydir)
        splitter.setSizes([520, 640])
        root.addWidget(splitter, 1)

    def _senaryo_karti(self, sn: dict) -> QWidget:
        kart = QGroupBox(sn["ad"])
        g = QVBoxLayout(kart)
        g.setSpacing(3)

        def satir(etiket, metin, renk=None):
            lb = QLabel(f"<b>{etiket}</b> {metin}")
            lb.setWordWrap(True)
            if renk:
                lb.setStyleSheet(f"color:{theme.c(renk)};")
            g.addWidget(lb)

        satir("Ne yapacaksın:", sn["tetik"])
        satir("Sistem ne yapmalı:", sn["beklenen"])
        satir("Nereden göreceksin:", sn["gozle"], "text_muted")
        if sn.get("geri"):
            satir("Normale dönüş:", sn["geri"], "text_muted")
        if sn.get("tehlike"):
            satir("⚠ DİKKAT:", sn["tehlike"], "danger")

        not_kutu = QLineEdit()
        not_kutu.setPlaceholderText("Gözlem notu (rapora girer)…")
        not_kutu.textChanged.connect(
            lambda t, ad=sn["ad"]: self._notlar.__setitem__(ad, t))
        g.addWidget(not_kutu)

        row = QHBoxLayout()
        b_ok = QPushButton("✓ GEÇTİ"); b_ok.setObjectName("primaryButton")
        b_no = QPushButton("✗ KALDI"); b_no.setObjectName("dangerButton")
        durum = QLabel("—")
        for b, sonuc in ((b_ok, "gecti"), (b_no, "kaldi")):
            b.setEnabled(False)
            b.clicked.connect(
                lambda _c=False, ad=sn["ad"], s=sonuc, d=durum: self._isaretle(ad, s, d))
            row.addWidget(b)
            self._senaryo_btn.append(b)
        row.addWidget(durum, 1)
        g.addLayout(row)
        return kart

    # ── Güvenlik kapısı ───────────────────────────────────────────────
    def _kapi_degisti(self, acik: bool) -> None:
        for b in self._senaryo_btn:
            b.setEnabled(acik)
        # ACİL DISARM HER ZAMAN açık: durdurmak her koşulda güvenlidir.
        self._btn_disarm.setEnabled(True)
        self._olay("GÜVENLİK", "Pervane kapısı %s" % ("AÇIK" if acik else "KAPALI"))

    def panel_terk_edildi(self) -> None:
        """Sekmeden çıkılınca onay DÜŞER (main_window çağırır)."""
        try:
            self._kapi.sifirla()
        except Exception:
            pass

    # ── Acil disarm ───────────────────────────────────────────────────
    def _acil_disarm(self) -> None:
        yanit = QMessageBox.question(
            self, "ACİL DISARM",
            "Araç ZORLA disarm edilecek — ArduPilot ön kontrolleri ATLANIR.\n\n"
            "Havadaysa DÜŞER. Yalnızca yerdeyken ya da gerçek acil durumda kullanın.\n\n"
            "Devam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if yanit != QMessageBox.StandardButton.Yes:
            return
        try:
            self.send_command.emit(_CMD_ARM_DISARM,
                                   [0.0, _ZORLA_DISARM, 0, 0, 0, 0, 0])
            self._olay("ACİL", "ZORLA DISARM komutu gönderildi (param2=21196)")
        except Exception as exc:
            self._olay("HATA", f"Disarm gönderilemedi: {exc}")

    # ── Sonuç işaretleme ──────────────────────────────────────────────
    def _isaretle(self, ad: str, sonuc: str, etiket: QLabel) -> None:
        self._sonuc[ad] = sonuc
        if sonuc == "gecti":
            etiket.setText("✓ GEÇTİ  " + time.strftime("%H:%M:%S"))
            etiket.setStyleSheet(f"color:{theme.c('success')};font-weight:bold;")
        else:
            etiket.setText("✗ KALDI  " + time.strftime("%H:%M:%S"))
            etiket.setStyleSheet(f"color:{theme.c('danger')};font-weight:bold;")
        self._olay("SONUÇ", f"{ad} → {sonuc.upper()}")

    # ── Olay günlüğü ──────────────────────────────────────────────────
    def _olay(self, tur: str, metin: str) -> None:
        satir = f"{time.strftime('%H:%M:%S')}  [{tur}] {metin}"
        self._olaylar.append(satir)
        self._log.append(satir)
        sb = self._log.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    # ── Dış girdiler ──────────────────────────────────────────────────
    @pyqtSlot(dict)
    def update_telemetry(self, data: dict) -> None:
        armed = bool(data.get("armed", False))
        if armed != self._armed:
            self._armed = armed
            self._olay("ARM", "ARM EDİLDİ — motorlar dönüyor" if armed else "DISARM edildi")
        self._d["arm"].setText("ARMED" if armed else "disarmed")
        self._d["arm"].setStyleSheet(
            f"color:{theme.c('danger' if armed else 'text_muted')};font-weight:bold;")

        mode = str(data.get("mode", "") or "?")
        if mode != self._mode:
            self._olay("KİP", f"{self._mode} → {mode}")
            self._mode = mode
        self._d["mode"].setText(mode)

        v = data.get("battery_voltage")
        self._d["batt"].setText(f"{float(v):.2f} V" if v else "—")
        fix = data.get("gps_fix_name") or data.get("gps_fix_type")
        sat = data.get("gps_sats")
        self._d["gps"].setText(f"{fix} · {sat} uydu" if fix is not None else "—")

    @pyqtSlot(str, str, str)
    def on_health_alert(self, subsystem: str, level: str, message: str) -> None:
        self._d["saglik"].setText(f"{subsystem}: {level}")
        self._d["saglik"].setStyleSheet(
            f"color:{theme.c('danger' if level in ('CRITICAL', 'FAILED') else 'warning')};"
            "font-weight:bold;")
        self._olay("SAĞLIK", f"[{level}] {subsystem}: {message[:90]}")

    @pyqtSlot(str, int)
    def on_status_text(self, text: str, severity: int) -> None:
        if text:
            self._olay("FC", text.strip()[:110])

    @pyqtSlot(bool)
    def on_connection_changed(self, bagli: bool) -> None:
        self._link_fc = bagli
        self._d["fc"].setText("bağlı" if bagli else "KOPUK")
        self._d["fc"].setStyleSheet(
            f"color:{theme.c('success' if bagli else 'danger')};font-weight:bold;")
        self._olay("LİNK", "FC linki " + ("kuruldu" if bagli else "KOPTU"))

    @pyqtSlot(bool)
    def on_companion_changed(self, bagli: bool) -> None:
        self._link_companion = bagli
        self._d["comp"].setText("bağlı" if bagli else "KOPUK")
        self._d["comp"].setStyleSheet(
            f"color:{theme.c('success' if bagli else 'danger')};font-weight:bold;")
        self._olay("LİNK", "Yardımcı bilgisayar " + ("bağlandı" if bagli else "KOPTU"))

    # ── Rapor ─────────────────────────────────────────────────────────
    def _rapor_kaydet(self) -> None:
        yol, _ = QFileDialog.getSaveFileName(
            self, "Saha testi raporu",
            time.strftime("saha_testi_%Y%m%d_%H%M.md"), "Markdown (*.md)")
        if not yol:
            return
        sat = ["# ASHİNA — Pervanesiz Saha Testi Raporu", "",
               f"**Tarih:** {time.strftime('%Y-%m-%d %H:%M')}", "",
               "## Senaryo sonuçları", "",
               "| Grup | Senaryo | Sonuç | Not |", "|---|---|---|---|"]
        for sn in SENARYOLAR:
            s = self._sonuc.get(sn["ad"], "—")
            isaret = {"gecti": "✓ GEÇTİ", "kaldi": "✗ KALDI"}.get(s, "denenmedi")
            sat.append("| %s | %s | %s | %s |"
                       % (sn["grup"], sn["ad"], isaret,
                          (self._notlar.get(sn["ad"], "") or "").replace("|", "/")))
        sat += ["", "## Olay günlüğü", "", "```"] + self._olaylar + ["```", ""]
        try:
            with open(yol, "w", encoding="utf-8") as f:
                f.write("\n".join(sat))
            self._olay("RAPOR", f"kaydedildi: {yol}")
        except Exception as exc:
            QMessageBox.warning(self, "Rapor", f"Kaydedilemedi: {exc}")

    # ── Video (mevcut davranış korundu) ───────────────────────────────
    @pyqtSlot()
    def _on_connect_clicked(self) -> None:
        if self._connected:
            self.video_worker.set_source("none")
            self._connected = False
            self._btn_connect.setText("▶ Bağlan")
            self._status_lbl.setText("● bağlı değil")
            self._video_label.setPixmap(QPixmap())
            self._video_label.setText("Bağlantı kesildi")
            return
        host = self._ip_edit.text().strip() or config.companion_host
        try:
            port = int(self._port_edit.text().strip())
        except ValueError:
            port = 8090
        url = f"http://{host}:{port}/video"
        self.video_worker.set_source(url)
        self._connected = True
        self._btn_connect.setText("⏹ Kes")
        self._status_lbl.setText("● bağlanıyor")

    @pyqtSlot(QImage)
    def _on_frame(self, q_img: QImage) -> None:
        if not self._connected or q_img.isNull():
            return
        self._status_lbl.setText("● canlı")
        self._status_lbl.setStyleSheet(f"color:{theme.c('success')};font-weight:bold;")
        px = QPixmap.fromImage(q_img)
        self._video_label.setPixmap(px.scaled(
            self._video_label.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    @pyqtSlot(str)
    def _on_video_error(self, msg: str) -> None:
        if self._connected:
            self._status_lbl.setText(f"● hata: {msg[:40]}")
            self._status_lbl.setStyleSheet(f"color:{theme.c('danger')};font-weight:bold;")

    def _apply_theme_colors(self) -> None:
        self._status_lbl.setStyleSheet(
            f"color:{theme.c('success' if self._connected else 'text_muted')};font-weight:bold;")
