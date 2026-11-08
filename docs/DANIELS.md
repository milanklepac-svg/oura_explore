# Daniels-inspired local planning

This is an independent implementation of selected publicly described principles, not an official Jack Daniels, V.O2 or licensed 2Q plan. No book tables or commercial schedules are reproduced.

## Primary sources and interpretation

- [V.O2 FAQ](https://support.vdoto2.com/v-o2-faq/): calibrate from demonstrated current fitness, preferably a recent race/time trial. Ordinary training runs and future target times do not establish VDOT. The local app therefore requires an achieved result and a separately supplied current-fitness threshold pace, rather than inventing a performance score from Oura.
- [Training definitions, by Jack Daniels](https://vdoto2.com/learn-more/training-definitions): E is conversational aerobic work; M is marathon-specific work; T develops threshold endurance; I targets aerobic power; R develops economy/speed. The public long-run guideline gives a weekly-volume share and time limit. Our Daniels-inspired option adopts the upper 30% share and a 150-minute ceiling, with a lower user time cap respected.
- [V.O2 Adaptive Trainer guide](https://support.vdoto2.com/vdot-adaptive-trainer-instructional-guide/): distinguishes base work from added quality, emphasises adaptation time, current mileage, and spacing of demanding days. Our four-week volume blocks and one-quality-session limit are an original, conservative interpretation.

## What the app implements

Choose **Daniels-inspired** in race-calendar setup. Existing saved foundation plans retain their original settings. The app uses four-week blocks, with a cutback week, instead of adding volume every week. The entered build percentage is a per-block increment in this mode. Long runs are capped by weekly volume, the entered distance/time limits and the approach's share/time limits. Every newly generated session has an intensity label, purpose and structured segments.

Base mode remains easy running plus a long run. An optional quality day requires at least four available running days and two intervening easy/non-quality days between it and the long run. Before scheduling threshold work, the app needs:

1. An actual race/time-trial date, distance and achieved finish time.
2. A current-fitness threshold pace supplied by the user, for example from the [official calculator](https://vdoto2.com/calculator/), using that result rather than a goal.
3. At least four elapsed plan weeks, a build week, adequate existing session distance and spacing from races.

No result is transmitted to the calculator by this app. No local VDOT equation, VO2max estimator or training-pace inference from normal workouts is implemented. A threshold pace remains user-supplied calibration, not a value independently verified by the app.

The generated threshold session is our own small two-bout workout: E warm-up, controlled T work with an easy recovery, E cool-down. Its total distance replaces an existing easy session; it does not add weekly mileage. T distance is capped at 8% of that week's budget and 30% of the session, with T work at most 20 minutes and at least 20 minutes allocated to warm-up/cool-down. These exact implementation gates, the four-week base requirement, one-session cap, and the 90-day benchmark freshness policy are product choices, not a claim that Daniels prescribes this exact schedule. Stale or absent calibration leaves the slot easy. M/I/R sessions are not generated yet.

When recovery causes a quality session to become easy, its demanding segments are removed. Recent recorded long runs or high-intensity workouts can also reduce a demanding next-day session. Adjustments never make training paces faster or add compensatory workload after a missed run. Oura does not provide the detailed running load, splits, route or HR trace needed for a richer performance model here; Suunto remains pending.

## Recorded running context

`GET /api/running/context` and MCP `get_running_context` report the previous 28 complete provider days, excluding today. Runs use Oura's recorded distance and elapsed duration. Only exact timed duplicates with equal distances are collapsed; separate same-day sessions remain separate and are flagged. Empty weeks stay unknown. Missing/zero/negative distance cannot produce a pace.

The app shows observed volume, longest recorded run, elapsed paces and four weekly blocks. Only after the user confirms the full log does **Use confirmed recorded volume in setup** copy the four-week average into setup. It does not infer a comfortable pace or long run. The creation audit stores the observed summary and whether coverage was confirmed. Subsequent reviews use actual available prior-day workouts for spacing, without automatically marking sessions completed or skipped.

Recorded volume cannot establish goal feasibility. Low mileage, incomplete coverage or the 30% long-run cap may make a marathon target incompatible with this template. The app exposes assumptions and warnings instead of raising load to force a target time.
