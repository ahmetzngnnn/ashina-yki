"""
advanced_view.py — Modül 5
Full Parameter Tree (QTreeView + QStandardItemModel)
Arama: QSortFilterProxyModel
RTK/NTRIP yapılandırması
"""
import logging
from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QSortFilterProxyModel
from PyQt6.QtGui import QFont, QStandardItemModel, QStandardItem, QBrush, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTreeView, QLineEdit, QPushButton, QGroupBox,
    QFormLayout, QTabWidget, QSpinBox, QHeaderView,
)

logger = logging.getLogger(__name__)

# Parametre kategori eşleşmesi (prefix → Türkçe kategori adı)
_PARAM_CATEGORIES: list = [
    ("Güvenlik / ARM",      ["ARMING_"]),
    ("Batarya",             ["BATT_", "BATT2_"]),
    ("GPS / Konum",         ["GPS_", "GPS2_"]),
    ("IMU / İvme / Jiro",   ["INS_", "AHRS_"]),
    ("Pusula",              ["COMPASS_"]),
    ("PID / Stabilizasyon", ["ATC_", "PSC_", "ANGLE_"]),
    ("Görev / Auto",        ["MIS_", "AUTO_", "WP_"]),
    ("Fail-Safe",           ["FS_", "FS2_"]),
    ("Motor / Servo",       ["MOT_", "SERVO_"]),
    ("RC / Radyo",          ["RC_", "RC1_", "RC2_", "RC3_", "RC4_",
                              "RC5_", "RC6_", "RC7_", "RC8_"]),
    ("Kamera / Gimbal",     ["CAM_", "MNT_"]),
    ("Loglama",             ["LOG_"]),
    ("EKF2 / EKF3",         ["EK2_", "EK3_"]),
    ("Veri Akışı",           ["SR_", "SR0_", "SR1_", "SR2_", "SR3_"]),
    ("Diğer",              []),   # catch-all
]

