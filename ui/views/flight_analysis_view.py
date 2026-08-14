import logging
import json
import math
from datetime import datetime
from PyQt6.QtCore import pyqtSlot, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QGridLayout, QFrame, QMessageBox, QTabWidget, QTextEdit, QScrollArea
)

import pyqtgraph as pg

from core import local_storage, theme

logger = logging.getLogger(__name__)

# ── Uçuş grafiği seri renkleri ───────────────────────────────────────────────
# aviyonik semantik — temadan bağımsız. Seri renkleri (hız=yeşil, gerilim=
# kırmızı, roll=mavi, pitch=turuncu, tepe noktası işareti=amber) uçuş verisi
# okuma geleneğidir; tema değişse de aynı kalır.
# Tek koşul: hem koyu (#0f161c) hem beyaz grafik zemininde okunabilmeleri.
# Eski parlak tonlar (hız #00e676 → beyazda 1.7:1, işaret #ffff00 → 1.1:1)
# açık temada görünmez oluyordu; hue korunarak koyulaştırıldı — yeni değerler
# iki zeminde de ≥3:1 (WCAG grafik nesnesi eşiği) kontrast verir.
SERIES_SPEED   = "#009e5c"   # yer hızı  — beyaz 3.5:1 / koyu 5.3:1
SERIES_VOLTAGE = "#f44336"   # gerilim   — beyaz 3.7:1 / koyu 5.0:1
SERIES_ROLL    = "#2196f3"   # roll      — beyaz 3.1:1 / koyu 5.8:1
SERIES_PITCH   = "#d97a00"   # pitch     — beyaz 3.1:1 / koyu 5.9:1
SERIES_MARKER  = "#a88500"   # tepe/dip işareti — beyaz 3.5:1 / koyu 5.2:1

# ── Havacılık Standartları Uyumluluk Kuralları ────────────────────────────────
_COMPLIANCE_RULES = [
    # (alan_adı, min, max, birim, ağırlık, açıklama)
    ("min_voltage",   10.5, 25.2, "V",   2, "Batarya gerilimi alt limiti"),
    ("max_alt",       0,    400,  "m",   2, "Maks. irtifa < 400m (yasal sınır)"),
    ("max_speed",     0,    25,   "m/s", 1, "Maks. hız < 25 m/s"),
    ("max_roll",      0,    45,   "°",   2, "Agresif dönüş (Roll) limiti"),
    ("max_pitch",     0,    45,   "°",   2, "Agresif eğilme (Pitch) limiti"),
]

def _analyze_compliance(params: dict) -> list[dict]:
    results = []
    for field, mn, mx, unit, weight, desc in _COMPLIANCE_RULES:
        val = params.get(field)
        if val is None:
            results.append({
                "field": field, "value": "—", "ok": None,
                "severity": "UNKNOWN", "weight": weight,
                "message": f"{desc} — Veri yok",
            })
            continue

        try:
            fval = float(val)
        except (ValueError, TypeError):
            continue

        ok = mn <= fval <= mx
        deviation_pct = 0.0
        if not ok:
            closest = max(mn, min(fval, mx))
            span = max(mx - mn, 0.001)
            deviation_pct = abs(fval - closest) / span * 100

        severity = "OK" if ok else ("HIGH" if deviation_pct > 20 or weight >= 2 else "MEDIUM")

        results.append({
            "field": field,
            "value": f"{fval:.2f} {unit}".strip(),
            "ok": ok,
            "severity": severity,
            "weight": weight,
            "deviation_pct": deviation_pct,
            "message": desc if ok else f"{desc}: {fval:.2f} {unit} (aralık: {mn}–{mx})",
        })
    return results

def _overall_risk(compliance: list[dict]) -> str:
    high_count = sum(1 for r in compliance if r.get("severity") == "HIGH")
    med_count  = sum(1 for r in compliance if r.get("severity") == "MEDIUM")
    if high_count >= 1:
        return "YÜKSEK"
    if med_count >= 2:
        return "ORTA"
    return "DÜŞÜK"


