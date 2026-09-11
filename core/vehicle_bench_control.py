"""
core/vehicle_bench_control.py — GercekGorevPaneli "Testi Başlat" uzak tetikleme
================================================================================
NEDEN (2026-09-04 operatör talebi): "Araç Pi'sinde çalıştırılacak komutlar"
kutusu operatörü ayrı bir terminal açıp elle SSH/komut yazmaya zorluyordu.
Araçta ashina-iha.service AYNI FC seri portuna bağlı olduğu için
human_guided_bench.py'den önce durdurulmalı (bkz. saha_testi_view.py
_GOREV_UYARI_METNI) — bu modül o iki adımı (durdur → betiği arka planda
başlat) ve tersini (betiği kapat → servisi geri başlat) SSH üzerinden yapar,
GercekGorevPaneli'deki butonlar bunu tetikler.

GÜVENLİK: parola YALNIZCA çağıran panelin bellekteki alanından okunur, bu
modül onu hiçbir dosyaya/ayara YAZMAZ. SSH bağlantısı `paramiko` ile
doğrudan (harici `sshpass` ikili dosyasına ihtiyaç yok).
"""
from __future__ import annotations

import logging
import shlex

from PyQt6.QtCore import QThread, pyqtSignal

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

logger = logging.getLogger(__name__)

SSH_TIMEOUT_S = 8.0
IHA_DIR = "/home/ash/ashina_iha"
PID_FILE = "/tmp/ashina_human_guided_bench.pid"
LOG_FILE = "/tmp/ashina_human_guided_bench.log"
SERVICE = "ashina-iha"


def _connect(host: str, user: str, password: str) -> "paramiko.SSHClient":
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host, username=user, password=password,
        timeout=SSH_TIMEOUT_S, banner_timeout=SSH_TIMEOUT_S,
        auth_timeout=SSH_TIMEOUT_S, look_for_keys=False, allow_agent=False,
    )
    return client


def _run(client: "paramiko.SSHClient", command: str,
         sudo_password: str | None = None,
         timeout: float = SSH_TIMEOUT_S) -> tuple[int, str, str]:
    """Uzak komut çalıştırır. `sudo_password` verilirse `sudo -S` parolayı stdin'den alır."""
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if sudo_password is not None:
        stdin.write(sudo_password + "\n")
        stdin.flush()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


