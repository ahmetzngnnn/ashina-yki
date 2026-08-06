# Runtime hook — exe açılırken çalışır, herhangi bir import'tan ÖNCE.
# pyqtgraph'ın hangi Qt binding'ini kullanacağını açıkça belirtiyoruz.
import os
os.environ['PYQTGRAPH_QT_LIB'] = 'PyQt6'
