import os
os.environ.setdefault('DATABASE_URL','sqlite://')
from datetime import date
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api import app
from app.services.training import TrainingService

class ProfileApiTests(unittest.TestCase):
    def test_preferences_persist_and_checkin_does_not_write(self):
        engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        try:
            with patch('app.api.engine',engine),patch('app.api.Session',sessionmaker(engine)),TestClient(app) as client:
                profile=client.get('/api/training/profile').json()
                profile.update(goal='10k',available_days=['mon','wed','fri'],current_weekly_km=24)
                profile['rules']['hrv_drop_percent']=30
                self.assertEqual(client.put('/api/training/profile',json=profile).status_code,200)
                saved=client.get('/api/training/profile').json()
                self.assertEqual(saved,profile)
                self.assertEqual(TrainingService(engine,'local-owner').profile().rules.hrv_drop_percent,30)
                result=client.post('/api/training/guidance',json={'day':str(date.today()),'planned_session':'easy'})
                self.assertEqual(result.status_code,200)
                self.assertEqual(client.get('/api/training/profile').json(),saved)
                denied=client.put('/api/training/profile',json=profile,headers={'Origin':'https://untrusted.example','Sec-Fetch-Site':'cross-site'})
                self.assertEqual(denied.status_code,403)
                profile['current_weekly_km']=-1
                self.assertEqual(client.put('/api/training/profile',json=profile).status_code,422)
        finally:engine.dispose()
