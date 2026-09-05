# Claude Code Trace Visualizer

**See exactly what Claude Code did on a task** — every tool call, in order, with
timing, tokens, dollars, network requests, and where it got stuck.

Point it at a session and it renders one **self-contained, offline HTML
dashboard**: a timeline of tool calls, a read/explore → execute/write phase view,
per-turn token & context growth, a tool-call breakdown, a file-access table, a
**network-activity** panel, a **benchmark-validity audit** (flags an agent
fetching a fix's provenance, instance-id leaks, work stranded in `git stash`, and
writes to the graded tests — see FINDINGS finding 11), a file co-access graph,
detected retry loops, a
**repeated-work** panel (identical or near-identical calls the agent re-issues — a
caching/optimization signal), and an
errors list. No instrumentation, no services, no dependencies — it just reads the
transcript Claude Code already writes.

---

## Quick start (about 60 seconds)

No dependencies — **Python 3.9+ standard library only**.

```bash
git clone https://github.com/fairozkhan10/claude-code-trace-visualizer
cd claude-code-trace-visualizer

# 1. see a sample dashboard (synthetic data, safe to open)
open examples/example-report.html          # macOS  (Linux: xdg-open)

# 2. profile your most recent real Claude Code session
python3 -m cc_trace --latest --open
```

That's it. Step 2 finds your latest session under `~/.claude/projects/`, profiles
it, and pops the report open in your browser.

> On systems where `python` already means Python 3, you can drop the `3`. If you
> prefer a short command, `pip install -e .` gives you a `cc-trace` binary.

---

## Everyday usage

```bash
# list the sessions available to profile (newest first)
python3 -m cc_trace --list

# profile a specific session (by id prefix) or a transcript path,
# and also dump the parsed data as JSON for your own analysis
python3 -m cc_trace 20814a75 -o reports/run.html --json --open
```

Transcripts live at `~/.claude/projects/<project>/<session>.jsonl`. The tool only
ever **reads** them.

### Compare several runs

Profiled a few tasks? Roll them into one cross-run table:

```bash
python3 -m cc_trace compare reports/*.json -o reports/compare.html

# name the rows yourself — SWE-bench fixtures all live in `<task>/repo`, so the
# auto-labels collapse to "repo" and a cross-model table becomes unreadable
python3 -m cc_trace compare a.json b.json --label opus --label fable -o out.html
```

You get one row per run with its phase mix, the **explore→execute separation**
(`sep`) and **`purity`**, cache-reuse %, network-request count, and top tool:

```
run                     calls  dur(s)  cost$  sep   pure  cache%  net  top tool
file search / refactor  15     75      0.79   0.48  0.93  0.98    —    Bash
coding bug fix          34     606     5.05   0.09  0.68  1.00    2    Bash
```

`sep` (and the more robust `purity`) measure phase structure: **high = a clean
"explore first, then execute" shift; near zero = explore and execute interleave**
in a reproduce→fix→test loop. Which one you see depends on *task type × difficulty*
— see [`FINDINGS.md`](FINDINGS.md).

### Is a cross-run difference real? (`stats`)

`compare` shows that two groups of runs *differ*. `stats` asks whether the gap
survives the sample sizes these benchmarks can afford:

```bash
python3 -m cc_trace stats r1.json r2.json r3.json r4.json r5.json r6.json \
    --group opus --group opus --group opus \
    --group fable --group fable --group fable
```

Exact permutation Mann-Whitney (it enumerates the null rather than assuming
normality, and handles the ties that phase purity produces), **Cliff's delta**
for effect size, seeded bootstrap CIs on the difference of medians, and
Holm adjustment across metrics.

Its most useful output is a warning about your *design*, not your data:

```
!! UNDERPOWERED BY DESIGN — n=3 vs 3 puts the smallest reachable two-sided p at 0.100.
   No p here can clear 0.05 no matter how large the effect.
   n=4 per group is the smallest balanced design that can reach p<0.05.
```

With three runs per group the permutation null has only `C(6,3)=20` splits, so
`p ≥ 0.1` **whatever the numbers are** — "not significant" would describe the
experiment, not the models. At n=1 the tool refuses to report a delta or a CI at
all, since both are ±1 and degenerate by construction. Bootstrap and Monte-Carlo
paths are seeded, so a figure in a write-up reproduces exactly.

