# -*- mode: python ; coding: utf-8 -*-
# MAVİOS-GCS PyInstaller Spec Dosyası
# Kullanım: pyinstaller mavios_gcs.spec

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None
project_root = os.path.dirname(os.path.abspath(SPEC))

# ── Bağımlılıkları topla ─────────────────────────────────────────────────────
datas     = []
binaries  = []
hiddenimports = []

for pkg in ['pymavlink', 'cv2', 'pyttsx3', 'pyqtgraph', 'cryptography',
            'folium', 'requests', 'serial', 'paho', 'zmq']:
    try:
        d, b, h = collect_all(pkg)
        datas    += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# Proje kaynak klasörleri
for folder in ('data', 'assets'):
    fp = os.path.join(project_root, folder)
    if os.path.exists(fp):
        datas += [(fp, folder)]

# ── Hidden imports ────────────────────────────────────────────────────────────
hiddenimports += [
    # PyQt6 / WebEngine
    'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebChannel',
    'PyQt6.QtNetwork',
    'PyQt6.sip',
    # MAVLink dialects
    'pymavlink.dialects.v20.ardupilotmega',
    'pymavlink.dialects.v20.common',
    'pymavlink.dialects.v10.ardupilotmega',
    # Serial / COM
    'serial',
    'serial.tools',
    'serial.tools.list_ports',
    'serial.tools.list_ports_windows',
    # TTS
    'pyttsx3.drivers',
    'pyttsx3.drivers.sapi5',
    # Network
    'zmq',
    'zmq.backend.cython',
    'zmq.backend.cffi',
    'paho.mqtt',
    'paho.mqtt.client',
    # pyqtgraph — Qt binding açık olarak belirtilmeli (frozen ortam için)
    'pyqtgraph',
    'pyqtgraph.Qt',
    'pyqtgraph.graphicsItems',
    'pyqtgraph.graphicsItems.PlotDataItem',
    'pyqtgraph.graphicsItems.PlotItem',
    'pyqtgraph.graphicsItems.PlotCurveItem',
    'pyqtgraph.graphicsItems.ScatterPlotItem',
    'pyqtgraph.graphicsItems.LegendItem',
    'pyqtgraph.graphicsItems.GridItem',
    'pyqtgraph.graphicsItems.AxisItem',
    'pyqtgraph.graphicsItems.ViewBox',
    'pyqtgraph.graphicsItems.InfiniteLine',
    'pyqtgraph.graphicsItems.LinearRegionItem',
    'pyqtgraph.widgets',
    'pyqtgraph.widgets.PlotWidget',
    'pyqtgraph.exporters',
    # Crypto
    'cryptography.hazmat.primitives',
    'cryptography.hazmat.backends',
    'cryptography.hazmat.backends.openssl',
    # Misc
    'PIL',
    'numpy',
]

# ── Analiz ────────────────────────────────────────────────────────────────────
a = Analysis(
    ['main.py'],
    pathex=[project_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['hooks'],
    hooksconfig={
        'matplotlib': {'backends': 'none'},
    },
    runtime_hooks=['hooks/rthook-pyqtgraph.py'],
    excludes=['tkinter', 'matplotlib', 'scipy', '_tkinter', 'test',
              'unittest', 'doctest', 'difflib'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MAVIOS-GCS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # GUI uygulama — konsol penceresi çıkmaz
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,               # icon eklemek için: icon='assets\\mavios.ico'
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=['vcruntime140.dll', 'Qt6*.dll', 'python3*.dll'],
    name='MAVIOS-GCS',
)
