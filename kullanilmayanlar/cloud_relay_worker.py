"""
cloud_relay_worker.py — MQTT / HQ Bağlantısı
paho-mqtt + TLS; HMAC-SHA256 komut doğrulama
Gelen her TAKEOVER komutu imzasız ise loglanır ve görmezden gelinir.
"""
import json
import logging
import socket
import ssl
import time
import hmac
import hashlib
from PyQt6.QtCore import QThread, pyqtSignal, pyqtSlot

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    logging.warning("paho-mqtt kurulu değil — Cloud relay devre dışı")

logger = logging.getLogger(__name__)

TELEMETRY_HZ = 1


class CloudRelayWorker(QThread):
    hq_override_activated = pyqtSignal()
    connection_changed = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        broker: str = "localhost",
        port: int = 8883,
        drone_id: str = "001",
        ca_certs: str = "",
        hmac_secret: bytes = b"",
        parent=None,
    ):
        super().__init__(parent)
        self._broker = broker
        self._port = port
        self._drone_id = drone_id
        self._ca_certs = ca_certs
        self._hmac_secret = hmac_secret
        self._telemetry_buffer: dict = {}
        self._client = None
        self.heartbeat_counter: int = 0
        self._gcs_hostname = socket.gethostname()
        self._gcs_info: dict = {"connected": False, "operator": "—", "role": 0}
        self._gcs_tick: int = 0

    def update_telemetry(self, data: dict) -> None:
        self._telemetry_buffer = data

    def update_gcs_info(self, connected: bool, operator: str, role: int) -> None:
        """MAVLink bağlantı durumu ve operatör bilgisini günceller."""
        self._gcs_info = {"connected": connected, "operator": operator, "role": role}

    def reconfigure(
        self,
        broker: str,
        port: int,
        drone_id: str,
        ca_certs: str,
        hmac_secret: bytes,
    ) -> None:
        """Çalışma zamanında MQTT bağlantı ayarlarını günceller; worker yeniden başlar."""
        self._broker      = broker
        self._port        = port
        self._drone_id    = drone_id
        self._ca_certs    = ca_certs
        self._hmac_secret = hmac_secret
        # Mevcut bağlantıyı kes — run() döngüsü otomatik yeniden bağlanır
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
        logger.info("CloudRelay yeniden yapılandırıldı: %s:%s drone=%s", broker, port, drone_id)

    def _verify_hmac(self, payload: bytes, signature: str) -> bool:
        expected = hmac.new(self._hmac_secret, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                self._run_loop()
            except Exception as exc:
                logger.error("CloudRelayWorker exception: %s", exc)
                self.error_occurred.emit(str(exc))
                self.connection_changed.emit(False)
                time.sleep(2)

    def _run_loop(self) -> None:
        if not MQTT_AVAILABLE:
            time.sleep(1)
            return

        client = mqtt.Client(
            client_id=f"gcs_{self._drone_id}",
            protocol=mqtt.MQTTv5,
        )
        self._client = client

        if self._ca_certs:
            client.tls_set(
                ca_certs=self._ca_certs,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )

        cmd_topic = f"hq/fleet/drone_{self._drone_id}/commands"
        tel_topic = f"hq/fleet/drone_{self._drone_id}/telemetry"

        def on_connect(c, userdata, flags, rc, props=None):
            if rc == 0:
                logger.info("MQTT bağlandı: %s:%s", self._broker, self._port)
                c.subscribe(cmd_topic, qos=1)
                self.connection_changed.emit(True)
            else:
                logger.warning("MQTT bağlantı reddedildi: rc=%s", rc)
                self.connection_changed.emit(False)

        def on_disconnect(c, userdata, rc, props=None):
            logger.warning("MQTT bağlantısı kesildi: rc=%s", rc)
            self.connection_changed.emit(False)

        def on_message(c, userdata, msg):
            try:
                payload = msg.payload
                data = json.loads(payload)
                sig = data.pop("hmac_sig", "")

                if not self._hmac_secret:
                    logger.warning("HMAC secret tanımlı değil — komut reddedildi")
                    return

                payload_to_verify = json.dumps(data, sort_keys=True).encode()
                if not self._verify_hmac(payload_to_verify, sig):
                    logger.warning("HMAC doğrulama başarısız — komut reddedildi: %s", msg.topic)
                    return

                if data.get("type") == "TAKEOVER":
                    from core.global_state import state
                    state.set_hq_override(True)
                    self.hq_override_activated.emit()
                    logger.info("HQ TAKEOVER komutu doğrulandı ve işlendi")
            except (json.JSONDecodeError, Exception) as exc:
                logger.error("MQTT mesajı işlenemedi: %s", exc)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        client.connect_async(self._broker, self._port, keepalive=60)
        client.loop_start()

        tick = 0
        try:
            while not self.isInterruptionRequested():
                self.heartbeat_counter += 1
                time.sleep(1)
                tick += 1
                if tick >= TELEMETRY_HZ and self._telemetry_buffer:
                    tick = 0
                    try:
                        payload = json.dumps(self._telemetry_buffer)
                        client.publish(tel_topic, payload, qos=0)
                    except Exception as exc:
                        logger.warning("Telemetri yayını başarısız: %s", exc)

                # GCS durum heartbeat — her 5 saniyede bir
                self._gcs_tick += 1
                if self._gcs_tick >= 5:
                    self._gcs_tick = 0
                    try:
                        gcs_status = {
                            "hostname":          self._gcs_hostname,
                            "drone_id":          self._drone_id,
                            "mavlink_connected": self._gcs_info["connected"],
                            "operator":          self._gcs_info["operator"],
                            "role":              self._gcs_info["role"],
                            "ts":                int(time.time()),
                        }
                        gcs_topic = f"hq/gcs/{self._gcs_hostname}/status"
                        client.publish(gcs_topic, json.dumps(gcs_status), qos=0)
                    except Exception as exc:
                        logger.warning("GCS durum yayını başarısız: %s", exc)
        finally:
            client.loop_stop()
            client.disconnect()

    def stop(self) -> None:
        self.requestInterruption()
        self.wait()
