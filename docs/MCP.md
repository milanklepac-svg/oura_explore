# Local Oura MCP

Personal Coach exposes six read-only tools using the official MCP Python SDK, pinned to 2.3.0 in a separate image. The server reads the existing PostgreSQL archive; it does not call Oura or mount wearable/GitHub credentials.

## Prepare

Docker Desktop and the existing database/API must be running. From the project folder:

```sh
python3 scripts/setup_mcp.py
docker compose --profile mcp build mcp
python3 scripts/test_mcp_stdio.py
```

Setup creates a separate `coach_mcp` database login with SELECT grants on wearable records and sync jobs. Its randomly generated password stays in ignored `.env`. Transactions default to read-only. The role is not a superuser and cannot create databases or roles. It is not a tenant boundary: this remains a single-owner local application; future multi-user deployment needs independent authorization and database isolation policies.

`mcp` is an optional Compose profile. Do not run it as a detached web service. The MCP client launches a temporary container through `scripts/run_mcp.sh`, talks over stdin/stdout, and removes it on exit. No TCP port, public tunnel, extra API token or Oura approval is needed. The database and worker continue to run independently. Start the dashboard normally if they are stopped.

## Connect a local MCP host

`config/mcp.example.json` contains the configuration for hosts that use the `mcpServers` JSON convention. It uses `/bin/sh`; replace `/absolute/path/to/personal-coach` with your actual checkout path. Other hosts should configure the same executable and argument using their own MCP settings. Do not copy GitHub or Oura secrets into client settings. Docker Desktop must be running, and a client allowed to launch Docker has access to the local Docker engine.

No host configuration has been installed automatically. When connecting a cloud-powered assistant, requested tool results may be sent to its model provider; local storage does not imply local model inference. This implementation does not provide a remote ChatGPT connector or an Internet-accessible endpoint. Keep it local as agreed.

## Training preferences

Configure goals and coaching thresholds in the local app. The training tool reads these saved settings and accepts a request-only check-in; see [TRAINING.md](TRAINING.md).

## Tools

| Tool | Inputs | Result |
| --- | --- | --- |
| `get_data_status` | none | Available datasets, record counts, date coverage, latest sync status and last successful sync |
| `get_daily_metrics` | `start_date`, `end_date` | Sleep/readiness, overnight HRV/lowest HR, steps, stress and recovery seconds; explicit missing days |
| `get_recovery_context` | `day`, optional `baseline_days` (7–60, default 28) | Current metrics vs arithmetic mean of available previous days; observed-day counts and deltas |
| `get_workouts` | date range, optional `activity`, `offset`, `limit` | Oura workouts, duration and distance; at most 100 per page, next offset when needed |
| `get_training_summary` | date range | Workout totals by activity and missing-value counts |
| `get_training_guidance` | day, planned session, optional minutes and check-in | Explain whether to retain or reconsider a session using app-saved rules and current data quality |

Dates use ISO `YYYY-MM-DD`, inclusive, at most 90 days per request. Dates are provider days, not dates reconstructed from UTC. Overnight metrics select the longest main sleep; short sleeps and rest records are excluded. Every result includes source, collection freshness and limitations. Missing values are `null`, not zero. A baseline uses only observed values and excludes the target day; a mean based on a few observations should not be treated as a stable physiological baseline.

Workouts expose an allowlist of fields. Free-text labels, routes and GPS coordinates are not exposed. No MCP tools write records, change training preferences, queue a sync, refresh tokens, send workouts, run arbitrary SQL, or diagnose overtraining. Use `python3 scripts/start_local.py` to launch and refresh the app, or the dashboard's Sync now button for fresh collection. MCP reads the resulting cache; it does not itself wait for a provider sync. Suunto and continuous heart-rate samples remain unavailable.

Example questions after connecting a host:

- What Oura data is available, and when was it last synced?
- Compare my recovery today with the preceding 28 days and show gaps in the baseline.
- List my running workouts from the last seven days.
- Summarize recorded workout duration and distance by activity this month.

## Verification

Unit/SDK tests use isolated synthetic data, not your health records:

```sh
docker compose --profile mcp run --rm -T --no-deps -e DATABASE_URL=sqlite:// \
  -v "$PWD/backend/tests:/tests:ro" mcp \
  python -m unittest discover -s /tests -p test_oura_queries.py

docker compose --profile mcp run --rm -T --no-deps -e DATABASE_URL=sqlite:// \
  -v "$PWD/backend/tests:/tests:ro" mcp \
  python -m unittest discover -s /tests -p test_mcp_contract.py

docker compose --profile mcp run --rm -T --no-deps \
  -v "$PWD/scripts/check_mcp_reader.py:/check.py:ro" mcp python /check.py
```

`test_mcp_stdio.py` launches the actual container and performs protocol initialization, tool discovery, all six calls and invalid-range rejection. It prints pass/fail only, not private tool payloads. The reader check attempts a no-row UPDATE inside a read-write transaction and verifies PostgreSQL denies it.

## Modules and cloud path

`app/services/oura_queries.py` contains the owner-scoped projections and calculations; `app/mcp_server.py` provides MCP schemas and sanitized database errors. Other transports can reuse the query service. No dashboard rebuild is needed for this addition. For a future remote deployment, add authenticated Streamable HTTP, origin/host validation and tenant isolation before exposing data.

Official SDK references: https://py.sdk.modelcontextprotocol.io/ and https://py.sdk.modelcontextprotocol.io/run/ .