### Flame graph (phase → tool → target)

Render a run as a flame graph, stacked **`phase → tool → target`** and *coloured by
the explore→execute phase* — so a front-loaded refactor (a wide blue `explore` base)
and an interleaved debug (blue/orange shredded together) look different at a glance:

```bash
# self-contained interactive HTML (click to zoom, hover for share)
python3 -m cc_trace flame 20814a75 --view time -o reports/flame.html --open

# …or folded stacks for speedscope / flamegraph.pl / inferno
python3 -m cc_trace flame 20814a75 --view tokens -o reports/flame.folded

# aggregate several runs into one graph (a `run` frame is added as the root)
python3 -m cc_trace flame reports/*.jsonl --view calls -o reports/flame.html
```

`--view` sets what frame *width* means: `calls` (default), `time` (seconds),
`tokens` (output tokens), `files`, or `net`. Sample:
[`examples/example-flame.html`](examples/example-flame.html).

Bash leaves are **normalised**, not raw commands: arguments collapse to
placeholders while the part that says *what ran* survives, so repeated work
stacks into one frame instead of fanning out into near-identical slivers.

| raw command | flame leaf |
| --- | --- |
| `/tmp/venv/bin/python script.py > out.log` | `python PATH > PATH` |
| `timeout 300 /tmp/venv/bin/python -m pytest tests/a.py` | `timeout N python -m pytest PATH` |
| `pytest tests/b.py -k 'bar' --maxfail 5` | `pytest PATH -k STR --maxfail N` |
| `grep -n 'is_finite' core/assumptions.py 2>/dev/null` | `grep -n STR PATH 2>/dev/null` |

Paths and globs become `PATH`, numbers `N`, quoted strings `STR`; a command keeps
its basename in command position (so `/usr/bin/grep` and `grep` cluster), and
redirections stay readable (`2>&1`, not `N>&N`). The same signature drives the
repeated-work detector, and it is computed from the **full** command at parse
time — the 80-char display label is only a fallback.

### Watch a run live (in-flight)

Profile a run *as it happens* instead of after the fact:

```bash
# let the tool launch Claude Code for you
python3 -m cc_trace live "fix the failing test in foo.py" -o reports/live.html

# …or pipe an existing stream-json run into it
claude -p "…" --output-format stream-json --verbose | python3 -m cc_trace live -
```

Each tool call prints as it streams; the HTML is written when the run ends.
(Stream events carry no transcript timestamp, so durations are measured from event
**arrival** time.)

### Run a fresh benchmark and profile it

```bash
scripts/profile_task.sh --prompt-file tasks/01-bugfix.md
```

Runs Claude Code headless (`claude -p`) on a fixed prompt, then profiles the
result. See [`tasks/`](tasks/) for the benchmark prompts used to keep runs
comparable.

### Network-isolated benchmark runs

[Finding 11](FINDINGS.md) found a capable model "solving" a SWE-bench instance
by downloading the upstream fix, and that de-identifying the fixture doesn't
stop it — the model re-derived the PR number from the issue text. Only network
isolation closes that. Full isolation is impossible (the agent needs the model
API), so this is an **egress allowlist**: the agent runs on an `--internal`
Docker network with no DNS and no route, reaching the outside only through a
stdlib proxy that permits the model API and 403s everything else.

```bash
# 1. build a red, de-identified fixture image (needs network: clone + pip)
scripts/isolated_setup.sh <fixture-dir> test_infinity test_neg_infinity

# 2. graded run with no egress but the model API
scripts/isolated_run.sh <prompt-file> opus

# replications in parallel — TAG namespaces the containers and networks
TAG=-a scripts/isolated_run.sh <prompt-file> opus reports/run-a &
TAG=-b scripts/isolated_run.sh <prompt-file> opus reports/run-b &
wait
```

