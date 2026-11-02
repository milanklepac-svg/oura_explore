import os
os.environ.setdefault('DATABASE_URL', 'sqlite://')
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from mcp import Client
from app.db import Base
from app.mcp_server import server
from app.services.oura_queries import OuraQueries

class ContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_discovery_and_calls(self):
        engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(engine)
        try:
            with patch('app.mcp_server.queries',OuraQueries(engine,'test-owner')):
                async with Client(server,raise_exceptions=True) as client:
                    tools=(await client.list_tools()).tools
                    self.assertEqual(len(tools),6)
                    self.assertTrue(all(t.annotations.read_only_hint for t in tools))
                    result=await client.call_tool('get_daily_metrics',{'start_date':'2026-10-01','end_date':'2026-10-02'})
                    self.assertFalse(result.is_error)
                    self.assertEqual(len(result.structured_content['days']),2)
                    self.assertIsNone(result.structured_content['days'][0]['steps'])
                    guidance=await client.call_tool('get_training_guidance',{'day':'2026-10-01','planned_session':'rest'})
                    self.assertFalse(guidance.is_error)
                    self.assertEqual(guidance.structured_content['recommendation']['action'],'keep_rest')
                    bad=await client.call_tool('get_daily_metrics',{'start_date':'not-a-date','end_date':'2026-10-02'})
                    self.assertTrue(bad.is_error)
        finally:
            engine.dispose()