# Sık kullanılan parametre açıklamaları
_PARAM_DESC: dict = {
    # ── Güvenlik / ARM ────────────────────────────────────────────────────────
    "ARMING_CHECK":      "ARM öncesi kontrol listesi — AC 4.6+ öncesi (0=devre dışı, 1=tüm kontroller). AC 4.6+ sürümlerde ARMING_SKIPCHK kullanılır.",
    "ARMING_SKIPCHK":    "ARM kontrollerini atla (AC 4.6+) — bitmask. 0=tüm kontroller aktif.",
    "ARMING_RUDDER":     "Rudder ile ARM/DISARM izni (0=Hayır, 1=Sadece ARM, 2=Her ikisi)",
    # ── Batarya ───────────────────────────────────────────────────────────────
    "BATT_CAPACITY":     "Batarya kapasitesi (mAh) — doğru değer RTH hesabı için gerekli",
    "BATT_FS_LOW_ACT":   "Düşük batarya aksiyonu (0=Hiçbiri, 1=Land, 2=RTL, 3=SmartRTL)",
    "BATT_LOW_VOLT":     "Düşük batarya uyarı eşiği (volt)",
    "BATT_CRT_VOLT":     "Kritik batarya eşiği (volt) — acil iniş tetikler",
    "BATT_ARM_VOLT":     "ARM için minimum batarya voltajı (AC 4.4+)",
    # ── GPS / Konum ───────────────────────────────────────────────────────────
    "GPS_TYPE":          "GPS türü (0=Yok, 1=Auto, 5=NMEA, 9=RTK u-blox)",
    "GPS_HDOP_GOOD":     "Kabul edilebilir maks HDOP değeri (önerilen ≤2.0)",
    "GPS_AUTO_SWITCH":   "İkincil GPS'e otomatik geç (0=Hayır, 1=Evet)",
    # ── IMU / INS ─────────────────────────────────────────────────────────────
    "INS_ACCEL_FILTER":  "İvmeölçer alçak geçiren filtre frekansı Hz (önerilen: 20)",
    "INS_GYRO_FILTER":   "Jiroskop alçak geçiren filtre frekansı Hz (önerilen: 20)",
    "INS_USE":           "Birincil IMU etkin (1=Evet)",
    # ── Pusula ────────────────────────────────────────────────────────────────
    "COMPASS_USE":       "Birincil pusula kullan (1=Evet, 0=Hayır)",
    "COMPASS_AUTODEC":   "Manyetik sapıklık auto hesapla (1=Evet)",
    "COMPASS_ENABLE":    "Pusula sistemi etkin (1=Evet)",
    # ── PID / Stabilizasyon — AC 4.x ─────────────────────────────────────────
    "ATC_ANG_RLL_P":     "Roll açı P katsayısı (tipik: 4.5)",
    "ATC_ANG_PIT_P":     "Pitch açı P katsayısı (tipik: 4.5)",
    "ATC_ANG_YAW_P":     "Yaw açı P katsayısı (tipik: 4.5)",
    "ATC_RAT_RLL_P":     "Roll hız P katsayısı",
    "ATC_RAT_RLL_I":     "Roll hız I katsayısı",
    "ATC_RAT_RLL_D":     "Roll hız D katsayısı",
    "ATC_RAT_PIT_P":     "Pitch hız P katsayısı",
    "ATC_RAT_PIT_I":     "Pitch hız I katsayısı",
    "ATC_RAT_PIT_D":     "Pitch hız D katsayısı",
    "ATC_RAT_YAW_P":     "Yaw hız P katsayısı",
    "ATC_RAT_YAW_I":     "Yaw hız I katsayısı",
    "ATC_ANGLE_MAX":     "Maks. eğim açısı derece (AC 4.7+ SI birim, önceki sürümlerde santiderece)",
    # ── Görev / Auto ─────────────────────────────────────────────────────────
    "MIS_TOTAL":         "Bellekteki waypoint toplamı (salt okunur)",
    "MIS_RESTART":       "Auto modda görev tamamlanınca (0=Durma, 1=Başa dön)",
    "WP_RADIUS":         "Waypoint kabul yarıçapı (metre, AC 4.7+ SI)",
    "WP_YAW_BEHAVIOR":   "Yaw davranışı (0=Sabit, 1=İleri bak, 2=ROI, 3=Waypoint'e bak)",
    # ── Fail-Safe ─────────────────────────────────────────────────────────────
    "FS_THR_ENABLE":     "Throttle fail-safe (1=Land, 2=Continue, 3=SmartRTL)",
    "FS_THR_VALUE":      "Throttle FS eşiği PWM (tipik: 975)",
    "FS_GCS_ENABLE":     "GCS heartbeat kaybolunca failsafe (0=Kapalı, 1=Land, 2=RTL)",
    "FS_EKF_ACTION":     "EKF hatasında failsafe (1=Land, 2=AltHold, 3=Land+disarm)",
    "FS_CRASH_CHECK":    "Kaza tespitinde disarm (1=Disarm, 2=Disarm+crash failsafe)",
    # ── Motor ─────────────────────────────────────────────────────────────────
    "MOT_SPIN_ARM":      "ARM sonrası motor dönme hızı (0-1, önerilen 0.10)",
    "MOT_SPIN_MIN":      "Minimum motor dönme hızı (throttle = 0, önerilen 0.15)",
    "MOT_THST_HOVER":    "Hover throttle tahmini (0-1, otomatik öğrenir)",
    "MOT_BAT_VOLT_MAX":  "Dolu batarya voltajı — throttle dengeleme için",
    "MOT_BAT_VOLT_MIN":  "Boş batarya voltajı — throttle dengeleme için",
    "MOT_IDLE_SEC":      "ESC başlangıç gecikmesi saniye (AC 4.6+)",
    # ── RC ────────────────────────────────────────────────────────────────────
    "RC_SPEED":          "ESC güncelleme frekansı Hz (tipik: 400 digital, 50 analog)",
    # ── EKF ──────────────────────────────────────────────────────────────────
    "EK3_ENABLE":        "EKF3 etkin (1=Evet — ArduCopter 4.x varsayılanı)",
    "EK3_FLOW_MAX":      "Optik akış maksimum değer (AC 4.6+ — eski adı EK3_MAX_FLOW)",
    "EK3_MAX_FLOW":      "Optik akış maks (eski parametre — AC 4.5 ve öncesi)",
    # ── Loglama ───────────────────────────────────────────────────────────────
    "LOG_BITMASK":       "Loglanacak veri seçimi bitmask (0xFFFF=Hepsi)",
    "LOG_DISARMED":      "DISARM durumunda loglama (1=Evet — hata ayıklama için önerilir)",
    "LOG_REPLAY":        "Replay için ham IMU verisi logla (1=Evet, disk yer kaplar)",
}


