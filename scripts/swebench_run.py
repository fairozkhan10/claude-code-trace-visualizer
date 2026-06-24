#!/usr/bin/env python3
"""Run a SWE-bench instance through Claude Code and profile it with cc_trace.

This is the benchmark harness for the workload-characterization study: it turns
one SWE-bench(-Lite) instance into a reproducible fixture, optionally drives
Claude Code on it, and profiles the resulting transcript.

Pipeline (mirrors how SWE-bench grades, minus the Docker image):
  1. clone the repo, hard-checkout ``base_commit``
  2. make a fresh venv and install the project + pytest
  3. apply the instance's *test* patch only (the failing tests, not the fix)
  4. run the FAIL_TO_PASS tests → they should be **red** (fixture is valid)
  5. (--run) drive ``claude -p`` with the problem statement to make them pass
  6. re-run FAIL_TO_PASS → red→green tells you if the agent solved it
  7. profile the transcript: cc_trace dashboard + JSON + a flame graph

Usage:
  python3 scripts/swebench_run.py <instance_id> --rows /tmp/swe_100.json        # setup + verify red
  python3 scripts/swebench_run.py <instance_id> --rows /tmp/swe_100.json --run  # + drive claude + profile
Options: --py python3.12 (interpreter for the fixture venv), --model sonnet,
         --reports-dir reports, --keep (don't re-clone if present).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path.home() / "cc-bench-scratch" / "swebench"
REPO_ROOT = Path(__file__).resolve().parent.parent

# per-repo install recipe (run inside the venv, cwd = repo). default below.
# per-repo install recipe (run inside the venv, cwd = repo). pytest is pinned to
# the instance era — newer pytest removes internals the old test suites import
# (e.g. flask 2.3's test_cli imports `_pytest.monkeypatch.notset`).
INSTALL = {
    "pallets/flask": ["pip install -e .", "pip install 'pytest>=7,<8'"],
    "psf/requests": ["pip install -e .",
                     "pip install 'pytest>=7,<8' 'urllib3<2' chardet idna certifi"],
}
DEFAULT_INSTALL = ["pip install -e .", "pip install 'pytest>=7,<8'"]


def sh(cmd: str, cwd: Path | None = None, env: dict | None = None,
       check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    print(f"  $ {cmd}", file=sys.stderr)
    return subprocess.run(cmd, shell=True, cwd=cwd, env=env, check=check,
                          text=True, capture_output=capture)


def load_instance(instance_id: str, rows_files: list[str]) -> dict:
    for rf in rows_files:
        for row in json.load(open(rf)).get("rows", []):
            if row["row"].get("instance_id") == instance_id:
                return row["row"]
    # fall back to the datasets-server API
    url = ("https://datasets-server.huggingface.co/rows?dataset=princeton-nlp/"
           "SWE-bench_Lite&config=default&split=test&offset=0&length=100")
    raise SystemExit(f"{instance_id} not in {rows_files}; fetch its page from {url}")


def venv_env(venv: Path) -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{venv / 'bin'}:{env['PATH']}"
    env["VIRTUAL_ENV"] = str(venv)
    return env


def run_tests(repo: Path, env: dict, tests: list[str]) -> bool:
    """True if all named tests pass."""
    sel = " ".join(shlex.quote(t) for t in tests)
    r = sh(f"python -m pytest -x -q {sel}", cwd=repo, env=env, check=False)
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("instance_id")
    ap.add_argument("--rows", nargs="*", default=[], help="cached datasets-server rows json")
    ap.add_argument("--py", default="python3.12", help="interpreter for the fixture venv")
    ap.add_argument("--run", action="store_true", help="drive claude -p after setup")
    ap.add_argument("--model", default=None, help="claude --model (e.g. sonnet, opus)")
    ap.add_argument("--keep", action="store_true", help="reuse an existing clone")
    ap.add_argument("--reports-dir", default=str(REPO_ROOT / "reports"))
    args = ap.parse_args()

    inst = load_instance(args.instance_id, args.rows)
    repo_slug = inst["repo"]
    f2p = json.loads(inst["FAIL_TO_PASS"])
    base = inst["base_commit"]
    work = ROOT / args.instance_id
    repo = work / "repo"
    venv = work / ".venv"
    work.mkdir(parents=True, exist_ok=True)

    # 1. clone + checkout base
    if repo.exists() and not args.keep:
        sh(f"rm -rf {shlex.quote(str(repo))}")
    if not repo.exists():
        sh(f"git clone https://github.com/{repo_slug} {shlex.quote(str(repo))}")
    sh("git reset --hard && git clean -fdq", cwd=repo, check=False)
    sh(f"git checkout -q {base}", cwd=repo)

    # 2. venv + install (recreate if it was built with a different interpreter)
    want_ver = sh(f"{args.py} -c 'import sys;print(\"%d.%d\"%sys.version_info[:2])'",
                  capture=True).stdout.strip()
    have_ver = ""
    if (venv / "bin" / "python").exists():
        have_ver = sh(f"{venv / 'bin' / 'python'} -c 'import sys;print(\"%d.%d\"%sys.version_info[:2])'",
                      capture=True, check=False).stdout.strip()
    if venv.exists() and have_ver != want_ver:
        sh(f"rm -rf {shlex.quote(str(venv))}")
    if not venv.exists():
        sh(f"{args.py} -m venv {shlex.quote(str(venv))}")
    env = venv_env(venv)
    sh("python -m pip install -q --upgrade pip", cwd=repo, env=env, check=False)
    for step in INSTALL.get(repo_slug, DEFAULT_INSTALL):
        sh(step, cwd=repo, env=env)

    # 3. apply the TEST patch only
    (work / "test_patch.diff").write_text(inst["test_patch"])
    sh(f"git apply {shlex.quote(str(work / 'test_patch.diff'))}", cwd=repo)

    # 4. verify the fixture is red
    print(f"\n>> verifying fixture is RED ({len(f2p)} FAIL_TO_PASS test(s))…", file=sys.stderr)
    if run_tests(repo, env, f2p):
        print("!! fixture tests already PASS — not a valid red fixture; aborting", file=sys.stderr)
        return 2
    print(">> fixture is red (tests fail as expected). Fixture ready.", file=sys.stderr)

    if not args.run:
        print(f"\nFixture at {repo}\n  venv: {venv}\n  to drive the agent, re-run with --run")
        return 0

    # 5. drive claude -p
    prompt = (f"{inst['problem_statement']}\n\n"
              f"Make the failing tests pass. The relevant tests are:\n  "
              + "\n  ".join(f2p)
              + "\nImplement the fix in the library source (do not edit the tests).")
    (work / "prompt.txt").write_text(prompt)
    model = f" --model {args.model}" if args.model else ""
    t0 = time.time()
    print(f"\n>> driving claude -p on {args.instance_id}…", file=sys.stderr)
    sh(f"cat {shlex.quote(str(work / 'prompt.txt'))} | "
       f"claude -p{model} --output-format text --dangerously-skip-permissions",
       cwd=repo, env=env, check=False)

    # 6. verify red→green
    green = run_tests(repo, env, f2p)
    print(f"\n>> result: FAIL_TO_PASS now {'PASS ✅' if green else 'still FAIL ❌'}", file=sys.stderr)

    # 7. profile the transcript cc_trace just produced. Claude Code encodes the
    # cwd into the project-dir name by replacing /, _ and . with '-', but rather
    # than mirror that fragile rule we just take the newest transcript written
    # since the run started, across all project dirs.
    projects = Path.home() / ".claude" / "projects"
    cands = sorted((c for c in projects.glob("*/*.jsonl")
                    if c.stat().st_mtime >= t0 - 5),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        print(f"!! no transcript found under {proj}", file=sys.stderr)
        return 0
    tr = cands[0]
    rep = Path(args.reports_dir)
    rep.mkdir(parents=True, exist_ok=True)
    stem = f"swe-{args.instance_id}" + (f"-{args.model}" if args.model else "")
    sh(f"cd {shlex.quote(str(REPO_ROOT))} && python3 -m cc_trace {shlex.quote(str(tr))} "
       f"-o {shlex.quote(str(rep / (stem + '.html')))} --json", check=False)
    sh(f"cd {shlex.quote(str(REPO_ROOT))} && python3 -m cc_trace flame {shlex.quote(str(tr))} "
       f"--view time -o {shlex.quote(str(rep / (stem + '-flame.html')))}", check=False)
    print(f"\nDone. transcript={tr}\n  reports/{stem}.html  reports/{stem}-flame.html  "
          f"(green={green})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
