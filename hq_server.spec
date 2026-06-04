# -*- mode: python ; coding: utf-8 -*-
# MAVİOS-GCS HQ Sunucu PyInstaller Spec
# Kullanım: pyinstaller hq_server.spec --noconfirm

import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None
project_root = os.path.dirname(os.path.abspath(SPEC))

datas    = []
binaries = []
hiddenimports = []

for pkg in ['paho', 'cryptography']:
    try:
        d, b, h = collect_all(pkg)
        datas    += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# assets klasörü varsa ekle
for folder in ('assets', 'data'):
    fp = os.path.join(project_root, folder)
    if os.path.exists(fp):
        datas += [(fp, folder)]

hiddenimports += [
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'PyQt6.sip',
    'paho.mqtt',
    'paho.mqtt.client',
]

a = Analysis(
    ['hq_server.py'],
    pathex=[project_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'scipy', '_tkinter',
        'pymavlink', 'cv2', 'pyttsx3', 'pyqtgraph',
        'folium', 'serial', 'zmq', 'pygame', 'numpy',
        'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebEngineCore',
        'test', 'unittest', 'pydoc', 'doctest',
    ],
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
    name='MAVIOS-HQ',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # GUI uygulama — konsol penceresi yok
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,               # icon: icon='assets\\mavios_hq.ico'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=['vcruntime140.dll', 'Qt6*.dll', 'python3*.dll'],
    name='MAVIOS-HQ',
)
