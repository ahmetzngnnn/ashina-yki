"""
ui/views/antenna_tracker_view.py — Anten Takip Paneli
======================================================

Antenin İHA'yı takibini görsel olarak gösterir ve kontrol eder:
  • Pusula göstergesi (azimut okı + elevasyon barı)
  • Canlı değerler (azimut/elevasyon/mesafe/irtifa farkı)
  • Aç/kapat, bağlantı durumu, mod
  • Anten home ayarı (İHA konumundan veya elle)

Sinyaller AntennaTrackerWorker'a/worker'dan main_window üzerinden bağlanır.
"""
from __future__ import annotations
import math

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QGroupBox, QLineEdit, QFormLayout, QSizePolicy, QMessageBox,
    QAbstractSpinBox, QComboBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QPointF, QRectF, QObject, QEvent
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPolygonF

from core import theme


# ── Aviyonik semantik renkler ────────────────────────────────────────────────
# NEDEN: Anten ibresi ve elevasyon hedef işaretleyicisi bir uçuş aleti
# göstergesidir; operatör "yeşil = takip ediyor / gri = pasif" kodunu doğrudan
# renginden okur. Pusulanın kromu (halka, tikler, harfler, şerit zemini) temaya
# uyar, AMA bu iki renk tema değişse de AYNI kalır.
ANT_AIM_ACTIVE = "#00e676"   # ibre + elevasyon işaretleyici: takip aktif — aviyonik semantik, temadan bağımsız
ANT_AIM_IDLE   = "#777777"   # ibre + elevasyon işaretleyici: takip pasif — aviyonik semantik, temadan bağımsız


# ── Fare Tekerleği Koruması ───────────────────────────────────────────────────
# NEDEN: Panel tekerlekle kaydırılırken imlecin ALTINDAKİ QSpinBox/QComboBox
# değeri SESSİZCE değişiyor (canlı gözlendi). Anten kontrol paneli için bu,
# operatör farkında olmadan yanlış referans/açı verilmesi demek. Widget odakta
# DEĞİLKEN tekerlek olayı yutulur → panel normal kayar, değer değişmez.
class _WheelGuard(QObject):
    """Odakta OLMAYAN spin/combo widget'larında fare tekerleğini etkisizleştirir."""

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if event is not None and event.type() == QEvent.Type.Wheel:
            if isinstance(obj, QWidget) and not obj.hasFocus():
                event.ignore()   # kabul etme → olay üst widget'a (scroll) gider
                return True      # widget'a teslim etme → değer değişmez
        return super().eventFilter(obj, event)


