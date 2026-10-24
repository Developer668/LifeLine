"""Run on the GPU host after serving starts. Optional image and audio smoke tests."""
import argparse
import base64
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from relay.model import ModelAdapter

parser = argparse.ArgumentParser()
parser.add_argument('--image', type=Path)
parser.add_argument('--audio-output', type=Path)
args = parser.parse_args()
adapter = ModelAdapter()
readiness = adapter.probe()
if not readiness['ready']:
    raise SystemExit('Model endpoint is not configured')
result = {'readiness': readiness}
text = adapter.generate({'text': 'What is your purpose? Answer briefly.'})
result['text'] = {'ok': bool(text.text), 'simulated': text.simulated}
if args.image:
    image = base64.b64encode(args.image.read_bytes()).decode()
    reply = adapter.generate({'text': 'Describe the visible scene cautiously.', 'image_base64': image})
    result['image'] = {'ok': bool(reply.text), 'simulated': reply.simulated}
if args.audio_output:
    reply = adapter.generate({'text': 'Hello. This is the RELAY prototype.'}, with_audio=True)
    args.audio_output.write_bytes(reply.audio)
    result['audio'] = {'ok': True, 'bytes': len(reply.audio), 'format': 'wav', 'sample_rate_expected': 24000}
print(json.dumps(result, indent=2))
