"""
auth_manager.py — JWT Token Yönetimi ve RBAC
Token bellekte tutulur (disk yazılmaz).
Expiry QTimer: Her 60 sn kontrol; kalan süre < 5 dk → refresh endpoint çağrılır.
RBAC seviyeleri: VIEWER=1, OPERATOR=2, ADMIN=3
"""
import hashlib
import hmac
import json
import logging
import os
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.global_state import state
from core import local_storage

logger = logging.getLogger(__name__)

ROLE_VIEWER = 1
ROLE_OPERATOR = 2
ROLE_ADMIN = 3

# 2026-08-26 GÜVENLİK DÜZELTMESİ: önceden tuzsuz SHA256 kullanılıyordu
# (rainbow-table/GPU brute-force'a açık, aynı şifre = aynı hash). Artık
# kullanıcı başına rastgele tuzlu PBKDF2-HMAC-SHA256. Format kendini
# tanımlıyor ("pbkdf2_sha256$iterasyon$tuz_hex$hash_hex") ve TEK bir
# password_hash TEXT sütununda saklanıyor — şema değişikliği gerekmedi.
_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Hem yeni tuzlu PBKDF2 formatını hem de ESKİ tuzsuz SHA256 hash'lerini
    (geriye dönük uyumluluk — mevcut kurulumlardaki kullanıcılar kilitlenmesin)
    doğrular. Çağıran (authenticate_local), eski formatla eşleşen bir girişten
    sonra hash'i otomatik olarak yeni formata YÜKSELTİR."""
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iters_s, salt_hex, hash_hex = stored.split("$", 3)
            salt = bytes.fromhex(salt_hex)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters_s))
            return hmac.compare_digest(dk.hex(), hash_hex)
        except Exception:
            return False
    # Eski format: tuzsuz sha256 hex digest (64 karakter)
    legacy = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(legacy, stored)


def is_legacy_hash(stored: str) -> bool:
    return not stored.startswith("pbkdf2_sha256$")


class AuthManager(QObject):
    token_refreshed = pyqtSignal(str)
    logout_required = pyqtSignal(str)   # mesaj ile
    # RBAC rolü DEĞİŞTİĞİNDE yayılır. Bunsuz MainWindow rol değişimini
    # göremiyordu; sekme kilitleri yalnız açılışta uygulanıyor, oturum
    # içinde rol düşse bile yetkili sekmeler açık kalıyordu.
    role_changed = pyqtSignal(int)      # yeni rol

    def __init__(self, db_conn, hq_refresh_url: str = "", parent=None):
        super().__init__(parent)
        self._db_conn = db_conn
        self._hq_refresh_url = hq_refresh_url
        self._token_expiry: float = 0.0

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(60_000)  # 60 sn
        self._refresh_timer.timeout.connect(self._check_token_expiry)

    def start(self) -> None:
        self._refresh_timer.start()

    def stop(self) -> None:
        self._refresh_timer.stop()

    # ── Yerel kimlik doğrulama ───────────────────────────────────────────────
    def authenticate_local(self, username: str, password: str) -> bool:
        row = local_storage.get_user(self._db_conn, username)
        if row is None:
            return False
        stored = row["password_hash"]
        if not verify_password(password, stored):
            return False
        if is_legacy_hash(stored):
            # Başarılı girişte eski tuzsuz hash'i sessizce yeni tuzlu formata
            # yükselt — kullanıcı şifresini değiştirmeden güvenlik iyileşir.
            try:
                local_storage.update_user_password_hash(
                    self._db_conn, username, hash_password(password))
                logger.info("Kullanıcı %s: eski parola hash'i yükseltildi (PBKDF2)", username)
            except Exception as exc:
                logger.warning("Parola hash yükseltmesi başarısız (%s): %s", username, exc)
        state.set_rbac_role(int(row["role"]))
        self.role_changed.emit(int(row["role"]))
        state.set_auth_token(f"local:{username}")
        logger.info("Yerel giriş başarılı: %s (rol=%s)", username, row["role"])
        return True

    def authenticate_jwt(self, token: str, role: int, expiry: float) -> None:
        state.set_auth_token(token)
        state.set_rbac_role(role)
        self.role_changed.emit(int(role))
        self._token_expiry = expiry
        logger.info("JWT ile giriş yapıldı; expiry=%s", expiry)

    def logout(self) -> None:
        state.set_auth_token("")
        state.set_rbac_role(0)
        self.role_changed.emit(0)
        self._token_expiry = 0.0
        self._refresh_timer.stop()
        logger.info("Kullanıcı çıkış yaptı")

    def is_logged_in(self) -> bool:
        return bool(state.get_auth_token())

    def get_role(self) -> int:
        return state.get_rbac_role()

    # ── Token yenileme ───────────────────────────────────────────────────────
    def _check_token_expiry(self) -> None:
        if not self._token_expiry:
            return
        remaining = self._token_expiry - time.time()
        if remaining < 300:  # 5 dakikadan az kaldıysa
            self._do_refresh()

    def _do_refresh(self) -> None:
        if not self._hq_refresh_url:
            return
        try:
            import urllib.request
            token = state.get_auth_token()
            req = urllib.request.Request(
                self._hq_refresh_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                new_token = data["access_token"]
                new_expiry = data["expires_at"]
                state.set_auth_token(new_token)
                self._token_expiry = float(new_expiry)
                self.token_refreshed.emit(new_token)
                logger.info("JWT token yenilendi")
        except Exception as exc:
            logger.error("Token refresh başarısız: %s", exc)
            # Uçuş devam ediyorsa yalnızca cloud bağlantısı kesilir
            flying = state.get_connection_status()
            self.logout_required.emit(
                "Token yenilenemedi. Lütfen yeniden giriş yapın."
                if not flying else
                "Token yenilenemedi. Cloud bağlantısı kesildi."
            )
