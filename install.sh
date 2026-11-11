#!/bin/sh
# Installs Harness Monitor for Claude Code and/or Codex without the plugin system.
#   ./install.sh            both
#   ./install.sh claude     Claude Code only  (/harnessmonitor)
#   ./install.sh codex      Codex only        ($harnessmonitor)
#   ./install.sh uninstall  remove both
set -e
REPO="$(cd "$(dirname "$0")" && pwd)"
BIN="$REPO/bin/harnessmonitor"
WHAT="${1:-all}"

put_skill() {  # $1 = destination folder
  mkdir -p "$1"
  # Point the skill at this clone, so it works without anything on PATH.
  sed "s|^harnessmonitor |\"$BIN\" |" "$REPO/skills/harnessmonitor/SKILL.md" > "$1/SKILL.md"
  echo "Installed skill -> $1/SKILL.md"
}

if [ "$WHAT" = "uninstall" ]; then
  rm -rf "$HOME/.claude/skills/harnessmonitor" "$HOME/.agents/skills/harnessmonitor"
  "$BIN" stop >/dev/null 2>&1 || true
  echo "Removed Harness Monitor skills."
  exit 0
fi
command -v python3 >/dev/null || { echo "Harness Monitor needs Python 3.9 or newer (python3)."; exit 1; }
chmod +x "$BIN"
case "$WHAT" in
  claude) put_skill "$HOME/.claude/skills/harnessmonitor" ;;
  codex)  put_skill "$HOME/.agents/skills/harnessmonitor" ;;
  all)    put_skill "$HOME/.claude/skills/harnessmonitor"; put_skill "$HOME/.agents/skills/harnessmonitor" ;;
  *) echo "Usage: ./install.sh [claude|codex|uninstall]"; exit 1 ;;
esac
echo "Done. Start a new session in your project, then type /harnessmonitor (Claude Code) or \$harnessmonitor (Codex)."
