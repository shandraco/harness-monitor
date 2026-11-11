---
name: harnessmonitor
description: Open the live Harness Monitor for this project, a diagram that lights up each part of an AI agent harness (model, loop, tools, guardrails, checks, agents, goal, memory, retrieval, output, logs) as it gets built, and keep it accurate while building. Use when the user types /harnessmonitor or $harnessmonitor, or asks to watch, map or visualize the harness being built.
---

# Harness Monitor

The user is building an agent harness with you and wants every part shown on a live diagram.

## 1. Start it

Run this from the project root:

```bash
harnessmonitor start --project "$PWD"
```

It starts the monitor (or points a running one at this project) and opens http://127.0.0.1:4849.
It changes no project files. Tell the user the URL in one line. If the command is not found, tell
the user to install Harness Monitor (https://github.com/shandraco/harness-monitor) and stop. If it
fails, show its output.

## 2. Map what exists now

Write `.harness/map.json` describing the parts already in the code. If there are none yet,
write it with the problem (if you know it) and an empty `components` list.

## 3. Keep the map current for the rest of this session

Whenever you add, rename, move or remove a harness part, or change how parts connect, update
`.harness/map.json` in the same turn, before you reply. The monitor redraws within a second and
announces each new part, so the map must match the code at all times. Never list a part or a path
the code does not have yet.

The diagram shows the real shape of the harness, so be granular: **one component per real part**
(each tool, each guardrail, each loop, each agent, each prompt or memory store), never one entry
standing for a whole category.

```json
{
  "name": "Short project name",
  "problem": "One sentence: the job this agent does",
  "components": [
    {"id": "loop", "feature": "loop", "label": "Agent loop", "file": "agent.py", "line": 42,
     "symbol": "run_agent", "note": "Calls the model until it stops asking for tools"},
    {"id": "model", "feature": "model", "label": "claude-sonnet-5-5", "in": "loop",
     "file": "agent.py", "line": 50, "note": "One model call per step"},
    {"id": "read-file", "feature": "tools", "label": "read_file", "in": "loop",
     "file": "tools.py", "line": 10, "symbol": "read_file", "note": "Reads a file inside the project folder"},
    {"id": "path-check", "feature": "guardrails", "label": "Path check", "in": "loop",
     "file": "tools.py", "line": 4, "note": "Blocks paths outside the project"}
  ],
  "links": [
    {"from": "problem", "to": "loop", "label": "task"},
    {"from": "model", "to": "read-file", "label": "calls"},
    {"from": "read-file", "to": "path-check", "label": "checks", "kind": "guard"},
    {"from": "read-file", "to": "model", "label": "result", "kind": "back"}
  ]
}
```

- `id`: short, unique, lowercase with dashes. Links and `in` refer to it. The top-level `problem`
  gets the id `problem` automatically.
- `in` (optional): the id of the part this one runs inside. Put the model, tools and their
  guardrails `in` the loop that repeats them, an inner loop `in` an outer loop, and a subagent's
  own model, tools and rules `in` that agent. The diagram draws these as boxes inside boxes.
- `origin` (optional): when the harness runs another tool that brings its own parts (Claude Code
  via `claude -p`, Codex, an MCP server), list the parts that tool really uses in this setup with
  `"origin": "Claude Code"`, `in` the agent that runs it. `file` and `line` point to where this
  project starts or configures it. Only list what this project's setup turns on (allowed tools,
  permission mode, budgets), not everything the tool can do.
- `links`: every path data or control takes between parts, each with a 1 to 3 word `label`.
  `kind` is `flow` (default: sends or calls), `back` (returns: results, "again"), `fail`
  (failed, try again), `data` (saves to or loads from memory, logs) or `guard` (a check or limit
  acting on the other part). Include the loop-back paths, the tool results and the failure paths.
  Without a `links` list the monitor guesses the usual paths, so always write it.

`feature` must be one of these keys:

| key | means |
|---|---|
| `problem` | the task given to the agent |
| `model` | the LLM call (label it with the model id) |
| `context` | what is sent each call: system prompt, history, injected notes |
| `loop` | each repeat-until-done loop (nest inner loops `in` outer ones) |
| `tools` | one entry per tool the model can call (never one entry for all tools) |
| `guardrails` | one entry per approval, allow/deny list or sandbox |
| `tests` | checks the harness runs on the agent's work |
| `agents` | one entry per extra role: planner, reviewer, subagent |
| `goal` | success criteria and limits: steps, tokens, money, time |
| `memory` | anything saved and loaded between calls or runs |
| `retrieval` | finding documents and adding them to the context |
| `output` | the shape of the final answer (schema, format) |
| `observability` | logs, traces, token and cost tracking |

Rules: `label` is what the diagram shows, so keep it under 24 characters. `note` is one plain
sentence under 60 characters. Paths are relative to the project root; `line` is where the part
is defined.

## Stopping

- Stop the monitor:

```bash
harnessmonitor stop
```
