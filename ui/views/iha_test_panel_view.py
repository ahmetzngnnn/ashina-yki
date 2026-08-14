"""
ui/views/iha_test_panel_view.py — İHA Devreye Alma Test Paneli
==============================================================

Özellikler:
  • 9 test modülü için sıralı veya seçici çalıştırma
  • Her testin anlık durumu (RUNNING/PASS/FAIL/SKIP)
  • Toplu veya tek test çalıştırma
  • Simülasyon / Gerçek mod seçimi
  • JSON rapor kayıt
  • Test çıktısı terminali
"""

from __future__ import annotations

import json
import time
from typing import Dict, Optional

from PyQt6.QtCore import (Qt, QTimer, pyqtSignal, pyqtSlot)
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QTextEdit, QGroupBox,
    QCheckBox, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QSplitter, QProgressBar, QFrame,
    QMessageBox,
)

from core import theme

# Test tanımları (UI için)
TEST_DEFS = [
    ("01", "Test01Mavlink",        "MAVLink Bağlantı & Telemetri", "BAGLANTI"),
    ("02", "Test02Servo",          "Servo & Gimbal Kontrolü",       "DONANIM"),
    ("03", "Test03Camera",         "Kamera & MJPEG Stream",          "SENSOR"),
    ("04", "Test04Tof",            "TOF Sensör Sistemi",             "SENSOR"),
    ("05", "Test05Vision",         "Vision Pipeline",                "VIZYON"),
    ("06", "Test06TargetTracking", "Hedef Takip & PID",              "VIZYON"),
    ("07", "Test07PayloadDrop",    "Yük Bırakma Sekansı",            "GOREV"),
    ("08", "Test08PayloadGrab",    "Yük Alma Sekansı (Bonus)",        "GOREV"),
    ("09", "Test09FullMission",    "Tam Görev Entegrasyon",           "ENTEGRASYON"),
]

# Test durumu → tema token'ı. Renk SABİT bir sözlükte tutulamaz: tema
# değişince bayat kalır. Bu yüzden token saklanır, renk `status_color()` ile
# çalışma anında `theme.c(...)` üzerinden üretilir.
STATUS_TOKENS = {
    "PASS":    "success",
    "FAIL":    "danger",
    "SKIP":    "warning",
    "WARNING": "warning",
    "RUNNING": "info",
    "PENDING": "text_muted",
}


def status_color(status: str) -> str:
    """Test durum kodunun aktif temadaki hex rengi (bilinmeyen → soluk metin)."""
    return theme.c(STATUS_TOKENS.get(status, "text_muted"))


