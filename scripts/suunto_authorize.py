#!/usr/bin/env python3
"""One-shot, loopback-only Suunto OAuth authorization; standard library only."""
import argparse
import base64
import hmac
import html
import json
import math
import os
from pathlib import Path
import queue
import secrets
import stat
import socket
import threading
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / '.secrets/suunto-runtime/configuration.json'
CALLBACK = '/oauth/suunto/callback'
AUTHORIZE_URL = 'https://cloudapi-oauth.suunto.com/oauth/authorize'
TOKEN_URL = 'https://cloudapi-oauth.suunto.com/oauth/token'
MAX_BODY = 16384
MAX_JSON = 65536


class SafeError(Exception):
    """Only fixed, credential-free messages may cross this boundary."""


def directory_fd(path, create=False):
    """Walk every directory without following symlinks, returning an owned fd."""
    path = Path(os.path.abspath(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def private_json(path, value):
    """Replace a regular file atomically; never follow directory/file symlinks."""
    path = Path(path)
    parent = directory_fd(path.parent, create=True)
    temporary = '.suunto-' + secrets.token_hex(16) + '.tmp'
    try:
        try:
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise SafeError('Private storage is not a regular file.')
        payload = (json.dumps(value, indent=2, allow_nan=False) + '\n').encode('utf-8')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.fchmod(parent, 0o700)
        # Recheck immediately before replace; replace itself never follows a link.
        try:
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise SafeError('Private storage is not a regular file.')
        os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def credential(value):
    return (isinstance(value, str) and 0 < len(value) <= 4096
            and value.strip() == value and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def validate_config(config):
    if not isinstance(config, dict):
        raise SafeError('Invalid Suunto configuration.')
    for name in ('client_id', 'subscription_key'):
        if not credential(config.get(name)):
            raise SafeError('Invalid Suunto configuration.')
    if ':' in config['client_id']:
        raise SafeError('Invalid Suunto configuration.')
    if 'client_secret' in config and not credential(config['client_secret']):
        raise SafeError('Invalid Suunto configuration.')
    uri = config.get('redirect_uri')
    if not isinstance(uri, str):
        raise SafeError('Invalid loopback redirect URI.')
    try:
        parsed = urllib.parse.urlsplit(uri)
        port = parsed.port
        if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1')
                or port is None or not 1 <= port <= 65535 or parsed.username is not None
                or parsed.password is not None or parsed.path != CALLBACK
                or parsed.query or parsed.fragment
                or uri != f'http://{parsed.hostname}:{port}{CALLBACK}'):
            raise ValueError()
    except ValueError:
        raise SafeError('Invalid loopback redirect URI.') from None
    return config


def load_config(path):
    try:
        parent = directory_fd(Path(path).parent)
        try:
            fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            with os.fdopen(fd, 'rb') as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError()
                raw = source.read(MAX_JSON + 1)
            if len(raw) > MAX_JSON:
                raise ValueError()
            config = json.loads(raw)
        finally:
            os.close(parent)
        return validate_config(config)
    except Exception:
        raise SafeError('Could not load a valid private Suunto configuration.') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never replay Basic credentials or a code to a redirected endpoint.
        raise SafeError('Token endpoint redirected the request.')


def validate_tokens(tokens, now=None):
    if (not isinstance(tokens, dict) or not credential(tokens.get('access_token'))
            or not credential(tokens.get('refresh_token'))
            or not isinstance(tokens.get('token_type'), str)
            or tokens['token_type'].lower() != 'bearer'):
        raise SafeError('Token endpoint returned an invalid response.')
    result = dict(tokens)
    result.pop('expires_at', None)
    lifetime = tokens.get('expires_in')
    if isinstance(lifetime, (int, float)) and not isinstance(lifetime, bool):
        try:
            expires_at = (time.time() if now is None else now) + lifetime
            if lifetime > 0 and math.isfinite(expires_at):
                result['expires_at'] = expires_at
        except (OverflowError, ValueError):
            pass
    return result


def exchange_code(config, code, timeout=30):
    data = urllib.parse.urlencode({'grant_type': 'authorization_code', 'code': code,
                                   'redirect_uri': config['redirect_uri']}).encode('ascii')
    basic = base64.b64encode((config['client_id'] + ':' + config['client_secret']).encode()).decode('ascii')
    request = urllib.request.Request(TOKEN_URL, data=data, headers={
        'Authorization': 'Basic ' + basic, 'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'})
    try:
        # Default HTTPSHandler retains certificate/hostname verification.
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=min(30, timeout)) as response:
            if response.status != 200:
                raise ValueError()
            raw = response.read(MAX_JSON + 1)
        if len(raw) > MAX_JSON:
            raise ValueError()
        return validate_tokens(json.loads(raw))
    except Exception:
        raise SafeError('Suunto token exchange failed. Restart the helper to try again.') from None


class AuthorizationServer(HTTPServer):
    allow_reuse_address = False

    def __init__(self, config, config_path, timeout=600, exchange=exchange_code):
        if not 0 < timeout <= 600:
            raise SafeError('Timeout must be between 1 and 600 seconds.')
        self.config = dict(validate_config(config))
        parsed = urllib.parse.urlsplit(config['redirect_uri'])
        self.origin = f'http://{parsed.netloc}'
        self.expected_host = parsed.netloc
        self.config_path = Path(config_path)
        self.csrf = secrets.token_urlsafe(32)
        self.state = secrets.token_urlsafe(32)
        self.used = False
        self.connected = False
        self.deadline = time.monotonic() + timeout
        self.exchange = exchange
        self.connection = None
        super().__init__(('127.0.0.1', parsed.port), Handler)
        self.timeout = min(1, timeout)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(max(0.01, min(2, self.deadline - time.monotonic())))
        self.connection = connection
        return connection, address

    def expire_connection(self):
        if self.connection is not None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def authorization_url(self):
        return AUTHORIZE_URL + '?' + urllib.parse.urlencode({
            'response_type': 'code', 'client_id': self.config['client_id'],
            'redirect_uri': self.config['redirect_uri'], 'state': self.state})

    def exchange_before_deadline(self, code):
        # urllib timeouts bound individual operations, so isolate the network work
        # and bound the complete exchange here. This daemon never writes files.
        deadline = min(self.deadline, time.monotonic() + 30)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SafeError('Authorization expired.')
        result = queue.Queue(maxsize=1)
        config = dict(self.config)

        def network_only():
            try:
                value = self.exchange(config, code, timeout=remaining)
            except Exception:
                result.put_nowait((False, None))
            else:
                result.put_nowait((True, value))

        worker = threading.Thread(target=network_only, daemon=True)
        worker.start()
        try:
            successful, value = result.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise SafeError('Authorization expired.') from None
        if not successful or time.monotonic() >= deadline:
            raise SafeError('Authorization exchange failed or expired.')
        return value

    def run(self):
        watchdog = threading.Timer(max(0, self.deadline - time.monotonic()), self.expire_connection)
        watchdog.daemon = True
        watchdog.start()
        try:
            while not self.connected and time.monotonic() < self.deadline:
                self.timeout = min(1, max(0, self.deadline - time.monotonic()))
                self.handle_request()
        finally:
            watchdog.cancel()
            self.server_close()
        return self.connected

    def handle_error(self, request, client_address):
        # Never print request targets, credential-bearing exceptions or bodies.
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'LocalAuthorization'
    sys_version = ''

    def log_message(self, *args):
        pass

    def reply(self, status, message, location=None):
        body = ('<!doctype html><html><meta charset="utf-8"><title>Suunto authorization</title>'
                '<body>' + message + '</body></html>').encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Referrer-Policy', 'same-origin' if self.command == 'GET' and self.path == '/setup' else 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        if location:
            self.send_header('Location', location)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def valid_host(self):
        return self.headers.get_all('Host') == [self.server.expected_host]

    def target(self):
        if (not self.valid_host() or time.monotonic() >= self.server.deadline
                or len(self.path) > MAX_BODY or not self.path.startswith('/')
                or self.path.startswith('//') or self.requestline.split()[1].startswith('//')):
            return None
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return None
        return parsed

    def do_GET(self):
        parsed = self.target()
        if parsed is None:
            self.reply(400, '<p>Invalid local request.</p>')
            return
        if parsed.path == '/setup' and not parsed.query:
            if self.server.used:
                self.reply(409, '<p>This authorization attempt has ended. Restart the helper.</p>')
            elif 'client_secret' not in self.server.config:
                self.reply(200, '<h1>Connect Suunto</h1><p>Enter the existing Suunto app client secret. '
                           'It stays on this computer.</p><form method="post" action="/setup">'
                           '<input type="hidden" name="csrf" value="' + self.server.csrf + '">'
                           '<label>Existing client secret <input type="password" name="client_secret" '
                           'required maxlength="4096" autocomplete="off"></label>'
                           '<button type="submit">Save and authorize Suunto</button></form>')
            else:
                self.reply(200, '<h1>Connect Suunto</h1><a href="' + html.escape(self.server.authorization_url(), quote=True)
                           + '">Authorize Suunto</a>')
            return
        if parsed.path != CALLBACK:
            self.reply(404, '<p>Not found.</p>')
            return
        try:
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=16)
            states = query.get('state', [])
            if (self.server.used or len(states) != 1 or not states[0]
                    or not hmac.compare_digest(states[0].encode(), self.server.state.encode())
                    or 'client_secret' not in self.server.config):
                raise ValueError()
        except (ValueError, UnicodeError):
            self.reply(400, '<p>Invalid or expired authorization state. Restart the helper.</p>')
            return
        self.server.used = True
        codes = query.get('code', [])
        if 'error' in query or len(codes) != 1 or not credential(codes[0]):
            self.reply(400, '<p>Authorization was not granted. Restart the helper.</p>')
            return
        try:
            tokens = validate_tokens(self.server.exchange_before_deadline(codes[0]))
            if time.monotonic() >= self.server.deadline:
                raise SafeError('Authorization expired.')
            private_json(self.server.config_path.parent / 'tokens.json', tokens)
        except Exception:
            self.reply(502, '<p>Authorization could not be saved. Restart the helper to try again.</p>')
            return
        self.server.connected = True
        self.reply(200, '<h1>Suunto connected</h1><p>Authorization tokens were saved privately on this computer. '
                   'Workout import is not yet implemented. You can close this tab.</p>')

    def do_POST(self):
        parsed = self.target()
        if (parsed is None or parsed.path != '/setup' or parsed.query
                or self.headers.get_all('Origin') != [self.server.origin]
                or self.headers.get('Transfer-Encoding') is not None
                or self.headers.get_all('Content-Type') != ['application/x-www-form-urlencoded']):
            self.reply(400, '<p>Invalid local form request.</p>')
            return
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                raise ValueError()
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY:
                raise ValueError()
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError()
            form = urllib.parse.parse_qs(body.decode('utf-8'), keep_blank_values=True, max_num_fields=4)
            if (set(form) != {'csrf', 'client_secret'} or len(form['csrf']) != 1
                    or len(form['client_secret']) != 1
                    or not hmac.compare_digest(form['csrf'][0].encode(), self.server.csrf.encode())
                    or not credential(form['client_secret'][0]) or self.server.used
                    or 'client_secret' in self.server.config):
                raise ValueError()
        except (ValueError, UnicodeError):
            self.reply(400, '<p>Invalid local form request.</p>')
            return
        config = dict(self.server.config, client_secret=form['client_secret'][0])
        try:
            private_json(self.server.config_path, config)
        except Exception:
            self.reply(500, '<p>Could not save the private configuration.</p>')
            return
        self.server.config = config
        self.reply(200, '<h1>Private configuration saved</h1><a href="'
                   + html.escape(self.server.authorization_url(), quote=True) + '">Authorize Suunto</a>')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--timeout', type=int, default=600, help='Lifetime in seconds (1–600).')
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.timeout <= 600:
            raise SafeError('Timeout must be between 1 and 600 seconds.')
        config = load_config(args.config)
        server = AuthorizationServer(config, args.config, args.timeout)
        print('Open ' + server.origin + '/setup on this Mac. The helper expires within '
              + str(args.timeout) + ' seconds.', flush=True)
        connected = server.run()
        print('Suunto connected; workout import is not yet implemented.' if connected
              else 'Authorization timed out; restart the helper to try again.', flush=True)
        return 0 if connected else 1
    except KeyboardInterrupt:
        print('Authorization stopped.', file=sys.stderr)
        return 1
    except Exception:
        print('Suunto authorization could not start. Check the private configuration and local port.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
