# ASHİNA YKİ (Yer Kontrol İstasyonu) — Performans & Mantık Analizi ve İyileştirme Planı

> Hazırlanma: 2026-06-16 · Hedef: Windows PyQt6 GCS · ~12.9k satır Python
> Bağlantı: FC ↔ telemetri radyosu (COM port) — **bant genişliği kısıtlı** (~57600 baud)
> Yöntem: tüm `core/` ve `ui/` modülleri, sinyal-slot bağlamları ve veri akışı incelendi.

---

## 0. Yönetici Özeti

YKİ mimarisi sağlam (worker thread'ler + sinyal/slot + tab'lı UI). Ama performansı
düşüren **3 ana kalem** var:

1. **Telemetri tüm sekmelere fan-out** — ~20Hz telemetri, **gizli sekmeler dahil** 11
   view'e gidiyor; 3 ayrı web-harita (`runJavaScript`) + repaint'ler her pakette çalışıyor.
2. **Çift telemetri akış isteği** — hem eski `REQUEST_DATA_STREAM(ALL@4Hz)` hem modern
   `SET_MESSAGE_INTERVAL` gönderiliyor → radyoyu **doyuruyor** (mission indirme 500ms/WP,
   telemetri gecikmesi/jitter).
3. **3 ayrı QWebEngineView haritası** — 3 Chromium render süreci = yüksek RAM/CPU tabanı.

Ayrıca tekrarlayan bir **mantık hatası kaynağı**: `_connect_signals` ile
`_reconnect_mavlink_signals` tutarsızlığı — bu oturumda çözdüğümüz "çift gönderim",
"oku/yaz ölü buton", "progress bağlanmıyor" hatalarının hepsi aynı kökten.

---

## 1. Mimari & Veri Akışı

```
  ┌─────────────────────────── ANA THREAD (UI / Qt event loop) ───────────────────────────┐
  │  MainWindow                                                                              │
  │   ├─ QTabWidget (12 sekme — AYNI ANDA YALNIZCA BİRİ GÖRÜNÜR)                             │
  │   │    Dashboard | Görev(Telemetri) | Özel Görev | İHA Test | Test | Kalib |            │
  │   │    Tuning | Gelişmiş | Sys/Log | Analiz | Anten | Ayarlar                            │
  │   ├─ Watchdog QTimer(5s) → worker heartbeat kontrol (hung/crashed → restart)            │
  │   └─ Sinyal yönlendirme: _connect_signals() + _reconnect_mavlink_signals()              │
  └──────────────────────────────────────────────────────────────────────────────────────┘
        ▲ telemetry_updated(dict)  ~20Hz  → 11 slot'a FAN-OUT (gizli sekmeler dahil)
        │
  ┌─────┴──────────── Worker Thread'ler (QThread) ────────────────────────────────────────┐
  │  MavlinkWorker   — COM/TCP/UDP MAVLink; recv loop; telemetri accumulator; mission up/dl │
  │  AISocketWorker  — companion TCP 5601 (görev JSON)                                       │
  │  RpiStatusWorker — companion TCP 5602 (durum JSON)                                       │
  │  VideoStreamWorker — MJPEG HTTP (Faz 2'de optimize edildi)                               │
  │  AntennaTracker / NTRIP / TTS                                                            │
  └────────────────────────────────────────────────────────────────────────────────────────┘
```

**Telemetri yolu:** MavlinkWorker.recv → accumulator güncelle → **her 5 mesajda** tüm
accumulator'ı kopyala → `telemetry_updated.emit(dict)` → ana thread'de 11 slot.

---

## 2. Performans Bulguları (öncelik sıralı)

