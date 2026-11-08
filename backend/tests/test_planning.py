import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
import unittest
from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import patch
from sqlalchemy import create_engine, select, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.db import Base, RacePlan, PlanAudit
from app.services.planning import (
    PlanSettings,
    SavePlan,
    Race,
    generate,
    PlanningService,
    ReviewPlan,
    SessionUpdate,
    PlanMode,
    PlanConflict,
    FitnessResult,
)

TODAY = date(2026, 11, 2)  # Monday


def settings(**changes):
    args = {
        "start": TODAY,
        "weekly_km": 32,
        "peak_weekly_km": 48,
        "current_long_km": 12,
        "easy_pace_minutes": 6,
        "running_days": ["mon", "wed", "fri", "sun"],
        "long_day": "sun",
        "races": [
            {
                "name": "Spring marathon",
                "day": str(TODAY + timedelta(days=140)),
                "distance": "marathon",
                "priority": "main",
                "target_minutes": 240,
            },
            {
                "name": "Tune-up",
                "day": str(TODAY + timedelta(days=70)),
                "distance": "10k",
                "priority": "milestone",
                "target_minutes": 50,
            },
            {
                "name": "Autumn half",
                "day": str(TODAY + timedelta(days=280)),
                "distance": "half_marathon",
                "priority": "main",
                "target_minutes": 110,
            },
        ],
    }
    args.update(changes)
    return PlanSettings.model_validate(args)


def guidance(groups=(), gaps=()):
    return {
        "metadata": {"last_successful_sync_at": "2026-11-02T08:00:00+00:00"},
        "signals": [{"group": g} for g in groups],
        "data_gaps": list(gaps),
    }


