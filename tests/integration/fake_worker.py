"""Real worker/transactions with an in-memory, credential-free provider."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch
from app import worker


class SyntheticOura:
    def close(self):
        pass

    def records(self, kind, start, end):
        today = datetime.now(ZoneInfo('Europe/Belgrade')).date()
        for offset in range(15):
            day = str(today - timedelta(days=offset))
            if not start <= day <= end:
                continue
            payload = {'id': f'synthetic-{kind}-{day}', 'day': day}
            if kind == 'daily_sleep':
                payload.update(score=81)
            elif kind == 'sleep':
                payload.update(type='long_sleep', total_sleep_duration=28800,
                               average_hrv=55, lowest_heart_rate=48,
                               bedtime_end=f'{day}T06:00:00+01:00')
            elif kind == 'daily_readiness':
                payload.update(score=84)
            elif kind == 'daily_activity':
                payload.update(steps=9000)
            elif kind == 'daily_stress':
                payload.update(stress_high=1800, recovery_high=3600)
            elif kind == 'workout':
                if offset == 0:
                    # Stable fixture for idempotent re-sync; early timing keeps sleep order explicit.
                    payload.update(activity='walking', intensity='low',
                                   start_datetime=f'{day}T00:00:00+01:00',
                                   end_datetime=f'{day}T00:00:01+01:00')
                    yield payload
                    continue
                if offset != 1:
                    continue
                payload.update(activity='running', label='Synthetic easy run',
                               intensity='easy', distance=5000,
                               start_datetime=f'{day}T08:00:00+01:00',
                               end_datetime=f'{day}T08:30:00+01:00')
            yield payload


if __name__ == '__main__':
    # Patching only the provider preserves the actual queue, worker loop,
    # PostgreSQL advisory lock, upserts and post-sync plan review.
    with patch.object(worker, 'OuraClient', SyntheticOura):
        worker.main()
