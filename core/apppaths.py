"""
apppaths.py — Platforma göre yazılabilir uygulama veri klasörü
==============================================================
ÖNCEDEN her yerde `%APPDATA%` (Windows'a özgü) kullanılıyordu; Linux/RPi'de
os.environ.get("APPDATA", Path.home()) → doğrudan ev dizinine düşüyordu (kirli).
Bu yardımcı platform standardına uyar:
  • Windows : %APPDATA%\\ASHINA-YKI
  • Linux   : $XDG_DATA_HOME/ASHINA-YKI  (yoksa ~/.local/share/ASHINA-YKI)
  • macOS   : ~/Library/Application Support/ASHINA-YKI

Bağımlılıksızdır (yalnızca stdlib) → gcs_config gibi düşük seviye modüller güvenle import eder.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ASHINA-YKI"


def app_data_dir(sub: str | None = None) -> Path:
    """Yazılabilir uygulama veri klasörünü döndürür (yoksa oluşturur).

    sub verilirse alt klasör (ör. 'telemetry_logs') eklenip oluşturulur.
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:  # linux / rpi / diğer POSIX
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")

    d = Path(base) / APP_NAME
    if sub:
        d = d / sub
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Son çare: ev dizini
        d = Path.home() / f".{APP_NAME.lower()}"
        if sub:
            d = d / sub
        d.mkdir(parents=True, exist_ok=True)
    return d
