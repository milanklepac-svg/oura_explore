"""Observed running context. Never equates an absent workout with a rest day."""

from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import Record
from .oura_queries import iso, number
from .suunto_sync import status as suunto_status
from .oura_queries import OuraQueries


def positive(value):
    try:
        value = number(value)
    except OverflowError:
        return None
    return value if value is not None and value > 0 else None


class RunningService:
    def __init__(self, engine, owner, timezone_name="Europe/Belgrade"):
        self.queries = OuraQueries(engine, owner, timezone_name)

    def context(self, through=None, include_today=False):
        # Planning defaults to complete days; the dashboard may include today.
        end = through or datetime.now(self.queries.zone).date() - timedelta(days=0 if include_today else 1)
        start = end - timedelta(days=27)
        with Session(self.queries.engine) as db:
            rows = self.queries._workouts(db, start, end, "running")
            metadata = self.queries._metadata(db)
            suunto = []
            for record in db.scalars(select(Record).where(Record.owner == self.queries.owner,
                    Record.source == 'suunto', Record.kind == 'workout',
                    Record.day >= str(start), Record.day <= str(end))):
                p = record.payload
                suunto.append({**{key: p.get(key) for key in (
                    'activity', 'activity_id', 'start_datetime', 'end_datetime',
                    'distance_meters', 'duration_seconds', 'elapsed_duration_seconds',
                    'duration_basis', 'timezone_offset_minutes', 'day_basis')},
                               'record_id': record.external_id, 'day': record.day,
                               'source': 'suunto', 'recorded_by': 'suunto', 'ingested_at': iso(record.updated_at)})
            provider_status = suunto_status(db, self.queries.owner)
        rows, cross_duplicates, ambiguous = reconcile(rows, suunto)
        metadata = {**metadata, 'source': 'reconciled_running_archive',
                    'providers': {'oura': metadata,
                                  'suunto': provider_status},
                    'limitations': ['Recovery metrics remain exclusively Oura.',
                                    'Running distance and active duration prefer Suunto for unambiguous matched sessions.']}
        metadata['last_successful_sync_at'] = max(filter(None, [metadata.get('last_successful_sync_at'), provider_status['last_successful_sync_at']]), default=None)
        metadata['latest_sync_status'] = 'see_providers'
        runs = []
        seen = set()
        duplicates = cross_duplicates
        for r in rows:
            for field in ("distance_meters", "duration_seconds", "elapsed_duration_seconds"):
                r[field] = positive(r.get(field))
            r["excluded_from_totals"] = r["reconciliation"] == "ambiguous_overlap"
            if r.get("source") == "suunto":
                times = interval(r)
                if times is None or times[1] > datetime.now(timezone.utc):
                    r["excluded_from_totals"] = True
                    r["observation_warning"] = "unconfirmed_completion"
            key = (r.get("source"), r["start_datetime"], r["end_datetime"], r["distance_meters"])
            # Only identical, timed records are collapsed. Same-day separate runs remain separate.
            if key[1] and key[2] and key in seen:
                duplicates += 1
                continue
            seen.add(key)
            km = (
                r["distance_meters"] / 1000
                if r["distance_meters"] is not None and r["distance_meters"] > 0
                else None
            )
            minutes = (
                r["duration_seconds"] / 60
                if r["duration_seconds"] is not None and r["duration_seconds"] > 0
                else None
            )
            runs.append(
                {
                    **r,
                    "km": round(km, 3) if km is not None else None,
                    "minutes": round(minutes, 2) if minutes is not None else None,
                    "elapsed_pace_minutes_per_km": (
                        round((r.get("elapsed_duration_seconds") or r["duration_seconds"]) / 60 / km, 3)
                        if km and (r.get("elapsed_duration_seconds") or (r["duration_seconds"] if r.get("source") != "suunto" else None)) else None
                    ),
                }
            )
        blocks = []
        for index in range(4):
            a = start + timedelta(days=index * 7)
            b = a + timedelta(days=6)
            block = [r for r in runs if str(a) <= r["day"] <= str(b)]
            valid = [r["km"] for r in block if r["km"] is not None and not r["excluded_from_totals"]]
            blocks.append(
                {
                    "start": str(a),
                    "end": str(b),
                    "recorded_runs": sum(not r["excluded_from_totals"] for r in block),
                    "visible_records": len(block),
                    "recorded_km": round(sum(valid), 2) if valid else None,
                    "missing_distance": sum(r["km"] is None for r in block),
                }
            )
        values = [r["km"] for r in runs if r["km"] is not None and not r["excluded_from_totals"]]
        days = sorted({r["day"] for r in runs})
        repeated = [day for day in days if sum(r["day"] == day for r in runs) > 1]
        return {
            "metadata": metadata,
            "start": str(start),
            "end": str(end),
            "includes_partial_today": include_today and end == datetime.now(self.queries.zone).date(),
            "runs": sorted(
                runs, key=lambda r: (r["day"], r["start_datetime"] or ""), reverse=True
            ),
            "weeks": blocks,
            "summary": {
                "recorded_runs": sum(not r["excluded_from_totals"] for r in runs),
                "visible_records": len(runs),
                "recorded_km": round(sum(values), 2),
                "observed_km_per_week": round(sum(values) / 4, 2) if values else None,
                "longest_recorded_km": max(values, default=None),
                "missing_distance": sum(r["km"] is None for r in runs),
                "missing_duration": sum(r["minutes"] is None for r in runs),
                "duplicate_records_excluded": duplicates,
                "ambiguous_overlap_records": ambiguous,
                "missing_suunto_distance": sum(r.get("source") == "suunto" and r["km"] is None for r in runs),
            },
            "multiple_run_days": repeated,
            "coverage": "unconfirmed",
            "limitations": [
                "Recorded totals are incomplete until you confirm all runs are present; an empty week is unknown, not zero training.",
                "Elapsed pace can include stops; an ordinary run is not a race result, VDOT measurement or evidence to increase training pace.",
                "Same-day runs with different times remain separate and need your review. Ambiguous provider overlaps and Suunto sessions without a confirmed past stop remain visible and are excluded from distance totals and longest-run estimates. No splits, GPS, running HR trace or Suunto training load is available.",
            ],
        }


