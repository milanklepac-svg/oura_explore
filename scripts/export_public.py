#!/usr/bin/env python3
"""Export an exact committed source snapshot locally. Never pushes or publishes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from privacy_check import CONFIG_PATH, GENERATED_DIRS, MAX_FILE_BYTES, allowed_path, forbidden_path, git, scan_bytes, validate_config


def untracked_sensitive_paths(root: Path, config: dict) -> list[dict[str, str]]:
    """Check path names under allowed source directories without opening data."""
    findings = []
    tracked = set(git(root, "ls-files", "-z").decode().split("\0"))
    for directory in config["directories"]:
        base = root / directory
        if base.is_symlink():
            findings.append({"path": directory, "rule": "symlink-source"})
            continue
        for current, dirs, files in os.walk(base, followlinks=False):
            for name in [*dirs, *files]:
                if name in dirs and name in GENERATED_DIRS:
                    continue
                full = Path(current) / name
                path = full.relative_to(root).as_posix()
                if path not in tracked and (forbidden_path(path) or full.is_symlink()):
                    findings.append({"path": path, "rule": "untracked-sensitive-source-path"})
            dirs[:] = [d for d in dirs if d not in GENERATED_DIRS and not forbidden_path((Path(current) / d).relative_to(root).as_posix()) and not (Path(current) / d).is_symlink()]
    return sorted(findings, key=lambda f: (f["path"], f["rule"]))


def export_source(root: Path, ref: str, destination: Path) -> dict:
    root = root.resolve()
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(root):
        raise ValueError("destination must be a new directory outside the private repository")
    commit = git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
    config_entry = git(root, "ls-tree", commit, "--", CONFIG_PATH).split()
    if len(config_entry) != 4 or config_entry[0] != b"100644" or config_entry[1] != b"blob":
        raise ValueError("invalid committed allowlist")
    if int(git(root, "cat-file", "-s", config_entry[2].decode())) > MAX_FILE_BYTES:
        raise ValueError("invalid committed allowlist")
    config_data = git(root, "cat-file", "blob", config_entry[2].decode())
    config = validate_config(json.loads(config_data))
    findings = untracked_sensitive_paths(root, config)
    files = []
    snapshot: dict[str, bytes] = {}
    for record in git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode().split()
        path = raw_path.decode("utf-8")
        p = PurePosixPath(path)
        if p.is_absolute() or ".." in p.parts or str(p) != path or forbidden_path(path):
            findings.append({"path": path, "rule": "forbidden-source-path"})
            continue
        if not allowed_path(path, config):
            # Omitted files have no place in the public snapshot or manifest.
            continue
        if mode not in {"100644", "100755"} or kind != "blob":
            findings.append({"path": path, "rule": "nonregular-source"})
            continue
        if int(git(root, "cat-file", "-s", object_id)) > MAX_FILE_BYTES:
            findings.append({"path": path, "rule": "oversize-source"})
            continue
        data = git(root, "cat-file", "blob", object_id)
        for rule in scan_bytes(path, data):
            findings.append({"path": path, "rule": rule})
        snapshot[path] = data
        files.append({"path": path, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "executable": mode == "100755"})
    if findings:
        return {"findings": sorted(findings, key=lambda f: (f["path"], f["rule"]))}
    manifest = {"version": 1, "commit": commit, "allowlist_sha256": hashlib.sha256(config_data).hexdigest(), "files": sorted(files, key=lambda f: f["path"])}
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".public-source-", dir=destination.parent))
    try:
        for entry in manifest["files"]:
            target = staging / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(snapshot[entry["path"]])
            target.chmod(0o755 if entry["executable"] else 0o644)
        (staging / "PUBLIC_SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"findings": [], "commit": commit, "file_count": len(files)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", required=True, help="committed ref to resolve once to an exact commit")
    parser.add_argument("--destination", type=Path, required=True, help="new local directory outside the private repository")
    args = parser.parse_args()
    try:
        result = export_source(args.root, args.ref, args.destination)
    except (OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError):
        result = {"findings": [{"path": CONFIG_PATH, "rule": "export-check-error"}]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
