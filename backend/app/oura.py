"""Oura adapter. Only the worker reads secrets or contacts Oura."""
import json
import os
from pathlib import Path
import tempfile
import httpx

KINDS = ['daily_sleep', 'sleep', 'daily_readiness', 'daily_activity', 'daily_stress', 'workout']

class OuraClient:
    def __init__(self):
        self.folder = Path(os.getenv('SECRETS_DIR', '/run/coach-secrets'))
        self.tokens = json.loads((self.folder / 'oura-tokens.json').read_text())
        self.http = httpx.Client(timeout=45)

    def close(self):
        self.http.close()

    def refresh(self):
        cfg = json.loads((self.folder / 'oura.json').read_text())
        response = self.http.post('https://api.ouraring.com/oauth/token', data={
            'grant_type': 'refresh_token', 'refresh_token': self.tokens['refresh_token'],
            'client_id': cfg['client_id'], 'client_secret': cfg['client_secret'],
        })
        if response.status_code != 200:
            raise RuntimeError(f'Oura token refresh failed (HTTP {response.status_code}); reconnect if authorization was revoked.')
        tokens = response.json()
        if not tokens.get('access_token') or not tokens.get('refresh_token'):
            raise RuntimeError('Oura returned incomplete refresh credentials.')
        # Persist the rotated single-use refresh token before making another request.
        fd, name = tempfile.mkstemp(dir=self.folder, prefix='.tokens-')
        try:
            with os.fdopen(fd, 'w') as file:
                json.dump(tokens, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.folder / 'oura-tokens.json')
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.tokens = tokens

    def records(self, kind, start, end):
        params = {'start_date': start, 'end_date': end}
        seen = set()
        while True:
            response = self.http.get(f'https://api.ouraring.com/v2/usercollection/{kind}', params=params,
                                     headers={'Authorization': 'Bearer ' + self.tokens['access_token']})
            if response.status_code == 401:
                self.refresh()
                response = self.http.get(f'https://api.ouraring.com/v2/usercollection/{kind}', params=params,
                                         headers={'Authorization': 'Bearer ' + self.tokens['access_token']})
            if response.status_code != 200:
                # Never log the response body, access token or private health records.
                retry = response.headers.get('retry-after', '')
                raise RuntimeError(f'Oura {kind}: HTTP {response.status_code}. Retry-After: {retry[:12]}')
            page = response.json()
            yield from page.get('data', [])
            token = page.get('next_token')
            if not token:
                return
            if token in seen:
                raise RuntimeError('Oura pagination returned a repeated cursor.')
            seen.add(token)
            params['next_token'] = token
