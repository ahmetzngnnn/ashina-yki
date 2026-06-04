"""
global_state.py — Singleton Global Durum Yöneticisi
QReadWriteLock ile thread-safe get/set metodları.
Worker'lar doğrudan attribute erişimi yapmaz; yalnızca get_*/set_* kullanır.
"""
from PyQt6.QtCore import QReadWriteLock


class GlobalState:
    _instance: "GlobalState | None" = None
    _lock: QReadWriteLock
    _auth_token: str
    _rbac_role: int
    _connection_status: bool

    def __new__(cls) -> "GlobalState":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._lock = QReadWriteLock()
            cls._instance._auth_token = ""
            cls._instance._rbac_role = 0
            cls._instance._connection_status = False
        return cls._instance

    # ── auth_token ──────────────────────────────────────────────────────────
    def get_auth_token(self) -> str:
        self._lock.lockForRead()
        try:
            return self._auth_token
        finally:
            self._lock.unlock()

    def set_auth_token(self, value: str) -> None:
        self._lock.lockForWrite()
        try:
            self._auth_token = value
        finally:
            self._lock.unlock()

    # ── rbac_role ────────────────────────────────────────────────────────────
    def get_rbac_role(self) -> int:
        self._lock.lockForRead()
        try:
            return self._rbac_role
        finally:
            self._lock.unlock()

    def set_rbac_role(self, value: int) -> None:
        self._lock.lockForWrite()
        try:
            self._rbac_role = value
        finally:
            self._lock.unlock()

    # ── connection_status ────────────────────────────────────────────────────
    def get_connection_status(self) -> bool:
        self._lock.lockForRead()
        try:
            return self._connection_status
        finally:
            self._lock.unlock()

    def set_connection_status(self, value: bool) -> None:
        self._lock.lockForWrite()
        try:
            self._connection_status = value
        finally:
            self._lock.unlock()


# Modül düzeyinde erişim kolaylığı
state = GlobalState()
