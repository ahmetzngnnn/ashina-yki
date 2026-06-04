KULLANILMAYAN DOSYALAR (YKİ)
============================

Aktif YKİ uygulamasında import EDİLMEYEN dosyalar. Silinmediler.

Taşınma tarihi: 2026-06-04

──────────────────────────────────────────────────────────────────
DOSYA                    NEDEN KULLANILMIYOR
──────────────────────────────────────────────────────────────────
joystick_worker.py       main_window.py'de "JoystickWorker sistemden
                         kaldırıldı" notu var. Hiçbir yerden import
                         edilmiyor. Gamepad kontrolü devre dışı.

cloud_relay_worker.py    MQTT/HQ bulut aktarımı. Hiçbir modül import
                         etmiyor; opsiyonel/ileride kullanılabilir özellik.

test_telemetry.py        Kök dizin bağımsız telemetri test scripti
                         (manuel debug). Uygulama akışında import yok.
──────────────────────────────────────────────────────────────────

GERİ ALMA: ilgili dosyayı core/ (veya kök) dizinine geri taşıyın ve
main_window.py içinde worker'ı oluşturup başlatın.
