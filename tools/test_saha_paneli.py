import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/home/ash/ASHİNA IHA/YKI")
from PyQt6.QtWidgets import QApplication, QMessageBox
app = QApplication.instance() or QApplication([])
import ui.views.saha_testi_view as sv
from ui.views.saha_senaryolari import SENARYOLAR

gonderilen = []
v = sv.SahaTestiView()
v.send_command.connect(lambda c, p: gonderilen.append((c, list(p))))
print("panel kuruldu | senaryo:", len(SENARYOLAR), "| buton:", len(v._senaryo_btn))

# 1) Pervane kapisi KAPALIYKEN senaryo butonlari kapali, ACIL DISARM ACIK
print("1) kapi kapali -> senaryo butonu:", any(b.isEnabled() for b in v._senaryo_btn), "(False bekle)")
print("   ACIL DISARM:", v._btn_disarm.isEnabled(), "(True bekle)")

# 2) Kapi acilinca senaryolar acilir
v._kapi._kutu.setChecked(True)
print("2) kapi acik -> hepsi acik:", all(b.isEnabled() for b in v._senaryo_btn), "(True bekle)")

# 3) ACIL DISARM dogru komutu gonderiyor mu
sv.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
v._acil_disarm()
print("3) disarm komutu:", gonderilen, "(400, param1=0, param2=21196 bekle)")

# 4) Telemetri: ARM gecisi gunluge dusuyor mu
v.update_telemetry({"armed": True, "mode": "GUIDED", "battery_voltage": 23.4,
                    "gps_fix_name": "3D", "gps_sats": 11})
v.update_telemetry({"armed": False, "mode": "LOITER", "battery_voltage": 23.3})
g = v._log.toPlainText()
print("4) ARM gecisi loglandi:", "ARM EDİLDİ" in g and "DISARM edildi" in g)
print("   kip gecisi loglandi:", "GUIDED" in g and "LOITER" in g)
print("   durum tahtasi:", v._d["arm"].text(), "|", v._d["mode"].text(), "|", v._d["batt"].text())

# 5) Saglik ve FC mesaji
v.on_health_alert("battery", "CRITICAL", "Batarya KRİTİK: 20.10V")
v.on_status_text("PreArm: Compass not calibrated", 4)
v.on_connection_changed(False)
v.on_companion_changed(False)
g = v._log.toPlainText()
print("5) saglik/FC/link loglandi:", all(x in g for x in ("SAĞLIK","FC","FC linki KOPTU","Yardımcı bilgisayar KOPTU")))

# 6) Sonuc isaretleme ve rapor
v._isaretle(SENARYOLAR[0]["ad"], "gecti", v._d["arm"])
v._notlar[SENARYOLAR[0]["ad"]] = "motorlar duzgun dondu"
import tempfile, io as _io
yol = tempfile.mktemp(suffix=".md")
sv.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (yol, ""))
v._rapor_kaydet()
r = _io.open(yol, encoding="utf-8").read()
print("6) rapor yazildi:", os.path.exists(yol), "| satir:", len(r.splitlines()))
print("   sonuc tabloda:", "✓ GEÇTİ" in r, "| not tabloda:", "motorlar duzgun dondu" in r)
print("   tum senaryolar tabloda:", all(s["ad"] in r for s in SENARYOLAR))

# 7) Panel terk edilince onay DUSMELI
v.panel_terk_edildi()
print("7) terk sonrasi kapi:", v._kapi.acik(), "(False bekle) | senaryo butonu:",
      any(b.isEnabled() for b in v._senaryo_btn), "(False bekle)")
print("SMOKE OK")