### 🔴 P1 — Telemetri tüm sekmelere fan-out (gizli sekmeler dahil)
`telemetry_updated` (~20Hz) şu slot'lara bağlı: top_bar, dashboard, tuning, antenna,
mission_view, mission_panel, test_panel, calib, syslog, telemetry_recorder. **Hepsi her
pakette çalışır** — sekme gizli olsa bile.
- **3 ayrı web-harita** `runJavaScript(set_drone_pos / updateDrone)` ile 20Hz güncelleniyor:
  `dashboard_view:692`, `mission_view:531`, `mission_panel_view:263`. Kullanıcı Dashboard'a
  bakarken bile Görev haritaları (gizli) güncelleniyor → boşa Chromium IPC.
- **Etki:** ~60 JS çağrısı/sn + gereksiz repaint → UI takılması, yüksek CPU.
- **Çözüm:** `QTabWidget.currentChanged` ile telemetriyi **yalnızca görünür view'e** yönlendir.
  Sürekli gerekenler (top_bar durum, telemetry_recorder, antenna_tracker) her zaman alsın;
  ağır görseller (harita/repaint) yalnızca görünürken.

### 🔴 P2 — Çift telemetri akış isteği radyoyu doyuruyor
`_request_streams()` ([mavlink_worker.py:429](core/mavlink_worker.py#L429)) hem eski
`REQUEST_DATA_STREAM` (özellikle **stream 0=ALL @4Hz**) hem modern `SET_MESSAGE_INTERVAL`
gönderiyor. CubeOrange (ArduCopter 4.x) ikisini de işler → ALL@4Hz, RAW_IMU/SCALED_PRESSURE/
SERVO_OUTPUT gibi **gereksiz mesajları** akıtır.
- Toplam ~80-100 msg/sn isteniyor; 57600 baud radyo gerçekçi ~50-80 msg/sn taşır → **doyma**.
- **Etki:** mission indirme 500ms/WP (33s!), telemetri gecikmesi, paket kaybı/retry
  (loglardaki "WP alınamadı, tekrar deneniyor" bundan).
- **Çözüm:** Eski `REQUEST_DATA_STREAM`'i (özellikle ALL) kaldır; sadece
  `SET_MESSAGE_INTERVAL` ile radyoya uygun hızlar (ATTITUDE/VFR 5-8Hz, pozisyon 4-5Hz, ESC
  3Hz vb.). Mission indirme sırasında akışları geçici kıs → indirme 3-4× hızlanır.

### 🟠 P3 — 3 ayrı QWebEngineView haritası
Dashboard, mission_view, mission_panel'in her biri ayrı `QWebEngineView` (Chromium süreci).
3 harita = 3 render süreci = yüksek RAM (~300-500MB) + CPU tabanı.
- **Çözüm (orta vade):** Tek paylaşılan harita view'i veya daha hafif bir harita (örn.
  pyqtlet/qtawesome yerine statik tile + QPainter, ya da tek QWebEngine'i sekmeler arası taşı).
  En azından gizli haritaları JS'ten beslemeyi durdur (P1 ile çözülür).

### 🟠 P4 — Watchdog UI thread'i bloke ediyor
`_check_workers` ([main_window.py:807](ui/main_window.py#L807)) hung/crashed worker'da
`worker.wait(3000)` çağırıyor → **ana thread 3s donar**. (Mission indirme heartbeat fix'i
ile artık nadiren tetikleniyor ama hâlâ risk.)
- **Çözüm:** `wait()` süresini kısalt (örn. 500ms) veya restart'ı UI'yi bloklamadan yap.

### 🟡 P5 — Telemetri "her 5 mesaj" (zamana değil sayıya bağlı)
`_telemetry_tick >= 5` ([mavlink_worker.py:421](core/mavlink_worker.py#L421)) — mesaj seli
olunca emit hızı artar (kontrolsüz). Ayrıca accumulator hiç temizlenmez (gördüğü her key
kalır), her emit'te tüm dict kopyalanır.
- **Çözüm:** Zaman tabanlı throttle (örn. en fazla 15Hz: `if now - last_emit > 0.066`).

### 🟡 P6 — Sürekli timer'lar gizli sekmelerde de çalışıyor
ESC animasyonu (`dashboard_view:723` showEvent/hideEvent) **doğru gated**. Ama:
calibration_view timer'ları (40ms ×2 = 25Hz), mission poll'ları (400-450ms) görünürlükten
bağımsız çalışabiliyor.
- **Çözüm:** Tüm görsel timer'ları `showEvent/hideEvent` ile gated yap.

### 🟢 P7 — Mikro
- `import math` `telemetry_recorder.on_telemetry` içinde (20Hz) → modül başına al.
- `telemetry_updated` her emit'te tam accumulator kopyalıyor (P5 ile birlikte ele alınır).

---

## 3. Mantık Hataları & Tutarsızlıklar

### 🔴 L1 — `_connect_signals` ↔ `_reconnect_mavlink_signals` tutarsızlığı (KÖK SEBEP)
MAVLink worker disconnect'te yeniden yaratılıyor; sinyaller iki ayrı metotta elle yeniden
bağlanıyor. İki liste **senkron değil** → bu oturumda çıkan TÜM şu hatalar buradan:
- "Görev çift gönderiliyor" (mission_ready_to_send `_reconnect`'te tekrar bağlanıyordu)
- "Drone Oku/Yaz ölü" (write/read `_reconnect`'te yoktu)
- "progress bağlanmıyor"
- **Çözüm:** Tek bir `_wire_mavlink_worker(worker)` metodu — TÜM worker↔UI bağlantıları
  orada; hem ilk kurulum hem reconnect onu çağırsın. Tek kaynak, tutarsızlık biter.

### 🟡 L2 — Mission indirme yavaşlığı (P2'ye bağlı)
Radyo doyması + sıralı request/response. P2 çözülünce büyük ölçüde düzelir; ek olarak
indirme sırasında akış kısma.

### 🟢 L3 — Emit sayıya bağlı (P5 ile aynı).

---

## 4. Kullanılmayan / Kullanılması Gereken / Ölü Kod

- **U1 (çözüldü):** `mission_upload_progress` emit ediliyordu ama UI'de kullanılmıyordu →
  bu oturumda progress dialog'a bağlandı.
- **U2 (çözüldü):** `_on_read_mission` ölü slot'tu → bağlandı (progress kutusu).
- **U3:** `kullanilmayanlar/` klasörü (joystick_worker, cloud_relay_worker, test_telemetry)
  — import edilmiyor; ölü kod. Sil veya açıkça arşivle.
- **U4:** Emit edilip tüketilmeyen başka sinyaller olabilir (audit gerekli):
  `home_position_updated`, `log_download_progress` kullanımları doğrulanmalı.

---

## 5. Teknoloji / Yöntem Değişiklikleri (Uyum Matrisi)

| # | Mevcut | Önerilen | Kazanç | Risk |
|---|--------|----------|--------|------|
| T1 | Telemetri tüm view'lere | **Görünür-sekme yönlendirme** | UI yükü 3-5× ↓ | Düşük |
| T2 | Çift akış isteği | **Tek SET_MESSAGE_INTERVAL + radyo-uyumlu hız** | Radyo rahatlar, indirme 3-4× hız | Düşük |
| T3 | 3× QWebEngine harita | **Tek paylaşılan harita / hafif harita** | RAM ~2× ↓ | Orta-Yüksek |
| T4 | İki ayrı sinyal-wire metodu | **Tek `_wire_mavlink_worker()`** | Mantık hatası sınıfı biter | Düşük |
| T5 | Sayıya bağlı emit | **Zaman tabanlı throttle (≤15Hz)** | Kararlı UI, kontrollü yük | Düşük |
| T6 | Mission indirme tam hız telemetriyle | **İndirmede akış kısma** | İndirme 3-4× hız | Düşük |

---

## 6. Faz Faz İyileştirme Planı

### Faz A — Sinyal mimarisi ✅ TAMAMLANDI (2026-06-16)
- [x] **L1/T4:** `_connect_signals` + `_reconnect_mavlink_signals` → tek `_wire_mavlink_worker()`.
      Eski metot silindi; ilk kurulum ve reconnect aynı metodu çağırıyor. Tutarsızlıktan
      doğan eksik bağlantılar (telemetry→mission_view/antenna reconnect'te yoktu) ve
      mükerrer (send_servo_action→ai_worker) düzeldi.

### Faz B — Telemetri yükü ✅ TAMAMLANDI (2026-06-16)
- [x] **P1/T1:** `_dispatch_telemetry` — sürekli tüketiciler (top_bar/recorder/antenna/
      syslog) her zaman; geri kalan YALNIZCA görünür sekme (`_tabs.currentWidget()`).
      3 web-harita + HUD artık gizli sekmelerde güncellenmiyor.
- [x] **P5/T5:** Emit "her 5 mesaj" → **zaman tabanlı ≤15Hz**.
- [x] **P6:** `Drone3DWidget` 25Hz timer'ı showEvent/hideEvent ile gated.

### Faz C — Radyo/telemetri akışı ✅ TAMAMLANDI (2026-06-16)
- [x] **P2/T2:** `REQUEST_DATA_STREAM(ALL@4Hz)` seli kaldırıldı (sadece hedefli fallback);
      SET_MESSAGE_INTERVAL radyo-uyumlu hızlara çekildi (~63→~41 msg/s).
- [x] **T6:** `_set_transfer_mode()` — mission oku/yaz sırasında ağır akışlar kısılıp
      (try/finally ile) sonra geri açılıyor → indirme radyoda hızlanır.

### Faz D — Temizlik & mikro ✅ TAMAMLANDI (2026-06-16)
- [x] **P7:** `import math` modül başına (telemetry_recorder).
- [x] **P4:** Watchdog `wait(3000)` → `800ms` (UI donmasını azalt).
- [ ] **U3:** `kullanilmayanlar/` zaten arşivli (import yok) — bırakıldı.
- [ ] **U4:** `home_position_updated` tüketilmiyor — ileride harita home'una bağlanabilir.

### Faz E (OPSİYONEL) — Harita konsolidasyonu ⏸️ ERTELENDİ (2026-06-16, yarış sonrası)
- [ ] **P3/T3:** 3 QWebEngine → lazy-load / tek harita.
- **Karar gerekçesi:** Faz B, gizli haritaların telemetri/JS güncellemesini durdurduğu için
  Faz E'nin CPU kazancı zaten alındı. Geriye yalnızca boşta Chromium RAM'i kaldı (modern
  PC'de kabul edilebilir). Lazy-load, aktif kullanılan görev planlama haritasında 10+ guard
  noktası → çökme riski. Yarış öncesi risk/kazanç dengesi olumsuz → **ertelendi.**

---

## 7. Beklenen Toplam Kazanım

| Metrik | Şimdi | Hedef (Faz A-D) |
|--------|-------|------------------|
| Telemetri UI yükü | 11 slot + 3 harita/pakette | 1 görünür view |
| Mission indirme | ~33 s (500ms/WP) | ~8-10 s |
| "Çift gönderim/ölü buton" sınıfı hata | tekrarlıyor | kökten biter |
| Gizli sekme CPU | sürekli (3 harita+timer) | ~sıfır |
| RAM (3 Chromium) | yüksek | Faz E ile ~2× ↓ |

---

## 8. Riskler & Önlemler
- Faz A sinyal birleştirme: bağlantı sayıları (mükerrer/eksik) dikkatle test edilmeli —
  kes/bağlan döngüsü + her buton tek tek denenmeli.
- Faz C akış kısma: indirme sonrası akışların **geri açıldığından** emin ol (yoksa telemetri durur).
- Her faz bağımsız test edilmeli; YKİ yeniden başlatılarak doğrulanmalı.
- Bu analiz YKİ tarafıdır; IHA tarafı [../IHA/PERFORMANS_ANALIZ.md](../IHA/PERFORMANS_ANALIZ.md)'de.
