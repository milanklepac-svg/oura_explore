"""Local race calendar and conservative, auditable adaptations; no model/API calls."""

from copy import deepcopy
from math import floor
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from ..db import RacePlan, PlanAudit, now
from .training import TrainingService, GuidanceRequest
from .running import RunningService

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DISTANCES = {"10k": 10.0, "half_marathon": 21.0975, "marathon": 42.195}


class Race(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    day: date
    priority: Literal["main", "milestone"] = "main"
    distance: Literal["10k", "half_marathon", "marathon", "custom"] = "marathon"
    custom_km: float | None = Field(default=None, gt=0, le=100)
    target_minutes: float = Field(gt=0, le=1440)

    @model_validator(mode="after")
    def valid_race(self):
        if self.distance == "custom" and self.custom_km is None:
            raise ValueError("Enter the custom race distance")
        if self.priority == "main" and self.distance == "custom":
            raise ValueError("Main races support 10k, half marathon or marathon")
        return self

    @property
    def km(self):
        return DISTANCES.get(self.distance, self.custom_km)


class FitnessResult(BaseModel):
    day: date
    distance_km: float = Field(ge=1, le=50)
    minutes: float = Field(gt=0, le=600)
    threshold_pace_minutes: float = Field(ge=2, le=12)


class PlanSettings(BaseModel):
    start: date
    history_confirmed: bool = False
    philosophy: Literal["foundation", "daniels_inspired", "marathon_endurance"] = "foundation"
    marathon_effort: bool = False
    quality_day: Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"] | None = None
    fitness_result: FitnessResult | None = None
    weekly_km: float = Field(ge=5, le=160)
    peak_weekly_km: float = Field(ge=5, le=160)
    current_long_km: float = Field(ge=1, le=40)
    easy_pace_minutes: float = Field(
        ge=3, le=15, description="Current comfortable pace, not target race pace"
    )
    max_long_minutes: int = Field(default=150, ge=30, le=180)
    running_days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] = (
        Field(min_length=3, max_length=6)
    )
    long_day: Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"] = "sun"
    weekly_growth_percent: float = Field(default=3, ge=0, le=5)
    mode: Literal["automatic", "review"] = "automatic"
    races: list[Race] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def coherent(self):
        if (
            len(set(self.running_days)) != len(self.running_days)
            or self.long_day not in self.running_days
        ):
            raise ValueError("Choose unique running days including the long-run day")
        if self.quality_day is not None:
            if (
                self.philosophy != "daniels_inspired"
                or self.quality_day not in self.running_days
            ):
                raise ValueError(
                    "The optional quality day must be a running day in Daniels-inspired mode"
                )
            spacing = abs(DAYS.index(self.quality_day) - DAYS.index(self.long_day))
            if min(spacing, 7 - spacing) < 3:
                raise ValueError(
                    "Keep at least two easy/non-quality days between the quality day and long run"
                )
            if len(self.running_days) < 4:
                raise ValueError(
                    "This implementation requires at least four running days before adding a quality day"
                )
        if (
            self.fitness_result
            and self.fitness_result.threshold_pace_minutes >= self.easy_pace_minutes
        ):
            raise ValueError(
                "A current-fitness threshold pace should be faster than your comfortable easy pace"
            )
        if self.peak_weekly_km < self.weekly_km:
            raise ValueError(
                "Peak distance cannot be below the current weekly distance"
            )
        if len({r.day for r in self.races}) != len(self.races):
            raise ValueError("Use one race per date")
        if not any(r.priority == "main" for r in self.races):
            raise ValueError("Add at least one main race")
        if (
            min(r.day for r in self.races) < self.start
            or (max(r.day for r in self.races) - self.start).days > 365
        ):
            raise ValueError("Race dates must be within one year of the plan start")
        if max(r.day for r in self.races) != max(
            r.day for r in self.races if r.priority == "main"
        ):
            raise ValueError("The calendar must finish with a main race")
        if (max(r.day for r in self.races) - self.start).days < 28:
            raise ValueError("Allow at least four weeks to the final main race")
        return self


