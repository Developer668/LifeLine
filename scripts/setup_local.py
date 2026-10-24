"""Generate local-only secrets once. Never prints credential values."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
path = root / '.env'
if path.exists():
    print('Existing .env preserved.')
else:
    template = (root / '.env.example').read_text()
    for name in ['DEMO_TOKEN', 'RELAY_WORKER_TOKEN', 'RELAY_LINK_SECRET']:
        lines = template.splitlines()
        found = any(line.startswith(name+'=') for line in lines)
        template = '\n'.join(name+'='+secrets.token_urlsafe(32) if line.startswith(name+'=') else line for line in lines)+'\n'
        if not found:
            template += name+'='+secrets.token_urlsafe(32)+'\n'
    path.write_text(template)
    path.chmod(0o600)
    print('Created private .env; add provider values there when available.')
