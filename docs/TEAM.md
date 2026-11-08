# Local development team

The application and its wearable data stay local. Development uses a private GitHub
repository, committed-source workspaces on the Mac, and the existing ChatGPT Codex
login. Model inference still uses the signed-in service and allowance; running tools
on the Mac does not make those models local. No OpenAI API key or paid agent service
is needed. The Mac must be awake while a local run is executing.

## Watch the work

Run `python3 scripts/watch_team.py` and open http://localhost:8081 . The read-only
activity page refreshes every two seconds, showing local ticket stages, changed source
paths and the latest synthetic test output. Links open all private tickets, PR diffs
and GitHub pipelines. Monitoring makes no AI calls. It does not read wearable records,
credentials, ticket bodies or raw agent command output. Keep this server on loopback.

## Roles

| Role | Responsibility |
| --- | --- |
| orchestrator | Turn a request into small tickets, scope work, run checks, coordinate PRs |
| backend_developer | Backend, adapters and MCP, using invented fixtures |
| frontend_developer | Dashboard and user flows, using invented values |
| qa | Independent review of the exact candidate and test evidence |

Project definitions are in `.codex/agents/`; the interactive Codex orchestrator can
use them after reopening the project. Existing sessions may need a fresh chat to
load new definitions. The command-line controller also reads these same role files,
so its bounded workflow does not depend on dynamic subagent discovery.

## Start

Run the CLI launcher from a normal macOS Terminal, in the repository folder. A
Codex child process cannot start its macOS sandbox inside an already sandboxed
Codex tool session: nested `sandbox-exec` is unsupported. If that error appears,
stop and use the normal terminal launcher; do not disable or bypass the sandbox.
App-based development agents can work normally through their existing app tools.

Run these from the repository folder:

```sh
python3 scripts/team.py doctor
python3 scripts/team.py tickets
python3 scripts/team.py create "Show when recovery data is stale" --acceptance "Using invented records, show a clear stale-data message and preserve the existing metrics."
python3 scripts/team.py prepare PC-0007 --role frontend_developer --paths frontend/src tests/e2e
python3 scripts/team.py run PC-0007 --dry-run
python3 scripts/team.py run PC-0007
python3 scripts/team.py status
```

Use the identifier printed by `create`; `PC-0007` is an example. Ticket titles remain
human readable. The ID derives from the native GitHub issue number, appears in the
body, branch and PR title, and remains stable if the title changes. PRs close their
issue when merged. Keep acceptance criteria code-focused, with no personal data.

`prepare` fetches private main and creates an independent committed-source checkout
under ignored `.team/workspaces/`. It copies no `.env`, tokens, runtime files or
credential-helper configuration. A developer can edit only the ticket's declared
paths; the controller checks that scope before staging. The controller runs the
synthetic test stack, requests read-only QA, verifies that source has not changed,
commits, pushes a private branch and opens a PR. Development subprocesses receive a
small environment, disable inherited user configuration/MCP, and require ChatGPT
login, using the project/CLI default model. Global user model overrides are also
disabled with that configuration; select a project model if a different available
model is needed. They do not receive the GitHub token. The controller alone uses the existing
local Git credential helper.

This is an on-demand workflow, not an always-running daemon. Start a bounded run for
an accepted ticket. Keep one ticket active; implementation and independent QA run
sequentially, following [TOKEN_WORKFLOW.md](TOKEN_WORKFLOW.md). No self-hosted runner is
registered on the Mac. GitHub executes submitted hosted jobs independently of it.

## Budgets and supervision

`config/team.json` allows one run, two attempts per ticket, ten minutes per agent,
6,000 prompt characters, 20,000 reported tokens per agent and 120,000 output characters.
The reported-token threshold is checked after a completed turn: a soft observed
circuit breaker, not a pre-generation hard cap. Timeouts stop stalled runs. These
controller settings do not enforce limits on manually launched desktop agents.
No automatic credit purchase or fallback to API-key billing is implemented.

