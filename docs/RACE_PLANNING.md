# Local race planning

The dashboard's race calendar supports up to 20 events over one year. Add multiple **main races** (10K, half marathon or marathon) and **milestones** (the same distances plus custom races). Each event has a date and target finish time, entered as H:MM or H:MM:SS.

Supply your current weekly distance, comfortable long-run distance, comfortable easy pace, running days, long-run day and weekly/time caps. These are user inputs; Oura is not used to invent running fitness. The generated schedule is an original easy/long-run foundation template with build/cutback weeks, race-specific taper windows and post-race recovery followed by distance-dependent return-to-running caps (up to three weeks after a marathon). Target race paces are displayed as goals. They do not prescribe training zones or automatically speed up workouts. The original foundation option has no faster workouts. The Daniels-inspired option adds a tightly bounded threshold session only with an explicitly entered recent result and a supplied current-fitness threshold pace; see [DANIELS.md](DANIELS.md). No VDOT formula or race-equivalence estimator is implemented.

## What runs automatically

Recent Oura runs are now read as context for prior long/hard-session spacing, and the app shows observed weekly volumes without assuming completeness. Ordinary run pace never calibrates a threshold pace. The local Docker worker refreshes Oura on startup and hourly, then calls the same planning service exposed through MCP. An MCP server does not independently schedule jobs or reason in the background. No LLM, cloud coach, public tunnel or remote deployment is used. Oura collection still requires the vendor's Internet API.

In **automatic mode**, the service can apply bounded changes. In **suggestion mode**, it records proposals; Apply these suggestions recalculates the review and rejects approval if the plan or recovery evidence changed. Switch modes without regenerating the calendar using Live adjustment mode.

Rules:

- Today’s reliable recovery signals can shorten the original run to 80%, or 65% when independent signal groups agree, and make it easy. HRV and resting heart rate count as one group; readiness alone never triggers a cut. A repeat sync cannot repeatedly multiply reductions.
- Physiological changes require current metrics, enough baseline observations, a complete latest sync and a successful sync no older than 36 hours. Missing check-ins remain unknown. Recovery thresholds come from the existing training preferences.
- Mark runs completed or skipped yourself. Missing Oura workouts do not imply missed training. A single skipped long-run focus can move to an existing easy slot within three days, with no added distance and spacing from other long runs/races. Without adequate recovery context, rescheduling is held.
- Two or more marked skips in the preceding seven days reduce eligible sessions in the next seven days to at most 80% of their original distance. No accumulated make-up mileage is inserted.
- Race dates, race targets, completed sessions, scheduled rest, post-race recovery and past sessions are protected. Rescheduling/re-entry changes respect race buffers. No automatic distance increases, restored missed mileage, changed race priority or date changes occur.
- Rest is not the first adaptation for wearable caution signals. An explicit illness/injury check-in can pause an ordinary session. Races remain protected from automatic edits and need the user's decision.

Numeric defaults are conservative product heuristics, not clinically validated thresholds or a guarantee of readiness. Conflicting races and short preparation windows produce warnings; the app does not establish that a target is achievable. Race distance itself can exceed the configured weekly cap: the user supplied the event and must review the warning.

## Storage and concurrency

`race_plans` stores owner-scoped settings, original and current sessions, statuses and a revision. `plan_audit` is append-only to the MCP login. Changes and their audit entry commit together. API writes use same-origin checks and expected revisions; PostgreSQL advisory/row locks serialize worker, API and MCP changes. Revising a started calendar preserves past and completed sessions while regenerating future sessions. Replacing a plan archives the full previous version in the local audit table; the dashboard omits that large payload from regular responses.

Oura records are a rebuildable cache. Your race calendar, completion statuses, training preferences and change history are **user-authored data**, and cannot be recovered by re-fetching Oura. Preserve the PostgreSQL volume or make a private database backup before deleting it. Do not commit database dumps, race details or wearable records to the public repository.

## MCP

- `get_race_plan`: reads the calendar, daily schedule and latest 50 audit events.
- `adapt_training_plan(expected_revision)`: recalculates today using the configured mode; may write plan changes/audit entries. It cannot create races, fabricate skipped sessions, supply subjective symptoms or increase distance.

The MCP role still cannot write health records, sync jobs or training preferences. It has SELECT/INSERT/UPDATE on `race_plans` and SELECT/INSERT on `plan_audit`; transactions default read-only and only the planning service explicitly opens a write transaction. This is a single-owner local application, not multi-tenant isolation.

After updating an existing installation, start/rebuild the containers so the new tables exist, then run `python3 scripts/setup_mcp.py` and rebuild the optional MCP image. No new external token is required.

## Verify

Run backend tests in the API image (the MCP SDK has its own image):

```sh
docker compose run --rm -T --no-deps -e DATABASE_URL=sqlite:// -e PYTHONPATH=/app:/tests \
  -v "$PWD/backend/tests:/tests:ro" api python -m unittest \
  test_oura test_oura_queries test_startup_sync test_training test_training_api test_planning test_running

docker compose --profile mcp run --rm -T --no-deps -e DATABASE_URL=sqlite:// \
  -v "$PWD/backend/tests:/tests:ro" mcp python -m unittest discover -s /tests -p test_mcp_contract.py
```

The planning and running-context tests use synthetic data. They cover race/date validation, preserved goals, caps, taper/recovery, bounded and repeat-safe adjustment, stale/missing context, completed/race/rest protection, missed sessions, manual approval, owner isolation, optimistic versions, audit rollback, API origins and worker failure isolation. The separate privilege check performs synthetic plan writes inside a rolled-back transaction and verifies health writes are denied. The real stdio smoke test calls only read tools to avoid changing a live training plan.
