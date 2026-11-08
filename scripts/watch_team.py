#!/usr/bin/env python3
"""Read-only development activity, with no wearable data or credential access."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def collect_status(root=ROOT):
    repository = json.loads((root / "config/team.json").read_text())["repository"]
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Invalid repository")
    result = {"repository": repository, "tickets": [],
              "roles": ["orchestrator", "backend_developer", "frontend_developer", "qa"]}
    for file in sorted((root / ".team/tickets").glob("PC-*.json")):
        if file.is_symlink() or file.stat().st_size > 64000:
            continue
        try:
            state = json.loads(file.read_text())
        except (ValueError, OSError):
            continue
        if not isinstance(state, dict):
            continue
        key = state.get("id", "")
        if not isinstance(key, str) or not re.fullmatch(r"PC-\d{4,}", key):
            continue
        if file.stem != key or any(not isinstance(state.get(k), str) for k in ("title", "status", "role")):
            continue
        if not isinstance(state.get("attempts"), int) or isinstance(state["attempts"], bool) or state["attempts"] < 0:
            continue
        usage = state.get("usage") or {}
        if not isinstance(usage, dict) or any(
            not isinstance(v, dict) or any(not isinstance(v.get(k, 0), int) or isinstance(v.get(k, 0), bool) or v.get(k, 0) < 0
                                           for k in ("input_tokens", "output_tokens")) for v in usage.values()
        ):
            continue
        row = {k: state[k] for k in ("id", "title", "status", "role", "attempts")}
        row["url"] = f"https://github.com/{repository}/issues/{int(key[3:])}"
        pr = str(state.get("pr", ""))
        row["pr"] = pr if pr.startswith(f"https://github.com/{repository}/pull/") else ""
        row["files"] = []
        workspace = root / ".team/workspaces" / key
        if row["status"] in {"ready", "developing", "checks", "qa"} and workspace.is_dir() and not workspace.is_symlink():
            try:
                status = subprocess.run(["git", "status", "--short"], cwd=workspace,
                                        capture_output=True, text=True, timeout=3, check=True)
                row["files"] = status.stdout.splitlines()[:30]
            except (OSError, subprocess.SubprocessError):
                pass
        # Only synthetic deterministic test output is eligible for this view.
        log = root / ".team/reports" / (key + "-checks.log")
        row["test_output"] = []
        if log.is_file() and not log.is_symlink():
            with log.open("rb") as handle:
                handle.seek(max(0, log.stat().st_size - 5000))
                text = handle.read().decode("utf-8", errors="replace")
            row["test_output"] = text.replace(str(root), "<project>").splitlines()[-12:]
            if row["status"] == "developing" and log.stat().st_mtime >= file.stat().st_mtime:
                row["status"] = "checks"
        row["tokens"] = sum(v.get("input_tokens", 0) + v.get("output_tokens", 0)
                            for v in usage.values() if isinstance(v, dict)) if usage else None
        result["tickets"].append(row)
    return result


PAGE = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Personal Coach · Development team</title><style>
:root{font-family:system-ui;color:#e5ebf7;background:#111823}body{max-width:1100px;margin:36px auto;padding:0 24px}h1{font-size:30px;margin-bottom:8px}p{color:#a9b8d0;line-height:1.5}a{color:#89c6ff}nav{display:flex;gap:20px;margin:22px 0;flex-wrap:wrap}.roles{display:flex;gap:10px;flex-wrap:wrap}.roles span{border:1px solid #3b4c66;padding:10px 14px;border-radius:9px}.ticket{background:#1b2738;border:1px solid #324866;padding:22px;border-radius:14px;margin-top:20px}.top{display:flex;justify-content:space-between;gap:20px}h2{font-size:21px;margin:8px 0}.stage{color:#96efc0;font-weight:650}.details{display:flex;gap:22px;color:#b7c5d9;font-size:14px;flex-wrap:wrap}pre{background:#101721;overflow:auto;padding:14px;border-radius:8px;line-height:1.5;font-size:12px;max-height:240px}.muted{font-size:13px;color:#9baac0}.flow{margin:18px 0;color:#c2cfe1}#error{color:#ffbdad}
</style><h1>Personal Coach development team</h1><p>Live local activity · refreshes every 2 seconds · no AI calls for monitoring</p>
<nav><a id="issues" target="_blank" rel="noreferrer">All tickets</a><a id="prs" target="_blank" rel="noreferrer">Pull requests and code changes</a><a id="actions" target="_blank" rel="noreferrer">GitHub tests</a></nav>
<div class="roles" id="roles"></div><p class="flow">Ticket → Developer → Local tests → QA → Private PR → GitHub tests → Guarded merge</p>
<div id="error" role="status"></div><main id="tickets"></main><p class="muted" id="updated"></p>
<script>
const node=(tag,text,cls)=>{const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;};
async function update(){try{const response=await fetch('/api/status',{cache:'no-store'});if(!response.ok)throw Error();const d=await response.json();
 document.querySelector('#roles').replaceChildren(...d.roles.map(r=>node('span',r.replaceAll('_',' '))));
 for(const [id,path] of [['issues','issues?q=is%3Aissue'],['prs','pulls?q=is%3Apr'],['actions','actions']])document.getElementById(id).href='https://github.com/'+d.repository+'/'+path;
 const cards=d.tickets.map(t=>{const c=node('article','','ticket');const top=node('div','','top');const link=node('a',t.id);link.href=t.url;link.target='_blank';link.rel='noreferrer';top.append(link,node('span',t.status.replaceAll('_',' '),'stage'));c.append(top,node('h2',t.title));const details=node('div','','details');details.append(node('span','Role: '+t.role.replaceAll('_',' ')),node('span','Attempt: '+t.attempts),node('span','Reported tokens: '+(t.tokens==null?'pending':t.tokens.toLocaleString())));c.append(details);
 if(t.files.length)c.append(node('h3','Files changing now'),node('pre',t.files.join('\n')));else c.append(node('p','No uncommitted file changes reported yet.','muted'));
 if(t.test_output.length)c.append(node('h3','Latest local test output'),node('pre',t.test_output.join('\n')));
 if(t.pr){const a=node('a','Open pull request and diff');a.href=t.pr;a.target='_blank';a.rel='noreferrer';c.append(a);}return c;});
 document.getElementById('tickets').replaceChildren(...(cards.length?cards:[node('p','No local ticket runs yet. Open All tickets for the backlog.')]));document.getElementById('updated').textContent='Updated '+new Date().toLocaleTimeString();document.getElementById('error').textContent='';
 }catch(e){document.getElementById('error').textContent='Activity server is unavailable. Start scripts/watch_team.py.';}finally{setTimeout(update,2000);}}
update();
</script></html>'''


def handler(root):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            expected = {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}
            if self.headers.get("Host") not in expected:
                self.send_error(403)
                return
            if self.path == "/":
                payload, content_type = PAGE.encode(), "text/html; charset=utf-8"
            elif self.path == "/api/status":
                value = collect_status(root)
                payload, content_type = json.dumps(value).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass
    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(ROOT))
    print(f"Development activity: http://localhost:{server.server_port} (read-only)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
