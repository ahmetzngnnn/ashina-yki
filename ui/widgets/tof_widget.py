import logging
from PyQt6.QtWidgets import QWidget, QGridLayout, QLabel, QVBoxLayout, QFrame, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QColor

logger = logging.getLogger(__name__)

class TofWidget(QFrame):
    """
    Görev 3 (Yük Alma) sırasında TOF sensör okumalarını görselleştiren araç.
    3x3 bir ızgara yapısında 4 sensörün durumunu gösterir.
    VeriGörselleştirme.py mantığına sadık kalınarak tasarlanmıştır.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setStyleSheet("background-color: #111111; color: white; border-radius: 5px;")
        
        self.target_expected = 240.0
        self.tolerance = 15.0

        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Durum Başlığı
        self.lbl_status = QLabel("[ DURUM MAKİNESİ ]\nBEKLENİYOR")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet("color: cyan; font-size: 14px; font-weight: bold;")
        main_layout.addWidget(self.lbl_status)

        # 3x3 Grid
        self.grid = QGridLayout()
        self.grid.setSpacing(5)

        # Renkler (CSS rgb format)
        self.C_WHITE = "rgb(255, 255, 255)"
        self.C_RED = "rgb(204, 25, 25)"
        self.C_GREEN = "rgb(25, 204, 25)"
        self.C_BLUE = "rgb(51, 102, 230)"
        self.C_YELLOW = "rgb(204, 204, 25)"
        self.C_BLACK = "rgb(13, 13, 13)"

        self.labels = {}
        for row in range(3):
            for col in range(3):
                lbl = QLabel()
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setMinimumSize(60, 60)
                lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                lbl.setStyleSheet(f"background-color: {self.C_BLACK}; border: 1px solid #333; font-size: 10px;")
                self.grid.addWidget(lbl, row, col)
                self.labels[(row, col)] = lbl

        # Sabit Köşe İsimleri
        self._set_cell(0, 0, "SOL ÜST\n(L5)\nBekleniyor...", self.C_BLACK)
        self._set_cell(0, 2, "SAĞ ÜST\n(L1)\nBekleniyor...", self.C_BLACK)
        self._set_cell(2, 0, "SOL ALT\n(L4)\nBekleniyor...", self.C_BLACK)
        self._set_cell(2, 2, "SAĞ ALT\n(L2)\nBekleniyor...", self.C_BLACK)

        main_layout.addLayout(self.grid)

    def _set_cell(self, row, col, text, bg_color, is_target=False):
        lbl = self.labels[(row, col)]
        lbl.setText(text)
        text_color = "black" if bg_color == self.C_WHITE else "white"
        lbl.setStyleSheet(f"background-color: {bg_color}; color: {text_color}; font-weight: bold; border: 1px solid #555;")

    @pyqtSlot(dict)
    def update_tof_data(self, data: dict):
        """
        Gelen data formatı:
        {
            "L1": 240.5, # SAĞ ÜST
            "L2": 241.0, # SAĞ ALT
            "L5": 239.5, # SOL ÜST
            "L4": 255.0  # SOL ALT
        }
        """
        d_L1 = data.get("L1", 0)
        d_L2 = data.get("L2", 0)
        d_L5 = data.get("L5", 0)
        d_L4 = data.get("L4", 0)

        # Hata durumu kontrolü (Tümü -1 ise)
        if d_L1 == -1 and d_L2 == -1 and d_L5 == -1 and d_L4 == -1:
            self.lbl_status.setText("HATA!\nSENSÖR OKUNAMIYOR")
            self.lbl_status.setStyleSheet("color: red; font-size: 14px; font-weight: bold;")
            return

        h_L1 = abs(d_L1 - self.target_expected) <= self.tolerance
        h_L2 = abs(d_L2 - self.target_expected) <= self.tolerance
        h_L5 = abs(d_L5 - self.target_expected) <= self.tolerance
        h_L4 = abs(d_L4 - self.target_expected) <= self.tolerance

        # Siyah zemin
        for row in range(3):
            for col in range(3):
                self._set_cell(row, col, "", self.C_BLACK)

        # Hücre güncellemeleri
        c_L5 = self.C_WHITE if h_L5 else self.C_RED
        self._set_cell(0, 0, f"SOL ÜST\n{d_L5:.1f} mm", c_L5)

        c_L1 = self.C_WHITE if h_L1 else self.C_GREEN
        self._set_cell(0, 2, f"SAĞ ÜST\n{d_L1:.1f} mm", c_L1)

        c_L4 = self.C_WHITE if h_L4 else self.C_BLUE
        self._set_cell(2, 0, f"SOL ALT\n{d_L4:.1f} mm", c_L4)

        c_L2 = self.C_WHITE if h_L2 else self.C_YELLOW
        self._set_cell(2, 2, f"SAĞ ALT\n{d_L2:.1f} mm", c_L2)

        # Durum Kararı
        durum_metni = ""
        durum_renk = "cyan"

        if h_L1 and h_L2 and h_L5 and h_L4:
            durum_metni = "[ KİLİTLENDİ ]\nHedef Tam Merkezde!"
            durum_renk = "lime"
            self._set_cell(1, 1, "HEDEF\nMERKEZ", self.C_WHITE)
        elif not h_L1 and not h_L2 and not h_L5 and not h_L4:
            durum_metni = "[ ARAMA MODU ]\nHedef Bulunamadı"
            durum_renk = "gray"
        else:
            yon_y = ""
            yon_x = ""
            if (not h_L5 and not h_L1) and (h_L4 or h_L2): yon_y = "YUKARI"
            elif (not h_L4 and not h_L2) and (h_L5 or h_L1): yon_y = "AŞAĞI"
            
            if (not h_L5 and not h_L4) and (h_L1 or h_L2): yon_x = "SOLA"
            elif (not h_L1 and not h_L2) and (h_L5 or h_L4): yon_x = "SAĞA"
            
            if yon_y or yon_x:
                hedef_yon = f"{yon_y} {yon_x}".strip()
                durum_metni = f"[ DÜZELTME YAPIYOR ]\n--> {hedef_yon} GİT"
                durum_renk = "yellow"
            else:
                durum_metni = "[ KARARSIZLIK ]\nHizalama Bekleniyor"
                durum_renk = "orange"

        self.lbl_status.setText(durum_metni)
        self.lbl_status.setStyleSheet(f"color: {durum_renk}; font-size: 14px; font-weight: bold;")
