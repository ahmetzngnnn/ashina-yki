#!/usr/bin/env bash
# Türkçe nöral ses modelini indirir (Piper).
#
# NOT: Model DEPODA MEVCUTTUR — `git clone` yapan makinede ses doğrudan
# çalışır, ayrıca bir şey indirmek gerekmez. Bu betik yalnızca modeli
# yenilemek ya da yanlışlıkla silinmişse geri getirmek içindir.
#
# NEDEN depoda tutuluyor: saha kurulumunda internet garanti değil ve
# "kurulum betiğini çalıştırmayı unutma" tuzağı bu projede bir kez yaşandı
# (çevrimdışı harita için Leaflet indirilmemişti, harita boş kalıyordu).
# 61 MB'lık tek seferlik bir ikili dosya, sessizce sesi olmayan bir yer
# istasyonundan iyidir.
#
# Python bağımlılığı (bir kez):  pip install --user piper-tts
#
# Model bulunamazsa YKİ sessizce espeak/pyttsx3'e düşer ve çalışmaya devam eder
# (bkz. core/audio_tts_worker.py:_piper_yukle).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICE="tr_TR-dfki-medium"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/tr/tr_TR/dfki/medium"

echo "[fetch_voice] Türkçe ses indiriliyor: ${VOICE} → ${DIR}"
for f in "${VOICE}.onnx" "${VOICE}.onnx.json"; do
  if [ -s "${DIR}/${f}" ]; then
    echo "  • ${f} zaten var, atlanıyor"
    continue
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -fL# -o "${DIR}/${f}" "${BASE}/${f}"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --show-progress -O "${DIR}/${f}" "${BASE}/${f}"
  else
    echo "HATA: curl veya wget gerekli." >&2
    exit 1
  fi
  echo "  ✓ ${f} ($(du -h "${DIR}/${f}" | cut -f1))"
done

echo "[fetch_voice] Tamam. YKİ açıldığında sesli otopilot Türkçe nöral sesi kullanır."
echo "             Doğrulama: log'da '[TTS] Türkçe nöral ses (Piper) etkin.' satırı."
