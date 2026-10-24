#!/usr/bin/env bash
# Run ONLY in a new Linux MoLab GPU session, never on the Mac.
# Sources: official MiniCPM recipe and vLLM-Omni CUDA installation docs.
set -euo pipefail
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'Requires Linux x86_64 on MoLab.' >&2; exit 1; }
command -v nvidia-smi >/dev/null || { echo 'No NVIDIA GPU available.' >&2; exit 1; }
command -v uv >/dev/null || { echo 'Install uv in the MoLab session first.' >&2; exit 1; }
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
RELAY_GPU_DIR="${RELAY_GPU_DIR:-$PWD/relay-gpu}"
OMNI_REV="${OMNI_REV:-7f90d24d76705c8ee901a5d049a285fcbcbaa816}"
mkdir -p "$RELAY_GPU_DIR"
uv venv --python 3.12 "$RELAY_GPU_DIR/.venv"
export VIRTUAL_ENV="$RELAY_GPU_DIR/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
# Matching CUDA13 wheel documented by upstream; requires a compatible host driver.
uv pip install --torch-backend=cu130 'vllm @ https://wheels.vllm.ai/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm-0.32.0-cp38-abi3-manylinux_2_28_x86_64.whl'
if [[ ! -d "$RELAY_GPU_DIR/vllm-omni/.git" ]]; then
  git clone https://github.com/vllm-project/vllm-omni.git "$RELAY_GPU_DIR/vllm-omni"
fi
git -C "$RELAY_GPU_DIR/vllm-omni" checkout "$OMNI_REV"
uv pip install -e "$RELAY_GPU_DIR/vllm-omni" stepaudio2-minicpmo httpx
# Do not install Omni's [dev] extra: it supplies a conflicting Step-Audio distribution.
python -c 'import torch, vllm_omni; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))'
uv pip freeze > "$RELAY_GPU_DIR/installed-requirements.txt"
printf '%s\n' "$OMNI_REV" > "$RELAY_GPU_DIR/omni-revision.txt"
echo "Installed. Activate $VIRTUAL_ENV/bin/activate, then run scripts/gpu_serve.sh with RELAY_GPU_DIR set."
