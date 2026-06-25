# Findings — does the agentic-workload model hold for Claude Code?

## Why we ran this

There's a 2026 paper — *Agentic AI Workload Characteristics* (Yuan, Nayak, Kundu,
Talati) — that makes three claims about how AI agents behave as a *compute*
workload: they're **decode-dominated**, they're **KV-cache-heavy** (most of the
context each turn is reused, not freshly read), and they move through **temporal
phases** — read/explore early, execute/write later. If those hold, you'd serve
agents very differently than chatbots.

But that paper studied small ReAct agents on Gemma/Qwen. **Does any of it transfer
to a real, frontier production agent?** This is a first measurement pass at that
question, using [`cc_trace`](README.md) to profile real Claude Code (Opus 4.8)
runs. Think of it as ground-truthing the paper before anyone optimizes for it.

## TL;DR

- **KV-cache reuse: confirmed, and it's the strongest signal.** ≥95% of context is
  reused every turn, across every run. Agents really are KV-cache-heavy.
- **The phase shift is more subtle than the paper says.** It's not a property of
  "agents" — it's a **task-kind × difficulty interaction**. Refactoring stays
  cleanly front-loaded *at any length*; short debugging is clean too; only *long,
  hard debugging* dissolves into an interleaved explore→act loop.
- **Decode-intensity scales with effort, not task type** — a long refactor is just
  as generation-heavy as a long bug-fix.
- **Cost is driven by context size, not output** — a corollary of the cache result.
- **Agents redo a real slice of their work** — ~16% of calls in refactoring, ~27% in
  debugging are near-duplicate repeats (re-editing a file, re-running a probe). A
  concrete caching/memoization target, biggest in debugging.
- **It holds on a standard benchmark and a second model.** SWE-bench Lite instances
  we *didn't* pick reproduce the clean short-task phase shift, and it's identical on
  Opus and Sonnet — the phase structure is a property of the task, not the model.

The middle finding is the interesting one, and it took two wrong turns to get
right (see finding 2). Below is how we got there.

## Setup

Thirteen headless runs (`claude -p`), each profiled from its transcript. The first
six were an exploratory pass over five real repos (a data task, two
search/refactor, two debugging) — and they *looked* like a clean "refactor vs
bug-fix" split. The catch: in those runs, **every refactor was short and every
bug-fix was long**, so "task type" and "difficulty" were hopelessly tangled.

