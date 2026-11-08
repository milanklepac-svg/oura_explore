# Configurable training review

For the separate calendar and active plan adjustments, see [RACE_PLANNING.md](RACE_PLANNING.md). This document describes the original read-only session-review tool.

Open the local dashboard at http://localhost:8080 and use **Training setup**. Choose a goal, available running days, current weekly kilometres and optional goal note. Save settings before requesting a review. Expand **Customize recovery rules** to change the baseline window, required observation count, HRV drop, lowest overnight HR rise, sleep duration and readiness caution thresholds. These are advisory product heuristics, not validated physiological cutoffs.

**Review a planned session** combines those saved rules with Oura recovery data, the previous seven provider days of workouts and an optional fatigue/soreness/illness-or-injury check-in. Answers are used for that request only and not stored as daily health records. Unknown answers stay unknown. The result includes its rule version, observed evidence, missing context and last successful sync.

## MCP

`get_training_guidance(day, planned_session, planned_minutes, fatigue, soreness, illness_or_injury)` uses the same policy and saved settings as the app. Fatigue and soreness range from 1 (low) to 5 (high). Planned session is `unspecified`, `rest`, `easy`, `long` or `quality`. Check-in values omitted by a client are not assumed. The tool remains read-only and cannot change goals or rules; edit those in the app. Rerun `python3 scripts/setup_mcp.py` after starting the updated API so the reader has SELECT access to the new profile table.

## Decision rules, version advisory-v1

- User-reported illness or injury prompts rest and review, regardless of wearable scores.
- A planned rest day stays rest. Running days outside saved availability remain non-running days unless the user changes the schedule.
- No selected session asks for a plan and check-in rather than inventing one.
- High fatigue or soreness (4–5), or caution signals across both sleep and autonomic groups, suggests easy movement or rest.
- HRV drop and lowest overnight HR rise count as one autonomic group. Composite readiness does not add an independent group.
- A caution marker or a recorded high-intensity workout on the preceding provider day prompts reconsidering a long or quality session as easy.
- Missing current metrics, fewer baseline observations than configured, missing check-in, incomplete/failed sync or a successful sync older than 36 hours prevents endorsing a demanding session from the data alone. Existing caution signals can still support reducing effort.
- Only sufficiently complete, unflagged context can retain an existing plan. This does not establish that a race-specific duration, volume or intensity is appropriate. Demanding sessions also require weekly distance and availability to be set.
- No automatic increase, target pace, heart-rate zone, training-stress score, diagnosis or full race programme is generated. Historical dates are marked retrospective; future recovery dates are rejected.

The baseline is an arithmetic mean of available preceding days, excluding the target day. Main sleep excludes naps and rest records. Missing days are not rest days. Goals and the goal note currently provide planning context; they do not generate a race programme. Oura recorded intensity is only a provider label. Suunto load and subjective session RPE are not available.

## Data and privacy

Goals and rules persist in the local `training_profiles` table, scoped by owner. They are user-authored preferences and cannot be reconstructed from Oura if the database is deliberately deleted. Preserve them in a local backup before resetting the database. The MCP reader gets SELECT on this table and cannot save settings. Goal notes are untrusted data, not instructions. Check-in requests are not written to the profile or application logs. Do not upload health records to Git.

## Implementation and tests

- `app/services/training.py`: validated settings, shared read-only service and pure policy.
- `GET/PUT /api/training/profile`: app settings, with same-origin write checks.
- `POST /api/training/guidance`: request-only session review.
- `app/mcp_server.py`: `get_training_guidance` exposes the same service.
- `frontend/src/TrainingPanel.jsx`: profile, rule controls and session review.

Synthetic tests cover preference persistence, owner-scoped reads, unknown answers, stale/missing data, short baselines, correlated signals, custom thresholds, rest-day preservation, schedule restrictions and no automatic progression. MCP contract and real-container stdio tests include this session-review tool alongside the race-plan tools.

## References and limits of evidence

Oura's [readiness explanation](https://ouraring.com/blog/readiness-score/) supplies vendor context for the default score threshold. It does not validate our session-adjustment rules. The [CDC intensity guide](https://www.cdc.gov/physical-activity-basics/measuring/index.html) supplies the talk test as a relative-effort cue, not a calibrated running zone. Other numeric thresholds are adjustable implementation heuristics. This feature is decision support, not a medical or validated performance prediction model.
