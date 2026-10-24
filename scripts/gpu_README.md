# MoLab inference handoff

Status: A live MoLab session was connected and its RTX PRO6000 Blackwell Server Edition (97887MiB) hardware verified. MiniCPM weights and the pinned stack are installed; all three stages initialized; real text/structured/image inference and a1/2/4-concurrency text benchmark passed. Optional generated audio timed out and is not enabled for phone output. No local Mac GPU inference was used. See docs/VERIFICATION.md for final measured status.

## Reproducible session setup

Upload this project into a fresh **Linux x86_64 RTX PRO 6000 96GB** MoLab session. The session needs a CUDA 13 compatible NVIDIA driver, Python 3.12/uv, sufficient checkpoint storage, and outbound HTTPS to Hugging Face, Python package hosts, and your gateway. Keep credentials in MoLab secrets or environment variables, never notebook source or repository files.

```sh
bash scripts/gpu_setup.sh
export RELAY_GPU_DIR="$PWD/relay-gpu"
bash scripts/gpu_serve.sh
```

The server intentionally binds `127.0.0.1:8099`. In a second session terminal, activate the created GPU environment and verify the model before connecting the worker:

```sh
source relay-gpu/.venv/bin/activate
export MODEL_BASE_URL=http://127.0.0.1:8099
python scripts/gpu_verify.py
python scripts/gpu_verify.py --image /path/to/test.jpg --audio-output /path/to/test.wav
python scripts/benchmark_gpu.py --requests 12 --output artifacts/gpu-text.json
python scripts/benchmark_gpu.py --requests 12 --audio --output artifacts/gpu-audio.json
```

Set `RELAY_API_URL` to the public HTTPS gateway and `RELAY_WORKER_TOKEN` to its separate worker token. The worker requires `httpx`, Pydantic 2, and local `relay` sources; setup installs these plus `python-dotenv` for the `python -m relay worker` wrapper. It does not need FastAPI or Twilio installed on the GPU. Start from the project root:

```sh
python -m relay.worker --check
python -m relay.worker
```

Set `RELAY_WORKER_CONCURRENCY=1` initially. Benchmark 1, 2, and 4 before selecting a setting. Model stage admission limits do not establish actual conversation capacity. No inbound MoLab tunnel is required.

## CPU backup demonstration

`RELAY_ALLOW_SIMULATION=true` explicitly enables a labeled backup response when `MODEL_BASE_URL` is empty. It never generates audio and never establishes GPU readiness. Benchmarking refuses to run without a real model endpoint, regardless of this flag.

## Upstream sources and version notes

Reviewed on 2026-10-24:
- [Official MiniCPM-o 4.5 recipe](https://github.com/vllm-project/vllm-omni/blob/7f90d24d76705c8ee901a5d049a285fcbcbaa816/recipes/OpenBMB/MiniCPM-o-4_5.md)
- [Official CUDA installation instructions](https://github.com/vllm-project/vllm-omni/blob/7f90d24d76705c8ee901a5d049a285fcbcbaa816/docs/getting_started/installation/gpu/cuda.inc.md)

Setup pins the reviewed Omni revision and the vLLM wheel recommended by that revision. CUDA compiler/CRT/NVVM packages are pinned to 13.0.88 after a real MoLab startup exposed unconstrained dependency resolution choosing compiler13.4 against torch CUDA13.0 headers; FlashInfer correctly rejected that mismatch. The recipe specifies the MiniCPM vocoder distribution; the development extras specify an incompatible Step-Audio distribution, so this setup excludes those extras. Initial text validation precedes image and audio validation. Actual compatibility on the allocated host still requires the smoke tests above.

The adapter requests text-only output initially, disables thinking, places images before their questions, and finds audio in whichever choice contains it. Retryable inference failures have a two-attempt bound. Completion retries resend the same result; a lost acknowledgement never causes a contradictory failure outcome. Reports contain measured request timings and error counts, not advertised capacities. Hardware is only marked observed when `nvidia-smi` succeeds on the benchmark host.

## Live host repairs

The hosted notebook inherits global NVIDIA packages. Explicitly set CUDA_HOME/CUDACXX/PATH to the matching environment compiler so FlashInfer does not accidentally choose the host's newer nvcc with CUDA13.0 runtime headers. The NVIDIA pip runtime uses lib/libcudart.so.13; the serving script supplies the lib64 and unversioned linker names expected by JIT builds. Compiler mismatch and missing -lcudart errors were observed and repaired during startup. Keep these repairs distinct from a successful inference result.

The live host also exposed an upstream adapter mismatch: StepAudio2's flow YAML references cosyvoice2 module names, and the reference package registers them with _setup_cosyvoice2_alias(). The pinned Omni in-tree adapter bypassed that registration. scripts/gpu_patch.py invokes the reference helper immediately before YAML construction, preserves a source backup, and refuses to patch an unexpected version. scripts/render_gpu_config.py selects the official turn-mode base with max_num_seqs4, eager Thinker/Talker execution, and single-image input; the duplex default is unnecessary for Twilio Gather turns.
