# Oura Explore

Personal research and development project to combine Oura recovery data with Suunto running workouts, maintain a user-controlled archive, and support training planning through an optional AI assistant and Model Context Protocol (MCP).

## Project status

Early development. No working data integration or public hosted service is currently provided. API registration and access are being arranged. The policies below describe the intended personal-use implementation and must be updated before offering a service to other users.

## Planned capabilities

- Oura OAuth2 connection for sleep, readiness, daily activity and heart-rate data.
- Suunto running workout import, subject to API approval; FIT import as a fallback.
- Local storage, reproducible metrics and data freshness reporting.
- Optional AI-assisted daily reviews and weekly training proposals.
- Read-only MCP tools initially.

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

Reports and training proposals may be incomplete or incorrect. They are informational and are not medical diagnoses or a substitute for professional care. The user decides whether to adopt any training proposal.

No software license has been selected yet; public visibility alone does not grant a license. These terms and the privacy policy will be revised as implementation choices are finalized.
