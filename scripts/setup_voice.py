"""Install local speech models; no carrier calls, texts or paid speech APIs."""
from pathlib import Path
import subprocess
import sys
from dotenv import load_dotenv, dotenv_values, set_key
import argparse


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-recognition',action='store_true',help='Use optional local Whisper instead of the configured OpenAI transcription API')
    args=parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env = root / '.env'
    if not env.exists():
        raise SystemExit('Run scripts/setup_local.py first to create private configuration.')
    values = dotenv_values(env)
    directory = root / '.runtime' / 'voices'
    directory.mkdir(parents=True, exist_ok=True)
    whisper = directory / 'faster-whisper-base.en'
    if args.local_recognition:
        from huggingface_hub import snapshot_download
        snapshot_download('Systran/faster-whisper-base.en', local_dir=whisper,
                          allow_patterns=['model.bin', 'config.json', 'tokenizer.json', 'vocabulary.*'])
    configured = values.get('RELAY_PIPER_MODEL')
    piper = Path(configured) if configured and Path(configured).is_file() else directory / 'en_US-lessac-medium.onnx'
    if not piper.is_file() or not piper.with_suffix('.onnx.json').is_file():
        subprocess.run([sys.executable, '-m', 'piper.download_voices', 'en_US-lessac-medium',
                        '--data-dir', str(directory)], check=True)
        piper = directory / 'en_US-lessac-medium.onnx'
    if args.local_recognition:
        set_key(str(env), 'RELAY_WHISPER_MODEL', str(whisper.resolve()))
    set_key(str(env), 'RELAY_PIPER_MODEL', str(piper.resolve()))
    set_key(str(env), 'RELAY_ASR_ENGINE', 'whisper' if args.local_recognition else 'openai')
    env.chmod(0o600)
    load_dotenv(env, override=True)
    from relay.live_audio import warmup
    warmup()
    print('Phone speech configured and local voice warmed. No API inference or carrier usage.')


if __name__ == '__main__':
    main()
