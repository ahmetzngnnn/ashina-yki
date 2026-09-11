"""
ai_socket_worker.py — Air Tarafı Görev Parametre Köprüsü
Protokol: Plain TCP JSON, Port 5601 (MissionReceiver ile uyumlu)

Her satır bir JSON mesajıdır (\n ile sonlandırılır).
"""
import itertools
import json
import logging
import queue
import socket
import time
from PyQt6.QtCore import QThread, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)

# Yanıt okuma tampon üst sınırı. NEDEN: _send_mission içindeki "\n" bekleyen
# recv döngüsü önceden yalnızca response_timeout ile sınırlıydı — sunucu "\n"
# hiç göndermeden veri akıtmaya devam ederse (bozuk/yanlış protokol) tampon
# sınırsız büyürdü. Kardeş worker'larla TUTARLI (rpi_status_worker 1 MB,
# video_stream_worker 512 KB) — burada da yanıt küçük bir JSON olduğundan
# aynı büyüklük mertebesi kullanılıyor.
_MAX_RESPONSE_BUF = 1_048_576  # 1 MB — taşma koruması

# AI model display adı → .pt dosya adı
_MODEL_NAME_MAP = {
    "YOLOv8n — Hızlı (Nano)":        "yolov8n.pt",
    "YOLOv8s — Dengeli (Small)":      "yolov8s.pt",
    "YOLOv8m — Orta (Medium)":        "yolov8m.pt",
    "YOLOv8l — Güçlü (Large)":        "yolov8l.pt",
    "YOLOv8x — En Güçlü (X-Large)":   "yolov8x.pt",
    "YOLOv8_person — İnsan Tespiti":   "yolov8n.pt",
    "YOLOv8_vehicle — Araç Tespiti":   "yolov8n.pt",
    "RT-DETR — Gerçek Zamanlı":        "rtdetr-l.pt",
    "Özel Model... (custom.pt)":       "custom.pt",
}

# GCS hedef adları → Air'in _TARGET_TO_CLASSES'taki isimlere normalize
_TARGET_NORMALIZE = {
    "İnsan / Kişi":                  "İnsan / Kişi",
    "Zırhlı Araç (Tank / APC)":      "Araç Tespiti",
    "Hafif Araç (Araba / Kamyon)":   "Araç Tespiti",
    "İnsansız Hava Aracı":           "İnsansız Hava Aracı",
    "Kuş / Yaban Hayvanı":           "Kuş / Yaban Hayvanı",
    "Silah / Silah Sistemi":         "Şüpheli Nesne",
    "Yangın / Duman":                "Yangın / Duman Tespiti",
    "Su Baskını / Sel":              "Su Baskını / Sel",
    "Gemi / Deniz Aracı":            "Gemi / Deniz Aracı",
    "Bina Hasarı":                   "Bina Hasarı",
    "Özel Hedef (Tanımla)":          "Özel Hedef",
}

# FIX P0-5: GCS mission type adları → Companion'ın beklediği canonical adlar
# GCS panelinden gelen display string'leri companion executor whitelist'iyle eşleştirir.
_MISSION_TYPE_NORMALIZE = {
    "TEKNOFEST_TASK_2_3":  "TEKNOFEST_TASK2",
    "TEKNOFEST_TASK2_3":   "TEKNOFEST_TASK2",
    "TEKNOFEST_GÖREV_2":   "TEKNOFEST_TASK2",
    "PAYLOAD_DROP":        "TEKNOFEST_TASK2",   # eski isim → yeni
    # TARGET_DROP görevi IHA'da kaldırıldı; yük bırakma artık TEKNOFEST_TASK2'nin
    # parçası (bkz. missions/executor.py). Ardılına eşle (PAYLOAD_DROP gibi) ki
    # eski isimle gelen istek IHA'da "unknown mission_type" ile reddedilmesin.
    "TARGET_DROP":         "TEKNOFEST_TASK2",
    "INFINITY_FLIGHT":     "INFINITY_FLIGHT",   # korunur
    # NOT: AI_DETECTION IHA'da kayıtlı bir görev DEĞİL ve YKİ'de başka yerde
    # kullanılmıyordu → ölü eşleme kaldırıldı (gönderilse IHA reddederdi).
}


