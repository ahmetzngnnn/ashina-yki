#!/usr/bin/env bash
###############################################################################
# ASHİNA YKİ — Offline harita için Leaflet vendoring
#
# Bir kez (İNTERNET olan makinede) çalıştırın:
#   bash assets/vendor/fetch_vendor.sh
#
# Leaflet CSS/JS'i assets/vendor/leaflet/ altına indirir. Sonra ui/map_assets.py
# bunları HTML'e INLINE gömer → 3 harita çevrimdışı (sahada, internetsiz RPi'de)
# çalışır. (Uydu TILE'ları ayrı; tam offline uydu için tile önbelleği gerekir.)
###############################################################################
set -euo pipefail

VER="${LEAFLET_VERSION:-1.9.4}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/leaflet"
mkdir -p "$DIR"

BASE="https://unpkg.com/leaflet@${VER}/dist"

echo "[fetch_vendor] Leaflet ${VER} indiriliyor → $DIR"
for f in leaflet.js leaflet.css; do
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$BASE/$f" -o "$DIR/$f"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$BASE/$f" -O "$DIR/$f"
  else
    echo "✗ curl/wget yok — indirilemiyor" >&2; exit 1
  fi
  echo "  ✓ $f ($(wc -c <"$DIR/$f") bayt)"
done

# Leaflet CSS marker/layer ikonlarına göreli images/ ister; onları da al (opsiyonel)
mkdir -p "$DIR/images"
for img in marker-icon.png marker-icon-2x.png marker-shadow.png layers.png layers-2x.png; do
  curl -fsSL "$BASE/images/$img" -o "$DIR/images/$img" 2>/dev/null || true
done

echo "[fetch_vendor] Tamam. Artık haritalar çevrimdışı Leaflet ile açılır."
