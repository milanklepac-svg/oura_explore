import base64,hashlib,hmac,json,os,secrets,urllib.parse,urllib.request
from pathlib import Path
from http.server import HTTPServer,BaseHTTPRequestHandler
ROOT=Path(__file__).resolve().parent.parent
cfg=json.loads((ROOT/'.secrets/oura.json').read_text())
state=secrets.token_urlsafe(32)
verifier=secrets.token_urlsafe(64)
challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
params={'client_id':cfg['client_id'],'redirect_uri':cfg['redirect_uri'],'response_type':'code','scope':'daily heartrate tag workout stress','state':state,'code_challenge':challenge,'code_challenge_method':'S256'}
url='https://cloud.ouraring.com/oauth/authorize?'+urllib.parse.urlencode(params)
(ROOT/'work/oura-auth-url.txt').write_text(url)
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  parsed=urllib.parse.urlsplit(self.path)
  if parsed.path!='/oauth/oura/callback':
   self.send_error(404);return
  q=urllib.parse.parse_qs(parsed.query)
  if not hmac.compare_digest(q.get('state',[''])[0],state):
   self.send_error(400,'Invalid authorization state');return
  if 'error' in q or not q.get('code'):
   self.send_error(400,'Authorization was not granted');return
  try:
   data=urllib.parse.urlencode({'grant_type':'authorization_code','code':q['code'][0],'redirect_uri':cfg['redirect_uri'],'client_id':cfg['client_id'],'client_secret':cfg['client_secret'],'code_verifier':verifier}).encode()
   req=urllib.request.Request('https://api.ouraring.com/oauth/token',data=data,headers={'Content-Type':'application/x-www-form-urlencoded'})
   with urllib.request.urlopen(req,timeout=30) as r: tokens=json.load(r)
   if not tokens.get('access_token'): raise ValueError('Missing token')
   p=ROOT/'.secrets/oura-tokens.json'
   fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
   with os.fdopen(fd,'w') as f: json.dump(tokens,f)
   os.chmod(p,0o600)
   self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
   self.wfile.write(b'<h1>Oura connected</h1><p>Authorization tokens were saved privately on your computer. You can close this tab.</p>')
   print('Oura authorization succeeded; tokens saved privately.',flush=True)
   self.server.connected=True
  except Exception as e:
   self.send_error(502,'Token exchange failed; return to the chat')
   print('Token exchange failed: '+type(e).__name__,flush=True)
server=HTTPServer(('127.0.0.1',8765),Handler)
server.connected=False
server.timeout=1
print('Local callback ready on port 8765.',flush=True)
while not server.connected: server.handle_request()
server.server_close()