Use [TOKEN_WORKFLOW.md](TOKEN_WORKFLOW.md) for focused briefs, sequential development
and independent QA, bounded output and checks. Failures persist `needs_attention`
under `.team/tickets/`; investigate deliberately without an automatic retry loop.
Reports and actual reported usage stay in ignored `.team/`, never PR artifacts.
PC-0027 adds a compact per-ticket resume checkpoint to TOKEN_WORKFLOW.md; reuse
head-matched evidence and dated research rather than replaying full conversations.
Start a fresh chat with `AGENTS.md`, [HANDOFF.md](HANDOFF.md) and its assigned ticket;
do not reload the entire team history. Privacy, exact-head private CI and merge gates
below remain mandatory.

## GitHub checks and merge boundary

See [TESTING.md](TESTING.md) for the identical local/hosted test command. Hosted jobs
require **both** repository variables below to equal `true`:

- `LOCAL_COACH_BILLING_ZERO_VERIFIED`
- `LOCAL_COACH_HOSTED_CI_ENABLED`

Bootstrap verification on 3 November 2026 confirmed the account Actions budget at
$0 with usage blocked, and both repository gates were enabled.

Missing or false variables skip the job before a runner starts. Before re-enabling, verify an
Actions spending budget of $0 with **Stop usage when budget limit is reached** enabled
in GitHub account billing. Alert-only budgets do not stop spending. No caches,
uploaded artifacts, image registry, larger runners or paid upgrades are configured.

The default `auto_merge: false` leaves the PR open. Explicitly request guarded squash
merge with either command:

```sh
python3 scripts/team.py run PC-0007 --merge
python3 scripts/team.py merge PC-0007
```

`merge` checks an existing PR without invoking an AI agent. Setting `auto_merge: true`
in `config/team.json` is another explicit opt-in for `run`; dry runs never merge.
Polling is deterministic and bounded: 600 seconds, every 10 seconds by default.
Optional `merge_timeout_seconds` (1–1200) and `merge_poll_seconds` (1–60) set those
bounds. API errors, missing evidence, failed/skipped/cancelled jobs and timeouts stop
closed, recording `merge_status: needs_attention` without retrying the write.

The controller requires recorded local-check and independent QA passes for the same
commit/tree/base, a clean checkout at that saved head, the configured private
repository, the matching open PR head/branch/base and an unchanged main commit.
Both hosted CI gates must still be true. Only a successful `pull_request` run of
`.github/workflows/ci.yml` at that head, with its successful `test` job from the same
run attempt, is accepted. Manual dispatches, old green commits, unrelated workflows
and skipped jobs do not qualify. The workflow blob must match the saved main base;
a PR that edits CI needs separate review. The run must also identify this PR and
tested head/base commits; an empty association array cannot establish that proof
and stops the merge. GitHub may remove associations after a PR is merged/deleted.

Local evidence is saved as `checks` and `qa` with `status`, `sha`, `tree` and `base`;
`checks.command` must be `scripts/check_local.py`. Older ticket state lacking these
records cannot merge. Recheck and obtain QA on a fresh candidate after main changes;
do not manually invent evidence. A confirmed merge persists `status: merged`,
`merge_sha` and the CI run/attempt/job IDs. Usage already reported by an agent is
saved even when its final report is blocked or its token circuit breaker fires.

One local merge lock serializes controller writes. The squash API request includes
the expected PR head SHA, and main is rechecked immediately before it. GitHub does
not provide an atomic expected-base argument on this endpoint: an external main
write can still race the final check. Private branch protection depends on the
GitHub plan; the controller is not server-enforced protection. Publishing code
publicly remains a separate reviewed source export described in
[PUBLICATION.md](PUBLICATION.md); private Git history is never mirrored automatically.

## Privacy limits

Source-only workspaces and concise instructions reduce accidental data access; they
are not a separate operating-system security boundary. Local Codex processes inherit
the user's readable filesystem and use the existing signed-in account. Keep real
health data away from development prompts and never enable coaching MCP in a dev
session. The privacy scan catches defined patterns; it cannot establish that arbitrary
code, free text, names or derived habits are anonymous or legally compliant.

## References

- [Codex custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Codex non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode)
- [GitHub spending budgets](https://docs.github.com/en/billing/how-tos/set-up-budgets)
