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

from PyQt6.QtCore import Qt, pyqtSlot, pyqtSignal, QTimer
from PyQt6.QtGui import QImage, QPixmap, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QGroupBox, QCheckBox, QSplitter, QTextEdit,
    QApplication, QScrollArea, QMessageBox, QFileDialog, QTabWidget,
    QSizePolicy, QComboBox, QDialog,
)

from core import theme
from core.gcs_config import config
from core.video_stream_worker import VideoStreamWorker
from core.vehicle_bench_control import VehicleBenchStartWorker, VehicleBenchStopWorker
from core.otonom_align_test import OtonomAlignTestWorker
from ui.widgets.panel_ortak import PervaneKapisi
from ui.views.saha_senaryolari import SENARYOLAR

# ArduPilot: ön kontrolleri ATLAYAN zorla-disarm (GCS.h:789).
_ZORLA_DISARM = 21196.0
_CMD_ARM_DISARM = 400

# ── Görev 2/3 gerçek görev — insan-rehberli bench (bkz. GercekGorevPaneli) ──
_GOREV_ASAMALARI = [
    ("kirmizi", "1. KIRMIZI hedef — hizalama + otonom bırakma",
     "python3 tools/human_guided_bench.py --renk kirmizi"),
    ("mavi", "2. MAVİ hedef — hizalama + otonom bırakma",
     "python3 tools/human_guided_bench.py --renk mavi"),
    ("yuk", "3. Yük alma — alçalma + otonom kavrama (HENÜZ UYGULANMADI)",
     "— (bkz. plan §E madde 3, ayrı bir sonraki adım)"),
]

_GOREV_UYARI_METNI = (
    "⚠ PERVANELER SÖKÜLÜ OLMALI. Bu test aracı GERÇEKTEN ARM EDER (motorlar "
    "döner) ve GUIDED moduna geçirir — normal GPS/EKF ön-uçuş kontrolleri "
    "(force=False) AYNEN uygulanır, atlanmaz.\n"
    "Aracın fiziksel konumunu SİZ elinizle değiştirirsiniz — companion "
    "betiği hiçbir zaman uçuş hızı/pozisyon komutu göndermez, yalnızca "
    "ARM/mod ve (hedef merkezlendiğinde) bırakma servosu komutu gönderir.\n"
    "🔴 Araç Pi'de ashina-iha.service ÇALIŞIYORSA ÖNCE DURDURUN (sudo "
    "systemctl stop ashina-iha) — bu betik AYNI FC seri portuna bağlanır, "
    "servis çalışırken ikinci bağlantı heartbeat'i bozar."
)


