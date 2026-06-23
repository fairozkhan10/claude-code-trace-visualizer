#!/usr/bin/env bash
set -euo pipefail
TASK="$1"; REPO="${2:-$PWD}"
TOOLDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # where cc_trace lives
cd "$REPO"

# --- env-specific notes (this VM) ---
# 1) Default `sudo` is sudo-rs, which refuses the google_sudoers passwordless
#    grant ("I'm afraid I can't do that"). Classic sudo at /usr/bin/sudo.ws works.
# 2) Under sudo, the traced child inherits HOME=/root, so Claude can't find our
#    credentials (~/.claude/.credentials.json) and reports "Not logged in".
#    We wrap the child in `env HOME=... USER=... LOGNAME=...`. Because that makes
#    argv[0]=`env`, we pin AgentSight's probes with --comm/--binary-path.
SUDO=/usr/bin/sudo.ws
CLAUDE_BIN=/usr/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
PROJDIR="$HOME/.claude/projects/$(echo "$REPO" | sed 's#/#-#g')"

# Snapshot existing transcripts so we pick the NEW one (not the live harness session).
BEFORE=$(ls "$PROJDIR"/*.jsonl 2>/dev/null | sort || true)

"$SUDO" agentsight record --binary-path "$CLAUDE_BIN" -- \
  env HOME="$HOME" USER="$USER" LOGNAME="$USER" \
  claude -p "$TASK" --dangerously-skip-permissions --output-format text

AFTER=$(ls "$PROJDIR"/*.jsonl 2>/dev/null | sort || true)
T=$(comm -13 <(printf '%s\n' "$BEFORE") <(printf '%s\n' "$AFTER") | \
    xargs -r ls -t 2>/dev/null | head -1)
if [ -z "${T:-}" ]; then
  echo "ERROR: no new transcript created (traced run may have failed to start/login)" >&2
  exit 1
fi
echo "transcript: $T"
PYTHONPATH="$TOOLDIR" python3 -m cc_trace "$T" -o /tmp/ours.html --json
DB=$(ls -t "$REPO"/agentsight-*.db 2>/dev/null | head -1)
agentsight report audit   --db "$DB" --json > /tmp/agentsight-audit.json
agentsight report prompts --db "$DB" --json > /tmp/agentsight-prompts.json
echo "--- ours: /tmp/ours.json | truth: /tmp/agentsight-audit.json | LLM: /tmp/agentsight-prompts.json"
