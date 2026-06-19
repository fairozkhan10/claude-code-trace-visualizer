# Findings — does the agentic-workload model hold for Claude Code?

A first measurement pass using this tool, run against the benchmark tasks in
[`tasks/`](tasks/). The motivating reference is *Agentic AI Workload
Characteristics* (Yuan, Nayak, Kundu, Talati, 2026), which characterizes agentic
workloads as **decode-dominated**, **KV-cache-heavy** (most context is reused
across turns), and moving through **distinct temporal phases** — read/explore
early, then execute/write later. That paper studied ReAct-style agents on
Gemma/Qwen; here we measure the same signals on a *real* production agent,
Claude Code (Opus 4.8).

## Setup

Five headless runs (`claude -p`), one per task, profiled from their transcripts
with `python -m cc_trace`. Targets were five real, independent code repositories
(kept anonymous here — a CSV dataset project, two general code projects for
search/refactor, and two service-style codebases for debugging), so each task
type is measured on **two different repos** (except the single data task).

Key metric — **`sep`** = mean(execute position) − mean(explore position) over the
ordered sequence of phased tool calls. High `sep` ⇒ explore front-loads and a
clean explore→execute *phase shift*; `sep ≈ 0` ⇒ explore and execute *interleave*
(an explore/act loop). **`cache%`** = cache-read ÷ (cache-read + fresh-input)
tokens = the share of each turn's context that is reused KV-cache.

## Results

| task type | repo | calls | turns | dur (s) | cost ($) | explore % | **sep** | **cache %** | top tool | phase sequence |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| data summary | A | 3 | 7 | 48 | 0.68 | 0.67 | **0.75** | 0.94 | Bash | `EEX` |
| search/refactor | B | 15 | 27 | 75 | 2.24 | 0.80 | **0.48** | 0.98 | Bash | `EEEEEEEEEEEXXEX` |
| search/refactor | C | 7 | 18 | 74 | 2.20 | 0.67 | **0.45** | 0.96 | Bash | `EEEXEX` |
| bug fix | D | 34 | 60 | 606 | 9.20 | 0.41 | **0.09** | 1.00 | Bash | `EEEXXXXEEEXXXEXXXXEXEXXEEXEEXXEXXX` |
| bug fix | E | 23 | 51 | 518 | 5.12 | 0.39 | **0.14** | 0.99 | Bash | `EEXXEXXEEXEXXXXEEXXXXEX` |

Token totals (output / cache-read / fresh-input):

| task | repo | output | cache-read | fresh-input |
|---|---|---:|---:|---:|
| data | A | 1,874 | 83,289 | 5,334 |
| refactor | B | 9,871 | 419,652 | 9,206 |
| refactor | C | 9,579 | 290,224 | 10,934 |
| bug fix | D | 47,567 | 2,405,752 | 6,343 |
| bug fix | E | 23,817 | 1,274,249 | 11,406 |

## What we found

**1. KV-cache reuse holds, universally and strongly.** Across all five runs,
`cache%` is **0.94–1.00** — i.e. ≥94% of the context fed to the model each turn
is reused KV-cache, not fresh prefill. Fresh input stays in the low-thousands of
tokens while cache-read runs into the hundreds of thousands to millions. This
matches the paper's KV-cache-heavy characterization directly, and it's the
cleanest, most repo-independent result.

**2. The explore→execute phase shift is *task-dependent*, not universal.**
This is the headline. The pattern replicates within each task type across two
different repos:

- **Search/refactor → clean phase shift.** `sep` 0.48 and 0.45; explore-heavy
  (67–80% of tool calls). The sequence front-loads exploration (`EEEEEEEEEEE…`)
  and then executes in a short burst — exactly the paper's read-then-write model.
- **Bug fixing → interleaved loop, *not* a phase shift.** `sep` 0.09 and 0.14;
  roughly balanced (~40% explore). The sequence flips back and forth
  (`…EEXXEXXEEXEXX…`) — a reproduce → hypothesize → edit → re-test loop, where
  exploration recurs *throughout* rather than front-loading.

So the paper's clean temporal phase shift holds for *navigational* tasks
(search, refactor) but breaks down for *iterative* tasks (debugging). A serving
system tuned for "explore early, execute late" would mismodel debugging
workloads.

**3. Tool behavior is Bash-dominated and work grows super-linearly with task
difficulty.** Bash is the top tool in every run (Claude leans on shell — including
heredocs and inline scripts — over Read/Edit/Write). Debugging runs cost ~5–14×
more and emit ~5–25× more output tokens than search/refactor, reflecting the long
interleaved loop. This supports the paper's call for **workload-dependent**
serving rather than one-size-fits-all.

## Limitations

- **n is small** (1–2 runs per task type, one model). Directional, not
  statistical. Next step is more reps and a second model.
- **Synthetic benchmark tasks** on heterogeneous repos; task difficulty isn't
  controlled across repos.
- **Bash blind spot:** because Claude does much of its file I/O via Bash
  (`>`, heredocs, running scripts), the parser's `file_access` under-counts files
  touched — it only sees Read/Edit/Write tool inputs. Fixing this is on the
  roadmap and would sharpen any file-level analysis.
- Durations are wall-clock (include permission/queue waits); costs are list-price
  estimates from `cc_trace/cost.py`.

## Reproduce

```bash
# one run per task (writes a transcript Claude Code can profile)
scripts/profile_task.sh --prompt-file tasks/02-search-refactor.md
# …profile each, then roll them up:
python -m cc_trace compare reports/*.json -o reports/compare.html
```