class GercekGorevPaneli(QWidget):
    """Görev 2/3 insan-rehberli bench testini İZLEYEN bağımsız panel.

    2026-09-03'te saha_testi_view arıza-senaryo koşucusuna dönüştürülürken
    kaybolan görünüm — operatör talebiyle 2026-09-04'te AYRI bir alt sekme
    olarak geri eklendi, 17-senaryo koşucusuna dokunulmadı.

    NEDEN ayrı, hafif bir görünüm (yeni bir telemetri kanalı İCAT EDİLMEDİ):
    `IHA/tools/human_guided_bench.py` yön oku + tolerans çemberi + dwell
    çubuğunu doğrudan KAREYE ÇİZİP aynı MJPEG protokolüyle yayınlıyor — bu
    panel yalnızca o akışı gösterir, yönlendirmeyi kendisi HESAPLAMAZ.
    Bırakma anı companion'dan ayrı bir telemetri sözleşmesiyle senkron
    değil — operatör videoda "OTONOM BIRAKILDI" bandını görünce checklist'i
    elle işaretler (bilinçli kapsam sınırı).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._checklist_boxes: dict[str, QCheckBox] = {}
        self._checklist_time_labels: dict[str, QLabel] = {}
        self._bench_worker: VehicleBenchStartWorker | VehicleBenchStopWorker | None = None
        self._otonom_worker: OtonomAlignTestWorker | None = None
        # Otonom testin "güncel konum" kaynağı: ana mavlink_worker'ın
        # telemetry_updated akışından SÜREKLİ biriktirilir (main_window
        # _wire_mavlink_worker'da bağlanır) — bağımsız bir MAVLink bağlantısı
        # AÇILMAZ (gerçek donanımda seri/RFD900 tek bağlantı kabul eder,
        # bkz. otonom_align_test.py başlığındaki 2026-09-05 düzeltme notu).
        self._son_lat: float | None = None
        self._son_lon: float | None = None
        self._son_konum_ts: float = 0.0
        # NEDEN AYRI PENCERELER (2026-09-05 operatör talebi): iki test bölümü
        # (bench + otonom) sağ sütuna aynı anda sığdırılınca dar (380px) ve
        # görsel olarak ayırt edilemez oluyordu, metinler kırpılıyordu. Her
        # biri kendi geniş penceresine taşındı, ana panelde yalnız "Aç"
        # düğmeleri kaldı. Tembel kurulur (ilk açılışta), sonra saklanıp
        # tekrar gösterilir — worker sinyalleri hep AYNI widget örneğine gider.
        self._uzaktan_dialog: QDialog | None = None
        self._otonom_dialog: QDialog | None = None

        # Ana senaryo koşucusundan BAĞIMSIZ kendi VideoStreamWorker'ı —
        # bench betiği farklı bir port/akış yayınlıyor.
        self.video_worker = VideoStreamWorker(source="none")
        self.video_worker.frame_ready.connect(self._on_frame)
        self.video_worker.error_occurred.connect(self._on_video_error)
        self.video_worker.start()

        self._build_ui()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        conn_frame = QFrame()
        conn_frame.setObjectName("toolbarFrame")
        conn_layout = QHBoxLayout(conn_frame)
        conn_layout.setContentsMargins(8, 6, 8, 6)
        conn_layout.setSpacing(8)
        conn_layout.addWidget(QLabel("Companion IP:"))
        self._ip_edit = QLineEdit(config.companion_host)
        self._ip_edit.setFixedWidth(140)
        conn_layout.addWidget(self._ip_edit)
        conn_layout.addWidget(QLabel("Port:"))
        self._port_edit = QLineEdit(str(getattr(config, "companion_bench_port", 8090)))
        self._port_edit.setFixedWidth(60)
        conn_layout.addWidget(self._port_edit)
        self._btn_connect = QPushButton("▶  Bağlan")
        self._btn_connect.setObjectName("primaryButton")
        self._btn_connect.clicked.connect(self._on_connect_clicked)
        conn_layout.addWidget(self._btn_connect)
        self._status_lbl = QLabel("● bağlı değil")
        self._status_lbl.setStyleSheet(f"color:{theme.c('text_muted')};font-weight:bold;")
        conn_layout.addWidget(self._status_lbl)
        conn_layout.addStretch()
        root.addWidget(conn_frame)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        vframe = QFrame()
        vframe.setObjectName("videoFrame")
        vframe.setStyleSheet(f"QFrame#videoFrame{{background:{theme.c('well')};}}")
        vfl = QVBoxLayout(vframe)
        vfl.setContentsMargins(0, 0, 0, 0)
        self._video_label = QLabel("Bağlantı bekleniyor — “Bağlan” ile başlatın")
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setObjectName("mutedLabel")
        self._video_label.setMinimumSize(320, 240)
        self._video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vfl.addWidget(self._video_label, stretch=1)
        left_layout.addWidget(vframe, stretch=1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        warn_box = QGroupBox("Güvenlik")
        warn_layout = QVBoxLayout(warn_box)
        warn_lbl = QLabel(_GOREV_UYARI_METNI)
        warn_lbl.setWordWrap(True)
        warn_lbl.setStyleSheet(f"color:{theme.c('danger')};")
        warn_layout.addWidget(warn_lbl)
        self._kapi = PervaneKapisi(
            ek_uyari="Bu kutu işaretlenmeden aşağıdaki “Testi Başlat” "
                     "düğmeleri AÇILMAZ."
        )
        self._kapi.durum_degisti.connect(self._kapi_degisti)
        warn_layout.addWidget(self._kapi)
        right_layout.addWidget(warn_box)

        acilir_box = QGroupBox("Testler")
        acilir_layout = QVBoxLayout(acilir_box)
        btn_ac_uzaktan = QPushButton("🧪  Pervanesiz Bench Testi — Pencereyi Aç…")
        btn_ac_uzaktan.clicked.connect(self._ac_uzaktan_dialog)
        acilir_layout.addWidget(btn_ac_uzaktan)
        btn_ac_otonom = QPushButton("🚁  Otonom Hizalama Testi (GERÇEK UÇUŞ) — Pencereyi Aç…")
        btn_ac_otonom.setObjectName("dangerButton")
        btn_ac_otonom.clicked.connect(self._ac_otonom_dialog)
        acilir_layout.addWidget(btn_ac_otonom)
        right_layout.addWidget(acilir_box)

        # ── (aşağıdaki widget'lar artık dialoglara TAŞINACAK, burada yalnız
        # kuruluyorlar — _ac_uzaktan_dialog/_ac_otonom_dialog onları alıp
        # kendi pencerelerine yerleştiriyor, bkz. o metodlar.) ──────────────
        remote_layout = QVBoxLayout()
        # NEDEN varsayılan doldurulmuş (2026-09-04 operatör talebi — "SSH
        # isteme, direkt buton olsun"): companion_auth_token ile AYNI desen —
        # gerçek şifre yalnızca APPDATA'daki settings.json'a yazılır (repo
        # şablonunda BOŞ). Doluysa operatöre hiç sorulmaz, düğme direkt çalışır.
        self._ssh_pw_edit = QLineEdit(config.companion_ssh_password)
        self._ssh_pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if not config.companion_ssh_password:
            pw_row = QHBoxLayout()
            pw_row.addWidget(QLabel("Araç SSH şifresi (bir kerelik, kaydedilmez):"))
            self._ssh_pw_edit.setPlaceholderText("yalnızca bellekte tutulur")
            pw_row.addWidget(self._ssh_pw_edit, 1)
            remote_layout.addLayout(pw_row)

        self._egim_cb = QCheckBox("Eğim deneyi modu (--egim-kapisi kapali --hedef-irtifa 2.0, bkz. B9)")
        remote_layout.addWidget(self._egim_cb)

        btn_row = QHBoxLayout()
        self._btn_baslat_kirmizi = QPushButton("▶ KIRMIZI Testi Başlat")
        self._btn_baslat_kirmizi.setEnabled(False)
        self._btn_baslat_kirmizi.clicked.connect(lambda: self._on_baslat_clicked("kirmizi"))
        btn_row.addWidget(self._btn_baslat_kirmizi)
        self._btn_baslat_mavi = QPushButton("▶ MAVİ Testi Başlat")
        self._btn_baslat_mavi.setEnabled(False)
        self._btn_baslat_mavi.clicked.connect(lambda: self._on_baslat_clicked("mavi"))
        btn_row.addWidget(self._btn_baslat_mavi)
        remote_layout.addLayout(btn_row)

        self._btn_durdur_servisi_geri = QPushButton("⏹ Durdur ve Uçuş Servisini Geri Yükle")
        self._btn_durdur_servisi_geri.clicked.connect(self._on_durdur_clicked)
        remote_layout.addWidget(self._btn_durdur_servisi_geri)

        # NEDEN QLabel DEĞİL (2026-09-04 sahada): araçtan gelen hata metni çok
        # satırlı olabiliyor (betik çökerse log kuyruğu basılıyor) ve QLabel
        # kaydırılamadığı için operatör metnin tamamını GÖREMİYORDU.
        self._remote_status_lbl = QTextEdit("—")
        self._remote_status_lbl.setReadOnly(True)
        self._remote_status_lbl.setFont(QFont("Courier New", 9))
        self._remote_status_lbl.setMinimumHeight(90)
        self._remote_status_lbl.setMaximumHeight(160)
        remote_layout.addWidget(self._remote_status_lbl)
        self._remote_layout_holder = remote_layout  # _ac_uzaktan_dialog bunu bir QWidget'a taşır

        # ── OTONOM HİZALAMA TESTİ — GERÇEK UÇUŞ (2026-09-05) ──────────────
        # NEDEN AYRI KUTU, AYRI ONAY (yukarıdaki PervaneKapisi'nden BAĞIMSIZ):
        # bu düğme aracı GERÇEKTEN kaldırıyor — pervaneler TAKILI olmalı, tam
        # tersi bir ön koşul. PervaneKapisi'yle karıştırılırsa "pervane sökülü"
        # onayı verilmiş bir operatör bu düğmeyi de açık sanabilir — İKİSİ
        # KASITLI OLARAK BİRBİRİNDEN BAĞIMSIZ ve farklı metinli.
        otonom_layout = QVBoxLayout()
        otonom_uyari = QLabel(
            "Aracı hedefin (ör. kırmızı üçgen) birkaç metre yanına yerleştirin. "
            "Bu düğme GERÇEK otonom görevi tetikler: 15 m'ye kalkış → tarama "
            "yapmadan hizala → bırak → GUIDED ile eve dön → LAND (RTL YOK). "
            "Aynı senaryo simde doğrulandı (2026-09-05). Companion'ın kendi "
            "sağlık kapısı (FC param + EKF/GPS) hâlâ geçerlidir, ATLANMAZ."
        )
        otonom_uyari.setWordWrap(True)
        otonom_uyari.setStyleSheet(f"color:{theme.c('danger')};")
        otonom_layout.addWidget(otonom_uyari)

        self._otonom_onay_cb = QCheckBox(
            "Alan güvenli, pervaneler TAKILI, kimse aracın altında/yakınında "
            "değil, aracı hedefin yanına yerleştirdim — bu ŞU AN doğru."
        )
        otonom_layout.addWidget(self._otonom_onay_cb)

        renk_row = QHBoxLayout()
        renk_row.addWidget(QLabel("Hedef renk:"))
        self._otonom_renk_combo = QComboBox()
        self._otonom_renk_combo.addItems(["red", "blue"])
        renk_row.addWidget(self._otonom_renk_combo)
        renk_row.addStretch()
        otonom_layout.addLayout(renk_row)

        self._btn_otonom_baslat = QPushButton("🚁 Otonom Testi BAŞLAT")
        self._btn_otonom_baslat.setObjectName("dangerButton")
        self._btn_otonom_baslat.setEnabled(False)
        self._otonom_onay_cb.toggled.connect(self._btn_otonom_baslat.setEnabled)
        self._btn_otonom_baslat.clicked.connect(self._on_otonom_baslat_clicked)
        otonom_layout.addWidget(self._btn_otonom_baslat)

        video_row = QHBoxLayout()
        btn_video_8081 = QPushButton("📹 Otonom Video Göster (işlenmiş, :8081)")
        btn_video_8081.clicked.connect(self._on_otonom_video_clicked)
        video_row.addWidget(btn_video_8081)
        otonom_layout.addLayout(video_row)

        self._otonom_status_lbl = QTextEdit("—")
        self._otonom_status_lbl.setReadOnly(True)
        self._otonom_status_lbl.setFont(QFont("Courier New", 9))
        self._otonom_status_lbl.setMinimumHeight(70)
        self._otonom_status_lbl.setMaximumHeight(130)
        otonom_layout.addWidget(self._otonom_status_lbl)
        self._otonom_layout_holder = otonom_layout  # _ac_otonom_dialog bunu bir QWidget'a taşır

        check_box = QGroupBox("Test protokolü (sırayla)")
        check_layout = QVBoxLayout(check_box)
        for key, label, _cmd in _GOREV_ASAMALARI:
            row = QHBoxLayout()
            cb = QCheckBox(label)
            cb.toggled.connect(lambda checked, k=key: self._on_checklist_toggled(k, checked))
            self._checklist_boxes[key] = cb
            row.addWidget(cb, 1)
            t_lbl = QLabel("—")
            t_lbl.setObjectName("mutedLabel")
            self._checklist_time_labels[key] = t_lbl
            row.addWidget(t_lbl)
            check_layout.addLayout(row)
        self._btn_reset_checklist = QPushButton("Sıfırla")
        self._btn_reset_checklist.clicked.connect(self._reset_checklist)
        check_layout.addWidget(self._btn_reset_checklist)
        note = QLabel("Not: bu kutular OTOMATİK işaretlenmez — videoda "
                      "“OTONOM BIRAKILDI” bandını gördüğünüzde elle işaretleyin.")
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        check_layout.addWidget(note)
        right_layout.addWidget(check_box)

        cmd_box = QGroupBox("Yedek: elle SSH ile çalıştırılacak komutlar (SSH otomasyonu çalışmazsa)")
        cmd_layout = QVBoxLayout(cmd_box)
        self._cmd_text = QTextEdit()
        self._cmd_text.setReadOnly(True)
        self._cmd_text.setFont(QFont("Courier New", 9))
        self._cmd_text.setPlainText(
            "# ÖNCE: sudo systemctl stop ashina-iha  (aynı FC portu çakışmasın)\n\n"
            + "\n".join(f"# {label}\n{cmd}\n" for _k, label, cmd in _GOREV_ASAMALARI)
        )
        self._cmd_text.setMaximumHeight(170)
        cmd_layout.addWidget(self._cmd_text)
        btn_copy = QPushButton("📋 Kopyala")
        btn_copy.clicked.connect(self._copy_commands)
        cmd_layout.addWidget(btn_copy)
        right_layout.addWidget(cmd_box)

        right_layout.addStretch()
        splitter.addWidget(right)
        splitter.setSizes([700, 380])
        root.addWidget(splitter, 1)

    def _ac_uzaktan_dialog(self) -> None:
        if self._uzaktan_dialog is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("🧪 Pervanesiz Bench Testi — Uzaktan Kontrol")
            dlg.setMinimumWidth(520)
            dlg.setLayout(self._remote_layout_holder)
            self._uzaktan_dialog = dlg
        self._uzaktan_dialog.show()
        self._uzaktan_dialog.raise_()
        self._uzaktan_dialog.activateWindow()

    def _ac_otonom_dialog(self) -> None:
        if self._otonom_dialog is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("🚁 Otonom Hizalama Testi — GERÇEK UÇUŞ")
            dlg.setMinimumWidth(560)
            dlg.setLayout(self._otonom_layout_holder)
            self._otonom_dialog = dlg
        self._otonom_dialog.show()
        self._otonom_dialog.raise_()
        self._otonom_dialog.activateWindow()

    @pyqtSlot()
    def _on_connect_clicked(self) -> None:
        if self._connected:
            self.video_worker.set_source("none")
            self._connected = False
            self._btn_connect.setText("▶  Bağlan")
            self._status_lbl.setText("● bağlı değil")
            self._status_lbl.setStyleSheet(f"color:{theme.c('text_muted')};font-weight:bold;")
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
        self._btn_connect.setText("⏹  Kes")
        self._status_lbl.setText(f"● bağlanıyor ({url})")
        self._status_lbl.setStyleSheet(f"color:{theme.c('info')};font-weight:bold;")

    @pyqtSlot(QImage)
    def _on_frame(self, q_img: QImage) -> None:
        if not self._connected:
            return
        if q_img.isNull():
            self._video_label.setPixmap(QPixmap())
            self._video_label.setText("Yeniden bağlanıyor…")
            return
        self._status_lbl.setText("● canlı")
        self._status_lbl.setStyleSheet(f"color:{theme.c('success')};font-weight:bold;")
        px = QPixmap.fromImage(q_img)
        self._video_label.setPixmap(
            px.scaled(self._video_label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
        )

    @pyqtSlot(str)
    def _on_video_error(self, msg: str) -> None:
        if not self._connected:
            return
        self._status_lbl.setText(f"● hata: {msg}")
        self._status_lbl.setStyleSheet(f"color:{theme.c('danger')};font-weight:bold;")

    def _kapi_degisti(self, acik: bool) -> None:
        self._btn_baslat_kirmizi.setEnabled(acik)
        self._btn_baslat_mavi.setEnabled(acik)
        # Durdurma HER ZAMAN açık: durdurmak/servisi geri yüklemek her koşulda güvenlidir.

    def _bench_host(self) -> str:
        return self._ip_edit.text().strip() or config.companion_host

    def _bench_running_guard(self) -> bool:
        if self._bench_worker is not None and self._bench_worker.isRunning():
            QMessageBox.warning(self, "Uzaktan Kontrol",
                                 "Zaten çalışan bir uzak komut var, bitmesini bekleyin.")
            return True
        return False

    def _on_baslat_clicked(self, renk: str) -> None:
        if not self._kapi.acik():
            QMessageBox.warning(self, "Uzaktan Kontrol",
                                 "Önce “PERVANELERİN SÖKÜLÜ olduğunu ŞU AN gözle "
                                 "kontrol ettim” kutusunu işaretleyin.")
            return
        if self._bench_running_guard():
            return
        pw = self._ssh_pw_edit.text()
        if not pw:
            QMessageBox.warning(self, "Uzaktan Kontrol", "Araç SSH şifresini girin.")
            return
        host = self._bench_host()
        if QMessageBox.question(
            self, "Testi Başlat",
            f"{host} üzerinde ashina-iha.service DURDURULACAK ve "
            f"human_guided_bench.py --renk {renk} ARKA PLANDA başlatılacak.\n"
            "Araç ARM olacak, motorlar dönecek. Devam edilsin mi?",
        ) != QMessageBox.StandardButton.Yes:
            return

        worker = VehicleBenchStartWorker(
            host=host, password=pw, renk=renk,
            egim_kapisi_kapali=self._egim_cb.isChecked(),
            hedef_irtifa=2.0 if self._egim_cb.isChecked() else None,
            user=config.companion_ssh_user,
        )
        self._wire_bench_worker(worker, baslatiyor=True)

    @pyqtSlot()
    def _on_durdur_clicked(self) -> None:
        if self._bench_running_guard():
            return
        pw = self._ssh_pw_edit.text()
        if not pw:
            QMessageBox.warning(self, "Uzaktan Kontrol", "Araç SSH şifresini girin.")
            return
        worker = VehicleBenchStopWorker(host=self._bench_host(), password=pw,
                                         user=config.companion_ssh_user)
        self._wire_bench_worker(worker, baslatiyor=False)

    def _wire_bench_worker(self, worker, baslatiyor: bool) -> None:
        self._bench_worker = worker
        for btn in (self._btn_baslat_kirmizi, self._btn_baslat_mavi, self._btn_durdur_servisi_geri):
            btn.setEnabled(False)
        worker.ilerleme.connect(self._on_bench_ilerleme)
        worker.basarili.connect(lambda msg: self._on_bench_basarili(msg, baslatiyor))
        worker.hata.connect(self._on_bench_hata)
        worker.finished.connect(lambda: self._kapi_degisti(self._kapi.acik()))
        worker.finished.connect(lambda: self._btn_durdur_servisi_geri.setEnabled(True))
        worker.start()

    @pyqtSlot(str)
    def _on_bench_ilerleme(self, msg: str) -> None:
        self._remote_status_lbl.setText(f"● {msg}")
        self._remote_status_lbl.setStyleSheet(f"color:{theme.c('info')};font-weight:bold;")

    def _on_bench_basarili(self, msg: str, baslatiyor: bool) -> None:
        self._remote_status_lbl.setText(f"✅ {msg}")
        self._remote_status_lbl.setStyleSheet(f"color:{theme.c('success')};font-weight:bold;")
        if baslatiyor and not self._connected:
            QTimer.singleShot(2500, self._on_connect_clicked)
        if not baslatiyor:
            # NEDEN (2026-09-05 denetimde bulundu): bu "Durdur" yalnız bench
            # SÜRECİNİ öldürür ve servisi geri başlatır — FC'ye disarm komutu
            # GÖNDERMEZ. Araç ARM kalmış olabilir; tek gerçek disarm yolu bu
            # ekrandaki "⛔ ACİL DISARM" düğmesi (MAV_CMD_COMPONENT_ARM_DISARM,
            # ön kontroller atlanır).
            QMessageBox.warning(
                self, "Uzaktan Kontrol",
                "Bench betiği durduruldu, ashina-iha.service yeniden başlatıldı.\n\n"
                "⚠ BU İŞLEM ARACI DISARM ETMEZ — yalnızca companion sürecini "
                "durdurur. Aracın gerçekten DISARM olduğunu ana HUD'daki ARM "
                "göstergesinden GÖZLE doğrulayın. Hâlâ ARM'lıysa bu sekmedeki "
                "“⛔ ACİL DISARM” düğmesini kullanın — otomatik disarm zaman "
                "aşımına güvenmeyin."
            )

    @pyqtSlot(str)
    def _on_bench_hata(self, msg: str) -> None:
        self._remote_status_lbl.setText(f"❌ {msg}")
        self._remote_status_lbl.setStyleSheet(f"color:{theme.c('danger')};font-weight:bold;")

    # ── Otonom hizalama testi ────────────────────────────────────────────
    @pyqtSlot()
    def _on_otonom_video_clicked(self) -> None:
        self._ip_edit.setText(config.companion_host)
        self._port_edit.setText("8081")
        if self._connected:
            self._on_connect_clicked()  # kes
        self._on_connect_clicked()      # yeniden bağlan (8081'e)

    @pyqtSlot(dict)
    def on_telemetry_position(self, data: dict) -> None:
        """Ana MAVLink akışından güncel konumu biriktirir (bkz. __init__ notu).

        main_window._wire_mavlink_worker() bunu w.telemetry_updated'a doğrudan
        bağlar — sekme görünür olsun olmasın SÜREKLİ çağrılır, tıpkı
        main_window._last_gps_fix'in beslendiği mekanizma gibi.
        """
        lat, lon = data.get("lat"), data.get("lon")
        if lat is None or lon is None or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
            return
        self._son_lat, self._son_lon = lat, lon
        self._son_konum_ts = time.monotonic()

    @pyqtSlot()
    def _on_otonom_baslat_clicked(self) -> None:
        if self._otonom_worker is not None and self._otonom_worker.isRunning():
            QMessageBox.warning(self, "Otonom Test",
                                 "Zaten çalışan bir otonom test var, bitmesini bekleyin.")
            return
        # Güncel konum yoksa ya da bayatsa (bağlantı koptu vb.) worker hiç
        # başlatılmaz — eskiden bu kontrol worker THREAD'İ içindeydi ve
        # "güncel konum okunamadı" hatası ARM'a çok yakın bir noktada,
        # gecikmeli geliyordu.
        konum_yasi = time.monotonic() - self._son_konum_ts
        if self._son_lat is None or self._son_lon is None or konum_yasi > 5.0:
            QMessageBox.warning(
                self, "Otonom Test",
                "Güncel konum yok veya bayat (MAVLink bağlantısını kontrol edin). "
                "Araç GPS konumu yayınlıyor ve YKİ bağlıyken tekrar deneyin.")
            return
        renk = self._otonom_renk_combo.currentText()
        renk_tr = "KIRMIZI" if renk == "red" else "MAVİ"
        if QMessageBox.question(
            self, "Otonom Testi Başlat — GERÇEK UÇUŞ",
            f"Araç ŞU AN {renk_tr} hedefin yanında mı? Pervaneler TAKILI, alan "
            "güvenli mi? Onaylarsanız araç ARM olup 15 m'ye kalkacak, hedefe "
            "kilitlenip bırakacak ve LAND ile inecek (RTL YOK, tam otonom).\n\n"
            "Devam edilsin mi?",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._btn_otonom_baslat.setEnabled(False)
        self._otonom_status_lbl.setText("başlatılıyor…")
        worker = OtonomAlignTestWorker(
            companion_host=config.companion_host,
            mission_port=config.companion_mission_port,
            lat0=self._son_lat,
            lon0=self._son_lon,
            renk=renk,
        )
        self._otonom_worker = worker
        worker.ilerleme.connect(self._on_otonom_ilerleme)
        worker.basarili.connect(self._on_otonom_basarili)
        worker.hata.connect(self._on_otonom_hata)
        worker.finished.connect(lambda: self._btn_otonom_baslat.setEnabled(
            self._otonom_onay_cb.isChecked()))
        worker.start()

    @pyqtSlot(str)
    def _on_otonom_ilerleme(self, msg: str) -> None:
        self._otonom_status_lbl.append(f"● {msg}")

    @pyqtSlot(str)
    def _on_otonom_basarili(self, msg: str) -> None:
        self._otonom_status_lbl.append(f"✅ {msg}")

    @pyqtSlot(str)
    def _on_otonom_hata(self, msg: str) -> None:
        self._otonom_status_lbl.append(f"❌ {msg}")

    def panel_terk_edildi(self) -> None:
        """Sekmeden çıkılınca pervane onayı DÜŞER (SahaTestiView.panel_terk_edildi çağırır)."""
        try:
            self._kapi.sifirla()
        except Exception:
            pass
        try:
            self._otonom_onay_cb.setChecked(False)
        except Exception:
            pass
        for _dlg in (self._uzaktan_dialog, self._otonom_dialog):
            try:
                if _dlg is not None:
                    _dlg.hide()
            except Exception:
                pass

    def _on_checklist_toggled(self, key: str, checked: bool) -> None:
        lbl = self._checklist_time_labels.get(key)
        if lbl is None:
            return
        lbl.setText(time.strftime("%H:%M:%S") if checked else "—")

    @pyqtSlot()
    def _reset_checklist(self) -> None:
        for cb in self._checklist_boxes.values():
            cb.setChecked(False)
        for lbl in self._checklist_time_labels.values():
            lbl.setText("—")

    def _copy_commands(self) -> None:
        QApplication.clipboard().setText(self._cmd_text.toPlainText())

    def _apply_theme_colors(self) -> None:
        if self._connected:
            self._status_lbl.setStyleSheet(f"color:{theme.c('success')};font-weight:bold;")
        else:
            self._status_lbl.setStyleSheet(f"color:{theme.c('text_muted')};font-weight:bold;")


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

        baslik = QLabel("🧪  SAHA TESTİ")
        baslik.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        root.addWidget(baslik)

        ic_sekmeler = QTabWidget()
        ic_sekmeler.addTab(self._arizalar_sekmesi(), "⚠ Arıza Senaryoları")
        self.gorev_paneli = GercekGorevPaneli()
        ic_sekmeler.addTab(self.gorev_paneli, "🎯 Gerçek Görev (İnsan-Rehberli)")
        root.addWidget(ic_sekmeler, 1)

    def _arizalar_sekmesi(self) -> QWidget:
        sekme = QWidget()
        root = QVBoxLayout(sekme)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(8)

        alt_baslik = QLabel("PERVANESİZ, GERÇEK SENARYOLAR")
        alt_baslik.setObjectName("mutedLabel")
        root.addWidget(alt_baslik)

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
        return sekme

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
        try:
            self.gorev_paneli.panel_terk_edildi()
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
        if not bagli:
            # NEDEN: FC linki koptuğunda yalnızca "fc" etiketi KOPUK'a dönüyordu,
            # hemen yanındaki ARM/kip/batarya/GPS kutucukları son gerçek
            # değerde donuk kalıp yeniden bağlanana kadar canlı görünüyordu.
            for k in ("arm", "mode", "batt", "gps"):
                self._d[k].setText("—")
                self._d[k].setStyleSheet(f"color:{theme.c('text_muted')};font-weight:bold;")

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
