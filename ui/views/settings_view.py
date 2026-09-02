"""
settings_view.py — GCS Bağlantı Ayarları Ekranı

Tüm IP/port değerleri burada düzenlenir ve settings.json'a kaydedilir.
Kaydet butonuna basınca workers yeniden yapılandırılır.
"""
import logging
from PyQt6.QtCore import pyqtSignal, QObject, QEvent, Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QSpinBox, QPushButton,
    QGroupBox, QFrame, QMessageBox,
    QAbstractSpinBox, QComboBox,
)

from core.gcs_config import config, DEFAULTS
from core import theme

logger = logging.getLogger(__name__)


# ── Fare Tekerleği Koruması ───────────────────────────────────────────────────
# NEDEN: Ayar paneli tekerlekle kaydırılırken imlecin ALTINDAKİ QSpinBox
# (port alanları) değeri SESSİZCE değişiyordu — operatör farkına varmadan
# görev/durum/video/MAVLink portu bozuluyor ve bağlantı kopuyordu.
# ÇÖZÜM: Widget odakta DEĞİLKEN tekerlek olayını yut (ignore + return True) →
# olay üst widget'a (scroll alanı) gider, panel kayar ama değer değişmez.
# StrongFocus: widget artık tekerlekle odak almaz, yalnızca tıklama/Tab ile.
class _WheelGuard(QObject):
    """Odakta OLMAYAN spin/combo widget'larında fare tekerleğini etkisizleştirir."""

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if event is not None and event.type() == QEvent.Type.Wheel:
            if isinstance(obj, QWidget) and not obj.hasFocus():
                event.ignore()   # kabul etme → olay üst widget'a (scroll) gider
                return True      # widget'a teslim etme → değer değişmez
        return super().eventFilter(obj, event)


