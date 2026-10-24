#!/usr/bin/env bash
set -euo pipefail
RELAY_GPU_DIR="${RELAY_GPU_DIR:-$PWD/relay-gpu}"
RELAY_PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RELAY_GPU_DIR/.venv/bin/activate"
# Prefer the matching compiler/runtime installed in this environment, not host tools.
RELAY_CUDA_ROOT="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"]+"/nvidia/cu13")')"
if [[ -x "$RELAY_CUDA_ROOT/bin/nvcc" ]]; then
  export CUDA_HOME="$RELAY_CUDA_ROOT"
  export CUDACXX="$RELAY_CUDA_ROOT/bin/nvcc"
  export PATH="$RELAY_CUDA_ROOT/bin:$PATH"
  python -c 'import os,pathlib; p=pathlib.Path(os.environ["CUDA_HOME"]); a=p/"lib64"; b=p/"lib/libcudart.so"; a.exists() or a.symlink_to("lib", target_is_directory=True); b.exists() or b.symlink_to("libcudart.so.13")'
fi
python "$RELAY_PROJECT_DIR/scripts/render_gpu_config.py" --output "$RELAY_GPU_DIR/relay-deploy.yaml"
cd "$RELAY_GPU_DIR/vllm-omni"
# Model remains loopback-only. Worker reaches this locally and polls gateway outbound.
exec vllm serve openbmb/MiniCPM-o-4_5 --omni --deploy-config "$RELAY_GPU_DIR/relay-deploy.yaml" --trust-remote-code \
  --chat-template vllm_omni/transformers_utils/chat_templates/minicpmo45_native.jinja \
  --chat-template-content-format openai --host 127.0.0.1 --port "${MODEL_PORT:-8099}"
