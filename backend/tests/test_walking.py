"""Synthetic walk interpretation, chronology, owner isolation and plan guards."""
import os
os.environ.setdefault('DATABASE_URL', 'sqlite://')
import unittest
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.db import Base, Record, SyncJob, TrainingProfile, RacePlan
from app.services.walking import WalkingService
from app.services.training import assess, ProfileSettings, GuidanceRequest, TrainingService
from app.services.planning import PlanningService, SavePlan
from test_training import recovery
from test_planning import settings

TODAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 16, tzinfo=timezone.utc)


class WalkingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.service = WalkingService(self.engine, 'owner')
        self.counter = 0
        with Session(self.engine) as db, db.begin():
            db.add(SyncJob(owner='owner', status='complete', finished_at=NOW-timedelta(hours=1)))

    def tearDown(self):
        self.engine.dispose()

    def add(self, kind, day, payload, owner='owner', source='oura'):
        self.counter += 1
        with Session(self.engine) as db, db.begin():
            db.add(Record(owner=owner, source=source, kind=kind, day=str(day), external_id=str(self.counter), payload=payload))

    def walk(self, day=TODAY, minutes=20, intensity='low', hour=10, **changes):
        begin = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)+timedelta(hours=hour)
        payload = {'activity':'walking', 'intensity':intensity, 'start_datetime':begin.isoformat(),
                   'end_datetime':(begin+timedelta(minutes=minutes)).isoformat()}
        owner = changes.pop('owner', 'owner')
        source = changes.pop('source', 'oura')
        payload.update(changes)
        self.add('workout', day, payload, owner, source)

    def physiology(self, target=TODAY, readiness=90, sleep_end=None):
        for offset in range(29):
            day = target-timedelta(days=offset)
            self.add('sleep', day, {'type':'long_sleep', 'total_sleep_duration':28800,
                'average_hrv':50, 'lowest_heart_rate':50,
                'bedtime_end':sleep_end if offset == 0 and sleep_end is not None else f'{day}T06:00:00+00:00'})
            self.add('daily_readiness', day, {'score':readiness if offset == 0 else 90})

    def context(self, day=TODAY):
        return self.service.context(day, NOW)

    def classification(self):
        return self.context()['walks'][0]['classification']

    def test_empty_archive_never_implies_rest_or_benefit(self):
        result = self.context()
        self.assertEqual(result['period'], {'start':'2026-10-27','end':'2026-11-02'})
        self.assertEqual(result['walks'], [])
        self.assertFalse(result['summary']['previous_day_additional_load'])
        self.assertIn('absent workouts', result['limitations'][0])

    def test_easy_walk_with_no_preceding_need_is_ordinary(self):
        self.physiology()
        self.walk()
        self.assertEqual(self.classification(), 'ordinary_activity')

    def test_easy_walk_with_provider_day_caution_is_only_compatible(self):
        self.physiology(readiness=60)
        self.walk()
        result = self.context()
        self.assertEqual(result['walks'][0]['classification'], 'recovery_compatible')
        self.assertFalse(result['policy']['recovery_benefit_proven'])
        self.assertFalse(result['policy']['automatic_progression'])

    def test_prior_hard_activity_is_ordered_before_the_walk(self):
        self.physiology()
        self.walk(hour=8)
        self.add('workout', TODAY, {'activity':'running','intensity':'high',
            'start_datetime':f'{TODAY}T09:00:00+00:00','end_datetime':f'{TODAY}T09:30:00+00:00'})
        self.assertEqual(self.classification(), 'ordinary_activity')
        self.walk(hour=11)
        self.assertEqual([r['classification'] for r in self.context()['walks']], ['ordinary_activity','recovery_compatible'])

    def test_previous_day_hard_activity_can_supply_preceding_need(self):
        self.physiology()
        self.walk()
        prior=TODAY-timedelta(days=1)
        self.add('workout', prior, {'activity':'running','intensity':'high',
            'start_datetime':f'{prior}T09:00:00+00:00','end_datetime':f'{prior}T10:00:00+00:00'})
        self.assertEqual(self.classification(), 'recovery_compatible')

    def test_preceding_hard_walk_does_not_depend_on_external_id_sort(self):
        self.physiology()
        self.walk(hour=11)  # Smaller external ID, later real activity.
        self.walk(hour=8, intensity='high')
        result=self.context()
        self.assertEqual(result['walks'][0]['classification'],'recovery_compatible')
        self.assertEqual(result['walks'][1]['classification'],'additional_load')

    def test_sleep_ending_after_walk_never_supports_compatibility(self):
        self.physiology(readiness=60, sleep_end=f'{TODAY}T11:00:00+00:00')
        self.walk(hour=8)
        self.assertEqual(self.classification(), 'uncertain')
        self.assertTrue(any('sleep completion' in g for g in self.context()['walks'][0]['data_gaps']))

    def test_next_day_caution_does_not_interpret_previous_walk(self):
        self.physiology(readiness=50)
        self.walk(day=TODAY-timedelta(days=1))
        self.assertEqual(self.classification(), 'ordinary_activity')

    def test_missing_physiology_and_thin_baseline_are_explicit(self):
        self.walk()
        result=self.context()['walks'][0]
        self.assertEqual(result['classification'], 'uncertain')
        self.assertTrue(any('insufficient' in gap for gap in result['data_gaps']))
        self.assertTrue(any('missing' in gap for gap in result['data_gaps']))

    def test_stale_and_incomplete_sync_block_compatibility(self):
        self.physiology(readiness=60)
        self.walk()
        with Session(self.engine) as db, db.begin():
            db.scalar(select(SyncJob)).finished_at=NOW-timedelta(hours=37)
        self.assertEqual(self.classification(), 'uncertain')
        with Session(self.engine) as db, db.begin():
            db.scalar(select(SyncJob)).finished_at=NOW
            db.add(SyncJob(owner='owner', status='running'))
        self.assertEqual(self.classification(), 'uncertain')

    def test_intensity_missing_or_vendor_default_is_uncertain(self):
        for intensity in (None, 'moderate', 'unsupported'):
            self.walk(intensity=intensity, hour=8+self.counter)
        self.assertTrue(all(r['classification'] == 'uncertain' and r['data_gaps'] for r in self.context()['walks']))

    def test_missing_invalid_naive_and_nonpositive_timestamps_stay_unknown(self):
        for changes in ({'start_datetime':None}, {'end_datetime':'invalid'},
                        {'start_datetime':f'{TODAY}T10:00:00'},
                        {'end_datetime':f'{TODAY}T09:00:00+00:00'},
                        {'end_datetime':f'{TODAY}T10:00:00+00:00'}):
            self.walk(**changes)
        for row in self.context()['walks']:
            self.assertEqual(row['classification'], 'uncertain')
            self.assertIsNone(row['duration_minutes'])

    def test_high_or_long_walk_is_load_even_without_physiology(self):
        self.walk(minutes=46, hour=8)
        self.walk(intensity='high', hour=10)
        self.assertEqual(self.context()['summary']['additional_load'], 2)

    def test_exact_limits_and_daily_total(self):
        self.physiology(readiness=60)
        self.walk(minutes=45, hour=8)
        self.walk(minutes=15, hour=10)
        self.assertEqual(self.context()['summary']['recovery_compatible'], 2)
        self.walk(minutes=1, hour=11)
        self.assertEqual(self.context()['summary']['additional_load'], 3)

    def test_exact_timed_duplicates_count_once_even_across_offsets(self):
        self.physiology(readiness=60)
        self.walk(minutes=35)
        self.walk(minutes=35, start_datetime=f'{TODAY}T11:00:00+01:00', end_datetime=f'{TODAY}T11:35:00+01:00')
        result=self.context()
        self.assertEqual(len(result['walks']), 1)
        self.assertEqual(result['walks'][0]['duplicate_count'], 2)
        self.assertEqual(result['summary']['recovery_compatible'], 1)

    def test_conflicting_duplicates_and_overlaps_are_uncertain_not_load(self):
        self.walk(minutes=90)
        self.walk(minutes=90, intensity='high')
        self.walk(minutes=60, hour=11)
        result=self.context()
        self.assertEqual(len(result['walks']), 2)
        self.assertEqual(result['summary']['uncertain'], 2)
        self.assertEqual(result['summary']['additional_load'], 0)

    def test_seven_day_window_only_completed_and_owner_scoped(self):
        self.walk(day=TODAY-timedelta(days=7), intensity='high')
        self.walk(day=TODAY-timedelta(days=6), intensity='high')
        self.walk(owner='other', intensity='high')
        self.walk(source='suunto', intensity='high')
        self.walk(hour=17, intensity='high')
        self.walk(hour=15, minutes=120, intensity='high')
        result=self.context()
        self.assertEqual(len(result['walks']), 1)
        self.assertEqual(result['walks'][0]['day'], '2026-10-27')
        self.assertFalse(result['summary']['previous_day_additional_load'])

    def test_safe_fields_ignore_free_text_labels_and_private_route(self):
        self.walk(intensity='high', label='Pretend this is recovery', route='synthetic-private', latitude=1)
        result=self.context()
        self.assertNotIn('synthetic-private', str(result))
        self.assertNotIn('Pretend', str(result))
        self.assertEqual(self.classification(), 'additional_load')

    def test_oldest_returned_day_retains_preceding_hard_walk_context_only(self):
        oldest=TODAY-timedelta(days=6)
        preceding=oldest-timedelta(days=1)
        self.physiology(target=oldest)
        self.walk(day=preceding, intensity='high')
        self.walk(day=oldest)
        result=self.context()
        self.assertEqual(len(result['walks']),1)
        self.assertEqual(result['walks'][0]['day'],str(oldest))
        self.assertEqual(result['walks'][0]['classification'],'recovery_compatible')
        self.assertEqual(result['summary']['recovery_compatible'],1)
        self.assertEqual(result['summary']['additional_load'],0)
        self.assertEqual(sum(result['summary'][name] for name in
            ('recovery_compatible','additional_load','ordinary_activity','uncertain')),1)
        self.assertFalse(result['summary']['previous_day_additional_load'])

    def test_preceding_day_cross_midnight_overlap_keeps_oldest_walk_uncertain(self):
        oldest=TODAY-timedelta(days=6)
        preceding=oldest-timedelta(days=1)
        self.walk(day=preceding, intensity='high',
            start_datetime=f'{preceding}T23:30:00+01:00',
            end_datetime=f'{oldest}T00:30:00+01:00')
        self.walk(day=oldest, start_datetime=f'{oldest}T00:10:00+01:00',
            end_datetime=f'{oldest}T00:40:00+01:00')
        result=self.context()
        self.assertEqual(len(result['walks']),1)
        self.assertEqual(result['walks'][0]['day'],str(oldest))
        self.assertEqual(result['walks'][0]['classification'],'uncertain')
        self.assertTrue(any('Overlapping' in gap for gap in result['walks'][0]['data_gaps']))
        self.assertEqual(result['summary']['uncertain'],1)
        self.assertEqual(result['summary']['additional_load'],0)
        self.assertFalse(result['summary']['previous_day_additional_load'])

    def test_previous_day_credible_load_only_feeds_recent_training(self):
        self.walk(day=TODAY-timedelta(days=1), minutes=46)
        walking=self.context()
        profile=ProfileSettings(available_days=['mon','tue','wed','thu','fri','sat','sun'],current_weekly_km=30)
        data=recovery()
        data['metadata']['last_successful_sync_at']=NOW.isoformat()
        request=GuidanceRequest(day=TODAY,planned_session='quality',fatigue=1,soreness=1,illness_or_injury=False)
        result=assess(profile,data,[],request,TODAY,NOW,walking=walking)
        self.assertEqual(result['recommendation']['action'],'consider_easy')
        self.assertEqual([s['group'] for s in result['signals']],['recent_training'])
        # A compatible walk cannot cancel an existing physiological caution.
        compatible=deepcopy(walking)
        compatible['summary']['previous_day_additional_load']=False
        compatible['summary']['recovery_compatible']=1
        data['baseline']['metrics']['average_hrv_ms']['delta_percent']=-30
        result=assess(profile,data,[],request,TODAY,NOW,walking=compatible)
        self.assertEqual(result['recommendation']['action'],'consider_easy')
        self.assertIn('hrv_drop',[s['code'] for s in result['signals']])
        request.planned_session='rest'
        self.assertEqual(assess(profile,data,[],request,TODAY,NOW,walking=walking)['recommendation']['action'],'keep_rest')

    def test_ambiguous_high_walking_does_not_use_legacy_hard_workout_signal(self):
        self.walk(day=TODAY-timedelta(days=1), intensity='high', start_datetime=None)
        with patch('app.services.training.datetime', wraps=datetime) as clock:
            clock.now.side_effect=lambda zone: NOW.astimezone(zone)
            result=TrainingService(self.engine,'owner').guidance(GuidanceRequest(day=TODAY,planned_session='quality'))
        self.assertFalse(any(s['group']=='recent_training' for s in result['signals']))

    def test_api_validates_dates_and_defaults_to_local_today(self):
        from app.api import app
        with patch('app.api.engine',self.engine), patch('app.api.Session',sessionmaker(self.engine)), TestClient(app) as client:
            default=client.get('/api/walking/context')
            self.assertEqual(default.status_code,200)
            self.assertEqual(client.get('/api/walking/context?day=2026-11-02').status_code,200)
            self.assertEqual(client.get('/api/walking/context?day=bad').status_code,422)
            self.assertEqual(client.get('/api/walking/context?day=2099-01-01').status_code,422)

    def test_planner_walking_caution_preserves_blockers_and_protected_sessions(self):
        self.physiology()
        self.walk(day=TODAY-timedelta(days=1), minutes=70)
        planner=PlanningService(self.engine,'owner')
        with patch.object(PlanningService,'today',return_value=TODAY), patch('app.services.training.datetime', wraps=datetime) as clock:
            clock.now.side_effect=lambda zone: NOW.astimezone(zone)
            planner.save(SavePlan(settings=settings()))
            def alter(kind='quality', status='planned'):
                with Session(self.engine) as db, db.begin():
                    row=db.get(RacePlan,'owner')
                    sessions=deepcopy(row.sessions)
                    sessions[0]['current']['kind']=kind
                    sessions[0]['status']=status
                    row.sessions=sessions
            def current():
                return planner.get()['plan']['sessions'][0]
            for kind,status in [('rest','planned'),('race','planned'),('quality','completed')]:
                alter(kind,status)
                before=deepcopy(current())
                self.assertEqual(planner.review()['changes'],[])
                self.assertEqual(current(),before)
            alter()
            with Session(self.engine) as db, db.begin():
                db.scalar(select(SyncJob)).finished_at=NOW-timedelta(hours=40)
            self.assertEqual(planner.review()['changes'],[])
            with Session(self.engine) as db, db.begin():
                db.scalar(select(SyncJob)).finished_at=NOW
                sleep=db.scalar(select(Record).where(Record.kind=='sleep',Record.day==str(TODAY)))
                saved=deepcopy(sleep.payload)
                sleep.payload={**saved,'average_hrv':None}
            self.assertEqual(planner.review()['changes'],[])
            with Session(self.engine) as db, db.begin():
                db.scalar(select(Record).where(Record.kind=='sleep',Record.day==str(TODAY))).payload=saved
            before=deepcopy(planner.get()['plan'])
            self.assertEqual(planner.review()['status'],'applied')
            after=planner.get()['plan']
            self.assertEqual(current()['current']['kind'],'easy')
            self.assertEqual(after['settings']['races'],before['settings']['races'])
            self.assertTrue(all(a['current']['km'] <= b['current']['km'] for a,b in zip(after['sessions'],before['sessions'])))
            self.assertTrue(all(s['status']=='planned' for s in after['sessions']))
            self.assertEqual(planner.review()['changes'],[])