class SavePlan(BaseModel):
    settings: PlanSettings
    expected_revision: int = Field(default=0, ge=0)


class SessionUpdate(BaseModel):
    day: date
    status: Literal["planned", "completed", "skipped"]
    expected_revision: int = Field(ge=1)


class ReviewPlan(BaseModel):
    expected_revision: int = Field(ge=1)
    fatigue: int | None = Field(default=None, ge=1, le=5)
    soreness: int | None = Field(default=None, ge=1, le=5)
    illness_or_injury: bool | None = None
    approve_review_id: str | None = Field(default=None, max_length=36)


class PlanMode(BaseModel):
    mode: Literal["automatic", "review"]
    expected_revision: int = Field(ge=1)


class PlanConflict(ValueError):
    pass


def race_buffer(race):
    # Deliberately conservative product defaults, not a validated training model.
    return 7 if race.km >= 40 else 4 if race.km >= 20 else 2


def generate(settings):
    races = sorted(settings.races, key=lambda r: r.day)
    sessions, warnings = [], [
        "This is an independent training template. Target times are goals, not evidence of fitness; they do not set faster training paces.",
        "Weekly growth, cutbacks, taper and recovery windows are configurable product heuristics, not a validated coaching model.",
        "Recorded running does not establish completed plan sessions or running fitness. Mark sessions yourself.",
    ]
    if any(r.km >= 40 and (r.day - settings.start).days < 84 for r in races):
        warnings.append(
            "A marathon is less than 12 weeks from the start; this template cannot establish adequate preparation."
        )
    for r in races:
        if r.km > settings.peak_weekly_km:
            warnings.append(
                f"{r.name}: race distance exceeds your weekly distance cap; review preparation and the goal."
            )
    for a, b in zip(races, races[1:]):
        if (b.day - a.day).days < race_buffer(a) + 14:
            warnings.append(
                f"{a.name} and {b.name} have overlapping recovery/build windows; both target times may be incompatible."
            )
    if settings.current_long_km > settings.weekly_km * 0.3:
        warnings.append(
            "Your reported current long run is a large share of weekly volume. Future long runs are capped by the selected approach; recorded history is not changed."
        )
    if settings.philosophy == "daniels_inspired":
        warnings.append(
            "Daniels-inspired mode holds build volume for four-week blocks, caps long runs at 30%/150 minutes, and uses current fitness rather than goal times. It is an independent implementation, not an official Daniels plan."
        )
        if settings.quality_day and not settings.fitness_result:
            warnings.append(
                "Quality sessions remain easy until you enter an actual recent race/time trial and a current-fitness threshold pace."
            )
    if settings.philosophy == "marathon_endurance":
        warnings.append(
            "Marathon endurance is an independent mostly-easy template: base >12 weeks, build 7–12 weeks, marathon-specific >3–7 weeks, then taper, relative to the next main marathon. Cutbacks and race recovery override phases. Long runs stay conversational, capped at 35% of weekly budget, 28 km and 150 minutes (or your lower time limit)."
        )
        warnings.append(
            "Optional marathon effort requires confirmed history, at least 40 current km/week, a comfortable 14 km long run, four running days and four plan weeks. Only marathon-specific non-cutback long runs qualify, at least 14 days from other races; work is capped at 15 minutes, 15% of that run and 5% of weekly volume. These independent product heuristics do not validate fitness. No goal-derived pace is prescribed."
        )
        if not any(r.priority == "main" and r.distance == "marathon" for r in races):
            warnings.append("No main marathon is scheduled; marathon endurance stays in easy base training outside other race tapers/recovery.")
    anchor = settings.start - timedelta(days=settings.start.weekday())
    end = races[-1].day + timedelta(days=race_buffer(races[-1]))
    day = settings.start
    while day <= end:
        week = (day - anchor).days // 7
        exponent = (
            week // 4 if settings.philosophy == "daniels_inspired" else week - week // 4
        )
        # A full week uses the same budget; partial weeks never redistribute missing days.
        next_main = next(
            (r for r in races if r.priority == "main" and r.day >= day), None
        )
        budget = min(
            settings.peak_weekly_km,
            settings.weekly_km * (1 + settings.weekly_growth_percent / 100) ** exponent,
        )
        phase = "build"
        if settings.philosophy == "marathon_endurance":
            next_marathon = next((r for r in races if r.priority == "main" and r.distance == "marathon" and r.day >= day), None)
            days_to_main = (next_marathon.day - day).days if next_marathon else None
            phase = (
                "base" if not next_marathon or days_to_main > 84
                else "build" if days_to_main > 49
                else "marathon_specific" if days_to_main > 21
                else "taper"
            )
        if week % 4 == 3:
            budget *= 0.8
            phase = "cutback"
        for race in races:
            days_to = (race.day - day).days
            taper = (
                21
                if race.km >= 40 and race.priority == "main"
                else 10 if race.km >= 20 else 5
            )
            if 0 < days_to <= taper:
                factor = 0.45 if days_to <= 7 else 0.65 if days_to <= 14 else 0.8
                budget = min(
                    budget,
                    settings.weekly_km
                    * (1 + settings.weekly_growth_percent / 100) ** exponent
                    * factor,
                    settings.peak_weekly_km * factor,
                )
                phase = "taper"
        previous_race = next((r for r in reversed(races) if r.day < day), None)
        if previous_race:
            returning = (day - previous_race.day).days - race_buffer(previous_race)
            window = (
                21 if previous_race.km >= 40 else 10 if previous_race.km >= 20 else 4
            )
            if 0 < returning <= window:
                factor = (
                    (0.5 if returning <= 7 else 0.75 if returning <= 14 else 0.9)
                    if previous_race.km >= 40
                    else (
                        (0.65 if returning <= 5 else 0.85)
                        if previous_race.km >= 20
                        else 0.85
                    )
                )
                budget = min(budget, settings.weekly_km * factor)
                phase = "return_to_running"
        race = next((r for r in races if r.day == day), None)
        post = next(
            (r for r in reversed(races) if 0 < (day - r.day).days <= race_buffer(r)),
            None,
        )
        available = DAYS[day.weekday()] in settings.running_days
        kind, km, label = "rest", 0, "Scheduled rest"
        if available:
            cap = (
                32
                if next_main and next_main.km >= 40
                else 20 if next_main and next_main.km >= 20 else 12
            )
            long_km = min(
                settings.current_long_km
                * (1 + settings.weekly_growth_percent / 100) ** exponent,
                budget * (0.3 if settings.philosophy == "daniels_inspired" else 0.35 if settings.philosophy == "marathon_endurance" else 0.4),
                min(cap, 28) if settings.philosophy == "marathon_endurance" else cap,
                min(
                    settings.max_long_minutes,
                    150 if settings.philosophy in ("daniels_inspired", "marathon_endurance") else 180,
                )
                / settings.easy_pace_minutes,
            )
            other = (budget - long_km) / (len(settings.running_days) - 1)
            kind = "long" if DAYS[day.weekday()] == settings.long_day else "easy"
            km = long_km if kind == "long" else other
            label = (
                "Comfortable long run" if kind == "long" else "Conversational easy run"
            )
        if post:
            kind, km, label, phase = (
                "rest",
                0,
                f"Recovery after {post.name}",
                "race_recovery",
            )
        if race:
            kind, km, label, phase = "race", race.km, race.name, "race"
        # Taper/race recovery never borrows mileage from another day.
        session = {
            "day": str(day),
            "kind": kind,
            "km": round(km, 1),
            "minutes": (
                round(km * settings.easy_pace_minutes)
                if kind not in ("race", "rest")
                else None
            ),
            "label": label,
            "phase": phase,
            "focus": next_main.name if next_main else None,
        }
        session["intensity"] = "E" if kind in ("easy", "long") else None
        session["purpose"] = (
            "Aerobic endurance at conversational effort."
            if kind in ("easy", "long")
            else (
                "Recovery and adaptation."
                if kind == "rest"
                else "Race performance; target is user-entered."
            )
        )
        session["segments"] = []
        if (
            settings.philosophy == "marathon_endurance"
            and settings.marathon_effort
            and settings.history_confirmed
            and settings.weekly_km >= 40
            and settings.current_long_km >= 14
            and len(settings.running_days) >= 4
            and week >= 4
            and kind == "long"
            and phase == "marathon_specific"
            and not any(abs((r.day - day).days) < 14 for r in races)
        ):
            # Easy-pace planning estimates bound the work; never infer a marathon pace.
            effort_km = floor(min(session["km"] * 0.15, budget * 0.05,
                                  15 / settings.easy_pace_minutes) * 10) / 10
            if effort_km >= 1:
                easy_km = round((session["km"] - effort_km) / 2, 2)
                session.update(
                    intensity="M",
                    label="Long run with controlled marathon effort",
                    purpose="Mostly conversational endurance with a modest steady segment; controlled sustainable marathon effort, never goal pace. Return to easy if strained. Duration is an easy-pace estimate.",
                    segments=[
                        {"intensity": "E", "km": easy_km},
                        {"intensity": "M", "km": effort_km, "effort": "Controlled sustainable marathon effort; no numeric pace target"},
                        {"intensity": "E", "km": easy_km},
                    ],
                )
        benchmark = settings.fitness_result
        if (
            settings.philosophy == "daniels_inspired"
            and settings.quality_day == DAYS[day.weekday()]
            and kind == "easy"
            and phase == "build"
            and week >= 4
            and benchmark
            and 0 <= (day - benchmark.day).days <= 90
            and not any(abs((r.day - day).days) < 7 for r in races)
        ):
            # Original small workout, not a reproduced published template. Total distance stays fixed.
            q = (
                floor(
                    min(budget * 0.08, km * 0.3, 20 / benchmark.threshold_pace_minutes)
                    * 10
                )
                / 10
            )
            recovery_km = 2 / settings.easy_pace_minutes
            easy_remaining = session["km"] - q - recovery_km
            if q >= 1 and easy_remaining * settings.easy_pace_minutes >= 20:
                session.update(
                    kind="quality",
                    intensity="T",
                    label="Controlled threshold repeats · current fitness",
                    purpose="Threshold endurance; controlled effort, not a race or goal-pace test.",
                    minutes=round(
                        (session["km"] - q) * settings.easy_pace_minutes
                        + q * benchmark.threshold_pace_minutes
                    ),
                    segments=[
                        {"intensity": "E", "km": round(easy_remaining / 2, 2)},
                        {
                            "intensity": "T",
                            "repetitions": 2,
                            "km_per_rep": round(q / 2, 2),
                            "pace_minutes_per_km": benchmark.threshold_pace_minutes,
                            "recovery_minutes": 2,
                        },
                        {"intensity": "E", "km": round(easy_remaining / 2, 2)},
                    ],
                )
        if race:
            session.update(
                priority=race.priority,
                target_minutes=race.target_minutes,
                target_pace_minutes=round(race.target_minutes / race.km, 3),
            )
        sessions.append(
            {
                "day": str(day),
                "original": deepcopy(session),
                "current": deepcopy(session),
                "status": "planned",
                "adjustment_keys": [],
            }
        )
        day += timedelta(days=1)
    return sessions, warnings


