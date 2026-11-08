# Personal Coach development team

Read this file, the assigned ticket and relevant files only. Source map: backend/app,
frontend/src, backend/tests, tests, scripts; docs/TEAM.md describes the delivery flow.

## Roles and work
The orchestrator owns acceptance criteria, workspaces, deterministic checks and PRs.
Use backend_developer or frontend_developer for a focused implementation and qa for
independent verification. At most two developers may work concurrently, on separate
branches with disjoint file ownership. QA follows a completed change; avoid idle agents.
Do not fork full chat history. Handoffs: ticket ID, changed files, test evidence, blockers,
under 200 words. Search first, read small relevant sections, never dump entire trees.
Use the current project/CLI model. Do not buy credits, create API keys, install paid services
or launch recursive agents. Stop at a usage limit; resume from saved state later.

## Privacy
Use invented fixtures only. Never read .secrets, .env, runtime volumes, real provider
payloads, routes, health records or private training profiles. Do not call the coaching
MCP from development agents. Never copy those files into workspaces or prompts.
Role instructions are not an OS isolation boundary; Codex inherits parent permissions.
Agent workspaces contain committed source only; CLI runs disable inherited MCP/config.
Do not print credentials or matching private values when reporting scan failures.

## Delivery
Tickets have stable PC-0001 identifiers and human-readable titles. Branches are
feat/PC-0001-short-description. PR titles start PC-0001: and close the GitHub issue.
Develop only in the private repository. No direct public push, public PR or history
mirror. Public export requires a separate source allowlist review and privacy gate.
Never commit directly to main. The controller commits and pushes after local checks
and QA pass. No automatic merge until exact-head GitHub checks are verified.
Do not change permissions, disable gates, or modify tests merely to make them green.
Tests must exercise behavior with synthetic data. The local Docker test project is
separate from the running application. Never stop production or delete its volumes.
