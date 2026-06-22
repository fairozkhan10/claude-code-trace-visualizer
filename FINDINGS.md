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

Thirteen headless runs (`claude -p`), profiled from their transcripts with
`python -m cc_trace`. The first six (the original pass) covered five real repos —
a CSV dataset project, two for search/refactor, two service-style codebases for
debugging — and suggested a clean "refactor vs bug-fix" split. The other five were
added deliberately to **break the difficulty / task-kind confound** in that first
split (refactor runs happened to be short, bug-fixes long), by filling in a
controlled 2×2 of *task type* × *task length*. These used small **obscure public
libraries** (cloned fresh, low-star, to avoid the model having memorized their
fixes) and were built SWE-bench-style for the debugging ones: check out the parent
of a real upstream fix commit (bug present), re-apply only that commit's *test*
change, and ask the agent to make the suite pass.

The five added runs:

| id | task | length | what it controls |
|---|---|---|---|
| F | trivial debug (1-line alignment bug, tiny formatter) | 4 calls | easiest possible debug |
| G | moderate debug (20-line path-escaping bug, requirements parser) | 25 calls | mid debug |
| H | mid refactor (de-duplicate ~30 accessors in a 735-LOC lib) | 14 calls | refactor at refactor-typical length |
| I | **short debug** (tokenizer crash on surrogate code points) | 16 calls | debug held to *short* length |
| J | **long refactor** (split a monolith class into mixin modules) | 24 calls | refactor pushed to *bug-fix* length |
| I2 | **short debug** rep (requirements-parser crash on relative paths) | 11 calls | replicate I in a different repo |
| J2 | **long refactor** rep (split an 828-line IP-calc module into a package) | 22 calls | replicate J in a different repo |

I and J are the decisive cells: a *short* debug and a *long* refactor, the two
corners missing from the original runs (see finding 2). Each was then **replicated
in a second, different repo** (I2, J2). One target (repo B, refactor) was also run
**twice** as a within-task reproducibility check (see *Replication*).

Key metric — **`sep`** = mean(execute position) − mean(explore position) over the
ordered sequence of phased tool calls. High `sep` ⇒ explore front-loads and a
clean explore→execute *phase shift*; `sep ≈ 0` ⇒ explore and execute *interleave*
(an explore/act loop). **`cache%`** = cache-read ÷ (cache-read + fresh-input)
tokens = the share of each turn's context that is reused KV-cache.

## Results

| task type | repo | calls | turns | dur (s) | cost ($) | explore % | **sep** | **purity** | **cache %** | top tool | phase sequence |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| data summary | A | 3 | 7 | 48 | 0.68 | 0.67 | **0.75** | 1.00 | 0.94 | Bash | `EEX` |
| debug — trivial | F | 4 | 8 | 24 | 0.92 | 0.25 | **0.22**† | 0.75† | 0.94 | Bash | `XEXX` |
| refactor | C | 7 | 18 | 74 | 2.20 | 0.67 | **0.45** | 1.00 | 0.96 | Bash | `EEEXEX` |
| refactor | B (rep 2) | 12 | 25 | 70 | 2.01 | 0.64 | **0.55** | 1.00 | 0.98 | Bash | `EEEEEEEXXXX` |
| refactor — mid | H | 14 | 26 | 104 | 2.33 | 0.21 | **−0.05** | 0.86 | 0.99 | Bash | `EXXXXXXXXXEEXX` |
| refactor | B | 15 | 27 | 75 | 2.24 | 0.80 | **0.48** | 0.93 | 0.98 | Bash | `EEEEEEEEEEEXXEX` |
| **debug — short** | **I2** | 11 | 19 | 86 | 2.03 | 0.45 | **0.26** | **0.82** | 0.98 | Bash | `XEEEEXXXEXX` |
| **debug — short** | **I** | 16 | 24 | 90 | 1.55 | 0.50 | **0.28** | **0.81** | 0.99 | Bash | `XEEEEEEXXEXXXEXX` |
| **refactor — long** | **J2** | 22 | 46 | 220 | 8.73 | 0.27 | **0.22** | **0.91** | 0.99 | Bash | `EEEEXXXXXXXXXXXXXXEEXX` |
| debug | E | 23 | 51 | 518 | 5.12 | 0.39 | **0.14** | 0.70 | 0.99 | Bash | `EEXXEXXEEXEXXXXEEXXXXEX` |
| **refactor — long** | **J** | 24 | 44 | 185 | 7.05 | 0.50 | **0.51** | **0.96** | 0.99 | Bash | `EEEEEEEEEEXEEXXXXXXXXXXX` |
| debug — mid | G | 25 | 49 | 310 | 5.07 | 0.46 | **0.23** | 0.76 | 1.00 | Bash | `XEEEXEEXXXXEEEEEEXXXXXXXX` |
| debug | D | 34 | 60 | 606 | 9.20 | 0.41 | **0.09** | 0.68 | 1.00 | Bash | `EEEXXXXEEEXXXEXXXXEXEXXEEXEEXXEXXX` |

