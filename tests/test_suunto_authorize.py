"""Synthetic loopback HTTP and storage checks; no provider or personal data."""
import base64
import contextlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('suunto_authorize', ROOT / 'scripts/suunto_authorize.py')
suunto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suunto)


def fixture(port=8765, secret=True):
    config = {'client_id': 'test-test-invented-client', 'subscription_key': 'test-invented-subscription',
              'redirect_uri': f'http://localhost:{port}/oauth/suunto/callback'}
    if secret:
        config['client_secret'] = 'test-invented-secret'
    return config


def tokens():
    return {'access_token': 'test-test-invented-access', 'refresh_token': 'test-test-invented-refresh',
            'token_type': 'bearer', 'expires_in': 3600}


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.path = Path(self.folder.name) / 'private/configuration.json'
        self.path.parent.mkdir()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.config = fixture(self.port)
        self.path.write_text(json.dumps(self.config))
        self.exchange = Mock(return_value=tokens())
        self.server = suunto.AuthorizationServer(self.config, self.path, exchange=self.exchange)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.folder.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        headers = dict(headers or {})
        headers.setdefault('Host', self.server.expected_host)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read().decode()
        connection.close()
        return result

    def post(self, secret='test-invented-added-secret', csrf=None, headers=None, path='/setup'):
        body = urllib.parse.urlencode({'csrf': self.server.csrf if csrf is None else csrf,
                                      'client_secret': secret})
        values = {'Origin': self.server.origin, 'Content-Type': 'application/x-www-form-urlencoded'}
        values.update(headers or {})
        return self.request('POST', path, body, values)

    def callback(self, query=None):
        return self.request('GET', suunto.CALLBACK + '?' + urllib.parse.urlencode(
            query if query is not None else {'state': self.server.state, 'code': 'test-invented-code'}, doseq=True))

    def test_setup_configured_has_authorize_only_and_private_headers(self):
        status, headers, body = self.request('GET', '/setup')
        self.assertEqual(status, 200)
        self.assertIn('Authorize Suunto', body)
        self.assertNotIn('type="password"', body)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        self.assertIn("form-action 'self'", headers['Content-Security-Policy'])
        self.assertNotIn(self.config['client_secret'], body)
        self.assertNotIn(self.config['subscription_key'], body)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.server.authorization_url()).query)
        self.assertEqual(set(query), {'response_type', 'client_id', 'redirect_uri', 'state'})
        self.assertEqual(query['state'], [self.server.state])
        self.assertGreaterEqual(len(self.server.state), 32)

    def test_missing_secret_is_collected_only_in_local_post(self):
        self.server.config.pop('client_secret')
        status, _, body = self.request('GET', '/setup')
        self.assertEqual(status, 200)
        self.assertIn('type="password"', body)
        self.assertIn(self.server.csrf, body)
        self.assertEqual(self.request('GET', '/setup?client_secret=must-not-save')[0], 404)
        status, _, body = self.post()
        self.assertEqual(status, 200)
        self.assertIn('Authorize Suunto', body)
        self.assertNotIn('test-invented-added-secret', body)
        self.assertEqual(json.loads(self.path.read_text())['client_secret'], 'test-invented-added-secret')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertNotIn('type="password"', self.request('GET', '/setup')[2])
        self.assertEqual(self.post(secret='test-replacement-attempt')[0], 400)
        self.assertEqual(json.loads(self.path.read_text())['client_secret'], 'test-invented-added-secret')

    def test_form_rejects_csrf_host_origin_content_and_size(self):
        self.server.config.pop('client_secret')
        original = self.path.read_bytes()
        for headers, csrf in [({}, 'wrong'), ({'Host': 'attacker.example'}, None),
                              ({'Origin': 'https://attacker.example'}, None),
                              ({'Origin': self.server.origin + '/'}, None),
                              ({'Content-Type': 'text/plain'}, None)]:
            with self.subTest(headers=headers):
                self.assertEqual(self.post(headers=headers, csrf=csrf)[0], 400)
        self.assertEqual(self.request('POST', '/setup', 'x', {'Origin': self.server.origin,
                         'Content-Type': 'application/x-www-form-urlencoded', 'Content-Length': '20000'})[0], 400)
        self.assertEqual(self.request('POST', '/setup', 'x', {
            'Content-Type': 'application/x-www-form-urlencoded'})[0], 400)
        duplicate = urllib.parse.urlencode({'csrf': self.server.csrf, 'client_secret': ['one', 'two']}, doseq=True)
        self.assertEqual(self.request('POST', '/setup', duplicate, {'Origin': self.server.origin,
                         'Content-Type': 'application/x-www-form-urlencoded'})[0], 400)
        self.assertEqual(self.path.read_bytes(), original)
        self.exchange.assert_not_called()

    def test_host_validation_applies_to_get_and_callback(self):
        self.assertEqual(self.request('GET', '/setup', headers={'Host': 'localhost.attacker:8765'})[0], 400)
        self.assertEqual(self.request('GET', suunto.CALLBACK + '?code=private-query', headers={'Host': 'attacker'})[0], 400)
        self.assertEqual(self.request('GET', 'http://attacker/setup')[0], 400)
        self.assertEqual(self.request('GET', '//attacker/setup')[0], 400)
        self.exchange.assert_not_called()

    def test_success_saves_tokens_and_marks_connected(self):
        status, _, body = self.callback()
        self.assertEqual(status, 200)
        self.assertIn('Suunto connected', body)
        self.assertIn('Workout import is not yet implemented', body)
        self.assertTrue(self.server.connected)
        saved = json.loads((self.path.parent / 'tokens.json').read_text())
        self.assertEqual(saved['access_token'], 'test-test-invented-access')
        self.assertGreater(saved['expires_at'], time.time())
        self.assertEqual(stat.S_IMODE((self.path.parent / 'tokens.json').stat().st_mode), 0o600)
        self.exchange.assert_called_once()
        self.assertEqual(self.exchange.call_args.args[1], 'test-invented-code')
        self.assertLessEqual(self.exchange.call_args.kwargs['timeout'], 30)
        self.assertEqual(self.callback()[0], 400)
        self.assertEqual(self.exchange.call_count, 1)

    def test_invalid_states_cannot_consume_valid_attempt(self):
        for query in ({'code': 'x'}, {'state': ''}, {'state': 'wrong', 'code': 'x'},
                      {'state': [self.server.state, self.server.state], 'code': 'x'},
                      {'state': '\u00e9', 'code': 'x'}):
            with self.subTest(query=query):
                self.assertEqual(self.callback(query)[0], 400)
                self.assertFalse(self.server.used)
        self.exchange.assert_not_called()
        self.assertEqual(self.callback()[0], 200)

    def test_decline_missing_or_duplicate_code_consumes_attempt_without_exchange(self):
        for additions in ({'error': 'access_denied'}, {}, {'code': ''}, {'code': ['one', 'two']}):
            with self.subTest(additions=additions):
                self.server.used = False
                query = dict(additions, state=self.server.state)
                self.assertEqual(self.callback(query)[0], 400)
                self.assertTrue(self.server.used)
                self.assertEqual(self.callback()[0], 400)
        self.exchange.assert_not_called()
        self.assertFalse((self.path.parent / 'tokens.json').exists())

    def test_wrong_callback_path_and_missing_secret_do_not_exchange(self):
        self.assertEqual(self.request('GET', '/oauth/suunto/callback/')[0], 404)
        self.server.config.pop('client_secret')
        self.assertEqual(self.callback()[0], 400)
        self.exchange.assert_not_called()

    def test_provider_and_storage_failures_are_redacted_and_preserve_tokens(self):
        path = self.path.parent / 'tokens.json'
        path.write_text('previous-private-tokens')
        self.exchange.side_effect = RuntimeError('test-invented-secret test-invented-code test-test-invented-access provider-body')
        output = io.StringIO()
        with contextlib.redirect_stderr(output), contextlib.redirect_stdout(output):
            status, _, body = self.callback()
        self.assertEqual(status, 502)
        for private in ('test-invented-secret', 'test-invented-code', 'test-test-invented-access', 'provider-body'):
            self.assertNotIn(private, body + output.getvalue())
        self.assertEqual(path.read_text(), 'previous-private-tokens')
        self.assertTrue(self.server.used)
        self.assertFalse(self.server.connected)

    def test_slow_exchange_response_is_bounded_and_late_tokens_are_ignored(self):
        release = threading.Event()
        finished = threading.Event()

        def slow_exchange(config, code, timeout):
            release.wait(timeout=2)
            finished.set()
            return tokens()

        self.exchange.side_effect = slow_exchange
        self.server.deadline = time.monotonic() + 0.15
        with patch.object(suunto, 'private_json') as save:
            start = time.monotonic()
            try:
                status, _, body = self.callback()
                self.assertEqual(status, 502)
                self.assertLess(time.monotonic() - start, 0.8)
                self.assertNotIn('test-test-invented-access', body)
                self.assertFalse(self.server.connected)
                self.assertTrue(self.server.used)
                save.assert_not_called()
            finally:
                release.set()
                self.assertTrue(finished.wait(timeout=1))
            save.assert_not_called()
        self.assertFalse((self.path.parent / 'tokens.json').exists())

    def test_deadline_is_rechecked_after_token_validation_before_save(self):
        validate = suunto.validate_tokens

        def expire_during_validation(value):
            result = validate(value)
            self.server.deadline = time.monotonic() - 1
            return result

        with patch.object(suunto, 'validate_tokens', side_effect=expire_during_validation), \
                patch.object(suunto, 'private_json') as save:
            self.assertEqual(self.callback()[0], 502)
            save.assert_not_called()
        self.assertFalse(self.server.connected)
        self.assertFalse((self.path.parent / 'tokens.json').exists())

    def test_token_validation_even_for_replaced_exchange(self):
        self.exchange.return_value = {'access_token': 'test-invented', 'token_type': 'bearer'}
        self.assertEqual(self.callback()[0], 502)
        self.assertFalse((self.path.parent / 'tokens.json').exists())

    def test_setup_save_failure_does_not_update_in_memory_or_existing_file(self):
        self.server.config.pop('client_secret')
        before = self.path.read_bytes()
        with patch.object(suunto.os, 'replace', side_effect=OSError('private-value')):
            status, _, body = self.post()
        self.assertEqual(status, 500)
        self.assertNotIn('private-value', body)
        self.assertNotIn('client_secret', self.server.config)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('.suunto-*')), [])


