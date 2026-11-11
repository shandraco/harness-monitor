"""Work out which harness parts a project has, and where each one lives.

Two sources, merged:
  1. .harness/map.json, written by the coding agent as it builds (the /harnessmonitor skill asks it to).
     This names parts precisely, in any language.
  2. A code scan with simple patterns. It needs no cooperation and catches what the map misses.
"""

import json
import re
from pathlib import Path

# The parts of a harness, in the order a workshop builds them. Plain-language meanings for the screen.
FEATURES = {
    "problem":       ("Problem",        "The job the agent is given."),
    "model":         ("Model",          "The LLM call: text in, text out."),
    "context":       ("Context",        "Everything sent to the model each call: instructions, history, results."),
    "loop":          ("Loop",           "Calls the model again and again until it says it is done."),
    "tools":         ("Tools",          "Actions the model can ask for. The harness runs them."),
    "guardrails":    ("Guardrails",     "Rules and approvals that stop risky actions."),
    "tests":         ("Checks",         "The harness verifies the work instead of trusting the model."),
    "agents":        ("Agents",         "More than one model role: planner, worker, reviewer, subagents."),
    "goal":          ("Goal & budget",  "When to stop: success criteria, step, token or money limits."),
    "memory":        ("Memory",         "What is saved and loaded between calls or runs."),
    "retrieval":     ("Retrieval",      "Finding the right documents and adding them to the context."),
    "output":        ("Output",         "The shape of the final answer."),
    "observability": ("Logs & tracing", "A record of what happened: steps, tokens, cost."),
}

CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rb", ".java", ".rs", ".sh"}
NOTE_FILES = {"agents.md", "claude.md", "readme.md"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next",
             ".harness", ".claude", ".idea", ".vscode", "site-packages", "target", "coverage"}
MAX_FILES = 400
MAX_BYTES = 400_000

I = re.IGNORECASE
PATTERNS = {
    "model": [re.compile(p, I) for p in (
        r"messages\.(create|stream|parse)\(", r"chat\.completions\.create", r"responses\.create\(",
        r"generate_?content\(", r"\bAnthropic\(\)", r"new Anthropic\(", r"\bOpenAI\(", r"ollama\.chat",
        r"litellm\.completion", r"\bclaude\s+-p\b", r"query\(\s*\{?\s*prompt")],
    "context": [re.compile(p, I) for p in (
        r"\bsystem\s*[=:]\s*", r"[\"']role[\"']\s*:\s*[\"']system", r"system_?prompt", r"messages\.append\(",
        r"messages\.push\(")],
    "loop": [re.compile(p, I) for p in (
        r"stop_reason", r"finish_reason", r"tool_use[\"']?\s*(==|===|in)", r"tool_runner|toolRunner",
        r"while\s+(True|true|\(true\)|not\s+done|!done)", r"for\s+\w+\s+in\s+range\(\s*(max_?steps|max_?turns|max_?iter)")],
    "tools": [re.compile(p, I) for p in (
        r"input_schema", r"@(beta_)?tool\b", r"betaZodTool|zodTool|tool\(\{", r"function_declarations",
        r"[\"']type[\"']\s*:\s*[\"']function[\"']", r"def\s+(run|execute|call)_tool", r"tool_result")],
    "guardrails": [re.compile(p, I) for p in (
        r"approv", r"\bconfirm\(", r"input\([^)]*(y/n|allow|approve)", r"allow_?list|deny_?list|block_?list",
        r"ALLOWED_|BLOCKED_|FORBIDDEN", r"sandbox", r"permission", r"dangerous|destructive")],
    "tests": [re.compile(p, I) for p in (
        r"(pytest|unittest|npm\s+(run\s+)?test|go\s+test)", r"def\s+(verify|check|grade)\w*\(",
        r"function\s+(verify|check|grade)\w*\(", r"\bgrader\b|\bjudge\b|acceptance", r"run_tests")],
    "agents": [re.compile(p, I) for p in (
        r"\bplanner\b", r"\breviewer\b|\bcritic\b", r"sub_?agent", r"orchestrat", r"\bdelegate", r"handoff",
        r"\bworker_?(agent|prompt|system)")],
    "goal": [re.compile(p, I) for p in (
        r"max_?steps|max_?turns|max_?iter", r"budget", r"max_?cost|cost_?limit", r"task_budget",
        r"success_?criteria|done_?when|\bgoal\b")],
    "memory": [re.compile(p, I) for p in (
        r"\bmemory\b|memories", r"remember", r"\.jsonl", r"sqlite", r"session_?(log|store|id)",
        r"AGENTS\.md|CLAUDE\.md", r"save_?(history|session|state)|load_?(history|session|state)")],
    "retrieval": [re.compile(p, I) for p in (
        r"embed(ding)?s?\b|embed\(", r"vector", r"faiss|chroma|pinecone|qdrant|weaviate|lancedb|pgvector",
        r"bm25|tf-?idf", r"retriev", r"search_?docs|semantic_?search", r"\brag\b", r"chunk_?(size|overlap|text|docs?)\b|split_into_chunks")],
    "output": [re.compile(p, I) for p in (
        r"output_config|response_format|json_schema", r"BaseModel\)|pydantic|zod\.object|z\.object",
        r"final_?answer", r"structured_?output")],
    "observability": [re.compile(p, I) for p in (
        r"\blogging\.|getLogger|logger\.", r"opentelemetry|tracer|\bspan\b", r"usage\.(input|output)_tokens",
        r"prompt_tokens|completion_tokens", r"\bcost\b", r"console\.log\(")],
}
MODEL_ID = re.compile(r"\b(claude-[a-z0-9.-]+|gpt-[0-9a-z.-]+|o[134]-?[a-z]*|gemini-[0-9a-z.-]+|llama[0-9a-z.:-]*)\b", I)
TOOL_NAME_NEAR_SCHEMA = re.compile(r"[\"']?name[\"']?\s*[:=]\s*[\"']([a-zA-Z_][\w-]{1,40})[\"']")
DECORATED_TOOL = re.compile(r"@(?:beta_)?tool\b[^\n]*\n\s*(?:async\s+)?def\s+(\w+)")