Replications are independent and **~90% of a run's wall-clock is the agent
re-running test suites locally**, not model latency — so running two or three at
once is the one lever that meaningfully shortens a batch. Each `TAG` gets its own
`--internal` network rather than sharing one: concurrent agents must not be able
to reach each other, or the isolation being claimed isn't the isolation being
run. Watch memory rather than CPU — Docker Desktop's VM has a fixed RAM
allocation, and a heavy suite can take ~1.5 GB per run.

The proxy logs every attempt, so `egress.jsonl` records what the agent *tried*
to fetch — a signal the transcript can't give you, since a blocked request may
never surface as a tool call. The run also grades `FAIL_TO_PASS`, checks `git
stash` for finding 11's other failure mode (a correct fix left stranded), and
profiles the transcript.

**Test integrity.** Before grading, the run verifies the graded test files
against a baseline copy the fixture image carries, restores any that changed,
and records the result in `test-integrity.txt` — a grade is only meaningful if
the tests are the ones the fixture shipped. This can't be done with git: the
setup script applies the instance's test patch to the *working tree* and never
commits it, so `HEAD` holds the pre-patch tests and `git status` cannot tell the
harness's own edits apart from the agent's. (Fixtures built before this check
report `NO BASELINE` and grade as `UNVERIFIED` rather than pretending.)

Requires Docker and a Claude Code OAuth token — no `ANTHROPIC_API_KEY`; on
macOS the token is read from the Keychain automatically. Note the agent sees an
HTTP 403 rather than a black hole, so this measures *retrieval denied*, not
*retrieval absent*.

Read the log back with:

```bash
python3 scripts/egress_audit.py reports/<run>/egress.jsonl \
    --transcript reports/<run>/transcript.jsonl
```

which splits connections into **model** / **vendor** / **agent** classes, lists
the agent-initiated denials, and scores `cc_trace`'s command-string network
parsing against what the proxy actually saw.

**Docker isn't the only way to enforce this.** The proxy is plain stdlib and
doesn't care what confines the agent; Docker only supplies the *enforcement*
(an `--internal` network leaves no route to bypass the proxy with). Two
alternatives, neither adopted here:

* `HTTPS_PROXY`/`HTTP_PROXY` alone, no container — trivial to set up, but it is
  a *convention*, not a boundary: anything opening a socket directly ignores it,
  so a null result would prove nothing.
* macOS `pf` with a `user` rule (`pfctl` supports per-uid filtering): run the
  agent as a dedicated uid, block its outbound traffic except to the proxy port.
  Real enforcement without Docker, at the cost of `sudo` and editing the host's
  live firewall — a mistake takes the whole machine's networking with it,
  whereas a container confines the blast radius. On Linux the equivalent
  (`unshare -n` plus a veth pair) is cheaper and worth preferring.

Docker also gives the fixture image a byte-identical rebuild, which removes a
confound when comparing runs — the reason it stayed.

---

## What it measures

Everything below is pulled straight from the transcript — nothing is instrumented:

| Signal | Where it comes from |
| --- | --- |
| Tool calls — order & arguments | `assistant` → `tool_use` blocks |
| Per-call duration | `tool_result` time − `tool_use` time |
| Success / failure (retry candidates) | `tool_result.is_error` |
| Files touched (read vs. write) | tool `file_path` **+ shell redirects / here-docs / `tee` / script runs parsed from Bash** |
| **Network activity** | curl/wget, git remote ops, package installs, ssh/scp, WebFetch/WebSearch/MCP — parsed from commands & tool inputs |
| **Benchmark-validity flags** | solution-channel network (PR diffs, commit searches against the repo under test), instance-id leaks in path/prompt, work stranded in `git stash`, and **writes to the tests the run is graded on** (`--graded-test`, e.g. a SWE-bench `f2p.txt`) — the finding-11 failure modes, flagged for review |
| Tokens (input / output / cache-read / cache-write) | `message.usage` per turn |
| Context growth | cumulative cache-read + input per turn |
| Estimated cost (USD) | usage × per-model price table |
| Phase (explore vs. execute) | tool name + read-only-shell heuristic |

Two things are worth knowing about the heuristics:

- **Bash is treated as a first-class citizen.** Read-only commands (`ls`, `grep`,
  `git status`, …) count as *explore*; mutating ones as *execute*. File I/O done
  through the shell (redirects, here-docs, `tee`, running a script) is parsed out
  of the command string, because agents lean on Bash far more than Read/Edit/Write.
