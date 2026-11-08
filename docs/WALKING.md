# Recorded walking context

`GET /api/walking/context` and the read-only MCP `get_walking_context` tool interpret
Oura walking records automatically. Optional `day` is an ISO provider date; the default
is today in the configured timezone. Future dates are rejected. The response covers
seven provider days including that day and reports archive freshness, classifications,
reasons and explicit gaps. The service makes no provider calls or writes.

This is a transparent conservative product heuristic, not a medical prescription,
validated recovery model or evidence that a particular walk improved recovery.
Its 45-minute single-walk and 60-minute daily envelopes are product choices, not
published clinical thresholds. No manual recovery labels are required or trusted.

| Classification | Evidence and interpretation |
| --- | --- |
| `recovery_compatible` | Recorded low/easy intensity, at most 45 minutes per walk and 60 credible walking minutes that provider day; known hard activity ending before the walk on that day or the previous provider day, or recovery cautions on the walk's own provider day; sufficient fresh physiology/baseline context. Compatible with easy activity, benefit unproven. |
| `additional_load` | A credible completed walk has high/hard recorded intensity, lasts over 45 minutes, or contributes to over 60 credible walking minutes that day. Additional observed activity; no fatigue diagnosis. Does not require physiology to establish recorded duration/effort. |
| `ordinary_activity` | Short low/easy walking with sufficient context but no observed preceding recovery need. Ordinary activity does not imply a recovery benefit. |
| `uncertain` | Missing/invalid/naive timestamps or nonpositive duration, missing/unsupported intensity, conflicting duplicates, overlapping records, unconfirmed activity-HR evidence for moderate intensity, or insufficient physiology for an easy-walking interpretation. |

Classification is evaluated in two passes so prior high walking is resolved regardless
of external record-ID ordering. Exact matching start/end instants and provider day
count once, even across timezone-offset representations. Conflicting duplicate
intensities and other overlapping walking intervals remain uncertain and their
minutes cannot create additional-load evidence. This does not deduplicate across
providers. Only this owner's Oura records are queried, and labels, routes, coordinates
and other payload fields are not exposed. Known future or ongoing intervals are
excluded; malformed records remain explicitly uncertain rather than establishing
completion. Provider dates must match the local start date.

Physiology uses the saved coaching baseline window and minimum observed days, HRV,
lowest overnight heart rate, sleep duration, readiness and caution thresholds.
Successful archive sync must be within 36 hours and the latest sync complete.
The main sleep's observed `bedtime_end` must precede the walk. Missing timing stays
uncertain. Baselines exclude that provider day; following-day physiology never
establishes a previous walk's benefit. Overnight measurements are not continuous
activity measurements. No workouts is unknown coverage, never evidence of rest.

Credible additional walking load on the previous provider day adds a `recent_training`
caution to training guidance and the existing demanding-session plan review. Missing
or stale physiology still blocks automatic plan changes. Walking never removes a
caution, offsets fatigue, increases mileage, changes race dates or targets, marks a run
completed, or changes completed/race/rest sessions. Review-mode approval and revision
checks remain in force. Other schedule adjustments retain their existing rules.

Oura explains that activity intensity can default to moderate when activity heart rate
is absent. The imported workout projection does not establish that HR evidence, so
moderate is uncertain rather than assumed measured effort:
[Oura Activity Heart Rate](https://support.ouraring.com/hc/en-us/articles/6321008135699-Activity-Heart-Rate).
CDC explains relative effort and the talk test; it does not validate these envelopes:
[CDC measuring activity intensity](https://www.cdc.gov/physical-activity-basics/measuring/index.html).