def interval(row):
    try:
        a, b = datetime.fromisoformat(row['start_datetime']), datetime.fromisoformat(row['end_datetime'])
        if a.tzinfo is None or b.tzinfo is None or b <= a:
            return None
        return a, b
    except (KeyError, TypeError, ValueError):
        return None


def reconcile(oura, suunto):
    """Only one-to-one near-identical timed sessions are suppressed; keep ambiguity."""
    oura = [{**r, 'source': 'oura', 'duration_basis': 'elapsed',
             'elapsed_duration_seconds': r['duration_seconds'], 'provenance': ['oura'],
             'source_fallback': True, 'reconciliation': 'oura_only'} for r in oura]
    suunto = [{**r, 'provenance': ['suunto'], 'source_fallback': False,
               'reconciliation': 'suunto_only'} for r in suunto]
    overlaps, matches = {}, {}
    for i, o in enumerate(oura):
        oi = interval(o)
        for j, s in enumerate(suunto):
            si = interval(s)
            if not oi or not si or min(oi[1], si[1]) <= max(oi[0], si[0]):
                continue
            overlaps.setdefault(i, []).append(j)
            matches.setdefault(j, []).append(i)
    removed = set()
    for i, js in overlaps.items():
        for j in js:
            oi, si = interval(oura[i]), interval(suunto[j])
            if len(js) == 1 and len(matches[j]) == 1 and all(abs((a-b).total_seconds()) <= 120 for a, b in zip(oi, si)):
                removed.add(i)
                suunto[j]['provenance'] = ['suunto', 'oura']
                suunto[j]['reconciliation'] = 'matched_suunto_preferred'
                suunto[j]['matched_oura_record_id'] = oura[i]['record_id']
            else:
                oura[i]['reconciliation'] = suunto[j]['reconciliation'] = 'ambiguous_overlap'
    rows = [*suunto, *(r for i, r in enumerate(oura) if i not in removed)]
    return rows, len(removed), sum(r['reconciliation'] == 'ambiguous_overlap' for r in rows)
