#!/bin/bash
# Graded, network-isolated SWE-bench run.
#
#   scripts/isolated_run.sh <prompt-file> [model] [out-dir]
#
# The agent runs on an --internal Docker network: no DNS, no route out. Its only
# egress is an allowlist proxy that permits the model API and 403s everything
# else, logging every attempt (scripts/egress_proxy.py). That closes finding
# 11's retrieval hole — de-identifying the fixture is provably not enough.
#
# Requires an image built by scripts/isolated_setup.sh, and a Claude Code OAuth
# token (Pro plan; no ANTHROPIC_API_KEY needed). On macOS the token is read from
# the Keychain automatically; elsewhere set CLAUDE_CODE_OAUTH_TOKEN yourself.
#
# Writes to <out-dir>: agent-stdout.txt, grade.txt, git-status.txt, git-stash.txt,
# egress.jsonl, report.html/.json (the cc_trace profile).
#
# What to read afterwards:
#   egress.jsonl  — what the agent TRIED to fetch (blocked requests never show
#                   up as tool calls, so the transcript alone can't tell you)
#   git-stash.txt — finding 11's other failure mode: a correct fix left stashed
set -euo pipefail

PROMPT_FILE="${1:?usage: isolated_run.sh <prompt-file> [model] [out-dir]}"
MODEL="${2:-opus}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
OUT="${3:-$REPO_ROOT/reports/isolated-$(date +%Y%m%d-%H%M%S)}"

FIXTURE_IMAGE="${FIXTURE_IMAGE:-cc-isolated-fixture:latest}"
NET_INT="${NET_INT:-cc-iso-int}"
NET_EXT="${NET_EXT:-cc-iso-ext}"
# Default allowlist is the model API alone. Add hosts the *task* legitimately
# needs (e.g. pypi.org if it must install) — but never a source-hosting domain,
# which is the retrieval path being closed.
ALLOW="${ALLOW:-api.anthropic.com}"

mkdir -p "$OUT/claude-home" && chmod 777 "$OUT/claude-home"

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  CLAUDE_CODE_OAUTH_TOKEN=$(security find-generic-password -s "Claude Code-credentials" -w \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["claudeAiOauth"]["accessToken"])')
fi

echo "=== bring up isolated network + allowlist proxy (allow: $ALLOW) ==="
docker network inspect "$NET_INT" >/dev/null 2>&1 || docker network create --internal "$NET_INT" >/dev/null
docker network inspect "$NET_EXT" >/dev/null 2>&1 || docker network create "$NET_EXT" >/dev/null
docker rm -f cc-iso-proxy >/dev/null 2>&1 || true
docker run -d --name cc-iso-proxy --network "$NET_EXT" \
  -v "$HERE:/scripts:ro" -v "$OUT:/out" python:3.12-slim \
  python3 /scripts/egress_proxy.py --allow $ALLOW --log /out/egress.jsonl >/dev/null
docker network connect "$NET_INT" cc-iso-proxy
sleep 1

# NOTE: the agent container is started detached with `sleep infinity` and the
# agent is run via `docker exec`. Running the agent as the container's main
# process makes the container exit the moment it finishes — taking the graded
# filesystem state with it before it can be inspected.
echo "=== start agent container (isolated) ==="
docker rm -f cc-iso-run >/dev/null 2>&1 || true
docker run -d --name cc-iso-run --network "$NET_INT" \
  -e CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
  -e HTTPS_PROXY="http://cc-iso-proxy:8888" -e HTTP_PROXY="http://cc-iso-proxy:8888" \
  -e https_proxy="http://cc-iso-proxy:8888" -e http_proxy="http://cc-iso-proxy:8888" \
  -v "$OUT/claude-home:/home/agent/.claude" \
  -w /home/agent/task/repo \
  "$FIXTURE_IMAGE" sleep infinity >/dev/null

echo "=== graded run: model=$MODEL ==="
set +e
docker exec -w /home/agent/task/repo cc-iso-run \
  claude -p "$(cat "$PROMPT_FILE")" --model "$MODEL" --dangerously-skip-permissions \
  2>&1 | tee "$OUT/agent-stdout.txt" | tail -30
set -e

echo
echo "=== test integrity: did the agent edit its own answer key? ==="
# A grade is only meaningful if the graded tests are the ones the fixture
# shipped. Compare against the baseline copy (not git — the test patch is
# uncommitted, so HEAD has the pre-patch tests) and restore before grading, so
# the number below always measures the fix rather than the edit.
docker exec -w /home/agent/task/repo cc-iso-run bash -lc '
  TASK=/home/agent/task
  if [ ! -d "$TASK/test_baseline" ]; then
    echo "NO BASELINE — fixture predates the integrity check; grade is UNVERIFIED"
    echo "  rebuild with scripts/isolated_setup.sh to enable it"
    exit 0
  fi
  changed=0
  for s in $(cat "$TASK/f2p.txt"); do
    f="${s%%::*}"; f="${f#./}"
    if ! cmp -s "$f" "$TASK/test_baseline/$f"; then
      echo "TAMPERED: $f differs from the fixture baseline — restoring"
      cp "$TASK/test_baseline/$f" "$f"
      changed=1
    fi
  done
  [ "$changed" -eq 0 ] && echo "graded test files unmodified" || \
    echo "^^ the agent rewrote graded tests; they were restored before grading"
' 2>&1 | tee "$OUT/test-integrity.txt" || true

echo
echo "=== GRADE: FAIL_TO_PASS in the final tree ==="
docker exec -w /home/agent/task/repo cc-iso-run bash -lc \
  '/home/agent/task/.venv/bin/python -m pytest -q $(cat /home/agent/task/f2p.txt)' \
  2>&1 | tail -5 | tee "$OUT/grade.txt" || true

echo
echo "=== stranded-work check (finding 11 failure mode #2) ==="
docker exec -w /home/agent/task/repo cc-iso-run git status --short | tee "$OUT/git-status.txt"
docker exec -w /home/agent/task/repo cc-iso-run git stash list | tee "$OUT/git-stash.txt"
[ -s "$OUT/git-stash.txt" ] && echo "  ^^ NON-EMPTY STASH — fix may be stranded, grade is not trustworthy"

echo
echo "=== egress (finding 11 failure mode #1: what did it TRY to fetch?) ==="
python3 "$HERE/egress_proxy.py" --summarize "$OUT/egress.jsonl"

echo
echo "=== profile the transcript ==="
T=$(find "$OUT/claude-home/projects" -name '*.jsonl' | head -1)
# Hand the audit the graded selectors so a write to one of them is flagged
# 'high' rather than a generic warning. REPO (optional, e.g. sympy/sympy) scopes
# solution-channel severity; it is read here on the host, after the run, so it
# never reaches the agent and cannot re-identify the fixture.
F2P="$(docker exec cc-iso-run cat /home/agent/task/f2p.txt 2>/dev/null || true)"
python3 -m cc_trace "$T" -o "$OUT/report.html" --json \
  ${F2P:+--graded-test "$F2P"} ${REPO:+--repo "$REPO"}

echo
echo "done — artifacts in $OUT"
echo "container cc-iso-run left up for inspection; tear down with:"
echo "  docker rm -f cc-iso-run cc-iso-proxy"
