"""Bounded, owner-scoped projections of the local Oura archive. No provider I/O."""
from datetime import date, datetime, timedelta, timezone
import math
from statistics import mean
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from ..db import Record, SyncJob

DAILY_KINDS = ('daily_sleep', 'daily_readiness', 'daily_activity', 'daily_stress', 'sleep')
METRICS = ('sleep_score', 'readiness_score', 'sleep_duration_seconds', 'average_hrv_ms',
           'lowest_heart_rate_bpm', 'steps', 'stress_high_seconds', 'recovery_high_seconds')


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def date_range(start, end, maximum=90):
    if end < start:
        raise ValueError('end_date must be on or after start_date')
    if (end - start).days + 1 > maximum:
        raise ValueError(f'Request at most {maximum} inclusive days at a time')
    return [start + timedelta(days=n) for n in range((end-start).days+1)]


def iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class OuraQueries:
    def __init__(self, engine, owner, timezone_name='Europe/Belgrade'):
        self.engine, self.owner = engine, owner
        self.zone = ZoneInfo(timezone_name)

    def _metadata(self, db):
        latest = db.scalar(select(SyncJob).where(SyncJob.owner == self.owner).order_by(SyncJob.id.desc()).limit(1))
        success = db.scalar(select(SyncJob).where(SyncJob.owner == self.owner, SyncJob.status == 'complete')
                            .order_by(SyncJob.finished_at.desc()).limit(1))
        return {'source': 'oura', 'storage': 'local_archive', 'timezone': str(self.zone),
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'last_successful_sync_at': iso(success.finished_at) if success else None,
                'latest_sync_status': latest.status if latest else None,
                'limitations': ['Archive reflects the last successful collection, not live ring readings.',
                                'Missing records are not evidence of rest or zero activity.',
                                'Oura workouts are not Suunto workouts; Suunto is not connected.']}

    def data_status(self):
        with Session(self.engine) as db:
            groups = db.execute(select(Record.kind, func.count(), func.min(Record.day), func.max(Record.day),
                                       func.max(Record.updated_at))
                                .where(Record.owner == self.owner, Record.source == 'oura').group_by(Record.kind)).all()
            return {'metadata': self._metadata(db), 'datasets': [
                {'kind': kind, 'records': count, 'first_day': first, 'last_day': last, 'last_ingested_at': iso(updated)}
                for kind, count, first, last, updated in sorted(groups)],
                'unavailable': ['suunto', 'heart_rate_time_series', 'training_plan', 'subjective_effort']}

    def _days(self, db, start, end):
        days = date_range(start, end)
        records = db.scalars(select(Record).where(Record.owner == self.owner, Record.source == 'oura',
            Record.kind.in_(DAILY_KINDS), Record.day >= start.isoformat(), Record.day <= end.isoformat())
            .order_by(Record.day, Record.id)).all()
        grouped = {}
        for row in records:
            grouped.setdefault(row.day, {}).setdefault(row.kind, []).append(row)
        result = []
        for day in days:
            group = grouped.get(day.isoformat(), {})
            def payload(kind):
                return group[kind][-1].payload if kind in group else {}
            sleeps = [r for r in group.get('sleep', []) if r.payload.get('type') in ('long_sleep', 'sleep')]
            # Legacy payloads without type may still be main sleep. Explicit naps are excluded.
            if not sleeps:
                sleeps = [r for r in group.get('sleep', []) if r.payload.get('type') is None]
            main = max(sleeps, key=lambda r: number(r.payload.get('total_sleep_duration')) or 0, default=None)
            detail = main.payload if main else {}
            metrics = {
                'sleep_score': number(payload('daily_sleep').get('score')),
                'readiness_score': number(payload('daily_readiness').get('score')),
                'sleep_duration_seconds': number(detail.get('total_sleep_duration')),
                'average_hrv_ms': number(detail.get('average_hrv')),
                'lowest_heart_rate_bpm': number(detail.get('lowest_heart_rate')),
                'steps': number(payload('daily_activity').get('steps')),
                'stress_high_seconds': number(payload('daily_stress').get('stress_high')),
                'recovery_high_seconds': number(payload('daily_stress').get('recovery_high')),
            }
            used = [r for kind, rows in group.items() for r in rows if kind != 'sleep']
            if main:
                used.append(main)
            result.append({'day': day.isoformat(), **metrics,
                'temperature_deviation_celsius': number(payload('daily_readiness').get('temperature_deviation')),
                'missing_metrics': [key for key, value in metrics.items() if value is None],
                'provenance': [{'kind': r.kind, 'record_id': r.external_id, 'ingested_at': iso(r.updated_at)} for r in used]})
        return result

    def daily_metrics(self, start, end):
        date_range(start, end)
        with Session(self.engine) as db:
            rows = self._days(db, start, end)
            return {'metadata': self._metadata(db), 'start_date': str(start), 'end_date': str(end),
                    'sleep_selection': 'Longest main sleep per provider day; naps excluded. Overnight values are not 24/7 averages.',
                    'days': rows}

    def recovery_context(self, target, baseline_days=28):
        if not 7 <= baseline_days <= 60:
            raise ValueError('baseline_days must be between 7 and 60')
        with Session(self.engine) as db:
            rows = self._days(db, target-timedelta(days=baseline_days), target)
            current, baseline = rows[-1], rows[:-1]
            comparisons = {}
            for metric in METRICS:
                values = [r[metric] for r in baseline if r[metric] is not None]
                average = mean(values) if values else None
                value = current[metric]
                comparisons[metric] = {'current': value, 'baseline_mean': round(average, 3) if average is not None else None,
                    'observed_days': len(values), 'requested_days': baseline_days,
                    'delta': round(value-average, 3) if value is not None and average is not None else None,
                    'delta_percent': round((value-average)/average*100, 2) if value is not None and average not in (None, 0) else None}
            return {'metadata': self._metadata(db), 'day': current,
                    'baseline': {'start_date': str(target-timedelta(days=baseline_days)), 'end_date': str(target-timedelta(days=1)),
                                 'method': 'Arithmetic mean of available days; target day excluded; no imputation.', 'metrics': comparisons},
                    'interpretation_limit': 'Descriptive context only. Do not diagnose overtraining or prescribe intensity from wearable scores alone.'}

    def _workouts(self, db, start, end, activity=None):
        query = select(Record).where(Record.owner == self.owner, Record.source == 'oura', Record.kind == 'workout',
                                    Record.day >= str(start), Record.day <= str(end)).order_by(Record.day, Record.external_id)
        output = []
        for row in db.scalars(query):
            p = row.payload
            if activity and str(p.get('activity', '')).casefold() != activity.casefold():
                continue
            duration = None
            try:
                duration = (datetime.fromisoformat(p['end_datetime']) - datetime.fromisoformat(p['start_datetime'])).total_seconds()
                if duration < 0:
                    duration = None
            except (KeyError, ValueError, TypeError):
                pass
            output.append({'record_id': row.external_id, 'day': row.day, 'activity': p.get('activity'),
                'start_datetime': p.get('start_datetime'), 'end_datetime': p.get('end_datetime'),
                'duration_seconds': duration, 'distance_meters': number(p.get('distance')),
                'intensity': p.get('intensity'), 'recorded_by': p.get('source'), 'ingested_at': iso(row.updated_at)})
        return output

    def workouts(self, start, end, activity=None, offset=0, limit=50):
        date_range(start, end)
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
            raise ValueError('limit must be 1–100 and offset 0–100000')
        if activity and (len(activity) > 60 or not activity.replace('_', '').replace(' ', '').isalnum()):
            raise ValueError('Use an activity name such as running or walking')
        with Session(self.engine) as db:
            rows = self._workouts(db, start, end, activity)
            return {'metadata': self._metadata(db), 'start_date': str(start), 'end_date': str(end),
                    'total': len(rows), 'offset': offset, 'next_offset': offset+limit if offset+limit < len(rows) else None,
                    'workouts': rows[offset:offset+limit],
                    'limitations': ['Free-text labels, coordinates and routes are not exposed.',
                                    'Oura may contain imported or detected workouts; cross-provider deduplication is not implemented.']}

    def training_summary(self, start, end):
        days = date_range(start, end)
        with Session(self.engine) as db:
            workouts = self._workouts(db, start, end)
            by_activity = {}
            for workout in workouts:
                stats = by_activity.setdefault(workout['activity'] or 'unknown',
                     {'count': 0, 'duration_seconds': 0, 'distance_meters': 0, 'missing_duration': 0, 'missing_distance': 0})
                stats['count'] += 1
                for field in ('duration_seconds', 'distance_meters'):
                    value = workout[field]
                    if value is None:
                        stats['missing_duration' if field == 'duration_seconds' else 'missing_distance'] += 1
                    else:
                        stats[field] += value
            return {'metadata': self._metadata(db), 'start_date': str(start), 'end_date': str(end),
                    'requested_days': len(days), 'workout_count': len(workouts), 'by_activity': by_activity,
                    'limitations': ['Totals include available Oura workouts only; absent workouts do not confirm rest days.',
                                    'No TSS, CTL, ATL, TSB or overtraining diagnosis is calculated.']}
