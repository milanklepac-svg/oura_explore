"""Conservative interpretation of recorded walks; no inferred recovery benefit."""
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import Record, TrainingProfile
from .oura_queries import OuraQueries, number

DEFAULT_RULES = {'baseline_days': 28, 'minimum_baseline_days': 14,
                 'hrv_drop_percent': 20, 'resting_hr_rise_bpm': 5,
                 'sleep_hours_caution': 6.5, 'readiness_caution': 70}
CLASSIFICATIONS = ('recovery_compatible', 'additional_load', 'ordinary_activity', 'uncertain')


def instant(value):
    """Unknown timezone is unknown ordering, never silently assume local/UTC."""
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def physiology(context, rules, timestamp, start):
    """Use the walk's provider day only, and only sleep completed before the walk."""
    current, baseline, meta = context['day'], context['baseline']['metrics'], context['metadata']
    gaps = []
    sync = instant(meta.get('last_successful_sync_at'))
    if not sync or not 0 <= (timestamp-sync).total_seconds() <= 36*3600 or meta.get('latest_sync_status') != 'complete':
        gaps.append('Archive sync is missing, incomplete or older than 36 hours.')
    for metric in ('sleep_duration_seconds', 'average_hrv_ms', 'lowest_heart_rate_bpm', 'readiness_score'):
        if current.get(metric) is None:
            gaps.append(f'{metric} is missing on the walk provider day.')
    for metric in ('average_hrv_ms', 'lowest_heart_rate_bpm'):
        if baseline[metric]['observed_days'] < rules['minimum_baseline_days']:
            gaps.append(f'{metric} baseline has insufficient observed days.')
    sleep_end = instant(context.get('sleep_end'))
    if not sleep_end or sleep_end > start:
        gaps.append('Main sleep completion before this walk is not confirmed.')
    hrv, hr = baseline['average_hrv_ms'], baseline['lowest_heart_rate_bpm']
    caution = ((hrv['delta_percent'] is not None and hrv['delta_percent'] <= -rules['hrv_drop_percent'])
               or (hr['delta'] is not None and hr['delta'] >= rules['resting_hr_rise_bpm'])
               or (current.get('sleep_duration_seconds') is not None and current['sleep_duration_seconds']/3600 < rules['sleep_hours_caution'])
               or (current.get('readiness_score') is not None and current['readiness_score'] < rules['readiness_caution']))
    return gaps, caution


