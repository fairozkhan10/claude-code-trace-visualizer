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

Six headless runs (`claude -p`), profiled from their transcripts with
`python -m cc_trace`. Targets were five real, independent code repositories
(kept anonymous here — a CSV dataset project, two general code projects for
search/refactor, and two service-style codebases for debugging), so each task
type is measured on **two different repos** (except the single data task). One
target (repo B, refactor) was additionally run **twice** — the same task on the
same clean-reset repo — as a run-to-run reproducibility check (see *Replication*).

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
| search/refactor | B (rep 2) | 12 | 25 | 70 | 2.01 | 0.64 | **0.55** | 0.98 | Bash | `EEEEEEEXXXX` |
| search/refactor | C | 7 | 18 | 74 | 2.20 | 0.67 | **0.45** | 0.96 | Bash | `EEEXEX` |
| bug fix | D | 34 | 60 | 606 | 9.20 | 0.41 | **0.09** | 1.00 | Bash | `EEEXXXXEEEXXXEXXXXEXEXXEEXEEXXEXXX` |
| bug fix | E | 23 | 51 | 518 | 5.12 | 0.39 | **0.14** | 0.99 | Bash | `EEXXEXXEEXEXXXXEEXXXXEX` |

Token totals (output / cache-read / fresh-input):

| task | repo | output | cache-read | fresh-input |
|---|---|---:|---:|---:|
| data | A | 1,874 | 83,289 | 5,334 |
| refactor | B | 9,871 | 419,652 | 9,206 |
| refactor | B (rep 2) | 8,508 | 414,835 | 8,009 |
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

A second, independent metric agrees. **Purity** — how cleanly the run splits at
its best explore→execute crossover point (1.0 = a perfect read-then-write shift,
~0.5 = fully interleaved) — is **0.93 / 0.83 for refactor** vs **0.68 / 0.70 for
bug fixing**. So both `sep` (separation of the two phases) and `purity`
(crispness of the single transition) draw the same line between task types.

**Run-to-run reproducibility.** Re-running the *identical* refactor task on the
same repo (B), reset to a clean tree, reproduced the pattern tightly: `sep`
0.48 → 0.55 and `purity` 0.93 → 1.00 (the second run produced the cleanest
sequence in the whole set, `EEEEEEEXXXX`). So the explore→execute signal for a
given task type is **stable across repeated runs**, not an artifact of a single
trace — important before reading anything into the cross-task contrast.

Pooling the runs, the two task families now separate **without overlap**: every
refactor run (`sep` 0.45/0.48/0.55, `purity` 0.83/0.93/1.00) sits above every
bug-fix run (`sep` 0.09/0.14, `purity` 0.68/0.70). The gap is large and clean,
but note (see *Limitations*) that with 3 vs 2 runs even a perfect split is not
yet statistically significant.

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

**4. Decode-intensity scales with task type — the same axis again.** The paper's
third pillar is that agentic workloads are *decode-dominated*. The honest picture
here is two-sided. Counting all tokens fed to the model, decode is a tiny fraction
— because (finding 1) ≥94% of context is reused KV-cache that costs no prefill. So
the meaningful question is decode *relative to the prefill work that actually runs*
(fresh input + cache-writes; cache-reads are free reuse). On that axis a clear
gradient appears:

| task | output : fresh-input | decode share of prefill-work |
|---|---:|---:|
| data (A) | 0.35 | 8% |
| refactor (B / B rep2 / C) | 1.07 / 1.06 / 0.88 | 17% / 17% / 14% |
| bug fix (D / E) | 7.50 / 2.09 | 30% / 23% |

Bug-fixing emits **2–7.5× its fresh input** in generated tokens; refactor ~1×;
the data task <0.5×. So decode-intensity tracks the *same* task-dependence axis as
`sep`/`purity`: the iterative debugging loop is not just longer, it is
proportionally far more generation-heavy. Two caveats keep this honest — (a) by raw
token count the workload is still prefill-heavy once cache-reads are included, so
"decode-dominated" holds in the *compute/latency* sense (decode is memory-bound and
per-token, far costlier than the parallel prefill it's measured against) rather than
the token-count sense; (b) these are list-price token totals, not measured GPU time.

## Limitations

- **n is small** (2–3 runs per task type, one model). The refactor and bug-fix
  groups now separate with **no overlap** on either metric, and the signal
  reproduces run-to-run — but this is still directional, not significant: with
  3 vs 2 runs a Mann–Whitney U test bottoms out at p ≈ 0.20 even for a perfect
  split, so significance is unreachable at this n regardless of separation.
  Reaching p < 0.05 needs roughly **≥4 runs per group** (a non-overlapping 4 vs 4
  gives p ≈ 0.014). Next step: 1–2 more bug-fix reps on clean fresh targets to
  cross that threshold, then a second model for cross-model generalization.
- **Synthetic benchmark tasks** on heterogeneous repos; task difficulty isn't
  controlled across repos.
- **Bash file I/O** is now parsed from the command string (output redirects,
  here-docs, `tee`, script runs), so `file_access` no longer ignores the shell —
  important because Claude does most of its file I/O through Bash, not the
  Write/Edit tools. It remains heuristic: file reads/writes that happen *inside*
  an inline `python -c`/`node -e` script (e.g. `open(..., "w")`) are still
  invisible, and `sed -i` in-place edits aren't counted.
- Durations are wall-clock (include permission/queue waits); costs are list-price
  estimates from `cc_trace/cost.py`.

## Reproduce

```bash
# one run per task (writes a transcript Claude Code can profile)
scripts/profile_task.sh --prompt-file tasks/02-search-refactor.md
# …profile each, then roll them up:
python -m cc_trace compare reports/*.json -o reports/compare.html
```
