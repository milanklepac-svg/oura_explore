import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('DATABASE_URL', 'sqlite://')
import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.db import Base, Record, SuuntoQuota, SuuntoSyncJob
from app.suunto import SuuntoClient, RateLimited, normalize
from app.services.suunto_sync import queue_sync, reserve_request, record_backoff
from app.services.running import RunningService


def fixture(**extra):
    return {'workoutKey': 'test-invented-run', 'activityId': 1, 'startTime': 1793638800000,
            'stopTime': 1793640600000, 'totalTime': 1700, 'totalDistance': 5200,
            'timeOffsetInMinutes': 60, **extra}


class AdapterTests(unittest.TestCase):
    def test_allowlist_units_active_elapsed_and_missing_values(self):
        item = normalize(fixture(route='private', name='ignored', extensions={'gps': [1]}))
        self.assertEqual(item['duration_seconds'], 1700)
        self.assertEqual(item['elapsed_duration_seconds'], 1800)
        self.assertNotIn('route', item)
        self.assertNotIn('name', item)
        self.assertEqual(normalize(fixture(startTime=1793638800)), item)
        self.assertIsNone(normalize(fixture(activityId=2)))
        self.assertIsNone(normalize(fixture(startTime=float('nan'))))
        self.assertIsNone(normalize(fixture(totalDistance=-5))['distance_meters'])
        self.assertIsNone(normalize(fixture(stopTime=None))['end_datetime'])
        self.assertIsNone(normalize(fixture(stopTime=None))['elapsed_duration_seconds'])

    def client(self, folder, handler, reserve=lambda: None):
        Path(folder, 'configuration.json').write_text(json.dumps({'client_id': 'test-invented', 'client_secret': 'test-invented', 'subscription_key': 'test-invented'}))
        Path(folder, 'tokens.json').write_text(json.dumps({'access_token': 'old', 'refresh_token': 'old-refresh'}))
        with patch.dict(os.environ, {'SUUNTO_SECRETS_DIR': folder}):
            client = SuuntoClient(reserve)
        client.http.close()
        client.http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
        self.addCleanup(client.close)
        return client

    def test_rotation_persisted_before_retry_and_fixed_url(self):
        with tempfile.TemporaryDirectory() as folder:
            requests = []
            def handler(request):
                requests.append(request)
                if request.url.path == '/oauth/token':
                    self.assertEqual(request.headers['authorization'], 'Basic dGVzdC1pbnZlbnRlZDp0ZXN0LWludmVudGVk')
                    self.assertNotIn(b'client_secret', request.content)
                    return httpx.Response(200, json={'access_token': 'new', 'refresh_token': 'new-refresh'})
                if request.headers['authorization'] == 'Bearer old':
                    return httpx.Response(401)
                self.assertEqual(json.loads(Path(folder, 'tokens.json').read_text())['refresh_token'], 'new-refresh')
                self.assertEqual(request.url.params['filter-by-modification-time'], 'false')
                return httpx.Response(200, json={'payload': [fixture()], 'metadata': {'next': 'https://evil.invalid'}})
            reservations = []
            client = self.client(folder, handler, lambda: reservations.append(1))
            self.assertEqual(len(list(client.records(1793600000000, 1793700000000))), 1)
            self.assertEqual(len(reservations), 3)
            self.assertEqual(Path(folder, 'tokens.json').stat().st_mode & 0o777, 0o600)
            self.assertTrue(all(r.url.host in ('cloudapi.suunto.com', 'cloudapi-oauth.suunto.com') for r in requests))

    def test_refresh_without_rotation_preserves_token_and_atomic_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            client = self.client(folder, lambda request: httpx.Response(200, json={'access_token': 'new'}))
            client.refresh()
            path = Path(folder, 'tokens.json')
            self.assertEqual(json.loads(path.read_text())['refresh_token'], 'old-refresh')
            previous = path.read_text()
            with patch('app.suunto.os.replace', side_effect=OSError('test-invented failure')):
                with self.assertRaises(OSError):
                    client.refresh()
            self.assertEqual(path.read_text(), previous)
            self.assertEqual(list(Path(folder).glob('.tokens-*')), [])

    def test_invalid_refresh_credentials_do_not_replace_saved_tokens(self):
        for body in ({'access_token': 'new', 'refresh_token': ''}, {'access_token': 12}, []):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as folder:
                client = self.client(folder, lambda request: httpx.Response(200, json=body))
                previous = Path(folder, 'tokens.json').read_text()
                with self.assertRaisesRegex(RuntimeError, 'incomplete refresh credentials'):
                    client.refresh()
                self.assertEqual(Path(folder, 'tokens.json').read_text(), previous)

    def test_redirect_rejected_and_rate_limit_header_only(self):
        with tempfile.TemporaryDirectory() as folder:
            client = self.client(folder, lambda request: httpx.Response(302, headers={'location': 'https://evil.invalid'}))
            with self.assertRaisesRegex(RuntimeError, 'HTTP 302'):
                list(client.records(1, 2))
            client.http.close()
            client.http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, headers={'retry-after': '120'}, text='private ignored')))
            with self.assertRaises(RateLimited) as result:
                list(client.records(1, 2))
            self.assertEqual(result.exception.retry_after, '120')
            self.assertNotIn('private', str(result.exception))

    def test_repeated_offset_page_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [fixture(workoutKey=str(i)) for i in range(50)]
            client = self.client(folder, lambda request: httpx.Response(200, json={'payload': rows}))
            with self.assertRaisesRegex(RuntimeError, 'repeated page'):
                list(client.records(1793600000000, 1793700000000))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)

    def test_quota_rolling_window_minute_and_owner_isolation(self):
        with Session(self.engine) as db, db.begin():
            for i in range(10):
                reserve_request(db, 'a', 1000000+i)
            with self.assertRaisesRegex(RuntimeError, 'minute budget'):
                reserve_request(db, 'a', 1000010)
            reserve_request(db, 'b', 1000010)
            db.get(SuuntoQuota, 'a').state = {'requests': [1000000]*160}
            with self.assertRaisesRegex(RuntimeError, 'seven-day budget'):
                reserve_request(db, 'a', 1000100)
            reserve_request(db, 'a', 1000000+7*86400+1)
        with Session(self.engine) as db, db.begin():
            record_backoff(db, 'a', '120')
        with Session(self.engine) as db, db.begin():
            with self.assertRaisesRegex(RuntimeError, 'Retry-After'):
                reserve_request(db, 'a')

    def test_jobs_coalesce_independently_of_oura(self):
        with Session(self.engine) as db, db.begin():
            a, created = queue_sync(db, 'a')
            b, again = queue_sync(db, 'a')
            c, other = queue_sync(db, 'b')
            self.assertTrue(created and other)
            self.assertFalse(again)
            self.assertEqual(a.id, b.id)
            self.assertNotEqual(a.id, c.id)

    def add_suunto(self, key='s', owner='a', **kwargs):
        p = normalize(fixture(workoutKey=key, **kwargs))
        with Session(self.engine) as db, db.begin():
            db.add(Record(owner=owner, source='suunto', kind='workout', external_id=key, day=p['day'], payload=p))
        return p

    def add_oura(self, p, key='o', start=None, end=None):
        with Session(self.engine) as db, db.begin():
            db.add(Record(owner='a', source='oura', kind='workout', external_id=key, day=p['day'], payload={
                'activity': 'running', 'distance': 4000, 'start_datetime': start or p['start_datetime'], 'end_datetime': end or p['end_datetime']}))

    def test_suunto_authoritative_and_oura_fallback_distinct_runs(self):
        p = self.add_suunto()
        self.add_suunto('other-owner', owner='b')
        self.add_oura(p)
        self.add_oura(p, 'separate', '2026-11-02T20:00:00+01:00', '2026-11-02T20:30:00+01:00')
        result = RunningService(self.engine, 'a').context(date(2026, 11, 2))
        self.assertEqual(result['summary']['recorded_runs'], 2)
        self.assertEqual(result['summary']['recorded_km'], 9.2)
        s = next(r for r in result['runs'] if r['source'] == 'suunto')
        self.assertEqual(s['duration_seconds'], 1700)
        self.assertEqual(s['provenance'], ['suunto', 'oura'])
        self.assertTrue(next(r for r in result['runs'] if r['source'] == 'oura')['source_fallback'])

    def test_ambiguous_overlap_and_missing_distance_not_filled_from_oura(self):
        p = self.add_suunto(totalDistance=None)
        self.add_oura(p)
        result = RunningService(self.engine, 'a').context(date(2026, 11, 2))
        self.assertEqual(result['summary']['recorded_runs'], 1)
        self.assertEqual(result['summary']['missing_suunto_distance'], 1)
        self.assertIsNone(result['runs'][0]['km'])
        self.add_oura(p, 'ambiguous', p['start_datetime'], '2026-11-02T18:45:00+01:00')
        result = RunningService(self.engine, 'a').context(date(2026, 11, 2))
        self.assertEqual(result['summary']['recorded_runs'], 0)
        self.assertEqual(result['summary']['visible_records'], 3)
        self.assertEqual(result['summary']['ambiguous_overlap_records'], 3)
        self.assertEqual(result['summary']['recorded_km'], 0)
        self.assertIsNone(result['summary']['observed_km_per_week'])
        self.assertIsNone(result['summary']['longest_recorded_km'])
        self.assertTrue(all(r['excluded_from_totals'] for r in result['runs']))
        self.assertTrue(all(w['recorded_km'] is None for w in result['weeks']))

    def test_partial_today_is_opt_in(self):
        self.add_suunto()
        with patch('app.services.running.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 2, 22, tzinfo=timezone.utc)
            default = RunningService(self.engine, 'a').context()
            dashboard = RunningService(self.engine, 'a').context(include_today=True)
        self.assertEqual(default['end'], '2026-11-01')
        self.assertEqual(default['summary']['recorded_runs'], 0)
        self.assertFalse(default['includes_partial_today'])
        self.assertEqual(dashboard['end'], '2026-11-02')
        self.assertEqual(dashboard['summary']['recorded_runs'], 1)
        self.assertTrue(dashboard['includes_partial_today'])

    def test_unconfirmed_suunto_completion_is_excluded_from_planner_totals(self):
        for stop in (None, 1795000000000):
            with self.subTest(stop=stop):
                with Session(self.engine) as db, db.begin():
                    db.query(Record).delete()
                self.add_suunto(stopTime=stop)
                with patch('app.services.running.datetime', wraps=datetime) as clock:
                    clock.now.return_value = datetime(2026, 11, 3, tzinfo=timezone.utc)
                    result = RunningService(self.engine, 'a').context()
                self.assertEqual(result['summary']['recorded_runs'], 0)
                self.assertEqual(result['summary']['visible_records'], 1)
                self.assertEqual(result['summary']['recorded_km'], 0)
                self.assertIsNone(result['summary']['observed_km_per_week'])
                self.assertEqual(result['runs'][0]['observation_warning'], 'unconfirmed_completion')

    def test_stored_summary_projection_and_invalid_metrics(self):
        for value in (True, -1, 0, '5200', float('nan'), float('inf'), 10**400):
            with self.subTest(value=value):
                with Session(self.engine) as db, db.begin():
                    db.query(Record).delete()
                    p = normalize(fixture())
                    p.update(distance_meters=value, duration_seconds=value,
                             elapsed_duration_seconds=value, route='test-test-invented-private',
                             tokens={'access_token': 'test-test-invented-private'})
                    db.add(Record(owner='a', source='suunto', kind='workout', external_id='s', day=p['day'], payload=p))
                result = RunningService(self.engine, 'a').context(date(2026, 11, 2))
                run = result['runs'][0]
                self.assertNotIn('route', run)
                self.assertNotIn('tokens', run)
                self.assertIsNone(run['km'])
                self.assertIsNone(run['minutes'])
                self.assertIsNone(run['elapsed_pace_minutes_per_km'])
                self.assertEqual(result['summary']['recorded_km'], 0)

    def test_api_include_today_is_typed_and_defaults_to_complete_history(self):
        from fastapi.testclient import TestClient
        from app import api
        with patch('app.services.running.RunningService') as service:
            service.return_value.context.return_value = {'test-invented': True}
            client = TestClient(api.app)
            self.assertEqual(client.get('/api/running/context').status_code, 200)
            service.return_value.context.assert_called_with(include_today=False)
            self.assertEqual(client.get('/api/running/context?include_today=true').status_code, 200)
            service.return_value.context.assert_called_with(include_today=True)
            self.assertEqual(client.get('/api/running/context?include_today=nonsense').status_code, 422)

    def test_worker_idempotency_failure_budget_and_allowlisted_storage(self):
        from app import suunto_worker
        payload = normalize(fixture())
        class Client:
            def __init__(self, reserve): self.reserve = reserve
            def records(self, a, b):
                self.reserve()
                yield payload
            def close(self): pass
        with patch.object(suunto_worker, 'Session', __import__('sqlalchemy.orm', fromlist=['sessionmaker']).sessionmaker(self.engine)), patch.object(suunto_worker, 'OWNER', 'a'), patch.object(suunto_worker, 'SuuntoClient', Client):
            for _ in range(2):
                with Session(self.engine) as db, db.begin():
                    job, _ = queue_sync(db, 'a')
                    id = job.id
                suunto_worker.run_job(id)
            with Session(self.engine) as db:
                self.assertEqual(len(db.scalars(select(Record)).all()), 1)
                self.assertEqual(len(db.get(SuuntoQuota, 'a').state['requests']), 2)
                self.assertEqual(db.get(SuuntoSyncJob, id).status, 'complete')
