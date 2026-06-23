#!/usr/bin/env bash
# Phase-A harness: like run-both.sh, but ALSO captures read events + full argv
# with bcc-tools, which AgentSight's audit does not emit (writes-only, no argv).
#
# Carries forward run-both.sh's env constraints (this VM):
#  - default `sudo` is sudo-rs and refuses the passwordless grant; use sudo.ws.
#  - never pass `sudo -E`.
#  - the traced claude needs an `env HOME=.. USER=.. LOGNAME=..` wrapper to find
#    credentials; that makes argv[0]=env, so AgentSight probes are pinned with
#    --binary-path.
#  - bcc-tools (opensnoop/execsnoop) run system-wide; we attribute by repo path
#    (reads) and by inspecting argv (execs), exactly as the writes analysis did.
#
# Outputs (all OUTSIDE the public repo — never committed):
#   $OUT/opensnoop.log  raw open() syscalls (timestamped, extended flags, full path)
#   $OUT/execsnoop.log  raw exec() syscalls with full argv
#   /tmp/ours.json, /tmp/agentsight-audit.json, /tmp/agentsight-prompts.json
set -euo pipefail
TASK="$1"; REPO="${2:-$PWD}"
OUT="${3:-/tmp/readnet-out}"
TOOLDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$OUT"
cd "$REPO"

SUDO=/usr/bin/sudo.ws
CLAUDE_BIN=/usr/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
PROJDIR="$HOME/.claude/projects/$(echo "$REPO" | sed 's#/#-#g')"
BEFORE=$(ls "$PROJDIR"/*.jsonl 2>/dev/null | sort || true)

# --- start bcc capture in the background (reads + argv ground truth) ---
# opensnoop: -T timestamp, -e extended (flags), -F full relative path.
# execsnoop: -T timestamp, -q quote args (so we can see full argv unambiguously).
"$SUDO" opensnoop-bpfcc -T -e -F   > "$OUT/opensnoop.log" 2>"$OUT/opensnoop.err" &
"$SUDO" execsnoop-bpfcc -T -q      > "$OUT/execsnoop.log" 2>"$OUT/execsnoop.err" &
# give the probes a moment to attach before the task starts
sleep 3

# --- the traced task (AgentSight = writes + network ground truth) ---
"$SUDO" agentsight record --binary-path "$CLAUDE_BIN" -- \
  env HOME="$HOME" USER="$USER" LOGNAME="$USER" \
  claude -p "$TASK" --dangerously-skip-permissions --output-format text

# --- stop bcc capture (needs sudo; the python child outlives the sudo wrapper) ---
sleep 2
"$SUDO" pkill -f opensnoop-bpfcc 2>/dev/null || true
"$SUDO" pkill -f execsnoop-bpfcc 2>/dev/null || true
sleep 1

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
echo "--- ours: /tmp/ours.json | truth(writes/net): /tmp/agentsight-audit.json"
echo "--- reads:  $OUT/opensnoop.log | argv: $OUT/execsnoop.log"