class SettingsView(QWidget):
    """Bağlantı ayarları ekranı. Kaydedince settings_changed sinyali yayar."""

    settings_changed = pyqtSignal(dict)   # yeni ayarlar dict'i

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fields: dict[str, QLineEdit | QSpinBox] = {}
        self._separators: list[QFrame] = []
        self._wheel_guard = _WheelGuard(self)   # referans tutulmazsa GC'ye gider
        self._build_ui()
        self._install_wheel_guards()
        self._load_current()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

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
        Kullanıcıya "uygulandı" demeden ÖNCE komutun bir alıcıya gittiğini
        doğruluyoruz (bu paneldeki "ölü kontrol" hatalarının kök nedeni buydu).
        """
        try:
            return self.receivers(signal) > 0
        except (TypeError, RuntimeError):
            return True   # tespit edilemiyorsa akışı engelleme

    # ── UI ────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        title = QLabel("Bağlantı Ayarları")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        # NEDEN (dürüstlük): Eski metin "worker'lar yeniden yapılandırılır" diyordu
        # ama MAVLink adresi hiçbir worker'a uygulanmıyor (main_window sadece
        # companion + video worker'larını tazeliyor). Metin kapsamı daraltıldı.
        sub = QLabel("Companion ve video ayarları kaydedilince ANINDA uygulanır. "
                     "MAVLink adresi için TopBar'dan yeniden bağlanmanız gerekir.")
        sub.setObjectName("mutedLabel")
        sub.setWordWrap(True)
        root.addWidget(sub)

        root.addWidget(self._separator())

        # ── Görünüm / Tema ────────────────────────────────────────────
        # Tema ANINDA uygulanır ve settings.json'a yazılır (Kaydet beklemez):
        # kullanıcı seçimin sonucunu görerek karar verir.
        appearance_grp = QGroupBox("Görünüm")
        appearance_grp.setMaximumWidth(640)
        appearance_lay = QGridLayout(appearance_grp)
        appearance_lay.setSpacing(6)
        appearance_lay.addWidget(QLabel("Tema:"), 0, 0)

        self._theme_combo = QComboBox()
        self._theme_combo.addItem("Koyu (Aviyonik)", theme.DARK)
        self._theme_combo.addItem("Açık (Gündüz)", theme.LIGHT)
        self._theme_combo.setMaximumWidth(220)
        self._theme_combo.setToolTip(
            "Açık tema güneş altında/aydınlık ortamda okunabilirlik için.\n"
            "Seçim anında uygulanır ve kaydedilir."
        )
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        appearance_lay.addWidget(self._theme_combo, 0, 1)
        appearance_lay.setColumnStretch(2, 1)

        root.addWidget(appearance_grp)

        # ── Companion ─────────────────────────────────────────────────
        comp_grp = QGroupBox("Yardımcı Bilgisayar (Companion)")
        comp_grp.setMaximumWidth(640)
        comp_layout = QGridLayout(comp_grp)
        comp_layout.setSpacing(6)

        self._add_field(comp_layout, 0, "IP Adresi",
                        "companion_host", is_spin=False,
                        placeholder="192.168.1.229")
        self._add_field(comp_layout, 1, "Görev Portu (TCP)",
                        "companion_mission_port", is_spin=True, spin_range=(1, 65535))
        self._add_field(comp_layout, 2, "Durum Portu (TCP)",
                        "companion_status_port", is_spin=True, spin_range=(1, 65535))
        self._add_field(comp_layout, 3, "Video Portu (HTTP)",
                        "companion_video_port", is_spin=True, spin_range=(1, 65535))
        self._add_field(comp_layout, 4, "Kimlik Doğrulama Anahtarı",
                        "companion_auth_token", is_spin=False,
                        placeholder="boş = doğrulama KAPALI (güvensiz)",
                        password=True)
        _auth_note = QLabel(
            "Görev/servo/abort komutları bu kanaldan (5601) geçer — companion'daki\n"
            "config.json → gcs_server.auth_token ile BİREBİR AYNI olmalı. Boş\n"
            "bırakılırsa aynı ağdaki herhangi bir cihaz komut gönderebilir.")
        _auth_note.setWordWrap(True)
        comp_layout.addWidget(_auth_note, 5, 0, 1, 2)

        # ── ARACI BUL (2026-09-03) ────────────────────────────────────
        # NEDEN: aracın adresi tek günde iki kez değişti (dahili WiFi .109 →
        # AC1200 dongle .229) ve sahada DHCP her açılışta farklı verebilir.
        # Operatörün IP ezberlemesi/yazması hem hata kaynağı hem de 8 dakikalık
        # uçuşa-hazırlık saatinden yiyen bir iş (şartname satır 1011).
        # Bu düğme sırayla dener: mevcut ayar → mDNS adı → bilinen adresler →
        # alt ağ taraması; bulduğunu alana YAZAR.
        self._btn_bul = QPushButton("Aracı Bul ve Sına")
        self._btn_bul.setToolTip(
            "Aracı ağda arar ve bulduğu adresi yukarıdaki alana yazar.\n"
            "Sıra: mevcut ayar → ashina-iha.local (mDNS) → bilinen adresler → tarama.")
        self._btn_bul.clicked.connect(self._on_araci_bul)
        comp_layout.addWidget(self._btn_bul, 6, 0)
        self._bul_sonuc = QLabel("")
        self._bul_sonuc.setWordWrap(True)
        comp_layout.addWidget(self._bul_sonuc, 6, 1)

        root.addWidget(comp_grp)

        # ── MAVLink ───────────────────────────────────────────────────
        mav_grp = QGroupBox("MAVLink Bağlantısı (SITL / FCU)")
        mav_grp.setMaximumWidth(640)
        mav_layout = QGridLayout(mav_grp)
        mav_layout.setSpacing(6)

        self._add_field(mav_layout, 0, "Host / IP",
                        "mavlink_host", is_spin=False,
                        placeholder="192.168.1.229")
        self._add_field(mav_layout, 1, "Port",
                        "mavlink_port", is_spin=True, spin_range=(1, 65535))

        # NEDEN (ölü kontrol düzeltmesi): Bu iki alan settings.json'a yazılıyor
        # ama çalışan MAVLink bağlantısına UYGULANMIYOR — main_window'un
        # _on_settings_changed'i yalnızca companion adreslerini ve video URL'lerini
        # tazeliyor; config.mavlink_address kod tabanında hiçbir yerden okunmuyor.
        # TopBar'ın adres kutusu da bu değerle DOLDURULMUYOR. Kullanıcı "kaydettim,
        # oldu" sanmasın diye gerçek davranış alanların yanında açıkça yazıyor.
        mav_note = QLabel(
            "ℹ️ Bu değerler yalnızca kayıt/referans içindir. Aktif MAVLink bağlantısını "
            "DEĞİŞTİRMEZ.<br>Uygulamak için: TopBar'daki adres kutusuna "
            "<b>tcp:host:port</b> yazıp <b>Bağlan</b>'a basın "
            "(ya da uygulamayı yeniden başlatın)."
        )
        mav_note.setObjectName("warningText")
        mav_note.setWordWrap(True)
        mav_layout.addWidget(mav_note, 2, 0, 1, 3)

        _mav_tip = ("Kaydedilir ancak çalışan bağlantıya uygulanmaz — "
                    "TopBar'dan 'Bağlan' ile yeniden bağlanın.")
        self._fields["mavlink_host"].setToolTip(_mav_tip)
        self._fields["mavlink_port"].setToolTip(_mav_tip)

        root.addWidget(mav_grp)

        # ── Mevcut değerler önizleme ───────────────────────────────────
        self._preview = QLabel()
        self._preview.setObjectName("monoLabel")
        self._preview.setStyleSheet(f"color:{theme.c('text_muted')};")
        self._preview.setWordWrap(True)
        root.addWidget(self._preview)

        root.addStretch()

        # ── Butonlar ──────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_reset = QPushButton("Varsayilanlara Sifirla")
        btn_reset.clicked.connect(self._on_reset)
        btn_row.addWidget(btn_reset)

        btn_save = QPushButton("Kaydet ve Uygula")
        btn_save.setObjectName("primaryButton")
        btn_save.setMinimumHeight(34)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        root.addLayout(btn_row)

    def _on_araci_bul(self) -> None:
        """Aracı ağda ara, bulunanı IP alanına yaz.

        NEDEN arayüzü kilitliyor: tam tarama birkaç saniye sürebilir ve bu
        işlem operatörün BİLEREK bastığı tek seferlik bir eylem. Yine de
        önce ağ trafiği üretmeyen hızlı yollar (mevcut ayar, mDNS, bilinen
        adresler) deneniyor; canlı araçta ölçüldü: 0.02 s.
        """
        from core import iha_bul

        alan = self._fields.get("companion_host")
        mevcut = alan.text().strip() if alan is not None else ""
        self._bul_sonuc.setText("Aranıyor…")
        self._btn_bul.setEnabled(False)
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()          # "Aranıyor…" ekrana çıksın
            adres, aciklama = iha_bul.bul(mevcut, port=config.companion_status_port)
        except Exception as exc:
            adres, aciklama = None, f"Arama hatası: {exc}"
        finally:
            self._btn_bul.setEnabled(True)

        if adres:
            if alan is not None:
                alan.setText(adres)
            self._bul_sonuc.setText(f"✓ {aciklama}")
            self._bul_sonuc.setStyleSheet(f"color:{theme.c('success')};")
            logger.info("Araç bulundu: %s (%s)", adres, aciklama)
        else:
            self._bul_sonuc.setText(f"✗ {aciklama}")
            self._bul_sonuc.setStyleSheet(f"color:{theme.c('danger')};")
            logger.warning("Araç bulunamadı: %s", aciklama)

    def _add_field(self, layout: QGridLayout, row: int, label: str,
                   key: str, is_spin: bool, placeholder: str = "",
                   spin_range: tuple = (1, 65535), password: bool = False) -> None:
        lbl = QLabel(label + ":")
        layout.addWidget(lbl, row, 0)

        if is_spin:
            widget = QSpinBox()
            widget.setRange(*spin_range)
            widget.setMaximumWidth(140)
            layout.addWidget(widget, row, 1)
        else:
            widget = QLineEdit()
            widget.setPlaceholderText(placeholder)
            widget.setMaximumWidth(200)
            if password:
                # 2026-08-26: paylaşılan sır düz metin gösteriliyordu (omuz
                # sörfü/ekran paylaşımı riski) — diğer parola alanlarıyla
                # (NTRIP, giriş) tutarlı şekilde varsayılan olarak maskeli,
                # "Göster" ile açılıp kapanabilir.
                widget.setEchoMode(QLineEdit.EchoMode.Password)
                row_box = QHBoxLayout()
                row_box.setContentsMargins(0, 0, 0, 0)
                row_box.addWidget(widget)
                toggle = QPushButton("Göster")
                toggle.setMaximumWidth(70)
                toggle.setCheckable(True)

                def _toggle_echo(checked: bool, w=widget, b=toggle) -> None:
                    w.setEchoMode(QLineEdit.EchoMode.Normal if checked
                                  else QLineEdit.EchoMode.Password)
                    b.setText("Gizle" if checked else "Göster")

                toggle.toggled.connect(_toggle_echo)
                row_box.addWidget(toggle)
                layout.addLayout(row_box, row, 1)
            else:
                layout.addWidget(widget, row, 1)

        layout.setColumnStretch(2, 1)
        self._fields[key] = widget

    def _separator(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet(f"color:{theme.c('border')};")
        self._separators.append(f)
        return f

    # ── Tema ──────────────────────────────────────────────────────────
    def _on_theme_changed(self, _index: int) -> None:
        """Tema seçimi — anında uygula + kalıcı kaydet."""
        name = self._theme_combo.currentData()
        if not name or name == theme.current():
            return
        theme.set_theme(name)
        config.set("ui_theme", name)
        config.save()

    def _apply_theme_colors(self) -> None:
        """Tema değişince yeniden kurulması gereken inline renkler."""
        self._preview.setStyleSheet(f"color:{theme.c('text_muted')};")
        for sep in self._separators:
            sep.setStyleSheet(f"color:{theme.c('border')};")

    # ── Değer yönetimi ────────────────────────────────────────────────
    def _load_current(self) -> None:
        for key, widget in self._fields.items():
            val = config.get(key)
            if isinstance(widget, QSpinBox):
                # BUG FIX: config.get() None dönebilir → int(None) TypeError crash
                widget.setValue(int(val) if val is not None else 0)
            else:
                widget.setText(str(val) if val is not None else "")
        # Tema kutusu _fields'te değil (anında uygulanır, Kaydet'e bağlı değil);
        # sinyali susturarak yükle ki açılışta tema yeniden uygulanmasın.
        self._theme_combo.blockSignals(True)
        idx = self._theme_combo.findData(theme.current())
        self._theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._theme_combo.blockSignals(False)
        self._update_preview()

    def _update_preview(self) -> None:
        vals = self._collect()
        lines = [
            f"Görev: {vals['companion_host']}:{vals['companion_mission_port']}",
            f"Durum: {vals['companion_host']}:{vals['companion_status_port']}",
            f"Video: http://{vals['companion_host']}:{vals['companion_video_port']}/video",
            f"MAVLink: tcp:{vals['mavlink_host']}:{vals['mavlink_port']}",
        ]
        self._preview.setText("  |  ".join(lines))

    def _collect(self) -> dict:
        result = {}
        for key, widget in self._fields.items():
            result[key] = widget.value() if isinstance(widget, QSpinBox) else widget.text().strip()
        return result

    # ── Slotlar ───────────────────────────────────────────────────────
    def _persist(self, vals: dict) -> bool:
        """Değerleri settings.json'a yazar ve dinleyicilere duyurur.

        NEDEN: Kaydetme yolu tek noktaya alındı; "Varsayılanlara Sıfırla" eskiden
        yalnızca widget'ları dolduruyor, config.set/save ÇAĞIRMIYORDU → kullanıcı
        sıfırlayıp uygulamayı kapatınca eski ayarlar geri geliyordu. Artık her iki
        buton da aynı gerçek kaydetme yolunu kullanıyor.

        Dönüş: settings_changed sinyalinin gerçekten bir alıcıya ulaşıp ulaşmadığı.
        """
        for key, value in vals.items():
            config.set(key, value)
        config.save()
        self._update_preview()

        delivered = self._signal_connected(self.settings_changed)
        self.settings_changed.emit(vals)
        if not delivered:
            logger.warning("settings_changed hiçbir slota bağlı değil — "
                           "worker'lar yeniden yapılandırılamadı.")
        return delivered

    def _applied_text(self, delivered: bool, auth_token: str = "") -> str:
        """Kaydetme sonrası DÜRÜST özet metni.

        NEDEN: Eski metin "Bağlantı worker'ları yeniden yapılandırıldı" diyordu;
        oysa MAVLink adresi hiçbir worker'a uygulanmıyor. Neyin anında geçerli
        olduğu ile neyin yeniden bağlanma gerektirdiği ayrı ayrı yazılıyor.
        """
        warn = ""
        if not auth_token:
            # 2026-08-26: boş auth_token ile kaydedilirse/sıfırlanırsa göz
            # ardı edilemez bir uyarı gösterilmeli — önceden risk yalnızca
            # kod yorumunda belgeliydi, kullanıcıya hiç görünmüyordu.
            warn = ("⚠ GÜVENLİK: Kimlik Doğrulama Anahtarı BOŞ — companion "
                    "komut (5601) ve durum (5602) kanalları aynı ağdaki "
                    "HERKESE açık. Yarışma/gerçek uçuş öncesi doldurun.\n\n")
        if not delivered:
            return (warn + "Ayarlar dosyaya kaydedildi.\n\n"
                    "⚠ Ancak ayar kanalı bağlı değil — çalışan worker'lar "
                    "güncellenmedi. Değişikliklerin geçerli olması için "
                    "uygulamayı yeniden başlatın.")
        return (warn + "Ayarlar kaydedildi.\n\n"
                "• Companion (görev/durum) ve video worker'ları yeniden "
                "yapılandırıldı — anında geçerli.\n"
                "• MAVLink host/port UYGULANMADI: TopBar'dan 'Kes' → 'Bağlan' ile "
                "yeniden bağlanın (ya da uygulamayı yeniden başlatın).")

    def _on_save(self) -> None:
        vals = self._collect()

        # Basit doğrulama
        if not vals.get("companion_host"):
            QMessageBox.warning(self, "Hata", "Companion IP adresi bos birakilamaz.")
            return
        if not vals.get("mavlink_host"):
            QMessageBox.warning(self, "Hata", "MAVLink host bos birakilamaz.")
            return

        delivered = self._persist(vals)
        logger.info("Ayarlar kaydedildi ve uygulanmaya gönderildi.")

        QMessageBox.information(
            self, "Kaydedildi",
            self._applied_text(delivered, vals.get("companion_auth_token", "")))

    def _on_reset(self) -> None:
        # NEDEN (ölü kontrol düzeltmesi): Onay metni "fabrika değerlerine dönecek"
        # diyordu ama eski kod yalnızca widget'ları dolduruyordu — kayıt yok,
        # sinyal yok. Artık gerçekten kaydediliyor; diyalog metni de bunu söylüyor.
        reply = QMessageBox.question(
            self, "Sifirla",
            "Tüm ayarlar fabrika değerlerine dönecek ve HEMEN kaydedilecek.\n"
            "Mevcut companion/MAVLink adresleriniz kaybolur. Emin misiniz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for key, widget in self._fields.items():
            val = DEFAULTS[key]
            if isinstance(widget, QSpinBox):
                widget.setValue(int(val))
            else:
                widget.setText(str(val))

        vals = self._collect()
        delivered = self._persist(vals)
        logger.info("Ayarlar varsayılanlara sıfırlandı ve kaydedildi.")

        QMessageBox.information(
            self, "Sıfırlandı",
            "Ayarlar fabrika değerlerine döndürüldü ve kaydedildi.\n\n"
            + self._applied_text(delivered, vals.get("companion_auth_token", ""))
        )
