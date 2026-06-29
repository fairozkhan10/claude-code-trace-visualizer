# Claude Code Trace Visualizer

**See exactly what Claude Code did on a task** — every tool call, in order, with
timing, tokens, dollars, network requests, and where it got stuck.

Point it at a session and it renders one **self-contained, offline HTML
dashboard**: a timeline of tool calls, a read/explore → execute/write phase view,
per-turn token & context growth, a tool-call breakdown, a file-access table, a
**network-activity** panel, a file co-access graph, detected retry loops, a
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
| **[`FINDINGS.md`](FINDINGS.md)** | the research — 9 findings, validated against an eBPF tracer, SWE-bench, Terminal-Bench, a second model, and a peer tool |
| **[`REPORT.md`](REPORT.md)** | a short, honest project report: the brief, what got built, what we learned |
| `examples/` | runnable sample outputs (`example-report.html`, `example-flame.html`) — open without running anything |

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
tasks/                    # fixed benchmark prompts
examples/                 # a committed example report + json
```

## Notes & limitations

- **Durations are wall-clock** between a tool call and its result — they include
  any queue/permission wait, not just execution time.
- **Costs are estimates** from list prices in `cc_trace/cost.py`; edit that table
  for current or your own rates.
- **Heuristic parsing.** File I/O inside an inline `python -c` / `node -e` script,
  and `sed -i` in-place edits, aren't counted; token totals can double-count if the
  transcript records per-iteration `usage`. Treat totals as indicative.