def _files(root: Path):
    out = []
    for p in root.rglob("*"):
        if len(out) >= MAX_FILES:
            break
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        if not p.is_file():
            continue
        if p.suffix.lower() in CODE_EXT or p.name.lower() in NOTE_FILES:
            try:
                if p.stat().st_size <= MAX_BYTES:
                    out.append(p)
            except OSError:
                pass
    return sorted(out)


def signature(root: Path):
    """Cheap fingerprint of the project, so the watcher only rescans when something changed."""
    sig = []
    for p in _files(root):
        try:
            st = p.stat()
            sig.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    mp = root / ".harness" / "map.json"
    if mp.is_file():
        st = mp.stat()
        sig.append((str(mp), st.st_mtime_ns, st.st_size))
    return hash(tuple(sig))


def _is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1].lower()
    return name.startswith("test_") or name.endswith(("_test.py", ".test.ts", ".test.js", ".spec.ts", ".spec.js")) \
        or "/tests/" in f"/{rel}/"


def scan(root: Path):
    found = {k: [] for k in FEATURES}
    model_ids, tool_names, test_files = set(), set(), []
    for p in _files(root):
        rel = str(p.relative_to(root))
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if p.name.lower() in NOTE_FILES:
            if p.name.lower() in ("agents.md", "claude.md"):
                found["memory"].append({"file": rel, "line": 1, "text": f"{p.name}: notes loaded into the agent's context"})
            continue
        if _is_test_file(rel):
            test_files.append(rel)
            continue
        lines = text.splitlines()
        in_doc = False
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments and docstrings: words like "memory" there describe, they don't build.
            quotes = stripped.count('"""') + stripped.count("'''")
            if in_doc or stripped.startswith(('"""', "'''")):
                if quotes % 2 == 1:
                    in_doc = not in_doc
                continue
            if not stripped or len(stripped) > 300 or stripped.startswith(("#", "//", "*", "/*")):
                continue
            for key, pats in PATTERNS.items():
                if len(found[key]) >= 12:
                    continue
                if any(pt.search(line) for pt in pats):
                    found[key].append({"file": rel, "line": i, "text": stripped[:140]})
        for m in MODEL_ID.finditer(text):
            if "-" in m.group(1) or m.group(1).lower().startswith("llama"):
                model_ids.add(m.group(1))
        tool_names.update(DECORATED_TOOL.findall(text))
        for m in re.finditer(r"input_schema|[\"']parameters[\"']\s*:", text):
            window = text[max(0, m.start() - 400): m.start()]
            names = TOOL_NAME_NEAR_SCHEMA.findall(window)
            if names:
                tool_names.add(names[-1])
    if test_files:
        found["tests"].append({"file": test_files[0], "line": 1, "text": f"{len(test_files)} test file(s)"})

    # A loop needs a model call to loop over; stop words alone are weak evidence.
    if found["loop"] and not found["model"] and not any("tool_runner" in e["text"].lower() for e in found["loop"]):
        found["loop"] = []
    return found, sorted(model_ids)[:6], sorted(t for t in tool_names if t not in {"function", "object", "string"})[:16]


