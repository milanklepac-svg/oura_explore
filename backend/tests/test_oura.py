import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from app.oura import OuraClient

class OuraTests(unittest.TestCase):
    def test_refresh_rotation_and_pagination(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p/'oura.json').write_text(json.dumps({'client_id':'test-id','client_secret':'test-secret'}))
            (p/'oura-tokens.json').write_text(json.dumps({'access_token':'expired','refresh_token':'old-refresh'}))
            def handle(request):
                if request.url.path == '/oauth/token':
                    return httpx.Response(200,json={'access_token':'new-access','refresh_token':'new-refresh'})
                if request.headers['authorization'] == 'Bearer expired':
                    return httpx.Response(401)
                self.assertEqual(json.loads((p/'oura-tokens.json').read_text())['refresh_token'],'new-refresh')
                if request.url.params.get('next_token'):
                    return httpx.Response(200,json={'data':[{'id':'second'}]})
                return httpx.Response(200,json={'data':[{'id':'first'}],'next_token':'next'})
            with patch.dict(os.environ,{'SECRETS_DIR':folder}):
                client=OuraClient()
            client.http.close()
            client.http=httpx.Client(transport=httpx.MockTransport(handle))
            self.assertEqual([r['id'] for r in client.records('sleep','2026-10-01','2026-10-21')],['first','second'])
            self.assertEqual((p/'oura-tokens.json').stat().st_mode & 0o777,0o600)
            client.close()

    def test_repeated_cursor_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'oura-tokens.json').write_text(json.dumps({'access_token':'test'}))
            with patch.dict(os.environ,{'SECRETS_DIR':folder}):client=OuraClient()
            client.http.close()
            client.http=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'data':[],'next_token':'same'})))
            with self.assertRaisesRegex(RuntimeError,'repeated cursor'):
                list(client.records('sleep','2026-10-01','2026-10-21'))
            client.close()
