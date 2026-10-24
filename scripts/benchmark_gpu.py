"""Real endpoint latency report at concurrency 1/2/4. Never emits fabricated GPU results."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from relay.model import ModelAdapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--requests', type=int, default=12, help='Requests per concurrency; minimum four')
    parser.add_argument('--audio', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('artifacts/gpu-benchmark.json'))
    args = parser.parse_args()
    if not 4 <= args.requests <= 1000:
        parser.error('--requests must be between 4 and 1000')
    adapter = ModelAdapter()
    if not adapter.base:
        raise SystemExit('MODEL_BASE_URL required. Simulation is prohibited for GPU benchmarks.')
    adapter.probe()
    try:
        gpu = subprocess.check_output(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader'], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        gpu = None
    def one(index):
        started = time.perf_counter()
        try:
            value = adapter.generate({'text': f'Fictional practice request {index}: explain RELAY in one brief sentence.'}, with_audio=args.audio)
            return {'ok': bool(value.text) and not value.simulated, 'seconds': time.perf_counter() - started}
        except Exception as error:
            return {'ok': False, 'seconds': time.perf_counter() - started, 'error_type': type(error).__name__}
    # Exclude one warmup; any warmup failure stops the benchmark.
    warmup = one(-1)
    if not warmup['ok']:
        raise SystemExit('Warmup failed: ' + warmup.get('error_type', 'invalid response'))
    rows = []
    for concurrency in (1, 2, 4):
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            samples = list(pool.map(one, range(args.requests)))
        elapsed = time.perf_counter() - started
        successes = [x['seconds'] for x in samples if x['ok']]
        ordered = sorted(successes)
        rows.append({'concurrency': concurrency, 'requests': args.requests, 'successes': len(successes),
            'errors': args.requests-len(successes), 'error_rate': 1-len(successes)/args.requests,
            'elapsed_seconds': elapsed, 'throughput_per_second': len(successes)/elapsed,
            'mean_seconds': statistics.mean(successes) if successes else None,
            'p50_seconds': statistics.median(successes) if successes else None,
            'p95_seconds': ordered[math.ceil(.95*len(ordered))-1] if ordered else None,
            'samples': samples})
    report = {'measured_at': datetime.now(timezone.utc).isoformat(), 'simulated': False,
        'hardware_observed_on_benchmark_host': gpu, 'gpu_verified': gpu is not None,
        'mode': 'text_audio' if args.audio else 'text', 'model': adapter.name,
        'notice': 'Measurements include bounded adapter retries; remote model hardware requires separate attestation.', 'results': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if any(row['errors'] for row in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
