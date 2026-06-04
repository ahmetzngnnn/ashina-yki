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

STATUS_COLORS = {
    "PASS":    "#06d6a0",
    "FAIL":    "#ef233c",
    "SKIP":    "#ff8700",
    "WARNING": "#ff8700",
    "RUNNING": "#00b4d8",
    "PENDING": "#8b949e",
}


class IHATestPanelView(QWidget):
    """İHA Devreye Alma Test Paneli"""

    run_test_requested   = pyqtSignal(str, bool)  # (spec, sim_mode)
    abort_test_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._test_statuses: Dict[str, str] = {t[1]: "PENDING" for t in TEST_DEFS}
        self._running: Optional[str] = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._refresh_ui)
        self._build_ui()

    # ── UI Kurulumu ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── Başlık ──────────────────────────────────────────────────────────
        title = QLabel("İHA Devreye Alma Test Sistemi")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #00b4d8; padding: 4px 0;")
        root.addWidget(title)

        subtitle = QLabel("Her modülü adım adım test et — YKİ üzerinden Companion'a gönder")
        subtitle.setStyleSheet("color: #8b949e; font-size: 11px;")
        root.addWidget(subtitle)

        # ── Üst Kontroller ───────────────────────────────────────────────────
        ctrl_frame = QFrame()
        ctrl_frame.setFrameShape(QFrame.Shape.StyledPanel)
        ctrl_frame.setStyleSheet("QFrame{background:#1c2526;border-radius:6px;padding:4px;}")
        ctrl_layout = QHBoxLayout(ctrl_frame)
        ctrl_layout.setSpacing(8)

        # Mod seçimi
        self._sim_check = QCheckBox("Simülasyon Modu")
        self._sim_check.setChecked(True)
        self._sim_check.setStyleSheet("color:#e0e0e0;")
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
        self._btn_run.setStyleSheet(
            "QPushButton{background:#065ea5;color:white;padding:6px 16px;"
            "border-radius:4px;font-weight:bold;}"
            "QPushButton:hover{background:#0078d7;}"
        )
        self._btn_run.clicked.connect(self._on_run_clicked)
        ctrl_layout.addWidget(self._btn_run)

        self._btn_abort = QPushButton("⏹  Durdur")
        self._btn_abort.setStyleSheet(
            "QPushButton{background:#3d1f1f;color:#ef233c;padding:6px 16px;"
            "border-radius:4px;font-weight:bold;}"
            "QPushButton:hover{background:#5a2020;}"
        )
        self._btn_abort.setEnabled(False)
        self._btn_abort.clicked.connect(self.abort_test_requested)
        ctrl_layout.addWidget(self._btn_abort)

        self._btn_clear = QPushButton("🗑  Temizle")
        self._btn_clear.setStyleSheet(
            "QPushButton{background:#2a2a2a;color:#8b949e;padding:6px 12px;"
            "border-radius:4px;}"
        )
        self._btn_clear.clicked.connect(self._clear_results)
        ctrl_layout.addWidget(self._btn_clear)

        ctrl_layout.addStretch()

        # Genel durum
        self._overall_label = QLabel("Hazır")
        self._overall_label.setStyleSheet("color:#8b949e;font-weight:bold;")
        ctrl_layout.addWidget(self._overall_label)

        root.addWidget(ctrl_frame)

        # ── Ana Bölücü ───────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ─ Test Listesi ────────────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel("Test Modülleri")
        lbl.setStyleSheet("color:#8b949e;font-size:10px;padding:2px;")
        left_layout.addWidget(lbl)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["#", "Test Adı", "Kategori", "Durum"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            "QTableWidget{background:#0d1b2a;color:#e0e0e0;border:none;}"
            "QTableWidget::item:selected{background:#065ea5;}"
            "QHeaderView::section{background:#1c2526;color:#8b949e;border:none;padding:4px;}"
        )

        for i, (no, cls_name, name, cat) in enumerate(TEST_DEFS):
            self._table.insertRow(i)
            self._table.setItem(i, 0, QTableWidgetItem(no))
            self._table.setItem(i, 1, QTableWidgetItem(name))
            self._table.setItem(i, 2, QTableWidgetItem(cat))
            status_item = QTableWidgetItem("BEKLEMEDE")
            status_item.setForeground(QColor(STATUS_COLORS["PENDING"]))
            self._table.setItem(i, 3, status_item)

        self._table.resizeColumnsToContents()
        self._table.setColumnWidth(0, 30)
        left_layout.addWidget(self._table)

        # İlerleme çubuğu
        self._progress = QProgressBar()
        self._progress.setRange(0, len(TEST_DEFS))
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setStyleSheet(
            "QProgressBar{background:#1c2526;border-radius:4px;color:white;}"
            "QProgressBar::chunk{background:#065ea5;border-radius:4px;}"
        )
        left_layout.addWidget(self._progress)

        # Özet
        self._summary_label = QLabel("─")
        self._summary_label.setStyleSheet("color:#8b949e;font-size:10px;padding:2px;")
        left_layout.addWidget(self._summary_label)

        splitter.addWidget(left)

        # ─ Test Çıktısı ────────────────────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        lbl2 = QLabel("Test Çıktısı")
        lbl2.setStyleSheet("color:#8b949e;font-size:10px;padding:2px;")
        right_layout.addWidget(lbl2)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(QFont("Courier New", 9))
        self._output.setStyleSheet(
            "QTextEdit{background:#0d1b2a;color:#a8d8ff;border:none;}"
        )
        right_layout.addWidget(self._output)

        # Hızlı butonlar (tek test)
        quick_frame = QFrame()
        quick_layout = QHBoxLayout(quick_frame)
        quick_layout.setSpacing(4)
        for no, _, name, _ in TEST_DEFS:
            btn = QPushButton(f"{no}")
            btn.setToolTip(f"Test {no}: {name}")
            btn.setFixedWidth(32)
            btn.setStyleSheet(
                "QPushButton{background:#1c2526;color:#e0e0e0;border-radius:3px;"
                "font-size:10px;padding:2px;}"
                "QPushButton:hover{background:#065ea5;}"
            )
            btn.clicked.connect(lambda checked, n=no: self._run_single(n))
            quick_layout.addWidget(btn)
        quick_layout.addStretch()
        quick_lbl = QLabel("Tek test çalıştır:")
        quick_lbl.setStyleSheet("color:#8b949e;font-size:10px;")
        quick_layout.insertWidget(0, quick_lbl)
        right_layout.addWidget(quick_frame)

        splitter.addWidget(right)
        splitter.setSizes([380, 620])
        root.addWidget(splitter)

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
        self._overall_label.setText("ÇALIŞIYOR...")
        self._overall_label.setStyleSheet("color:#00b4d8;font-weight:bold;")
        self._poll_timer.start()
        self.run_test_requested.emit(spec, sim_mode)

    @pyqtSlot()
    def _clear_results(self) -> None:
        self._output.clear()
        self._test_statuses = {t[1]: "PENDING" for t in TEST_DEFS}
        self._refresh_ui()
        self._overall_label.setText("Hazır")
        self._overall_label.setStyleSheet("color:#8b949e;font-weight:bold;")
        self._summary_label.setText("─")
        self._progress.setValue(0)

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

        if failed == 0:
            self._overall_label.setText(f"PASS  {passed}✓")
            self._overall_label.setStyleSheet(
                "color:#06d6a0;font-weight:bold;font-size:12px;"
            )
        else:
            self._overall_label.setText(f"FAIL  {passed}✓ {failed}✗")
            self._overall_label.setStyleSheet(
                "color:#ef233c;font-weight:bold;font-size:12px;"
            )

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
            color = STATUS_COLORS.get(status, STATUS_COLORS["PENDING"])
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
