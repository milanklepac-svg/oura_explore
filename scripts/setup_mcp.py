"""Provision one local database reader. Password is passed via stdin, never output."""
from pathlib import Path
import os
import re
import secrets
import subprocess
ROOT = Path(__file__).resolve().parent.parent
path = ROOT / '.env'
if not path.exists():
    raise SystemExit('Run scripts/setup_local.py and start the database/API first.')
contents = path.read_text()
settings = dict(line.split('=', 1) for line in contents.splitlines() if '=' in line and not line.startswith('#'))
password = settings.get('MCP_DATABASE_PASSWORD') or secrets.token_hex(32)
if not re.fullmatch('[a-f0-9]{64}', password):
    raise SystemExit('MCP_DATABASE_PASSWORD must contain 64 hexadecimal characters.')
if 'MCP_DATABASE_PASSWORD' not in settings:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    with os.fdopen(fd, 'w') as file:
        file.write(('' if contents.endswith('\n') else '\n')+'MCP_DATABASE_PASSWORD='+password+'\n')
os.chmod(path, 0o600)
# Fixed role name and validated random hexadecimal password; no user-supplied SQL.
sql = """\set ON_ERROR_STOP on
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='coach_mcp') THEN CREATE ROLE coach_mcp; END IF; END $$;
ALTER ROLE coach_mcp WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD '%s';
ALTER ROLE coach_mcp SET default_transaction_read_only = on;
ALTER ROLE coach_mcp SET statement_timeout = '10s';
GRANT CONNECT ON DATABASE coach TO coach_mcp;
GRANT USAGE ON SCHEMA public TO coach_mcp;
GRANT SELECT ON TABLE public.wearable_records, public.sync_jobs, public.training_profiles TO coach_mcp;
""" % password
result = subprocess.run(['docker','compose','exec','-T','db','psql','-U','coach','-d','coach'],cwd=ROOT,
                        input=sql,text=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
if result.returncode:
    raise SystemExit('Could not provision the MCP reader. Check the database and API are initialized. No credentials logged.')
print('MCP database reader ready: SELECT-only, with default read-only transactions.')
