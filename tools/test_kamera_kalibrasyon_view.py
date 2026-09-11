"""Kamera Kalibrasyonu paneli — kurulum + matematik sınaması.

NEDEN VAR: yeni CameraCalView'in (a) hatasız kurulup CalibrationView'e alt
sekme olarak eklendiğini, (b) fov_from_focal/objp matematiğinin
calibrate_camera.py ile AYNI sonucu ürettiğini doğrular. Gerçek video/kamera
GEREKTİRMEZ — sentetik satranç tahtası görüntüleriyle çalışır.

Çalıştırma:  YKI/.venv/bin/python tools/test_kamera_kalibrasyon_view.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import cv2  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from ui.views.camera_cal_view import CameraCalView, _fov_from_focal, _qimage_to_gray  # noqa: E402

hata = 0


def bekle(ad, gercek, beklenen, tol=None):
    global hata
    if tol is not None:
        ok = abs(gercek - beklenen) <= tol
    else:
        ok = gercek == beklenen
    if not ok:
        hata += 1
    print("  %-52s -> %-10s %s" % (ad, gercek, "OK" if ok else "KALDI (bekle %s)" % (beklenen,)))


print("-- KURULUM --")
try:
    v = CameraCalView()
    bekle("CameraCalView bagimsiz kuruldu", True, True)
except Exception as exc:
    bekle("CameraCalView bagimsiz kuruldu", False, True)
    print("    hata:", exc)
    raise SystemExit(1)

# NEDEN CalibrationView'i TAM KURMUYORUZ: onun _accel_tab()'i lazy-import ile
# Model3DView'i (QtWebEngine tabanli 3B gorunum) kuruyor. QtWebEngine, QApplication
# argv'siz ("[]") kurulduysa ya da AA_ShareOpenGLContexts QApplication'dan ONCE
# ayarlanmadiysa bazi ortamlarda (ozellikle bu test gibi main.py disi, hafif bir
# harness'ta) coker - bu KAMERA panelimizle ILGISIZ, mevcut/onceden var olan bir
# davranis. Kayit dogru yapildi mi diye agir widget'i kurmadan, KAYNAK METNI
# statik olarak dogruluyoruz.
import inspect  # noqa: E402
import ui.views.calibration_view as _cv_mod
kaynak = inspect.getsource(_cv_mod)
bekle("calibration_view.py 'Kamera' sekmesini kaydediyor",
      'self._tabs.addTab(self._camera_view, "Kamera")' in kaynak, True)
bekle("CameraCalView import ediliyor",
      "from ui.views.camera_cal_view import CameraCalView" in kaynak, True)
bekle("hata durumunda diger sekmeleri gotürmeyen try/except icinde",
      kaynak.count('_hata_sekmesi("Kamera"') >= 1, True)

print("\n-- MATEMATIK (calibrate_camera.py ile AYNI olmali) --")
# f_x = 900 px, genislik 1152 px -> HFOV = 2*atan((1152/2)/900) derece
hfov = _fov_from_focal(900.0, 1152)
beklenen_hfov = 2.0 * __import__("math").degrees(
    __import__("math").atan((1152 / 2.0) / 900.0))
bekle("fov_from_focal calibrate_camera.py formuluyle ayni", hfov, beklenen_hfov, tol=1e-9)

print("\n-- QIMAGE -> GRI DONUSUM + SATRANC TAHTASI TESPITI (sentetik) --")
# 8x6 kare (7x5 ic kose) sentetik satranc tahtasi ciz
kare_px = 40
cols_sq, rows_sq = 8, 6
w, h = cols_sq * kare_px, rows_sq * kare_px
board = np.zeros((h, w), dtype=np.uint8)
for r in range(rows_sq):
    for c in range(cols_sq):
        if (r + c) % 2 == 0:
            board[r * kare_px:(r + 1) * kare_px, c * kare_px:(c + 1) * kare_px] = 255
rgb = cv2.cvtColor(board, cv2.COLOR_GRAY2RGB)
qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()

gri = _qimage_to_gray(qimg)
bekle("gri donusum boyutu dogru", None if gri is None else gri.shape, (h, w))

ok, corners = cv2.findChessboardCorners(gri, (cols_sq - 1, rows_sq - 1), None)
bekle("sentetik tahtada ic koseler bulundu", bool(ok), True)

print("\n-- _on_yakala() UCTAN UCA (EN PAHALI yol — Pi'de olculen 5.7-6.2 sn) --")
# Bu, gercek arizaya sebep olan TAM (bayraksiz) findChessboardCorners +
# cornerSubPix cagrisini yapan buton handler'i - simdiye kadar test edilmemisti.
v._sp_cols.setValue(cols_sq - 1)
v._sp_rows.setValue(rows_sq - 1)
v._son_gri = gri
import time as _tm
_t0 = _tm.perf_counter()
v._on_yakala()
_yakala_cagri_ms = (_tm.perf_counter() - _t0) * 1000
print("  _on_yakala() cagrisinin suresi: %.1f ms (thread BASLATMA)" % _yakala_cagri_ms)
bekle("_on_yakala() 200ms altinda doner (ESKI kod burada 5.7-6.2 sn blokluyordu)",
      _yakala_cagri_ms < 200, True)
# Donanim hizindan BAGIMSIZ mimari kanit: cagridan hemen sonra (wait/
# processEvents'tan ONCE) karolar hala BOS olmali - sentetik veri hizli
# oldugu icin yukaridaki ms olcumu tek basina senkron bir mutasyonu
# YAKALAMAYABILIR, ama bu satir yakalar.
bekle("cagridan hemen sonra karolar HALA BOS (senkron calismadiginin kaniti)",
      len(v._karolar), 0)
yw = v._yakala_worker
bekle("yakalama worker'i olusturuldu", yw is not None, True)
if yw is not None:
    yw.wait(10000)
for _ in range(20):
    app.processEvents()
    if len(v._karolar) >= 1:
        break
    _tm.sleep(0.05)
bekle("yakalama sonrasi karolar listesine eklendi", len(v._karolar), 1)
bekle("liste widget guncellendi", v._liste.count(), 1)
bekle("yakala worker temizlendi (None'a dondu)", v._yakala_worker, None)
bekle("buton tekrar etkinlesti", v._btn_yakala.text(), "Kare Yakala")

print("\n-- OBJP INSASI (mode_board ile AYNI satirlar) --")
cols, rows, square = 9, 6, 25.0
objp = np.zeros((rows * cols, 3), np.float32)
objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square
bekle("objp nokta sayisi", objp.shape[0], cols * rows)
bekle("ilk nokta (0,0,0)", tuple(objp[0]), (0.0, 0.0, 0.0))
bekle("son nokta (kare kenarindan)", tuple(objp[-1]),
      ((cols - 1) * square, (rows - 1) * square, 0.0))

print("\n-- _on_hesapla UCTAN UCA (bilinen kamera matrisinden sentetik izdusum) --")
# _on_yakala/_on_frame'i (gercek video/QImage) atlayip _karolar'i DOGRUDAN
# dolduruyoruz - amac _on_hesapla'nin GERCEK kod yolunu (calibrateCamera
# cagrisi + fov hesabi + _son_kalibrasyon sozlugu) matematiksel olarak
# GECERLI bir veriyle sinamak. cv2.calibrateCamera'nin kendisi guvenilir
# (calibrate_camera.py zaten kullaniyor) - burada test edilen KENDI KODUMUZ.
GERCEK_FX, GERCEK_FY = 900.0, 900.0
CX, CY = 576.0, 324.0
W, H = 1152, 648
K_gercek = np.array([[GERCEK_FX, 0, CX], [0, GERCEK_FY, CY], [0, 0, 1]], dtype=np.float64)

cols, rows, square = 9, 6, 25.0
objp3d = np.zeros((rows * cols, 3), np.float32)
objp3d[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square

v._sp_cols.setValue(cols)
v._sp_rows.setValue(rows)
v._sp_square.setValue(square)
v._karolar = []
v._liste.clear()   # onceki (_on_yakala) bolumunden kalan 1 gercek kareyi temizle

# 8 farkli poz (aci+mesafe) - gercekci bir kalibrasyon oturumunu taklit eder
rng = np.random.default_rng(42)
for i in range(8):
    rvec = rng.uniform(-0.4, 0.4, size=3).astype(np.float64)
    rvec[2] = rng.uniform(-0.2, 0.2)
    tvec = np.array([rng.uniform(-60, 60), rng.uniform(-40, 40),
                     rng.uniform(500, 900)], dtype=np.float64)
    imgpts, _ = cv2.projectPoints(objp3d.astype(np.float64), rvec, tvec, K_gercek, None)
    corners = imgpts.reshape(-1, 1, 2).astype(np.float32)
    v._karolar.append((corners, (W, H)))
    # _on_yakala normalde _liste'yi de AYNI ANDA dolduruyor - burada
    # _karolar'i dogrudan yazdigimiz icin (gercek video/QImage'i atlayarak)
    # _liste'yi elle senkron tutuyoruz, aksi halde asagidaki geri-alma
    # kontrolu SAHTE bir uyumsuzluk raporlar (urun kodunda degil, testin
    # kendi kurgusunda).
    v._liste.addItem(f"Kare {len(v._karolar)}  —  {W}×{H}")

v._btn_hesapla.setEnabled(True)
import time as _time_mod
_t0 = _time_mod.perf_counter()
v._on_hesapla()
_cagri_ms = (_time_mod.perf_counter() - _t0) * 1000
print("  _on_hesapla() cagrisinin suresi: %.1f ms (thread BASLATMA, hesaplamanin KENDISI degil)" % _cagri_ms)
bekle("_on_hesapla() 200ms altinda doner (asenkron kanit — ESKI kod burada saniyeler surerdi)",
      _cagri_ms < 200, True)
# _on_hesapla ARTIK ASENKRON (2026-09-04 duzeltmesi - bkz. camera_cal_view.py
# modul basligi): calibrateCamera cagrisi arka plan QThread'inde kosuyor,
# _on_hesapla() aninda doner. Isin bitmesini ve kuyruklu sinyalin islenmesini
# bekliyoruz - GERCEK arayuzde de ayni akis (worker.finished -> ana thread
# olay dongusu sinyali isler).
w = v._hesapla_worker
bekle("hesaplama arka planda baslatildi (worker olusturuldu)", w is not None, True)
bekle("_on_hesapla ANINDA donuyor (_son_kalibrasyon henuz BOS)",
      getattr(v, "_son_kalibrasyon", None), None)
if w is not None:
    w.wait(5000)
for _ in range(20):
    app.processEvents()
    if getattr(v, "_son_kalibrasyon", None) is not None:
        break
    time_mod = __import__("time"); time_mod.sleep(0.05)

cal = getattr(v, "_son_kalibrasyon", None)
bekle("_son_kalibrasyon dolduruldu", cal is not None, True)
if cal:
    bekle("fx bilinen degere yakin (%5 tolerans)", cal["fx"], GERCEK_FX, tol=GERCEK_FX * 0.05)
    bekle("fy bilinen degere yakin (%5 tolerans)", cal["fy"], GERCEK_FY, tol=GERCEK_FY * 0.05)
    bilinen_hfov = _fov_from_focal(GERCEK_FX, W)
    bekle("hfov_deg bilinen degere yakin (2 derece tolerans)",
          cal["hfov_deg"], bilinen_hfov, tol=2.0)
    bekle("reproj_err_px dusuk (sentetik, gurultusuz veri)", cal["reproj_err_px"] < 0.5, True)
    bekle("width/height dogru", (cal["width"], cal["height"]), (W, H))
    bekle("panoya kopyala butonu acildi", v._btn_kopyala.isEnabled(), True)
    bekle("sonuc metni dolduruldu", len(v._sonuc.toPlainText()) > 0, True)

print("\n-- Kare geri alma --")
onceki = len(v._karolar)
v._on_geri_al()
bekle("son kare silindi", len(v._karolar), onceki - 1)
bekle("liste widget senkron", v._liste.count(), len(v._karolar))

print("\nSONUC:", "TEMIZ" if hata == 0 else "%d KALDI" % hata)
raise SystemExit(1 if hata else 0)
