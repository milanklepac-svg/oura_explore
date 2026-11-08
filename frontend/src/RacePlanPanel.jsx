import React, { useEffect, useState } from "react";
const weekdays = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const distances = [
  ["10k", "10K"],
  ["half_marathon", "Half marathon"],
  ["marathon", "Marathon"],
];
const localDay = () =>
  new Date().toLocaleDateString("en-CA", { timeZone: "Europe/Belgrade" });
const clock = (minutes) => {
  const s = Math.round(minutes * 60);
  return `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};
const pace = (minutes) => {
  const s = Math.round(minutes * 60);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
const emptyRace = (priority = "main") => ({
  name: "",
  day: "",
  priority,
  distance: priority === "main" ? "marathon" : "10k",
  custom_km: "",
  target_time: "",
});
function parseTime(value) {
  if (!/^\d{1,2}:[0-5]\d(?::[0-5]\d)?$/.test(value))
    throw Error("Use H:MM or H:MM:SS for each target time.");
  const a = value.split(":").map(Number);
  return a[0] * 60 + a[1] + (a[2] || 0) / 60;
}
async function api(path, body) {
  const r = await fetch(
    path,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  const d = await r.json();
  if (!r.ok)
    throw Error(
      typeof d.detail === "string"
        ? d.detail
        : d.detail?.map((x) => x.msg).join("; ") || "Request failed",
    );
  return d;
}
export default function RacePlanPanel() {
  const [snapshot, setSnapshot] = useState({ plan: null, history: [] }),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [review, setReview] = useState(null),
    [all, setAll] = useState(false),
    [running, setRunning] = useState(null),
    [coverage, setCoverage] = useState(false);
  const [form, setForm] = useState({
    start: localDay(),
    philosophy: "daniels_inspired",
    quality_day: null,
    fitness_result: null,
    history_confirmed: false,
    weekly_km: "",
    peak_weekly_km: "",
    current_long_km: "",
    easy_pace_minutes: "",
    max_long_minutes: 150,
    running_days: ["mon", "wed", "fri", "sun"],
    long_day: "sun",
    weekly_growth_percent: 3,
    mode: "automatic",
    races: [emptyRace()],
  });
  const [checkin, setCheckin] = useState({
    fatigue: "",
    soreness: "",
    illness_or_injury: "",
  });
  function setSaved(d) {
    setSnapshot(d);
    if (d.plan)
      setForm({
        ...d.plan.settings,
        fitness_result: d.plan.settings.fitness_result
          ? {
              ...d.plan.settings.fitness_result,
              result_time: clock(d.plan.settings.fitness_result.minutes),
            }
          : null,
        races: d.plan.settings.races.map((r) => ({
          ...r,
          custom_km: r.custom_km ?? "",
          target_time: clock(r.target_minutes),
        })),
      });
  }
  useEffect(() => {
    let live = true;
    api("/api/plan")
      .then((d) => {
        if (live) setSaved(d);
      })
      .catch((e) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    const t = setInterval(
      () =>
        api("/api/plan")
          .then((d) => live && setSnapshot(d))
          .catch(() => {}),
      15000,
    );
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);
  useEffect(() => {
    api("/api/running/context")
      .then(setRunning)
      .catch(() => {});
    const t = setInterval(
      () =>
        api("/api/running/context")
          .then(setRunning)
          .catch(() => {}),
      60000,
    );
    return () => clearInterval(t);
  }, []);
  useEffect(() => setCoverage(false), [running?.end]);
  const update = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const raceUpdate = (i, key, value) =>
    setForm((f) => ({
      ...f,
      races: f.races.map((r, n) => (n === i ? { ...r, [key]: value } : r)),
    }));
  async function save(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const settings = {
        ...form,
        quality_day: form.quality_day || null,
        fitness_result: form.fitness_result
          ? {
              ...form.fitness_result,
              minutes: parseTime(form.fitness_result.result_time),
            }
          : null,
        races: form.races.map((r) => ({
          name: r.name.trim(),
          day: r.day,
          priority: r.priority,
          distance: r.distance,
          custom_km: r.distance === "custom" ? Number(r.custom_km) : null,
          target_minutes: parseTime(r.target_time),
        })),
      };
      if (
        snapshot.plan &&
        !window.confirm(
          "Replace this calendar with a new plan? Past and completed sessions stay intact; future sessions will be regenerated. The previous version will be archived locally.",
        )
      )
        return;
      setSaved(
        await api("/api/plan", {
          settings,
          expected_revision: snapshot.plan?.revision || 0,
        }),
      );
      setReview(null);
      setMessage(
        "Plan saved locally. Recovery reviews run after each successful Oura sync.",
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function status(day, status) {
    setBusy(true);
    setError("");
    try {
      setSnapshot(
        await api("/api/plan/session", {
          day,
          status,
          expected_revision: snapshot.plan.revision,
        }),
      );
      setMessage(
        status === "skipped"
          ? "Missed session recorded; the coach reviewed the next sessions."
          : "Session status saved.",
      );
    } catch (e) {
      setError(e.message);
      setSnapshot(await api("/api/plan"));
    } finally {
      setBusy(false);
    }
  }
  async function runReview(approve = false) {
    setBusy(true);
    setError("");
    try {
      const body = {
        expected_revision: snapshot.plan.revision,
        ...Object.fromEntries(
          Object.entries(checkin).map(([k, v]) => [
            k,
            v === ""
              ? null
              : k === "illness_or_injury"
                ? v === "yes"
                : Number(v),
          ]),
        ),
      };
      if (approve) body.approve_review_id = review.review_id;
      setReview(await api("/api/plan/review", body));
      setSnapshot(await api("/api/plan"));
    } catch (e) {
      setError(e.message);
      setSnapshot(await api("/api/plan"));
    } finally {
      setBusy(false);
    }
  }
  async function mode(value) {
    setBusy(true);
    setError("");
    try {
      const d = await api("/api/plan/mode", {
        mode: value,
        expected_revision: snapshot.plan.revision,
      });
      setSnapshot(d);
      setForm((f) => ({ ...f, mode: value }));
      setReview(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const plan = snapshot.plan,
    today = localDay();
  const visible =
    plan?.sessions.filter(
      (s) =>
        all ||
        (s.day >= today &&
          s.day <=
            new Date(Date.now() + 28 * 86400000).toLocaleDateString("en-CA", {
              timeZone: "Europe/Belgrade",
            })),
    ) || [];
  return (
    <section className="panel training race-planner">
      <p className="eyebrow">YOUR RACE CALENDAR · LOCAL ONLY</p>
      <h2>Main races, milestones, one adaptable plan.</h2>
      <p className="form-note">
        Add multiple main races and milestones with target finish times. The
        coach prioritises easier or shorter runs, then safe rescheduling; it
        never adds catch-up mileage or changes race targets. Scheduled rest and
        post-race recovery remain in the plan.
      </p>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {message && (
        <p role="status" className="form-note">
          {message}
        </p>
      )}
      {running && (
        <div className="running-context">
          <p className="eyebrow">
            RECENT RUNS · {running.start} TO {running.end}
          </p>
          <h3>Build from what you have actually run.</h3>
          <p className="form-note">
            {running.summary.recorded_runs} recorded runs ·{" "}
            {running.summary.recorded_km.toFixed(1)} km total ·{" "}
            {running.summary.longest_recorded_km?.toFixed(1) ?? "—"} km longest.
            These are observed Oura records; coverage is unconfirmed.
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Distance</th>
                  <th>Elapsed time</th>
                  <th>Elapsed pace</th>
                </tr>
              </thead>
              <tbody>
                {running.runs.map((r) => (
                  <tr key={r.record_id}>
                    <td>
                      {r.day}
                      {running.multiple_run_days.includes(r.day)
                        ? " · multiple entries"
                        : ""}
                    </td>
                    <td>{r.km?.toFixed(2) ?? "—"} km</td>
                    <td>{r.minutes?.toFixed(0) ?? "—"} min</td>
                    <td>
                      {r.elapsed_pace_minutes_per_km
                        ? pace(r.elapsed_pace_minutes_per_km) + "/km"
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="run-weeks">
            {running.weeks.map((w) => (
              <p key={w.start}>
                {w.start} – {w.end}
                <br />
                <strong>
                  {w.recorded_km === null
                    ? "No records · unknown"
                    : w.recorded_km.toFixed(1) + " km observed"}
                </strong>
              </p>
            ))}
          </div>
          <p className="form-note">
            Ordinary run pace is not race fitness. Same-day entries are kept
            separate unless their timestamps and distance match exactly. Suunto
            splits, routes and running heart-rate traces are still unavailable.
          </p>
          <label className="form-note">
            <input
              type="checkbox"
              checked={coverage}
              onChange={(e) => setCoverage(e.target.checked)}
            />{" "}
            I checked these entries, including multiple entries on a day; every
            run in this 28-day window is included.
          </label>
          <div className="form-actions">
            <button
              className="secondary"
              disabled={
                !coverage ||
                !running.summary.observed_km_per_week ||
                running.summary.missing_distance > 0
              }
              onClick={() => {
                setForm((f) => ({
                  ...f,
                  weekly_km: running.summary.observed_km_per_week,
                  peak_weekly_km:
                    f.peak_weekly_km || running.summary.observed_km_per_week,
                  history_confirmed: true,
                }));
                setMessage(
                  "Confirmed recorded volume copied into the setup form. Enter your comfortable long run and easy pace, then review the calendar before saving.",
                );
              }}
            >
              Use confirmed recorded volume in setup
            </button>
          </div>
        </div>
      )}
      {!loading && (
        <details open={!plan}>
          <summary>
            {plan
              ? "Edit race calendar / replace plan"
              : "Set up your first plan"}
          </summary>
          <form onSubmit={save}>
            <div className="form-grid">
              <label>
                Planning approach
                <select
                  value={form.philosophy || "foundation"}
                  onChange={(e) => {
                    update("philosophy", e.target.value);
                    if (e.target.value !== "daniels_inspired")
                      update("quality_day", null);
                  }}
                >
                  <option value="daniels_inspired">
                    Daniels-inspired · current fitness and purpose
                  </option>
                  <option value="marathon_endurance">
                    Marathon endurance · mostly easy
                  </option>
                  <option value="foundation">
                    Original foundation template
                  </option>
                </select>
              </label>
              {form.philosophy === "marathon_endurance" && (
                <div>
                  <p>Original independent marathon endurance template. Base: more than 12 weeks; build: 7–12 weeks; marathon-specific: more than 3–7 weeks; taper: last 3 weeks before the upcoming main marathon. Cutbacks, tune-up tapers and recovery take priority. Without an upcoming main marathon, running stays in easy base training.</p>
                  <p>Conversational long runs: at most 35% of weekly volume, 28 km and 150 minutes (or your lower limit). Growth and cutbacks use your existing settings. These are conservative product heuristics, not validated preparation or injury-risk predictions.</p>
                  <label>
                    <input type="checkbox" checked={!!form.marathon_effort} onChange={(e) => update("marathon_effort", e.target.checked)} />
                    Optional modest marathon-effort segments
                  </label>
                  <p>Requires confirmed history, at least 40 current km/week, a comfortable 14 km long run, four running days and four plan weeks. Only non-cutback marathon-specific long runs at least 14 days from races qualify. At most 15 minutes estimated at easy pace, 15% of the run and 5% of weekly volume. M means controlled sustainable marathon effort; no numeric pace or goal-time-derived fitness. Poor recovery removes M segments. Changes take effect only when you save the plan.</p>
                </div>
              )}
              {form.philosophy === "daniels_inspired" && (
                <label>
                  Optional quality day
                  <select
                    value={form.quality_day || ""}
                    onChange={(e) =>
                      update("quality_day", e.target.value || null)
                    }
                  >
                    <option value="">Base phase · easy + long only</option>
                    {form.running_days.map((d) => (
                      <option key={d} value={d}>
                        {d} · one controlled threshold session/week
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {[
                ["start", "Plan start", "date"],
                ["weekly_km", "Current weekly distance · km", "number"],
                ["peak_weekly_km", "Maximum weekly distance · km", "number"],
                [
                  "current_long_km",
                  "Current comfortable long run · km",
                  "number",
                ],
                [
                  "easy_pace_minutes",
                  "Current comfortable pace · minutes/km (e.g. 6.5)",
                  "number",
                ],
                [
                  "max_long_minutes",
                  "Long-run time cap · minutes (30–180)",
                  "number",
                ],
                [
                  "weekly_growth_percent",
                  form.philosophy === "daniels_inspired"
                    ? "Build increment · % per four-week block (0–5)"
                    : "Maximum build trend · %/week (0–5)",
                  "number",
                ],
              ].map(([key, label, type]) => (
                <label key={key}>
                  {label}
                  <input
                    required
                    type={type}
                    step={type === "number" ? "any" : undefined}
                    min={key === "start" ? plan?.settings.start || today : 0}
                    value={form[key]}
                    onChange={(e) => update(key, e.target.value)}
                  />
                </label>
              ))}
              <label>
                Preferred long-run day
                <select
                  value={form.long_day}
                  onChange={(e) => update("long_day", e.target.value)}
                >
                  {weekdays.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Adjustment mode
                <select
                  value={form.mode}
                  onChange={(e) => update("mode", e.target.value)}
                >
                  <option value="automatic">
                    Apply bounded adjustments locally
                  </option>
                  <option value="review">Suggest changes only</option>
                </select>
              </label>
            </div>
            <fieldset>
              <legend>Available running days · choose 3–6</legend>
              <div className="day-choices">
                {weekdays.map((d) => (
                  <label key={d}>
                    <input
                      type="checkbox"
                      checked={form.running_days.includes(d)}
                      onChange={(e) =>
                        update(
                          "running_days",
                          e.target.checked
                            ? [...form.running_days, d]
                            : form.running_days.filter((x) => x !== d),
                        )
                      }
                    />
                    {d}
                  </label>
                ))}
              </div>
            </fieldset>
            <p className="form-note">
              Target time format: H:MM or H:MM:SS. Your current comfortable pace
              controls easy-run time estimates; race targets do not
              automatically make workouts faster.
            </p>
            {form.philosophy === "daniels_inspired" && (
              <fieldset className="race-row">
                <legend>Demonstrated current fitness · optional</legend>
                <label className="form-note">
                  <input
                    type="checkbox"
                    checked={!!form.fitness_result}
                    onChange={(e) =>
                      update(
                        "fitness_result",
                        e.target.checked
                          ? {
                              day: today,
                              distance_km: "",
                              result_time: "",
                              threshold_pace_minutes: "",
                            }
                          : null,
                      )
                    }
                  />{" "}
                  I have an actual recent race/time-trial result and its
                  current-fitness threshold pace.
                </label>
                <p className="form-note">
                  Quality remains easy until this is supplied, and is limited to
                  one controlled session after the base block. Obtain the
                  threshold pace from your current result using the{" "}
                  <a
                    href="https://vdoto2.com/calculator/"
                    target="_blank"
                    rel="noreferrer"
                  >
                    official VDOT calculator
                  </a>
                  . The app does not transmit your result to that site or
                  estimate VDOT from an ordinary run. Calibration expires after
                  90 days (our conservative freshness policy).
                </p>
                {form.fitness_result && (
                  <div className="form-grid">
                    {[
                      ["day", "Actual result date", "date"],
                      [
                        "distance_km",
                        "Actual race/time-trial distance · km",
                        "number",
                      ],
                      ["result_time", "Actual finish time · H:MM:SS", "text"],
                      [
                        "threshold_pace_minutes",
                        "Current-fitness threshold pace · decimal minutes/km",
                        "number",
                      ],
                    ].map(([key, label, type]) => (
                      <label key={key}>
                        {label}
                        <input
                          required
                          type={type}
                          max={type === "date" ? today : undefined}
                          step={type === "number" ? "any" : undefined}
                          value={form.fitness_result[key]}
                          onChange={(e) =>
                            update("fitness_result", {
                              ...form.fitness_result,
                              [key]: e.target.value,
                            })
                          }
                        />
                      </label>
                    ))}
                  </div>
                )}
              </fieldset>
            )}
            <div className="race-list">
              {form.races.map((race, i) => (
                <fieldset className="race-row" key={i}>
                  <legend>
                    {race.priority === "main" ? "Main race" : "Milestone"}{" "}
                    {i + 1}
                  </legend>
                  <div className="form-grid">
                    <label>
                      Race name
                      <input
                        required
                        maxLength="100"
                        value={race.name}
                        onChange={(e) => raceUpdate(i, "name", e.target.value)}
                      />
                    </label>
                    <label>
                      Date
                      <input
                        required
                        type="date"
                        min={form.start}
                        value={race.day}
                        onChange={(e) => raceUpdate(i, "day", e.target.value)}
                      />
                    </label>
                    <label>
                      Distance
                      <select
                        value={race.distance}
                        onChange={(e) =>
                          raceUpdate(i, "distance", e.target.value)
                        }
                      >
                        {distances.map(([v, t]) => (
                          <option key={v} value={v}>
                            {t}
                          </option>
                        ))}
                        {race.priority === "milestone" && (
                          <option value="custom">Custom race</option>
                        )}
                      </select>
                    </label>
                    <label>
                      Target finish time
                      <input
                        required
                        placeholder="3:45:00"
                        value={race.target_time}
                        onChange={(e) =>
                          raceUpdate(i, "target_time", e.target.value)
                        }
                      />
                    </label>
                    {race.distance === "custom" && (
                      <label>
                        Custom distance · km
                        <input
                          required
                          type="number"
                          min="0.1"
                          max="100"
                          step="any"
                          value={race.custom_km}
                          onChange={(e) =>
                            raceUpdate(i, "custom_km", e.target.value)
                          }
                        />
                      </label>
                    )}
                  </div>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy || form.races.length === 1}
                    onClick={() =>
                      update(
                        "races",
                        form.races.filter((_, n) => i !== n),
                      )
                    }
                  >
                    Remove race
                  </button>
                </fieldset>
              ))}
            </div>
            <div className="form-actions">
              <button
                type="button"
                className="secondary"
                onClick={() =>
                  update("races", [...form.races, emptyRace("main")])
                }
              >
                + Main race
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() =>
                  update("races", [...form.races, emptyRace("milestone")])
                }
              >
                + Milestone
              </button>
              <button disabled={busy} className="primary">
                {busy
                  ? "Saving…"
                  : plan
                    ? "Save revised calendar"
                    : "Create local plan"}
              </button>
            </div>
          </form>
        </details>
      )}
      {plan && (
        <>
          <div className="race-chips">
            {[...plan.settings.races]
              .sort((a, b) => a.day.localeCompare(b.day))
              .map((r) => (
                <div key={r.day}>
                  <small>
                    {r.priority === "main" ? "MAIN RACE" : "MILESTONE"} ·{" "}
                    {r.day}
                  </small>
                  <strong>{r.name}</strong>
                  <span>
                    {r.distance === "custom"
                      ? r.custom_km + " km"
                      : distances.find((d) => d[0] === r.distance)?.[1]}{" "}
                    · target {clock(r.target_minutes)}
                  </span>
                </div>
              ))}
          </div>
          <details>
            <summary>Plan assumptions and limits</summary>
            <ul className="form-note">
              {plan.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </details>
          <div className="training-review">
            <label className="form-note">
              Live adjustment mode{" "}
              <select
                aria-label="Live adjustment mode"
                disabled={busy}
                value={plan.settings.mode}
                onChange={(e) => mode(e.target.value)}
              >
                <option value="automatic">
                  Apply bounded adjustments locally
                </option>
                <option value="review">Suggest changes for my approval</option>
              </select>
            </label>
            <p className="eyebrow">
              LOCAL COACH ·{" "}
              {plan.settings.mode === "automatic"
                ? "AUTOMATIC ADJUSTMENTS"
                : "SUGGESTIONS ONLY"}{" "}
              · REVISION {plan.revision}
            </p>
            <p className="form-note">
              Reviews run after each successful sync and when you mark a skipped
              run. Recovery flags shorten/ease today's session. Repeated missed
              runs reduce the next week. A missed long-run focus can move into
              an existing easy slot without extra distance. Races, completed
              sessions and scheduled rest are protected. Check-ins remain
              optional and unknown when left blank.
            </p>
            <div className="form-grid">
              {["fatigue", "soreness"].map((k) => (
                <label key={k}>
                  {k} · 1 low to 5 high
                  <select
                    value={checkin[k]}
                    onChange={(e) =>
                      setCheckin((c) => ({ ...c, [k]: e.target.value }))
                    }
                  >
                    <option value="">Unknown</option>
                    {[1, 2, 3, 4, 5].map((n) => (
                      <option key={n}>{n}</option>
                    ))}
                  </select>
                </label>
              ))}
              <label>
                Illness or injury today?
                <select
                  value={checkin.illness_or_injury}
                  onChange={(e) =>
                    setCheckin((c) => ({
                      ...c,
                      illness_or_injury: e.target.value,
                    }))
                  }
                >
                  <option value="">Unknown</option>
                  <option value="no">No</option>
                  <option value="yes">Yes</option>
                </select>
              </label>
            </div>
            <div className="form-actions">
              <button
                className="primary"
                disabled={busy}
                onClick={() => runReview()}
              >
                Review plan now
              </button>
              <button className="secondary" onClick={() => setAll(!all)}>
                {all
                  ? "Show next 4 weeks"
                  : "Show full calendar / past sessions"}
              </button>
            </div>
            {review && (
              <div className="guidance">
                <strong>Review: {review.status.replaceAll("_", " ")}</strong>
                {review.changes?.map((c, i) => (
                  <p key={i}>
                    {c.day} · {c.before.kind} {c.before.km} km → {c.after.kind}{" "}
                    {c.after.km} km
                    <br />
                    {c.reason}
                  </p>
                ))}
                {!!review.data_gaps?.length && (
                  <>
                    <p>Recovery changes need more context:</p>
                    <ul>
                      {review.data_gaps.map((g) => (
                        <li key={g}>{g}</li>
                      ))}
                    </ul>
                  </>
                )}
                <p className="form-note">{review.note}</p>
                {review.status === "proposed" && (
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() => runReview(true)}
                  >
                    Apply these suggestions
                  </button>
                )}
              </div>
            )}
            <div className="table-wrap plan-table">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Session</th>
                    <th>Distance / time</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((s) => (
                    <tr key={s.day}>
                      <td>{s.day}</td>
                      <td>
                        <strong>{s.current.label}</strong>
                        <small>
                          {s.current.intensity
                            ? `${s.current.intensity} · `
                            : ""}
                          {s.current.purpose}
                        </small>
                        {s.current.segments?.length > 0 && (
                          <small>
                            {s.current.segments
                              .map((b, i) =>
                                b.intensity === "T"
                                  ? `${b.repetitions} × ${b.km_per_rep} km T @ ${pace(b.pace_minutes_per_km)}/km, ${b.recovery_minutes} min easy between`
                                  : b.intensity === "M"
                                    ? `${b.km} km M · ${b.effort}`
                                    : `${b.km} km E`,
                              )
                              .join(" · ")}
                          </small>
                        )}
                        <small>
                          {s.current.phase}{" "}
                          {JSON.stringify(s.original) !==
                          JSON.stringify(s.current)
                            ? `· originally ${s.original.kind} ${s.original.km} km`
                            : ""}
                        </small>
                      </td>
                      <td>
                        {s.current.km ? `${s.current.km} km` : "—"}
                        <small>
                          {s.current.kind === "race"
                            ? `Target ${clock(s.current.target_minutes)} · ${pace(s.current.target_pace_minutes)}/km goal`
                            : s.current.minutes
                              ? `≈ ${s.current.minutes} min`
                              : ""}
                        </small>
                      </td>
                      <td>
                        <span>{s.status}</span>
                        {s.day <= today && s.current.kind !== "rest" && (
                          <select
                            aria-label={`Status ${s.day}`}
                            disabled={busy}
                            value={s.status}
                            onChange={(e) => status(s.day, e.target.value)}
                          >
                            <option value="planned">Planned / unknown</option>
                            <option value="completed">Completed</option>
                            <option value="skipped">Skipped</option>
                          </select>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <details className="plan-history">
            <summary>Local change history · latest 50 events</summary>
            {snapshot.history.map((a) => (
              <div className="history-row" key={a.id}>
                <strong>
                  {a.event.replaceAll("_", " ")} · revision {a.revision}
                </strong>
                <small>{new Date(a.created_at).toLocaleString()}</small>
                {a.event === "review" ? (
                  <>
                    <p>
                      {a.detail.applied
                        ? "Changes applied"
                        : a.detail.mode === "automatic"
                          ? "Automatic review"
                          : "Suggestion review"}{" "}
                      · {a.detail.changes?.length || 0} changes
                    </p>
                    {a.detail.changes?.map((c, i) => (
                      <p key={i}>
                        {c.day}: {c.before.kind} {c.before.km} km →{" "}
                        {c.after.kind} {c.after.km} km. {c.reason}
                      </p>
                    ))}
                    {a.detail.blocked?.length > 0 && (
                      <p>Recovery changes held: {a.detail.blocked.join(" ")}</p>
                    )}
                  </>
                ) : a.event === "session_status" ? (
                  <p>
                    {a.detail.day}: {a.detail.before} → {a.detail.after}
                  </p>
                ) : (
                  <p>
                    {a.event === "mode_changed"
                      ? `Adjustment mode: ${a.detail.mode}`
                      : "Calendar saved locally; prior version archived when replaced."}
                  </p>
                )}
              </div>
            ))}
          </details>
        </>
      )}
    </section>
  );
}
