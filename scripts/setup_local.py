"""Create local runtime configuration without printing credentials."""
from pathlib import Path
import json
import os
import secrets
ROOT = Path(__file__).resolve().parent.parent
folder = ROOT / '.secrets'
runtime = folder / 'oura-runtime'
runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(runtime, 0o700)
for name in ['oura.json', 'oura-tokens.json']:
    target = runtime / name
    if not target.exists():
        content = (folder / name).read_text()
        json.loads(content)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write(content)
env = ROOT / '.env'
if not env.exists():
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as file:
        file.write('POSTGRES_PASSWORD=' + secrets.token_urlsafe(32) + '\nWEB_PORT=8080\n')
print('Local settings ready. The worker will exclusively manage tokens in .secrets/oura-runtime/.')
