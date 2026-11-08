# Testing without private data

Run from the repository root with Docker Desktop (Compose v2.24 or later) and Python 3:

```sh
python3 scripts/check_local.py
```

The same command runs locally on Apple Silicon and on the GitHub `ubuntu-24.04` runner. The first run downloads/builds the app, test dependencies and the official multi-architecture Selenium Chromium image. It requires internet access for those downloads; test services run on an internal Docker network afterward. No paid service, Oura account, host browser or Python environment is required.

The command first runs `python3 scripts/privacy_check.py`, and source-tool unit tests, then the existing backend unit suite, the MCP SDK contract test in its separate dependency image, integration tests against real HTTP endpoints and disposable PostgreSQL, then Selenium tests against the built React/nginx application in headless Chromium. Integration tests verify sync queue completion and idempotent persisted upserts, profile persistence/validation, guidance, plan revisions/audit records and cross-origin write rejection. The browser saves settings, reloads to verify persistence, and reviews a session.

Only the Oura provider is replaced: `tests/integration/fake_worker.py` supplies explicitly synthetic rolling dates and fixed values to the real worker loop. The real queue, PostgreSQL advisory lock, worker transactions, API and browser remain in use. The suite never reads `.env`, `.secrets`, real exports or the running application's database. It does not start the production worker or contact Oura. No test service exposes a host port.

Every invocation generates a unique `personal-coach-test-…` Compose project. PostgreSQL uses memory-backed storage, with no persistent volume. Cleanup removes only that invocation's test containers and network, including after normal errors and interrupts. The script never combines `compose.test.yaml` with production `compose.yaml`, never runs production `down`, and never removes production volumes. A force kill or host shutdown can prevent cleanup; identify the exact test project with `docker compose ls` and use the test file and that exact project name for cleanup. Never substitute `personal-coach`.

## Pull request checks and cost control

`.github/workflows/ci.yml` triggers on pull requests and manual dispatch only. Both use a job-level repository-variable gate requiring both `LOCAL_COACH_HOSTED_CI_ENABLED == 'true'` and `LOCAL_COACH_BILLING_ZERO_VERIFIED == 'true'`. Either variable being unset or false skips the job before a GitHub-hosted runner is allocated. Keep it disabled until the repository owner has verified the account's included Actions minutes and configured a hard $0 overage spending limit. A skipped check is not evidence the tests passed; the local command works while hosted CI is disabled.

The workflow uses one standard Ubuntu runner, read-only repository permissions, no saved checkout credentials, a 20-minute timeout and cancellation of stale runs. It uploads no artifacts, saves no Actions caches, and never deploys or publishes images. The local Docker build cache can still speed up repeat local runs. Current dependency versions are pinned where provided by the app and the Selenium release is explicit; image tags are not immutable digests. Review upstream versions deliberately when updating them.

No tests publish screenshots, records or credentials. Failures print only test output and bounded logs from the synthetic API/worker/web stack. Hosted CI cost remains controlled by the owner-managed gate and account spending settings, not by this workflow alone.
