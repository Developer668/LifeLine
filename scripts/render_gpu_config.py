"""Write a conservative official turn-mode deployment overlay for this demo."""
import argparse
from importlib.util import find_spec
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    spec=find_spec('vllm_omni')
    if not spec or not spec.origin:raise SystemExit('Install the pinned vLLM-Omni environment first.')
    upstream=Path(spec.origin).parent/'deploy/minicpmo_4_5_turn.yaml'
    if not upstream.is_file():raise SystemExit('Official MiniCPM turn deployment profile is missing.')
    text=f'''base_config: {upstream}
session_mode: turn
stages:
  - stage_id: 0
    max_num_seqs: 4
    max_model_len: 8192
    max_num_batched_tokens: 4096
    enforce_eager: true
    limit_mm_per_prompt: {{image: 1, audio: 1, video: 0}}
  - stage_id: 1
    max_num_seqs: 4
    enforce_eager: true
  - stage_id: 2
    max_num_seqs: 4
platforms:
  cuda:
    cuda_mps: false
'''
    Path(args.output).write_text(text)
    print('Wrote turn-based GPU deployment overlay:',args.output)

if __name__=='__main__':main()
