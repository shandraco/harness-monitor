"""Run with: python3 -m unittest discover tests"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness_monitor.analyzer import analyze  # noqa: E402
from harness_monitor.server import in_project  # noqa: E402

PORT = "48999"
BASE = f"http://127.0.0.1:{PORT}"
AGENT = '''import anthropic
client = anthropic.Anthropic()
def run(task):
    messages = [{"role": "user", "content": task}]
    while True:
        r = client.messages.create(model="claude-sonnet-5-5", max_tokens=1000, messages=messages, tools=TOOLS)
        if r.stop_reason != "tool_use":
            return r
'''


TOKEN = {"value": ""}


def get(path, token=True):
    req = urllib.request.Request(BASE + path, headers={"X-Monitor-Token": TOKEN["value"]} if token else {})
    return urllib.request.urlopen(req, timeout=3)


def post(path, body, ctype="application/json", headers=None, token=True):
    hdrs = {"Content-Type": ctype, **({"X-Monitor-Token": TOKEN["value"]} if token else {}), **(headers or {})}
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST", headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


class AnalyzerTest(unittest.TestCase):
    def test_finds_model_and_loop(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "agent.py").write_text(AGENT)
            feats = analyze(Path(d))["features"]
            self.assertTrue(feats["model"]["on"])
            self.assertTrue(feats["loop"]["on"])

    def test_ignores_comments(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "notes.py").write_text('# client.messages.create(model="claude-sonnet-5-5")\n')
            self.assertFalse(analyze(Path(d))["features"]["model"]["on"])

    def test_bad_map_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, ".harness").mkdir()
            Path(d, ".harness", "map.json").write_text("{not json")
            self.assertIn("features", analyze(Path(d)))


class ProjectFilterTest(unittest.TestCase):
    def test_filter(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d).resolve()
            (p / "sub").mkdir()
            self.assertTrue(in_project({"cwd": str(p / "sub")}, p))
            self.assertTrue(in_project({}, p))
            self.assertFalse(in_project({"cwd": "/somewhere/else"}, p))


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = tempfile.TemporaryDirectory()
        cls.env = {**os.environ, "HARNESS_MONITOR_PORT": PORT, "HARNESS_MONITOR_HOME": cls.home.name}
        out = subprocess.run([sys.executable, str(ROOT / "bin" / "harnessmonitor"), "start", "--project", cls.tmp.name,
                              "--no-open"], env=cls.env, capture_output=True, text=True, timeout=20)
        assert out.returncode == 0, out.stdout + out.stderr
        tf = Path(cls.home.name, "token")
        TOKEN["value"] = tf.read_text().strip()
        assert oct(tf.stat().st_mode & 0o777) == "0o600"
        assert TOKEN["value"] in out.stdout  # the printed link carries it

    @classmethod
    def tearDownClass(cls):
        subprocess.run([sys.executable, str(ROOT / "bin" / "harnessmonitor"), "stop"], env=cls.env, timeout=10)
        cls.tmp.cleanup()
        cls.home.cleanup()

    def test_page_and_state(self):
        with get("/") as r:
            self.assertIn(b"Harness Monitor", r.read())
        with get("/api/state") as r:
            self.assertIn("state", json.loads(r.read()))

    def test_rejects_non_json_post(self):
        self.assertEqual(post("/api/hook", {}, ctype="text/plain"), 415)

    def test_requires_token(self):
        self.assertEqual(post("/api/hook", {}, token=False), 401)
        self.assertEqual(post("/api/project", {"path": "/"}, token=False), 401)
        with self.assertRaises(urllib.error.HTTPError) as e:
            get("/api/state", token=False)
        self.assertEqual(e.exception.code, 401)
        e.exception.close()
        self.assertEqual(post("/api/hook", {}, headers={"X-Monitor-Token": "wrong"}, token=False), 401)

    def test_browser_link_sets_cookie(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        with self.assertRaises(urllib.error.HTTPError) as e:
            opener.open(BASE + "/?t=" + TOKEN["value"], timeout=3)
        self.assertEqual(e.exception.code, 303)
        self.assertIn("HttpOnly", e.exception.headers.get("Set-Cookie", ""))
        e.exception.close()
        cookie = e.exception.headers["Set-Cookie"].split(";")[0]
        with urllib.request.urlopen(urllib.request.Request(BASE + "/api/state", headers={"Cookie": cookie}), timeout=3) as r:
            self.assertEqual(r.status, 200)

    def test_rejects_wrong_host(self):
        self.assertEqual(post("/api/hook", {}, headers={"Host": "evil.example:80"}), 403)

    def test_picks_up_new_part(self):
        Path(self.tmp.name, "agent.py").write_text(AGENT)
        for _ in range(40):
            with get("/api/state") as r:
                if json.loads(r.read())["state"]["features"]["model"]["on"]:
                    return
            time.sleep(0.25)
        self.fail("model call was not picked up")

    def test_hook_from_other_project_is_ignored(self):
        self.assertEqual(post("/api/hook", {"hook_event_name": "UserPromptSubmit", "prompt": "elsewhere",
                                            "cwd": "/not/this/project"}), 200)
        self.assertEqual(post("/api/hook", {"hook_event_name": "UserPromptSubmit", "prompt": "here",
                                            "cwd": self.tmp.name}, headers={"X-Agent": "codex"}), 200)
        with get("/api/state") as r:
            acts = json.loads(r.read())["activity"]
        details = [a.get("detail") for a in acts]
        self.assertIn("here", details)
        self.assertNotIn("elsewhere", details)
        self.assertEqual([a for a in acts if a.get("detail") == "here"][0]["agent"], "Codex")


if __name__ == "__main__":
    unittest.main()