class FlightAnalysisView(QWidget):
    def __init__(self, db_conn, parent=None):
        super().__init__(parent)
        self._db_conn = db_conn
        # Tema değişince HTML panelleri (renkleri gövdeye gömülü) yeniden
        # üretilebilsin diye son gösterilen kayıt bellekte tutulur.
        self._last_record: dict | None = None
        self._last_points: list = []
        self._last_compliance: dict | None = None
        # Okuma hatası mesajı da tema rengiyle (danger) yazılıyor — tema
        # değişince bayat kalmasın diye metni saklıyoruz.
        self._last_error: str | None = None
        self._build_ui()
        self.refresh_list()
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _build_ui(self):
        main_lay = QHBoxLayout(self)
        main_lay.setContentsMargins(10, 10, 10, 10)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_lay.addWidget(splitter)
        
        # ── Sol Panel (Uçuş Listesi) ──────────────────────────────────────────
        left_panel = QWidget()
        l_lay = QVBoxLayout(left_panel)
        l_lay.setContentsMargins(0,0,0,0)
        
        lbl_title = QLabel("📂 Kayıtlar & Analiz")
        lbl_title.setObjectName("sectionTitle")
        l_lay.addWidget(lbl_title)

        self._list_widget = QListWidget()
        self._list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        l_lay.addWidget(self._list_widget)

        btn_refresh = QPushButton("🔄 Listeyi Yenile")
        btn_refresh.clicked.connect(self.refresh_list)
        btn_refresh.setMinimumHeight(30)   # şartname §3: buton 28-30px
        btn_refresh.setObjectName("ghostButton")
        l_lay.addWidget(btn_refresh)
        
        # ── Sağ Panel (Dashboard) ─────────────────────────────────────────────
        right_panel = QWidget()
        r_lay = QVBoxLayout(right_panel)
        r_lay.setContentsMargins(10,0,0,0)
        
        self._lbl_dash = QLabel("← Soldan bir uçuş kaydı seçin")
        self._lbl_dash.setObjectName("pageTitle")
        r_lay.addWidget(self._lbl_dash)

        # KPI Kartları
        kpi_frame = QFrame()
        kpi_frame.setObjectName("cardFrame")
        kpi_lay = QGridLayout(kpi_frame)
        kpi_lay.setContentsMargins(5,5,5,5)

        def make_kpi(title):
            l_t = QLabel(title)
            l_t.setObjectName("mutedLabel")
            l_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l_v = QLabel("--")
            l_v.setObjectName("valueLabel")
            l_v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            w = QWidget()
            wl = QVBoxLayout(w)
            wl.setContentsMargins(5,5,5,5)
            wl.addWidget(l_t)
            wl.addWidget(l_v)
            return w, l_v

        w1, self._kpi_speed = make_kpi("Maks Hız (m/s)")
        w2, self._kpi_volt = make_kpi("Min Voltaj (V)")
        w3, self._kpi_roll = make_kpi("Maks Roll (°)")
        w4, self._kpi_dur = make_kpi("Süre (sn)")

        kpi_lay.addWidget(w1, 0, 0)
        kpi_lay.addWidget(w2, 0, 1)
        kpi_lay.addWidget(w3, 0, 2)
        kpi_lay.addWidget(w4, 0, 3)
        r_lay.addWidget(kpi_frame)
        
        # Sekmeler
        self._tabs = QTabWidget()
        self._tabs.setFont(QFont("Segoe UI", 10))
        r_lay.addWidget(self._tabs, 1)
        
        # -- Tab 1: 📊 Uçuş Grafikleri --
        tab_charts = QWidget()
        tab_charts_lay = QVBoxLayout(tab_charts)
        
        # pg.setConfigOption GLOBAL'dir ve yalnızca BUNDAN SONRA kurulan
        # grafikleri etkiler; bu yüzden widget'lar oluşturulmadan önce yazılır,
        # tema değişiminde ise _apply_theme_colors hem global ayarı hem mevcut
        # grafiklerin zemin/eksen renklerini tazeler.
        self._apply_pg_config()

        # Grafikler
        self._plot_speed = pg.PlotWidget(title="Yer Hızı (m/s) vs Zaman (s)")
        self._plot_speed.showGrid(x=True, y=True, alpha=0.3)

        self._plot_volt = pg.PlotWidget(title="Batarya Gerilimi (V) vs Zaman (s)")
        self._plot_volt.showGrid(x=True, y=True, alpha=0.3)

        self._plot_att = pg.PlotWidget(title="Yatma Açıları (Roll/Pitch) vs Zaman (s)")
        self._plot_att.showGrid(x=True, y=True, alpha=0.3)
        self._plot_att.addLegend()

        # Başlıklarını da tazeleyebilmek için grafik → başlık eşlemesi.
        self._plots = (
            (self._plot_speed, "Yer Hızı (m/s) vs Zaman (s)"),
            (self._plot_volt,  "Batarya Gerilimi (V) vs Zaman (s)"),
            (self._plot_att,   "Yatma Açıları (Roll/Pitch) vs Zaman (s)"),
        )
        self._style_plots()

        tab_charts_lay.addWidget(self._plot_speed)
        tab_charts_lay.addWidget(self._plot_volt)
        tab_charts_lay.addWidget(self._plot_att)
        
        self._tabs.addTab(tab_charts, "📊 Uçuş Grafikleri")
        
        # -- Tab 2: 🤖 Akıllı Gösterge & Motor Analizi --
        tab_ai = QWidget()
        tab_ai_lay = QVBoxLayout(tab_ai)
        
        # Metinsel Analiz ve Gösterge Kutusu
        self._ai_analysis_text = QTextEdit()
        self._ai_analysis_text.setReadOnly(True)
        tab_ai_lay.addWidget(self._ai_analysis_text)
        
        self._tabs.addTab(tab_ai, "🤖 Akıllı Gösterge & Motor Analizi")
        
        # -- Tab 3: Uyumluluk Analizi --
        self._compliance_w = QTextEdit()
        self._compliance_w.setReadOnly(True)
        self._compliance_w.setFont(QFont("Consolas", 9))   # mono ~12px (şartname §3)
        self._tabs.addTab(self._compliance_w, "⚖ Uyumluluk Analizi")

        # -- Tab 4: Ham Veri --
        self._raw_text = QTextEdit()
        self._raw_text.setReadOnly(True)
        self._raw_text.setFont(QFont("Consolas", 9))   # mono ~12px (şartname §3)
        self._tabs.addTab(self._raw_text, "📄 Ham Veri")
        
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 700])

    # ── Tema ──────────────────────────────────────────────────────────────────
    def _apply_pg_config(self):
        """pyqtgraph'ın GLOBAL zemin/ön plan ayarını aktif temadan yazar."""
        pg.setConfigOption('background', theme.c("chart_bg"))
        pg.setConfigOption('foreground', theme.c("chart_fg"))

    def _style_plots(self):
        """Var olan grafiklerin zemin/eksen/başlık renklerini tazeler.

        NEDEN: setConfigOption yalnızca SONRADAN kurulan grafikleri etkiler;
        çalışan uygulamada tema değişince mevcut PlotWidget'lar eski renkte
        kalır (açık temada beyaz zemine açık gri eksen = okunmaz)."""
        fg = theme.c("chart_fg")
        bg = theme.c("chart_bg")
        for plot, title in getattr(self, "_plots", ()):
            plot.setBackground(bg)
            plot_item = plot.getPlotItem()
            for axis_name in ("left", "bottom", "top", "right"):
                axis = plot_item.getAxis(axis_name)
                if axis is None:
                    continue
                axis.setPen(fg)
                axis.setTextPen(fg)
            plot_item.setTitle(title, color=fg)
            if plot_item.legend is not None:
                plot_item.legend.setLabelTextColor(fg)

    def _apply_theme_colors(self):
        """Tema değişince inline yazılmış tüm renkleri yeniden üretir.

        Grafikler (global pg ayarı + mevcut widget'lar) ve renkleri HTML
        gövdesine gömülü olan Akıllı Gösterge / Uyumluluk sekmeleri son
        gösterilen kayıttan yeniden çizilir. Seri renkleri aviyonik semantik
        olduğu için sabit kalır, yeniden çizim gerekmez."""
        self._apply_pg_config()
        self._style_plots()
        if self._last_error is not None:
            # Okuma hatası ekranda duruyor — mesaj yeni temanın danger rengiyle
            # yeniden yazılır (koyu tema kırmızısı beyaz zeminde solgun kalıyordu).
            self._show_read_error(self._last_error)
        elif self._last_record is not None:
            self._generate_ai_analysis(self._last_record, self._last_points)
        if self._last_compliance is not None:
            self._show_compliance(self._last_compliance)

    def _show_read_error(self, message: str) -> None:
        """Veri okuma hatasını aktif temanın danger rengiyle gösterir."""
        self._ai_analysis_text.setHtml(
            f"<b style='color:{theme.c('danger')};'>{message}</b>"
        )

    def refresh_list(self):
        self._list_widget.clear()
        try:
            records = local_storage.list_flight_analyses(self._db_conn)
            for r in records:
                dt = datetime.fromtimestamp(r["ts_start"]).strftime("%Y-%m-%d %H:%M:%S")
                dur = r["ts_end"] - r["ts_start"]
                text = f"✈ [{dt}] {r['mission_type']} ({dur}s)\n    Maks Hız: {r['max_speed']:.1f} m/s"
                
                item = QListWidgetItem(text)
                item.setData(100, dict(r))
                self._list_widget.addItem(item)
        except Exception as e:
            logger.error(f"Uçuş kayıtları listelenirken hata: {e}")

    @pyqtSlot()
    def _on_selection_changed(self):
        items = self._list_widget.selectedItems()
        if not items:
            return
            
        data = items[0].data(100)
        dur = data['ts_end'] - data['ts_start']
        
        dt = datetime.fromtimestamp(data["ts_start"]).strftime("%Y-%m-%d %H:%M:%S")
        self._lbl_dash.setText(f"✈ UÇUŞ — {data['mission_type']} — {dt}")
        
        self._kpi_speed.setText(f"{data['max_speed']:.1f}")
        self._kpi_volt.setText(f"{data['min_voltage']:.1f}")
        self._kpi_roll.setText(f"{data['max_roll']:.1f}")
        self._kpi_dur.setText(f"{dur}")
        
        json_path = data["json_path"]
        points = []
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                points = json.load(f)
                
            times = [p["ts"] for p in points]
            speeds = [p["spd"] for p in points]
            volts = [p["volt"] for p in points]
            rolls = [p["roll"] for p in points]
            pitches = [p["pitch"] for p in points]
            
            # -- Grafikleri Çiz --
            self._plot_speed.clear()
            self._plot_speed.plot(times, speeds, pen=pg.mkPen(SERIES_SPEED, width=2))
            if speeds:
                max_spd = max(speeds)
                max_spd_idx = speeds.index(max_spd)
                self._plot_speed.plot([times[max_spd_idx]], [max_spd], symbol='star', symbolSize=15, symbolBrush=SERIES_MARKER, pen=None, name="Maks Hız")

            self._plot_volt.clear()
            self._plot_volt.plot(times, volts, pen=pg.mkPen(SERIES_VOLTAGE, width=2))
            valid_volts = [v for v in volts if v > 0]
            if valid_volts:
                min_v = min(valid_volts)
                min_v_idx = volts.index(min_v)
                self._plot_volt.plot([times[min_v_idx]], [min_v], symbol='t1', symbolSize=15, symbolBrush=SERIES_MARKER, pen=None, name="Min Voltaj")

            self._plot_att.clear()
            self._plot_att.plot(times, rolls, pen=pg.mkPen(SERIES_ROLL, width=2), name="Roll")
            self._plot_att.plot(times, pitches, pen=pg.mkPen(SERIES_PITCH, width=2), name="Pitch")

            if rolls and pitches:
                max_r = max(rolls, key=abs)
                max_r_idx = rolls.index(max_r)
                self._plot_att.plot([times[max_r_idx]], [max_r], symbol='x', symbolSize=12, symbolPen=pg.mkPen(SERIES_MARKER, width=3), pen=None, name="Maks Roll")

                max_p = max(pitches, key=abs)
                max_p_idx = pitches.index(max_p)
                self._plot_att.plot([times[max_p_idx]], [max_p], symbol='+', symbolSize=12, symbolPen=pg.mkPen(SERIES_MARKER, width=3), pen=None, name="Maks Pitch")

            # -- Algoritmik Metinsel Yorum Üretimi --
            self._generate_ai_analysis(data, points)
            # Tema değişince aynı HTML yeniden üretilebilsin diye sakla.
            self._last_record = data
            self._last_points = points
            self._last_error = None

            # -- Ham Veri --
            pretty = json.dumps(points, indent=2, ensure_ascii=False)
            self._raw_text.setPlainText(pretty)
            
        except Exception as e:
            logger.error(f"Grafik verisi okunamadı: {e}")
            self._last_error = f"Hata: Veri dosyası okunamadı: {e}"
            self._show_read_error(self._last_error)
            self._raw_text.setPlainText("Veri Yok")
            # Okuma başarısız — tema değişiminde hata metninin üzerine
            # "Yeterli veri yok" yazılmasın diye önbellek temizlenir.
            self._last_record = None
            self._last_points = []

        # -- Uyumluluk --
        # İrtifa bilgisini bul
        max_alt = 0
        if points:
            max_alt = max([p.get("alt", 0) for p in points])
            
        comp_data = {
            "min_voltage": data["min_voltage"],
            "max_speed": data["max_speed"],
            "max_roll": data["max_roll"],
            "max_pitch": data["max_pitch"],
            "max_alt": max_alt
        }
        self._last_compliance = comp_data
        self._show_compliance(comp_data)

    def _generate_ai_analysis(self, db_record, points):
        if not points:
            self._ai_analysis_text.setHtml("<b>Yeterli veri yok.</b>")
            return
            
        max_spd = db_record.get("max_speed", 0)
        min_v = db_record.get("min_voltage", 0)
        max_r = db_record.get("max_roll", 0)
        max_p = db_record.get("max_pitch", 0)
        
        # Titreşim, PI Hata ve ESC verilerini hesapla
        max_vib = 0.0
        max_pid_err = 0.0
        max_esc_rpm = [0, 0, 0, 0]
        max_esc_temp = [0, 0, 0, 0]
        
        for p in points:
            # Vektörel titreşim büyüklüğü
            v_mag = math.sqrt(p.get("vib_x",0)**2 + p.get("vib_y",0)**2 + p.get("vib_z",0)**2)
            if v_mag > max_vib: max_vib = v_mag
            if abs(p.get("pid_err",0)) > max_pid_err: max_pid_err = abs(p.get("pid_err",0))
            
            # ESC RPM & Temp (Eğer 0 gelmiyorsa)
            esc_rpm = p.get("esc_rpm", [0, 0, 0, 0])
            esc_temp = p.get("esc_temp", [0, 0, 0, 0])
            for i in range(4):
                if i < len(esc_rpm) and esc_rpm[i] > max_esc_rpm[i]:
                    max_esc_rpm[i] = esc_rpm[i]
                if i < len(esc_temp) and esc_temp[i] > max_esc_temp[i]:
                    max_esc_temp[i] = esc_temp[i]
        
        # İlk geçerli voltaj (başlangıç voltajı)
        start_v = next((p["volt"] for p in points if p["volt"] > 0), min_v)
        sag = start_v - min_v if start_v > 0 else 0
        
        # -- Yüzdelik Hesaplamalar --
        # Gösterge renkleri temadan gelir; tema değişince _apply_theme_colors
        # bu HTML'i baştan üretir.
        c_ok, c_warn, c_bad = theme.c("success"), theme.c("warning"), theme.c("danger")
        c_text, c_muted = theme.c("text"), theme.c("text_muted")
        c_track, c_border, c_accent = theme.c("panel_alt"), theme.c("border"), theme.c("accent")

        spd_pct = min(100, (max_spd / 25.0) * 100)
        spd_col = c_ok if spd_pct < 40 else (c_warn if spd_pct < 75 else c_bad)

        sag_pct = min(100, (sag / 2.0) * 100)
        sag_col = c_ok if sag_pct < 30 else (c_warn if sag_pct < 70 else c_bad)

        max_angle = max(max_r, max_p)
        ang_pct = min(100, (max_angle / 45.0) * 100)
        ang_col = c_ok if ang_pct < 40 else (c_warn if ang_pct < 80 else c_bad)

        vib_pct = min(100, (max_vib / 60.0) * 100)
        vib_col = c_ok if vib_pct < 30 else (c_warn if vib_pct < 70 else c_bad)

        pid_pct = min(100, (max_pid_err / 10.0) * 100)
        pid_col = c_ok if pid_pct < 25 else (c_warn if pid_pct < 60 else c_bad)

        # HTML Tablo Şablonu
        def make_bar(label, pct, col, val_text):
            return f"""
            <tr>
              <td width='25%' style='padding: 4px; color:{c_text};'><b>{label}</b></td>
              <td width='55%' style='padding: 4px;'>
                <div style='background-color: {c_track}; width: 100%; height: 14px; border-radius: 3px; border: 1px solid {c_border};'>
                   <div style='background-color: {col}; width: {pct}%; height: 14px; border-radius: 2px;'></div>
                </div>
              </td>
              <td width='20%' align='right' style='padding: 4px; color:{col}; font-weight:bold;'>{val_text}</td>
            </tr>
            """

        html = [f"<h3 style='color:{c_accent}; margin-top:0px; margin-bottom: 8px;'>📊 Akıllı Performans Göstergeleri</h3>"]
        html.append("<table width='100%' cellpadding='0' cellspacing='0' style='font-size: 13px;'>")
        
        html.append(make_bar("Hız Stresi", spd_pct, spd_col, f"{max_spd:.1f} m/s"))
        html.append(make_bar("Batarya Yorgunluğu", sag_pct, sag_col, f"{sag:.1f} V Düşüş"))
        html.append(make_bar("Denge & Agresiflik", ang_pct, ang_col, f"{max_angle:.1f}°"))
        html.append(make_bar("Şasi Titreşimi", vib_pct, vib_col, f"{max_vib:.1f} m/s²"))
        html.append(make_bar("PI (PID) Hatası", pid_pct, pid_col, f"{max_pid_err:.1f}°"))
        html.append("</table>")
        
        # -- Motor Analizi --
        html.append(f"<h3 style='color:{c_accent}; margin-top:16px; margin-bottom: 8px;'>⚙ Motor &amp; ESC Yük Analizi</h3>")
        if max(max_esc_rpm) == 0:
            html.append(f"<p style='color:{c_muted}; font-size:12px;'><i>Telemetri bağlantısı yok veya motorlar çalışmadı (RPM=0).</i></p>")
        else:
            html.append("<table width='100%' cellpadding='0' cellspacing='0' style='font-size: 12px;'>")
            for i in range(4):
                rpm = max_esc_rpm[i]
                temp = max_esc_temp[i]
                rpm_pct = min(100, (rpm / 10000.0) * 100) # Max 10.000 RPM varsayımı
                col = c_ok if rpm_pct < 60 else (c_warn if rpm_pct < 85 else c_bad)
                html.append(make_bar(f"Motor {i+1} RPM", rpm_pct, col, f"{rpm} RPM ({temp}°C)"))
            html.append("</table>")

        html.append(f"<hr style='border: 1px solid {c_border}; margin-top: 16px; margin-bottom: 12px;'>")
        
        # Genel Yorum Metni
        summary = []
        if max_spd > 12:
            summary.append("Araç agresif yüksek hız sınırlarında kullanılmış.")
        elif max_spd > 5:
            summary.append("Dinamik ve sportif bir görev profili izlenmiş.")
        else:
            summary.append("Sakin ve yavaş bir uçuş profili izlenmiş.")
            
        if sag_pct > 70:
            summary.append(f"<span style='color:{c_bad};'>Batarya çöküşü çok yüksek, güç sistemi yetersiz kalıyor olabilir!</span>")
        elif sag_pct > 30:
            summary.append("Voltajda normal seviyede dalgalanmalar (sag) mevcut.")

        if ang_pct > 80:
            summary.append(f"<span style='color:{c_bad};'>Tehlikeli yatma açılarına ulaşılmış, devrilme veya irtifa kaybı riski yüksek.</span>")

        if vib_pct > 70:
            summary.append(f"<span style='color:{c_warn};'>Mekanik titreşim fazla; pervanelerde balans veya şaside gevşeklik olabilir.</span>")

        if max(max_esc_rpm) > 0:
            heaviest_motor_idx = max_esc_rpm.index(max(max_esc_rpm))
            highest_rpm = max_esc_rpm[heaviest_motor_idx]
            avg_rpm = sum(max_esc_rpm) / 4.0
            if highest_rpm > avg_rpm * 1.2:  # Eğer ortalamanın %20 üstündeyse
                summary.append(f"<span style='color:{c_warn};'><br><br><b>Motor Dengesizliği:</b> Motor {heaviest_motor_idx + 1} diğerlerine göre daha çok zorlanmış ({highest_rpm} RPM). Ağırlık merkeziniz bu motora doğru kaymış veya sürekli rüzgar direncine maruz kalmış olabilir.</span>")
            else:
                summary.append("<br><br><b>Motor Dağılımı:</b> Motor yükleri (RPM) genel olarak dengeli dağılmış, şasi ağırlık merkezi sağlıklı.")

        html.append(f"<p style='color:{c_text}; font-size: 13px; line-height: 1.6;'><b>🤖 Genel Durum Özeti:</b> {' '.join(summary)}</p>")
        
        self._ai_analysis_text.setHtml("".join(html))

    def _show_compliance(self, params: dict):
        results = _analyze_compliance(params)
        risk = _overall_risk(results)

        # Risk rozeti renkleri temadan; tema değişince _apply_theme_colors
        # bu HTML'i baştan üretir.
        c_ok, c_warn, c_bad = theme.c("success"), theme.c("warning"), theme.c("danger")
        risk_color = {
            "DÜŞÜK":  c_ok,
            "ORTA":   c_warn,
            "YÜKSEK": c_bad,
        }.get(risk, theme.c("text_muted"))
        risk_bg = {
            "DÜŞÜK":  theme.c("success_bg"),
            "ORTA":   theme.c("warning_bg"),
            "YÜKSEK": theme.c("danger_bg"),
        }.get(risk, theme.c("panel_alt"))

        lines = [
            f"<div style='background:{risk_bg}; color:{risk_color}; padding: 10px; border: 1px solid {risk_color}; border-radius: 4px; font-size:14px; font-weight:bold; margin-bottom: 15px;'>",
            f"Risk Seviyesi: {risk}",
            f"</div>",
            f"<h4 style='color:{theme.c('text')};'>HAVACILIK STANDARTLARI VE LİMİT KONTROLLERİ</h4>",
            "<ul>"
        ]

        for r in results:
            icon = "✅" if r["ok"] else ("❌" if r["ok"] is False else "❔")
            sev  = r.get("severity", "")
            sev_tag = f"<b style='color:{c_bad};'>[{sev}]</b>" if sev == "HIGH" else (f"<b style='color:{c_warn};'>[{sev}]</b>" if sev == "MEDIUM" else "")
            color = c_ok if r["ok"] else c_bad
            lines.append(f"<li style='margin-bottom: 4px;'><span style='color:{color}'>{icon} {r['message']}</span> {sev_tag}</li>")

        lines += [
            "</ul>",
            "<hr>",
            f"<p style='color:{theme.c('text_muted')}; font-size: 11px;'>",
            "<b>UYARI EŞİK DEĞERLERİ:</b><br>",
            "• Batarya alt limit: 10.5V (3S için kritik)<br>",
            "• Maks irtifa < 400m (SHGM limitleri)<br>",
            "• Yatma (Roll/Pitch) < 45° (Sportif güvenli sınır)",
            "</p>"
        ]
        self._compliance_w.setHtml("".join(lines))
