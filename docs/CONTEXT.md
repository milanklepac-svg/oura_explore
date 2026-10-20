# Personal Coach — project context

Updated: 20 October 2026

## Goal
Build a personal, provider-independent archive and MCP integration combining Oura Ring 24/7 recovery/activity data with Suunto 9 running workouts. Add optional AI-assisted training formation, reviews and weekly planning. Personal use initially on the owner’s Mac.

## Owner and repository
Milan Klepac, Serbia. GitHub repository: https://github.com/milanklepac-svg/oura_explore (public). Repository README contains project description, Privacy Policy and Terms of Use. Never commit credentials, tokens, health records or GPS tracks.

## Oura — connected and verified
Application: Personal Running Coach. Registered in https://developer.ouraring.com/applications. OAuth callback: http://localhost:8765/oauth/oura/callback .
Credentials: .secrets/oura.json . Tokens: .secrets/oura-tokens.json . Both restricted to owner access. Do not print or copy their contents into documentation or public files. Tokens are not encrypted at rest; file permissions provide access restriction.
Granted scopes: extapi:daily extapi:heartrate extapi:tag extapi:workout extapi:stress. Read-only API checks succeeded for daily_sleep, sleep, daily_readiness, daily_activity, heartrate, daily_stress, tag and workout. Empty tag results were successful responses. Heart-rate result was one page of 1,000 samples, not a complete backfill.
OAuth helper: scripts/oura_authorize.py . It uses state validation and PKCE and saves tokens privately. Callback process completed. Implement robust token refresh with single-use refresh-token rotation and atomic persistence before ongoing collection. Do not rerun authorization unless needed.
Reference OpenAPI specification remains at /Users/milanklepac/Downloads/openapi-1.42.json; it contains documentation, not credentials.

## Suunto — application submitted, awaiting review
Submitted and electronically signed on 20 October 2026 with explicit user confirmation. Applied honestly as an individual, non-commercial personal development project; no company or marketing partnership claimed. Contact/developer email was the same Proton Mail address entered for Oura.
Confirmation states review within two weeks, approximately 3 November 2026. Personal-use eligibility is uncertain because published API policy excludes personal use, despite community reports. No Suunto API credentials or OAuth authorization yet. User should check their inbox for approval, invitation or questions. No monitoring automation has been created.
Application: https://survey.alchemer.eu/s3/90553908/PARTNER-Become-a-Suunto-Partner
After approval: subscribe to Developer API, obtain subscription key, configure OAuth application, then authorize own Suunto account and verify a workout. FIT imports are fallback if API access is declined.

## Architecture and next work
Proposed stack (not implemented): Python, SQLite and official MCP Python SDK. Separate scheduled collectors, deterministic calculations, MCP tools and optional coaching agent. Oura is primary for recovery; Suunto is primary for runs. Deduplicate overlapping workouts, preserve provenance/freshness/time zones and missing values. Include subjective effort, symptoms, schedule and running goals before training formation.
Next milestone: build reliable Oura collector with pagination, refresh, incremental sync and local archive; add Suunto after approval. Start with daily recovery brief and weekly running review, then training proposals. Exact Suunto 9 model, Oura generation and running goal still need confirmation.

## Files and prior deliverables
Local implementation and secrets were moved into this folder at the user’s request. Existing screenshots remain in ../outputs so earlier chat links stay valid: oura-registration.png, oura-created.png, suunto-agreement.png, suunto-submitted.png. Some screenshots contain contact information; do not publish them.

## Development checkout
Canonical repository checkout: /Users/milanklepac/Code/personal-coach . Git remote uses HTTPS. Oura credentials and tokens moved here from the earlier chat workspace. Screenshots remain in the earlier chat outputs folder.
