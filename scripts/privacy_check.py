#!/usr/bin/env python3
"""Deterministic source-only privacy checks. Reports contain paths/rule names only."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

CONFIG_PATH = "config/public-source.json"
MAX_FILE_BYTES = 2 * 1024 * 1024
PRIVATE_PARTS = {".git", ".secrets", "secrets", "data", "runtime", "exports", "backups", "outputs", "node_modules", ".venv", "__pycache__", ".pytest_cache"}
PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".fit", ".gpx", ".tcx", ".har", ".pem", ".key", ".p12", ".pfx", ".csv", ".tsv", ".jsonl", ".zip", ".tar", ".gz"}
GENERATED_DIRS = {"node_modules", ".venv", "__pycache__", ".pytest_cache"}
PLACEHOLDER = re.compile(r"^(?:test(?:[-_].*)?|example(?:[-_].*)?|dummy(?:[-_].*)?|placeholder|changeme|change-me|replace[-_].*|your[-_].*|not-configured|expired|old-refresh|new-refresh|new-access|<[^>]+>|\$\{[^}]+\})$", re.I)
CREDENTIAL_ASSIGNMENT = re.compile(r'''(?im)["']?\b(?:client_secret|client_id|access_token|refresh_token|api_key|secret_key|password|postgres_password|mcp_database_password)\b["']?\s*[:=]\s*["']([^"'\r\n]{8,})["']''')
ENV_CREDENTIAL = re.compile(r'''(?im)^(?:export\s+)?(?:OURA_)?(?:CLIENT_SECRET|CLIENT_ID|ACCESS_TOKEN|REFRESH_TOKEN|API_KEY|SECRET_KEY|PASSWORD|POSTGRES_PASSWORD|MCP_DATABASE_PASSWORD)\s*=\s*([^\s#'"$]{8,})\s*(?:#.*)?$''')
URL_CREDENTIAL = re.compile(r"\b(?:postgres(?:ql)?(?:\+[a-z]+)?|https?)://[^\s/:]+:([^\s/@]+)@", re.I)
TOKEN = re.compile(r"(?:\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|(?:AKIA|ASIA)[A-Z0-9]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
HOME_PATH = re.compile(r"(?:/(?:Users|home)/([^/\s\"'<>`]+)|[A-Za-z]:[\\/]Users[\\/]([^\\/\s\"'<>`]+))")
EMAIL = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")
EMAIL_PLACEHOLDERS = {"example.com", "example.org", "example.net", "example.invalid", "test.invalid", "test.local"}
HEALTH_KEYS = {"sleep_score", "readiness_score", "average_hrv_ms", "lowest_heart_rate_bpm", "total_sleep_duration", "average_heart_rate", "resting_heart_rate", "steps", "calories", "weight", "spo2_percentage"}


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict) or config.get("version") != 1 or set(config) != {"version", "files", "directories"}:
        raise ValueError("invalid allowlist")
    if not isinstance(config["files"], list) or not isinstance(config["directories"], dict):
        raise ValueError("invalid allowlist")
    for path in [*config["files"], *config["directories"]]:
        if not isinstance(path, str):
            raise ValueError("invalid allowlist path")
        p = PurePosixPath(path)
        if p.is_absolute() or ".." in p.parts or str(p) != path or forbidden_path(path):
            raise ValueError("invalid allowlist path")
    for extensions in config["directories"].values():
        if not isinstance(extensions, list) or not extensions or any(not isinstance(e, str) or not re.fullmatch(r"\.[a-z]+", e) for e in extensions):
            raise ValueError("invalid allowlist extensions")
    return config


def load_config(root: Path) -> dict:
    target = root / CONFIG_PATH
    if target.is_symlink() or target.parent.is_symlink() or target.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("invalid allowlist file")
    return validate_config(json.loads(target.read_text()))


def in_source_directory(path: str, config: dict) -> bool:
    return any(path.startswith(directory + "/") for directory in config["directories"])


def allowed_path(path: str, config: dict) -> bool:
    return path in config["files"] or any(path.startswith(directory + "/") and PurePosixPath(path).suffix in extensions for directory, extensions in config["directories"].items())


def forbidden_path(path: str) -> bool:
    p = PurePosixPath(path)
    return (any(part.lower() in PRIVATE_PARTS or (part.lower().startswith(".env") and part != ".env.example") for part in p.parts)
            or p.suffix.lower() in PRIVATE_SUFFIXES or bool(re.search(r"\.(?:db|sqlite\d?)-(?:wal|shm|journal)$", p.name, re.I)))


def structured_rules(value: object) -> set[str]:
    rules: set[str] = set()
    if isinstance(value, dict):
        numeric = {str(k).lower() for k, v in value.items() if isinstance(v, (float, int)) and not isinstance(v, bool)}
        if ({"latitude", "longitude"} <= numeric or {"lat", "lon"} <= numeric or {"lat", "lng"} <= numeric):
            rules.add("coordinate-record")
        if len(numeric & HEALTH_KEYS) >= 3:
            rules.add("health-record")
        for child in value.values():
            rules.update(structured_rules(child))
    elif isinstance(value, list):
        for child in value:
            rules.update(structured_rules(child))
    return rules


def scan_bytes(path: str, data: bytes) -> list[str]:
    if len(data) > MAX_FILE_BYTES:
        return ["oversize-source"]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["binary-source"]
    rules: set[str] = set()
    if "\x00" in text:
        rules.add("binary-source")
    if TOKEN.search(text):
        rules.add("credential-token")
    if any(not PLACEHOLDER.fullmatch(m.group(1)) and not m.group(1).lstrip().startswith(("+", "%")) and not any(s in m.group(1) for s in ("${", "process.env", "os.environ", "settings.get", "cfg[")) for m in CREDENTIAL_ASSIGNMENT.finditer(text)):
        rules.add("credential-literal")
    if any(not PLACEHOLDER.fullmatch(m.group(1)) and "${" not in m.group(1) for pattern in (ENV_CREDENTIAL, URL_CREDENTIAL) for m in pattern.finditer(text)):
        rules.add("credential-literal")
    for match in HOME_PATH.finditer(text):
        user = match.group(1) or match.group(2)
        if not PLACEHOLDER.fullmatch(user) and user.lower() not in {"username", "user", "runner", "<user>"}:
            rules.add("private-home-path")
    if any(match.group(1).lower() not in EMAIL_PLACEHOLDERS and not match.group(1).lower().endswith(".example") for match in EMAIL.finditer(text)):
        rules.add("personal-email")
    candidates = [text] if PurePosixPath(path).suffix == ".json" else []
    if PurePosixPath(path).suffix == ".md":
        candidates += re.findall(r"```json\s*\n(.*?)```", text, re.S | re.I)
    for candidate in candidates:
        try:
            rules.update(structured_rules(json.loads(candidate)))
        except (ValueError, RecursionError):
            pass
    return sorted(rules)


def working_findings(root: Path, config: dict) -> list[dict[str, str]]:
    findings: set[tuple[str, str]] = set()
    tracked = set(git(root, "ls-files", "-z").decode().split("\0")) - {""}
    untracked = set(git(root, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")) - {""}
    candidates = tracked | {p for p in untracked if allowed_path(p, config) or in_source_directory(p, config)}
    # Enumerate names only: never open ignored/runtime files, even inside source roots.
    for directory in config["directories"]:
        base = root / directory
        if base.is_symlink():
            findings.add((directory, "symlink-source"))
            continue
        if not base.is_dir():
            continue
        import os
        for current, dirs, files in os.walk(base, followlinks=False):
            for name in [*dirs, *files]:
                if name in dirs and name in GENERATED_DIRS:
                    continue
                full = Path(current) / name
                relative = full.relative_to(root).as_posix()
                if forbidden_path(relative):
                    findings.add((relative, "forbidden-source-path"))
                elif full.is_symlink():
                    findings.add((relative, "symlink-source"))
            dirs[:] = [d for d in dirs if d not in GENERATED_DIRS and not forbidden_path((Path(current) / d).relative_to(root).as_posix()) and not (Path(current) / d).is_symlink()]
    for path in sorted(candidates):
        if forbidden_path(path):
            findings.add((path, "forbidden-source-path"))
            continue
        if not allowed_path(path, config):
            if path in tracked:
                findings.add((path, "not-allowlisted"))
            continue
        full = root / path
        if any(parent.is_symlink() for parent in [full, *full.parents] if parent != root and root in parent.parents):
            findings.add((path, "symlink-source"))
        elif full.is_file() and full.stat().st_size > MAX_FILE_BYTES:
            findings.add((path, "oversize-source"))
        elif full.is_file():
            for rule in scan_bytes(path, full.read_bytes()):
                findings.add((path, rule))
        else:
            findings.add((path, "missing-source"))
    return [{"path": path, "rule": rule} for path, rule in sorted(findings)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="emit path/rule-only JSON")
    args = parser.parse_args()
    try:
        findings = working_findings(args.root.resolve(), load_config(args.root))
    except (OSError, ValueError, TypeError, subprocess.CalledProcessError):
        findings = [{"path": CONFIG_PATH, "rule": "source-check-error"}]
    if args.json:
        print(json.dumps({"findings": findings}, sort_keys=True, indent=2))
    elif findings:
        for item in findings:
            print(f'{json.dumps(item["path"])}: {item["rule"]}')
    else:
        print("Source privacy checks passed.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