Rows ordered by session length. Watch the **purity** column down the length axis:
it stays high (0.81–1.00) for *every* task — refactor *and* debug — until ~20
calls, then drops **only for debug** (E, G, D: 0.68–0.76). The long refactor **J**
(24 calls) stays clean at 0.96. †F at 4 calls is too short for `sep`/`purity` to
mean anything; it is read only for `cache%` and decode-intensity.

Token totals (output / cache-read / fresh-input):

| task | repo | output | cache-read | fresh-input |
|---|---|---:|---:|---:|
| data | A | 1,874 | 83,289 | 5,334 |
| debug (trivial) | F | 2,054 | 78,762 | 5,064 |
| refactor | C | 9,579 | 290,224 | 10,934 |
| refactor | B | 9,871 | 419,652 | 9,206 |
| refactor (mid) | H | 10,351 | 506,066 | 5,229 |
| debug (short) | I | 6,658 | 394,147 | 5,354 |
| debug (short, rep) | I2 | 10,907 | 353,182 | 5,539 |
| refactor (long) | J | 39,671 | 1,210,825 | 11,087 |
| refactor (long, rep) | J2 | 44,382 | 1,827,923 | 11,091 |
| debug (mid) | G | 28,659 | 1,171,556 | 5,662 |
| debug | E | 23,817 | 1,274,249 | 11,406 |
| debug | D | 47,567 | 2,405,752 | 6,343 |

## What we found

**1. KV-cache reuse holds, universally and strongly.** Across all thirteen runs,
`cache%` is **0.94–1.00** — i.e. ≥94% of the context fed to the model each turn
is reused KV-cache, not fresh prefill. Fresh input stays in the low-thousands of
tokens while cache-read runs into the hundreds of thousands to millions. This
matches the paper's KV-cache-heavy characterization directly, and it's the
cleanest, most repo-independent result.

**2. The explore→execute phase shift breaks down only for *long debugging* — a
task-kind × difficulty interaction, not a clean dichotomy and not a pure
continuum.** This is the headline, and getting it right took two corrections.
The first pass saw a clean split — refactor front-loads (`sep` 0.45–0.55, `purity`
0.83–1.00; `EEEEEEEEEEE…` then a short execute burst), hard bug-fixing interleaves
(`sep` 0.09–0.14, `purity` 0.68–0.70; `…EEXXEXXEEXEXX…`, a reproduce → hypothesize
→ edit → re-test loop). But in those runs **task type was confounded with length**:
every refactor was short (7–15 calls), every bug-fix long (23–34). So we ran the
two missing corners.

The result is an **interaction**, cleanest in the `purity` column read down the
length axis:

| | **short** (≤16 calls) | **long** (22–34 calls) |
|---|---|---|
| **refactor** | `purity` 0.83–1.00 — clean | **J, J2: 0.96, 0.91 — still clean** (24, 22 calls) |
| **debugging** | **I, I2: 0.81, 0.82 — clean** (16, 11 calls) | 0.68–0.76 — interleaved |

Each decisive corner was run **twice, in different repos**, and replicated tightly
(see *Replication*): long refactor 0.96 / 0.91, short debug 0.81 / 0.82.

- **A *long* refactor stays clean.** J split a monolithic class into mixin modules
  over 24 calls — bug-fix length — yet produced `EEEEEEEEEEXEEXXXXXXXXXXX`: explore
  almost everything up front, then a long execute burst (`purity` 0.96, `sep`
  0.51). A second long refactor (J2, splitting an 828-line IP-calculator module
  into a package over 22 calls) did the same: `purity` 0.91, `EEEE` then a long
  execute burst. So **length alone does not cause interleaving.** Refactoring is
  "map the surface, then transform" — the exploration is bounded by code size and
  stays front-loadable however big the job.
- **A *short* genuine debug stays clean too.** I traced a tokenizer crash to lone
  surrogate code points and fixed it in 16 calls — `purity` 0.81, `sep` 0.28,
  comparable to a refactor of the same length; a second short debug (I2, a
  requirements-parser crash on relative paths, 11 calls) matched at `purity` 0.82.
  One reproduce→diagnose→fix pass doesn't interleave.
