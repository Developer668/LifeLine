"""Launch gateway and a temporary Cloudflare HTTPS tunnel as one development process."""
import argparse
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
from dotenv import load_dotenv, set_key


def main():
    root=Path(__file__).resolve().parents[1]
    load_dotenv(root/'.env')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    binary=shutil.which('cloudflared') or str(root/'.runtime/cloudflared')
    if not Path(binary).is_file():
        raise SystemExit('Install cloudflared from its official package, then run again.')
    tunnel=subprocess.Popen([binary,'tunnel','--no-autoupdate','--url',f'http://127.0.0.1:{args.port}'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    gateway=None
    reload_requested=threading.Event()
    signal.signal(signal.SIGHUP,lambda *_:reload_requested.set())
    try:
        for line in tunnel.stdout:
            match=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com',line)
            if not match:continue
            url=match.group()
            # Public URL is not a credential. Never print environment token values.
            os.environ['PUBLIC_BASE_URL']=url
            set_key(str(root/'.env'),'PUBLIC_BASE_URL',url)
            print('Public gateway:',url,flush=True)
            print('Configure Twilio POST webhooks at /twilio/voice and /twilio/sms.',flush=True)
            gateway=subprocess.Popen([sys.executable,'-m','relay','serve','--port',str(args.port)],cwd=root,env=os.environ.copy())
            break
        if gateway is None:
            raise RuntimeError('Cloudflare did not allocate a tunnel URL.')
        def consume():
            for _ in tunnel.stdout:pass
        threading.Thread(target=consume,daemon=True).start()
        while tunnel.poll() is None:
            if reload_requested.is_set() or gateway.poll() is not None:
                reload_requested.clear()
                if gateway.poll() is None:
                    gateway.terminate()
                    try:gateway.wait(timeout=40)
                    except subprocess.TimeoutExpired:gateway.kill();gateway.wait()
                load_dotenv(root/'.env',override=True)
                os.environ['PUBLIC_BASE_URL']=url
                gateway=subprocess.Popen([sys.executable,'-m','relay','serve','--port',str(args.port)],cwd=root,env=os.environ.copy())
                print('Gateway restarted; public HTTPS address preserved.',flush=True)
            threading.Event().wait(.5)
        raise SystemExit(gateway.returncode or tunnel.returncode or 1)
    except KeyboardInterrupt:
        pass
    finally:
        for process in [gateway,tunnel]:
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=40)
                except subprocess.TimeoutExpired:process.kill()

if __name__=='__main__':main()
