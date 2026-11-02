"""Configurable, deterministic session review. Advisory rules, not a validated medical model."""
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import TrainingProfile
from .oura_queries import OuraQueries

SessionType = Literal['unspecified', 'rest', 'easy', 'long', 'quality']

class CoachingRules(BaseModel):
    baseline_days: int = Field(default=28, ge=7, le=60)
    minimum_baseline_days: int = Field(default=14, ge=3, le=60)
    hrv_drop_percent: float = Field(default=20, ge=5, le=60)
    resting_hr_rise_bpm: float = Field(default=5, ge=2, le=20)
    sleep_hours_caution: float = Field(default=6.5, ge=4, le=10)
    readiness_caution: int = Field(default=70, ge=1, le=100)

    @model_validator(mode='after')
    def sufficient_window(self):
        if self.minimum_baseline_days > self.baseline_days:
            raise ValueError('Minimum baseline observations cannot exceed the baseline window')
        return self

class ProfileSettings(BaseModel):
    goal: Literal['general_fitness','5k','10k','half_marathon','marathon','custom'] = 'general_fitness'
    goal_note: str = Field(default='', max_length=160)
    available_days: list[Literal['mon','tue','wed','thu','fri','sat','sun']] = Field(default_factory=list, max_length=7)
    current_weekly_km: float | None = Field(default=None, ge=0, le=250)
    rules: CoachingRules = Field(default_factory=CoachingRules)

    @model_validator(mode='after')
    def unique_days(self):
        if len(set(self.available_days)) != len(self.available_days):
            raise ValueError('Choose each available day once')
        return self

class GuidanceRequest(BaseModel):
    day: date
    planned_session: SessionType = 'unspecified'
    planned_minutes: int | None = Field(default=None, ge=5, le=300)
    fatigue: int | None = Field(default=None, ge=1, le=5)
    soreness: int | None = Field(default=None, ge=1, le=5)
    illness_or_injury: bool | None = None


def load_profile(db, owner):
    row = db.scalar(select(TrainingProfile).where(TrainingProfile.owner == owner))
    return ProfileSettings.model_validate(row.settings) if row else ProfileSettings()


