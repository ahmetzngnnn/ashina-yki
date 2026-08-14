"""
main.py — ASHİNA YKİ Başlatma Dizilimi (Boot Sequence)
Spesifikasyon Bölüm 7'ye göre:
  1. GlobalState oluştur
  2. SQLite başlat / şema doğrula
  3. AuthManager + LoginWindow
  4. MainWindow + Worker'lar
  5. Watchdog başlat
  6. AudioTTS process başlat
  7. Worker thread'leri başlat
"""
import os
import sys
import signal
import logging
import multiprocessing
from pathlib import Path

# Yazılabilir uygulama veri klasörü (platforma göre — Win:%APPDATA%, Linux:XDG)
from core.apppaths import app_data_dir
_APP_DATA_DIR = app_data_dir()

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

# Global state ilk sıradadır

from core.global_state import state
from core import local_storage
from ui.auth_manager import AuthManager, hash_password
from ui.login_window import LoginWindow
from ui.main_window import MainWindow


def setup_logging() -> None:
    log_path = _APP_DATA_DIR / "ashina_yki.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_path), encoding="utf-8"),
        ],
    )


def ensure_default_admin(db_conn) -> None:
    """İlk çalıştırmada varsayılan admin kullanıcısı oluşturur."""
    if local_storage.get_user(db_conn, "admin") is None:
        local_storage.add_user(
            db_conn,
            username="admin",
            password_hash=hash_password("admin"),
            role=3,
        )
        logging.getLogger(__name__).info(
            "Varsayılan admin kullanıcısı oluşturuldu (admin/admin). Lütfen şifreyi değiştirin!"
        )


def main() -> None:
    # Windows'ta multiprocessing için zorunlu
    multiprocessing.freeze_support()

    # --sim: sahte worker'larla (gerçek İHA/SITL olmadan) başlat. Env üzerinden
    # gcs_config.mode'a geçer (property env'i her okumada kontrol eder).
    if "--sim" in sys.argv:
        os.environ["ASHINA_YKI_MODE"] = "sim"

    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("ASHİNA YKİ başlatılıyor... (mod: %s)",
                os.environ.get("ASHINA_YKI_MODE", "real"))

    # ── 1. Qt Uygulaması ─────────────────────────────────────────────────────
    app = QApplication(sys.argv)
    # Ctrl+C → Qt event loop'u temiz kapatır (traceback olmadan)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    app.setApplicationName("ASHİNA YKİ")
    app.setApplicationVersion("2.0")
    app.setOrganizationName("ASHİNA")
    # Cross-platform arayüz fontu: Windows'ta Segoe UI, Linux/RPi'de yaygın sans
    # fontlar (Noto/DejaVu); ilk bulunan kullanılır. Segoe UI Linux'ta YOKtur →
    # önceden sessiz sübstitüsyon UI hizalamasını bozabiliyordu.
    _ui_font = QFont()
    _ui_font.setPointSize(10)
    _ui_font.setFamilies([
        "Segoe UI",          # Windows
        "Noto Sans", "DejaVu Sans", "Cantarell", "Ubuntu",  # Linux/RPi
        "Helvetica Neue", "Arial", "sans-serif",             # macOS/genel
    ])
    app.setFont(_ui_font)
    app.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    # Tema (koyu/açık) — palet + QSS şablonu core/theme.py'de; kayıtlı seçim
    # settings.json'daki ui_theme'den gelir, Ayarlar ekranından değiştirilir.
    from core.gcs_config import config as _cfg
    from core import theme
    theme.apply(app, _cfg.ui_theme)

    # ── 2. SQLite Başlat ──────────────────────────────────────────────────────
    logger.info("SQLite başlatılıyor...")
    db_conn = local_storage.get_connection()
    local_storage.init_schema(db_conn)
    ensure_default_admin(db_conn)

    # ── 3. AuthManager ────────────────────────────────────────────────────────
    auth_manager = AuthManager(db_conn)
    auth_manager.start()

    # ── 4. Login Penceresi ────────────────────────────────────────────────────
    main_window: MainWindow | None = None
    kiosk_mode = os.environ.get("ASHINA_YKI_KIOSK") == "1"

    def on_login_success(role: int) -> None:
        nonlocal main_window
        logger.info("Giriş başarılı — rol: %s", role)
        main_window = MainWindow(db_conn, auth_manager)
        if kiosk_mode:
            main_window.showFullScreen()
        else:
            main_window.show()

    if kiosk_mode:
        logger.info("Kiosk modu aktif — giriş ekranı atlanıyor, admin olarak otomatik giriş.")
        auth_manager.authenticate_local("admin", "admin")
        on_login_success(auth_manager.get_role())
    else:
        login = LoginWindow(auth_manager)
        login.login_successful.connect(on_login_success)
        login.show()

    # ── 5. Event Loop ─────────────────────────────────────────────────────────
    exit_code = app.exec()

    # ── Temizleme ─────────────────────────────────────────────────────────────
    auth_manager.stop()
    if db_conn:
        db_conn.close()
    logger.info("ASHİNA YKİ kapatıldı (exit=%s)", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
