#!/usr/bin/env bash
# Fetch the speaker-embedding model for the speaker gate (idempotent, sha256-verified).
set -euo pipefail
DIR="${FRIDAY_SPEAKER_GATE_DIR:-/srv/friday/state/speaker-gate}"
MODEL="$DIR/eres2net_base_16k.onnx"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
SHA="f1ee61be04000b50392a63442e5dd9feaea7e81dddd494d3647cf1c3d88a1498"

if [ -f "$MODEL" ] && [ "$(sha256sum "$MODEL" | cut -d' ' -f1)" = "$SHA" ]; then
  echo "model already present"; exit 0
fi
mkdir -p "$DIR"
TMP="$MODEL.part"
curl -fL --retry 3 -o "$TMP" "$URL"
if [ "$(sha256sum "$TMP" | cut -d' ' -f1)" != "$SHA" ]; then
  rm -f "$TMP" "$MODEL"
  echo "sha256 mismatch, file deleted" >&2; exit 1
fi
mv "$TMP" "$MODEL"
echo "model installed"
