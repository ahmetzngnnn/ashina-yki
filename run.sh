#!/usr/bin/env bash
###############################################################################
# ASHİNA YKİ — Linux/Raspberry Pi Başlatıcı
#
# Windows PyInstaller/Inno zinciri yerine Linux/ARM'de doğal çalıştırma:
#   ./run.sh            # venv kur (ilk sefer) + bağımlılıkları yükle + YKİ başlat
#   ./run.sh --sim      # sahte worker'larla (backend yok)
#   ASHINA_YKI_HOST=127.0.0.1 ./run.sh   # host override (sim/e2e)
#
# Gereksinim (sistem paketi): Qt6 çalışma zamanı + TTS için espeak-ng
#   sudo apt install -y python3-venv libxcb-cursor0 espeak-ng
###############################################################################
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VENV="${VENV_DIR:-.venv}"
PY="${PYTHON:-python3}"

if [ ! -d "$VENV" ]; then
  echo "[run] venv oluşturuluyor: $VENV"
  "$PY" -m venv "$VENV"
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  echo "[run] bağımlılıklar yükleniyor (requirements-linux.txt)…"
  pip install -q --upgrade pip
  pip install -q -r requirements-linux.txt
else
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi

echo "[run] ASHİNA YKİ başlatılıyor…"
exec python main.py "$@"
