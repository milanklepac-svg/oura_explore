"""Exercise real Docker stdio MCP without logging private tool payloads."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import selectors
import subprocess

ROOT=Path(__file__).resolve().parent.parent
process=subprocess.Popen([str(ROOT/'scripts/run_mcp.sh')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                         text=True,bufsize=1,cwd=ROOT)
selector=selectors.DefaultSelector()
selector.register(process.stdout,selectors.EVENT_READ)

def send(message):
    process.stdin.write(json.dumps(message)+'\n')
    process.stdin.flush()

def request(number,method,params):
    send({'jsonrpc':'2.0','id':number,'method':method,'params':params})
    while True:
        if not selector.select(45):raise RuntimeError('MCP stdio response timed out')
        line=process.stdout.readline()
        if not line:raise RuntimeError('MCP server exited before responding')
        result=json.loads(line)
        if result.get('id')!=number:continue
        if 'error' in result:raise RuntimeError('MCP protocol returned an error')
        return result['result']

try:
    result=request(1,'initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'personal-coach-smoke','version':'1.0'}})
    assert 'tools' in result['capabilities']
    send({'jsonrpc':'2.0','method':'notifications/initialized'})
    tools=request(2,'tools/list',{})['tools']
    expected={'get_data_status','get_daily_metrics','get_recovery_context','get_workouts','get_training_summary','get_training_guidance'}
    assert {tool['name'] for tool in tools}==expected
    assert all(t['annotations']['readOnlyHint'] for t in tools)
    end=datetime.now(ZoneInfo('Europe/Belgrade')).date()
    start=end-timedelta(days=6)
    for index,name in enumerate(sorted(expected),3):
        args={} if name=='get_data_status' else {'day':str(end)} if name in ('get_recovery_context','get_training_guidance') else {'start_date':str(start),'end_date':str(end)}
        result=request(index,'tools/call',{'name':name,'arguments':args})
        assert not result.get('isError'),name+' returned a tool error'
        assert result.get('structuredContent') is not None,name+' did not return structured content; keys='+str(list(result))
        print(name+': passed (payload kept private)')
    invalid=request(99,'tools/call',{'name':'get_daily_metrics','arguments':{'start_date':'2026-10-30','end_date':'2026-10-01'}})
    assert invalid.get('isError'),'Invalid date range should be rejected'
    print('Stdio handshake, discovery, all 6 calls and invalid-range rejection passed.')
finally:
    selector.close()
    process.stdin.close()
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait()
