# Private development and public source export

The development repository stays private. Wearable records, health exports, GPS
tracks, databases, local configuration, credentials and personal paths must stay
outside published source and container images. Keeping a repository private does
not make it safe to publish its history later.

Run the source gate before committing and before preparing a release:

```sh
python3 scripts/privacy_check.py
python3 -m unittest discover -s tests -p 'test_privacy.py'
```

The gate examines tracked source and allowlisted untracked source. It enumerates
file names under the source directories to catch ignored sensitive files, but
never opens forbidden paths such as `.env`, `.secrets`, data directories,
databases or health export formats. Generated dependency and cache directories
are pruned. Findings contain only a relative path and rule name. Source files
larger than 2 MiB, binary files and symlinks fail closed.

Content rules identify common credential formats and literal credential
assignments, personal email addresses, user home paths, and structured JSON
records containing coordinate pairs or multiple health measures. Deliberate
`example.com`, `example.org`, `example.net`, `.example`, `test.invalid` and
`test.local` addresses and clear credential placeholders are permitted. Generic
thresholds, field names and synthetic Python test cases remain valid source.
Do not add blanket exceptions for a file or directory to bypass a finding.

`config/public-source.json` is the reviewable source allowlist. It names root
files and source directories with permitted extensions. Review any allowlist
change as a publication boundary change. New top-level source files must be
added deliberately; tracked files outside the allowlist fail the working gate.

After reviewing and committing the intended source, prepare a new local export
outside the private repository:

```sh
python3 scripts/export_public.py --ref HEAD --destination ../personal-coach-public-review
```

The exporter resolves the ref once to an exact commit, reads the committed
allowlist, checks committed allowlisted content, and copies only regular source
files from that commit. It also rejects committed forbidden paths and untracked
sensitive paths inside allowlisted source directories. Dirty working files are
never copied. There is no `.git` directory, commit history, runtime data or push
step in the export. An existing destination is rejected.

Review `PUBLIC_SOURCE_MANIFEST.json` in the export. It records the exact commit,
allowlist hash, each exported file's relative path, SHA-256 hash, byte count and
executable flag. Its content is deterministic for the same commit. Review every
exported file and build container images only from that reviewed source tree.
The Docker contexts also exclude local configuration and private data; do not
pass secrets through build arguments, copied files or image layers.

Publication is a separate, explicit action after review. Never make the private
development repository public or push its history as a shortcut. If a secret
has entered a commit or image, revoke or rotate it and handle the historical
exposure separately; deleting the current file is insufficient.

These checks are useful release safeguards, not complete anonymization or GDPR
certification. They cannot establish consent, lawful processing, prove data is
synthetic, recognize every identifying fact in prose, or detect every secret
format. Human review remains necessary before any public release.
