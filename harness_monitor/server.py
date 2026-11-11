"""Harness Monitor: watches a project and draws its harness parts live. Standard library only.

    python3 -m harness_monitor.server --project /path/to/project

Rescans when files change (polling once a second) and when a Claude Code hook reports an edit.
Every change is pushed to the browser, with what is new. Binds to 127.0.0.1 only.
"""

import argparse
import hmac
import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .analyzer import analyze, signature

WEB = Path(__file__).resolve().parent / "web"
PORT = int(os.environ.get("HARNESS_MONITOR_PORT", "4849"))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
# Per-run secret from `harnessmonitor start` (env, never argv). Every /api call and the page need it,
# so other local users and processes can neither read the activity nor re-point the monitor.
TOKEN = os.environ.get("HARNESS_MONITOR_TOKEN", "")
COOKIE = f"hm_{PORT}"
LOCKED = """<!doctype html><meta charset="utf-8"><title>Harness Monitor</title>
<body style="font:18px/1.5 sans-serif;max-width:36em;margin:4em auto;padding:0 16px">
<h1>Open the link from the start command</h1><p>For safety this page needs the private link that
<code>harnessmonitor start</code> printed. Run it again to get a fresh one.</p>"""
TYPES = {".svg": "image/svg+xml", ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


class Monitor:
    def __init__(self, project: Path):
        self.lock = threading.Lock()
        self.clients = set()
        self.activity = []
        self.set_project(project)

    # ------------------------------------------------------------ state
    def set_project(self, project: Path):
        with self.lock:
            self.project = project.resolve()
            self.sig = None
            self.state = None
            self.first_seen = {}
            self.activity = []
        self.rescan(reason="Started watching")

    def rescan(self, reason="Files changed", force=False):
        sig = signature(self.project)
        if not force and sig == self.sig:
            return
        new = analyze(self.project)
        now = time.time()
        with self.lock:
            old = self.state
            self.sig = sig
            added, removed, grew = [], [], []
            was = {x["id"]: x for x in old["parts"]} if old else {}
            now_ids = {x["id"] for x in new["parts"]}
            for x in new["parts"]:
                if x["id"] not in self.first_seen:
                    self.first_seen[x["id"]] = now
                if old and x["id"] not in was:
                    added.append(x["id"])
            for pid, x in was.items():
                if pid not in now_ids:
                    removed.append({"id": pid, "label": x["label"]})
                    self.first_seen.pop(pid, None)
            new["first_seen"] = self.first_seen
            self.state = new
        self.broadcast({"type": "map", "state": new, "added": added, "removed": removed,
                        "grew": grew, "reason": reason})

    def note(self, item):
        with self.lock:
            self.activity.append(item)
            self.activity = self.activity[-200:]
        self.broadcast({"type": "activity", "item": item})

    # ------------------------------------------------------------ fan-out
    def subscribe(self):
        q = queue.Queue(maxsize=500)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def broadcast(self, event):
        line = json.dumps(event, default=str)
        with self.lock:
            for q in list(self.clients):
                try:
                    q.put_nowait(line)
                except queue.Full:
                    pass

    def watch(self):
        while True:
            time.sleep(1.0)
            try:
                self.rescan()
            except Exception as exc:  # keep watching whatever happens
                self.note({"kind": "error", "text": f"Scan failed: {exc}"})


MON: Monitor = None


def _short(value, n=160):
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= n else text[:n] + "…"


AGENTS = {"claude-code": "Claude", "codex": "Codex"}


def in_project(p, project: Path) -> bool:
    """Hooks may be installed for every session; keep only events from the watched project."""
    cwd = p.get("cwd")
    if not cwd:
        return True
    try:
        c = Path(str(cwd)).resolve()
    except (OSError, ValueError):
        return False
    return c == project or project in c.parents or c in project.parents


def hook_item(p, agent="claude-code"):
    tool = p.get("tool_name", "")
    ti = p.get("tool_input") or {}
    detail = ""
    if isinstance(ti, dict):
        for key in ("file_path", "command", "pattern", "path", "url", "description"):
            if ti.get(key):
                detail = _short(ti[key])
                break
    if p.get("prompt"):
        detail = _short(p["prompt"], 220)
    return {"kind": "claude", "agent": AGENTS.get(agent, "Agent"), "event": p.get("hook_event_name", ""), "tool": tool, "detail": detail, "t": time.time()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        if not TOKEN:
            return True
        given = self.headers.get("X-Monitor-Token", "")
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                given = given or v
        return hmac.compare_digest(given.encode(), TOKEN.encode())

    def do_GET(self):
        if self.headers.get("Host", "") not in ALLOWED_HOSTS:
            return self._send(403, {"error": f"Open http://127.0.0.1:{PORT}"})
        path, _, query = self.path.partition("?")
        if path == "/" and query.startswith("t="):
            if not hmac.compare_digest(query[2:].encode(), TOKEN.encode()):
                return self._send(401, LOCKED.encode(), "text/html; charset=utf-8")
            self.send_response(303)
            self.send_header("Set-Cookie", f"{COOKIE}={TOKEN}; Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self._authed():
            if path.startswith("/api/"):
                return self._send(401, {"error": "Missing token"})
            if path in ("/", "/index.html"):
                return self._send(401, LOCKED.encode(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._send(200, {"state": MON.state, "activity": MON.activity})
        if path == "/api/events":
            return self._events()
        target = (WEB / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
        if WEB not in target.parents or not target.is_file():
            return self._send(404, {"error": "Not found"})
        return self._send(200, target.read_bytes(), TYPES.get(target.suffix, "application/octet-stream"))

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = MON.subscribe()
        try:
            self.wfile.write(f"data: {json.dumps({'type': 'map', 'state': MON.state, 'added': [], 'removed': [], 'grew': [], 'reason': 'Connected'}, default=str)}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    self.wfile.write(f"data: {q.get(timeout=15)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            MON.unsubscribe(q)

    def do_POST(self):
        if self.headers.get("Host", "") not in ALLOWED_HOSTS:
            return self._send(403, {"error": "Wrong host"})
        # JSON content type forces a CORS preflight that is never approved: web pages can't post here.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(415, {"error": "Send JSON"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 2_000_000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "Bad JSON"})
        if not self._authed():
            return self._send(401, {"error": "Missing token"})
        path = self.path.split("?")[0]
        if path == "/api/hook":
            if isinstance(body, dict) and in_project(body, MON.project):
                agent = self.headers.get("X-Agent", "claude-code")
                item = hook_item(body, agent)
                MON.note(item)
                if body.get("hook_event_name") == "PostToolUse":
                    MON.rescan(reason=f"{item['agent']} used {body.get('tool_name', 'a tool')}")
            return self._send(200, {})
        if path == "/api/project":
            p = Path(str(body.get("path", ""))).expanduser()
            if not p.is_dir():
                return self._send(400, {"error": "Not a folder"})
            MON.set_project(p)
            return self._send(200, {"project": str(p.resolve())})
        if path == "/api/rescan":
            MON.rescan(reason="Rescanned", force=True)
            return self._send(200, {})
        return self._send(404, {"error": "Not found"})


def main():
    global MON
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    args = ap.parse_args()
    MON = Monitor(Path(args.project))
    threading.Thread(target=MON.watch, daemon=True).start()
    ThreadingHTTPServer.daemon_threads = True
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Harness Monitor: http://127.0.0.1:{PORT}  watching {MON.project}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
