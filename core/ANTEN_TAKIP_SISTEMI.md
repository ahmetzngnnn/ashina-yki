# ASHİNA — Anten Takip Sistemi (Antenna Tracker) Altyapısı

YKİ tarafında, antenin (RUTX10 üzerindeki 2 eksenli gimbal + Pixhawk 2.4.8)
sürekli İHA'ya bakmasını sağlayan altyapı. İHA enerji alıp koordinat göndermeye
başladığı an anten otomatik olarak İHA'ya yönelir ve takip eder.

---

## 1. Donanım Mimarisi

```
   İHA (havada)
     │  RFD900A telsiz (915 MHz)
     ▼
   RFD900A  ──USB──►  Raspberry Pi (YKİ yanında)
                          │  Ethernet
                          ▼
                       RUTX10  (Access Point + mobil internet)
                          │  ├─ WiFi/Ethernet ağı → YKİ (Windows)
                          │  └─ NTRIP RTK için internet (RPi'ye)
                          ▼
                       Pixhawk 2.4.8  (anten gimbal)
                          │  2× servo
                          ▼
                       Anten (pan/tilt — azimut/elevasyon)
```

**Veri akışı:**
1. İHA → RFD900A → RPi: MAVLink telemetri (lat/lon/alt)
2. RPi/RUTX10 ağı → YKİ: telemetri (`mavlink_worker`)
3. YKİ `antenna_tracker_worker`: azimut/elevasyon hesaplar
4. YKİ → RUTX10 → anten Pixhawk: hedef açı/konum (MAVLink)
5. Anten Pixhawk → 2 servo: anteni İHA'ya çevirir

---

## 2. Yazılım Bileşenleri (eklenen)

| Dosya | Görev |
|-------|-------|
| `core/tracker_math.py` | Saf geometri: azimut/elevasyon/mesafe hesabı |
| `core/antenna_tracker_worker.py` | QThread: telemetri izle → hesapla → anten Pixhawk'a ilet |
| `core/gcs_config.py` | Anten takip ayarları (bağlantı, mod, home) |
| `ui/main_window.py` | Worker oluşturma + telemetri bağlama + yaşam döngüsü |

**Otomatik takip:** `mavlink_worker.telemetry_updated` → `antenna_tracker.update_drone_position`.
İHA geçerli koordinat (lat/lon ≠ 0) göndermeye başladığı an takip devreye girer.

---

## 3. Çalışma Modları (`antenna_tracker_mode`)

### "forward" (ÖNERİLEN)
İHA konumu anten Pixhawk'ına `GLOBAL_POSITION_INT` olarak iletilir.
Anten Pixhawk'ında **ArduPilot AntennaTracker** firmware'i azimut/elevasyon
hesabını ve servo sürüşünü kendisi yapar (en akıcı, PID dahili).

> Pixhawk 2.4.8'e **AntennaTracker** firmware yüklenmelidir (ArduPilot).
> Servo ayarları: `SERVO1` = PAN (yaw), `SERVO2` = TILT (pitch).
> İlgili parametreler: `PITCH_MIN/MAX`, `YAW_RANGE`, `SCAN_SPEED`.

### "compute"
YKİ azimut/elevasyon hesaplar ve `DO_MOUNT_CONTROL` (pitch=elevasyon, yaw=azimut)
ile gönderir. AntennaTracker firmware kullanılmıyorsa / mount-tabanlı gimbal için.

---

## 4. Ayarlar (`gcs_config.py` / settings.json)

| Anahtar | Varsayılan | Açıklama |
|---------|-----------|----------|
| `antenna_tracker_enabled` | `false` | Açılışta takip aktif mi |
| `antenna_pixhawk_conn` | `udp:0.0.0.0:14560` | Anten Pixhawk MAVLink adresi (RUTX10 üzerinden) |
| `antenna_tracker_mode` | `forward` | `forward` veya `compute` |
| `antenna_home_lat/lon/alt` | `0` | Anten sabit konumu (0 → otomatik Pixhawk GPS) |
| `antenna_min_distance_m` | `3.0` | Bu mesafeden yakında anteni oynatma (gürültü) |
| `antenna_update_hz` | `5.0` | Takip güncelleme hızı |

**Bağlantı adresi örnekleri:**
- `udp:0.0.0.0:14560` — RUTX10/RPi anten Pixhawk telemetrisini bu porta yollar
- `tcp:192.168.1.1:5760` — RUTX10 üzerinden TCP
- `/dev/ttyUSB1` (RPi) — anten Pixhawk RPi'ye seri bağlıysa

---

## 5. Anten Home (Referans Konum)

Anten azimut/elevasyon hesabı, antenin kendi konumuna göre yapılır:

- **Sabit kurulum** → `antenna_home_lat/lon/alt` elle girilir (saha GPS ölçümü).
- **Otomatik** → değerler 0 ise, anten Pixhawk'ının kendi GPS'i (`GLOBAL_POSITION_INT`)
  ilk fix'te home olarak kilitlenir.
- Çalışma sırasında elle: `antenna_tracker.set_antenna_home(lat, lon, alt)`.

---

## 6. RUTX10 Ağ Yapılandırması (özet)

1. **Access Point**: RUTX10 WiFi/LAN AP olarak ayarlanır; RPi ve YKİ aynı ağda.
2. **Mobil İnternet (WAN)**: SIM ile internet → NTRIP RTK için RPi'ye sağlanır.
3. **MAVLink yönlendirme**: Anten Pixhawk telemetrisi (Pixhawk → RUTX10) YKİ'nin
   `antenna_pixhawk_conn` adresine (UDP/TCP) yönlendirilir.
4. **NTRIP**: Mevcut `ntrip_worker` (YKİ) RUTX10 internetiyle caster'a bağlanır;
   RTCM düzeltmeleri İHA'ya RFD900A üzerinden iletilebilir.

---

## 7. NTRIP / RTK Notu

NTRIP altyapısı YKİ'de zaten mevcut (`core/ntrip_worker.py`). RUTX10'un mobil
internet erişimi bu worker'ın caster'a ulaşmasını sağlar. RTCM3 düzeltmeleri
İHA'ya iletildiğinde hem İHA hem (forward modda) anten Pixhawk RTK hassasiyetiyle
konumlanır → daha keskin anten takibi.

---

## 8. Kullanım

```python
# Açılışta otomatik (config'de):
"antenna_tracker_enabled": true

# Veya çalışma anında:
main_window.antenna_tracker.enable()    # takibi başlat
main_window.antenna_tracker.disable()   # durdur
```

**Takip durumu** `tracker_status` sinyaliyle yayınlanır:
`{azimuth, elevation, ground_distance_m, slant_distance_m, alt_diff_m, tracking}`
— bir UI göstergesine bağlanabilir (sonraki adım).

---

## 9. Sonraki Adımlar (opsiyonel)

- [ ] TopBar/Dashboard'a anten takip durum göstergesi (azimut/elevasyon/mesafe)
- [ ] Ayarlar görünümüne anten takip ayar alanı
- [ ] "Anten Home Ayarla" butonu (mevcut konumu home yap)
- [ ] forward modda anten Pixhawk SCAN modu kontrolü (İHA bağlantısı kesilince)
