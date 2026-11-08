import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
import unittest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.db import Base, Record
from app.services.running import RunningService


class RunningContextTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.service = RunningService(self.engine, "owner")

    def tearDown(self):
        self.engine.dispose()

    def add(
        self,
        id,
        day="2026-11-02",
        activity="running",
        distance=5000,
        start="18:00",
        end="18:30",
        owner="owner",
    ):
        with Session(self.engine) as db, db.begin():
            db.add(
                Record(
                    owner=owner,
                    kind="workout",
                    external_id=id,
                    day=day,
                    payload={
                        "activity": activity,
                        "distance": distance,
                        "start_datetime": day + "T" + start + ":00+01:00",
                        "end_datetime": day + "T" + end + ":00+01:00",
                        "source": "confirmed",
                    },
                )
            )

    def test_distance_elapsed_pace_and_nonrunning_owner_filters(self):
        self.add("run")
        self.add("walk", activity="walking")
        self.add("other", owner="other")
        r = self.service.context(date(2026, 11, 2))
        self.assertEqual(r["summary"]["recorded_runs"], 1)
        self.assertEqual(r["runs"][0]["elapsed_pace_minutes_per_km"], 6)
        self.assertEqual(r["coverage"], "unconfirmed")
        self.assertNotIn("vdot", r["summary"])

    def test_identical_timed_duplicates_collapsed_but_distinct_sessions_preserved(self):
        self.add("a")
        self.add("copy")
        self.add("b", start="19:00", end="19:30")
        r = self.service.context(date(2026, 11, 2))
        self.assertEqual(r["summary"]["duplicate_records_excluded"], 1)
        self.assertEqual(r["summary"]["recorded_km"], 10)
        self.assertEqual(r["multiple_run_days"], ["2026-11-02"])

    def test_missing_metrics_and_empty_weeks_remain_unknown(self):
        self.add("missing", distance=None)
        r = self.service.context(date(2026, 11, 2))
        self.assertIsNone(r["summary"]["observed_km_per_week"])
        self.assertIsNone(r["runs"][0]["elapsed_pace_minutes_per_km"])
        self.assertEqual(r["summary"]["missing_distance"], 1)
        self.assertTrue(all(w["recorded_km"] is None for w in r["weeks"]))

    def test_exact_complete_window_and_no_fake_rest_or_current_day(self):
        self.add("old", day="2026-10-05")
        self.add("first", day="2026-10-06")
        self.add("today", day="2026-11-03")
        r = self.service.context(date(2026, 11, 2))
        self.assertEqual([w["record_id"] for w in r["runs"]], ["first"])
        self.assertEqual(r["start"], "2026-10-06")
        self.assertEqual(len(r["weeks"]), 4)

    def test_invalid_zero_distance_does_not_invent_fast_pace(self):
        self.add("zero", distance=0)
        self.add("negative", distance=-100, start="19:00", end="19:30")
        r = self.service.context(date(2026, 11, 2))
        self.assertEqual(r["summary"]["missing_distance"], 2)
        self.assertTrue(
            all(w["elapsed_pace_minutes_per_km"] is None for w in r["runs"])
        )
