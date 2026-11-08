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
                    self.assertEqual(len(tools),10)
                    self.assertEqual([t.name for t in tools if not t.annotations.read_only_hint],['adapt_training_plan'])
                    context=await client.call_tool('get_running_context',{})
                    self.assertEqual(context.structured_content['coverage'],'unconfirmed')
                    self.assertEqual(context.structured_content['metadata']['source'],'reconciled_running_archive')
                    self.assertIn('suunto', context.structured_content['metadata']['providers'])
                    walking=await client.call_tool('get_walking_context',{})
                    self.assertFalse(walking.is_error)
                    self.assertEqual(walking.structured_content['walks'],[])
                    self.assertFalse(walking.structured_content['summary']['previous_day_additional_load'])
                    dated=await client.call_tool('get_walking_context',{'day':'2026-10-01'})
                    self.assertEqual(dated.structured_content['period']['end'],'2026-10-01')
                    invalid=await client.call_tool('get_walking_context',{'day':'invalid'})
                    self.assertTrue(invalid.is_error)
                    plan=await client.call_tool('get_race_plan',{})
                    self.assertIsNone(plan.structured_content['plan'])
                    adapted=await client.call_tool('adapt_training_plan',{'expected_revision':1})
                    self.assertEqual(adapted.structured_content['status'],'no_plan')
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