class WalkingService:
    def __init__(self, engine, owner, timezone_name='Europe/Belgrade'):
        self.engine, self.owner = engine, owner
        self.queries = OuraQueries(engine, owner, timezone_name)

    def context(self, day=None, timestamp=None):
        timestamp = timestamp or datetime.now(timezone.utc)
        today = timestamp.astimezone(self.queries.zone).date()
        target = day or today
        if target > today:
            raise ValueError('Use today or a past provider day for walking context.')
        start_day = target-timedelta(days=6)
        with Session(self.engine) as db:
            metadata = self.queries._metadata(db)
            profile = db.get(TrainingProfile, self.owner)
            rules = {**DEFAULT_RULES, **((profile.settings.get('rules') or {}) if profile else {})}
            records = db.scalars(select(Record).where(
                Record.owner == self.owner, Record.source == 'oura', Record.kind == 'workout',
                Record.day >= str(start_day-timedelta(days=1)), Record.day <= str(target)
            ).order_by(Record.day, Record.external_id)).all()
            rows, hard, seen = [], [], {}
            for record in records:
                payload = record.payload
                begin, end = instant(payload.get('start_datetime')), instant(payload.get('end_datetime'))
                intensity = payload.get('intensity')
                intensity = intensity.casefold() if isinstance(intensity, str) else None
                valid = begin is not None and end is not None and end > begin
                if (begin and begin > timestamp) or (end and end > timestamp):
                    continue
                if (valid and intensity in ('high', 'hard')
                    and str(payload.get('activity', '')).casefold() != 'walking'
                    and begin.astimezone(self.queries.zone).date().isoformat() == record.day):
                    hard.append((record.day, end))
                if str(payload.get('activity', '')).casefold() != 'walking':
                    continue
                gaps = []
                if not valid:
                    gaps.append('Start/end timestamps or positive elapsed duration are missing or invalid; completion/order cannot be confirmed.')
                if valid and begin.astimezone(self.queries.zone).date().isoformat() != record.day:
                    gaps.append('Provider day does not match the local start date.')
                if intensity not in ('low', 'easy', 'moderate', 'high', 'hard'):
                    gaps.append('Recorded intensity is missing or unsupported.')
                if intensity == 'moderate':
                    gaps.append('The imported workout projection lacks activity heart-rate evidence; Oura moderate intensity may be a vendor default.')
                row = {'day': record.day, 'duration_minutes': (end-begin).total_seconds()/60 if valid else None,
                       'intensity': intensity if intensity in ('low', 'easy', 'moderate', 'high', 'hard') else None,
                       'classification': 'uncertain', 'reasons': [], 'data_gaps': gaps,
                       'start_datetime': begin.isoformat() if begin else None,
                       'end_datetime': end.isoformat() if end else None, 'duplicate_count': 1}
                key = (record.day, begin, end) if valid else None
                if key and key in seen:
                    prior = seen[key]
                    prior['duplicate_count'] += 1
                    if prior['intensity'] != row['intensity']:
                        prior['data_gaps'].append('Exact timed duplicates disagree on intensity.')
                    continue
                if key:
                    seen[key] = row
                rows.append(row)
            contexts = {}
            for row in rows:
                if row['day'] in contexts:
                    continue
                walk_day = date.fromisoformat(row['day'])
                context = self.queries.recovery_context(walk_day, rules['baseline_days'])
                sleeps = db.scalars(select(Record).where(Record.owner == self.owner, Record.source == 'oura',
                    Record.kind == 'sleep', Record.day == row['day'])).all()
                main = [r for r in sleeps if r.payload.get('type') in ('long_sleep', 'sleep')]
                if not main:
                    main = [r for r in sleeps if r.payload.get('type') is None]
                selected = max(main, key=lambda r: number(r.payload.get('total_sleep_duration')) or 0, default=None)
                context['sleep_end'] = selected.payload.get('bedtime_end') if selected else None
                contexts[row['day']] = context
        # Detect overlaps before totals, so ambiguous minutes never create a load signal.
        for row in rows:
            begin, end = instant(row['start_datetime']), instant(row['end_datetime'])
            if begin and end and end > begin and any(
                other is not row and instant(other['start_datetime']) and instant(other['end_datetime'])
                and instant(other['end_datetime']) > instant(other['start_datetime'])
                and instant(other['start_datetime']) < end and instant(other['end_datetime']) > begin
                for other in rows):
                row['data_gaps'].append('Overlapping walking records cannot be safely separated or deduplicated.')
        totals = {d: sum(r['duration_minutes'] or 0 for r in rows if r['day'] == d and not r['data_gaps'])
                  for d in contexts}
        # Resolve observed load independently of record ID/order before checking prior activity.
        for row in rows:
            if not row['data_gaps'] and (row['intensity'] in ('high', 'hard')
                or row['duration_minutes'] > 45 or totals[row['day']] > 60):
                row['classification'] = 'additional_load'
                row['reasons'].append('Recorded high effort or duration exceeds the conservative easy-walking envelope; additional observed activity load.')
        for row in rows:
            begin = instant(row['start_datetime'])
            if row['data_gaps']:
                row['reasons'].append('Recording ambiguity prevents a reliable walking interpretation.')
                continue
            if row['classification'] == 'additional_load':
                continue
            gaps, caution = physiology(contexts[row['day']], rules, timestamp, begin)
            prior = str(date.fromisoformat(row['day'])-timedelta(days=1))
            preceding_hard = any(d in (prior, row['day']) and finish <= begin for d, finish in hard)
            preceding_hard = preceding_hard or any(
                other['day'] in (prior, row['day']) and other['classification'] == 'additional_load'
                and other['intensity'] in ('high', 'hard') and instant(other['end_datetime']) <= begin
                for other in rows)
            if gaps:
                row['data_gaps'].extend(gaps)
                row['reasons'].append('Easy short walking is recorded, but recovery context is insufficient for a compatible interpretation.')
            elif caution or preceding_hard:
                row['classification'] = 'recovery_compatible'
                row['reasons'].append('Short low-intensity walking fits the conservative envelope alongside preceding hard activity or provider-day recovery cautions; benefit is unproven.')
            else:
                row['classification'] = 'ordinary_activity'
                row['reasons'].append('Short low-intensity walking is ordinary activity; no preceding recovery need or benefit is inferred.')
        # The preceding provider day remains internal context for chronology and load.
        # Exclude it from the public seven-day window and every summary count.
        rows = [row for row in rows if row['day'] >= str(start_day)]
        summary = {name: sum(row['classification'] == name for row in rows) for name in CLASSIFICATIONS}
        summary['previous_day_additional_load'] = any(row['day'] == str(target-timedelta(days=1))
            and row['classification'] == 'additional_load' for row in rows)
        return {'metadata': metadata, 'period': {'start': str(start_day), 'end': str(target)},
                'summary': summary, 'walks': rows,
                'policy': {'version': 'walking-heuristic-v1', 'maximum_easy_walk_minutes': 45,
                    'maximum_daily_easy_minutes': 60, 'basis': 'Conservative product heuristics, not prescribed medical thresholds or a validated recovery model.',
                    'physiology_rules': rules,
                    'automatic_progression': False, 'recovery_benefit_proven': False,
                    'references': ['https://support.ouraring.com/hc/en-us/articles/6321008135699-Activity-Heart-Rate',
                                   'https://www.cdc.gov/physical-activity-basics/measuring/index.html']},
                'limitations': ['Only recorded Oura walking in seven provider days; absent workouts do not establish rest or benefit.',
                    'Recovery-compatible means compatible with easy activity, never proven recovery or a fatigue offset.',
                    'No following-day physiology is used to interpret an earlier walk; the import lacks activity HR samples and subjective effort.',
                    'Walking never earns running completion credit, raises mileage, changes races or offsets existing cautions.']}
