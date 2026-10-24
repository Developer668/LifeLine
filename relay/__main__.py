"""Local gateway/worker entrypoints, loading ignored .env without exposing secrets."""
import argparse
from dotenv import load_dotenv


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description='RELAY phone-first gateway')
    p.add_argument('command', choices=['serve', 'worker'])
    p.add_argument('--port', type=int, default=8000)
    args = p.parse_args()
    if args.command == 'serve':
        import uvicorn
        uvicorn.run('relay.app:app', host='127.0.0.1', port=args.port, access_log=False)
    else:
        from .worker import main as worker
        worker([])

if __name__ == '__main__':
    main()
