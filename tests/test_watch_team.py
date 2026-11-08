"""Activity view uses invented ticket state and no wearable/runtime fixtures."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("watch_team", ROOT / "scripts/watch_team.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class WatchTeamTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        (root / "config").mkdir()
        (root / "config/team.json").write_text(json.dumps({"repository": "example/private"}))
        (root / ".team/tickets").mkdir(parents=True)
        return root

    def test_state_projection_excludes_private_fields_and_unreported_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            state = {"id": "PC-0001", "title": "Invented task", "role": "backend_developer",
                     "status": "developing", "attempts": 1, "body": "private body is never rendered",
                     "workspace": "private path is never rendered", "access_token": "test-placeholder"}
            (root / ".team/tickets/PC-0001.json").write_text(json.dumps(state))
            row = watch.collect_status(root)["tickets"][0]
            self.assertNotIn("body", row)
            self.assertNotIn("workspace", row)
            self.assertNotIn("access_token", row)
            self.assertIsNone(row["tokens"])
            self.assertEqual(row["url"], "https://github.com/example/private/issues/1")

    def test_bad_state_and_symlinks_are_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            (root / ".team/tickets/PC-0001.json").write_text("not-json")
            (root / ".team/tickets/PC-0002.json").write_text("[]")
            (root / ".team/tickets/PC-0003.json").symlink_to("/nonexistent-private-target")
            self.assertEqual(watch.collect_status(root)["tickets"], [])

    def test_malformed_field_types_are_skipped_and_terminal_workspaces_are_not_scanned(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            valid = {"id": "PC-0001", "title": "Invented", "role": "qa", "status": "merged", "attempts": 1}
            file = root / ".team/tickets/PC-0001.json"
            for bad in [{"role": None}, {"status": 2}, {"attempts": "one"}, {"usage": [1]}, {"usage": {"qa": {"input_tokens": "ten"}}}]:
                file.write_text(json.dumps(dict(valid, **bad)))
                self.assertEqual(watch.collect_status(root)["tickets"], [])
            file.write_text(json.dumps(valid))
            (root / ".team/workspaces/PC-0001").mkdir(parents=True)
            with patch.object(watch.subprocess, "run") as run:
                self.assertEqual(watch.collect_status(root)["tickets"][0]["status"], "merged")
                run.assert_not_called()

    def test_http_is_read_only_and_rejects_foreign_hosts_and_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            server = ThreadingHTTPServer(("127.0.0.1", 0), watch.handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(url + "/api/status") as response:
                    self.assertEqual(json.load(response)["repository"], "example/private")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                with urllib.request.urlopen(url) as response:
                    self.assertIn(b"Personal Coach development team", response.read())
                for path, method, host, expected in [
                    ("/api/status", "GET", "foreign.example", 403),
                    ("/.secrets", "GET", None, 404),
                    ("/api/status", "POST", None, 501),
                ]:
                    request = urllib.request.Request(url + path, method=method,
                                                     headers={"Host": host} if host else {})
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request)
                    self.assertEqual(error.exception.code, expected)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
