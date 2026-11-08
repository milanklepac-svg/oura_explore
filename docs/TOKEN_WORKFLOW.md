# Token-saving workflow

Keep one ticket active. Use one focused developer, then one independent QA reviewer,
sequentially. Do not fork full conversation history, launch redundant agents or have
the orchestrator repeat repository research already summarized by the developer.
Avoid speculative feature work; stop when the ticket acceptance criteria are met.

Begin each fresh chat by reading `AGENTS.md`, `docs/HANDOFF.md` and the assigned
ticket. Read relevant source slices as needed, not the entire `TEAM.md` history.
Give the developer a fresh brief of at most 200 words containing the ticket ID, goal,
acceptance criteria, allowed paths and test command. Preserve useful findings in
that brief instead of rediscovering the repository after a failure.

Use deterministic shell tools for tests, polling and waiting, without an AI loop.
Run targeted checks first. Freeze the candidate, then run the full required suite
once; rerun only when source changes or failures justify it. Preserve required
checks and independent QA. A failed check permits one focused repair using the
existing evidence, then relevant verification and QA; unresolved failures require
attention, not an automatic retry loop. The configured two-attempt limit is a
ceiling, not an instruction to retry.

Show only failures and relevant output slices, capped around 2,000 tokens per report
or tool result. Save larger safe output locally when necessary. Never expose secrets
through tool output or stderr; sanitize failures before reporting. Use invented test
fixtures and never read runtime data, credentials, real health records or coaching
MCP. Workspace and role instructions are not operating-system isolation.

QA reviews the exact candidate diff and test summary independently and returns at
most 150 words: acceptance result, actionable defects and missing evidence. A changed
candidate needs matching test and QA evidence. Keep private delivery, privacy gates,
exact-head hosted CI checks and guarded merge requirements unchanged.

Use actual reported token counts, never estimates presented as actual usage or
conversions into monthly subscription allowance. Missing usage stays unknown. The
20,000 reported-token threshold is checked after completed turns; it is a soft
observed threshold, not a pre-generation hard cap. Controller configuration does
not enforce limits for manually launched desktop agents. Do not change models,
reasoning settings or project configuration based on unsupported assumptions.

## Resume from a small checkpoint (PC-0027)

Before stopping, write one checkpoint of at most 150 words under ignored
`.team/reports/PC-xxxx-handoff.md`. Keep it task-specific and free of runtime values:

```text
Ticket / role:
Workspace / branch:
Base commit / candidate tree or committed head:
Changed source paths:
Checks: command, result, exact tree/head, safe log path:
QA: verdict and exact tree/head, or pending:
PR / CI: URL and exact checked head, or not created:
Blocker / unanswered decision:
Next action:
Reported usage: actual count and source, or unknown:
```

On resume, read the assigned ticket and this checkpoint first, then confirm the
working-tree status and recorded head/tree. Reuse matching evidence; stale evidence
must not authorize a merge. Read only changed or relevant source slices. Do not
replay the full chat, restart completed agents or repeat successful tests without
a changed candidate, failure or unresolved concern.

Keep research in one dated, source-linked project document. Reuse its stable
architectural findings. Recheck volatile prices, quotas, policies and API contracts
before relying on them for a new decision or deployment; a saved report is not an
automatic freshness guarantee. Add only the changed conclusion and supporting link.

Batch independent read-only checks into one bounded tool call and inspect each
result. Keep mutations and dependent operations sequential. Let deterministic
scripts wait for builds/CI, with short human updates at milestones; do not ask an
agent to repeatedly interpret unchanged logs. Send a completed developer's concise
handoff plus the exact diff to QA, rather than having QA reconstruct the work.

Stop and checkpoint at a usage limit. Resume only after the user does so; do not
switch accounts, models, billing methods or tools to evade it. These practices
reduce repeated context, but no percentage saving or monthly allowance conversion
is claimed without measured evidence. Existing privacy and verification gates stay
mandatory.