class StorageConfigTests(unittest.TestCase):
    def test_valid_redirects_and_rejects_nonliteral_or_nonexact_urls(self):
        suunto.validate_config(fixture())
        suunto.validate_config(dict(fixture(), redirect_uri='http://127.0.0.1:8765' + suunto.CALLBACK))
        for uri in ['https://localhost:8765' + suunto.CALLBACK, 'http://localhost' + suunto.CALLBACK,
                    'http://127.1:8765' + suunto.CALLBACK, 'http://[::1]:8765' + suunto.CALLBACK,
                    'http://localhost.attacker:8765' + suunto.CALLBACK,
                    'http://user@localhost:8765' + suunto.CALLBACK,
                    'http://LOCALHOST:8765' + suunto.CALLBACK,
                    'http://localhost:8765' + suunto.CALLBACK + '?secret=x',
                    'http://localhost:8765' + suunto.CALLBACK + '#x',
                    'http://localhost:0' + suunto.CALLBACK, 'http://localhost:65536' + suunto.CALLBACK,
                    'http://localhost:8765/wrong', 'http://localhost:8765' + suunto.CALLBACK + '/']:
            with self.subTest(uri=uri), self.assertRaises(suunto.SafeError):
                suunto.validate_config(dict(fixture(), redirect_uri=uri))

    def test_invalid_config_and_credentials_have_fixed_errors(self):
        for config in ([], {}, dict(fixture(), client_id='bad:id'), dict(fixture(), client_secret=''),
                       dict(fixture(), subscription_key='x\nprivate'), dict(fixture(), client_secret=123)):
            with self.subTest(config=config), self.assertRaises(suunto.SafeError) as error:
                suunto.validate_config(config)
            self.assertNotIn('private', str(error.exception))
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            path = Path(folder) / 'configuration.json'
            path.write_text('{invalid-secret-value')
            with self.assertRaises(suunto.SafeError) as error:
                suunto.load_config(path)
            self.assertNotIn('invalid-secret-value', str(error.exception))
            path.write_text(json.dumps(fixture(secret=False)))
            self.assertEqual(suunto.load_config(path), fixture(secret=False))

    def test_atomic_permissions_nested_creation_and_replace_failure(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            path = Path(folder) / 'new/private/tokens.json'
            suunto.private_json(path, tokens())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            before = path.read_bytes()
            with patch.object(suunto.os, 'replace', side_effect=OSError('synthetic failure')):
                with self.assertRaises(OSError):
                    suunto.private_json(path, {'access_token': 'test-replacement'})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob('.suunto-*')), [])
            with self.assertRaises(ValueError):
                suunto.private_json(path, {'invalid': float('nan')})
            self.assertEqual(path.read_bytes(), before)

    def test_symlink_destinations_and_directories_are_refused(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            root = Path(folder)
            target = root / 'real.json'
            target.write_text(json.dumps(fixture()))
            link = root / 'link.json'
            link.symlink_to(target)
            with self.assertRaises(suunto.SafeError):
                suunto.private_json(link, {'test-replacement': 'no'})
            with self.assertRaises(suunto.SafeError):
                suunto.load_config(link)
            real_dir = root / 'real'
            real_dir.mkdir()
            dir_link = root / 'linked'
            dir_link.symlink_to(real_dir, target_is_directory=True)
            with self.assertRaises(OSError):
                suunto.private_json(dir_link / 'tokens.json', tokens())
            with self.assertRaises(suunto.SafeError):
                suunto.load_config(dir_link / 'configuration.json')
            self.assertEqual(json.loads(target.read_text()), fixture())
            self.assertFalse((real_dir / 'tokens.json').exists())
            with self.assertRaises(suunto.SafeError):
                suunto.private_json(real_dir, tokens())

    def test_timeout_validation_and_actual_bounded_lifetime(self):
        with self.assertRaises(suunto.SafeError):
            suunto.AuthorizationServer(fixture(), Path('not-read'), timeout=601)
        with self.assertRaises(suunto.SafeError):
            suunto.AuthorizationServer(fixture(), Path('not-read'), timeout=0)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        server = suunto.AuthorizationServer(fixture(port), Path('not-read'), timeout=0.15)
        start = time.monotonic()
        self.assertFalse(server.run())
        self.assertLess(time.monotonic() - start, 1)

    def test_slow_local_body_cannot_extend_server_lifetime(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        server = suunto.AuthorizationServer(fixture(port, secret=False), Path('not-read'), timeout=0.2)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        connection = socket.create_connection(('127.0.0.1', port), timeout=1)
        try:
            partial = (f'POST /setup HTTP/1.1\r\nHost: localhost:{port}\r\n'
                       f'Origin: http://localhost:{port}\r\n'
                       'Content-Type: application/x-www-form-urlencoded\r\n'
                       'Content-Length: 100\r\n\r\nx')
            connection.sendall(partial.encode())
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertFalse(server.connected)
        finally:
            connection.close()
            server.server_close()

    def test_success_stops_one_shot_server(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            exchange = Mock(return_value=tokens())
            server = suunto.AuthorizationServer(fixture(port), Path(folder) / 'configuration.json',
                                                timeout=2, exchange=exchange)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
            connection.request('GET', suunto.CALLBACK + '?' + urllib.parse.urlencode(
                {'state': server.state, 'code': 'test-invented-code'}), headers={'Host': server.expected_host})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertTrue(server.connected)
            exchange.assert_called_once()

    def test_main_redacts_config_error_and_does_not_read_for_bad_timeout(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output), patch.object(suunto, 'load_config', side_effect=OSError('private-details')) as load:
            self.assertEqual(suunto.main(['--timeout', '601']), 1)
            load.assert_not_called()
            self.assertEqual(suunto.main(['--config', '/test-invented/not-present.json']), 1)
        self.assertNotIn('private-details', output.getvalue())


class TokenExchangeTests(unittest.TestCase):
    def response(self, body, status=200):
        response = Mock(status=status)
        response.read.return_value = body
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_basic_exchange_exact_endpoint_form_and_timeout(self):
        response = self.response(json.dumps(tokens()).encode())
        opener = Mock()
        opener.open.return_value = response
        with patch.object(suunto.urllib.request, 'build_opener', return_value=opener) as build:
            result = suunto.exchange_code(fixture(), 'test-invented-code')
        self.assertEqual(result['refresh_token'], 'test-test-invented-refresh')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, suunto.TOKEN_URL)
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Basic ' + base64.b64encode(b'test-test-invented-client:test-invented-secret').decode())
        self.assertEqual(urllib.parse.parse_qs(request.data.decode()), {
            'grant_type': ['authorization_code'], 'code': ['test-invented-code'],
            'redirect_uri': [fixture()['redirect_uri']]})
        self.assertNotIn('subscription', str(request.headers).lower())
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 30)
        self.assertIsInstance(build.call_args.args[0], suunto.NoRedirect)
        response.read.assert_called_once_with(suunto.MAX_JSON + 1)

    def test_redirect_is_refused_even_to_same_host(self):
        for target in ('https://attacker.example/token', suunto.TOKEN_URL):
            with self.subTest(target=target), self.assertRaises(suunto.SafeError):
                suunto.NoRedirect().redirect_request(Mock(), Mock(), 307, 'redirect', {}, target)

    def test_bounded_json_and_provider_errors_are_redacted(self):
        for body in (b'not-json provider-private', b'x' * (suunto.MAX_JSON + 1), b'[]',
                     json.dumps({'access_token': 'test-private-provider-token'}).encode(),
                     json.dumps(dict(tokens(), token_type='MAC')).encode()):
            opener = Mock()
            opener.open.return_value = self.response(body)
            with self.subTest(body=body[:30]), patch.object(suunto.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(suunto.SafeError) as error:
                    suunto.exchange_code(fixture(), 'test-invented-code')
                self.assertNotIn('private', str(error.exception))
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(suunto.TOKEN_URL, 401, 'private-provider-body', {}, None)
        with patch.object(suunto.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(suunto.SafeError) as error:
                suunto.exchange_code(fixture(), 'test-invented-code')
            self.assertNotIn('private-provider-body', str(error.exception))

    def test_expiry_is_derived_only_for_valid_lifetimes(self):
        self.assertEqual(suunto.validate_tokens(tokens(), now=100)['expires_at'], 3700)
        for value in (None, '3600', -1, 0, True, float('inf'), float('nan'), 10 ** 1000):
            with self.subTest(value=type(value).__name__):
                result = suunto.validate_tokens(dict(tokens(), expires_in=value, expires_at='untrusted'), now=100)
                self.assertNotIn('expires_at', result)
        self.assertEqual(suunto.validate_tokens(dict(tokens(), token_type='Bearer'))['token_type'], 'Bearer')


if __name__ == '__main__':
    unittest.main()
