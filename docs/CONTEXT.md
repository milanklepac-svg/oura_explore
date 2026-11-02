# Personal Coach — implementation context

Updated: 2 November 2026

## Purpose
Personal wearable archive, local dashboard and read-only MCP tools for Oura recovery data and future Suunto running data. The repository contains code and documentation only. Credentials, tokens, health records, local exports and screenshots are not repository content.

## Implemented
- Docker Compose: React/Nginx web UI, FastAPI API, Oura sync worker, PostgreSQL, optional stdio MCP container.
- Launch with `python3 scripts/start_local.py`, or add `--no-build` to reuse images. Every launch requests an Oura refresh, including when containers are already running.
- Worker startup also queues a refresh. Every collection rechecks a configurable inclusive lookback window (default 90 days); background refresh defaults to hourly.
- Token refresh persists rotated single-use tokens atomically. `.secrets/oura-runtime/` is authoritative for the running worker. Do not run competing refresh clients against the bootstrap credentials.
- PostgreSQL is a rebuildable wearable cache. Failures preserve existing records. User-authored training preferences are separate data in the same database and need a backup before deliberately resetting it.
- The dashboard configures running goals, availability, weekly distance and advisory recovery rules. Session check-in answers are request-only.
- Shared training logic reviews an existing session using recovery, baseline coverage, previous workouts and check-in. It does not generate race programmes, diagnose conditions or automatically increase load.
- Six MCP tools: data status, daily metrics, recovery context, workouts, training summary and training guidance.
- MCP runs on demand without a network listener or wearable credential mounts, using the dedicated SELECT-only database role. Client configuration example is in `config/mcp.example.json`.

## Boundaries
Local, single-owner application. No cloud deployment, multi-user sign-in, public MCP endpoint or production migration system is implemented. Suunto is not connected and should not be treated as a source of available training data. Do not assume any MCP host is configured automatically.

## References
- `LOCAL_DEVELOPMENT.md`: run instructions, modules and cloud prerequisites.
- `MCP.md`: tools, reader role, connection configuration and tests.
- `TRAINING.md`: settings, advisory policy, evidence limits and privacy.

## Publication
The repository is public. Review changes before pushing. Never commit `.env`, `.secrets/`, database files, exports, provider payloads, GPS tracks, private screenshots or personal training settings. Synthetic test fixtures must remain independent of real health records.
