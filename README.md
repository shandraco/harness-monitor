# Harness Monitor

A live diagram of an AI agent harness while your coding agent builds it.

You ask Claude Code or Codex to build an agent. Harness Monitor shows each part as soon as it exists in
the code: the model call, the loop, each tool, guardrails, checks, extra agents, goal and budget,
memory, retrieval, output and logs. When a new part arrives, the diagram announces it ("New part:
Guardrails in guard.py:2"). Click any part to see what it does and where it lives.

It was made for teaching people how agent harnesses work, and it is just as useful for checking
what you actually built.

- Runs on your machine at http://127.0.0.1:4849. Nothing is uploaded.
- Python 3.9+ standard library only. No npm, no pip dependencies.
- Works on macOS, Linux and Windows.

Download: **https://harness-monitor.vercel.app/download** (zip of the latest version)

## Install

Pick the agent you use. You only need one of these.

### Claude Code (terminal, VS Code, desktop)

```
/plugin marketplace add shandraco/harness-monitor
/plugin install harnessmonitor@harness-monitor
```

Start a new session, then type `/harnessmonitor:harnessmonitor` (or just ask "open the harness
monitor"). The plugin also adds an activity feed of your prompts and the agent's tool use.

The same plugin works in the Claude Code VS Code extension: type `/plugins` there to manage it.

### Codex (CLI or IDE extension), or Claude Code without plugins

```sh
git clone https://github.com/shandraco/harness-monitor
cd harness-monitor
./install.sh          # both;  ./install.sh codex  or  ./install.sh claude  for one
```

Then start a new session in your project and type `$harnessmonitor` in Codex or `/harnessmonitor` in
Claude Code. `./install.sh uninstall` removes it.

### Just the command

```sh
pip install git+https://github.com/shandraco/harness-monitor
harnessmonitor start --project path/to/your/agent
```

This shows the diagram from the code scan alone, with any coding agent or none.

## How it knows what each part is

Two sources, merged, and every part is labelled with where it came from:

1. **The agent names the parts.** The skill asks your coding agent to keep `.harness/map.json`
   up to date in the same turn it changes the code: which file and line is the loop, each tool,
   each guardrail, and how they connect.
2. **A code scan backs it up.** Patterns spot model calls, loops, tool schemas, tests, memory files
   and more, so the diagram still fills in if the agent forgets the map.

The monitor rescans once a second, so it also works without any hooks.

## Commands

| Command | What it does |
|---|---|
| `harnessmonitor start [--project DIR] [--no-open]` | Start, or point a running monitor at another project |
| `harnessmonitor status` | Is it running, and what is it watching |
| `harnessmonitor stop` | Stop it |
| `harnessmonitor hooks add\|remove` | Activity feed hooks for one Claude Code project (not needed with the plugin) |

Settings: `HARNESS_MONITOR_PORT` (default 4849), `HARNESS_MONITOR_HOME` (default
`~/.harness-monitor`, holds the log and process id).

### Activity feed in Codex (optional)

Codex supports hooks. To see prompts and tool use in the feed, add a command hook for
`UserPromptSubmit`, `PostToolUse` and `Stop` that runs:

```sh
T="${HARNESS_MONITOR_HOME:-$HOME/.harness-monitor}/token"; [ -r "$T" ] && curl -s -m 2 -X POST -H 'Content-Type: application/json' -H 'X-Agent: codex' -H "X-Monitor-Token: $(cat "$T")" --data-binary @- http://127.0.0.1:4849/api/hook >/dev/null 2>&1 || true
```

See the Codex hooks docs for where your hooks file lives. The diagram itself does not need this.

## Safety

Each run creates a random token in `~/.harness-monitor/token` (readable only by you). The
browser link, the command and the hooks must present it, so other local users and programs cannot
read the activity or point the monitor elsewhere. The server also listens on 127.0.0.1 only, rejects requests whose Host header is not localhost (this
blocks DNS rebinding), and only accepts JSON posts, which websites cannot send to it without a
CORS preflight it never approves. Hook events from folders other than the watched project are
ignored. It reads your project files; it never changes them.

## Develop

```sh
python3 -m unittest discover tests
claude plugin validate . --strict
```

## License

MIT. Bundles elkjs (EPL-2.0) for the diagram layout; see THIRD_PARTY_NOTICES.md.
