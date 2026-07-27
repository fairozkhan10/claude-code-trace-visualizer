#!/bin/bash
# Build a RED, de-identified SWE-bench fixture inside a container image.
#
# Runs WITH network (cloning and pip need it). The graded run afterwards
# (isolated_run.sh) has no egress except the model API, so all installation
# must happen here.
#
# Usage:
#   scripts/isolated_setup.sh <fixture-dir> <F2P-test-name>...
#
#   <fixture-dir> must contain:
#       repo/              a git clone checked out at the instance base commit
#       test_patch.diff    the instance's TEST patch (not the fix)
#     and its *path must not leak the instance id* — a SWE-bench instance id is
#     the upstream PR number, and finding 11 showed agents read it off the path.
#
# Example:
#   scripts/isolated_setup.sh ~/cc-bench-scratch/swebench/task-a \
#       test_infinity test_neg_infinity test_other_symbol
#
# Produces the image $FIXTURE_IMAGE (default cc-isolated-fixture:latest),
# frozen in its verified-red state.
set -euo pipefail

FIXTURE_DIR="${1:?usage: isolated_setup.sh <fixture-dir> <F2P-test-name>...}"
shift
F2P_NAMES=("$@")
[ ${#F2P_NAMES[@]} -gt 0 ] || { echo "need at least one FAIL_TO_PASS test name" >&2; exit 2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_IMAGE="${BASE_IMAGE:-cc-isolated-base:latest}"
FIXTURE_IMAGE="${FIXTURE_IMAGE:-cc-isolated-fixture:latest}"
NET_EXT="${NET_EXT:-cc-iso-ext}"
PY="${PY:-3.9}"

docker network inspect "$NET_EXT" >/dev/null 2>&1 || docker network create "$NET_EXT" >/dev/null

echo "=== build base image (python $PY + node + claude) ==="
docker build -q --build-arg "PY=$PY" -f "$HERE/isolated.Dockerfile" -t "$BASE_IMAGE" "$HERE" >/dev/null
docker run --rm "$BASE_IMAGE" bash -lc 'python3 -V; node -v; claude --version'

echo
echo "=== build fixture (clone at base commit, apply TEST patch, install, red-check) ==="
docker rm -f cc-iso-setup >/dev/null 2>&1 || true
docker run --name cc-iso-setup --network "$NET_EXT" \
  -v "$(cd "$FIXTURE_DIR" && pwd):/fixture:ro" \
  -e "F2P_NAMES=${F2P_NAMES[*]}" \
  "$BASE_IMAGE" bash -euo pipefail -c '
TASK=/home/agent/task
mkdir -p "$TASK" && cd "$TASK"

# clone the COMMITTED state — a fixture reused across runs often has a dirty tree
git clone -q /fixture/repo repo
cd repo
git remote remove origin || true      # nothing to fetch, no path to read
git log --oneline -1

git apply -v /fixture/test_patch.diff
git status --short

python3 -m venv "$TASK/.venv"
"$TASK/.venv/bin/pip" -q install --upgrade pip
"$TASK/.venv/bin/pip" -q install -e .
"$TASK/.venv/bin/pip" -q install "pytest>=7,<8" py

# sympy-style FAIL_TO_PASS are bare function names (it runs its own test runner);
# resolve each to <file>::<name> the way scripts/swebench_run.py does.
SEL=""
for t in $F2P_NAMES; do
  if [ "${t#*::}" != "$t" ]; then SEL="$SEL $t"; continue; fi
  f=$(grep -rln "def ${t}(" --include="*.py" . | head -1)
  echo "  $t -> $f"
  SEL="$SEL ${f}::${t}"
done
echo "$SEL" > "$TASK/f2p.txt"

echo "=== RED CHECK (must FAIL — a green fixture means the patch or env is wrong) ==="
set +e
"$TASK/.venv/bin/python" -m pytest -q $SEL 2>&1 | tail -6
rc=${PIPESTATUS[0]}
set -e
[ "$rc" -ne 0 ] || { echo "FIXTURE IS GREEN — aborting, nothing to solve" >&2; exit 1; }
echo "fixture is correctly RED"
'

docker commit -m "red fixture, de-identified" cc-iso-setup "$FIXTURE_IMAGE" >/dev/null
docker rm -f cc-iso-setup >/dev/null
echo
echo "committed $FIXTURE_IMAGE — now run: scripts/isolated_run.sh <model>"
