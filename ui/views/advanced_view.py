"""
advanced_view.py — Modül 5
Full Parameter Tree (QTreeView + QStandardItemModel)
Arama: QSortFilterProxyModel
RTK/NTRIP yapılandırması
"""
import logging
from PyQt6.QtCore import (
    pyqtSignal, pyqtSlot, Qt, QSortFilterProxyModel, QObject, QEvent,
)
from PyQt6.QtGui import QFont, QStandardItemModel, QStandardItem, QBrush, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTreeView, QLineEdit, QPushButton, QGroupBox,
    QFormLayout, QTabWidget, QSpinBox, QHeaderView,
    QMessageBox, QAbstractSpinBox, QComboBox,
)

from core import theme
from core.gcs_config import config

logger = logging.getLogger(__name__)


# ── Fare Tekerleği Koruması ───────────────────────────────────────────────────
# NEDEN: Parametre/NTRIP paneli tekerlekle kaydırılırken imlecin ALTINDAKİ
# QSpinBox (ör. NTRIP portu) değeri SESSİZCE değişiyordu — operatör fark etmeden
# yanlış porta bağlanmaya çalışıyordu. Widget odakta DEĞİLKEN tekerlek olayı
# yutulur (ignore + return True → olay üst scroll alanına gider, panel normal
# kayar ama değer değişmez). StrongFocus: tekerlek artık odak vermez.
class _WheelGuard(QObject):
    """Odakta OLMAYAN spin/combo widget'larında fare tekerleğini etkisizleştirir."""

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if event is not None and event.type() == QEvent.Type.Wheel:
            if isinstance(obj, QWidget) and not obj.hasFocus():
                event.ignore()   # kabul etme → olay üst widget'a (scroll) gider
                return True      # widget'a teslim etme → değer değişmez
        return super().eventFilter(obj, event)

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
        # NTRIP durum etiketinin SON durumu token olarak saklanır — tema
        # değişince aynı durum yeni paletle yeniden boyanabilsin diye.
        self._ntrip_status_token: str = "idle"
        # Açıklama bandındaki "geçersiz sayı" uyarısı satır-içi (HTML) renk
        # taşır; hangi parametre için gösterildiği saklanır ki tema değişince
        # aynı uyarı YENİ paletin danger tonuyla yeniden yazılabilsin.
        self._desc_error_param: str | None = None
        self._wheel_guard = _WheelGuard(self)   # referans tutulmazsa GC'ye gider
        self._build_ui()
        self._install_wheel_guards()
        # Tema değişince kurulumda yazılan renkler bayat kalmasın (C kuralı).
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _apply_theme_colors(self) -> None:
        """Tema değişiminde çalışma-anı renklerini yeniden uygular.

        1) NTRIP durum etiketi son bilinen durumun token'ıyla yeniden boyanır.
        2) Parametre ağacındaki kategori başlıkları ve 'değiştirildi' (dirty)
           satırların arka plan/metin renkleri yeniden yazılır — aksi halde
           koyu temada kurulan sarı zemin açık temada okunmaz kalırdı.
        3) Açıklama bandında bir "geçersiz sayı" uyarısı duruyorsa HTML içine
           gömülü kırmızı da bayat kalır; aynı uyarı yeniden yazılır.
        """
        self._set_ntrip_status(self._ntrip_status.text(), self._ntrip_status_token)
        self._refresh_param_colors()
        if self._desc_error_param is not None:
            self._set_desc_error(self._desc_error_param)

    def _set_desc_error(self, name: str) -> None:
        """Açıklama bandına 'geçersiz sayı' uyarısını AKTİF tema rengiyle yazar.

        Renk HTML içine gömüldüğü için tema değişiminde kendiliğinden
        güncellenmez; parametre adı saklanır ve _apply_theme_colors bu metodu
        yeniden çağırır.
        """
        self._desc_error_param = name
        self._desc_label.setText(
            f"<span style='color:{theme.c('danger')};'><b>{name}</b>: geçersiz sayı — "
            "eski değere geri alındı. Ondalık için '.' veya ',' kullanın.</span>"
        )

    def _refresh_param_colors(self) -> None:
        """Ağaçtaki kategori ve 'değiştirildi' satır renklerini yeniden yazar.

        setBackground/setForeground itemChanged tetikler; yeniden giriş olmasın
        diye model sinyalleri geçici olarak kapatılır, sonunda görünüm elle
        tazelenir.
        """
        if not hasattr(self, "_param_model"):
            return
        self._param_model.blockSignals(True)
        try:
            cat_brush = QBrush(QColor(theme.c("accent")))
            dirty_bg  = QBrush(QColor(theme.c("warning_bg")))
            dirty_fg  = QBrush(QColor(theme.c("warning_fg")))
            for row in range(self._param_model.rowCount()):
                cat_item = self._param_model.item(row, 0)
                if cat_item is None:
                    continue
                cat_item.setForeground(cat_brush)
                for child_row in range(cat_item.rowCount()):
                    name_child = cat_item.child(child_row, 0)
                    val_child  = cat_item.child(child_row, 1)
                    if val_child is None:
                        continue
                    is_dirty = (name_child is not None
                                and name_child.text() in self._modified_params)
                    val_child.setBackground(dirty_bg if is_dirty else QBrush())
                    val_child.setForeground(dirty_fg if is_dirty else QBrush())
        finally:
            self._param_model.blockSignals(False)
        vp = self._tree.viewport()
        if vp is not None:
            vp.update()

    def _install_wheel_guards(self) -> None:
        """Paneldeki tüm spin/combo widget'larına tekerlek korumasını kurar.

        NEDEN: Tek tek eklemek yerine tarayarak kuruyoruz; ileride eklenecek yeni
        bir QSpinBox/QDoubleSpinBox/QComboBox da otomatik korunur.
        """
        for cls in (QAbstractSpinBox, QComboBox):   # QAbstractSpinBox = QSpinBox + QDoubleSpinBox
            for wdg in self.findChildren(cls):
                wdg.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                wdg.installEventFilter(self._wheel_guard)

    def _signal_connected(self, signal) -> bool:
        """Sinyalin gerçekten bir slota bağlı olup olmadığını döndürür.

        NEDEN: Bağlanmamış bir sinyale emit etmek sessizce hiçbir şey yapmaz.
        Kullanıcıya "yazıldı / bağlanıyor" demeden ÖNCE komutun bir alıcıya
        (worker'a) gittiğini doğruluyoruz.
        """
        try:
            return self.receivers(signal) > 0
        except (TypeError, RuntimeError):
            return True   # tespit edilemiyorsa akışı engelleme

    def _build_ui(self) -> None:
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
        self._search_box.textChanged.connect(self._on_search)

        btn_refresh = QPushButton("Yenile (Tümü)")
        # NEDEN: Doğrudan sinyale bağlıydı; araç bağlı değilken basınca hiçbir şey
        # olmuyor, hiçbir geri bildirim de verilmiyordu. Aracı önce doğrulayan bir
        # sarmalayıcıdan geçiyoruz (sinyal adı DEĞİŞMEDİ, yalnızca emit noktası).
        btn_refresh.clicked.connect(self._on_refresh_params)

        btn_write = QPushButton("Değişiklikleri YAZ")
        btn_write.setObjectName("primaryButton")
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
        self._tree.setExpandsOnDoubleClick(True)
        sel = self._tree.selectionModel()
        if sel is not None:
            sel.selectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._tree)

        # Parametre açiklama bandi
        self._desc_label = QLabel("Bir parametre seçin...")
        self._desc_label.setWordWrap(True)
        self._desc_label.setObjectName("mutedLabel")
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
        # Bant artık hata mesajı taşımıyor → tema tazelemesi onu geri getirmesin
        self._desc_error_param = None
        desc = _PARAM_DESC.get(clean)
        if desc:
            self._desc_label.setText(f"<b>{clean}</b>: {desc}")
        elif item.parent() is None:
            self._desc_label.setText(f"Kategori: {clean}")
        else:
            self._desc_label.setText(f"<b>{clean}</b>: ArduPilot parametresi")

    def _on_search(self, text: str) -> None:
        self._proxy_model.setFilterFixedString(text)

    def _on_refresh_params(self) -> None:
        """Parametre listesini talep eder — kanal yoksa açıkça söyler."""
        if not self._signal_connected(self.request_params):
            QMessageBox.warning(
                self, "Bağlantı Yok",
                "Parametre listesi istenemedi: komut kanalı bağlı değil.\n"
                "Önce TopBar'dan araca bağlanın."
            )
            return
        self.request_params.emit()
        self._desc_error_param = None
        self._desc_label.setText("Parametre listesi isteniyor... (yanıt gelince ağaç dolar)")

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
            # NEDEN: Eskiden sessizce return ediliyordu — hücrede geçersiz metin
            # ("abc" ya da virgüllü "4,5") kalıyor, kullanıcı değeri girdiğini
            # sanıyor ama YAZ listesine hiç eklenmiyordu. Artık virgüllü ondalık
            # kabul ediliyor, kabul edilemeyen değer eski değere geri alınıyor ve
            # açıklama bandında kırmızı uyarı gösteriliyor.
            raw = item.text().strip().replace(",", ".")
            try:
                value = float(raw)
            except ValueError:
                old = self._param_cache.get(name)
                self._param_model.blockSignals(True)   # setText → tekrar itemChanged tetiklemesin
                item.setText(f"{old:.6g}" if old is not None else "")
                item.setBackground(QBrush())
                item.setForeground(QBrush())
                self._param_model.blockSignals(False)
                self._modified_params.pop(name, None)
                self._set_desc_error(name)
                return
            # Virgüllü giriş düzeltildi → hücreyi normalize et
            self._param_model.blockSignals(True)
            item.setText(f"{value:.6g}")
            self._param_model.blockSignals(False)

        if self._param_cache.get(name) != value:
            self._modified_params[name] = value
            # "Değiştirildi" işareti: sabit koyu sarı zemin açık temada
            # okunmuyordu — zemin/metin çifti tema token'ından gelir.
            item.setBackground(QBrush(QColor(theme.c("warning_bg"))))
            item.setForeground(QBrush(QColor(theme.c("warning_fg"))))

    def _write_modified(self) -> None:
        # NEDEN (sessiz no-op düzeltmesi): Değişiklik yokken ya da komut kanalı
        # bağlı değilken buton hiçbir şey yapmıyor, hiçbir uyarı da vermiyordu —
        # operatör parametreyi araca yazdığını sanıyordu.
        if not self._modified_params:
            QMessageBox.information(
                self, "Değişiklik Yok",
                "Yazılacak değiştirilmiş parametre yok.\n"
                "Ağaçtaki bir değeri düzenleyin (sarı işaretlenir), sonra yazın."
            )
            return

        if not self._signal_connected(self.set_param):
            QMessageBox.warning(
                self, "Bağlantı Yok",
                f"{len(self._modified_params)} parametre YAZILAMADI: komut kanalı "
                "bağlı değil.\nDeğişiklikleriniz korundu; araca bağlandıktan sonra "
                "tekrar 'Değişiklikleri YAZ'a basın."
            )
            return

        written = list(self._modified_params.items())
        for name, value in written:
            self.set_param.emit(name, value)
            logger.info("Parametre yazıldı: %s = %s", name, value)
        self._modified_params.clear()
        # "Değiştirildi" işaretlerini temizle (dirty listesi boşaldı)
        self._refresh_param_colors()

        self._desc_error_param = None
        self._desc_label.setText(
            f"{len(written)} parametre araca gönderildi — "
            "değerlerin uçuş kontrolcüsünde geçerli olduğunu 'Yenile (Tümü)' ile doğrulayın."
        )

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
        cat_item.setForeground(QBrush(QColor(theme.c("accent"))))
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
        grp.setMaximumWidth(560)
        form = QFormLayout(grp)
        form.setSpacing(6)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        # NEDEN varsayılan değerler config'ten (2026-08-28, kullanıcı talebi):
        # kullanıcının TUSAGA-Aktif abonelik bilgileri gcs_config.py'de kayıtlı
        # (ntrip_host/port/mountpoint/username/password) — panel her açılışta
        # elle doldurulmasın diye buradan önceden dolduruluyor. Otomatik
        # başlatma main_window._start_workers()'ta (ntrip_autostart=True ise).
        self._ntrip_host = QLineEdit()
        self._ntrip_host.setPlaceholderText("rtk2go.com")
        self._ntrip_host.setMaximumWidth(220)
        self._ntrip_host.setText(config.ntrip_host)
        self._ntrip_port = QSpinBox()
        self._ntrip_port.setRange(1, 65535)
        self._ntrip_port.setValue(config.ntrip_port or 2101)
        self._ntrip_port.setMaximumWidth(120)
        self._ntrip_mount = QLineEdit()
        self._ntrip_mount.setPlaceholderText("MOUNTPOINT")
        self._ntrip_mount.setMaximumWidth(200)
        self._ntrip_mount.setText(config.ntrip_mountpoint)
        self._ntrip_user = QLineEdit()
        self._ntrip_user.setPlaceholderText("Kullanıcı adı")
        self._ntrip_user.setMaximumWidth(200)
        self._ntrip_user.setText(config.ntrip_username)
        self._ntrip_pass = QLineEdit()
        self._ntrip_pass.setPlaceholderText("Şifre")
        self._ntrip_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self._ntrip_pass.setMaximumWidth(200)
        self._ntrip_pass.setText(config.ntrip_password)

        form.addRow("Sunucu IP/Host:", self._ntrip_host)
        form.addRow("Port:", self._ntrip_port)
        form.addRow("Mountpoint:", self._ntrip_mount)
        form.addRow("Kullanıcı:", self._ntrip_user)
        form.addRow("Şifre:", self._ntrip_pass)

        self._ntrip_status = QLabel()
        self._set_ntrip_status("Durum: Bağlı Değil", "idle")
        form.addRow("Durum:", self._ntrip_status)

        btn_row = QHBoxLayout()
        btn_start = QPushButton("NTRIP Başlat")
        btn_start.setObjectName("successButton")
        btn_start.setMinimumHeight(32)
        btn_start.clicked.connect(self._on_start_ntrip)

        btn_stop = QPushButton("Durdur")
        btn_stop.setMinimumHeight(32)
        btn_stop.clicked.connect(self._on_stop_ntrip)

        btn_row.addStretch()
        btn_row.addWidget(btn_start)
        btn_row.addWidget(btn_stop)

        # Form + buton satırı 560px'lik ortak kapsayıcıda: butonlar formun
        # sağ kenarına hizalanır, tam pencere genişliğine yayılmaz.
        ntrip_col = QWidget()
        col = QVBoxLayout(ntrip_col)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.addWidget(grp)
        col.addLayout(btn_row)
        ntrip_col.setMaximumWidth(560)

        layout.addWidget(ntrip_col)
        layout.addStretch()
        return w

    def _set_ntrip_status(self, text: str, color: str) -> None:
        """Durum etiketini yazar. `color` artık HAM HEX DEĞİL bir TEMA TOKEN
        ADI'dır (ör. "success" / "idle" / "danger"); renk theme.c ile çözülür
        ve token saklanır — tema değişince aynı durum yeniden boyanır."""
        self._ntrip_status_token = color
        self._ntrip_status.setText(text)
        self._ntrip_status.setStyleSheet(f"color: {theme.c(color)};")

    def _on_start_ntrip(self) -> None:
        host = self._ntrip_host.text().strip()
        port = self._ntrip_port.value()
        mountpoint = self._ntrip_mount.text().strip()
        username = self._ntrip_user.text().strip()
        password = self._ntrip_pass.text()

        # NEDEN (ölü buton düzeltmesi): Eski kod `if not host or not mountpoint:
        # return` ile SESSİZCE çıkıyordu — uyarı yok, durum etiketi değişmiyordu.
        # Operatör butona basıyor, hiçbir şey olmuyor ve nedenini bilmiyordu.
        # Artık hangi alanın boş olduğu tek tek söyleniyor ve odak oraya veriliyor.
        missing: list[str] = []
        if not host:
            missing.append("Sunucu IP/Host")
        if not mountpoint:
            missing.append("Mountpoint")

        if missing:
            self._set_ntrip_status("Durum: Eksik alan — " + ", ".join(missing), "danger")
            QMessageBox.warning(
                self, "Eksik Bilgi",
                "NTRIP başlatılamadı. Zorunlu alan(lar) boş:\n\n"
                + "\n".join(f"  • {m}" for m in missing)
                + "\n\nÖrnek: Sunucu 'rtk2go.com', Port 2101, "
                  "Mountpoint sağlayıcınızın verdiği istasyon adı."
            )
            (self._ntrip_host if not host else self._ntrip_mount).setFocus()
            return

        # NEDEN: Sinyal hiçbir slota bağlı değilse emit sessizce kaybolur ama
        # etiket "Bağlanıyor..." yazardı → operatör RTK geldiğini sanardı.
        if not self._signal_connected(self.start_ntrip):
            self._set_ntrip_status("Durum: Komut kanalı bağlı değil", "danger")
            QMessageBox.warning(
                self, "Bağlantı Yok",
                "NTRIP komut kanalı bağlı değil — istek hiçbir worker'a "
                "ulaşmayacak.\nÖnce TopBar'dan araca bağlanın."
            )
            return

        self.start_ntrip.emit(host, port, mountpoint, username, password)
        logger.info("NTRIP başlatma isteği: %s:%s/%s", host, port, mountpoint)
        self._set_ntrip_status("Durum: Bağlanıyor...", "warning")

    def _on_stop_ntrip(self) -> None:
        # NEDEN: Kanal bağlı değilken de "Durduruldu" yazıyordu (yanıltıcı onay).
        if not self._signal_connected(self.stop_ntrip):
            self._set_ntrip_status("Durum: Komut kanalı bağlı değil", "danger")
            QMessageBox.warning(
                self, "Bağlantı Yok",
                "NTRIP durdurma isteği hiçbir worker'a ulaşmıyor "
                "(komut kanalı bağlı değil)."
            )
            return
        self.stop_ntrip.emit()
        self._set_ntrip_status("Durum: Durduruldu", "idle")

    @pyqtSlot(bool)
    def on_ntrip_connection(self, connected: bool) -> None:
        if connected:
            self._set_ntrip_status("Durum: Bağlı ✓", "success")
        else:
            self._set_ntrip_status("Durum: Bağlı Değil", "idle")