class VehicleBenchStartWorker(QThread):
    """Araçta ashina-iha.service'i durdurur, human_guided_bench.py'yi arka planda başlatır."""

    basarili = pyqtSignal(str)
    hata = pyqtSignal(str)
    ilerleme = pyqtSignal(str)

    def __init__(self, host: str, password: str, renk: str,
                 egim_kapisi_kapali: bool = False,
                 hedef_irtifa: float | None = None,
                 user: str = "ash", parent=None) -> None:
        super().__init__(parent)
        self._host = host
        self._password = password
        self._renk = renk
        self._egim_kapali = egim_kapisi_kapali
        self._hedef_irtifa = hedef_irtifa
        self._user = user

    def run(self) -> None:
        if not PARAMIKO_AVAILABLE:
            self.hata.emit("paramiko kurulu değil — requirements-linux.txt'ten kurun")
            return
        try:
            self.ilerleme.emit(f"{self._host}'a bağlanılıyor…")
            client = _connect(self._host, self._user, self._password)
        except Exception as exc:
            self.hata.emit(f"SSH bağlantısı başarısız: {exc}")
            return
        try:
            # NEDEN önce eskiyi öldür (2026-09-04 sahada yakalandı): butona
            # ikinci kez basılınca ikinci kopya açılıyordu. İlk kopya kamerayı
            # ve 8090'ı tutmaya devam ettiği için ikincisi
            # "Failed to acquire camera: Device or resource busy" ile ÇÖKÜYOR —
            # ama çökmeden ÖNCE aracı ARM edip GUIDED'a alıyordu; süreç ölünce
            # araç takeoff olmadığı için otomatik disarm oluyordu. Ayrıca iki
            # kopya AYNI FC seri portunu açar (belgelenmiş heartbeat bozulması).
            # NEDEN "p[y]" (2026-09-05 denetimde bulundu): düz "human_guided_bench.py"
            # deseni bu KOMUTUN KENDİSİNİ çalıştıran SSH kabuğunun da komut satırında
            # geçiyor — pgrep/pkill kendini bulup (a) bekleme döngüsünü hep en uzun
            # süreye zorluyor (b) kendi kabuğunu SIGTERM/SIGKILL ile öldürebiliyordu.
            # Köşeli parantez deseni gerçek python sürecinde eşleşir, bu komutun
            # kendi metninde eşleşmez (aynı numara yki_yeniden_baslat.py'de de var).
            self.ilerleme.emit("önceki bench kopyası (varsa) kapatılıyor…")
            _run(client,
                 f"pkill -f human_guided_bench.p[y] 2>/dev/null; "
                 f"for i in 1 2 3 4 5; do pgrep -f human_guided_bench.p[y] >/dev/null || break; sleep 1; done; "
                 f"pkill -9 -f human_guided_bench.p[y] 2>/dev/null; rm -f {PID_FILE}; true",
                 timeout=15.0)

            self.ilerleme.emit(f"{SERVICE} durduruluyor (FC portu boşalıyor)…")
            rc, out, err = _run(client, f"sudo -S systemctl stop {SERVICE}",
                                 sudo_password=self._password)
            if rc != 0:
                self.hata.emit(f"Servis durdurulamadı (rc={rc}): {(err or out).strip()}")
                return

            # --pencere yok : araç Pi'si HEADLESS (monitör yok). Varsayılan
            #   "var" cv2.imshow() çağırır ve DISPLAY olmadan döngüyü çökertir;
            #   panel zaten aynı kareleri MJPEG ile ağdan izliyor.
            # --evet-onayladim : betiğin terminal istemi (input() ile "PERVANE
            #   YOK" yazdırma) arka planda EOF alıp çıkar. Onay KALDIRILMADI,
            #   YKİ'ye TAŞINDI: PervaneKapisi kutusu + başlatma öncesi soru
            #   diyaloğu aynı beyanı operatörden alıyor (bkz. saha_testi_view).
            args = [f"{IHA_DIR}/venv/bin/python3", "tools/human_guided_bench.py",
                    "--renk", self._renk, "--pencere", "yok", "--evet-onayladim"]
            if self._egim_kapali:
                args += ["--egim-kapisi", "kapali"]
            if self._hedef_irtifa is not None:
                args += ["--hedef-irtifa", str(self._hedef_irtifa)]
            cmd_str = " ".join(shlex.quote(a) for a in args)

            self.ilerleme.emit("bench betiği başlatılıyor…")
            baslat_cmd = (
                f"cd {IHA_DIR} && "
                f"setsid nohup {cmd_str} > {LOG_FILE} 2>&1 < /dev/null & "
                f"echo $! > {PID_FILE}; sleep 1; cat {PID_FILE}"
            )
            rc, out, err = _run(client, baslat_cmd)
            pid = out.strip()
            if not pid.isdigit():
                self.hata.emit(f"Betik başlatılamadı — çıktı: {(out + err).strip() or '(boş)'}")
                return

            # NEDEN hayatta-mı kontrolü (2026-09-04, sahada yakalandı): yalnız
            # PID'e bakmak YETMEZ — kabuk süreci çatallar, PID oluşur, betik bir
            # saniye sonra çökse bile panel "başarılı" derdi. Gerçek olayda araçtaki
            # dosya null byte'larla bozulmuştu (SyntaxError) ve operatör hatayı
            # göremeden yalnızca "bağlanmıyor" gördü; servis de durmuş kalmıştı.
            self.ilerleme.emit("betik ayakta mı doğrulanıyor…")
            rc, out, err = _run(
                client,
                f"for i in 1 2 3 4 5 6; do kill -0 {pid} 2>/dev/null || "
                f"{{ echo OLDU; tail -n 15 {LOG_FILE}; exit 0; }}; sleep 1; done; echo YASIYOR",
                timeout=20.0,
            )
            if out.startswith("OLDU"):
                gerekce = out[len("OLDU"):].strip() or "(log boş)"
                self.hata.emit(
                    f"Betik başladı ama HEMEN ÇÖKTÜ. Araçtaki hata:\n{gerekce}\n"
                    f"⚠ {SERVICE} DURDURULMUŞ durumda — “Durdur ve Uçuş Servisini "
                    f"Geri Yükle” ile geri alın."
                )
                return
            self.basarili.emit(f"Başlatıldı ve ayakta (PID {pid}) — video birazdan bağlanır")
        except Exception as exc:
            self.hata.emit(f"Hata: {exc}")
        finally:
            client.close()


class VehicleBenchStopWorker(QThread):
    """human_guided_bench.py'yi durdurur ve ashina-iha.service'i geri başlatır."""

    basarili = pyqtSignal(str)
    hata = pyqtSignal(str)
    ilerleme = pyqtSignal(str)

    def __init__(self, host: str, password: str, user: str = "ash", parent=None) -> None:
        super().__init__(parent)
        self._host = host
        self._password = password
        self._user = user

    def run(self) -> None:
        if not PARAMIKO_AVAILABLE:
            self.hata.emit("paramiko kurulu değil")
            return
        try:
            self.ilerleme.emit(f"{self._host}'a bağlanılıyor…")
            client = _connect(self._host, self._user, self._password)
        except Exception as exc:
            self.hata.emit(f"SSH bağlantısı başarısız: {exc}")
            return
        try:
            self.ilerleme.emit("bench betiği durduruluyor…")
            _run(client,
                 f"[ -f {PID_FILE} ] && kill $(cat {PID_FILE}) 2>/dev/null; "
                 f"pkill -f human_guided_bench.p[y] 2>/dev/null; rm -f {PID_FILE}; true")
            self.ilerleme.emit(f"{SERVICE} yeniden başlatılıyor…")
            rc, out, err = _run(client, f"sudo -S systemctl start {SERVICE}",
                                 sudo_password=self._password)
            if rc != 0:
                self.hata.emit(f"Servis başlatılamadı (rc={rc}): {(err or out).strip()}")
                return
            self.basarili.emit(f"{SERVICE} yeniden başlatıldı — uçuş bilgisayarı normale döndü")
        except Exception as exc:
            self.hata.emit(f"Hata: {exc}")
        finally:
            client.close()