class AdvancedView(QWidget):
    set_param = pyqtSignal(str, float)
    start_ntrip = pyqtSignal(str, int, str, str, str)  # host, port, mountpoint, user, pass
    stop_ntrip  = pyqtSignal()                          # NTRIP durdur
    request_params = pyqtSignal()  # parametre listesi talep et

    def __init__(self, parent=None):
        super().__init__(parent)
        self._param_cache: dict = {}
        self._modified_params: dict = {}
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet("color: #e0e0e0;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        tabs = QTabWidget()
        tabs.addTab(self._param_tab(), "Parametre Ağacı")
        tabs.addTab(self._ntrip_tab(), "RTK / NTRIP")
        layout.addWidget(tabs)

    # ── Parametre Sekmesi ─────────────────────────────────────────────────────
    def _param_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # Arama kutusu
        search_row = QHBoxLayout()
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Parametre ara...")
        self._search_box.setStyleSheet(
            " border: 1px solid #0f3460; "
            "padding: 6px; border-radius: 3px; color: #e0e0e0;"
        )
        self._search_box.textChanged.connect(self._on_search)

        btn_refresh = QPushButton("Yenile (Tümü)")
        btn_refresh.setStyleSheet(" padding: 6px 14px; border-radius: 3px;")
        btn_refresh.clicked.connect(self.request_params)

        btn_write = QPushButton("Değişiklikleri YAZ")
        btn_write.setStyleSheet(
            " color: white; padding: 6px 14px; border-radius: 3px;"
        )
        btn_write.clicked.connect(self._write_modified)

        search_row.addWidget(self._search_box)
        search_row.addWidget(btn_refresh)
        search_row.addWidget(btn_write)
        layout.addLayout(search_row)

        # Model ve View
        self._param_model = QStandardItemModel()
        self._param_model.setHorizontalHeaderLabels(["Parametre", "Değer"])
        self._param_model.itemChanged.connect(self._on_param_item_changed)

        self._proxy_model = QSortFilterProxyModel()
        self._proxy_model.setSourceModel(self._param_model)
        self._proxy_model.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy_model.setFilterKeyColumn(-1)  # Tüm sütunlarda ara

        self._tree = QTreeView()
        self._tree.setModel(self._proxy_model)
        self._tree.setAlternatingRowColors(True)
        _hdr = self._tree.header()
        if _hdr is not None:
            _hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            _hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setStyleSheet("""
            QTreeView {  color: #e0e0e0;
                        alternate- }
            QTreeView::item:selected {  }
            QHeaderView::section {  color: #e0e0e0; padding: 4px; }
        """)
        self._tree.setExpandsOnDoubleClick(True)
        sel = self._tree.selectionModel()
        if sel is not None:
            sel.selectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._tree)

        # Parametre açiklama bandi
        self._desc_label = QLabel("Bir parametre seçin...")
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet(
            "color:#aaa;font-size:11px;"
            "padding:6px;border-top:1px solid #0f3460;"
        )
        self._desc_label.setFixedHeight(48)
        layout.addWidget(self._desc_label)
        return w

    def _on_selection_changed(self) -> None:
        indexes = self._tree.selectedIndexes()
        if not indexes:
            return
        src_idx = self._proxy_model.mapToSource(indexes[0])
        item = self._param_model.itemFromIndex(src_idx)
        if item is None:
            return
        # BUG FIX: itemFromIndex() iki kez çağrılıyordu, ilk çağrı None dönseydi
        # .text() crash olurdu; geçici değişken ile tek çağrıya indirildi.
        if item.column() == 0:
            name = item.text()
        else:
            _sibling = self._param_model.itemFromIndex(src_idx.siblingAtColumn(0))
            name = _sibling.text() if _sibling is not None else ""
        # Kategori başlığını temizle
        clean = name.lstrip("▶ ").strip()
        desc = _PARAM_DESC.get(clean)
        if desc:
            self._desc_label.setText(f"<b>{clean}</b>: {desc}")
        elif item.parent() is None:
            self._desc_label.setText(f"Kategori: {clean}")
        else:
            self._desc_label.setText(f"<b>{clean}</b>: ArduPilot parametresi")

    def _on_search(self, text: str) -> None:
        self._proxy_model.setFilterFixedString(text)

    def _on_param_item_changed(self, item: QStandardItem) -> None:
        if item.column() != 1:
            return
        parent = item.parent()
        if parent is None:
            return  # Kategori satırı, atla
        name_item = parent.child(item.row(), 0)
        if name_item is None:
            return
        name = name_item.text()
        try:
            value = float(item.text())
        except ValueError:
            return
        if self._param_cache.get(name) != value:
            self._modified_params[name] = value
            item.setBackground(QBrush(QColor("#3a3a00")))  # Sarı arka plan

    def _write_modified(self) -> None:
        for name, value in list(self._modified_params.items()):
            self.set_param.emit(name, value)
            logger.info("Parametre yazıldı: %s = %s", name, value)
        self._modified_params.clear()
        # Sarı arka planları temizle
        for row in range(self._param_model.rowCount()):
            cat_item = self._param_model.item(row, 0)
            if cat_item is None:
                continue
            for child_row in range(cat_item.rowCount()):
                it = cat_item.child(child_row, 1)
                if it is not None:
                    it.setBackground(QBrush())

    # ── Yardimci metodlar ─────────────────────────────────────────────────
    def _get_category(self, name: str) -> str:
        upper = name.upper()
        for cat_name, prefixes in _PARAM_CATEGORIES:
            for prefix in prefixes:
                if upper.startswith(prefix.upper()):
                    return cat_name
        return "Diğer"

    def _find_or_create_category(self, cat_name: str) -> QStandardItem:
        for row in range(self._param_model.rowCount()):
            item = self._param_model.item(row, 0)
            if item is not None and item.text() == f"▶ {cat_name}":
                return item
        cat_item = QStandardItem(f"▶ {cat_name}")
        cat_item.setEditable(False)
        cat_item.setForeground(QBrush(QColor("#4ade80")))
        cat_item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._param_model.appendRow([cat_item])
        return cat_item

    @pyqtSlot(str, float)
    def update_param(self, name: str, value: float) -> None:
        self._param_cache[name] = value
        cat_name = self._get_category(name)
        cat_item = self._find_or_create_category(cat_name)
        # Çocuk satırları ara
        for child_row in range(cat_item.rowCount()):
            name_child = cat_item.child(child_row, 0)
            if name_child is not None and name_child.text() == name:
                val_child = cat_item.child(child_row, 1)
                if val_child is not None:
                    val_child.setText(f"{value:.6g}")
                return
        # Yeni parametre
        name_item = QStandardItem(name)
        name_item.setEditable(False)
        val_item = QStandardItem(f"{value:.6g}")
        cat_item.appendRow([name_item, val_item])

    # ── NTRIP Sekmesi ─────────────────────────────────────────────────────────
    def _ntrip_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        grp = QGroupBox("RTK/NTRIP Bağlantısı")
        grp.setStyleSheet("QGroupBox { border: 1px solid #0f3460; color: #aaa; font-weight: bold; }")
        form = QFormLayout(grp)
        form.setSpacing(12)

        self._ntrip_host = QLineEdit()
        self._ntrip_host.setPlaceholderText("rtk2go.com")
        self._ntrip_port = QSpinBox()
        self._ntrip_port.setRange(1, 65535)
        self._ntrip_port.setValue(2101)
        self._ntrip_mount = QLineEdit()
        self._ntrip_mount.setPlaceholderText("MOUNTPOINT")
        self._ntrip_user = QLineEdit()
        self._ntrip_user.setPlaceholderText("Kullanıcı adı")
        self._ntrip_pass = QLineEdit()
        self._ntrip_pass.setPlaceholderText("Şifre")
        self._ntrip_pass.setEchoMode(QLineEdit.EchoMode.Password)

        for widget in [self._ntrip_host, self._ntrip_port, self._ntrip_mount,
                       self._ntrip_user, self._ntrip_pass]:
            if isinstance(widget, QLineEdit):
                widget.setStyleSheet(
                    " border: 1px solid #0f3460; "
                    "padding: 6px; border-radius: 3px; color: #e0e0e0;"
                )

        form.addRow("Sunucu IP/Host:", self._ntrip_host)
        form.addRow("Port:", self._ntrip_port)
        form.addRow("Mountpoint:", self._ntrip_mount)
        form.addRow("Kullanıcı:", self._ntrip_user)
        form.addRow("Şifre:", self._ntrip_pass)

        self._ntrip_status = QLabel("Durum: Bağlı Değil")
        self._ntrip_status.setStyleSheet("color: #888;")
        form.addRow("Durum:", self._ntrip_status)

        btn_row = QHBoxLayout()
        btn_start = QPushButton("NTRIP Başlat")
        btn_start.setStyleSheet(
            " color: #000; padding: 8px; border-radius: 4px; font-weight: bold;"
        )
        btn_start.clicked.connect(self._on_start_ntrip)

        btn_stop = QPushButton("Durdur")
        btn_stop.setStyleSheet(" padding: 8px; border-radius: 4px;")
        btn_stop.clicked.connect(self._on_stop_ntrip)

        btn_row.addWidget(btn_start)
        btn_row.addWidget(btn_stop)

        layout.addWidget(grp)
        layout.addLayout(btn_row)
        layout.addStretch()
        return w

    def _on_start_ntrip(self) -> None:
        host = self._ntrip_host.text().strip()
        port = self._ntrip_port.value()
        mountpoint = self._ntrip_mount.text().strip()
        username = self._ntrip_user.text().strip()
        password = self._ntrip_pass.text()

        if not host or not mountpoint:
            return

        self.start_ntrip.emit(host, port, mountpoint, username, password)
        self._ntrip_status.setText("Durum: Bağlanıyor...")
        self._ntrip_status.setStyleSheet("color: #f0a500;")

    def _on_stop_ntrip(self) -> None:
        self.stop_ntrip.emit()
        self._ntrip_status.setText("Durum: Durduruldu")
        self._ntrip_status.setStyleSheet("color: #888;")

    @pyqtSlot(bool)
    def on_ntrip_connection(self, connected: bool) -> None:
        if connected:
            self._ntrip_status.setText("Durum: Bağlı ✓")
            self._ntrip_status.setStyleSheet("color: #4ade80;")
        else:
            self._ntrip_status.setText("Durum: Bağlı Değil")
            self._ntrip_status.setStyleSheet("color: #888;")