class GeneratorTests(unittest.TestCase):
    def test_multiple_main_races_and_custom_milestones_keep_dates_times(self):
        conf = settings()
        conf.races.append(
            Race(
                name="Trail",
                day=TODAY + timedelta(days=90),
                distance="custom",
                custom_km=15,
                priority="milestone",
                target_minutes=100,
            )
        )
        sessions, warnings = generate(conf)
        races = [s["current"] for s in sessions if s["current"]["kind"] == "race"]
        self.assertEqual(len(races), 4)
        self.assertEqual(sum(s["priority"] == "main" for s in races), 2)
        self.assertEqual(
            next(s for s in races if s["label"] == "Trail")["target_minutes"], 100
        )
        self.assertTrue(warnings)

    def test_originals_are_independent_and_schedule_caps_hold(self):
        conf = settings()
        sessions, _ = generate(conf)
        for s in sessions:
            c = s["current"]
            if c["kind"] not in ("race", "rest"):
                self.assertIn(
                    ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][
                        date.fromisoformat(s["day"]).weekday()
                    ],
                    conf.running_days,
                )
                if c["kind"] == "long":
                    self.assertLessEqual(c["minutes"], conf.max_long_minutes)
            self.assertEqual(s["original"], c)
        sessions[0]["current"]["km"] = 0
        self.assertGreater(sessions[0]["original"]["km"], 0)
        first = [
            s["current"]["km"]
            for s in sessions
            if TODAY <= date.fromisoformat(s["day"]) < TODAY + timedelta(days=7)
        ]
        # First current value was edited to zero; original weekly budget remains bounded.
        self.assertLessEqual(sum(first), conf.weekly_km + 0.2)

    def test_target_time_never_invents_fitness_or_increases_training(self):
        a = settings()
        b = a.model_copy(deep=True)
        b.races[0].target_minutes = 180
        sa, _ = generate(a)
        sb, _ = generate(b)
        self.assertEqual(
            [s["current"] for s in sa if s["current"]["kind"] != "race"],
            [s["current"] for s in sb if s["current"]["kind"] != "race"],
        )

    def test_taper_and_recovery_survive_conflicting_races(self):
        conf = settings()
        race = conf.races[0]
        sessions, _ = generate(conf)
        for s in sessions:
            d = date.fromisoformat(s["day"])
            if race.day < d <= race.day + timedelta(days=7):
                self.assertEqual(s["current"]["kind"], "rest")
        first_week_back = [
            s["current"]
            for s in sessions
            if race.day + timedelta(days=7)
            < date.fromisoformat(s["day"])
            <= race.day + timedelta(days=14)
        ]
        self.assertLessEqual(
            sum(s["km"] for s in first_week_back), conf.weekly_km * 0.5 + 0.5
        )
        close = settings(
            races=[
                race.model_dump(),
                {
                    **race.model_dump(),
                    "day": race.day + timedelta(days=10),
                    "name": "Too close",
                },
            ]
        )
        self.assertTrue(any("overlapping" in w for w in generate(close)[1]))

    def test_invalid_dates_distances_and_availability_rejected(self):
        for kwargs in [
            {"running_days": ["mon", "mon", "sun"]},
            {"current_long_km": 81},
            {"peak_weekly_km": 20},
            {"long_day": "tue"},
            {"weekly_growth_percent": 20},
        ]:
            with self.assertRaises(ValidationError):
                settings(**kwargs)
        with self.assertRaises(ValidationError):
            Race(
                name="x",
                day=TODAY,
                distance="custom",
                priority="milestone",
                target_minutes=60,
            )
        conf = settings()
        duplicate = conf.model_dump()
        duplicate["races"].append(duplicate["races"][0])
        with self.assertRaises(ValidationError):
            PlanSettings.model_validate(duplicate)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.service = PlanningService(self.engine, "owner")
        self.day_patch = patch.object(PlanningService, "today", return_value=TODAY)
        self.day_patch.start()
        self.guide_patch = patch(
            "app.services.planning.TrainingService.guidance", return_value=guidance()
        )
        self.guide = self.guide_patch.start()
        self.service.save(SavePlan(settings=settings()))

    def tearDown(self):
        self.guide_patch.stop()
        self.day_patch.stop()
        self.engine.dispose()

    def plan(self):
        return self.service.get()["plan"]

    def session(self, day=TODAY):
        return next(s for s in self.plan()["sessions"] if s["day"] == str(day))

    def alter(self, callback):
        with Session(self.engine) as db, db.begin():
            row = db.get(RacePlan, "owner")
            ss = deepcopy(row.sessions)
            callback(ss)
            row.sessions = ss

    def test_recovery_reduces_then_repeated_sync_is_idempotent(self):
        before = self.session()["current"]["km"]
        self.guide.return_value = guidance(["autonomic", "sleep"])
        result = self.service.review()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.session()["current"]["kind"], "easy")
        self.assertLess(self.session()["current"]["km"], before)
        value = deepcopy(self.session())
        revision = self.plan()["revision"]
        for _ in range(3):
            self.service.review()
        self.assertEqual(self.session(), value)
        self.assertEqual(self.plan()["revision"], revision)

    def test_missing_data_blocks_automatic_recovery_changes(self):
        before = deepcopy(self.session())
        self.guide.return_value = guidance(
            ["autonomic"], ["No successful sync within the last 36 hours"]
        )
        self.assertEqual(self.service.review()["status"], "no_change")
        self.assertEqual(self.session(), before)

    def test_vendor_readiness_alone_does_not_force_changes(self):
        self.guide.return_value = guidance(["vendor_summary"])
        self.assertEqual(self.service.review()["changes"], [])

    def test_races_completed_sessions_and_rest_protected(self):
        self.guide.return_value = guidance(["sleep", "autonomic"])
        for kind, status in [
            ("race", "planned"),
            ("rest", "planned"),
            ("easy", "completed"),
        ]:
            self.alter(
                lambda ss: ss[0].update(
                    current={**ss[0]["current"], "kind": kind}, status=status
                )
            )
            before = deepcopy(self.session())
            self.assertEqual(self.service.review()["changes"], [])
            self.assertEqual(self.session(), before)

    def test_illness_uses_rest_last_without_rewriting_races(self):
        self.service.review(ReviewPlan(expected_revision=1, illness_or_injury=True))
        self.assertEqual(self.session()["current"]["kind"], "rest")
        self.assertEqual(self.session()["original"]["kind"], "easy")

    def test_proposals_need_approval_and_detect_stale_evidence(self):
        self.service.set_mode(PlanMode(mode="review", expected_revision=1))
        self.guide.return_value = guidance(["sleep"])
        original = deepcopy(self.session())
        r = self.service.review(ReviewPlan(expected_revision=2))
        self.assertEqual(r["status"], "proposed")
        self.assertEqual(self.session(), original)
        self.guide.return_value = guidance(["autonomic", "sleep"])
        with self.assertRaises(PlanConflict):
            self.service.review(
                ReviewPlan(expected_revision=2, approve_review_id=r["review_id"])
            )
        self.guide.return_value = guidance(["sleep"])
        r = self.service.review(ReviewPlan(expected_revision=2))
        # Identical proposal may be deduplicated; retain the original review ID.
        history = self.service.get()["history"]
        proposal = next(
            a for a in history if a["event"] == "review" and a["detail"]["changes"]
        )
        r = self.service.review(
            ReviewPlan(expected_revision=2, approve_review_id=proposal["id"])
        )
        self.assertEqual(r["status"], "applied")
        self.assertNotEqual(self.session(), original)

    def test_revision_conflicts_owner_isolation_and_archival(self):
        with self.assertRaises(PlanConflict):
            self.service.review(ReviewPlan(expected_revision=22))
        self.assertIsNone(PlanningService(self.engine, "other").get()["plan"])
        with self.assertRaises(ValueError):
            PlanningService(self.engine, "other").set_status(
                SessionUpdate(day=TODAY, status="skipped", expected_revision=1)
            )
        self.service.save(SavePlan(settings=settings(), expected_revision=1))
        with Session(self.engine) as db:
            event = db.scalar(
                select(PlanAudit).where(PlanAudit.event == "plan_replaced")
            )
            self.assertEqual(event.detail["previous_plan"]["revision"], 1)
        self.assertNotIn("previous_plan", self.service.get()["history"][0]["detail"])

    def test_two_skips_reduce_next_week_without_making_up_mileage(self):
        later = TODAY + timedelta(days=7)
        with patch.object(PlanningService, "today", return_value=later):
            self.service.set_status(
                SessionUpdate(day=TODAY, status="skipped", expected_revision=1)
            )
            revision = self.plan()["revision"]
            self.service.set_status(
                SessionUpdate(
                    day=TODAY + timedelta(days=2),
                    status="skipped",
                    expected_revision=revision,
                )
            )
            changed = [
                s for s in self.plan()["sessions"] if s["current"] != s["original"]
            ]
            self.assertTrue(changed)
            self.assertTrue(
                all(s["current"]["km"] <= s["original"]["km"] for s in changed)
            )
            self.assertTrue(all(s["current"]["kind"] != "rest" for s in changed))
            before = deepcopy(self.plan()["sessions"])
            self.service.review()
            self.assertEqual(before, self.plan()["sessions"])

    def test_missing_workouts_never_mark_skips_and_future_status_rejected(self):
        self.service.review()
        self.assertTrue(all(s["status"] == "planned" for s in self.plan()["sessions"]))
        with self.assertRaises(ValueError):
            self.service.set_status(
                SessionUpdate(
                    day=TODAY + timedelta(days=2), status="skipped", expected_revision=1
                )
            )

    def test_missed_long_focus_moves_only_inside_existing_slot(self):
        sunday = TODAY + timedelta(days=6)
        with patch.object(PlanningService, "today", return_value=sunday):
            self.service.set_status(
                SessionUpdate(day=sunday, status="skipped", expected_revision=1)
            )
            moved = [
                s
                for s in self.plan()["sessions"]
                if "Rescheduled" in s["current"]["label"]
            ]
            self.assertEqual(len(moved), 1)
            self.assertLessEqual(moved[0]["current"]["km"], moved[0]["original"]["km"])
            self.assertEqual(moved[0]["day"], str(TODAY + timedelta(days=7)))
            before = deepcopy(self.plan()["sessions"])
            for _ in range(3):
                self.service.review()
            self.assertEqual(self.plan()["sessions"], before)

    def test_audit_failure_rolls_back_plan(self):
        before = deepcopy(self.plan())
        self.guide.return_value = guidance(["sleep"])

        def fail(*args):
            raise RuntimeError("synthetic audit failure")

        event.listen(PlanAudit, "before_insert", fail)
        try:
            with self.assertRaises(RuntimeError):
                self.service.review()
        finally:
            event.remove(PlanAudit, "before_insert", fail)
        self.assertEqual(self.plan(), before)


