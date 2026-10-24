#!/usr/bin/env bash
set -euo pipefail
RELAY_GPU_DIR="${RELAY_GPU_DIR:-$PWD/relay-gpu}"
source "$RELAY_GPU_DIR/.venv/bin/activate"
cd "$RELAY_GPU_DIR/vllm-omni"
# Model remains loopback-only. Worker reaches this locally and polls gateway outbound.
exec vllm serve openbmb/MiniCPM-o-4_5 --omni --trust-remote-code \
  --chat-template vllm_omni/transformers_utils/chat_templates/minicpmo45_native.jinja \
  --chat-template-content-format openai --host 127.0.0.1 --port "${MODEL_PORT:-8099}"
