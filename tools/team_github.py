"""Local controller GitHub access; never imported by application containers."""
import json
import re
import subprocess
import urllib.error
import urllib.request


class GitHub:
    def __init__(self, root, repository):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
            raise ValueError("Invalid repository")
        self.repository = repository
        # Use the existing local credential helper, never a token argument or log.
        response = subprocess.run(
            ["git", "-C", str(root), "credential", "fill"],
            input=f"protocol=https\nhost=github.com\npath={repository}.git\n\n",
            text=True, capture_output=True, check=True,
        )
        fields = dict(line.split("=", 1) for line in response.stdout.splitlines() if "=" in line)
        self._token = fields.get("password", "")
        if not self._token:
            raise RuntimeError("No local GitHub credential available")
        metadata = self.request("GET", "")
        if metadata.get("private") is not True:
            raise RuntimeError("Development controller requires a PRIVATE repository")

    def request(self, method, path, body=None):
        if not path.startswith("/") and path:
            raise ValueError("Invalid API path")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repository}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Authorization": f"Bearer {self._token}",
                     "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            # Provider error bodies can contain submitted text: do not print them.
            raise RuntimeError(f"GitHub request failed (HTTP {error.code})") from None