So the other seven runs were designed to *untangle* them — a controlled 2×2 of
**task type × length**, using small **obscure public libraries** (cloned fresh,
low-star, so the model hadn't memorized their fixes). The debugging runs were built
SWE-bench-style: rewind a repo to just before a real upstream fix, re-apply only
that fix's *test*, and ask the agent to make the suite pass again.

| id | task | length | the cell it fills |
|---|---|---|---|
| F | trivial debug (1-line alignment bug) | 4 calls | easiest possible debug |
| G | moderate debug (20-line path-escaping bug) | 25 calls | mid debug |
| H | mid refactor (de-dup ~30 accessors) | 14 calls | refactor, typical length |
| **I** | **short debug** (tokenizer crash) | 16 calls | **debug, held short** |
| **J** | **long refactor** (split a monolith into modules) | 24 calls | **refactor, pushed long** |
| I2 | short-debug replication (different repo) | 11 calls | replicate I |
| J2 | long-refactor replication (different repo) | 22 calls | replicate J |

**I** and **J** are the whole ballgame — a *short* debug and a *long* refactor are
the two corners the original runs never had. Both were then replicated in a second
repo (I2, J2).

Two metrics do most of the work. **`sep`** = mean(execute position) −
mean(explore position): high means explore front-loads (a clean phase shift), ~0
means the two interleave. **`purity`** measures the same thing more robustly (how
cleanly the run splits at its best explore→execute crossover; 1.0 = perfect shift,
~0.5 = fully interleaved). **`cache%`** = the share of each turn's context that's
reused KV-cache.

## Results

| task type | repo | calls | dur (s) | cost ($) | **sep** | **purity** | **cache %** | phase sequence |
|---|---|---:|---:|---:|---:|---:|---:|---|
| data summary | A | 3 | 48 | 0.34 | 0.75 | 1.00 | 0.95 | `EEX` |
| debug — trivial | F | 4 | 24 | 0.50 | 0.22† | 0.75† | 0.96 | `XEXX` |
| refactor | C | 7 | 74 | 0.70 | 0.45 | 0.83 | 0.97 | `EEEXEX` |
| refactor | B (rep 2) | 12 | 70 | 0.93 | 0.55 | 1.00 | 0.99 | `EEEEEEEXXXX` |
| refactor — mid | H | 14 | 104 | 1.32 | −0.05 | 0.86 | 0.99 | `EXXXXXXXXXEEXX` |
| refactor | B | 15 | 75 | 0.79 | 0.48 | 0.93 | 0.98 | `EEEEEEEEEEEXXEX` |
| **debug — short** | **I2** | 11 | 86 | 1.15 | 0.26 | **0.82** | 0.99 | `XEEEEXXXEXX` |
| **debug — short** | **I** | 16 | 90 | 1.04 | 0.28 | **0.81** | 0.99 | `XEEEEEEXXEXXXEXX` |
| **refactor — long** | **J2** | 22 | 220 | 3.84 | 0.22 | **0.91** | 1.00 | `EEEEXXXXXXXXXXXXXXEEXX` |
| debug | E | 23 | 518 | 2.26 | 0.14 | 0.70 | 0.99 | `EEXXEXXEEXEXXXXEEXXXXEX` |
| **refactor — long** | **J** | 24 | 185 | 2.62 | 0.51 | **0.96** | 0.99 | `EEEEEEEEEEXEEXXXXXXXXXXX` |
| debug — mid | G | 25 | 310 | 2.58 | 0.23 | 0.76 | 1.00 | `XEEEXEEXXXXEEEEEEXXXXXXXX` |
| debug | D | 34 | 606 | 5.05 | 0.09 | 0.68 | 1.00 | `EEEXXXXEEEXXXEXXXXEXEXXEEXEEXXEXXX` |

Rows are ordered by length. **Read the `purity` column top to bottom:** it stays
high (0.81–1.00) for *every* task — refactor and debug alike — right up until ~20
calls, then drops **only for the debugging runs** (E, G, D at 0.68–0.76). The long
refactor **J** sails through at 0.96. That single pattern is finding 2.

(†F is too short at 4 calls for `sep`/`purity` to mean anything; it's read only for
cache and decode.)

> **Numbers note (v2, 2026-06-23).** The cost/token figures above were re-generated
> after fixing a token **double-count** in the parser: one assistant message spans
> several transcript lines, each repeating the same message-level `usage`, and the
> builder was summing per line — inflating absolute tokens and cost by ~1.5–3×. Counts
> are now taken once per `message.id` (verified equal to the wire totals via the MITM
> capture in finding 5). The earlier `$`/token numbers were correspondingly high. **All
> three headline findings are unaffected:** every *ratio*-based metric (`cache%`,
> `purity`, `sep`, decode-share, output:fresh-input) is robust to the bug, because
> numerator and denominator inflated together — only the absolute cost and token totals
> moved. Tool-call counts, durations, and phase sequences were never affected.

## What we found

### 1. KV-cache reuse holds — universally, strongly, and it explains the cost

`cache%` is **0.95–1.00 across all thirteen runs.** Fresh input stays in the low
thousands of tokens while cache-read runs into the hundreds of thousands to
*millions*. The paper's KV-cache-heavy claim is the cleanest, most
repo-independent result here — no asterisks.

A direct corollary, visible in the cost column: **what you pay tracks context
*accumulation*, not output.** The expensive runs aren't the ones that wrote the
most code — they're the ones that dragged the biggest reused context across the
most turns.

### 2. The phase shift is a task-kind × difficulty *interaction* (the headline)

This is the interesting one, and we got it wrong twice before getting it right.

**Wrong turn #1 — "it's a clean dichotomy."** The first six runs showed refactor
front-loading (`purity` 0.83–1.00, `EEEEEEEE…` then a short burst) and bug-fixing
interleaving (`purity` 0.68–0.70, `…EEXXEXXEEXEXX…`, a reproduce→hypothesize→edit
loop). Tidy — but confounded: every refactor was short, every bug-fix long.

**Wrong turn #2 — "it's just difficulty."** Adding a *trivial* bug (F, 4 calls)
that behaved like the easy data task suggested the real axis was difficulty/length,
and the labels were a proxy. Cleaner — but still wrong, because it predicts a *long
refactor* should interleave too.

**The actual answer.** Run the two missing corners and the picture resolves into an
interaction — clearest in `purity`:

| | **short** (≤16 calls) | **long** (22–34 calls) |
|---|---|---|
| **refactor** | 0.83–1.00 — clean | **J, J2: 0.96, 0.91 — still clean** |
| **debugging** | **I, I2: 0.81, 0.82 — clean** | 0.68–0.76 — interleaved |

Three of the four corners are clean. **Only long debugging breaks.** Both decisive
corners were run twice in different repos and replicated tightly (long refactor
0.96/0.91; short debug 0.81/0.82). What it means:

- **A long refactor stays front-loaded.** J split a monolithic class into modules
  over 24 calls — bug-fix length — and still produced `EEEEEEEEEE…` then a long
  execute burst. Refactoring is *"map the whole surface, then transform it"*: the
  exploration is bounded by the code and stays front-loadable no matter how big.
- **A short debug stays clean too.** One reproduce→diagnose→fix pass (I, 16 calls)
  doesn't have enough cycles to interleave.
- **Interleaving needs *both* ingredients** — a debugging task *and* enough
  difficulty to force many hypothesis cycles. Debug `purity` slides down with
  length (0.81 → 0.76 → 0.70 → 0.68); refactor `purity` doesn't budge.

So the paper's clean phase shift is real — but it's the rule for *navigational*
work (search, refactor) at any size and for *easy* debugging, and it breaks
specifically in the **long, iterative debugging tail**. A serving system tuned for
"explore early, execute late" would mismodel exactly that tail. *(We trust `purity`
over `sep` here: `sep` is fooled by late "verify" reads — e.g. H's `sep` is −0.05
purely from a re-check at the end, though its `purity` is a clean 0.86.)*

### 3. Bash dominates, and work grows super-linearly with difficulty

Bash is the top tool in **every** run — Claude reaches for the shell (including
here-docs and inline scripts) far more than Read/Edit/Write, which is exactly why
the parser works so hard to read file *and* network I/O out of command strings. And
the hard debugging runs (E, G, D) cost ~3–7× more and emit ~3–8× more output than the
short refactor runs: difficulty is expensive, not linearly.

### 4. Decode-intensity scales with *effort*, not task type

The paper's third pillar is decode-dominance. The honest version: by raw token
count the workload is *prefill*-heavy, because ≥95% of context is free cache reuse
(finding 1). The meaningful question is decode relative to the prefill work that
actually runs — fresh input + cache-writes. On that axis there's a clean gradient:

| task (by length) | calls | output : fresh-input | decode share of prefill-work |
|---|---:|---:|---:|
| data (A) | 3 | 0.46 | 11% |
| debug — trivial (F) | 4 | 0.48 | 6% |
| refactor (C) | 7 | 1.03 | 14% |
| refactor — mid (H) | 14 | 2.52 | 25% |
| debug — short (I) | 16 | 1.60 | 21% |
| debug (E) | 23 | 2.39 | 23% |
| **refactor — long (J)** | 24 | **5.11** | **28%** |
| debug — mid (G) | 25 | 4.96 | 31% |
| debug (D) | 34 | 7.78 | 29% |

It climbs almost perfectly with length — but notice the axis is **effort, not type.**
The long refactor **J** is decode-heavy (5.11) right next to the long bug-fixes; the
trivial bug **F** sits at the bottom with the data task. So decode-intensity and the
phase breakdown (finding 2) are *different* signals that happen to correlate, because
long debugging maximizes both. *(Caveats: "decode-dominated" holds in the
compute/latency sense — decode is memory-bound and per-token — not the token-count
sense; and these are list-price token totals, not measured GPU time.)*

### 5. How much can we trust the parser? An eBPF ground-truth check

Every finding above rests on `cc_trace`'s best-effort parsing of the transcript —
including file/network I/O scraped out of Bash *command strings*. To see how much
that can be trusted, we ran the tool **and** [AgentSight](https://eunomia.dev/agentsight/)
(an eBPF tracer that observes the same run at the syscall + TLS layer) on one
identical task, and diffed them. Full write-up:
[`ebpf-validation.md` on the `ebpf-validation` branch](https://github.com/fairozkhan10/claude-code-trace-visualizer/blob/ebpf-validation/ebpf-validation.md).
The short version:

- **Where eBPF can keep score, the parser is exact.** On task file *writes* — the
  one signal with clean kernel ground truth here — precision and recall were both
  1.00: the parser reported exactly the task's output file, hallucinated nothing,
  and correctly *omitted* the agent's internal plumbing writes (transcript,
  MCP logs, `/dev/tty`) that eBPF over-captures.
- **This is a pilot (n=1).** Reads and task-initiated network weren't exercised
  (the capture surfaced writes only, and `exec` events lacked arguments), so those
  heuristics remain unvalidated. Treat "exact" as "exact on writes, on one task."
- **eBPF sees two things the transcript fundamentally can't** — the agent's own
  control-plane network (8 endpoints, incl. a 3rd-party Datadog telemetry sink) and
  the real process tree (~43 execs from ~2 Bash tool calls). Our tool and eBPF are
  **complementary**, not competing: intent/phase/cost vs. OS-level fan-out.
- **The decode-dominance blind spot is still open.** We hoped eBPF's TLS capture
  would give an *independent* token count to corroborate finding 4 — it didn't.
  `claude` statically links BoringSSL, so the SSL-read uprobe couldn't reassemble
  the API response bodies that carry `usage` (1 of 5 calls recovered, and only the
  Haiku title side-call). So the token/cost numbers above still rest **solely on
  the transcript's self-report**. Closing this needs a MITM proxy
  (`ANTHROPIC_BASE_URL` → `mitmproxy`), not uprobes — tracked separately.

### 6. Agents redo a real fraction of their work — most in debugging

Shawn's framing of the retry signal was a *systems* one: if an agent keeps issuing
the same or near-identical operations, that's a **caching / memoization
opportunity**. `cc_trace`'s `repeated_work()` quantifies it — it normalizes each
Bash call to a signature (`pytest a.py -q` and `pytest b.py` collapse to one),
unions near-identical signatures with a `difflib` ratio ≥ 0.9, and groups non-Bash
calls by structured target (re-reading/-editing the *same file*). "Redundant" =
every call in a cluster after the first.

| group | n | redundant calls (% of all calls) | dominant repeat |
|---|---:|---:|---|
| refactor | 6 | mean **16%** (0–50%) | re-editing one file (`Edit ×7` in H) |
| debugging | 6 | mean **27%** (12–48%) | re-running shell probes (`Bash`), re-editing |

- **It's ubiquitous.** 11 of 13 runs repeat work; only the two shortest (A, C, ≤7
  calls) don't. Across the suite, a meaningful slice of every nontrivial run is
  re-tread ground a cache could absorb.
- **Debugging repeats more than refactoring** (27% vs 16% mean) — consistent with
  the reproduce→hypothesize→re-test loop of finding 2: debugging re-runs the same
  failing command across hypotheses. So the optimization payoff is *largest exactly
  where the phase shift breaks down* (the long-debug tail).
- **But it does *not* track length** — honestly, the cleanest "redundancy rises
  with length" story doesn't hold. The single longest run (D, 34 calls) is low at
  12%, while mid-size G and H top the list (48–50%). The repeats are driven by
  *task dynamics* (re-editing a file you're iterating on; re-running a probe), not
  by run length. Treat the group means as directional (n=6 each, noisy).

The two repeat mechanisms are distinct caching targets: **Edit-churn** (the same
file edited many times — a write-back/coalescing opportunity) and **Bash-rerun**
(the same probe issued repeatedly — a result-memoization opportunity).

### 7. Cross-check on a standard benchmark (SWE-bench Lite) + a second model

The 13 runs above use repos *we* picked. To check the phase result on tasks we
didn't choose, we ran five **SWE-bench Lite** instances through the same harness
([`scripts/swebench_run.py`](scripts/swebench_run.py): clone @ base commit, apply
the instance's *test* patch, drive `claude -p`, confirm red→green, profile) — three
repos, two models, a refactor plus three different debugging bugs. **All were solved,
and every one reproduces finding 2's *clean* corner:**

| instance | task kind | model | calls | purity | sequence |
|---|---|---|---:|---:|---|
| `pallets__flask-5063` | feature/refactor | Opus 4.8 | 8 | **1.00** | `EEEEXXXX` |
| `pallets__flask-5063` | feature/refactor | **Sonnet** | 8 | **1.00** | `EEEEXXXX` |
| `psf__requests-3362` | debug (unicode decode) | Opus 4.8 | 6 | **1.00** | `EEEEXX` |
| `sympy__sympy-13177` | debug (`Mod` math bug) | Opus 4.8 | 12 | **0.92** | `EEEXXEXXXXXX` |
| `sympy__sympy-13480` | debug (`subs` crash) | Opus 4.8 | 3 | **1.00** | `EXX` |

- **The clean front-load holds on tasks we didn't pick.** Refactor *and* debugging
  both come out cleanly phased (`EEE…XXX`, purity 0.92–1.00) — finding 2's prediction
  for the short/tractable corners.
- **It's model-invariant.** The *same* flask task on **Opus and Sonnet** gave an
  identical phase signature (purity 1.00, `EEEEXXXX`); they differed only in efficiency
  (Sonnet faster, ~⅓ fewer output tokens). Phase structure is a property of the *task*,
  not the model.
- **The interleaving corner doesn't appear here — and that itself is the finding.**
  Every Lite run stayed short (3–12 calls) and clean, *including* the debugging ones.
  The reason isn't length per se: SWE-bench Lite bugs are **well-specified and
  tractable** (a clear repro, often pointing near the fault), so a frontier model
  *diagnoses fast* and never enters the long reproduce→hypothesize→re-test loop. The
  interleaving regime (finding 2's long-debug tail) needs harder-to-**diagnose**,
  under-specified bugs — which is exactly what our own hand-built long-debug repos
  were. So the breaking corner is about **diagnostic difficulty, not task source or
  call count** — and Lite, by construction, sits in the clean regime for a strong model.
  *(Env note: SWE-bench targets period-correct Pythons — flask 2.3 runs on 3.12, but
  sympy 1.1 / requests 2.10 need 3.9. Heavy repos that need per-instance Docker, and
  SWE-bench Verified's nastier bugs, are the route to eliciting the long tail.)*

### 8. Head-to-head with another offline profiler (agentpprof)

`cc_trace` isn't the only tool that profiles agent transcripts offline.
**[agentpprof](https://github.com/eunomia-bpf/agentsight)** (the same group as the
AgentSight tracer in finding 5) reads the *same* Claude Code JSONL and projects it
into pprof/flame-graph profiles by `tokens` / `files` / `network`. We built it and
ran it against two of the SWE-bench transcripts cc_trace already profiled, and diffed
the overlapping metrics:

| metric (one run, flask-5063) | cc_trace | agentpprof | note |
|---|---:|---:|---|
| output tokens | 3,702 | 7,142 | agentpprof **1.9×** higher |
| total tokens | 128,429 | 204,913 | agentpprof **1.6×** higher |
| files | `cli.py` (w3), `test_cli.py` (r2) | `src/flask` (dir), `tests/…` | cc_trace **file-level**; agentpprof **dir-level** |
| network | 0 | 0 | agree |

- **Tokens: agentpprof over-reports ~1.5–1.9×** (same pattern on the sympy run: 1.6×
  output). cc_trace's counts are **wire-validated** (the MITM check in finding 5, after
  deduping `usage` per `message.id`). So the gap is an **independent corroboration of
  our token-double-count finding** — a second tool that sums repeated per-message usage
  lands ~1.5–2× high, exactly the failure we fixed. *(We didn't fully trace agentpprof's
  accounting, so we report the discrepancy, not a root-cause claim about their code.)*
- **Files: a precision-vs-grouping trade-off.** cc_trace tracks individual files with
  read/write modes (`cli.py` *written*, `mod.py` *written* — matching the real fix, and
  eBPF-validated exact on writes in finding 5). agentpprof groups by **directory** and
  misattributed one write to the test file. Each tool has one wart (cc_trace emitted a
  spurious `p.is_Pow` token from a command string).
- **Different missions, hence complementary.** agentpprof's value-add is **semantic
  intent tagging** (collapse 1000s of prompts into `debug`/`review` flame bars) — but
  that needs tag rules configured (coverage was 0% untagged here). cc_trace's value-add
  is the per-run **phase / cache / purity** workload characterization agentpprof doesn't
  compute. On the *raw* shared measurements, cc_trace is the more accurate/granular one;
  agentpprof is the better cross-session **aggregator**.

## Limitations (read before citing)

- **The decisive corners are n=2.** Short-debug and long-refactor are each two runs
  in two repos — replicated tightly, enough to be more than anecdotal, but not yet a
  significance test. A third rep each would get there. One model (Opus 4.8)
  throughout; cross-model is wide open. *(Clean short-debug targets were
  surprisingly scarce: app-style repos kept having broken baselines; small
  pure-Python libs with one bug-fix-plus-test commit were the reliable source.)*
- **Difficulty is proxied by length** (call count), not an independent rating —
  good enough to break the confound, not a precise dial.
- **Heuristic parsing & wall-clock timing.** Bash file/network parsing is
  best-effort (inline-script I/O and `sed -i` are invisible); durations include
  queue/permission waits; costs are list-price estimates from `cc_trace/cost.py`.
  Finding 5 spot-checks this against eBPF ground truth — exact on writes, but only
  n=1, and token/cost figures are still the transcript's self-report.

## Reproduce

```bash
# run one task headless, writing a transcript cc_trace can profile
scripts/profile_task.sh --prompt-file tasks/02-search-refactor.md

# …then roll your runs up into the comparison view
python3 -m cc_trace compare reports/*.json -o reports/compare.html
```
