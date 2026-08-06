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

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)

        # Logo / başlık
        title = QLabel("ASHİNA YKİ")
        title.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #3daee9; letter-spacing: 2px;")
        layout.addWidget(title)

        subtitle = QLabel("Taktiksel Yer Kontrol İstasyonu v2.0")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #8b949e; font-size: 13px; font-weight: bold;")
        layout.addWidget(subtitle)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #30363d; margin-top: 10px; margin-bottom: 10px;")
        layout.addWidget(sep)

        layout.addWidget(self._local_tab())

        layout.addStretch()

        ver = QLabel("v2.0 · Güvenli Bağlantı")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setStyleSheet("color: #484f58; font-size: 11px;")
        layout.addWidget(ver)

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
            self.login_successful.emit(self._auth.get_role())
            self.close()
        else:
            QMessageBox.critical(self, "Hata", "Kullanıcı adı veya şifre hatalı.")
            self._local_pass.clear()