# ── Pusula / Elevasyon Göstergesi ───────────────────────────────────────────
class CompassWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._az = 0.0
        self._el = 0.0
        self._tracking = False
        self.setMinimumSize(260, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _apply_theme_colors(self):
        """Tema değişince yeniden çizer — renkler paintEvent içinde okunur."""
        self.update()

    def set_aim(self, az: float, el: float, tracking: bool):
        self._az = az; self._el = el; self._tracking = tracking
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # Sağda elevasyon şeridi için 48px + 8px boşluk ayrılır; pusula kalan alana çizilir
        gauge_w, gap = 48, 8
        avail_w = max(60, w - gauge_w - gap)
        size = min(avail_w, h) - 20
        cx, cy = avail_w / 2, h / 2
        r = size / 2

        # Dış halka — krom, temadan gelir (açık temada beyaz kadran + gri halka)
        p.setPen(QPen(QColor(theme.c("border_strong")), 3))
        p.setBrush(QBrush(QColor(theme.c("well"))))
        p.drawEllipse(QPointF(cx, cy), r, r)

        # Yön etiketleri ve çizgiler — krom
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        for ang, lbl in [(0, "K"), (90, "D"), (180, "G"), (270, "B")]:
            a = math.radians(ang - 90)
            x1 = cx + (r - 18) * math.cos(a); y1 = cy + (r - 18) * math.sin(a)
            x2 = cx + r * math.cos(a); y2 = cy + r * math.sin(a)
            p.setPen(QPen(QColor(theme.c("text_muted")), 2))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            tx = cx + (r - 32) * math.cos(a); ty = cy + (r - 32) * math.sin(a)
            p.setPen(QColor(theme.c("text")))
            p.drawText(QRectF(tx - 12, ty - 10, 24, 20), Qt.AlignmentFlag.AlignCenter, lbl)

        # Derece tikleri (her 30°) — krom
        # NEDEN border_strong: açık temada "border" beyaz kadran üzerinde
        # neredeyse kaybolur; tikler her iki temada da seçilebilir kalmalı.
        p.setPen(QPen(QColor(theme.c("border_strong")), 1))
        for ang in range(0, 360, 30):
            a = math.radians(ang - 90)
            x1 = cx + (r - 8) * math.cos(a); y1 = cy + (r - 8) * math.sin(a)
            x2 = cx + r * math.cos(a); y2 = cy + r * math.sin(a)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Azimut okı (antenin baktığı yön) — AVİYONİK SEMANTİK, temadan bağımsız
        col = QColor(ANT_AIM_ACTIVE if self._tracking else ANT_AIM_IDLE)
        a = math.radians(self._az - 90)
        tip = QPointF(cx + (r - 24) * math.cos(a), cy + (r - 24) * math.sin(a))
        left = QPointF(cx + 14 * math.cos(a + 2.5), cy + 14 * math.sin(a + 2.5))
        right = QPointF(cx + 14 * math.cos(a - 2.5), cy + 14 * math.sin(a - 2.5))
        p.setBrush(QBrush(col)); p.setPen(QPen(col, 2))
        p.drawPolygon(QPolygonF([tip, left, right]))
        # İbre pivotu krom sayılır: sabit açık gri kalsaydı beyaz kadranda kaybolurdu
        p.setBrush(QBrush(QColor(theme.c("text"))))
        p.drawEllipse(QPointF(cx, cy), 6, 6)   # kenarlığı ibre renginde kalır

        # Merkez azimut yazısı — krom (dijital okuma)
        p.setPen(QColor(theme.c("accent"))); p.setFont(QFont("Consolas", 13, QFont.Weight.Bold))
        p.drawText(QRectF(cx - 40, cy + 12, 80, 22), Qt.AlignmentFlag.AlignCenter,
                   f"{self._az:.0f}°")

        # Elevasyon barı (sağdaki 48px şeridin ortası, pusuladan 8px boşlukla ayrık)
        bx = w - gauge_w / 2; bt = cy - r; bb = cy + r
        p.setPen(QPen(QColor(theme.c("border_strong")), 2))
        p.setBrush(QBrush(QColor(theme.c("well"))))
        p.drawRect(QRectF(bx - 6, bt, 12, bb - bt))
        # -90..+90 → bar konumu (işaretleyici AVİYONİK — ibre ile aynı renk kodu)
        frac = (90 - max(-90, min(90, self._el))) / 180.0
        ey = bt + frac * (bb - bt)
        p.setBrush(QBrush(col)); p.setPen(QPen(col, 1))
        p.drawRect(QRectF(bx - 9, ey - 3, 18, 6))
        p.setPen(QColor(theme.c("text"))); p.setFont(QFont("Consolas", 8))
        p.drawText(QRectF(bx - 20, ey - 18, 40, 14), Qt.AlignmentFlag.AlignCenter, f"{self._el:.0f}°")
        p.end()


# ── Ana Panel ────────────────────────────────────────────────────────────────
class AntennaTrackerView(QWidget):
    enable_requested      = pyqtSignal()
    disable_requested     = pyqtSignal()
    set_home_from_drone   = pyqtSignal()
    set_home_manual       = pyqtSignal(float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._connected = False        # son bilinen bağlantı durumu (tema tazelemesi için)
        self._state_text = "● PASİF"   # son bilinen durum metni + token — tema
        self._state_token = "idle"     # değişince aynı durum yeniden boyanır
        self._home_msg_text = ""       # elle-home sonuç bandının son metni + token
        self._home_msg_token = "text_muted"
        self._value_labels: list[QLabel] = []
        self._wheel_guard = _WheelGuard(self)   # referans tutulmazsa GC'ye gider
        self._build()
        self._install_wheel_guards()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _apply_theme_colors(self):
        """Kurulumda yazılmış tüm satır-içi renkleri tema değişiminde yeniden yazar.

        NEDEN: setStyleSheet ile verilen renk QSS yenilenince güncellenmez; durum
        etiketi/değerler son bilinen DURUMLARIYLA (metin aynı kalarak) yeniden
        boyanır, yoksa koyu tema renkleri açık temada okunmaz halde kalır.
        """
        self._set_state(self._state_text, self._state_token)
        for lbl in self._value_labels:
            lbl.setStyleSheet(f"color:{theme.c('accent')};")
        self.set_connection(self._connected)
        self._set_home_msg(self._home_msg_text, self._home_msg_token)

    def _install_wheel_guards(self):
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
        Anten komutlarında bu, operatörün "takip başladı" sanması demek.
        """
        try:
            return self.receivers(signal) > 0
        except (TypeError, RuntimeError):
            return True   # tespit edilemiyorsa akışı engelleme

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Sol: pusula (kalan tüm alanı alır) ──
        self._compass = CompassWidget()
        root.addWidget(self._compass, stretch=1)

        # ── Sağ: kontrol + değerler — sabit 380px panel ──
        right_panel = QWidget()
        right_panel.setFixedWidth(380)
        right = QVBoxLayout(right_panel)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)

        # Durum başlığı
        st_grp = QGroupBox("Anten Takip Durumu")
        st = QGridLayout(st_grp)
        self._lbl_state = QLabel("● PASİF")
        _sf = self._lbl_state.font(); _sf.setPixelSize(15); _sf.setBold(True)
        self._lbl_state.setFont(_sf)
        self._set_state(self._state_text, self._state_token)   # PASİF → idle rengi
        self._lbl_conn = QLabel("Anten Pixhawk: bağlı değil")
        self._lbl_conn.setObjectName("mutedLabel")
        st.addWidget(self._lbl_state, 0, 0, 1, 2)
        st.addWidget(self._lbl_conn, 1, 0, 1, 2)
        right.addWidget(st_grp)

        # Canlı değerler
        val_grp = QGroupBox("Hedef Açıları ve Mesafe")
        vg = QFormLayout(val_grp); vg.setSpacing(4)
        vg.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        def _v():
            l = QLabel("—")
            l.setObjectName("monoLabel")   # mono yazı tipi QSS'ten, aksan rengi tokendan
            l.setStyleSheet(f"color:{theme.c('accent')};")
            l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._value_labels.append(l)
            return l
        self._v_az = _v(); self._v_el = _v(); self._v_gd = _v(); self._v_sd = _v(); self._v_ad = _v()
        vg.addRow("Azimut (Pan):", self._v_az)
        vg.addRow("Elevasyon (Tilt):", self._v_el)
        vg.addRow("Yatay Mesafe:", self._v_gd)
        vg.addRow("Eğik Mesafe:", self._v_sd)
        vg.addRow("İrtifa Farkı:", self._v_ad)
        right.addWidget(val_grp)

        # Kontrol
        ctl_grp = QGroupBox("Kontrol")
        cg = QVBoxLayout(ctl_grp)
        self._btn_toggle = QPushButton("▶ Takibi Başlat")
        self._btn_toggle.setObjectName("successButton")
        self._btn_toggle.setMinimumHeight(30)
        self._btn_toggle.clicked.connect(self._on_toggle)
        cg.addWidget(self._btn_toggle)

        home_row = QHBoxLayout()
        self._btn_home_drone = QPushButton("Anten Home = İHA Konumu")
        self._btn_home_drone.setMinimumHeight(28)
        self._btn_home_drone.setToolTip("Antenin referans konumunu, anlık İHA GPS'ine ayarlar (saha kalibrasyonu)")
        # NEDEN: Doğrudan sinyale bağlıydı — komut kanalı bağlı değilse basınca
        # hiçbir şey olmuyor, geri bildirim de yoktu. Sarmalayıcı ile doğrulanıyor
        # (sinyal adı DEĞİŞMEDİ, yalnızca emit noktası).
        self._btn_home_drone.clicked.connect(self._on_home_from_drone)
        cg.addWidget(self._btn_home_drone)

        man_grp = QGroupBox("Elle Anten Home (opsiyonel)")
        mg = QFormLayout(man_grp); mg.setSpacing(6)
        # NEDEN: Türkçe klavyede ondalık ayracı virgüldür; "39,92" girildiğinde
        # eski kod ValueError'ı yutup hiçbir şey yapmıyordu. Placeholder/tooltip
        # artık her iki biçimin de kabul edildiğini söylüyor.
        self._e_lat = QLineEdit(); self._e_lat.setPlaceholderText("Enlem (örn. 39.925014)")
        self._e_lon = QLineEdit(); self._e_lon.setPlaceholderText("Boylam (örn. 32.836944)")
        self._e_alt = QLineEdit(); self._e_alt.setPlaceholderText("İrtifa (m) — boş = 0")
        self._e_lat.setToolTip("Ondalık derece, -90 ile +90 arası. '.' veya ',' kullanabilirsiniz.")
        self._e_lon.setToolTip("Ondalık derece, -180 ile +180 arası. '.' veya ',' kullanabilirsiniz.")
        self._e_alt.setToolTip("Deniz seviyesinden metre, -500 ile +10000 arası. Boş bırakılırsa 0.")
        for _e in (self._e_lat, self._e_lon, self._e_alt):
            _e.setMaximumWidth(220)
        mg.addRow("Enlem:", self._e_lat); mg.addRow("Boylam:", self._e_lon); mg.addRow("İrtifa:", self._e_alt)
        btn_man = QPushButton("Elle Ayarla")
        btn_man.setMinimumHeight(28)
        btn_man.clicked.connect(self._on_manual_home)
        _man_row = QHBoxLayout()
        _man_row.addStretch()
        _man_row.addWidget(btn_man)
        mg.addRow(_man_row)
        # Sonuç bandı — eskiden hiçbir geri bildirim yoktu, buton "ölü" görünüyordu
        self._lbl_home = QLabel("")
        self._lbl_home.setWordWrap(True)
        self._lbl_home.setObjectName("mutedLabel")
        mg.addRow(self._lbl_home)
        cg.addWidget(man_grp)
        right.addWidget(ctl_grp)

        right.addStretch()
        root.addWidget(right_panel)

    # ── Kullanıcı eylemleri ────────────────────────────────────────────────
    def _set_state(self, text: str, token: str):
        """Durum etiketini yazar ve rengini tema token'ından verir.

        Metin + token saklanır; tema değişince _apply_theme_colors aynı durumu
        yeni palette tekrar uygular.
        """
        self._state_text = text
        self._state_token = token
        self._lbl_state.setText(text)
        self._lbl_state.setStyleSheet(f"color:{theme.c(token)};")

    def _set_home_msg(self, text: str, token: str = "text_muted"):
        """Elle-home sonuç bandı; renk ham hex değil tema token'ı ile verilir."""
        self._home_msg_text = text
        self._home_msg_token = token
        self._lbl_home.setText(text)
        self._lbl_home.setStyleSheet(f"color:{theme.c(token)};")

    @staticmethod
    def _parse_coord(text: str, default: float | None = None) -> float:
        """Ondalık sayı ayrıştırır; hem '.' hem ',' ayracını kabul eder.

        NEDEN: Türkçe klavye/yerel ayarda kullanıcı "39,925014" yazıyor;
        float() bunu ValueError'a düşürüyordu. Boşluk ve binlik ayracı olmayan
        basit girişte ',' → '.' dönüşümü yeterli ve güvenli.
        """
        raw = text.strip().replace(",", ".")
        if not raw:
            if default is None:
                raise ValueError("boş")
            return default
        return float(raw)

    def _on_toggle(self):
        # NEDEN: Sinyal bağlı değilse buton tamamen ölüdür ama kullanıcı bunu
        # göremez. Emit etmeden önce alıcı var mı diye bakıyoruz.
        sig = self.disable_requested if self._active else self.enable_requested
        if not self._signal_connected(sig):
            QMessageBox.warning(
                self, "Bağlantı Yok",
                "Anten takip komut kanalı bağlı değil — istek hiçbir worker'a "
                "ulaşmıyor.\nAnten Pixhawk bağlantısını kontrol edin."
            )
            return
        sig.emit()

    def _on_home_from_drone(self):
        """Anten home'unu anlık İHA konumuna ayarlar (kanal doğrulamalı)."""
        if not self._signal_connected(self.set_home_from_drone):
            self._set_home_msg("Komut kanalı bağlı değil — home ayarlanamadı.", "danger")
            QMessageBox.warning(
                self, "Bağlantı Yok",
                "Anten home komutu hiçbir worker'a ulaşmıyor "
                "(komut kanalı bağlı değil)."
            )
            return
        self.set_home_from_drone.emit()
        self._set_home_msg("Anten home, anlık İHA GPS konumuna ayarlanması için gönderildi.",
                           "accent")

    def _on_manual_home(self):
        # NEDEN (ölü buton düzeltmesi): Eski kod tüm ValueError'ları `pass` ile
        # yutuyordu. Virgüllü ondalık ya da boş alan giren kullanıcı için buton
        # TAMAMEN ölüydü — ne uyarı ne durum değişikliği. Artık: virgül kabul
        # ediliyor, hatalı alan ADIYLA bildiriliyor ve aralık kontrolü yapılıyor
        # (yanlış home = anten tüm uçuş boyunca yanlış yöne bakar).
        errors: list[str] = []
        bad_widget = None

        try:
            lat = self._parse_coord(self._e_lat.text())
            if not (-90.0 <= lat <= 90.0):
                errors.append("Enlem -90 ile +90 arasında olmalı.")
                bad_widget = bad_widget or self._e_lat
        except ValueError:
            errors.append("Enlem geçerli bir sayı değil (örn. 39.925014 veya 39,925014).")
            bad_widget = bad_widget or self._e_lat
            lat = 0.0

        try:
            lon = self._parse_coord(self._e_lon.text())
            if not (-180.0 <= lon <= 180.0):
                errors.append("Boylam -180 ile +180 arasında olmalı.")
                bad_widget = bad_widget or self._e_lon
        except ValueError:
            errors.append("Boylam geçerli bir sayı değil (örn. 32.836944 veya 32,836944).")
            bad_widget = bad_widget or self._e_lon
            lon = 0.0

        try:
            alt = self._parse_coord(self._e_alt.text(), default=0.0)
            if not (-500.0 <= alt <= 10000.0):
                errors.append("İrtifa -500 m ile +10000 m arasında olmalı.")
                bad_widget = bad_widget or self._e_alt
        except ValueError:
            errors.append("İrtifa geçerli bir sayı değil (boş bırakılırsa 0 kabul edilir).")
            bad_widget = bad_widget or self._e_alt
            alt = 0.0

        if errors:
            self._set_home_msg("Geçersiz giriş — home ayarlanmadı.", "danger")
            QMessageBox.warning(
                self, "Geçersiz Anten Home",
                "Anten home ayarlanamadı:\n\n" + "\n".join(f"  • {e}" for e in errors)
            )
            if bad_widget is not None:
                bad_widget.setFocus(); bad_widget.selectAll()
            return

        if not self._signal_connected(self.set_home_manual):
            self._set_home_msg("Komut kanalı bağlı değil — home ayarlanamadı.", "danger")
            QMessageBox.warning(
                self, "Bağlantı Yok",
                "Anten home komutu hiçbir worker'a ulaşmıyor "
                "(komut kanalı bağlı değil)."
            )
            return

        self.set_home_manual.emit(lat, lon, alt)
        self._set_home_msg(f"Anten home ayarlandı: {lat:.6f}, {lon:.6f} @ {alt:.1f} m",
                           "accent")

    # ── Worker → UI slotları ───────────────────────────────────────────────
    @pyqtSlot(dict)
    def update_status(self, s: dict):
        az = s.get("azimuth", 0.0); el = s.get("elevation", 0.0)
        tracking = bool(s.get("tracking", False))
        self._compass.set_aim(az, el, tracking)
        self._v_az.setText(f"{az:6.1f}°")
        self._v_el.setText(f"{el:6.1f}°")
        self._v_gd.setText(f"{s.get('ground_distance_m', 0):.0f} m")
        self._v_sd.setText(f"{s.get('slant_distance_m', 0):.0f} m")
        self._v_ad.setText(f"{s.get('alt_diff_m', 0):+.0f} m")
        if tracking:
            self._set_state("● TAKİP EDİYOR", "success")
        else:
            self._set_state("● BEKLEMEDE (yakın/hedef yok)", "warning")

    @pyqtSlot(bool)
    def set_tracking_active(self, active: bool):
        """enable/disable sonrası buton/etiket durumunu güncelle."""
        self._active = active
        if active:
            self._btn_toggle.setText("■ Takibi Durdur")
            self._btn_toggle.setObjectName("dangerButton")
        else:
            self._btn_toggle.setText("▶ Takibi Başlat")
            self._btn_toggle.setObjectName("successButton")
            self._set_state("● PASİF", "idle")
        # objectName değişince QSS sınıf stilini yeniden uygula
        _stl = self._btn_toggle.style()
        if _stl is not None:
            _stl.unpolish(self._btn_toggle)
            _stl.polish(self._btn_toggle)

    @pyqtSlot(bool)
    def set_connection(self, connected: bool):
        self._connected = connected   # tema değişiminde aynı durum yeniden boyanır
        if connected:
            self._lbl_conn.setText("Anten Pixhawk: ● bağlı")
            self._lbl_conn.setStyleSheet(f"color:{theme.c('success')};")
        else:
            self._lbl_conn.setText("Anten Pixhawk: bağlı değil")
            self._lbl_conn.setStyleSheet(f"color:{theme.c('text_muted')};")
