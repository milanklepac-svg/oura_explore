"""Launch containers, refresh from Oura, and wait for the result. No health payloads logged."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def settings():
    return dict(line.split('=', 1) for line in (ROOT/'.env').read_text().splitlines()
                if '=' in line and not line.startswith('#'))


def request(opener, base, path, method='GET'):
    req = urllib.request.Request(base+path, method=method)
    with opener.open(req, timeout=10) as response:
        return json.load(response)


def wait_for_api(opener, base, timeout):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        try:
            request(opener, base, '/api/health')
            return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise RuntimeError('Local API did not become ready. Inspect docker compose logs api web.')


def refresh(opener, base, timeout):
    job = request(opener, base, '/api/sync', 'POST')
    print(f'Oura refresh requested (job {job["job_id"]}). Cached data remains available.', flush=True)
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        result = request(opener, base, '/api/sync/'+str(job['job_id']))
        if result['status'] == 'complete':
            print('Oura refresh complete. Open '+base, flush=True)
            return result
        if result['status'] == 'failed':
            raise RuntimeError('Oura refresh failed: '+(result['detail'] or 'unknown error')+
                               '. Existing cached records remain available in the dashboard.')
        time.sleep(1)
    raise RuntimeError('Oura refresh is still pending. The app is running; inspect its sync status in the dashboard.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-build', action='store_true', help='Reuse existing container images')
    parser.add_argument('--timeout', type=int, default=180, help='Readiness and refresh wait timeout in seconds')
    args = parser.parse_args()
    if not 10 <= args.timeout <= 1800:
        parser.error('--timeout must be between 10 and 1800')
    subprocess.run([sys.executable, str(ROOT/'scripts/setup_local.py')], cwd=ROOT, check=True)
    docker = shutil.which('docker') or next((p for p in ['/usr/local/bin/docker','/opt/homebrew/bin/docker'] if os.path.exists(p)), None)
    if not docker:
        raise RuntimeError('Docker not found. Open Docker Desktop first.')
    command = [docker, 'compose', 'up', '-d']
    if not args.no_build:
        command.append('--build')
    services = ['db', 'api', 'worker', 'web']
    if settings().get('SUUNTO_ENABLED', '').lower() == 'true':
        services.append('suunto-worker')
    subprocess.run(command+services, cwd=ROOT, check=True)
    port = int(settings().get('WEB_PORT', '8080'))
    if not 1 <= port <= 65535:
        raise RuntimeError('Invalid WEB_PORT')
    base = f'http://127.0.0.1:{port}'
    # Keep local health records out of any configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    wait_for_api(opener, base, args.timeout)
    refresh(opener, base, args.timeout)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, urllib.error.URLError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
