import os
os.environ.setdefault('DATABASE_URL','sqlite://')
from datetime import date, datetime, timezone
import unittest
from pydantic import ValidationError
from app.services.training import assess, ProfileSettings, GuidanceRequest, CoachingRules

TODAY=date(2026,10,30)
NOW=datetime(2026,10,30,10,tzinfo=timezone.utc)

def recovery(hrv_delta=0,hr_delta=0,sleep_hours=8,readiness=90,observations=20):
    return {'metadata':{'source':'oura','last_successful_sync_at':'2026-10-30T09:00:00+00:00','latest_sync_status':'complete'},
            'day':{'day':str(TODAY),'sleep_duration_seconds':None if sleep_hours is None else sleep_hours*3600,
                   'readiness_score':readiness,'average_hrv_ms':50,'lowest_heart_rate_bpm':50},
            'baseline':{'metrics':{'average_hrv_ms':{'observed_days':observations,'delta_percent':hrv_delta},
                        'lowest_heart_rate_bpm':{'observed_days':observations,'delta':hr_delta}}}}

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.profile=ProfileSettings(available_days=['mon','tue','wed','thu','fri','sat','sun'],current_weekly_km=30)

    def run_policy(self,data=None,plan='quality',profile=None,workouts=None,**overrides):
        inputs=dict(day=TODAY,planned_session=plan,planned_minutes=45,fatigue=1,soreness=1,illness_or_injury=False)
        inputs.update(overrides)
        return assess(profile or self.profile,data or recovery(),workouts or [],GuidanceRequest(**inputs),TODAY,NOW)

    def test_good_context_keeps_existing_plan_without_progression(self):
        r=self.run_policy()
        self.assertEqual(r['recommendation']['action'],'keep_planned')
        self.assertEqual(r['recommendation']['duration_minutes'],45)
        self.assertFalse(r['recommendation']['automatic_progression'])

    def test_rest_stays_rest_despite_high_readiness(self):
        self.assertEqual(self.run_policy(plan='rest')['recommendation']['suggested_session'],'rest')

    def test_correlated_hrv_and_hr_count_once(self):
        r=self.run_policy(recovery(hrv_delta=-30,hr_delta=8),plan='easy')
        self.assertEqual(r['policy']['signal_groups'],1)
        self.assertEqual(r['recommendation']['action'],'keep_easy_with_review')

    def test_readiness_does_not_double_count_other_metrics(self):
        r=self.run_policy(recovery(hrv_delta=-30,readiness=50),plan='easy')
        self.assertEqual(r['policy']['signal_groups'],1)

    def test_sleep_plus_autonomic_or_fatigue_adjusts_session(self):
        r=self.run_policy(recovery(hrv_delta=-30,sleep_hours=5))
        self.assertEqual(r['recommendation']['action'],'consider_easy_or_rest')
        self.assertIsNone(r['recommendation']['duration_minutes'])
        self.assertEqual(self.run_policy(fatigue=5)['recommendation']['action'],'consider_easy_or_rest')

    def test_illness_wins_over_good_scores(self):
        self.assertEqual(self.run_policy(illness_or_injury=True)['recommendation']['action'],'rest_and_review')

    def test_missing_inputs_and_thin_baseline_block_endorsement(self):
        for kwargs in [{'fatigue':None},{'illness_or_injury':None}]:
            self.assertEqual(self.run_policy(**kwargs)['recommendation']['action'],'review_missing_context')
        r=self.run_policy(recovery(hrv_delta=-80,observations=2))
        self.assertFalse(any(s['code']=='hrv_drop' for s in r['signals']))
        self.assertEqual(r['recommendation']['action'],'review_missing_context')
        self.assertEqual(self.run_policy(recovery(sleep_hours=None))['recommendation']['action'],'review_missing_context')

    def test_stale_sync_and_interrupted_sync_are_visible(self):
        d=recovery();d['metadata']['last_successful_sync_at']='2026-10-20T09:00:00+00:00'
        self.assertEqual(self.run_policy(d)['recommendation']['action'],'review_missing_context')
        d=recovery();d['metadata']['latest_sync_status']='failed'
        self.assertEqual(self.run_policy(d)['recommendation']['action'],'review_missing_context')

    def test_previous_day_hard_workout_changes_quality_session(self):
        r=self.run_policy(workouts=[{'day':'2026-10-29','intensity':'high'}])
        self.assertEqual(r['recommendation']['action'],'consider_easy')

    def test_custom_rule_changes_result(self):
        p=self.profile.model_copy(deep=True);p.rules.hrv_drop_percent=40
        self.assertEqual(self.run_policy(recovery(hrv_delta=-30),profile=p)['recommendation']['action'],'keep_planned')
        self.assertEqual(self.run_policy(recovery(hrv_delta=-30))['recommendation']['action'],'consider_easy')

    def test_schedule_and_incomplete_profile(self):
        p=ProfileSettings(available_days=['mon'],current_weekly_km=30)
        self.assertEqual(self.run_policy(profile=p)['recommendation']['action'],'keep_schedule')
        self.assertEqual(self.run_policy(profile=ProfileSettings())['recommendation']['action'],'review_missing_context')

    def test_unknown_plan_requests_checkin_and_historical_label(self):
        self.assertEqual(self.run_policy(plan='unspecified')['recommendation']['action'],'check_in')
        self.assertEqual(self.run_policy(day=date(2026,10,29))['mode'],'retrospective')
        with self.assertRaises(ValueError):self.run_policy(day=date(2026,11,1))

    def test_invalid_settings_and_checkin_rejected(self):
        with self.assertRaises(ValidationError):CoachingRules(baseline_days=7,minimum_baseline_days=14)
        with self.assertRaises(ValidationError):ProfileSettings(current_weekly_km=-5)
        with self.assertRaises(ValidationError):ProfileSettings(available_days=['mon','mon'])
        with self.assertRaises(ValidationError):GuidanceRequest(day=TODAY,fatigue=6)
