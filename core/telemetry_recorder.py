import logging
import math
import re
import threading
import time
import json
import os
from pathlib import Path
from PyQt6.QtCore import QObject, QTimer, pyqtSlot

from core import local_storage
from core.global_state import state

logger = logging.getLogger(__name__)

# Bu kadar saniyede bir, uçuş sırasında buffer diske "kurtarma" dosyasına
# yazılır. Önceden buffer SADECE temiz bir armed→disarmed geçişinde diske
# yazılıyordu — bağlantı kaybı/çökme (tam olarak kaza öncesi en değerli anlar)
# durumunda bellekteki tüm veri kaybolurdu. Artık en kötü ihtimalle bu kadar
# saniyelik veri kaybedilir.
AUTOSAVE_INTERVAL_MS = 20_000

# ── F10 düzeltmesi ────────────────────────────────────────────────────────────
# Kayıt ÖNCEDEN yalnızca telemetride armed→disarmed geçişi görülünce kapanıp
# DB'ye yazılıyordu. Link koparsa (veya YKİ kapatılırsa) o geçiş HİÇ gelmiyor →
# uçuş sadece .autosave.json olarak diskte kalıyor ve "Kayıtlar" sekmesinde
# GÖRÜNMÜYORDU. Aşağıdaki üç mekanizma bunu kapatır:
#   1) Telemetri bu kadar saniyedir gelmiyorsa kayıt otomatik kapanır (link kaybı).
TELEMETRY_STALE_TIMEOUT_S = 15.0
#   2) Bayatlık kontrolü bu periyotla yapılır (yalnızca kayıt sırasında çalışır).
STALE_CHECK_INTERVAL_MS = 2_000
#   3) finalize()/flush() ile kapanışta dışarıdan (main_window) kapatılabilir.

# Buffer üst sınırı — 15 Hz × 20 dk ≈ 18.000 örnek; uzun/unutulmuş bir oturumda
# liste sınırsız büyüyordu. Sınır aşılınca veri ATILMAZ: buffer 2'de bire
# seyreltilir (decimation) ve sonraki örnekler de aynı oranda alınır. Böylece
# uçuşun TAMAMI korunur, yalnızca zaman çözünürlüğü düşer.
MAX_BUFFER_SAMPLES = 50_000

# flight_<ts>.autosave.json → ts
_AUTOSAVE_RE = re.compile(r"^flight_(\d+)\.autosave\.json$")


