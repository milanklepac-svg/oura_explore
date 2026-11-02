# Run locally

Prerequisites: Docker Desktop with its engine running, Docker Compose, and connected Oura credentials in `.secrets/`.

```sh
python3 scripts/start_local.py
```

Open http://localhost:8080 . Every invocation of the startup command refreshes Oura, even if the containers are already running. The command waits for the requested sync to complete and reports failures without clearing cached data. Use `--no-build` to reuse existing images. The worker also requests a refresh every time its process starts, regardless of when the last sync completed.

Each sync fetches the configured lookback window (90 inclusive days by default), paginates provider responses and updates matching record IDs. Hourly sync and the Sync now button use the same window. Set `SYNC_LOOKBACK_DAYS` (1–365) and `SYNC_INTERVAL_SECONDS` (default 3600) in local `.env`, then relaunch to apply. Open the Oura mobile app to ensure the ring has synced.

PostgreSQL is a rebuildable cache, still required as a running service for the current dashboard/MCP implementation. A fresh empty database is populated automatically; a pre-existing archive is not required. Do not delete the current database as a normal startup step. Records older than the chosen lookback window remain cached; this is not an exact mirror of provider deletions or a guarantee Oura will return all historical data. After deliberately recreating the database, rerun `scripts/setup_mcp.py` to restore the MCP reader role.

```sh
docker compose ps
docker compose logs --tail=50 worker
docker compose down
```

`down` preserves the database volume. Do not use `down -v` unless deliberately deleting local data.

## Modules

- `frontend/`: React dashboard; Nginx proxies same-origin API requests.
- `backend/app/api.py`: read APIs and sync-job requests; no Oura secrets mounted.
- `backend/app/worker.py`: single-worker scheduler, PostgreSQL advisory lock, idempotent record upserts.
- `backend/app/oura.py`: paginated Oura adapter, one retry on expired access tokens, atomic refresh-token rotation.
- `backend/app/db.py`: owner-scoped persistence for original Oura records and job status.

Only the web container publishes a port, bound to loopback. PostgreSQL data persists in a Docker volume. The worker mounts only `.secrets/oura-runtime/`; it never sees the GitHub token. Setup copies existing Oura credentials into that runtime folder once. Thereafter the runtime copy is authoritative: never run the old OAuth helper or another refresh client concurrently against the same refresh token.

## Local MCP

See [MCP.md](MCP.md) for the optional read-only MCP container, database reader setup and client configuration.

## Cloud deployment boundary

Images are portable, but this version is single-owner and local-only. Before Internet exposure, add authentication and owner authorization, HTTPS, managed secret storage and token persistence, backups, schema migrations, and production observability. Current schema bootstrapping uses create_all; it is not a migration system. Future MCP tools should call the same application services, not access tokens in the dashboard.

Missing records remain missing. Daily records are date-keyed using provider day fields; sleep detail uses the longest sleep record for a day in history. The header shows each latest metric date independently. No medical diagnosis or automatic training prescription is performed.

## Current limitations

Heart-rate time series is not yet imported; resting heart rate and HRV come from overnight sleep records. Scheduled sync uses reconciliation polling, not webhooks. HTTP 429 and provider failures are reported as failed jobs; a subsequent manual or scheduled sync retries. This is an initial development app, not a multi-user production service.