def assess(profile, recovery, workouts, request, today, timestamp):
    """Pure policy: no writes, provider requests, automatic progression or diagnosis."""
    rules, current = profile.rules, recovery['day']
    baseline = recovery['baseline']['metrics']
    meta = recovery['metadata']
    if request.day > today:
        raise ValueError('Use today or a past date. Future-day recovery data is unavailable.')
    gaps, signals = [], []
    def signal(code, group, explanation, value=None, threshold=None):
        unit = {'hrv_drop':'%', 'resting_hr_rise':'bpm', 'short_sleep':'hours', 'readiness_caution':'points', 'recent_hard_session':'workouts', 'high_fatigue':'/5', 'high_soreness':'/5'}.get(code)
        signals.append({'code':code, 'group':group, 'explanation':explanation, 'value':value, 'threshold':threshold, 'unit':unit})

    observed = {}
    for metric in ['average_hrv_ms', 'lowest_heart_rate_bpm']:
        observed[metric] = baseline[metric]['observed_days']
        if observed[metric] < rules.minimum_baseline_days:
            gaps.append(f'{metric}: only {observed[metric]} baseline days; need {rules.minimum_baseline_days}.')
    for metric in ['sleep_duration_seconds','average_hrv_ms','lowest_heart_rate_bpm','readiness_score']:
        if current.get(metric) is None:
            gaps.append(f'{metric} is missing for the requested day.')
    last_sync = meta.get('last_successful_sync_at')
    fresh = False
    if last_sync:
        try:
            when = datetime.fromisoformat(last_sync)
            if when.tzinfo is None:when=when.replace(tzinfo=timezone.utc)
            age=(timestamp-when).total_seconds()/3600
            fresh=0 <= age <= 36
        except ValueError:
            pass
    if not fresh:gaps.append('No successful sync within the last 36 hours; launch the app or use Sync now.')
    if meta.get('latest_sync_status') in ('failed','queued','running'):
        gaps.append('The latest sync is not complete; some records may still be changing.')
    if request.fatigue is None:gaps.append('Fatigue check-in is missing.')
    if request.soreness is None:gaps.append('Soreness check-in is missing.')
    if request.illness_or_injury is None:gaps.append('Illness/injury check-in is missing.')

    hrv = baseline['average_hrv_ms']
    if hrv['observed_days'] >= rules.minimum_baseline_days and hrv['delta_percent'] is not None and hrv['delta_percent'] <= -rules.hrv_drop_percent:
        signal('hrv_drop','autonomic','Overnight HRV is below your configured baseline threshold.',hrv['delta_percent'],-rules.hrv_drop_percent)
    hr = baseline['lowest_heart_rate_bpm']
    if hr['observed_days'] >= rules.minimum_baseline_days and hr['delta'] is not None and hr['delta'] >= rules.resting_hr_rise_bpm:
        signal('resting_hr_rise','autonomic','Lowest overnight heart rate is above your configured baseline threshold.',hr['delta'],rules.resting_hr_rise_bpm)
    sleep = current.get('sleep_duration_seconds')
    if sleep is not None and sleep/3600 < rules.sleep_hours_caution:
        signal('short_sleep','sleep','Main sleep is shorter than your configured threshold.',round(sleep/3600,2),rules.sleep_hours_caution)
    score = current.get('readiness_score')
    if score is not None and score < rules.readiness_caution:
        signal('readiness_caution','vendor_summary','Readiness is below your configured threshold; this overlaps other recovery metrics.',score,rules.readiness_caution)
    if request.fatigue is not None and request.fatigue >= 4:
        signal('high_fatigue','subjective','You reported high fatigue.',request.fatigue,4)
    if request.soreness is not None and request.soreness >= 4:
        signal('high_soreness','subjective','You reported high soreness.',request.soreness,4)
    prior = str(request.day-timedelta(days=1))
    hard = [w for w in workouts if w['day'] == prior and w.get('intensity') == 'high']
    if hard:
        signal('recent_hard_session','recent_training','Oura recorded a high-intensity workout on the previous provider day.',len(hard),1)
    available = profile.available_days
    weekday = ['mon','tue','wed','thu','fri','sat','sun'][request.day.weekday()]
    unavailable = bool(available) and weekday not in available
    if unavailable:
        signal('outside_availability','schedule','This day is outside your configured running days.')
    # Correlated HRV/HR count once. Composite readiness never adds an independent family.
    families = {s['group'] for s in signals} & {'autonomic','sleep','subjective'}
    main_missing = any(current.get(m) is None for m in ['sleep_duration_seconds','average_hrv_ms','lowest_heart_rate_bpm','readiness_score'])
    baseline_enough = all(n >= rules.minimum_baseline_days for n in observed.values())
    checkin_complete = all(v is not None for v in [request.fatigue,request.soreness,request.illness_or_injury])
    complete = fresh and not main_missing and baseline_enough and checkin_complete and meta.get('latest_sync_status') == 'complete'
    cautions = bool(signals)
    session = request.planned_session
    action, suggested, explanation = 'check_in', None, 'Choose a planned session and complete the check-in before deciding.'
    if request.illness_or_injury:
        action,suggested,explanation='rest_and_review','rest','You reported illness or injury. Pause training and review symptoms; seek professional advice if needed.'
    elif session == 'rest':
        action,suggested,explanation='keep_rest','rest','Keep your planned rest day; a high wearable score does not add a workout.'
    elif unavailable:
        action,suggested,explanation='keep_schedule','rest','Keep this as a non-running day unless you explicitly change your availability.'
    elif session == 'unspecified':
        pass
    elif 'subjective' in families or len(families) >= 2:
        action,suggested,explanation='consider_easy_or_rest','easy','Your check-in or multiple recovery signal groups support reducing the session. Choose rest if easy movement does not feel comfortable.'
    elif session in ('long','quality') and cautions:
        action,suggested,explanation='consider_easy','easy','Consider replacing the demanding session with easy movement; review the listed signals and your check-in.'
    elif not complete:
        action,suggested,explanation='review_missing_context',None,'There is not enough current context to endorse the planned session. Review the data gaps and how you feel.'
    elif cautions:
        action,suggested,explanation='keep_easy_with_review','easy','Keep effort easy and reassess how you feel; do not add intensity or volume.'
    else:
        action,suggested,explanation='keep_planned',session,'No configured caution rules fired. Keep the existing plan if it matches your established training and you feel well; this is not a guarantee of readiness.'

    missing_profile=[]
    if not available:missing_profile.append('available running days')
    if profile.current_weekly_km is None:missing_profile.append('current weekly running distance')
    if missing_profile:
        gaps += ['Training setup is incomplete: '+', '.join(missing_profile)+'.']
        if action == 'keep_planned' and session in ('long','quality'):
            action,suggested,explanation='review_missing_context',None,'Set your running availability and current weekly distance before reviewing a demanding session.'
    return {'metadata':meta,'policy':{'version':'advisory-v1','rules':rules.model_dump(),
                'basis':'Configurable coaching heuristics; not clinically validated thresholds or an overtraining test.',
                'signal_groups':len(families),'readiness_counts_as_independent_signal':False},
        'mode':'today' if request.day == today else 'retrospective',
        'profile':profile.model_dump(), 'request':request.model_dump(mode='json'),
        'recommendation':{'action':action,'suggested_session':suggested,'explanation':explanation,
            'duration_minutes':request.planned_minutes if action == 'keep_planned' else None,
            'effort_cue':'If choosing easy movement, keep breathing comfortable enough for full sentences; slow down or stop if it feels unexpectedly hard.' if suggested=='easy' else None,
            'automatic_progression':False},
        'evidence_quality':'limited' if gaps else 'sufficient_for_configured_rules',
        'signals':signals,'data_gaps':gaps,
        'recovery_evidence':{'day':current,'baseline':recovery['baseline']},
        'recent_workouts':{'period_start':str(request.day-timedelta(days=7)),'period_end':str(request.day-timedelta(days=1)),
                           'count':len(workouts),'high_intensity_previous_day':len(hard)},
        'limitations':['Advice requires your decision; historical results must not guide a current session.',
                       'Oura workout coverage is incomplete; no Suunto load, pace zones or validated training-stress model.',
                       'No race plan, prescribed mileage increase or automatic schedule changes are generated.'],
        'references':[{'url':'https://ouraring.com/blog/readiness-score/','use':'Vendor readiness interpretation only, not validation of this policy.'},
                      {'url':'https://www.cdc.gov/physical-activity-basics/measuring/index.html','use':'Talk test as a relative-effort cue, not a calibrated running zone.'}]}


class TrainingService:
    def __init__(self, engine, owner, timezone_name='Europe/Belgrade'):
        self.engine,self.owner=engine,owner
        self.queries=OuraQueries(engine,owner,timezone_name)

    def profile(self):
        with Session(self.engine) as db:
            return load_profile(db,self.owner)

    def guidance(self, request):
        today=datetime.now(self.queries.zone).date()
        if request.day > today:
            raise ValueError('Future-day recovery data is unavailable; use today or a past date.')
        profile=self.profile()
        recovery=self.queries.recovery_context(request.day,profile.rules.baseline_days)
        with Session(self.engine) as db:
            workouts=self.queries._workouts(db,request.day-timedelta(days=7),request.day-timedelta(days=1))
        return assess(profile,recovery,workouts,request,today,datetime.now(timezone.utc))
