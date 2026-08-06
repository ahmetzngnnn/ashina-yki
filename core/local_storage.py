"""
local_storage.py — SQLite Yöneticisi
Her worker kendi bağlantısını açar (check_same_thread=False).
Yazma işlemleri QMutex ile serialize edilir.
"""
import logging
import os
import sqlite3
import time
from pathlib import Path
from PyQt6.QtCore import QMutex, QMutexLocker, pyqtSignal, QObject

logger = logging.getLogger(__name__)

# Yazılabilir uygulama veri klasörü — Program Files altına yazılamaz
_APP_DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ASHINA-YKI"
_APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = _APP_DATA_DIR / "ashina_yki.db"
WRITE_MUTEX = QMutex()

MAX_RETRY = 3
BACKOFF_BASE = 1.0  # saniye


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL mode: okuma-yazma çakışmasını önler, eş zamanlı erişimde performans artar
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")  # fsync overhead'i azalt
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    with QMutexLocker(WRITE_MUTEX):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS parameters (
                name TEXT PRIMARY KEY,
                value REAL NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS log_files (
                id INTEGER PRIMARY KEY,
                drone_id TEXT,
                size INTEGER,
                time_utc INTEGER,
                local_path TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS test_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_start INTEGER NOT NULL,
                ts_end INTEGER,
                test_type TEXT NOT NULL,
                result TEXT,
                params_json TEXT,
                report_path TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS flight_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_start INTEGER NOT NULL,
                ts_end INTEGER,
                session_type TEXT NOT NULL,
                max_alt REAL DEFAULT 0,
                max_speed REAL DEFAULT 0,
                total_dist REAL DEFAULT 0,
                duration_s INTEGER DEFAULT 0,
                params_json TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS flight_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_start INTEGER NOT NULL,
                ts_end INTEGER,
                mission_type TEXT NOT NULL,
                max_speed REAL DEFAULT 0,
                min_voltage REAL DEFAULT 0,
                max_roll REAL DEFAULT 0,
                max_pitch REAL DEFAULT 0,
                json_path TEXT NOT NULL
            );
        """)
        conn.commit()
    logger.info("SQLite şema hazır: %s", DB_PATH)


# ── Parametre CRUD ──────────────────────────────────────────────────────────

def save_param(conn: sqlite3.Connection, name: str, value: float) -> None:
    ts = int(time.time())
    with QMutexLocker(WRITE_MUTEX):
        conn.execute(
            "INSERT OR REPLACE INTO parameters (name, value, updated_at) VALUES (?, ?, ?)",
            (name, value, ts),
        )
        conn.commit()


def save_params_batch(conn: sqlite3.Connection, params: dict) -> None:
    """300-800 parametreyi tek transaction'da yazar — bağlantıda kullan."""
    if not params:
        return
    ts = int(time.time())
    rows = [(name, value, ts) for name, value in params.items()]
    with QMutexLocker(WRITE_MUTEX):
        conn.executemany(
            "INSERT OR REPLACE INTO parameters (name, value, updated_at) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    logger.info("Toplu parametre kaydedildi: %d adet", len(rows))


def get_param(conn: sqlite3.Connection, name: str) -> float | None:
    row = conn.execute(
        "SELECT value FROM parameters WHERE name = ?", (name,)
    ).fetchone()
    return float(row["value"]) if row else None


def get_all_params(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT name, value FROM parameters").fetchall()
    return {r["name"]: float(r["value"]) for r in rows}


# ── Kullanıcı CRUD ───────────────────────────────────────────────────────────

def add_user(conn: sqlite3.Connection, username: str, password_hash: str, role: int = 1) -> None:
    with QMutexLocker(WRITE_MUTEX):
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, password_hash, role),
        )
        conn.commit()


def get_user(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()


def update_user_role(conn: sqlite3.Connection, username: str, role: int) -> None:
    with QMutexLocker(WRITE_MUTEX):
        conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
        conn.commit()


def delete_user(conn: sqlite3.Connection, username: str) -> None:
    with QMutexLocker(WRITE_MUTEX):
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()


def list_users(conn: sqlite3.Connection) -> list:
    return conn.execute("SELECT id, username, role FROM users").fetchall()


# ── Audit Log ────────────────────────────────────────────────────────────────

_AUDIT_MAX_ROWS = 5000   # fazlası otomatik silinir

def write_audit(conn: sqlite3.Connection, level: str, message: str) -> None:
    ts = int(time.time())
    with QMutexLocker(WRITE_MUTEX):
        conn.execute(
            "INSERT INTO audit_log (ts, level, message) VALUES (?, ?, ?)",
            (ts, level, message),
        )
        # Eski kayıtları temizle (AUDIT_MAX_ROWS aşıldıysa en eskiler silinir)
        conn.execute(
            "DELETE FROM audit_log WHERE id NOT IN "
            "(SELECT id FROM audit_log ORDER BY id DESC LIMIT ?)",
            (_AUDIT_MAX_ROWS,),
        )
        conn.commit()


def prune_audit_log(conn: sqlite3.Connection, keep_rows: int = _AUDIT_MAX_ROWS) -> int:
    """Audit log'u temizler, silinen satır sayısını döner."""
    with QMutexLocker(WRITE_MUTEX):
        cur = conn.execute(
            "DELETE FROM audit_log WHERE id NOT IN "
            "(SELECT id FROM audit_log ORDER BY id DESC LIMIT ?)",
            (keep_rows,),
        )
        conn.commit()
        return cur.rowcount


# ── Log dosyası kayıt ────────────────────────────────────────────────────────

def save_log_entry(conn: sqlite3.Connection, log_id: int, drone_id: str,
                   size: int, time_utc: int, local_path: str = "") -> None:
    with QMutexLocker(WRITE_MUTEX):
        conn.execute(
            "INSERT OR REPLACE INTO log_files (id, drone_id, size, time_utc, local_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (log_id, drone_id, size, time_utc, local_path),
        )
        conn.commit()


def list_log_entries(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT id, drone_id, size, time_utc, local_path FROM log_files ORDER BY id DESC"
    ).fetchall()


# ── Test Oturumları ──────────────────────────────────────────────────────────

def save_test_session(conn: sqlite3.Connection, test_type: str, result: str,
                      params_json: str, ts_start: int | None = None,
                      ts_end: int | None = None, report_path: str = "") -> int:
    now = int(time.time())
    ts_start = ts_start or now
    ts_end = ts_end or now
    with QMutexLocker(WRITE_MUTEX):
        cur = conn.execute(
            "INSERT INTO test_sessions (ts_start, ts_end, test_type, result, params_json, report_path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts_start, ts_end, test_type, result, params_json, report_path),
        )
        conn.commit()
        return cur.lastrowid


def list_test_sessions(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT id, ts_start, ts_end, test_type, result, params_json, report_path "
        "FROM test_sessions ORDER BY ts_start DESC"
    ).fetchall()


def get_test_session(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM test_sessions WHERE id = ?", (session_id,)
    ).fetchone()


# ── Uçuş Kayıtları ──────────────────────────────────────────────────────────

def save_flight_record(conn: sqlite3.Connection, session_type: str, max_alt: float,
                       max_speed: float, total_dist: float, duration_s: int,
                       params_json: str, ts_start: int | None = None,
                       ts_end: int | None = None) -> int:
    now = int(time.time())
    ts_start = ts_start or now
    ts_end = ts_end or now
    with QMutexLocker(WRITE_MUTEX):
        cur = conn.execute(
            "INSERT INTO flight_records (ts_start, ts_end, session_type, max_alt, max_speed, "
            "total_dist, duration_s, params_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts_start, ts_end, session_type, max_alt, max_speed, total_dist, duration_s, params_json),
        )
        conn.commit()
        return cur.lastrowid


def list_flight_records(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT id, ts_start, ts_end, session_type, max_alt, max_speed, total_dist, duration_s "
        "FROM flight_records ORDER BY ts_start DESC"
    ).fetchall()


def get_flight_record(conn: sqlite3.Connection, record_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM flight_records WHERE id = ?", (record_id,)
    ).fetchone()


# ── Uçuş Analizleri (Telemetri Kayıtları) ────────────────────────────────────

def save_flight_analysis(conn: sqlite3.Connection, ts_start: int, ts_end: int,
                         mission_type: str, max_speed: float, min_voltage: float,
                         max_roll: float, max_pitch: float, json_path: str) -> int:
    with QMutexLocker(WRITE_MUTEX):
        cur = conn.execute(
            "INSERT INTO flight_analysis (ts_start, ts_end, mission_type, max_speed, "
            "min_voltage, max_roll, max_pitch, json_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts_start, ts_end, mission_type, max_speed, min_voltage, max_roll, max_pitch, json_path),
        )
        conn.commit()
        return cur.lastrowid

def list_flight_analyses(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT id, ts_start, ts_end, mission_type, max_speed, min_voltage, max_roll, max_pitch, json_path "
        "FROM flight_analysis ORDER BY ts_start DESC"
    ).fetchall()

