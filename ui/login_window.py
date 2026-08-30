"""
login_window.py — Giriş Ekranı
Yerel DB ile kimlik doğrulama.
"""
import logging
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel,
    QLineEdit, QPushButton, QMessageBox,
    QFormLayout, QFrame,
)
from core import theme
from ui.auth_manager import AuthManager

logger = logging.getLogger(__name__)


class LoginWindow(QWidget):
    login_successful = pyqtSignal(int)   # rol

    def __init__(self, auth_manager: AuthManager, parent=None):
        super().__init__(parent)
        self._auth = auth_manager
        self.setWindowTitle("ASHİNA YKİ — Giriş")
        self.setFixedSize(420, 380)
        # self.setStyleSheet() kaldırıldı; global style.qss kullanılacak
        self._build_ui()
        # Kurulumda yazılan satır-içi renkler tema değişince BAYAT kalır —
        # sinyale bağlanıp hepsini aktif paletle yeniden yazıyoruz.
        theme.notifier().changed.connect(lambda _n: self._apply_theme_colors())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)

        # Logo / başlık
        self._title_lbl = QLabel("ASHİNA YKİ")
        self._title_lbl.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title_lbl)

        self._subtitle_lbl = QLabel("Taktiksel Yer Kontrol İstasyonu v2.0")
        self._subtitle_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._subtitle_lbl)

        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(self._sep)

        layout.addWidget(self._local_tab())

        layout.addStretch()

        self._ver_lbl = QLabel("v2.0 · Güvenli Bağlantı")
        self._ver_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._ver_lbl)

        # Satır-içi renkler TEK noktadan yazılır (tema değişiminde de aynı yol)
        self._apply_theme_colors()

    # ── Tema: satır-içi renkler ───────────────────────────────────────────────
    def _apply_theme_colors(self) -> None:
        """Kurulumda yazılan TÜM satır-içi renkleri aktif palete göre (yeniden)
        yazar. Tema değişince `notifier().changed` üzerinden çağrılır — aksi
        halde bu ekranın renkleri eski paletle bayat kalırdı."""
        # 26px + letter-spacing QSS class'larıyla karşılanmıyor (pageTitle 14px),
        # bu yüzden satır içi kalıyor — renk yalnızca tema token'ından.
        self._title_lbl.setStyleSheet(
            f"color: {theme.c('accent')}; letter-spacing: 2px;"
        )
        self._subtitle_lbl.setStyleSheet(
            f"color: {theme.c('text_muted')}; font-size: 13px; font-weight: bold;"
        )
        # QFrame HLine çizgi rengini `color` özelliğinden alır
        self._sep.setStyleSheet(
            f"color: {theme.c('border')}; margin-top: 10px; margin-bottom: 10px;"
        )
        self._ver_lbl.setStyleSheet(
            f"color: {theme.c('text_disabled')}; font-size: 11px;"
        )

    def _local_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(14)

        self._local_user = QLineEdit()
        self._local_user.setPlaceholderText("Kullanıcı adı")
        self._local_pass = QLineEdit()
        self._local_pass.setPlaceholderText("Şifre")
        self._local_pass.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("Kullanıcı:", self._local_user)
        form.addRow("Şifre:", self._local_pass)

        btn = QPushButton("Giriş Yap")
        btn.setObjectName("primaryButton")
        btn.clicked.connect(self._do_local_login)
        self._local_pass.returnPressed.connect(self._do_local_login)
        form.addRow(btn)
        return w

    def _do_local_login(self) -> None:
        username = self._local_user.text().strip()
        password = self._local_pass.text()

        if not username or not password:
            QMessageBox.warning(self, "Uyarı", "Kullanıcı adı ve şifre gereklidir.")
            return

        if self._auth.authenticate_local(username, password):
            if username == "admin" and password == "admin":
                # 2026-08-26: varsayılan admin/admin hesabı değişime hiç
                # zorlanmıyordu, yalnızca log'a "değiştirin" yazılıyordu —
                # artık her girişte göz ardı edilemez bir uyarı çıkıyor.
                QMessageBox.warning(
                    self, "Güvenlik Uyarısı",
                    "Varsayılan yönetici hesabıyla (admin/admin) giriş yaptınız.\n\n"
                    "Bu şifreyi HEMEN değiştirin: Sistem Günlüğü → Kullanıcı "
                    "Yönetimi'nden yeni bir admin hesabı oluşturup bu hesabı "
                    "silin, ya da şifresini değiştirin.")
            self.login_successful.emit(self._auth.get_role())
            self.close()
        else:
            QMessageBox.critical(self, "Hata", "Kullanıcı adı veya şifre hatalı.")
            self._local_pass.clear()

