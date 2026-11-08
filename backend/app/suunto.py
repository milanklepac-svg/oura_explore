"""Worker-only summary adapter; never downloads FIT, routes or raw workouts."""
import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx

RUNNING_ACTIVITIES = {1, 22, 53, 59, 60, 103, 115}
API_URL = 'https://cloudapi.suunto.com/v3/workouts/'
TOKEN_URL = 'https://cloudapi-oauth.suunto.com/oauth/token'


def positive(value):
    try:
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else None
    except OverflowError:
        return None


def credential(value):
    return (isinstance(value, str) and 0 < len(value) <= 4096
            and value.strip() == value and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def timestamp(value):
    value = positive(value)
    if value is None:
        return None
    if value >= 100000000000:
        value /= 1000
    try:
        result = datetime.fromtimestamp(value, timezone.utc)
        return result if 2000 <= result.year <= 2100 else None
    except (ValueError, OverflowError, OSError):
        return None


def normalize(payload):
    if not isinstance(payload, dict) or type(payload.get('activityId')) is not int or payload['activityId'] not in RUNNING_ACTIVITIES:
        return None
    key = payload.get('workoutKey') or payload.get('key')
    start, end = timestamp(payload.get('startTime')), timestamp(payload.get('stopTime'))
    if not isinstance(key, str) or not key or len(key) > 256 or start is None:
        return None
    if end is not None and end <= start:
        end = None
    offset = payload.get('timeOffsetInMinutes')
    valid_offset = isinstance(offset, (int, float)) and not isinstance(offset, bool) and math.isfinite(offset) and -840 <= offset <= 840
    day = (start + timedelta(minutes=offset if valid_offset else 0)).date()
    active = positive(payload.get('totalTime'))
    return {'record_id': key, 'day': str(day), 'activity': 'running', 'activity_id': payload['activityId'],
            'start_datetime': start.isoformat(), 'end_datetime': end.isoformat() if end else None,
            'distance_meters': positive(payload.get('totalDistance')), 'duration_seconds': active,
            'elapsed_duration_seconds': (end-start).total_seconds() if end else None,
            'duration_basis': 'active' if active else 'missing', 'timezone_offset_minutes': offset if valid_offset else None,
            'day_basis': 'provider_offset' if valid_offset else 'utc_fallback'}


class RateLimited(RuntimeError):
    def __init__(self, retry_after):
        super().__init__('Suunto rate limit reached; collection paused.')
        self.retry_after = retry_after


class SuuntoClient:
    def __init__(self, reserve):
        self.folder = Path(os.getenv('SUUNTO_SECRETS_DIR', '/run/suunto-secrets'))
        self.config = json.loads((self.folder/'configuration.json').read_text())
        self.tokens = json.loads((self.folder/'tokens.json').read_text())
        self.reserve = reserve
        self.http = httpx.Client(timeout=45, follow_redirects=False)

    def close(self):
        self.http.close()

    @staticmethod
    def check(response):
        if response.status_code == 429:
            raise RateLimited(response.headers.get('retry-after', '')[:128])
        if response.status_code != 200:
            raise RuntimeError(f'Suunto request failed (HTTP {response.status_code}); reconnect for authorization errors.')

    def refresh(self):
        self.reserve()
        response = self.http.post(TOKEN_URL, data={'grant_type': 'refresh_token',
            'refresh_token': self.tokens['refresh_token']},
            auth=httpx.BasicAuth(self.config['client_id'], self.config['client_secret']))
        self.check(response)
        tokens = response.json()
        if isinstance(tokens, dict) and 'refresh_token' not in tokens:
            tokens['refresh_token'] = self.tokens.get('refresh_token')
        if not isinstance(tokens, dict) or not all(credential(tokens.get(key)) for key in ('access_token', 'refresh_token')):
            raise RuntimeError('Suunto returned incomplete refresh credentials; reconnect.')
        fd, name = tempfile.mkstemp(dir=self.folder, prefix='.tokens-')
        try:
            with os.fdopen(fd, 'w') as file:
                json.dump(tokens, file, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.folder/'tokens.json')
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.tokens = tokens

    def records(self, since, until):
        seen = set()
        for page in range(8):
            params = {'since': since, 'until': until, 'limit': 50, 'offset': page*50,
                      'filter-by-modification-time': 'false'}
            def get():
                self.reserve()
                return self.http.get(API_URL, params=params, headers={
                    'Authorization': 'Bearer '+self.tokens['access_token'],
                    'Ocp-Apim-Subscription-Key': self.config['subscription_key']})
            response = get()
            if response.status_code == 401:
                self.refresh()
                response = get()
            self.check(response)
            body = response.json()
            if not isinstance(body, dict) or body.get('error') or not isinstance(body.get('payload'), list):
                raise RuntimeError('Suunto returned an invalid workout envelope.')
            rows = body['payload']
            if len(rows) > 50:
                raise RuntimeError('Suunto exceeded the requested page size.')
            fingerprint = json.dumps(rows, sort_keys=True)
            if rows and fingerprint in seen:
                raise RuntimeError('Suunto returned a repeated page.')
            seen.add(fingerprint)
            for row in rows:
                workout = normalize(row)
                if workout and since <= int(datetime.fromisoformat(workout['start_datetime']).timestamp()*1000) < until:
                    yield workout
            if len(rows) < 50:
                return
        raise RuntimeError('Suunto page limit reached; use a smaller lookback window.')