class PlanApiTests(unittest.TestCase):
    def test_endpoints_validation_origins_and_versions(self):
        from app.api import app
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        try:
            with patch("app.api.engine", engine), patch(
                "app.api.Session", sessionmaker(engine)
            ), patch.object(PlanningService, "today", return_value=TODAY), TestClient(
                app
            ) as client:
                self.assertIsNone(client.get("/api/plan").json()["plan"])
                body = {
                    "settings": settings().model_dump(mode="json"),
                    "expected_revision": 0,
                }
                self.assertEqual(
                    client.post(
                        "/api/plan",
                        json=body,
                        headers={"Origin": "https://other.example"},
                    ).status_code,
                    403,
                )
                self.assertEqual(client.post("/api/plan", json=body).status_code, 200)
                self.assertEqual(client.post("/api/plan", json=body).status_code, 409)
                self.assertEqual(
                    client.post(
                        "/api/plan/review", json={"expected_revision": 99}
                    ).status_code,
                    409,
                )
                self.assertEqual(
                    client.post(
                        "/api/plan/mode",
                        json={"expected_revision": 1, "mode": "review"},
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    client.post(
                        "/api/plan/session",
                        json={
                            "expected_revision": 2,
                            "day": str(TODAY),
                            "status": "completed",
                        },
                    ).status_code,
                    200,
                )
        finally:
            engine.dispose()


class WorkerReviewTests(unittest.TestCase):
    def test_planner_failure_does_not_fail_provider_sync(self):
        from app.worker import review_plan_after_sync

        with patch(
            "app.services.planning.PlanningService.review",
            side_effect=RuntimeError("synthetic"),
        ):
            review_plan_after_sync()  # Must not raise.


class DanielsTests(unittest.TestCase):
    def test_long_caps_hold_blocks_and_purpose(self):
        conf = settings(philosophy="daniels_inspired", max_long_minutes=180)
        sessions, _ = generate(conf)
        longs = [s["current"] for s in sessions if s["current"]["kind"] == "long"]
        self.assertLessEqual(longs[0]["km"], conf.weekly_km * 0.3 + 0.05)
        self.assertLessEqual(max(s["minutes"] for s in longs), 150)
        self.assertEqual(longs[0]["km"], longs[1]["km"])
        self.assertTrue(all(s["intensity"] == "E" and s["purpose"] for s in longs))

    def test_quality_needs_recent_actual_result_and_current_pace(self):
        conf = settings(philosophy="daniels_inspired", quality_day="wed")
        self.assertFalse(
            any(s["current"]["kind"] == "quality" for s in generate(conf)[0])
        )
        conf.fitness_result = FitnessResult(
            day=TODAY - timedelta(days=100),
            distance_km=5,
            minutes=25,
            threshold_pace_minutes=5.4,
        )
        self.assertFalse(
            any(s["current"]["kind"] == "quality" for s in generate(conf)[0])
        )

    def test_quality_has_capped_work_and_never_uses_race_goal_as_pace(self):
        conf = settings(
            philosophy="daniels_inspired",
            quality_day="wed",
            fitness_result={
                "day": str(TODAY),
                "distance_km": 5,
                "minutes": 25,
                "threshold_pace_minutes": 5.4,
            },
        )
        sessions, _ = generate(conf)
        quality = [s["current"] for s in sessions if s["current"]["kind"] == "quality"]
        self.assertTrue(quality)
        for s in quality:
            t = next(b for b in s["segments"] if b["intensity"] == "T")
            self.assertEqual(t["pace_minutes_per_km"], 5.4)
            self.assertLessEqual(t["repetitions"] * t["km_per_rep"] * 5.4, 20.01)
            self.assertEqual(s["intensity"], "T")
        conf.races[0].target_minutes = 180
        changed = generate(conf)[0]
        self.assertEqual(
            [s["current"] for s in changed if s["current"]["kind"] == "quality"],
            quality,
        )

    def test_quality_day_spacing_and_faster_threshold_validation(self):
        for changes in [
            {"quality_day": "mon"},
            {
                "quality_day": "wed",
                "fitness_result": {
                    "day": str(TODAY),
                    "distance_km": 5,
                    "minutes": 25,
                    "threshold_pace_minutes": 8,
                },
            },
        ]:
            with self.assertRaises(ValidationError):
                settings(philosophy="daniels_inspired", **changes)


class AdditionalPlannerTests(unittest.TestCase):
    setUp = PlannerTests.setUp
    tearDown = PlannerTests.tearDown
    plan = PlannerTests.plan
    session = PlannerTests.session
    alter = PlannerTests.alter

    # Reuse isolated fixtures without re-running inherited tests.
    def test_recent_recorded_long_run_blocks_another_demanding_session(self):
        from app.db import Record

        self.alter(
            lambda ss: ss[0].update(current={**ss[0]["current"], "kind": "long"})
        )
        prior = TODAY - timedelta(days=1)
        with Session(self.engine) as db, db.begin():
            db.add(
                Record(
                    owner="owner",
                    kind="workout",
                    external_id="prior-long",
                    day=str(prior),
                    payload={
                        "activity": "running",
                        "distance": 13000,
                        "start_datetime": str(prior) + "T10:00:00+01:00",
                        "end_datetime": str(prior) + "T11:30:00+01:00",
                    },
                )
            )
        self.assertEqual(self.service.review()["status"], "applied")
        self.assertEqual(self.session()["current"]["kind"], "easy")

    def test_recovery_change_removes_quality_segments(self):
        self.alter(
            lambda ss: ss[0]["current"].update(
                kind="quality", intensity="T", segments=[{"intensity": "T", "km": 1}]
            )
        )
        self.guide.return_value = guidance(["sleep"])
        self.service.review()
        self.assertEqual(self.session()["current"]["segments"], [])
        self.assertEqual(self.session()["current"]["intensity"], "E")

    def test_revising_started_calendar_preserves_past_completed_sessions(self):
        self.service.set_status(
            SessionUpdate(day=TODAY, status="completed", expected_revision=1)
        )
        original = deepcopy(self.session())
        with patch.object(
            PlanningService, "today", return_value=TODAY + timedelta(days=7)
        ):
            conf = settings(weekly_km=36)
            self.service.save(SavePlan(settings=conf, expected_revision=2))
        self.assertEqual(self.session(), original)

    def test_future_result_is_not_an_achieved_benchmark(self):
        conf = settings(
            fitness_result={
                "day": str(TODAY + timedelta(days=1)),
                "distance_km": 5,
                "minutes": 25,
                "threshold_pace_minutes": 5.4,
            }
        )
        with self.assertRaises(ValueError):
            self.service.save(SavePlan(settings=conf, expected_revision=1))


class MarathonEnduranceTests(unittest.TestCase):
    def config(self, **changes):
        args = dict(philosophy="marathon_endurance", marathon_effort=True,
                    history_confirmed=True, weekly_km=40, peak_weekly_km=55,
                    current_long_km=14)
        args.update(changes)
        return settings(**args)

    def test_phases_caps_and_effort_work_are_bounded(self):
        conf = self.config()
        sessions, _ = generate(conf)
        active = [s["current"] for s in sessions]
        self.assertTrue({"base", "build", "marathon_specific", "taper", "cutback"} <= {s["phase"] for s in active})
        work = [s for s in active if s["intensity"] == "M"]
        self.assertTrue(work)
        for s in work:
            self.assertEqual(s["phase"], "marathon_specific")
            self.assertEqual(s["kind"], "long")
            self.assertNotIn("Conversational", s["label"])
            m = next(b for b in s["segments"] if b["intensity"] == "M")
            self.assertLessEqual(m["km"], s["km"] * .15 + .001)
            self.assertLessEqual(m["km"] * conf.easy_pace_minutes, 15)
            week = (date.fromisoformat(s["day"]) - (conf.start - timedelta(days=conf.start.weekday()))).days // 7
            budget = min(conf.peak_weekly_km, conf.weekly_km * (1 + conf.weekly_growth_percent / 100) ** (week - week // 4))
            self.assertLessEqual(m["km"], budget * .05)
            self.assertNotIn("pace_minutes_per_km", m)
            self.assertTrue(all(abs((r.day - date.fromisoformat(s["day"])).days) >= 14 for r in conf.races))
        for s in active:
            if s["kind"] == "long":
                self.assertLessEqual(s["minutes"], 150)
                self.assertLessEqual(s["km"], 28)
        conf.races[0].target_minutes = 180
        changed = generate(conf)[0]
        self.assertEqual([s["current"] for s in changed if s["current"]["kind"] != "race"],
                         [s for s in active if s["kind"] != "race"])

    def test_second_main_marathon_gets_new_relative_phases(self):
        conf = self.config()
        conf.races[-1] = Race(name="Second marathon", day=TODAY + timedelta(days=280),
                              distance="marathon", priority="main", target_minutes=250)
        ss = {s["day"]: s["current"] for s in generate(conf)[0]}
        self.assertEqual(ss[str(TODAY + timedelta(days=175))]["phase"], "base")
        self.assertEqual(ss[str(TODAY + timedelta(days=238))]["phase"], "marathon_specific")
        self.assertEqual(ss[str(TODAY + timedelta(days=266))]["phase"], "taper")
        for day in range(141, 169):
            self.assertNotEqual(ss[str(TODAY + timedelta(days=day))]["intensity"], "M")

    def test_low_or_unconfirmed_base_and_opt_out_never_get_effort(self):
        for changes in ({"history_confirmed": False}, {"weekly_km": 32},
                        {"current_long_km": 12}, {"running_days": ["mon", "fri", "sun"]},
                        {"marathon_effort": False}):
            with self.subTest(changes=changes):
                self.assertFalse(any(s["current"]["intensity"] == "M" for s in generate(self.config(**changes))[0]))

    def test_upcoming_main_marathon_controls_phases_across_multiple_races(self):
        conf = self.config()
        conf.races.append(Race(name="Early main half", day=TODAY + timedelta(days=45),
                               distance="half_marathon", priority="main", target_minutes=110))
        ss = {s["day"]: s["current"] for s in generate(conf)[0]}
        self.assertEqual(ss[str(TODAY + timedelta(days=28))]["phase"], "base")
        self.assertEqual(ss[str(TODAY + timedelta(days=150))]["phase"], "return_to_running")
        self.assertFalse(any(s["intensity"] == "M" for d, s in ss.items() if d > str(conf.races[0].day)))


class MarathonRecoveryTests(unittest.TestCase):
    setUp = PlannerTests.setUp
    tearDown = PlannerTests.tearDown
    session = PlannerTests.session
    alter = PlannerTests.alter
    plan = PlannerTests.plan

    def test_poor_recovery_clears_m_even_if_volume_already_reduced(self):
        self.alter(lambda ss: ss[0]["current"].update(
            kind="long", intensity="M", km=1,
            segments=[{"intensity": "M", "km": 1}], label="Marathon effort"))
        self.guide.return_value = guidance(["sleep"])
        self.service.review()
        current = self.session()["current"]
        self.assertEqual(current["kind"], "easy")
        self.assertEqual(current["intensity"], "E")
        self.assertEqual(current["segments"], [])
        self.assertLessEqual(current["km"], 1)

    def test_method_draft_does_not_mutate_saved_plan(self):
        before = deepcopy(self.plan())
        conf = MarathonEnduranceTests().config()
        generate(conf)
        self.assertEqual(self.plan(), before)
        self.service.save(SavePlan(settings=conf, expected_revision=1))
        self.assertEqual(self.plan()["settings"]["philosophy"], "marathon_endurance")


class MarathonMissedWorkTests(unittest.TestCase):
    setUp = PlannerTests.setUp
    tearDown = PlannerTests.tearDown
    session = PlannerTests.session
    alter = PlannerTests.alter
    plan = PlannerTests.plan

    def test_missed_long_preserves_marathon_caps_in_large_existing_slot(self):
        for weekly, current_long, max_minutes, skipped_offset in (
            (160, 14, 150, 6), (160, 40, 180, 6), (40, 28, 90, 20),
        ):
            with self.subTest(weekly=weekly, max_minutes=max_minutes):
                conf = settings(philosophy="marathon_endurance", weekly_km=weekly,
                                peak_weekly_km=weekly, current_long_km=current_long,
                                max_long_minutes=max_minutes, history_confirmed=True)
                self.service.save(SavePlan(settings=conf, expected_revision=self.plan()["revision"]))
                skipped_day = TODAY + timedelta(days=skipped_offset)
                replacement_day = skipped_day + timedelta(days=1)
                missed = deepcopy(self.session(skipped_day))
                existing = deepcopy(self.session(replacement_day))
                def skip_and_leave_stale_segment(ss):
                    for s in ss:
                        if s["day"] == str(skipped_day):
                            s["status"] = "skipped"
                        if s["day"] == str(replacement_day):
                            s["current"].update(intensity="M", segments=[{"intensity": "M", "km": 1}])
                self.alter(skip_and_leave_stale_segment)
                with patch.object(PlanningService, "today", return_value=replacement_day):
                    self.service.review()
                    moved = self.session(replacement_day)["current"]
                    self.assertEqual(moved["kind"], "long")
                    self.assertEqual(moved["intensity"], "E")
                    self.assertEqual(moved["segments"], [])
                    self.assertLessEqual(moved["km"], min(missed["current"]["km"], existing["current"]["km"], 28))
                    self.assertLessEqual(moved["minutes"], min(max_minutes, 150))
                    reference, _ = generate(conf.model_copy(update={"long_day": "mon"}))
                    limit = next(s["current"] for s in reference if s["day"] == str(replacement_day))
                    self.assertLessEqual(moved["km"], limit["km"])
                    self.assertEqual(self.plan()["settings"]["long_day"], "sun")
                    stable = deepcopy(self.plan()["sessions"])
                    self.service.review()
                    self.assertEqual(self.plan()["sessions"], stable)

    def test_multiple_misses_remove_hard_segments_even_after_volume_reduction(self):
        for intensity in ("M", "T"):
            with self.subTest(intensity=intensity):
                conf = settings(philosophy="marathon_endurance")
                self.service.save(SavePlan(settings=conf, expected_revision=self.plan()["revision"]))
                def mark(ss):
                    ss[0]["status"] = "skipped"
                    ss[2]["status"] = "skipped"
                    ss[4]["current"].update(kind="long", intensity=intensity, km=1,
                                             segments=[{"intensity": intensity, "km": 1}])
                self.alter(mark)
                with patch.object(PlanningService, "today", return_value=TODAY + timedelta(days=4)):
                    self.service.review()
                    current = self.session(TODAY + timedelta(days=4))["current"]
                    self.assertEqual(current["kind"], "easy")
                    self.assertEqual(current["intensity"], "E")
                    self.assertEqual(current["segments"], [])
                    self.assertLessEqual(current["km"], 1)
                    stable = deepcopy(self.plan()["sessions"])
                    self.service.review()
                    self.assertEqual(self.plan()["sessions"], stable)
