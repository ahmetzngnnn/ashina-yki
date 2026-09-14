# ASHİNA YKİ — Yer Kontrol İstasyonu

TEKNOFEST döner-kanat İHA yarışması için PyQt6 ile geliştirilen yer kontrol
istasyonu (Ground Control Station) masaüstü uygulaması. Görev planlama,
canlı telemetri/video izleme, uçuş sonrası analiz, saha testi/kalibrasyon
araçları ve sesli otopilot anlatımı tek uygulamada toplanır.

Bu depo **sistemin iki parçasından biridir**. Diğeri: [ASHİNA İHA](https://github.com/ahmetzngnnn/ashina-iha)
(araç yardımcı bilgisayar firmware'i, Raspberry Pi üzerinde çalışır). İkisi
ayrı bilgisayarlarda çalışır ve ağ üzerinden TCP protokolüyle haberleşir —
mimari şeması için İHA deposunun README'sine bakın.

## İçindekiler

1. [Özellikler](#özellikler)
2. [Yazılım Gereksinimleri ve Sürümler](#yazılım-gereksinimleri-ve-sürümler)
3. [Kurulum — Linux Masaüstü / Geliştirme](#kurulum--linux-masaüstü--geliştirme)
4. [Kurulum — Raspberry Pi (Kiosk / Saha)](#kurulum--raspberry-pi-kiosk--saha)
5. [Çalıştırma](#çalıştırma)
6. [Servis Yönetimi](#servis-yönetimi-raspberry-pi)
7. [Yapılandırma ve Görev Profilleri](#yapılandırma-ve-görev-profilleri)
8. [İHA ile Haberleşme](#i̇ha-ile-haberleşme)
9. [Proje Yapısı](#proje-yapısı)
10. [Bilinen Sınırlamalar](#bilinen-sınırlamalar)

---

## Özellikler

| Sekme / Modül | İşlev |
|---|---|
| **Görev Paneli** (`mission_panel_view.py`) | Görev 1 (Sonsuz Uçuş), Görev 2 (Hedef Tespit + Yük Bırakma), **Final Görev** (kalıcı hızlı özel mod) — harita üzerinde direk/iniş noktası işaretleme, otomatik rota üretimi, tek tuşla görev gönderimi |
| **Dashboard** | Canlı telemetri HUD, batarya (gerilimden ölçeklenen yüzde), harita, sesli otopilot anlatıcısı (Türkçe nöral TTS) |
| **Flight Analysis** | Kaydedilmiş görev loglarının (`telemetry_recorder`) uçuş sonrası analizi, grafikler (`pyqtgraph`) |
| **Flight Modes** | Uçuş kipi anahtarı (FLTMODE1/4/6) izleme/ayarlama |
| **Autotune** | ArduCopter AutoTune modunu yardımcı butonlarla başlatma/izleme |
| **Calibration** (sensör / kamera / kamera montajı) | Kamera kalibrasyon değerleri, sensör kalibrasyonu, montaj açısı kalibrasyonu |
| **Preflight Check** | Uçuş öncesi kontrol listesi, FC parametre doğrulama sonuçlarının görünümü |
| **Saha Testi** | Pervanesiz, insan gözetimli gerçek senaryo koşucusu — İHA'ya SSH üzerinden uzaktan tetiklenebilir |
| **Servo/Drop Test** | Yük bırakma servolarını tek tek tetikleyip test etme |
| **Motor Test** | Motor step testi |
| **System Log** | Canlı İHA/YKİ log akışı |
| **Settings** | Bağlantı, kamera profili, ses, appdata ayarları |

Ek altyapı: RTK/NTRIP GNSS worker, MAVLink worker, offline harita (vendored
Leaflet), SQLite tabanlı yerel depolama, otomatik açılış süresi izleme.

## Yazılım Gereksinimleri ve Sürümler

| Bileşen | Sürüm |
|---|---|
| Python (geliştirme) | 3.10.12 (Ubuntu 22.04.5 LTS, jammy) |
| Python (Raspberry Pi dağıtımı) | 3.13.5 (Debian 13 trixie) |
| PyQt6 | ≥ 6.5.0 |
| PyQt6-WebEngine | ≥ 6.5.0 |
| pymavlink | ≥ 2.4.41 |
| opencv-python | ≥ 4.8.0 |
| pyqtgraph | ≥ 0.13.3 |
| piper-tts | ≥ 1.6.0 (Türkçe nöral ses — model `assets/tts/` altında depoda) |

Tam liste: `requirements.txt` (masaüstü) / `requirements-linux.txt` (Linux
paketleme varyantı).

## Kurulum — Linux Masaüstü / Geliştirme

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

## Kurulum — Raspberry Pi (Kiosk / Saha)

Yer istasyonu Pi'sinde (önerilen: Raspberry Pi 5, 8 GB RAM, Debian 13 trixie)
PyQt6'nın **apt'ten** kurulması gerekir — pip sürümü sistemin Qt/Wayland
altyapısıyla (labwc, Xwayland) uyumsuz olabilir:

```bash
sudo apt install -y python3-pyqt6 python3-pyqt6.qtwebengine \
                     python3-opencv pyqt6-dev-tools

# venv, sistem paketlerini görebilsin diye --system-site-packages ile:
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements-linux.txt
```

**Bilinen Pi-özgü notlar:**
- `pyqt6-dev-tools` şarttır — Debian'ın çıplak `python3-pyqt6` paketi
  `PyQt6.uic` içermez, `pyqtgraph` bunu import eder ve paket eksikse çöker.
- Wayland oturumunda (labwc) `qt6-wayland` kurulu değilse
  `QT_QPA_PLATFORM=xcb` ve `DISPLAY=:0` (Xwayland) zorunludur — aksi halde
  uygulama Qt wayland eklentisini bulamayıp açılmaz.
- Kiosk modu (`ASHINA_YKI_KIOSK=1`) giriş ekranını atlayıp otomatik admin
  girişi yapar ve tam ekran açılır (**F11** pencere moduna alır).

## Çalıştırma

```bash
./run.sh
# ya da doğrudan:
QT_QPA_PLATFORM=xcb DISPLAY=:0 python3 main.py
```

## Servis Yönetimi (Raspberry Pi)

Kullanıcı düzeyinde systemd servisi (GUI uygulaması olduğundan grafik
oturumu gerektirir — sistem servisi **değil**):

```bash
mkdir -p ~/.config/systemd/user
cp packaging/linux/ashina-yki.service ~/.config/systemd/user/
# WorkingDirectory'yi kendi kurulum yolunuza göre düzenleyin
systemctl --user daemon-reload
systemctl --user enable --now ashina-yki
```

İzleme:

```bash
systemctl --user status ashina-yki
journalctl --user -u ashina-yki -f
```

`Restart=on-failure` ile çöküş sonrası otomatik yeniden başlar. `sudo`
**gerekmez** — İHA tarafındaki sistem servisiyle karıştırmayın.

## Yapılandırma ve Görev Profilleri

- **Gerçek ayar dosyası**: `~/.local/share/ASHINA-YKI/settings.json`
  (uygulama verisi dizini, depo kökündeki `settings.json` değil).
- **Görev profilleri**: `~/.local/share/ASHINA-YKI/mission_profiles.json`
  — her görev sekmesi (Görev 1/2/Final) kendi kayıtlı profil grubunu
  (koordinatlar, irtifa, hız, vb.) burada tutar; varsayılan profil işaretiyle
  sekme açılışta otomatik doldurulur.
- Kamera/sensör kalibrasyonları ve appdata, `core/apppaths.py` üzerinden
  platforma uygun (Linux `XDG_DATA_HOME`) dizinde saklanır.

## İHA ile Haberleşme

Uygulama, İHA firmware'ine ağ üzerinden bağlanır:

| Port | Yön | İçerik |
|---|---|---|
| TCP 5601 | YKİ → İHA | Görev parametreleri (JSON) + start/abort/status komutları |
| TCP 5602 | İHA → YKİ | Sürekli durum/telemetri akışı |
| HTTP 8080 | İHA → YKİ | Ham kamera MJPEG akışı |
| HTTP 8081 | İHA → YKİ | İşlenmiş (overlay'li) MJPEG akışı |

Varsayılan adres `ashina-iha.local` (mDNS); farklı bir ağda/simülasyonda
çalışırken üst çubuktan İHA IP adresi elle girilebilir.

## Proje Yapısı

```
YKI/
├── main.py                       Giriş noktası, kiosk modu, QApplication kurulumu
├── ui/
│   ├── main_window.py            Ana pencere, sekme yönetimi, worker thread'leri
│   └── views/                    Her sekme kendi dosyasında (mission_panel_view.py,
│                                  dashboard_view.py, flight_analysis_view.py, ...)
├── core/
│   ├── mavlink_worker.py         MAVLink telemetri işçisi
│   ├── mission_profiles.py       Appdata profil kaydetme/yükleme
│   ├── rtk_gps_worker.py         RTK/NTRIP GNSS
│   ├── telemetry_recorder.py     Görev kaydı (uçuş sonrası analiz için)
│   ├── audio_tts_worker.py       Sesli anlatım (piper-tts)
│   └── vehicle_bench_control.py  Saha testi bench kontrolü
├── assets/                       İkonlar, harita, TTS ses modeli
├── packaging/linux/               systemd birim dosyası
└── tools/                        Yardımcı betikler
```

## Bilinen Sınırlamalar

- `settings.json` (repo kökünde) sadece şablon amaçlıdır — appdata varken
  uygulama tarafından okunmaz, gerçek ayarlar `~/.local/share/ASHINA-YKI/`
  altındadır.
- `.venv/` ve derlenmiş paket çıktıları (`build/`, `dist/`) repoya dahil
  değildir, her ortamda yeniden oluşturulmalıdır.
- Ses modeli dosyası (`assets/tts/tr_TR-dfki-medium.onnx`, ~60 MB) GitHub'ın
  önerilen dosya boyutu sınırını (50 MB) aşıyor — push edilebiliyor ama
  ileride Git LFS'e taşınması değerlendirilebilir.