class TelemetryRecorder(QObject):
    def __init__(self, db_conn, parent=None):
        super().__init__(parent)
        self._db_conn = db_conn
        self._is_recording = False
        self._ts_start = 0
        self._mission_type = "UNKNOWN"
        self._buffer = []

        self._max_speed = 0.0
        self._min_voltage = 999.0
        self._max_roll = 0.0
        self._max_pitch = 0.0

        # Save directory (platforma göre yazılabilir veri klasörü)
        from core.apppaths import app_data_dir
        self._save_dir = app_data_dir("telemetry_logs")

        self._last_armed = False

        # Buffer seyreltme durumu (bkz. MAX_BUFFER_SAMPLES)
        self._decimation = 1        # kaç örnekte bir buffer'a yazılıyor
        self._sample_seq = 0        # gelen telemetri sayacı (seyreltme için)

        # Son telemetri zamanı (monotonic) — link kaybı tespiti için
        self._last_telemetry_mono = 0.0

        # Periyodik autosave — sadece kayıt sırasında çalışır (bkz. _start/_stop_recording)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.timeout.connect(self._autosave)

        # Link kaybı bekçisi — telemetri kesilirse kaydı kapatıp DB'ye yazar.
        # NEDEN: temiz DISARM gelmeden biten uçuşlar "Kayıtlar" sekmesinde hiç
        # görünmüyordu (F10).
        self._stale_timer = QTimer(self)
        self._stale_timer.setInterval(STALE_CHECK_INTERVAL_MS)
        self._stale_timer.timeout.connect(self._check_stale)

    @pyqtSlot(dict)
    def on_telemetry(self, data: dict):
        # Link kaybı bekçisi bu damgayı okur (bkz. _check_stale)
        self._last_telemetry_mono = time.monotonic()

        # Otomatik Başlatma/Durdurma
        armed = data.get("armed", False)
        if armed and not self._last_armed:
            self._start_recording(data.get("flight_mode", "AUTO"))
        elif not armed and self._last_armed:
            self._stop_recording("disarm")

        self._last_armed = armed

        if not self._is_recording:
            return

        # Zaman (relatif sn)
        ts_rel = time.time() - self._ts_start
        
        # Değerleri al (doğru key'lerle)
        spd = data.get("groundspeed", 0.0)
        volt = data.get("battery_voltage", 0.0)
        
        # Roll ve Pitch radyan gelir, dereceye çevir
        r_rad = data.get("roll", 0.0)
        p_rad = data.get("pitch", 0.0)
        roll = abs(math.degrees(r_rad))
        pitch = abs(math.degrees(p_rad))
        
        alt = data.get("alt", 0.0)
        
        vib_x = data.get("vib_x", 0.0)
        vib_y = data.get("vib_y", 0.0)
        vib_z = data.get("vib_z", 0.0)
        pid_err = data.get("pid_err", 0.0)
        
        esc_rpm = data.get("esc_rpm", [0, 0, 0, 0])
        esc_temp = data.get("esc_temp", [0, 0, 0, 0])
        
        # Min/Max güncelle
        self._max_speed = max(self._max_speed, spd)
        if volt > 0:
            self._min_voltage = min(self._min_voltage, volt)
        self._max_roll = max(self._max_roll, roll)
        self._max_pitch = max(self._max_pitch, pitch)
        
        # Grafik verisi
        point = {
            "ts": round(ts_rel, 2),
            "spd": round(spd, 2),
            "volt": round(volt, 2),
            "alt": round(alt, 2),
            "roll": round(math.degrees(r_rad), 2),
            "pitch": round(math.degrees(p_rad), 2),
            "vib_x": round(vib_x, 2),
            "vib_y": round(vib_y, 2),
            "vib_z": round(vib_z, 2),
            "pid_err": round(pid_err, 2),
            "esc_rpm": esc_rpm[:4] if len(esc_rpm) >= 4 else [0,0,0,0],
            "esc_temp": esc_temp[:4] if len(esc_temp) >= 4 else [0,0,0,0]
        }

        # Buffer üst sınırı (F10-c). Min/Max istatistikleri YUKARIDA, seyreltmeden
        # ÖNCE güncellendi — böylece atlanan örneklerin tepe değerleri kaybolmaz.
        self._sample_seq += 1
        if self._decimation > 1 and (self._sample_seq % self._decimation) != 0:
            return

        self._buffer.append(point)

        if len(self._buffer) >= MAX_BUFFER_SAMPLES:
            # Veriyi atmak yerine çözünürlüğü yarıya indir: uçuşun tamamı korunur,
            # bellek kullanımı sabit bir tavanın altında kalır.
            self._buffer = self._buffer[::2]
            self._decimation *= 2
            logger.warning(
                "Telemetri buffer sınırı (%d) aşıldı — kayıt 1/%d oranında "
                "seyreltildi (%d nokta kaldı).",
                MAX_BUFFER_SAMPLES, self._decimation, len(self._buffer),
            )

    def _start_recording(self, mission_type: str):
        logger.info(f"Telemetri kaydı başladı: {mission_type}")
        self._is_recording = True
        self._ts_start = int(time.time())
        self._mission_type = mission_type
        self._buffer.clear()
        self._max_speed = 0.0
        self._min_voltage = 999.0
        self._max_roll = 0.0
        self._max_pitch = 0.0
        self._decimation = 1
        self._sample_seq = 0
        self._last_telemetry_mono = time.monotonic()
        self._autosave_timer.start()
        self._stale_timer.start()

    def _autosave_path(self) -> Path:
        return self._save_dir / f"flight_{self._ts_start}.autosave.json"

    def _autosave(self) -> None:
        """
        Periyodik kurtarma yazımı — bağlantı kopması/çökme gibi TEMİZ disarm
        olmayan bir bitişte bile en fazla AUTOSAVE_INTERVAL_MS kadar veri
        kaybedilsin diye. Diske yazma arka plan thread'inde yapılır (UI
        thread'ini bloklamasın).
        """
        if not self._is_recording or len(self._buffer) < 10:
            return
        # list(...) tam bir kopya üretir; buffer seyreltmede yeniden bağlansa bile
        # (bkz. MAX_BUFFER_SAMPLES) bu anlık görüntü etkilenmez.
        snapshot = list(self._buffer)
        path = self._autosave_path()

        def _write():
            try:
                tmp = path.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f)
                os.replace(tmp, path)   # atomik değiştirme — yarım yazılmış dosya kalmasın
            except Exception as exc:
                logger.warning(f"Telemetri autosave yazılamadı: {exc}")

        threading.Thread(target=_write, daemon=True, name="telemetry-autosave").start()

    def _check_stale(self) -> None:
        """Telemetri kesildiyse kaydı otomatik kapat (F10-b).

        NEDEN: Link koparsa armed→disarmed geçişi HİÇ gelmez; eski kodda kayıt
        sonsuza dek "açık" kalır, uygulama kapanınca da yalnızca .autosave.json
        artardı → uçuş "Kayıtlar" sekmesinde hiç görünmezdi. Artık telemetri
        TELEMETRY_STALE_TIMEOUT_S saniyedir gelmiyorsa uçuş normal biten bir
        uçuş gibi diske + DB'ye yazılır.
        """
        if not self._is_recording:
            self._stale_timer.stop()
            return
        idle = time.monotonic() - self._last_telemetry_mono
        if idle >= TELEMETRY_STALE_TIMEOUT_S:
            logger.warning(
                "Telemetri %.0f saniyedir gelmiyor — uçuş kaydı otomatik kapatılıyor "
                "(link kaybı).", idle)
            self._stop_recording("telemetri kesildi")
            # Telemetri geri gelirse YENİ bir kayıt başlayabilsin. Aksi halde
            # armed hâlâ True geldiği için armed→armed olur ve _start_recording
            # bir daha hiç tetiklenmezdi.
            self._last_armed = False

    # ── Public: dışarıdan kapatma / kurtarma ────────────────────────────────
    @pyqtSlot()
    def flush(self) -> None:
        """finalize() için kısa ad — kapanışta çağrılabilir (bkz. finalize)."""
        self.finalize()

    def finalize(self, reason: str = "kapanış") -> bool:
        """Açık kaydı TEMİZ DISARM beklemeden kapatır ve DB'ye SENKRON yazar.

        main_window.closeEvent() içinden çağrılmalıdır (F10-a). Yazma bilerek
        senkron yapılır: kapanışta arka plan daemon thread'i uygulama sonlanınca
        öldürülebilir ve kayıt yine kaybolurdu.

        Dönüş: kayıt kapatılıp yazıldıysa True, açık kayıt yoksa False.
        """
        if not self._is_recording:
            return False
        logger.info("Uçuş kaydı dışarıdan sonlandırılıyor (%s).", reason)
        self._stop_recording(reason, sync=True)
        return True

    def _stop_recording(self, reason: str = "disarm", sync: bool = False):
        if not self._is_recording:
            return
        self._is_recording = False
        self._autosave_timer.stop()
        self._stale_timer.stop()
        ts_end = int(time.time())

        if len(self._buffer) < 10:
            logger.info("Çok kısa uçuş (10 noktadan az), kaydedilmedi.")
            self._cleanup_autosave()
            return

        logger.info("Telemetri kaydı bitti (%s). %d nokta.", reason, len(self._buffer))

        filename = f"flight_{self._ts_start}.json"
        filepath = self._save_dir / filename
        snapshot = list(self._buffer)
        min_v = self._min_voltage if self._min_voltage < 999 else 0.0
        ts_start, mission_type = self._ts_start, self._mission_type
        max_speed, max_roll, max_pitch = self._max_speed, self._max_roll, self._max_pitch

        def _write():
            # F2 düzeltmesi: bu yazma önceden UI thread'inde SENKRON çalışıyordu —
            # tam disarm/iniş anında (operatörün en çok tepkisel UI istediği an)
            # büyük bir buffer için json.dump + DB yazımı arayüzü kilitleyebiliyordu.
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f)

                local_storage.save_flight_analysis(
                    self._db_conn,
                    ts_start,
                    ts_end,
                    mission_type,
                    max_speed,
                    min_v,
                    max_roll,
                    max_pitch,
                    str(filepath)
                )
                logger.info(f"Uçuş analizi DB'ye eklendi: {filepath}")
                self._cleanup_autosave()
            except Exception as e:
                logger.error(f"Uçuş analizi kaydedilemedi: {e}")

        if sync:
            # Kapanış yolu — daemon thread uygulama sonlanınca öldürülebilir.
            _write()
        else:
            threading.Thread(target=_write, daemon=True, name="telemetry-final-save").start()

    def recover_orphan_autosaves(self) -> int:
        """Yarım kalmış .autosave.json dosyalarını DB'ye aktarır (F10-d).

        NEDEN: Çökme / güç kesintisi / eski sürümle biten oturumlardan kalan
        kurtarma dosyaları diskte duruyor ama "Kayıtlar" sekmesinde görünmüyordu.
        Açılışta (main_window kurulumunda) bir kez çağrılması yeterlidir.

        Dönüş: DB'ye aktarılan uçuş sayısı.
        """
        recovered = 0
        try:
            candidates = sorted(self._save_dir.glob("flight_*.autosave.json"))
        except Exception as exc:
            logger.warning("Kurtarma dosyaları taranamadı: %s", exc)
            return 0

        for path in candidates:
            m = _AUTOSAVE_RE.match(path.name)
            if not m:
                continue
            ts_start = int(m.group(1))
            # Şu an kaydedilmekte olan uçuşa dokunma
            if self._is_recording and ts_start == self._ts_start:
                continue
            final_path = self._save_dir / f"flight_{ts_start}.json"
            if final_path.exists():
                # Kalıcı dosya zaten yazılmış → kurtarma dosyası artık gereksiz
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    points = json.load(f)
                if not isinstance(points, list) or len(points) < 10:
                    path.unlink(missing_ok=True)
                    continue

                # İstatistikleri noktalardan yeniden türet (özet değerler
                # autosave dosyasına yazılmıyor).
                max_speed = max((float(p.get("spd", 0.0)) for p in points), default=0.0)
                volts     = [float(p.get("volt", 0.0)) for p in points
                             if float(p.get("volt", 0.0)) > 0]
                min_v     = min(volts) if volts else 0.0
                max_roll  = max((abs(float(p.get("roll", 0.0))) for p in points), default=0.0)
                max_pitch = max((abs(float(p.get("pitch", 0.0))) for p in points), default=0.0)
                ts_end    = ts_start + int(float(points[-1].get("ts", 0.0)))

                with open(final_path, "w", encoding="utf-8") as f:
                    json.dump(points, f)

                local_storage.save_flight_analysis(
                    self._db_conn, ts_start, ts_end, "KURTARILDI",
                    max_speed, min_v, max_roll, max_pitch, str(final_path),
                )
                path.unlink(missing_ok=True)
                recovered += 1
                logger.info("Yarım kalmış uçuş kurtarıldı: %s (%d nokta)",
                            final_path.name, len(points))
            except Exception as exc:
                logger.warning("Kurtarma dosyası aktarılamadı (%s): %s", path.name, exc)

        if recovered:
            logger.info("%d yarım kalmış uçuş kaydı DB'ye aktarıldı.", recovered)
        return recovered

    def _cleanup_autosave(self) -> None:
        """Uçuş düzgün bitip kalıcı dosya yazıldıktan sonra kurtarma dosyasına gerek kalmaz."""
        try:
            self._autosave_path().unlink(missing_ok=True)
        except Exception:
            pass