- **The network panel sees what the *agent* does, not the model.** It captures the
  network the agent reaches through its tools (curl, git, pip/npm/uv, ssh, web/MCP).
  It does **not** include Claude Code's own model-API calls — those never appear in
  the transcript (you'd need a proxy in front of the CLI to see them).

---

## Why this exists

This is the **measurement layer** for a summer research direction on *agentic AI
workload characterization* (with Shawn Zhong & Caeden Whitaker, UW–Madison),
motivated by *Agentic AI Workload Characteristics* (Yuan, Nayak, Kundu, Talati,
2026). That paper says agentic workloads are **decode-dominated**,
**KV-cache-heavy**, and move through **explore-then-execute phases** — but it
studied ReAct agents on Gemma/Qwen. This tool checks whether those claims hold for
a *real* production agent (Claude Code) before anyone optimizes for them.

📊 **Short version of what we found** (full write-up in [`FINDINGS.md`](FINDINGS.md)):
KV-cache reuse holds universally (≥95% of context reused per turn), but the
explore→execute phase shift is a **task-kind × difficulty interaction** —
refactoring stays cleanly front-loaded at any length, short debugging is clean too,
and only *long* debugging dissolves into an interleaved loop.

### Where to look (for reviewers)

| Doc | What's in it |
|---|---|
| **[`README.md`](README.md)** (this file) | what the tool is and how to run it |
| **[`FINDINGS.md`](FINDINGS.md)** | the research — 11 findings, validated against an eBPF tracer, SWE-bench Lite & Verified, Terminal-Bench, three models, and a peer tool |
| **[`REPORT.md`](REPORT.md)** | a short, honest project report: the brief, what got built, what we learned |
| `examples/` | sample outputs (`example-report.html`, `example-flame.html`, `terminal-bench-compare.html`, `swe-crossmodel-compare.html`) — open without running anything |

---

## Output & layout

Each run writes a `<session>.html` dashboard (open straight from disk) and,
with `--json`, a `<session>.json` of the parsed trace + all derived summaries for
notebook analysis.

```
cc_trace/
  parser.py    # transcript JSONL → structured Trace (tool calls, turns, tokens, network)
  cost.py      # per-model USD price table + per-turn cost
  report.py    # Trace → self-contained HTML dashboard (inline SVG/JS)
  compare.py   # cross-run rollup: phase shift, tool mix, cache share, network
  stream.py    # live profiling from --output-format stream-json
  cli.py       # `python3 -m cc_trace` entry point
scripts/profile_task.sh   # run a task headless, then profile it
scripts/egress_proxy.py   # stdlib allowlist proxy — logs what an agent tries to fetch
scripts/isolated_setup.sh # build a red, de-identified SWE-bench fixture image
scripts/isolated_run.sh   # graded run with no egress but the model API
scripts/egress_audit.py   # egress log → model/vendor/agent split + parser check
tasks/                    # fixed benchmark prompts
examples/                 # a committed example report + json
tests/                    # unittest suite (stdlib only) — see below
```

### Tests

The heuristics and metric definitions are pinned by a stdlib-only test suite —
table-driven cases for the Bash file/network parsing, the per-`message.id`
token-dedup invariant, purity/crossover math on known sequences, flame-graph
conservation checks, a golden-metric snapshot of the committed example, the
egress allowlist's host matching, and a guard that committed artifacts carry no
personal data. CI runs it on Python 3.9 and 3.13.

```bash
python3 -m unittest discover -s tests
```

## Notes & limitations

- **Durations are wall-clock** between a tool call and its result — they include
  any queue/permission wait, not just execution time.
- **Costs are estimates** from list prices in `cc_trace/cost.py`; edit that table
  for current or your own rates.
- **Heuristic parsing.** File I/O inside an inline `python -c` / `node -e` script,
  and `sed -i` in-place edits, aren't counted. Token totals are deduplicated per
  `message.id` (one assistant message spans several transcript lines that repeat
  the same usage) and were verified equal to the wire counts via a MITM capture —
  see FINDINGS finding 5.
