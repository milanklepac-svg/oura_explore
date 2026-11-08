"""Run existing application tests without importing the separately tested MCP SDK."""
import os
import sys
import unittest
from pathlib import Path

os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['OWNER_ID'] = 'local-owner'
loader = unittest.TestLoader()
suite = unittest.TestSuite()
sys.path.insert(0, '/unit-tests')
for path in sorted(Path('/unit-tests').glob('test_*.py')):
    if path.name != 'test_mcp_contract.py':
        suite.addTests(loader.loadTestsFromName(path.stem))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
