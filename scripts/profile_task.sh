#!/usr/bin/env bash
# Run Claude Code headless on a task, then profile the resulting trace.
#
# Usage:
#   scripts/profile_task.sh "fix the failing test in foo.py"
#   scripts/profile_task.sh --prompt-file tasks/01-bugfix.md
#
# Requires the `claude` CLI on PATH. Claude Code writes a transcript to
# ~/.claude/projects/<cwd>/<session>.jsonl, which `--latest` then picks up.
set -euo pipefail

if [[ "${1:-}" == "--prompt-file" ]]; then
  PROMPT="$(cat "$2")"
else
  PROMPT="${1:?usage: profile_task.sh \"<task prompt>\" | --prompt-file <path>}"
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/reports/$STAMP.html"

echo ">> running Claude Code on task..."
START=$(date +%s)
claude -p "$PROMPT" --output-format text || true
END=$(date +%s)
echo ">> task wall-clock: $((END-START))s"

echo ">> profiling latest session -> $OUT"
python3 -m cc_trace --latest -o "$OUT" --json --open