LINK_KINDS = {"flow", "back", "fail", "data", "guard"}
SLUG = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return SLUG.sub("-", str(text).lower()).strip("-")[:60] or "part"


def read_map(root: Path):
    mp = root / ".harness" / "map.json"
    if not mp.is_file():
        return None, None
    try:
        data = json.loads(mp.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return None, f".harness/map.json could not be read: {exc}"
    if not isinstance(data, dict):
        return None, ".harness/map.json must be a JSON object"
    comps = data.get("components", [])
    clean = []
    for c in comps if isinstance(comps, list) else []:
        if not isinstance(c, dict) or c.get("feature") not in FEATURES:
            continue
        clean.append({k: str(c.get(k, "") or "")[:200] for k in ("id", "feature", "label", "file", "symbol", "line", "note", "in", "origin")})
    links = data.get("links")
    if isinstance(links, list):
        links = [{"from": str(l.get("from", ""))[:60], "to": str(l.get("to", ""))[:60],
                  "label": str(l.get("label", "") or "")[:30],
                  "kind": l.get("kind") if l.get("kind") in LINK_KINDS else "flow"}
                 for l in links if isinstance(l, dict)][:400]
    else:
        links = None  # no links key: the monitor draws the usual paths itself
    return {"name": str(data.get("name", ""))[:80], "problem": str(data.get("problem", ""))[:300],
            "components": clean, "links": links}, None


def build_parts(hmap, found, model_ids, tool_names):
    """One node per part. The agent's map first; the code scan fills in what the map does not name."""
    parts, seen = [], set()

    def add(node):
        base, n = node["id"], 2
        while node["id"] in seen:
            node["id"] = f"{base}-{n}"; n += 1
        seen.add(node["id"]); parts.append(node)

    for c in (hmap or {}).get("components", []):
        add({"id": _slug(c["id"] or c["label"] or c["symbol"] or c["feature"]), "feature": c["feature"],
             "label": c["label"] or c["symbol"] or FEATURES[c["feature"]][0], "file": c["file"], "line": c["line"],
             "symbol": c["symbol"], "note": c["note"], "in": c["in"], "origin": c["origin"][:40],
             "source": "external" if c["origin"] else "claude", "evidence": []})
    named = {p["feature"] for p in parts}
    if hmap and hmap.get("problem") and "problem" not in named:
        add({"id": "problem", "feature": "problem", "label": "Problem", "note": hmap["problem"], "file": "",
             "line": "", "symbol": "", "in": "", "source": "claude", "evidence": []})
        named.add("problem")
    known = {p["label"].lower() for p in parts} | {p["symbol"].lower() for p in parts if p["symbol"]}
    for t in tool_names:
        if t.lower() not in known:
            add({"id": _slug("tool-" + t), "feature": "tools", "label": t, "note": "Tool found in the code",
                 "file": "", "line": "", "symbol": t, "in": "", "source": "code", "evidence": []})
            named.add("tools")
    for key, ev in found.items():
        if ev and key not in named:
            label = model_ids[0] if key == "model" and model_ids else FEATURES[key][0]
            add({"id": "code-" + key, "feature": key, "label": label, "note": "Found by scanning the code",
                 "file": ev[0]["file"], "line": ev[0]["line"], "symbol": "", "in": "", "source": "code", "evidence": ev[:8]})

    # Nesting: "in" must name another part and must not loop back on itself.
    ids = {p["id"] for p in parts}
    for p in parts:
        parent = _slug(p["in"]) if p["in"] else ""
        if parent not in ids or parent == p["id"]:
            parent = ""
        p["in"] = parent
    parent_of = {p["id"]: p["in"] for p in parts}
    for p in parts:
        cur, hops = p["in"], 0
        while cur and hops < 50:
            if cur == p["id"]:
                p["in"] = ""; parent_of[p["id"]] = ""; break
            cur, hops = parent_of.get(cur, ""), hops + 1
    return parts


def _ancestors(pid, parent_of):
    out, cur = set(), parent_of.get(pid, "")
    while cur and cur not in out:
        out.add(cur); cur = parent_of.get(cur, "")
    return out


def infer_links(parts):
    """The usual paths of a harness, drawn when the map does not list its own links."""
    parent_of = {p["id"]: p["in"] for p in parts}
    has_kids = {p["in"] for p in parts if p["in"]}
    by = {}
    for p in parts:
        by.setdefault(p["feature"], []).append(p)

    def near(feature, p):
        """The part of this kind closest to p: same box first, then any."""
        cands = by.get(feature, [])
        scope = p["in"]
        for c in cands:
            if c["in"] == scope and c["id"] != p["id"]:
                return c
        anc = _ancestors(p["id"], parent_of)
        for c in cands:
            if c["in"] in anc and c["id"] != p["id"]:
                return c
        return next((c for c in cands if c["id"] != p["id"]), None)

    L = []
    def link(a, b, label="", kind="flow"):
        if a and b and a["id"] != b["id"] and a["id"] not in _ancestors(b["id"], parent_of) \
                and b["id"] not in _ancestors(a["id"], parent_of):
            L.append({"from": a["id"], "to": b["id"], "label": label, "kind": kind})

    if not by.get("model"):
        return []  # without a model call there is no path to guess; draw the boxes only
    for p in parts:
        f, hub = p["feature"], near("model", p)
        if f == "problem":
            link(p, near("context", p) or hub, "task")
        elif f == "context":
            link(p, hub, "each call")
        elif f == "memory":
            link(p, near("context", p) or hub, "loads", "data")
        elif f == "retrieval":
            link(p, near("context", p) or hub, "adds docs", "data")
        elif f == "tools":
            link(hub, p, "calls"); link(p, hub, "result", "back")
        elif f == "guardrails":
            link(p, hub, "checks", "guard")
        elif f == "loop" and p["id"] not in has_kids:
            link(p, hub, "calls"); link(hub, p, "again", "back")
        elif f == "goal":
            link(p, near("loop", p) or hub, "limits", "guard")
        elif f == "agents" and p["id"] not in has_kids:
            link(hub, p, "delegates"); link(p, hub, "reports", "back")
        elif f == "tests":
            link(hub, p, "done"); link(p, near("output", p), "pass"); link(p, hub, "try again", "fail")
        elif f == "output" and not by.get("tests"):
            link(hub, p, "answer")
        elif f == "observability":
            link(hub, p, "logs", "data")
    return L


def analyze(root: Path):
    root = root.resolve()
    found, model_ids, tool_names = scan(root)
    hmap, map_error = read_map(root)
    # Context, logs and limits patterns are noisy on their own: count them only with a model call present.
    for key in ("context", "observability", "goal"):
        if not found["model"]:
            found[key] = []
    parts = build_parts(hmap, found, model_ids, tool_names)
    ids = {p["id"] for p in parts}
    if hmap and hmap.get("links") is not None:
        links = [l for l in (_fix(l) for l in hmap["links"]) if l["from"] in ids and l["to"] in ids and l["from"] != l["to"]]
        links_source = "claude"
    else:
        links, links_source = infer_links(parts), "guessed"
    features = {}
    for key, (label, meaning) in FEATURES.items():
        mine = [p for p in parts if p["feature"] == key]
        srcs = {p["source"] for p in mine}
        features[key] = {"label": label, "meaning": meaning, "on": bool(mine), "count": len(mine),
                         "source": "both" if len(srcs) > 1 else (srcs.pop() if srcs else ""),
                         "evidence": found[key][:8]}
    return {
        "project": str(root),
        "name": (hmap or {}).get("name") or root.name,
        "problem": (hmap or {}).get("problem", ""),
        "features": features,
        "parts": parts,
        "links": links,
        "links_source": links_source,
        "model_ids": model_ids,
        "tools": tool_names,
        "has_map": hmap is not None,
        "map_error": map_error,
    }


def _fix(l):
    return {**l, "from": _slug(l["from"]), "to": _slug(l["to"])}


if __name__ == "__main__":
    import sys
    print(json.dumps(analyze(Path(sys.argv[1] if len(sys.argv) > 1 else ".")), indent=2)[:6000])