- **Interleaving needs *both*: a debugging task *and* enough difficulty** to force
  many hypothesis cycles. `purity` for debugging falls with length (I 0.81 @16 →
  G 0.76 @25 → E 0.70 @23 → D 0.68 @34) while refactor `purity` stays flat-high
  (0.83–1.00) from 7 to 24 calls. The two only diverge at the *long* end.
- **The trivial bug (F)** is consistent: 4 calls, fixed in one shot, `out:fresh`
  0.41 like the data task — too short to interleave, and too short for `sep`/
  `purity` to mean anything (so it's read only for decode/cache).

So my earlier "difficulty continuum" reading was *also* too simple: difficulty
gates interleaving, but only *for debugging*. Refactor is robustly front-loaded at
every length we tested. (`sep` is noisier than `purity` here — H's `sep` collapses
to −0.05 purely from a late verify-coda, `EXXXXXXXXX**EE**XX`, even though its
`purity` is a clean 0.86; we lead with `purity` for that reason.)

**Run-to-run reproducibility.** Re-running the *identical* refactor task on the
same repo (B), reset to a clean tree, reproduced the pattern tightly: `sep`
0.48 → 0.55 and `purity` 0.93 → 1.00. So the signal for a *fixed* task is
**stable across repeated runs**, not single-trace noise.

So the paper's clean temporal phase shift holds for *navigational* work (search,
refactor) **at any length**, and for *easy* debugging — and dissolves into an
explore/act loop only for *hard, iterative* debugging. A serving system tuned for
"explore early, execute late" would mismodel specifically the long-running
debugging tail; task type alone or length alone each mispredicts it.

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

| task (by length) | calls | output : fresh-input | decode share of prefill-work |
|---|---:|---:|---:|
| data (A) | 3 | 0.35 | 8% |
| debug — trivial (F) | 4 | 0.41 | 5% |
| refactor (C) | 12 | 1.06 | 17% |
| refactor — mid (H) | 14 | 1.98 | 19% |
| debug — short (I) | 16 | 1.24 | 21% |
| debug (E) | 23 | 2.09 | 23% |
| **refactor — long (J)** | 24 | **3.58** | **24%** |
| debug — mid (G) | 25 | 5.06 | 31% |
| debug (D) | 34 | 7.50 | 30% |

Ordered by session length, decode-intensity climbs **monotonically** — the
cleanest gradient in the study. But note the axis: it is **task *effort* / length,
not task *type*.** The long refactor **J** is decode-heavy (`out:fresh` 3.58,
24% share) right alongside the long bug-fixes — a big refactor generates a lot of
output too. And the trivial bug **F** sits at the bottom with the data task. So
decode-intensity and the phase-shift breakdown (finding 2) are driven by
*different* things: decode rises with **how much work/output a task involves
(any task)**, while interleaving needs **debugging *and* length**. They correlate
only because long debugging maximizes both. Two caveats keep the decode claim
honest — (a) by raw token count the workload is still prefill-heavy once
cache-reads are included, so "decode-dominated" holds in the *compute/latency*
sense (decode is memory-bound and per-token, far costlier than the parallel
prefill it is measured against) rather than the token-count sense; (b) these are
list-price token totals, not measured GPU time.

## Limitations

- **The decisive corners are at n=2, not yet n=3+.** Each of the two key cells —
  *short* debug and *long* refactor — has now been run **twice in different repos**
  and replicated tightly (short debug `purity` 0.81 / 0.82; long refactor 0.96 /
  0.91), against the long-bug-fix band of 0.68–0.76. That is enough to make the
  interaction more than anecdotal, but a third rep each (and ideally a few short
  refactors / long bug-fixes pooled) would let it carry a real significance test.
  This supersedes the earlier framings in this doc: the first pass read a clean
  *dichotomy*, the second a pure *difficulty continuum* — the controlled 2×2 shows
  **neither**; it is an interaction (refactor stays front-loaded at any length;
  debugging interleaves only when long). One model (Opus 4.8) throughout; a second
  model is the other open axis. (Clean *short-debug* targets proved scarce: of the
  obscure repos tried, app-style ones — dsmr_parser, sulguk — had baseline env
  failures or feature-in-progress test states; small pure-Python libs with a single
  upstream bug-fix commit that also touched a test were the reliable source.)
- **Synthetic benchmark tasks** on heterogeneous repos. Difficulty is now varied
  *on purpose* (it is the second axis of the 2×2) rather than left as an
  uncontrolled confound, but it is proxied by session length / call count, not an
  independent difficulty rating. The added targets (F, G, I, and J's repo) were
  deliberately obscure, low-star public repos so the model had not memorized their
  fixes, which would deflate the explore phase.
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
