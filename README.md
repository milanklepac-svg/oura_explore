# Oura Explore

Personal research and development project to combine Oura recovery data with Suunto running workouts, maintain a user-controlled archive, and support training planning through an optional AI assistant and Model Context Protocol (MCP).

## Project status

A local containerized Oura dashboard is implemented. React, FastAPI, a dedicated sync worker and PostgreSQL run through Docker Compose. No public hosted service is provided. Suunto API approval is pending.

Launch with `python3 scripts/start_local.py` to start the containers and refresh the Oura cache every time, including when services are already running.

See [local development instructions](docs/LOCAL_DEVELOPMENT.md) for setup, architecture and cloud deployment prerequisites. Private health records and credentials remain outside Git. The policies below describe the intended personal-use implementation and must be updated before offering a service to other users.

## Planned capabilities

- Oura OAuth2 connection for sleep, readiness, daily activity and heart-rate data.
- Suunto running workout import, subject to API approval; FIT import as a fallback.
- Local storage, reproducible metrics and data freshness reporting.
- Optional AI-assisted daily reviews and weekly training proposals.
- Local MCP tools for recovery context and bounded race-plan adjustments.

## Training review

The dashboard includes configurable goals, running availability and recovery rules. A session review is available in the app and through MCP. See [training logic and limits](docs/TRAINING.md).

## Race calendar and automatic adjustments

The local app supports multiple main races, intermediate milestones and target finish times. Its original easy/long-run template can shorten or ease sessions for poor recovery, cautiously reschedule missed long-run focus, and reduce the next week after repeated skips. Recent Oura runs provide recorded volume and spacing context. A Daniels-inspired option uses current fitness, explicit workout purposes and bounded quality work; see [approach and sources](docs/DANIELS.md). Choose automatic or approval-based mode in the app. See [race planning, storage and limits](docs/RACE_PLANNING.md).

## Local MCP

A local Oura and training-plan MCP server is available as an optional Docker Compose service. See [MCP setup and tools](docs/MCP.md). It reads the local archive without Oura credentials and exposes no network port.

## Local development team

Private ticket → branch → PR development uses local Mac workspaces, bounded Codex runs, independent QA and a synthetic Docker test stack. See [team setup and commands](docs/TEAM.md) and [test instructions](docs/TESTING.md). Hosted jobs use standard Ubuntu runners after verification of the account zero-spend stop. Public publication uses a separate [reviewed source export](docs/PUBLICATION.md).

## Privacy Policy

Updated: 20 October 2026.

This project is intended for use by its owner with their own wearable accounts. It currently operates no hosted data collection service.

The planned application will request only the wearable permissions needed for selected features. It may process sleep, recovery, activity, heart-rate and running workout records, including location if workout files contain it. OAuth tokens will be used to access authorized accounts.

The intended default is storage on the user-controlled computer. Credentials, wearable records and GPS tracks must never be committed to this public repository.

AI integration is optional. When a cloud AI provider is enabled, selected summaries or records may be sent to that provider for the requested analysis and will be subject to its policies. Local storage does not make cloud AI processing local.

The owner can disconnect an integration by revoking its authorization at the wearable provider and deleting local tokens. Locally retained records and backups can be deleted by the owner. This project does not set an automatic retention period yet.

No advertising, sale of health data or project analytics is planned. GitHub and wearable or AI providers process information under their own policies.

For general project questions, contact the repository owner through GitHub. Do not post private health records or credentials in public issues.

## Terms of Use

Updated: 20 October 2026.

This is an experimental personal-use project. It currently provides no hosted service, availability guarantee or support commitment.

Use only accounts and records you are authorized to access, and comply with the relevant wearable and AI provider agreements. This project is independent of Oura, Suunto and OpenAI and is not endorsed by them.

Reports and training proposals may be incomplete or incorrect. They are informational and are not medical diagnoses or a substitute for professional care. The user controls the adjustment mode and can choose suggestions requiring approval or automatic bounded changes. Race targets remain user-controlled.

No software license has been selected yet; public visibility alone does not grant a license. These terms and the privacy policy will be revised as implementation choices are finalized.
