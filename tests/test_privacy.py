"""Synthetic privacy regressions. No real health or credential fixtures."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from export_public import export_source
from privacy_check import load_config, scan_bytes, working_findings


class PrivacyTests(unittest.TestCase):
    def test_sensitive_literals_report_rules_only(self):
        cases = [
            ("docs/sample.md", "/Us" + "ers/alice/private", "private-home-path"),
            ("docs/sample.md", "alice" + "@personal.invalid", "personal-email"),
            ("backend/app/sample.py", 'client_' + 'secret = "' + 'aB29cD38eF47gH56iJ65' + '"', "credential-literal"),
            (".env.example", 'OURA_CLIENT_' + 'SECRET=' + 'aB29cD38eF47gH56iJ65', "credential-literal"),
            ("compose.yaml", 'postgresql://coach:' + 'aB29cD38eF47gH56iJ65' + '@localhost/coach', "credential-literal"),
            ("docs/sample.md", "ghp_" + "A" * 30, "credential-token"),
            ("config/sample.json", json.dumps({"latitude": 1.25, "longitude": 2.5}), "coordinate-record"),
            ("config/sample.json", json.dumps({"sleep_score": 80, "readiness_score": 70, "average_hrv_ms": 50}), "health-record"),
        ]
        for path, content, rule in cases:
            with self.subTest(rule=rule):
                self.assertIn(rule, scan_bytes(path, content.encode()))

    def test_generic_rules_and_synthetic_source_are_allowed(self):
        source = '''client_secret = cfg['client_secret']
latitude_min, latitude_max = -90, 90
payload = {'sleep_score': 80, 'readiness_score': 70, 'average_hrv_ms': 50}
contact = 'developer@example.com'
file.write('POSTGRES_PASSWORD=' + secrets.token_urlsafe(32) + '\\n')
'''
        self.assertEqual(scan_bytes("backend/tests/test_example.py", source.encode()), [])
        self.assertEqual(scan_bytes("config/example.json", b'{"client_secret":"your-secret-here"}'), [])

    def test_binary_and_oversize(self):
        self.assertEqual(scan_bytes("frontend/src/data.svg", b"\xff"), ["binary-source"])
        self.assertEqual(scan_bytes("docs/data.md", b"x" * (2 * 1024 * 1024 + 1)), ["oversize-source"])

    def make_repo(self, folder):
        root = Path(folder) / "private"
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "config").mkdir()
        (root / "scripts").mkdir()
        (root / "config/public-source.json").write_text(json.dumps({"version": 1, "files": ["README.md", "config/public-source.json"], "directories": {"scripts": [".py"]}}))
        (root / "README.md").write_text("Public source example.\n")
        self.commit(root)
        return root

    def commit(self, root):
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Privacy Test", "-c", "user.email=test@example.com", "commit", "-qm", "synthetic source"], check=True)

    def test_export_is_exact_committed_snapshot_without_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_repo(folder)
            (root / "README.md").write_text("Uncommitted source must stay private.\n")
            (root / "unlisted.txt").write_text("Omitted by the source allowlist.\n")
            destination = Path(folder) / "public"
            result = export_source(root, "HEAD", destination)
            self.assertEqual(result["findings"], [])
            self.assertEqual((destination / "README.md").read_text(), "Public source example.\n")
            self.assertFalse((destination / ".git").exists())
            self.assertFalse((destination / "unlisted.txt").exists())
            manifest = json.loads((destination / "PUBLIC_SOURCE_MANIFEST.json").read_text())
            self.assertEqual(manifest["commit"], result["commit"])
            self.assertEqual(len(manifest["files"]), 2)
            second = Path(folder) / "public-again"
            export_source(root, "HEAD", second)
            self.assertEqual((destination / "PUBLIC_SOURCE_MANIFEST.json").read_bytes(), (second / "PUBLIC_SOURCE_MANIFEST.json").read_bytes())

    def test_export_rejects_committed_sensitive_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_repo(folder)
            (root / "README.md").write_text("/Us" + "ers/alice/private")
            self.commit(root)
            destination = Path(folder) / "public"
            result = export_source(root, "HEAD", destination)
            self.assertEqual(result["findings"], [{"path": "README.md", "rule": "private-home-path"}])
            self.assertFalse(destination.exists())

    def test_untracked_ignored_sensitive_source_names_are_rejected_without_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_repo(folder)
            (root / ".git/info/exclude").write_text(".env\n")
            (root / "scripts/.env").mkdir()  # A directory proves the check never opens it.
            result = export_source(root, "HEAD", Path(folder) / "public")
            self.assertEqual(result["findings"], [{"path": "scripts/.env", "rule": "untracked-sensitive-source-path"}])

    def test_symlink_is_rejected_without_following(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_repo(folder)
            (root / "scripts/example.py").symlink_to("/nonexistent-private-target")
            findings = working_findings(root, load_config(root))
            self.assertIn({"path": "scripts/example.py", "rule": "symlink-source"}, findings)

    def test_repository_source_scan(self):
        # Real repository source is checked; ignored runtime data is never opened.
        self.assertEqual(working_findings(ROOT, load_config(ROOT)), [])


if __name__ == "__main__":
    unittest.main()
