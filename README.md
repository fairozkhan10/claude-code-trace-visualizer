# Claude Code Trace Visualizer

A small **workload profiler** for [Claude Code](https://claude.com/claude-code).
It answers one question:

> When Claude Code runs on a task, what exactly does it do, in what order, how
> long does each step take, how many tokens/dollars does it burn, and where does
> it fail or retry?

It parses a Claude Code session transcript and renders a **self-contained,
offline HTML dashboard**: a timeline of tool calls, a read/explore → execute/write
phase view, per-turn token & context growth, a tool-call breakdown, a file-access
table, a file co-access graph, detected retry loops, and an errors/retries list.

👉 Open [`examples/example-report.html`](examples/example-report.html) in a
browser to see a sample report. Regenerate it any time with
`python examples/make_sample.py && python -m cc_trace examples/sample-session.jsonl -o examples/example-report.html`.

## Why

This is the measurement layer for a summer research direction on **agentic AI
workload characterization** (with Shawn Zhong / Caeden Whitaker, UW–Madison).
It's directly motivated by *Agentic AI Workload Characteristics* (Yuan, Nayak,
Kundu, Talati, 2026), which finds that agentic workloads:

- are **decode-dominated** and reuse most input tokens across turns via
  **KV-cache** state (context grows over the run);
- move through **distinct temporal phases** — *read/explore* early, then
  *execute/write* later;
- need serving systems that jointly handle model re-entry, persistent context,
  and **workload-dependent tool behavior**.

That paper studied ReAct-style agents on Gemma/Qwen. This tool measures the same
characteristics for a *real* production agent (Claude Code) so we can see how
well those findings transfer before doing any optimization work.

📊 **First results:** see [`FINDINGS.md`](FINDINGS.md) — across 5 benchmark runs,
KV-cache reuse holds universally (≥94% of context reused per turn), but the
explore→execute phase shift is *task-dependent*: clean for search/refactor,
an interleaved loop for debugging.

## What it measures

Per session, extracted straight from the transcript (no instrumentation needed):

| Signal | Source |
| --- | --- |
| Tool calls, order, arguments | `assistant` → `tool_use` blocks |
| Per-call duration | `tool_result` timestamp − `tool_use` timestamp |
| Success / failure (retry candidates) | `tool_result.is_error` |
| Files touched (read vs. write) | tool input `file_path`, **plus shell redirects / here-docs / `tee` / script runs parsed from Bash commands** |
| Tokens: input / output / cache-read / cache-write | `message.usage` per turn |
| Context growth | cumulative cache-read + input per turn |
| Estimated cost (USD) | usage × per-model price table |
| Phase (explore vs. execute) | tool name + read-only shell heuristic |

Bash calls are sub-classified: read-only commands (`ls`, `grep`, `git status`,
…) count as *explore*; mutating ones count as *execute*. File I/O done through the
shell — output redirects (`>`, `>>`), here-docs, `tee`, and reading/running a
script — is parsed out of the command string so `file_access` isn't under-counted
(agents lean on Bash over the Write/Edit tools).

## Install

No dependencies — Python 3.9+ standard library only.

```bash
git clone https://github.com/fairozkhan10/claude-code-trace-visualizer
cd claude-code-trace-visualizer
# optional: pip install -e .   # gives you a `cc-trace` command
```

## Usage

```bash
# profile the most recent Claude Code session
python -m cc_trace --latest --open

# list available sessions
python -m cc_trace --list

# profile a specific transcript or session id, also dump the parsed JSON
python -m cc_trace 20814a75 -o reports/run.html --json
```

Transcripts live at `~/.claude/projects/<project>/<session>.jsonl`; this tool
reads them read-only.

### Compare runs

Once you've profiled a few tasks (each `--json` dump, or just their transcripts),
roll them up into one cross-run comparison:

```bash
python -m cc_trace compare reports/*.json -o reports/compare.html
```

It prints a comparison table and writes an HTML rollup with, per run, the
phase mix, the **explore→execute separation** (`sep`), the tool mix, and the
**cache-read share** (the KV-cache-reuse / decode-dominated signal):

```
run                     calls  turns  dur(s)  cost$  expl%  sep   cache%  top tool
data task               3      7      48      0.68   0.67   0.75  0.94    Bash
file search / refactor  15     27     75      2.24   0.80   0.48  0.98    Bash
coding bug fix          34     60     606     9.20   0.41   0.09  1.00    Bash
```

`sep` = mean execute position − mean explore position over the run. A high `sep`
is a clean **explore→execute phase shift** (search/refactor); a `sep` near zero
means explore and execute **interleave** in a reproduce→fix→test loop (debugging).
That is: the paper's clean phase shift holds for some task types but not all.

### Run a fresh benchmark task and profile it

```bash
scripts/profile_task.sh --prompt-file tasks/01-bugfix.md
```

This runs Claude Code headless (`claude -p`) on a task, then profiles the trace.
See [`tasks/`](tasks/) for the fixed benchmark prompts (bug fix, search/refactor,
data task) used to keep runs comparable.

## Output

- **`<session>.html`** — the interactive dashboard (open straight from disk).
- **`<session>.json`** (with `--json`) — the parsed trace + all derived summaries,
  for further analysis in a notebook.

## Project layout

```
cc_trace/
  parser.py    # transcript JSONL -> structured Trace (tool calls, turns, tokens)
  cost.py      # per-model USD price table + per-turn cost
  report.py    # Trace -> self-contained HTML dashboard (inline SVG/JS)
  compare.py   # cross-run rollup: phase shift, tool mix, cache share
  cli.py       # `python -m cc_trace` entry point
scripts/profile_task.sh   # run a task headless, then profile it
tasks/                    # fixed benchmark prompts
examples/                 # a committed example report + json
```

## Roadmap

- [x] Aggregate **across** sessions/tasks/models for comparison charts (`compare`)
- [x] Track files touched via **Bash** redirects (`>`, heredocs, `tee`, script
      runs), not just Read/Edit/Write inputs — agents lean on Bash
- [x] File-access **graph** (which files co-occur in a run)
- [x] Detect retry **loops** (same tool+target failing repeatedly)
- [x] Phase-transition metric — explore→execute **crossover** point + a `purity`
      score for how cleanly the run splits into the two phases
- [ ] Parse `--output-format stream-json` live for in-flight profiling

## Notes & limitations

- Durations are wall-clock between a tool call and its result; they include any
  queuing/permission wait, not just tool execution.
- Cost numbers are **estimates** from list prices in `cc_trace/cost.py` — edit
  that table to match current/your rates.
- Token usage may double-count if the transcript records per-iteration `usage`;
  treat totals as indicative.
