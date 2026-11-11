"""harnessmonitor: start, stop and point the Harness Monitor. Standard library only.

    harnessmonitor start [--project DIR] [--no-open]   start (or re-point) the monitor and open it
    harnessmonitor stop                                stop it
    harnessmonitor status                              is it running, and what is it watching
    harnessmonitor hooks add|remove [--project DIR]    Claude Code activity hooks for one project
                                                       (not needed with the Claude Code plugin)
"""

import argparse
import secrets
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__

PKG_PARENT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("HARNESS_MONITOR_PORT", "4849"))
URL = f"http://127.0.0.1:{PORT}"
HOME = Path(os.environ.get("HARNESS_MONITOR_HOME", Path.home() / ".harness-monitor"))
PIDFILE = HOME / "monitor.pid"
LOGFILE = HOME / "monitor.log"
TOKENFILE = HOME / "token"
MARK = f"127.0.0.1:{PORT}/api/hook"
HOOK_CMD = ("[ -r \"${HARNESS_MONITOR_HOME:-$HOME/.harness-monitor}/token\" ] && curl -s -m 2 -X POST "
            "-H 'Content-Type: application/json' -H 'X-Agent: claude-code' "
            "-H \"X-Monitor-Token: $(cat \"${HARNESS_MONITOR_HOME:-$HOME/.harness-monitor}/token\")\" "
            f"--data-binary @- http://{MARK} >/dev/null 2>&1 || true")
EVENTS = {"UserPromptSubmit": None, "PreToolUse": "*", "PostToolUse": "*", "Stop": None, "SubagentStop": None}


def token() -> str:
    try:
        return TOKENFILE.read_text().strip()
    except OSError:
        return ""


def new_token() -> str:
    """A fresh secret readable only by this user."""
    HOME.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(HOME, 0o700)
    except OSError:
        pass
    value = secrets.token_urlsafe(24)
    TOKENFILE.unlink(missing_ok=True)
    fd = os.open(TOKENFILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)
    return value


def _request(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", "X-Monitor-Token": token()})
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.loads(r.read() or b"{}")


def state():
    try:
        return _request("/api/state")
    except Exception:
        return None


def start_server(project: Path) -> bool:
    HOME.mkdir(parents=True, exist_ok=True)
    log = open(LOGFILE, "a")
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    env = {**os.environ, "HARNESS_MONITOR_TOKEN": new_token(), "PYTHONPATH": str(PKG_PARENT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = subprocess.Popen([sys.executable, "-m", "harness_monitor.server", "--project", str(project)],
                            stdout=log, stderr=log, stdin=subprocess.DEVNULL, env=env, **kw)
    PIDFILE.write_text(str(proc.pid))
    for _ in range(50):
        if state() is not None:
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.1)
    return False


def stop_server() -> bool:
    if not PIDFILE.is_file():
        return False
    try:
        os.kill(int(PIDFILE.read_text()), signal.SIGTERM)
    except (ProcessLookupError, ValueError, OSError):
        pass
    PIDFILE.unlink()
    TOKENFILE.unlink(missing_ok=True)
    return True


def _ours(entry):
    return any(MARK in h.get("command", "") for h in entry.get("hooks", []) if isinstance(h, dict))


def set_hooks(project: Path, add: bool) -> str:
    """Add or remove Claude Code activity hooks in <project>/.claude/settings.local.json."""
    path = project / ".claude" / "settings.local.json"
    settings = {}
    if path.is_file():
        try:
            settings = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError:
            return f"Left {path} alone: it is not valid JSON."
        shutil.copy2(path, path.with_suffix(".json.bak-harnessmonitor"))
    hooks = settings.setdefault("hooks", {})
    changed = False
    for event, matcher in EVENTS.items():
        entries = [e for e in hooks.get(event, []) if not _ours(e)]
        if add:
            entry = {"hooks": [{"type": "command", "command": HOOK_CMD}]}
            if matcher:
                entry = {"matcher": matcher, **entry}
            entries.append(entry)
        if entries != hooks.get(event, []):
            changed = True
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2) + "\n")
        return f"{'Added' if add else 'Removed'} activity hooks in {path}. They apply from the next session."
    return "Hooks already up to date."


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harnessmonitor", description="Live diagram of an AI agent harness as it gets built.")
    ap.add_argument("--version", action="version", version=f"harnessmonitor {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("start", help="start the monitor (or point it at another project) and open it")
    s.add_argument("--project", default=os.getcwd())
    s.add_argument("--no-open", action="store_true", help="don't open a browser")
    sub.add_parser("stop", help="stop the monitor")
    sub.add_parser("status", help="show whether it runs and what it watches")
    h = sub.add_parser("hooks", help="add or remove Claude Code activity hooks for one project")
    h.add_argument("action", choices=["add", "remove"])
    h.add_argument("--project", default=os.getcwd())
    a = ap.parse_args(argv)

    if a.cmd == "stop":
        print("Harness Monitor stopped." if stop_server() else "Harness Monitor was not running.")
    elif a.cmd == "status":
        st = state()
        if st is None:
            print("Harness Monitor is not running. Start it with: harnessmonitor start")
        else:
            print(f"Running at {URL}, watching {(st.get('state') or {}).get('project', '?')}")
    elif a.cmd == "hooks":
        print(set_hooks(Path(a.project).expanduser().resolve(), add=a.action == "add"))
    elif a.cmd == "start":
        project = Path(a.project).expanduser().resolve()
        if not project.is_dir():
            sys.exit(f"Not a folder: {project}")
        if state() is not None:
            _request("/api/project", {"path": str(project)})
            print(f"Harness Monitor now watching {project}")
        elif start_server(project):
            print(f"Harness Monitor started, watching {project}")
        else:
            tail = LOGFILE.read_text(errors="replace").splitlines()[-8:] if LOGFILE.is_file() else []
            sys.exit("The monitor did not start.\n" + "\n".join(tail) + f"\nFull log: {LOGFILE}")
        link = f"{URL}/?t={token()}"
        print(f"Open: {link}")
        if not a.no_open:
            webbrowser.open(link)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