class AISocketWorker(QThread):
    """
    GCS mission panel parametrelerini air tarafındaki MissionReceiver'a gönderir.
    Protokol: TCP JSON, port 5601 (\\n sonlandırmalı satırlar).
    """
    ai_detection_alert = pyqtSignal(dict)
    error_occurred     = pyqtSignal(str)
    mission_accepted   = pyqtSignal()
    # FIX P0-1: Görev reddedildiğinde UI'ye bildir
    mission_rejected   = pyqtSignal(str)

    def __init__(self, host: str = "127.0.0.1", port: int = 5601,
                 connect_timeout: float = 5.0, response_timeout: float = 5.0,
                 auth_token: str = "", parent=None):
        super().__init__(parent)
        self._host = host
        self._port = port
        # GÜVENLİK (2026-08-25): companion'ın gcs_server.auth_token'ıyla
        # AYNI dizgi olmalı — boşsa (varsayılan) kanal kimlik doğrulamasız
        # kalır (companion tarafı bunu zaten yüksek sesle uyarır).
        self._auth_token = auth_token
        # FIX P1-4: Timeout değerleri artık parametre — gcs_config'den beslenebilir
        self._connect_timeout  = connect_timeout
        self._response_timeout = response_timeout
        # ÖNCEDEN düz FIFO queue.Queue idi: high_priority bayrağı SADECE kuyruk
        # DOLUYKEN bir şey yapıyordu (en eski öğeyi atıp yer açma) — normal
        # (dolu olmayan) durumda abort/servo gibi "yüksek öncelikli" komutlar
        # kuyruğun EN SONUNA ekleniyor, önündeki (ör. yavaş/askıda kalmış bir
        # mission-load) komutların bitmesini bekliyordu. Flaky bir bağlantıda
        # her item connect+response timeout kadar (varsayılan 5+5=10s) sürebilir
        # — yani DURDUR/RTL komutu, önünde 1-2 sıradan komut varsa 10-20s
        # gecikebiliyordu. PriorityQueue ile artık abort/servo/drop_now GERÇEKTEN
        # öne geçiyor. seq sayacı aynı öncelikteki öğeler arasında FIFO sırasını
        # korur (PriorityQueue eşit öncelikte dict'leri karşılaştırmaya
        # çalışırsa TypeError atar — bu yüzden tie-breaker şart).
        self._send_queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=10)
        self._seq_counter = itertools.count()
        self._PRIO_HIGH = 0
        self._PRIO_NORMAL = 10
        self.heartbeat_counter: int = 0

    def run(self) -> None:
        while not self.isInterruptionRequested():
            self.heartbeat_counter += 1
            try:
                _prio, _seq, mission = self._send_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                self._send_mission(mission)
            except Exception as exc:
                logger.error("Görev gönderilemedi: %s", exc)
                self.error_occurred.emit(str(exc))

    def _send_mission(self, mission: dict) -> None:
        # GÜVENLİK: token TEK noktadan (burada, serileştirmeden hemen önce)
        # eklenir ki hangi çağrı yolundan geldiğine bakılmaksızın (görev
        # yükleme, servo, abort, drop_now ...) HİÇBİR mesaj tokensız gitmesin.
        if self._auth_token:
            mission = {**mission, "auth_token": self._auth_token}
        payload = json.dumps(mission, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            with socket.create_connection(
                (self._host, self._port), timeout=self._connect_timeout
            ) as sock:
                sock.sendall(payload)
                sock.settimeout(self._response_timeout)
                raw = b""
                while b"\n" not in raw:
                    if len(raw) > _MAX_RESPONSE_BUF:
                        # Taşma koruması — bkz. modül üstü not / rpi_status_worker.
                        raise ConnectionError(
                            f"MissionReceiver yanıtı {_MAX_RESPONSE_BUF} bayttan "
                            "büyük ama satır sonu (\\n) bulunamadı — bağlantı "
                            "sonlandırılıyor"
                        )
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    raw += chunk
                line = raw.split(b"\n")[0].strip()
                if line:
                    resp = json.loads(line)
                    logger.info("MissionReceiver yanıtı: %s", resp)
                    if resp.get("status") == "ok":
                        self.ai_detection_alert.emit(resp)
                        if resp.get("mission_type"):
                            self.mission_accepted.emit()
                    else:
                        # FIX P0-2: Rejection → kullanıcıya bildir.
                        # DÜZELTME: mission_rejected SADECE görev yüklemesi
                        # (mission_type içeren mesaj) reddedildiğinde emit edilir.
                        # NEDEN: eskiden komut tipinden BAĞIMSIZ emit ediliyordu →
                        # operatör kalkıştan önce "Yük Bırak"/servo komutu deneyip
                        # NACK alınca "Görev Reddedildi" dialogu görüyor ve
                        # "GÖREVİ BAŞLAT" butonu kilitleniyordu. Görev dışı
                        # komutların NACK'i artık yalnızca error_occurred ile
                        # bildirilir (mevcut sinyal — main_window değiştirilmedi).
                        msg = resp.get("message", "Companion komutu reddetti")
                        if mission.get("mission_type"):
                            logger.warning("Görev reddedildi: %s", msg)
                            self.mission_rejected.emit(msg)
                            self.error_occurred.emit(f"Companion: {msg}")
                        else:
                            action = mission.get("action", "?")
                            logger.warning("Komut reddedildi (action=%s): %s", action, msg)
                            self.error_occurred.emit(f"Companion [{action}]: {msg}")
                else:
                    # FIX P0-2: Boş yanıt → açık hata sinyali
                    logger.warning("Companion'dan boş yanıt geldi")
                    self.error_occurred.emit(
                        f"Companion yanıt vermedi ({self._host}:{self._port})"
                    )
        except Exception as exc:
            raise ConnectionError(f"MissionReceiver ({self._host}:{self._port}): {exc}")

    # ── Normalization yardımcısı ────────────────────────────────────────────────
    def _normalize_params(self, params: dict) -> dict:
        """Model adı, hedef adları ve mission_type'ı companion uyumlu forma çevirir."""
        # Model adı → .pt dosya adı
        model_display = params.get("model", "")
        params["model"] = _MODEL_NAME_MAP.get(model_display, model_display)

        # Hedef adları normalize
        raw_targets = params.get("targets", [])
        params["targets"] = [_TARGET_NORMALIZE.get(t, t) for t in raw_targets]

        # FIX P0-5: Mission type normalization
        mt = params.get("mission_type", "")
        params["mission_type"] = _MISSION_TYPE_NORMALIZE.get(mt, mt)

        # PAYLOAD_DROP param isimleri uyarla (TARGET_DROP artık TEKNOFEST_TASK2'ye
        # normalize edildiği için tek değeri kontrol etmek yeterli).
        if params.get("mission_type") == "TEKNOFEST_TASK2":
            if "drop_altitude_m" in params and "drop_duration_s" not in params:
                params["drop_duration_s"] = 0.5
            if "drop_slot" not in params:
                params["drop_slot"] = 1

        return params

    # ── Public slot'lar ────────────────────────────────────────────────────────

    @pyqtSlot(dict)
    def send_ai_mission(self, raw_params: dict) -> None:
        """GCS'ten gelen parametreleri normalize ederek kuyruğa ekler."""
        params = self._normalize_params(dict(raw_params))
        self._enqueue(params)

    @pyqtSlot(dict, list)
    def send_mission_with_waypoints(self, raw_params: dict, waypoints: list) -> None:
        """Parametreler + waypoint listesini normalize edip kuyruğa ekler."""
        params = self._normalize_params(dict(raw_params))

        if waypoints:
            params["waypoints"] = list(waypoints)
        elif "waypoints" not in params:
            params["waypoints"] = []

        self._enqueue(params)

    @pyqtSlot()
    def send_start_command(self) -> None:
        """RPi'ye görev başlatma komutu gönderir."""
        self._enqueue({"action": "start"})

    @pyqtSlot()
    def send_abort(self) -> None:
        """RPi'ye görev iptal komutu gönderir (yüksek öncelik — kuyruğu beklemez)."""
        self._enqueue({"action": "abort"}, high_priority=True)

    @pyqtSlot()
    def send_pilot_devir_onay(self) -> None:
        """Operatörün PİLOTA DEVİR onayını araca iletir — yüksek öncelik.

        NEDEN AYRI ONAY: devir hedefi LOITER'dır ve ArduCopter'da LOITER
        PİLOT kipidir — gaz kolu doğrudan alçalma hızını sürer. Onay gelene
        kadar araç GUIDED'da (çubuklar yok sayılır) pozisyonunu tutar; bu
        adım pilotun bilgilendirilip gaz kolunu ORTALAMASI için vardır.
        """
        self._enqueue({"action": "pilot_devir_onay"}, high_priority=True)

    @pyqtSlot()
    def send_drop_now(self) -> None:
        """Manuel yük bırakma (Görev 2/3 manuel mod) — yüksek öncelik."""
        self._enqueue({"action": "drop_now"}, high_priority=True)

    @pyqtSlot(str, str, int, bool)
    def send_servo_command(self, target: str, state: str = "", angle: int = 0,
                            hold_torque: bool = False) -> None:
        """Manuel servo ve gimbal kontrolünü Companion PC'ye iletir.

        hold_torque: yalnızca grabber ailesi (grabber/grabber_left/grabber_right)
        için anlamlıdır — limit switch'e ulaşınca sürekli-dönüş servoyu tam nötr
        (pwm_stop) yerine çok küçük bir tutma PWM'inde bırakır (bkz. companion
        hardware/servo_controller.py _spin_grabber_until_switch). Diğer
        hedeflerde (red_drop/blue_drop/gimbal) yok sayılır.

        target red_drop/blue_drop VE state="" iken: 'angle' parametresi ham
        PWM (µs) olarak yorumlanır — manuel yükleme slider'ı için (bkz.
        dashboard_view.py _send_red/blue_drop_pwm_cmd). Normal Aç/Yük Al
        butonları her zaman state="open"/"close" gönderir, bu yol o zaman
        DEVREYE GİRMEZ.
        """
        cmd: dict = {"action": "servo_command", "target": target}
        if target == "gimbal":
            cmd["angle"] = angle
        elif target in ("red_drop", "blue_drop") and not state:
            cmd["pwm"] = angle
        else:
            cmd["state"] = state
            if target in ("grabber", "grabber_left", "grabber_right"):
                cmd["hold_torque"] = bool(hold_torque)
        self._enqueue(cmd, high_priority=True)

    @pyqtSlot(int)
    def send_battery_config(self, config_data: int) -> None:
        """Top-bar'dan gönderilen batarya konfigürasyonu."""
        self._enqueue({"action": "battery_config", "data": config_data})

    # ── İç yardımcılar ─────────────────────────────────────────────────────────

    def _enqueue(self, item: dict, high_priority: bool = False) -> None:
        """
        Kuyruğa güvenli ekleme (PriorityQueue — bkz. __init__ notu).
        FIX P0-1: Queue dolu ise kullanıcıya error_occurred sinyali gönderir.
        high_priority=True → abort/servo/drop_now gibi komutlar kuyrukta
        BEKLEYEN normal komutların ÖNÜNE geçer (dolu olsun olmasın).
        """
        prio = self._PRIO_HIGH if high_priority else self._PRIO_NORMAL
        entry = (prio, next(self._seq_counter), item)
        try:
            self._send_queue.put_nowait(entry)
            logger.info("Kuyruğa eklendi (öncelik=%s): action=%s mission=%s",
                        "yüksek" if high_priority else "normal",
                        item.get("action"), item.get("mission_type"))
        except queue.Full:
            if high_priority:
                # Yüksek öncelikli komut için yer aç — PriorityQueue'da "en eski
                # düşük öncelikli"yi hedeflemek yerine mevcut en-önde-bekleyeni
                # çıkarıp yerine bunu koyuyoruz (dolu olmak zaten dejenere bir
                # durum; kritik olan komutun GİRMESİ, kimin çıktığı değil).
                try:
                    self._send_queue.get_nowait()
                    self._send_queue.put_nowait(entry)
                    logger.warning("Kuyruk dolu — yer açılıp yüksek öncelikli komut eklendi")
                    return
                except queue.Empty:
                    pass
            msg = (
                f"Görev kuyruğu dolu (max 10). "
                f"'{item.get('mission_type', item.get('action', '?'))}' gönderilemedi."
            )
            logger.error(msg)
            self.error_occurred.emit(msg)

    @pyqtSlot(str, bool)
    def send_run_test(self, spec: str, sim_mode: bool) -> None:
        """İHA test koşucusunu başlat: {"action":"run_test", "test":"all", "sim":true}"""
        self._enqueue({"action": "run_test", "test": spec, "sim": sim_mode})

    @pyqtSlot()
    def send_abort_test(self) -> None:
        """Çalışan İHA test paketini durdur: {"action":"abort_test"}.

        NEDEN: YKİ test panelinde "⏹ Durdur" butonu vardı ama İHA tarafında
        karşılığı YOKTU — başlayan test hiçbir şekilde durdurulamıyordu.
        Yüksek öncelik: durdurma komutu kuyrukta bekleyen normal komutları
        beklememeli (abort/drop_now ile aynı mantık).
        NOT: Butona bağlama (iha_test_panel_view.abort_test_requested →
        bu slot) main_window.py işidir — burada YAPILMADI.
        """
        self._enqueue({"action": "abort_test"}, high_priority=True)

    def stop(self) -> None:
        # SÜRESİZ wait() KALDIRILDI (2026-09-06 denetimde bulundu — aynı
        # sınıf hata `ntrip_worker.py`'de 2026-09-03'te bulunup düzeltilmişti,
        # bu dosya o taramadan kaçmış). `closeEvent()` (GUI thread) burayı
        # çağırır; companion yavaş/yanıt vermezse (izole testle 19.53 s
        # bloke olduğu doğrulandı) TÜM YKİ arayüzü donuyordu. Kardeş
        # worker'ların "donmuşsa devam et" bandıyla aynı: 5000 ms.
        self.requestInterruption()
        self.wait(5000)
