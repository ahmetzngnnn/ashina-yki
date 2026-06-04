import logging
import json
import math
from datetime import datetime
from PyQt6.QtCore import pyqtSlot, Qt
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QGridLayout, QFrame, QMessageBox, QTabWidget, QTextEdit, QScrollArea
)

import pyqtgraph as pg

from core import local_storage

logger = logging.getLogger(__name__)

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
        self._build_ui()
        self.refresh_list()

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
        lbl_title.setStyleSheet("color:#00e5ff;font-size:14px;font-weight:bold;")
        l_lay.addWidget(lbl_title)
        
        self._list_widget = QListWidget()
        self._list_widget.setStyleSheet(
            "QListWidget{color:#ccc;border:1px solid #0f3460;font-size:12px;}"
            "QListWidget::item{padding:8px 4px;border-bottom:1px solid #1a1a3e;}"
            "QListWidget::item:selected{color:#fff;background-color:#0f3460;}"
        )
        self._list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        l_lay.addWidget(self._list_widget)
        
        btn_refresh = QPushButton("🔄 Listeyi Yenile")
        btn_refresh.clicked.connect(self.refresh_list)
        btn_refresh.setMinimumHeight(40)
        btn_refresh.setStyleSheet("QPushButton{color:#00e5ff;border:1px solid #00e5ff; border-radius: 4px; font-weight: bold;} QPushButton:hover{background-color:#00e5ff; color:black;}")
        l_lay.addWidget(btn_refresh)
        
        # ── Sağ Panel (Dashboard) ─────────────────────────────────────────────
        right_panel = QWidget()
        r_lay = QVBoxLayout(right_panel)
        r_lay.setContentsMargins(10,0,0,0)
        
        self._lbl_dash = QLabel("← Soldan bir uçuş kaydı seçin")
        self._lbl_dash.setStyleSheet("color:#00e5ff;font-size:15px;font-weight:bold;")
        r_lay.addWidget(self._lbl_dash)
        
        # KPI Kartları
        kpi_frame = QFrame()
        kpi_frame.setStyleSheet("QFrame{border:1px solid #0f3460;border-radius:6px; background-color:#161616;}")
        kpi_lay = QGridLayout(kpi_frame)
        kpi_lay.setContentsMargins(5,5,5,5)
        
        def make_kpi(title):
            l_t = QLabel(title)
            l_t.setStyleSheet("color: #888; font-size: 11px; border:none; background:transparent;")
            l_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l_v = QLabel("--")
            l_v.setStyleSheet("color: #00e5ff; font-size: 18px; font-weight: bold; border:none; background:transparent;")
            l_v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            w = QWidget()
            w.setStyleSheet("border:none; background:transparent;")
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
        self._tabs.setStyleSheet(
            "QTabWidget::pane{border:1px solid #0f3460;}"
            "QTabBar::tab{color:#aaa;padding:8px 16px;font-size:12px;}"
            "QTabBar::tab:selected{color:#00e5ff;font-weight:bold;}"
        )
        r_lay.addWidget(self._tabs, 1)
        
        # -- Tab 1: 📊 Uçuş Grafikleri --
        tab_charts = QWidget()
        tab_charts_lay = QVBoxLayout(tab_charts)
        
        pg.setConfigOption('background', '#121212')
        pg.setConfigOption('foreground', '#d4d4d4')
        
        # Grafikler
        self._plot_speed = pg.PlotWidget(title="Yer Hızı (m/s) vs Zaman (s)")
        self._plot_speed.showGrid(x=True, y=True, alpha=0.3)
        
        self._plot_volt = pg.PlotWidget(title="Batarya Gerilimi (V) vs Zaman (s)")
        self._plot_volt.showGrid(x=True, y=True, alpha=0.3)
        
        self._plot_att = pg.PlotWidget(title="Yatma Açıları (Roll/Pitch) vs Zaman (s)")
        self._plot_att.showGrid(x=True, y=True, alpha=0.3)
        self._plot_att.addLegend()
        
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
        self._ai_analysis_text.setStyleSheet(
            "QTextEdit{background-color:#0d1117; color:#c9d1d9; font-size:13px; "
            "border:1px solid #30363d; border-radius:6px; padding:12px; line-height:1.5;}"
        )
        tab_ai_lay.addWidget(self._ai_analysis_text)
        
        self._tabs.addTab(tab_ai, "🤖 Akıllı Gösterge & Motor Analizi")
        
        # -- Tab 3: Uyumluluk Analizi --
        self._compliance_w = QTextEdit()
        self._compliance_w.setReadOnly(True)
        self._compliance_w.setStyleSheet(
            "QTextEdit{color:#ddd;font-family:'Consolas','Courier New'; font-size:12px; border:none;}"
        )
        self._tabs.addTab(self._compliance_w, "⚖ Uyumluluk Analizi")
        
        # -- Tab 4: Ham Veri --
        self._raw_text = QTextEdit()
        self._raw_text.setReadOnly(True)
        self._raw_text.setStyleSheet(
            "QTextEdit{color:#aaa;font-family:'Consolas','Courier New'; font-size:11px; border:none;}"
        )
        self._tabs.addTab(self._raw_text, "📄 Ham Veri")
        
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 700])

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
            self._plot_speed.plot(times, speeds, pen=pg.mkPen('#00e676', width=2))
            if speeds:
                max_spd = max(speeds)
                max_spd_idx = speeds.index(max_spd)
                self._plot_speed.plot([times[max_spd_idx]], [max_spd], symbol='star', symbolSize=15, symbolBrush='#ffff00', pen=None, name="Maks Hız")
            
            self._plot_volt.clear()
            self._plot_volt.plot(times, volts, pen=pg.mkPen('#f44336', width=2))
            valid_volts = [v for v in volts if v > 0]
            if valid_volts:
                min_v = min(valid_volts)
                min_v_idx = volts.index(min_v)
                self._plot_volt.plot([times[min_v_idx]], [min_v], symbol='t1', symbolSize=15, symbolBrush='#ffff00', pen=None, name="Min Voltaj")
            
            self._plot_att.clear()
            self._plot_att.plot(times, rolls, pen=pg.mkPen('#2196f3', width=2), name="Roll")
            self._plot_att.plot(times, pitches, pen=pg.mkPen('#ff9800', width=2), name="Pitch")
            
            if rolls and pitches:
                max_r = max(rolls, key=abs)
                max_r_idx = rolls.index(max_r)
                self._plot_att.plot([times[max_r_idx]], [max_r], symbol='x', symbolSize=12, symbolPen=pg.mkPen('#ffeb3b', width=3), pen=None, name="Maks Roll")
                
                max_p = max(pitches, key=abs)
                max_p_idx = pitches.index(max_p)
                self._plot_att.plot([times[max_p_idx]], [max_p], symbol='+', symbolSize=12, symbolPen=pg.mkPen('#ffeb3b', width=3), pen=None, name="Maks Pitch")
            
            # -- Algoritmik Metinsel Yorum Üretimi --
            self._generate_ai_analysis(data, points)
            
            # -- Ham Veri --
            pretty = json.dumps(points, indent=2, ensure_ascii=False)
            self._raw_text.setPlainText(pretty)
            
        except Exception as e:
            logger.error(f"Grafik verisi okunamadı: {e}")
            self._ai_analysis_text.setHtml(f"<b style='color:red;'>Hata: Veri dosyası okunamadı: {e}</b>")
            self._raw_text.setPlainText("Veri Yok")
            
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
        spd_pct = min(100, (max_spd / 25.0) * 100)
        spd_col = "#2ea043" if spd_pct < 40 else ("#d29922" if spd_pct < 75 else "#f85149")
        
        sag_pct = min(100, (sag / 2.0) * 100)
        sag_col = "#2ea043" if sag_pct < 30 else ("#d29922" if sag_pct < 70 else "#f85149")
        
        max_angle = max(max_r, max_p)
        ang_pct = min(100, (max_angle / 45.0) * 100)
        ang_col = "#2ea043" if ang_pct < 40 else ("#d29922" if ang_pct < 80 else "#f85149")
        
        vib_pct = min(100, (max_vib / 60.0) * 100)
        vib_col = "#2ea043" if vib_pct < 30 else ("#d29922" if vib_pct < 70 else "#f85149")
        
        pid_pct = min(100, (max_pid_err / 10.0) * 100)
        pid_col = "#2ea043" if pid_pct < 25 else ("#d29922" if pid_pct < 60 else "#f85149")
        
        # HTML Tablo Şablonu
        def make_bar(label, pct, col, val_text):
            return f"""
            <tr>
              <td width='25%' style='padding: 4px; color:#c9d1d9;'><b>{label}</b></td>
              <td width='55%' style='padding: 4px;'>
                <div style='background-color: #21262d; width: 100%; height: 14px; border-radius: 3px; border: 1px solid #30363d;'>
                   <div style='background-color: {col}; width: {pct}%; height: 14px; border-radius: 2px;'></div>
                </div>
              </td>
              <td width='20%' align='right' style='padding: 4px; color:{col}; font-weight:bold;'>{val_text}</td>
            </tr>
            """
            
        html = ["<h3 style='color:#00e5ff; margin-top:0px; margin-bottom: 8px;'>📊 Akıllı Performans Göstergeleri</h3>"]
        html.append("<table width='100%' cellpadding='0' cellspacing='0' style='font-size: 13px;'>")
        
        html.append(make_bar("Hız Stresi", spd_pct, spd_col, f"{max_spd:.1f} m/s"))
        html.append(make_bar("Batarya Yorgunluğu", sag_pct, sag_col, f"{sag:.1f} V Düşüş"))
        html.append(make_bar("Denge & Agresiflik", ang_pct, ang_col, f"{max_angle:.1f}°"))
        html.append(make_bar("Şasi Titreşimi", vib_pct, vib_col, f"{max_vib:.1f} m/s²"))
        html.append(make_bar("PI (PID) Hatası", pid_pct, pid_col, f"{max_pid_err:.1f}°"))
        html.append("</table>")
        
        # -- Motor Analizi --
        html.append("<h3 style='color:#00e5ff; margin-top:16px; margin-bottom: 8px;'>⚙ Motor & ESC Yük Analizi</h3>")
        if max(max_esc_rpm) == 0:
            html.append("<p style='color:#888; font-size:12px;'><i>Telemetri bağlantısı yok veya motorlar çalışmadı (RPM=0).</i></p>")
        else:
            html.append("<table width='100%' cellpadding='0' cellspacing='0' style='font-size: 12px;'>")
            for i in range(4):
                rpm = max_esc_rpm[i]
                temp = max_esc_temp[i]
                rpm_pct = min(100, (rpm / 10000.0) * 100) # Max 10.000 RPM varsayımı
                col = "#2ea043" if rpm_pct < 60 else ("#d29922" if rpm_pct < 85 else "#f85149")
                html.append(make_bar(f"Motor {i+1} RPM", rpm_pct, col, f"{rpm} RPM ({temp}°C)"))
            html.append("</table>")
            
        html.append("<hr style='border: 1px solid #30363d; margin-top: 16px; margin-bottom: 12px;'>")
        
        # Genel Yorum Metni
        summary = []
        if max_spd > 12:
            summary.append("Araç agresif yüksek hız sınırlarında kullanılmış.")
        elif max_spd > 5:
            summary.append("Dinamik ve sportif bir görev profili izlenmiş.")
        else:
            summary.append("Sakin ve yavaş bir uçuş profili izlenmiş.")
            
        if sag_pct > 70:
            summary.append("<span style='color:#f85149;'>Batarya çöküşü çok yüksek, güç sistemi yetersiz kalıyor olabilir!</span>")
        elif sag_pct > 30:
            summary.append("Voltajda normal seviyede dalgalanmalar (sag) mevcut.")
            
        if ang_pct > 80:
            summary.append("<span style='color:#f85149;'>Tehlikeli yatma açılarına ulaşılmış, devrilme veya irtifa kaybı riski yüksek.</span>")
            
        if vib_pct > 70:
            summary.append("<span style='color:#d29922;'>Mekanik titreşim fazla; pervanelerde balans veya şaside gevşeklik olabilir.</span>")
            
        if max(max_esc_rpm) > 0:
            heaviest_motor_idx = max_esc_rpm.index(max(max_esc_rpm))
            highest_rpm = max_esc_rpm[heaviest_motor_idx]
            avg_rpm = sum(max_esc_rpm) / 4.0
            if highest_rpm > avg_rpm * 1.2:  # Eğer ortalamanın %20 üstündeyse
                summary.append(f"<span style='color:#d29922;'><br><br><b>Motor Dengesizliği:</b> Motor {heaviest_motor_idx + 1} diğerlerine göre daha çok zorlanmış ({highest_rpm} RPM). Ağırlık merkeziniz bu motora doğru kaymış veya sürekli rüzgar direncine maruz kalmış olabilir.</span>")
            else:
                summary.append("<br><br><b>Motor Dağılımı:</b> Motor yükleri (RPM) genel olarak dengeli dağılmış, şasi ağırlık merkezi sağlıklı.")
            
        html.append(f"<p style='color:#c9d1d9; font-size: 13px; line-height: 1.6;'><b>🤖 Genel Durum Özeti:</b> {' '.join(summary)}</p>")
        
        self._ai_analysis_text.setHtml("".join(html))

    def _show_compliance(self, params: dict):
        results = _analyze_compliance(params)
        risk = _overall_risk(results)

        risk_color = {
            "DÜŞÜK":  "#4ade80",
            "ORTA":   "#f0a500",
            "YÜKSEK": "#ff4444",
        }.get(risk, "#aaa")
        risk_bg = {
            "DÜŞÜK":  "#0a2a0a",
            "ORTA":   "#2a2a0a",
            "YÜKSEK": "#2a0a0a",
        }.get(risk, "#111122")

        lines = [
            f"<div style='background:{risk_bg}; color:{risk_color}; padding: 10px; border: 1px solid {risk_color}; border-radius: 4px; font-size:14px; font-weight:bold; margin-bottom: 15px;'>",
            f"Risk Seviyesi: {risk}",
            f"</div>",
            f"<h4 style='color:#ccc;'>HAVACILIK STANDARTLARI VE LİMİT KONTROLLERİ</h4>",
            "<ul>"
        ]
        
        for r in results:
            icon = "✅" if r["ok"] else ("❌" if r["ok"] is False else "❔")
            sev  = r.get("severity", "")
            sev_tag = f"<b style='color:#ff4444;'>[{sev}]</b>" if sev == "HIGH" else (f"<b style='color:#f0a500;'>[{sev}]</b>" if sev == "MEDIUM" else "")
            color = "#4ade80" if r["ok"] else "#ff4444"
            lines.append(f"<li style='margin-bottom: 4px;'><span style='color:{color}'>{icon} {r['message']}</span> {sev_tag}</li>")

        lines += [
            "</ul>",
            "<hr>",
            "<p style='color:#888; font-size: 11px;'>",
            "<b>UYARI EŞİK DEĞERLERİ:</b><br>",
            "• Batarya alt limit: 10.5V (3S için kritik)<br>",
            "• Maks irtifa < 400m (SHGM limitleri)<br>",
            "• Yatma (Roll/Pitch) < 45° (Sportif güvenli sınır)",
            "</p>"
        ]
        self._compliance_w.setHtml("".join(lines))
