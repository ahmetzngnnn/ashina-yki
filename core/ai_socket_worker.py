"""
ai_socket_worker.py — Air Tarafı Görev Parametre Köprüsü
Protokol: Plain TCP JSON, Port 5601 (MissionReceiver ile uyumlu)

Her satır bir JSON mesajıdır (\n ile sonlandırılır).
"""
import json
import logging
import queue
import socket
import time
from PyQt6.QtCore import QThread, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)

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
    "TARGET_DROP":         "TARGET_DROP",       # korunur
    "INFINITY_FLIGHT":     "INFINITY_FLIGHT",   # korunur
    "AI_DETECTION":        "AI_DETECTION",      # korunur
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
                 parent=None):
        super().__init__(parent)
        self._host = host
        self._port = port
        # FIX P1-4: Timeout değerleri artık parametre — gcs_config'den beslenebilir
        self._connect_timeout  = connect_timeout
        self._response_timeout = response_timeout
        self._send_queue: queue.Queue = queue.Queue(maxsize=10)
        self.heartbeat_counter: int = 0

    def run(self) -> None:
        while not self.isInterruptionRequested():
            self.heartbeat_counter += 1
            try:
                mission = self._send_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                self._send_mission(mission)
            except Exception as exc:
                logger.error("Görev gönderilemedi: %s", exc)
                self.error_occurred.emit(str(exc))

    def _send_mission(self, mission: dict) -> None:
        payload = json.dumps(mission, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            with socket.create_connection(
                (self._host, self._port), timeout=self._connect_timeout
            ) as sock:
                sock.sendall(payload)
                sock.settimeout(self._response_timeout)
                raw = b""
                while b"\n" not in raw:
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
                        # FIX P0-2: Rejection → kullanıcıya bildir
                        msg = resp.get("message", "Companion görevi reddetti")
                        logger.warning("Görev reddedildi: %s", msg)
                        self.mission_rejected.emit(msg)
                        self.error_occurred.emit(f"Companion: {msg}")
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

        # PAYLOAD_DROP param isimleri uyarla
        if params.get("mission_type") in ("TEKNOFEST_TASK2", "TARGET_DROP"):
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

    @pyqtSlot(str, str, int)
    def send_servo_command(self, target: str, state: str = "", angle: int = 0) -> None:
        """Manuel servo ve gimbal kontrolünü Companion PC'ye iletir."""
        cmd: dict = {"action": "servo_command", "target": target}
        if target == "gimbal":
            cmd["angle"] = angle
        else:
            cmd["state"] = state
        self._enqueue(cmd, high_priority=True)

    @pyqtSlot(int)
    def send_battery_config(self, config_data: int) -> None:
        """Top-bar'dan gönderilen batarya konfigürasyonu."""
        self._enqueue({"action": "battery_config", "data": config_data})

    # ── İç yardımcılar ─────────────────────────────────────────────────────────

    def _enqueue(self, item: dict, high_priority: bool = False) -> None:
        """
        Kuyruğa güvenli ekleme.
        FIX P0-1: Queue dolu ise kullanıcıya error_occurred sinyali gönderir.
        high_priority=True ise önce en eski non-priority öğeyi atmayı dener.
        """
        try:
            self._send_queue.put_nowait(item)
            logger.info("Kuyruğa eklendi: action=%s mission=%s",
                        item.get("action"), item.get("mission_type"))
        except queue.Full:
            if high_priority:
                # Yüksek öncelikli komutlar (servo, acil) için kuyruğu temizle
                try:
                    self._send_queue.get_nowait()   # en eski öğeyi çıkar
                    self._send_queue.put_nowait(item)
                    logger.warning("Kuyruk dolu — en eski öğe atılarak yer açıldı")
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

    def stop(self) -> None:
        self.requestInterruption()
        self.wait()