class IHATestPanelView(QWidget):
    """İHA Devreye Alma Test Paneli"""

    run_test_requested   = pyqtSignal(str, bool)  # (spec, sim_mode)
    abort_test_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._test_statuses: Dict[str, str] = {t[1]: "PENDING" for t in TEST_DEFS}
        self._running: Optional[str] = None
        # Genel durum etiketinin son bilinen tema token'ı (None → QSS class'ı
        # geçerli, inline renk yok). Tema değişince aynı durum yeniden boyanır.
        self._overall_token: Optional[str] = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._refresh_ui)
        self._build_ui()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    # ── UI Kurulumu ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Başlık ──────────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        title = QLabel("İHA Devreye Alma Test Sistemi")
        title.setObjectName("pageTitle")
        header_row.addWidget(title)

        subtitle = QLabel("Her modülü adım adım test et — YKİ üzerinden Companion'a gönder")
        subtitle.setObjectName("mutedLabel")
        header_row.addWidget(subtitle, 0, Qt.AlignmentFlag.AlignBottom)
        header_row.addStretch()
        root.addLayout(header_row)

        # ── Üst Kontroller ───────────────────────────────────────────────────
        ctrl_frame = QFrame()
        ctrl_frame.setObjectName("toolbarFrame")
        ctrl_layout = QHBoxLayout(ctrl_frame)
        ctrl_layout.setContentsMargins(8, 6, 8, 6)
        ctrl_layout.setSpacing(8)

        # Mod seçimi
        self._sim_check = QCheckBox("Simülasyon Modu")
        self._sim_check.setChecked(True)
        ctrl_layout.addWidget(self._sim_check)

        # Seçici
        ctrl_layout.addWidget(QLabel("Test:"))
        self._test_combo = QComboBox()
        self._test_combo.setMinimumWidth(200)
        self._test_combo.addItem("Tüm Testler (01-09)", "all")
        for no, cls_name, name, _ in TEST_DEFS:
            self._test_combo.addItem(f"{no}. {name}", no)
        self._test_combo.addItem("─── Kategoriler ───", None)
        for cat in ["BAGLANTI", "DONANIM", "SENSOR", "VIZYON", "GOREV"]:
            self._test_combo.addItem(f"  Kategori: {cat}", cat)
        ctrl_layout.addWidget(self._test_combo)

        # Butonlar
        self._btn_run = QPushButton("▶  Çalıştır")
        self._btn_run.setObjectName("primaryButton")
        self._btn_run.setMinimumHeight(30)
        self._btn_run.clicked.connect(self._on_run_clicked)
        ctrl_layout.addWidget(self._btn_run)

        self._btn_abort = QPushButton("⏹  Durdur")
        self._btn_abort.setObjectName("dangerButton")
        self._btn_abort.setMinimumHeight(30)
        self._btn_abort.setEnabled(False)
        self._btn_abort.clicked.connect(self.abort_test_requested)
        ctrl_layout.addWidget(self._btn_abort)

        self._btn_clear = QPushButton("🗑  Temizle")
        self._btn_clear.setMinimumHeight(30)
        self._btn_clear.clicked.connect(self._clear_results)
        ctrl_layout.addWidget(self._btn_clear)

        ctrl_layout.addStretch()

        # Genel durum
        self._overall_label = QLabel("Hazır")
        self._overall_label.setObjectName("mutedLabel")
        ctrl_layout.addWidget(self._overall_label)

        root.addWidget(ctrl_frame)

        # ── Ana Bölücü ───────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ─ Test Listesi ────────────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        lbl = QLabel("Test Modülleri")
        lbl.setObjectName("sectionTitle")
        left_layout.addWidget(lbl)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["#", "Test Adı", "Kategori", "Durum"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(30)

        for i, (no, cls_name, name, cat) in enumerate(TEST_DEFS):
            self._table.insertRow(i)
            self._table.setItem(i, 0, QTableWidgetItem(no))
            self._table.setItem(i, 1, QTableWidgetItem(name))
            self._table.setItem(i, 2, QTableWidgetItem(cat))
            status_item = QTableWidgetItem("BEKLEMEDE")
            status_item.setForeground(QColor(status_color("PENDING")))
            self._table.setItem(i, 3, status_item)

        self._table.resizeColumnsToContents()
        self._table.setColumnWidth(0, 30)
        left_layout.addWidget(self._table)

        # İlerleme çubuğu
        self._progress = QProgressBar()
        self._progress.setRange(0, len(TEST_DEFS))
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        left_layout.addWidget(self._progress)

        # Özet
        self._summary_label = QLabel("─")
        self._summary_label.setObjectName("mutedLabel")
        left_layout.addWidget(self._summary_label)

        splitter.addWidget(left)

        # ─ Test Çıktısı ────────────────────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        lbl2 = QLabel("Test Çıktısı")
        lbl2.setObjectName("sectionTitle")
        right_layout.addWidget(lbl2)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(QFont("Courier New", 10))
        right_layout.addWidget(self._output)

        # Hızlı butonlar (tek test)
        quick_frame = QFrame()
        quick_layout = QHBoxLayout(quick_frame)
        quick_layout.setSpacing(6)
        for no, _, name, _ in TEST_DEFS:
            btn = QPushButton(f"{no}")
            btn.setToolTip(f"Test {no}: {name}")
            btn.setObjectName("denseButton")   # normal buton padding'i 2 haneyi yutar
            btn.setFixedWidth(34)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda checked, n=no: self._run_single(n))
            quick_layout.addWidget(btn)
        quick_layout.addStretch()
        quick_lbl = QLabel("Tek test çalıştır:")
        quick_lbl.setObjectName("mutedLabel")
        quick_layout.insertWidget(0, quick_lbl)
        right_layout.addWidget(quick_frame)

        splitter.addWidget(right)
        splitter.setSizes([420, 580])
        root.addWidget(splitter, 1)

    # ── Slot'lar ─────────────────────────────────────────────────────────────

    @pyqtSlot()
    def _on_run_clicked(self) -> None:
        spec     = self._test_combo.currentData() or "all"
        sim_mode = self._sim_check.isChecked()
        self._start_run(spec, sim_mode)

    def _run_single(self, test_no: str) -> None:
        sim_mode = self._sim_check.isChecked()
        self._start_run(test_no, sim_mode)

    def _start_run(self, spec: str, sim_mode: bool) -> None:
        self._output.clear()
        self._log(f"Test başlatılıyor: {spec}  [{'SİM' if sim_mode else 'GERÇEK'}]")
        self._btn_run.setEnabled(False)
        self._btn_abort.setEnabled(True)
        self._set_overall("ÇALIŞIYOR...", "info")
        self._poll_timer.start()
        self.run_test_requested.emit(spec, sim_mode)

    @pyqtSlot()
    def _clear_results(self) -> None:
        self._output.clear()
        self._test_statuses = {t[1]: "PENDING" for t in TEST_DEFS}
        self._refresh_ui()
        self._set_overall("Hazır", "text_muted")
        self._summary_label.setText("─")
        self._progress.setValue(0)

    def _set_overall(self, text: str, token: str | None) -> None:
        """Genel durum etiketini metin + tema token'ı ile yazar.

        Token saklanır; tema değişince `_apply_theme_colors` aynı durumu
        yeniden boyar (kurulumda yazılan renk bayat kalmasın)."""
        self._overall_token = token
        self._overall_label.setText(text)
        if token is None:
            self._overall_label.setStyleSheet("")
        else:
            self._overall_label.setStyleSheet(
                f"color:{theme.c(token)};font-weight:bold;"
            )

    def _apply_theme_colors(self) -> None:
        """Tema değişince inline yazılmış renkleri yeniden üretir.

        Genel durum etiketi son bilinen token'la, tablo durum hücreleri ise
        `_refresh_ui` üzerinden mevcut test durumlarıyla yeniden boyanır.

        NEDEN metin saklanıyor: `_refresh_ui` sonda `_summary_label`'ı sayaçlarla
        EZİYOR. Tema değişimi salt görsel olmalı — `_finish_run`'ın yazdığı
        "Test tamamlanamadı — <hata>" gibi mesajlar kaybolmamalı."""
        self._set_overall(self._overall_label.text(), self._overall_token)
        _summary = self._summary_label.text()
        self._refresh_ui()
        self._summary_label.setText(_summary)

    # ── Status Güncelleme ─────────────────────────────────────────────────────

    @pyqtSlot(dict)
    def update_test_status(self, status_data: dict) -> None:
        """RpiStatusWorker'dan gelen status paketinde test_status alanını işle."""
        test_status = status_data.get("test_status", {})
        if not test_status:
            return

        results = test_status.get("results", {})
        running = test_status.get("running")

        for cls_name, status_str in results.items():
            self._test_statuses[cls_name] = status_str

        if running:
            self._running = running
            self._test_statuses[running] = "RUNNING"
        else:
            self._running = None
            if self._btn_abort.isEnabled():
                self._finish_run(test_status)

        self._refresh_ui()

    def _finish_run(self, test_status: dict) -> None:
        self._poll_timer.stop()
        self._btn_run.setEnabled(True)
        self._btn_abort.setEnabled(False)

        summary = test_status.get("summary", {})
        passed  = summary.get("passed", 0)
        failed  = summary.get("failed", 0)
        skipped = summary.get("skipped", 0)

        # Test altyapısı çökerse İHA {"running":None,"error":...} döner —
        # summary HİÇ gelmez. Eski kod yalnızca `failed == 0` baktığı için
        # bu durumda yeşil "PASS 0✓" gösteriyordu ve operatör çökmüş bir test
        # paketini "geçti" sanıp uçuşa devam edebiliyordu. Önce hata/boş
        # sonuç durumlarını ele al.
        err = test_status.get("error")
        aborted = bool(test_status.get("aborted"))
        if err or aborted or (passed + failed + skipped) == 0:
            if aborted:
                txt, tok = "İPTAL EDİLDİ", "warning"
            elif err:
                txt, tok = "HATA", "danger"
            else:
                txt, tok = "SONUÇ YOK", "danger"
            self._set_overall(txt, tok)
            self._summary_label.setText(
                f"Test tamamlanamadı — {err}" if err else
                ("Test operatör tarafından iptal edildi" if aborted
                 else "Companion'dan sonuç gelmedi")
            )
            self._log(f"\n✗ Test tamamlanamadı: {err or ('iptal' if aborted else 'sonuç yok')}")
            return

        if failed == 0:
            self._set_overall(f"PASS  {passed}✓", "success")
        else:
            self._set_overall(f"FAIL  {passed}✓ {failed}✗", "danger")

        self._summary_label.setText(
            f"Geçti: {passed}  Başarısız: {failed}  Atlandı: {skipped}"
        )
        self._log(f"\nTamamlandı — Geçti:{passed}  Başarısız:{failed}  Atlandı:{skipped}")

    @pyqtSlot(str)
    def append_log(self, message: str) -> None:
        """Görev bildirimlerini test çıktısına ekle."""
        self._log(f"  [Bildirim] {message}")

    def _log(self, text: str) -> None:
        self._output.append(text)
        self._output.verticalScrollBar().setValue(
            self._output.verticalScrollBar().maximum()
        )

    def _refresh_ui(self) -> None:
        """Tabloyu ve ilerleme çubuğunu güncelle."""
        passed = failed = skipped = done = 0
        for i, (no, cls_name, name, cat) in enumerate(TEST_DEFS):
            status = self._test_statuses.get(cls_name, "PENDING")
            item = self._table.item(i, 3)
            if item is None:
                continue
            color = status_color(status)
            label = {
                "PASS": "✓ PASS", "FAIL": "✗ FAIL", "SKIP": "◌ ATLANIDI",
                "WARNING": "⚠ UYARI", "RUNNING": "⏳ ÇALIŞIYOR", "PENDING": "○ BEKLEMEDE"
            }.get(status, status)
            item.setText(label)
            item.setForeground(QColor(color))

            if status == "PASS":    passed  += 1; done += 1
            elif status == "FAIL":  failed  += 1; done += 1
            elif status == "SKIP":  skipped += 1; done += 1

        self._progress.setValue(done)
        if done > 0:
            self._summary_label.setText(
                f"Geçti: {passed}  Başarısız: {failed}  Atlandı: {skipped}"
            )