class PlanningService:
    def __init__(self, engine, owner, timezone_name="Europe/Belgrade"):
        self.engine, self.owner, self.zone = engine, owner, ZoneInfo(timezone_name)

    def today(self):
        return datetime.now(self.zone).date()

    def _write(self, db):
        # The MCP login defaults to read-only and can write only the two plan tables.
        if self.engine.dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ WRITE"))
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": "race-plan:" + self.owner},
            )

    def _row(self, db, revision=None):
        row = db.scalar(
            select(RacePlan).where(RacePlan.owner == self.owner).with_for_update()
        )
        if row is None:
            raise ValueError("Create a race plan in the app first")
        if revision is not None and row.revision != revision:
            raise PlanConflict("The plan changed. Reload before trying again.")
        return row

    def _audit(self, db, row, event, detail):
        db.add(
            PlanAudit(
                id=str(uuid4()),
                owner=self.owner,
                revision=row.revision,
                event=event,
                detail=detail,
            )
        )

    def get(self):
        with Session(self.engine) as db:
            row = db.get(RacePlan, self.owner)
            audits = db.scalars(
                select(PlanAudit)
                .where(PlanAudit.owner == self.owner)
                .order_by(PlanAudit.created_at.desc())
                .limit(50)
            ).all()
            if row is None:
                return {"plan": None, "history": []}
            settings = PlanSettings.model_validate(row.settings)
            _, warnings = generate(settings)
            return {
                "plan": {
                    "settings": row.settings,
                    "revision": row.revision,
                    "updated_at": row.updated_at.isoformat(),
                    "sessions": row.sessions,
                    "warnings": warnings,
                },
                "history": [
                    {
                        "id": a.id,
                        "revision": a.revision,
                        "event": a.event,
                        "detail": {
                            k: v for k, v in a.detail.items() if k != "previous_plan"
                        },
                        "created_at": a.created_at.isoformat(),
                    }
                    for a in audits
                ],
            }

    def save(self, request):
        sessions, warnings = generate(request.settings)
        with Session(self.engine) as db, db.begin():
            self._write(db)
            row = db.scalar(
                select(RacePlan).where(RacePlan.owner == self.owner).with_for_update()
            )
            if request.settings.start < self.today() and (
                row is None
                or request.settings.start.isoformat() != row.settings["start"]
            ):
                raise ValueError(
                    "A new plan must start today or later. An existing plan can retain its original start date."
                )
            if row is not None:
                preserved = {
                    s["day"]: s
                    for s in row.sessions
                    if s["day"] < str(self.today()) or s["status"] == "completed"
                }
                sessions = [preserved.pop(s["day"], s) for s in sessions]
                sessions = sorted(
                    [*preserved.values(), *sessions], key=lambda s: s["day"]
                )
            revision = row.revision if row else 0
            if revision != request.expected_revision:
                raise PlanConflict("The plan changed. Reload before saving.")
            previous = (
                None
                if row is None
                else {
                    "settings": row.settings,
                    "sessions": row.sessions,
                    "revision": row.revision,
                }
            )
            if row is None:
                row = RacePlan(owner=self.owner, settings={}, sessions=[], revision=0)
                db.add(row)
            if (
                request.settings.fitness_result
                and request.settings.fitness_result.day > self.today()
            ):
                raise ValueError(
                    "Enter an achieved race/time-trial result, not a future goal"
                )
            row.settings = request.settings.model_dump(mode="json")
            row.sessions = sessions
            row.revision = revision + 1
            row.updated_at = now()
            self._audit(
                db,
                row,
                "plan_created" if previous is None else "plan_replaced",
                {
                    "previous_plan": previous,
                    "warnings": warnings,
                    "running_context": RunningService(
                        self.engine, self.owner, str(self.zone)
                    ).context()["summary"],
                    "history_confirmed": request.settings.history_confirmed,
                },
            )
        return self.get()

    def set_mode(self, request):
        with Session(self.engine) as db, db.begin():
            self._write(db)
            row = self._row(db, request.expected_revision)
            if row.settings["mode"] != request.mode:
                row.settings = {**row.settings, "mode": request.mode}
                row.revision += 1
                row.updated_at = now()
                self._audit(db, row, "mode_changed", {"mode": request.mode})
        return self.get()

    def set_status(self, request):
        if request.day > self.today():
            raise ValueError("Only mark today or past sessions completed/skipped")
        with Session(self.engine) as db, db.begin():
            self._write(db)
            row = self._row(db, request.expected_revision)
            sessions = deepcopy(row.sessions)
            session = next((s for s in sessions if s["day"] == str(request.day)), None)
            if session is None:
                raise ValueError("No session on that date")
            if session["current"]["kind"] == "rest":
                raise ValueError("Rest days do not need completion tracking")
            before = session["status"]
            if before != request.status:
                session["status"] = request.status
                row.sessions = sessions
                row.revision += 1
                row.updated_at = now()
                self._audit(
                    db,
                    row,
                    "session_status",
                    {
                        "day": str(request.day),
                        "before": before,
                        "after": request.status,
                    },
                )
        # A missed workout triggers a local review immediately, as well as after sync.
        if request.status == "skipped":
            self.review()
        return self.get()

    def review(self, request=None):
        today = self.today()
        # Serialize writers, including worker and MCP. Missing Oura never means a missed run.
        with Session(self.engine) as db, db.begin():
            self._write(db)
            row = db.scalar(
                select(RacePlan).where(RacePlan.owner == self.owner).with_for_update()
            )
            if row is None:
                return {"status": "no_plan", "changes": []}
            if request and row.revision != request.expected_revision:
                raise PlanConflict("The plan changed. Reload before review.")
            settings = PlanSettings.model_validate(row.settings)
            sessions = deepcopy(row.sessions)
            current = next((s for s in sessions if s["day"] == str(today)), None)
            if current is None:
                return {"status": "outside_plan", "changes": []}
            args = {
                k: getattr(request, k) if request else None
                for k in ("fatigue", "soreness", "illness_or_injury")
            }
            kind = current["current"]["kind"]
            guidance = TrainingService(
                self.engine, self.owner, str(self.zone)
            ).guidance(
                GuidanceRequest(
                    day=today,
                    planned_session=(
                        kind
                        if kind in ("rest", "easy", "long", "quality")
                        else "unspecified"
                    ),
                    **args,
                )
            )
            # Require fresh, complete physiological context for automatic recovery changes.
            blocking = [
                g
                for g in guidance["data_gaps"]
                if not any(
                    g.startswith(prefix)
                    for prefix in (
                        "Fatigue check-in",
                        "Soreness check-in",
                        "Illness/injury check-in",
                        "Training setup is incomplete",
                    )
                )
            ]
            groups = {s["group"] for s in guidance["signals"]} & {
                "autonomic",
                "sleep",
                "subjective",
            }
            running = RunningService(self.engine, self.owner, str(self.zone)).context(
                today - timedelta(days=1)
            )
            yesterday_runs = [
                r for r in running["runs"] if r["day"] == str(today - timedelta(days=1))
            ]
            prior_long = any(
                (r["km"] or 0) >= settings.current_long_km for r in yesterday_runs
            )
            prior_hard = any(
                s["group"] == "recent_training" for s in guidance["signals"]
            )
            if kind in ("long", "quality") and (prior_long or prior_hard):
                groups.add("recent_training")
            changes = []

            def change(session, after, reason, key):
                if (
                    session["status"] != "planned"
                    or session["current"]["kind"] in ("race", "rest")
                    or key in session["adjustment_keys"]
                ):
                    return False
                if after["kind"] in ("easy", "rest"):
                    after["intensity"] = "E" if after["kind"] == "easy" else None
                    after["segments"] = []
                    after["purpose"] = (
                        "Recovery adjustment: conversational effort."
                        if after["kind"] == "easy"
                        else "Pause and review symptoms."
                    )
                before = deepcopy(session["current"])
                if before == after:
                    return False
                changes.append(
                    {
                        "day": session["day"],
                        "before": before,
                        "after": deepcopy(after),
                        "reason": reason,
                        "key": key,
                    }
                )
                session["current"] = after
                session["adjustment_keys"].append(key)
                return True

            protected = lambda d: any(
                -race_buffer(r) <= (r.day - d).days <= 2 for r in settings.races
            )
            if args["illness_or_injury"] is True:
                after = {
                    **current["current"],
                    "kind": "rest",
                    "km": 0,
                    "minutes": None,
                    "label": "Rest and review reported illness/injury",
                }
                change(
                    current,
                    after,
                    "You reported illness or injury. Training is paused for review.",
                    "illness:" + str(today),
                )
            elif groups and not blocking:
                factor = (
                    0.65
                    if len(groups & {"autonomic", "sleep", "subjective"}) >= 2
                    else 0.8
                )
                # Based on the original session, so repeat syncs never compound cuts.
                km = round(current["original"]["km"] * factor, 1)
                km = min(km, current["current"]["km"])
                if 0 < km and (km < current["current"]["km"] or current["current"].get("intensity") in ("T", "M")):
                    after = {
                        **current["current"],
                        "kind": "easy",
                        "km": km,
                        "minutes": round(km * settings.easy_pace_minutes),
                        "label": "Shorter easy run · recovery adjustment",
                    }
                    change(
                        current,
                        after,
                        "Recovery flags: "
                        + ", ".join(sorted(groups))
                        + ". Easier/shorter before rest.",
                        "recovery:" + str(today) + ":" + str(factor),
                    )
            missed = [
                s
                for s in sessions
                if s["status"] == "skipped"
                and today - timedelta(days=7) <= date.fromisoformat(s["day"]) <= today
            ]
            # Preserve the focus of one missed long run in an existing easy slot; never add distance.
            if (
                len(missed) == 1
                and missed[0]["current"]["kind"] == "long"
                and not groups
                and not blocking
                and not any(
                    "missed-long:" + missed[0]["day"] in s["adjustment_keys"]
                    for s in sessions
                )
            ):
                missed_day = date.fromisoformat(missed[0]["day"])
                candidates = [
                    s
                    for s in sessions
                    if max(today, missed_day + timedelta(days=1))
                    <= date.fromisoformat(s["day"])
                    <= missed_day + timedelta(days=3)
                    and s["status"] == "planned"
                    and s["current"]["kind"] == "easy"
                    and not protected(date.fromisoformat(s["day"]))
                ]
                for candidate in candidates:
                    d = date.fromisoformat(candidate["day"])
                    if any(
                        s["current"]["kind"] in ("long", "race")
                        and s["status"] != "skipped"
                        and s is not candidate
                        and abs((date.fromisoformat(s["day"]) - d).days) < 2
                        for s in sessions
                    ):
                        continue
                    replacement = {
                        **candidate["current"],
                        "kind": "long",
                        "intensity": "E",
                        "segments": [],
                        "purpose": "Aerobic endurance at conversational effort; no catch-up work.",
                        "label": "Rescheduled long-run focus · distance capped at existing easy run",
                    }
                    if settings.philosophy == "marathon_endurance":
                        # Reuse this date's growth, weekly budget, cutback and race windows.
                        # The temporary day change never alters saved user settings.
                        reference, _ = generate(settings.model_copy(update={
                            "long_day": DAYS[d.weekday()], "marathon_effort": False,
                        }))
                        limit = next(s["current"] for s in reference if s["day"] == candidate["day"])
                        if limit["kind"] != "long":
                            continue
                        km = floor(min(
                            candidate["current"]["km"], candidate["original"]["km"],
                            missed[0]["current"]["km"], missed[0]["original"]["km"],
                            limit["km"], 28,
                            min(settings.max_long_minutes, 150) / settings.easy_pace_minutes,
                        ) * 10 + 1e-9) / 10
                        replacement.update(
                            km=km,
                            minutes=round(km * settings.easy_pace_minutes),
                            label="Rescheduled conversational long run · marathon limits preserved",
                        )
                    if change(
                        candidate,
                        replacement,
                        "One long run was explicitly skipped. Preserve its focus in an existing slot without adding distance.",
                        "missed-long:" + missed[0]["day"],
                    ):
                        break
            if len(missed) >= 2:
                key = "missed:" + ",".join(s["day"] for s in missed)
                for s in sessions:
                    d = date.fromisoformat(s["day"])
                    if today <= d <= today + timedelta(days=6) and not protected(d):
                        km = round(s["original"]["km"] * 0.8, 1)
                        km = min(km, s["current"]["km"])
                        if 0 < km and (km < s["current"]["km"] or s["current"].get("intensity") in ("T", "M")):
                            change(
                                s,
                                {
                                    **s["current"],
                                    "kind": "easy",
                                    "km": km,
                                    "minutes": round(km * settings.easy_pace_minutes),
                                    "label": "Re-entry easy run · missed sessions",
                                },
                                "Two or more explicitly skipped sessions in seven days: reduce the next week; no catch-up mileage.",
                                key,
                            )
            # Recovery changes never modify race targets, race days, completed sessions or dates.
            fingerprint = {
                "policy": "bounded-plan-v2",
                "running_context": {
                    "summary": running["summary"],
                    "multiple_run_days": running["multiple_run_days"],
                },
                "recovery_policy": guidance.get("policy", {}),
                "recovery_evidence": guidance.get("recovery_evidence", {}),
                "walking_context": {
                    k: guidance["walking_context"][k] for k in ("summary", "walks", "policy")
                } if guidance.get("walking_context") else None,
                "day": str(today),
                "sync": guidance["metadata"].get("last_successful_sync_at"),
                "missed": [s["day"] for s in missed],
                "signals": sorted(groups),
                "blocked": blocking,
                "checkin": args,
                "changes": changes,
                "mode": settings.mode,
            }
            previous = db.scalar(
                select(PlanAudit)
                .where(PlanAudit.owner == self.owner, PlanAudit.event == "review")
                .order_by(PlanAudit.created_at.desc())
                .limit(1)
            )
            approved = False
            if request and request.approve_review_id:
                proposal = db.scalar(
                    select(PlanAudit).where(
                        PlanAudit.id == request.approve_review_id,
                        PlanAudit.owner == self.owner,
                        PlanAudit.event == "review",
                    )
                )
                if (
                    not proposal
                    or proposal.revision != row.revision
                    or proposal.detail != {**fingerprint, "applied": False}
                    or not changes
                ):
                    raise PlanConflict(
                        "The suggestion or recovery data changed. Run a new review before applying."
                    )
                approved = True
            fingerprint["applied"] = bool(
                changes and (settings.mode == "automatic" or approved)
            )
            # Repeated no-op reviews keep a compact log; changed evidence is recorded.
            if not approved and previous and previous.detail == fingerprint:
                return {"status": "unchanged", "changes": [], "data_gaps": blocking}
            if changes and (settings.mode == "automatic" or approved):
                row.sessions = sessions
                row.revision += 1
                row.updated_at = now()
            audit_id = str(uuid4())
            db.add(
                PlanAudit(
                    id=audit_id,
                    owner=self.owner,
                    revision=row.revision,
                    event="review",
                    detail=fingerprint,
                )
            )
            return {
                "status": (
                    "applied"
                    if changes and (settings.mode == "automatic" or approved)
                    else "proposed" if changes else "no_change"
                ),
                "changes": changes,
                "data_gaps": blocking,
                "revision": row.revision,
                "review_id": audit_id,
                "note": (
                    "Illness/injury reported: review participation; a protected race entry does not mean you should run."
                    if args["illness_or_injury"] and kind == "race"
                    else "Targets and dates stay fixed. No catch-up load or increases beyond the schedule. Mark missed runs in the app."
                ),
            }
