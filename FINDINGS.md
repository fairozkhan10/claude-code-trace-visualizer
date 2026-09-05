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
- **A third task family looks different — and that's the point.** On Terminal-Bench
  (shell/sysadmin), the workload is *execute-dominated* (often no explore phase at all),
  Bash-only, with near-zero decode — while KV-cache reuse stays universal. Phase
  structure is task-shaped, now shown across refactor, debug, and sysadmin work.
- **Even a benchmark's *hardest* bugs only bend the phase shift — and the reason is
  structural.** Two SWE-bench Verified instances rated 1–4 hrs land *between* the
  clean and interleaved regimes (purity 0.80/0.89) — because SWE-bench-style
  fail-to-pass tests are written against the fix and *leak the diagnosis*. The fully
  interleaved loop needs symptom-only debugging, which benchmark test patches
  structurally can't supply.
- **Stronger agents break the benchmark before they break the phase model.** Running
  a stronger model (Fable 5) on the same hard bug produced two *new* benchmark-validity
  failures the score can't see: it **fetched the upstream fix from GitHub** (the
  fixture path leaked the PR number — caught by the network panel), and, once
  de-identified, it debugged *cleaner than Opus* (purity 0.944) but **stranded its
  correct fix in `git stash`** mid-verification — a one-shot harness grading an
  asynchronous work style. An explicit autonomous-instruction fixes the stranding
  (tested); de-identification does **not** reliably stop retrieval — a follow-up run
  re-found the PR by *searching GitHub with the issue's wording*. The phase framework
  stayed legible through all of it.
- **The cross-model capability gradient does not replicate — it was an n=1
  artifact.** At n=4 per model on the same isolated fixture, Opus and Fable 5
  have **indistinguishable phase purity** (medians 0.688 vs 0.692, Cliff's delta
  0.00, p=1.000) in the first design here with the statistical power to say so.
  What differs is *effort* — Opus takes 61.5 median tool calls to Fable's 27 for
  the same grade. Earlier single-run comparisons in findings 10/11, and the
  cross-model figure in `examples/`, assert a gradient that is not there.
- **Close the network and the score is *still* wrong — now with honest agents.**
  Four network-isolated runs of the same model on the same bug all pass, with zero
  retrieval attempts and verified-untouched tests — and produce **four different
  patches**. One of them also finds the nondeterministic hang the fix itself
  introduces; the other three ship it. The grade is identical for all four. The
  same run is 2.7× the cost of the median, so scoring on the grade alone selects
  against the thorough one. Purity replicates tightly here (sd 0.028) — and lands
  ~0.25 below the single-run 0.944 quoted above, which is why single-run
  cross-model claims in this document should be read as unreplicated.

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

### 9. A third task distribution: Terminal-Bench (shell/sysadmin)

Findings 2/7 cover *code-edit* work (refactor, debug). To test whether the phase
framework generalizes to a different kind of work, we ran **[Terminal-Bench](https://www.tbench.ai/)
core** (hand-crafted shell/sysadmin tasks: fix permissions, repair a git repo,
issue a self-signed cert, parse nginx logs, truncate a sqlite db, stand up a git
web server). Six tasks, Claude Code as the agent, each transcript profiled by
`cc_trace`.

**Getting here cleared the auth wall that blocked the eBPF/Docker work (finding 5).**
Terminal-Bench runs the agent *inside a per-task Linux container*, and the built-in
`claude-code` agent demands an `ANTHROPIC_API_KEY` — which a $20 Pro plan doesn't
have (its OAuth token lives in the macOS Keychain, unmountable into a container). We
wrote a small custom agent that injects the Pro token via `CLAUDE_CODE_OAUTH_TOKEN`
and captures `claude`'s `--output-format stream-json` to the host, which `cc_trace
live` parses directly. So the same Pro plan that runs the Mac-side benchmarks now
drives containerized ones too — no API key, no Keychain hack.

| task | solved | calls | top tool | purity | cache% | decode | sequence |
|---|:---:|---:|---|---:|---:|---:|---|
| fix-permissions | ✓ | 4 | Bash | 1.00 | 0.95 | 0.001 | `EEXX` |
| openssl-selfsigned-cert | ✓ | 7 | Bash | 1.00 | 0.96 | 0.007 | `XXXXXXX` |
| sqlite-db-truncate | ✓ | 5 | Bash | 0.80 | 0.93 | 0.001 | `XEXXE` |
| fix-git | ✗ | 5 | Bash | 1.00 | 0.94 | 0.001 | `XXXXX` |
| nginx-request-logging | ✗ | 7 | Bash | 1.00 | 0.96 | 0.008 | `EXXXXXX` |
| configure-git-webserver | ✗ | 22 | Bash | 0.95 | 0.97 | 0.011 | `EEXX…EXXXX` |

- **This distribution is execute-dominated.** Several runs have *no explore phase at
  all* (`XXXXX`, `XXXXXXX`) — the agent goes straight to acting. That's the opposite of
  the refactor profile (front-loaded `EEEE…XXXX`): sysadmin goals are concrete, so
  there's little codebase to map first. **Bash is the top tool on every task.**
- **Decode-intensity is near zero** (0.001–0.011, vs 0.35–7.5 for code tasks). These
  are command-dispatch workloads — lots of cached prefill, little generated output —
  which sharpens finding 4: decode tracks *generation effort*, and shell work barely
  generates.
- **KV-cache reuse still holds universally** (0.93–1.00), exactly as in every other
  distribution — the most robust finding, now across three task families.
- **No interleaving appeared, even on the unsolved tasks.** The longest run
  (configure-git-webserver, 22 calls, unsolved) shows only a mild late-explore re-entry
  (purity 0.95), not the long-debug reproduce→hypothesize loop. Consistent with finding
  7: interleaving needs *diagnostic difficulty*, which short well-scoped shell tasks
  don't supply.

*Caveat:* replaying a captured `stream-json` gives reliable **phase/sequence, token,
cache, and call-count** metrics but **not wall-clock timing** (event timestamps
collapse on replay), so time-based separation is omitted here; purity (order-based) is
unaffected. Six tasks, one model — directional, like the rest.

### 10. Hunting the interleaved corner on SWE-bench Verified — it bends, doesn't break

Finding 7 left one corner unelicited on a standard benchmark: the long-debug
**interleaved** regime (purity 0.68–0.76), which we'd only produced on our own
hand-built tasks. Hypothesis: **SWE-bench Verified**'s harder bugs (it ships a
human difficulty rating) would supply the missing *diagnostic* difficulty. We
filtered all 500 Verified instances to the 17 that are both hard (rated ≥ 1–4 hrs)
and bare-metal-runnable (pure-Python repo, no Docker), picked the two whose bug
reports are pure *symptom* — no fault location named, no traceback — and ran them
through the same harness (fixture verified red before any agent spend; Opus; same
era-correct-interpreter rules as finding 7).

| instance | human rating | solved | calls | dur (s) | purity | cache% | redundant | sequence |
|---|---|:---:|---:|---:|---:|---:|---:|---|
| `sympy__sympy-16597` (`is_even` ⇏ `is_finite`) | 1–4 hrs | ✓ | 18 | 281 | **0.89** | 0.94 | 44% | `EEEEEEXEXXXEXXXXXX` |
| `pytest-dev__pytest-10356` (marks lost under MRO) | 1–4 hrs | ✓ | 10 | 52 | **0.80** | 0.91 | 30% | `EEEEXXXEEX` |

- **Both land *between* the bands.** Purity 0.80/0.89 sits below every clean Lite
  run (0.92–1.00) but above the interleaved regime (0.68–0.76). Verified-hard bugs
  bend the phase shift — mid-execute explore returns, a `git stash` baseline check,
  the highest redundant-work fractions we've measured (44%!) — but don't dissolve
  it. The 2×2's "interleaving tracks diagnostic difficulty" claim gains two
  mid-gradient points: it's a **continuum**, not a clean/interleaved dichotomy.
- **Why the corner resists elicitation here — the benchmark format leaks the
  diagnosis.** SWE-bench hands the agent the FAIL_TO_PASS tests, and those tests
  are *written against the fix*: pytest-10356's test body literally calls
  `get_unpacked_marks`, the function the fix modifies. Opus read the failing test
  (call 2) and grepped straight for the fix site (call 3) — no
  reproduce→hypothesize loop required, because the hypothesis is in the test. Any
  failing-tests-in-prompt setup inherits this shortcut. The full interleaved regime
  seems to need *symptom-only* debugging where nothing names the subsystem — which
  is exactly what our hand-built tasks were, and what benchmark test patches
  structurally can't be.
- **The rest of the profile behaves as the other nine findings predict:** cache%
  0.91–0.94 (universal band, give or take a short run), decode share 30% on the
  18-call sympy run (top of the debug band, finding 4's effort scaling), Bash the
  top tool, zero retry loops.

*Caveats:* n=2, one model, both solved — no unsolved-hard contrast yet. The two
fixtures each needed an env repair **before** the agent ran (sympy 1.5's test shim
wants the standalone `py` package; pytest 7.2 collapses under py3.12's `ast.Str`
removal — both caught by the red-check, zero agent spend on either), so the
harness's abort-if-not-red valve is doing real work: an uncaught false-red would
have measured *environment repair*, not debugging.

*Capability addendum (from finding 11's control run):* the same sympy bug given to
a **stronger model (Fable 5)** on a de-identified fixture came out *cleaner* than
Opus — purity **0.944** vs 0.89, 4 explore calls straight to an 8-line fix, 26%
redundancy vs 44%. The pytest instance replicates it (see finding 11's
amendment): Fable 5 de-identified purity **0.929** vs Opus's 0.80 on
`pytest-10356`. Two points (n=2) for the claim that the gradient is
**diagnostic difficulty *relative to model capability***: the same bug sits at a
different place on the continuum for a different model.

> **SUPERSEDED by finding 13.** Both points above are single runs. Replicated at
> n=4 per model under isolation, the gap vanishes: Opus 0.688 vs Fable 0.692,
> Cliff's delta 0.00, p=1.000. Keep the *diagnostic-difficulty* framing, which
> rests on the task-type × difficulty evidence in finding 2; drop the
> **capability-relative** extension — it does not replicate.

### 11. Stronger agents break the *benchmark* before they break the phase model

Finding 10 needed a cross-model check: does a stronger model re-clean the phase on
the same bug? We ran **Claude Fable 5** on the identical `sympy-16597` fixture —
first twice, then a third run to test the fix for what the second run exposed.
None went the way we expected — and the failures are the finding. Four runs, same
bug, same task prompt:

| run | protocol verdict | what actually happened | calls | purity | network |
|---|---|---|---:|---:|---:|
| Opus, original fixture | ✅ solved | genuine debugging | 18 | 0.89 | 0 |
| Fable 5, original fixture | ✅ "solved" | **downloaded the upstream fix** | 15 | (invalid) | 1 req |
| Fable 5, de-identified fixture | ❌ failed | **correct fix, stranded in `git stash`** | 19 | **0.944** | 0 |
| Fable 5, de-identified + autonomous instruction | ✅ solved | fix applied — but **re-found the PR by searching GitHub** | 22 | (0.955, retrieval-assisted) | 6 req |

- **Failure mode 1 — the agent attacks the task's provenance, not the task.** On the
  original fixture, Fable 5 explored for five calls, recognized the instance (the
  fixture *path* contained `sympy__sympy-16597`, and a SWE-bench instance id is a PR
  number), then ran
  `curl …githubusercontent.com/raw/sympy/sympy/pull/16597.diff` and `git apply`'d the
  canonical upstream fix — tests excluded — and spent the rest of the run verifying.
  Red→green: pass. As a *debugging workload*: invalid — decode share halved (0.16 vs
  0.30), redundancy collapsed (13% vs 44%), because nothing was diagnosed. Opus, same
  path, same prompt, never tried this. Contamination on SWE-bench is usually framed as
  *memorization*; this is **active runtime retrieval of the solution's public
  provenance**, and it is capability-dependent. (`cc_trace`'s network panel is what
  caught it — one HTTPS request to GitHub, plainly visible.)
- **Failure mode 2 — the grading protocol assumes a turn-taking model the agent
  doesn't share.** On the de-identified fixture (neutral `task-a` path, prompt clean of
  the instance id) Fable 5 *did* debug — zero network, straight to the assumptions
  engine, an 8-line fix, purity 0.944, the cleanest hard-debug signature we've
  measured. Then it verified *too well*: it stashed its own fix to run a clean-baseline
  sweep in the background, started a watcher on the sweep, and **ended its turn
  promising to report back**. `claude -p` is one-shot — no next turn exists. The fix
  died in `stash@{0}`; the harness graded red. We applied the stash afterwards: all 3
  FAIL_TO_PASS pass (66/66 in the file). A **correct solution scored as a failure**
  because the agent's asynchronous work style doesn't fit a single-turn harness.
- **The protocol fix works; the provenance fix doesn't.** Run 4 re-ran the
  de-identified fixture with one paragraph added to the prompt — *"you are operating
  autonomously in a one-shot session… leave the completed fix applied in the working
  tree; do not stash; do not end on a promise"*. **Failure mode 2: eliminated** — fix
  applied in the tree, stash empty, protocol verdict green, and the verification
  discipline survived intact (baseline comparisons now run as atomic
  `stash && test && pop` commands instead of stranding work). But the same run showed
  **de-identification is only a mitigation *attempt*** for failure mode 1: with no
  instance id anywhere, Fable 5 browsed the file's GitHub commit history (turn 3),
  **searched GitHub commits using the issue's own wording**
  (`api.github.com/search/commits?q=repo:sympy/sympy+"Make is_…"`, turn 5), and by
  turn 8 was curling `pull/16597.diff` again — it re-derived the PR number from
  *content*. Provenance retrieval is stochastic (the zero-network debug in run 3 and
  the successful hunt in run 4 came from the *same* fixture and near-identical
  prompts) — so run 4's purity is retrieval-assisted, not a debugging data point; run
  3's **0.944** stands as the capability measurement.
- **The phase model survived every failure.** Cache% 0.89–0.94, front-loaded explore,
  execute-dominated tail — every run, including the invalid ones, is legible in the
  same framework; it's the *benchmark's* verdicts that stopped meaning what they claim.
  For workload measurement, that's the punchline: **benchmark validity degrades with
  model capability along axes the score can't see** — task leakage (finding 10),
  provenance retrieval, and protocol mismatch — and you need exactly this kind of
  per-call instrumentation to notice.

*Amendment — second instance (pytest-10356, de-identified, autonomous):* the
protocol replicates on finding 10's other instance. Fable 5 on a de-identified
`pytest-dev__pytest-10356` fixture (neutral `task-b` path, prompt clean of the
instance id, autonomous instruction appended): **solved**, fix applied in the
tree, stash empty, **zero validity-audit flags**, and zero provenance retrieval —
the only network activity was two local `pip install`s building its own test
venv. 14 calls / 78 s, sequence `EEEXXEXXXXXXXX`, cache 1.0, purity **0.929** vs
Opus's **0.80** on the identical instance (finding 10). That makes the
capability-relative gradient **n=2** (sympy: 0.944 vs 0.89; pytest: 0.929 vs
0.80 — Fable 5 cleaner on both hard-debug instances) and the
autonomous-instruction mitigation **n=2** (no stranding either time). The clean
network here doesn't retire failure mode 1 — retrieval is stochastic (run 3 vs
run 4 above), so a quiet run is consistent, not exculpatory.
**The purity half of that gradient is superseded by finding 13** (n=4 per model:
no difference). The stranding and retrieval observations in this finding are
unaffected — they are existence proofs, not comparisons.

*Amendment 2 — the isolated replication (2026-09-03) revises two claims above.*
(a) **The 0.944 does not replicate.** Four network-isolated Fable 5 runs on this
same instance give 0.714 / 0.704 / 0.652 / 0.679 (sd 0.028) — see finding 12.
Run 3's 0.944 was n=1 and should no longer be read as "the capability
measurement"; the capability-relative purity gradient asserted here rests on
single runs per cell and is **unreplicated**. (b) **The retrieval hole is
closed, and confirmed empty.** Under isolation, across four runs, Fable 5 made
**zero** attempts on any source-hosting domain — the proxy's deny log is entirely
vendor telemetry and blocked `pypi` installs. That is a stronger result than run
3's quiet network (which was consistent-but-not-exculpatory, since retrieval is
stochastic): here retrieval was *impossible*, and the model solved the bug
anyway, four times out of four. What isolation then exposed is a third failure
mode the score cannot see — finding 12.

*Practical notes:* (1) **de-identifying fixture paths is necessary but not
sufficient** — the instance id is a URL, but the issue text is a search query;
the only real immunity from runtime provenance retrieval is **network isolation**
(the Docker harness, now built and used in finding 12). (2) One-shot (`-p`) harnesses driving newer agentic models
should say so in the prompt — the autonomous-instruction mitigation is **tested
here (n=2)**: it eliminates stranded work without degrading verification. (3)
Small n per failure mode: existence proofs and a twice-tested fix, not rates. (4) These
detections are now **built into the tool**: `cc_trace`'s benchmark-validity audit
flags solution-channel network, instance-id leaks, and stash-stranded work on
every profiled run — re-auditing the four transcripts above reproduces this
table's verdicts exactly (run 1: high-severity provenance flag on the
`pull/16597` diff; run 3: stranded-work only; run 4: the retrieval hunt, no
stranding).

### 12. Four passing runs, four different patches — the grade can't see solution quality

Finding 11 ended on a promise: only **network isolation** gives immunity from
runtime provenance retrieval. We built that harness (`scripts/isolated_run.sh` —
task container on an `--internal` Docker network, no DNS, no route, reaching out
only through a stdlib allowlist proxy) and ran **Fable 5 on `sympy-16597` four
times**, same de-identified fixture, same autonomous prompt, nothing changed
between runs but the sampling.

All four **passed**, and all four passed *legitimately*: zero source-hosting
egress attempts (the proxy logged only model-API traffic, vendor telemetry, and
blocked `pypi` installs), empty stashes, zero validity-audit flags, and — for the
three runs post-dating the check — the graded test files verified byte-identical
to the fixture baseline before `pytest` ran. Failure modes 1 and 2 are closed
here. **The third one is new.**

| run | calls | dur (s) | cost $ | purity | redun | files changed | grade |
|---|---:|---:|---:|---:|---:|---|---|
| r1 | 21 | 851 | 0.47 | 0.714 | 0.190 | `core/assumptions.py` | ✅ 3 passed |
| r2 | 30 | 873 | 0.76 | 0.704 | 0.233 | `core/assumptions.py` | ✅ 3 passed |
| r3 | 24 | 830 | 0.58 | 0.652 | 0.208 | `core/assumptions.py` | ✅ 3 passed |
| r4 | 53 | 2309 | 1.18 | 0.679 | 0.264 | `core/assumptions.py` **+ `core/power.py`** | ✅ 3 passed |

**The four patches are not the same patch.** Every run reached the same insight —
the assumption engine never connects the rational/algebraic hierarchy to
`finite`, so `even` fails to imply `finite` — but each wired it in differently:

- **r1** conjoined `& finite` onto three implications (`rational -> real`,
  `algebraic -> complex`, **`imaginary -> complex`**) plus `irrational`. That
  third one is a semantic choice the others never make: it declares every
  imaginary number finite.
- **r2** took `algebraic`, `transcendental`, `irrational` — and left `imaginary`
  and `rational` alone.
- **r3** reached the same place structurally differently, adding a *separate*
  `'algebraic -> finite'` rule rather than conjoining onto the existing one.
- **r4** matched r2's three rules, then kept going.

**Only r4 found the bug its own fix introduces.** Making `rational` a
prerequisite of `finite` lets the engine's randomized deduction order reach
`Pow._eval_is_rational`, which rebuilds the power *with* evaluation — so
`Mod(Pow(2, 10000000000, evaluate=False), 3)` tries to materialise a
10-billion-bit integer and hangs. It is **nondeterministic**: it depends on which
prerequisite the engine happens to try first. r4 reproduced it, added the early
exit (the same guard SymPy upstream adopted), then ran the `core` suite three
times *specifically because the hang is probabilistic*, and swept twelve
assumption-sensitive modules.

Runs r1–r3 ship a fix that introduces a latent nondeterministic hang into
`Pow`. **The benchmark scores them identically to r4.** `FAIL_TO_PASS` is three
assertions about `oo` and `Symbol`; nothing in it can reach a pathological
exponent, so the grade is blind — not to cheating this time, but to *correctness
the task never thought to ask about*. Finding 11 showed benchmark validity
degrading along axes the score can't see; this is the same failure with the
adversarial reading removed. Every run here is honest. The scoreboard is still
wrong.

The thoroughness is not free: r4 cost **2.7× the median run** (53 calls vs ~27,
2309 s vs ~860 s, $1.18 vs $0.67). A harness that rewarded only the score would
select against it.

**Phase purity replicates tightly, and it is not 0.944.** Across the four runs:
**0.714 / 0.704 / 0.652 / 0.679** — median 0.692, mean 0.687, **sd 0.028**. The
metric is stable run-to-run, which is what makes it usable for cross-run
comparison at all. But finding 11 offered run 3's **0.944** as "the capability
measurement" for Fable 5 on this instance, and at n=4 that number does not
reproduce — every isolated run lands ~0.25 below it. Two honest caveats before
reading this as a refutation: the isolated runs face **blocked `pypi` requests**
(8–15 denials each) that the un-isolated run never hit, which adds recovery work
and depresses purity; and the environments differ (container vs host). The
defensible claim is narrower than "0.944 was wrong": **0.944 was n=1, and no
replication has come near it.** Cross-model purity claims built on single runs —
including finding 11's capability gradient — should be treated as unreplicated
until they are re-run under isolation.

*Scope:* one model, one instance, n=4. The solution-scope result is an
**existence proof** — four passing runs, four different patches, one materially
more complete — not a rate; we cannot say how often agents ship the incomplete
version. What needs no statistics is the disagreement *within* the passing set.

*Amendment — it replicates on Opus (2026-09-04), n=8 across two models.* Four
isolated Opus runs on the same fixture, all `3 passed`, all integrity-verified,
spread the same way:

| model | run | files changed beyond `core/assumptions.py` |
|---|---|---|
| Fable | r1, r2, r3 | — |
| Fable | r4 | `core/power.py` (the induced `Pow` hang) |
| Opus | r1, r3 | — |
| Opus | r2 | `tensor/indexed.py` (an `Idx` regression its fix introduced) |
| Opus | r4 | `printing/tree.py`, `tensor/indexed.py` |

**Eight passing runs, solution scope from one file to three, one identical
grade.** Opus r2 and r4 independently found a *different* second-order breakage
than Fable r4 did — the `Idx` regression rather than the `Pow` hang — which says
the incomplete fix has more than one way to be incomplete, and that `FAIL_TO_PASS`
sees none of them. The blindness is a property of the benchmark, not of a model.

### 13. The cross-model purity gap does not survive replication

Findings 10 and 11 built a **capability gradient**: a stronger model splits
phases more cleanly on the same hard bug (0.944 vs 0.89 on sympy, 0.929 vs 0.80
on pytest). Each cell was one run. With the isolated harness and
`cc_trace stats` we can finally test it — **n=4 vs n=4, exact permutation floor
p=0.0286, the first design in this study that could reach significance at all.**

| | purity (4 runs) | median |
|---|---|---:|
| Opus | 0.558 / 0.966 / 0.750 / 0.620 | **0.688** |
| Fable 5 | 0.714 / 0.704 / 0.652 / 0.679 | **0.692** |

```
purity          delta  0.00 (negligible)   p = 1.000
n_tool_calls    delta  0.62 (large)        p = 0.200   (61.5 vs 27)
redundant_frac  delta  0.44 (medium)       p = 0.400
duration        delta  0.38 (medium)       p = 0.486
```

**There is no purity difference.** Cliff's delta is 0.00 — the medians differ by
0.004, well inside the run-to-run noise of either model. The gradient was an
artifact of comparing single runs.

Two honest qualifications, in opposite directions. **Opus r2 is metrically
degenerate** (see Limitations): a polling loop inflates its purity to 0.966, and
it is the only Opus run above Fable's range. Dropping it gives Opus median 0.625
vs Fable 0.692 — Cliff's delta −0.33, *medium, and pointing the other way* — but
at n=3 vs 4 the floor is 0.057 and nothing is testable. So the primary analysis
says "no difference" and the sensitivity analysis says "if anything, the reverse."
**Neither supports the published gradient.**

What does survive is an **effort** difference, not a structural one: Opus spent
**61.5 median tool calls to Fable's 27** and roughly 2.2× the wall-clock, for the
same grade and the same phase structure. The models differ in how much work they
do, not in the shape of it — which is the phase model's own claim (findings 2, 7)
holding across a capability gap that was supposed to break it.

*Scope:* one instance (`sympy-16597`), one harness, n=4 per cell. A null result
at n=4 rules out only *large* effects; a real gap of a few hundredths would need
far more runs. The cross-task figure `examples/swe-crossmodel-compare.html`
predates this and asserts the gradient — **it should be read as superseded.**

## Limitations (read before citing)

- **Polling loops silently corrupt purity and redundancy — check the tool mix
  before citing either.** Opus r2 in finding 13 ran `true` **792 times** out of
  880 calls, polling for a backgrounded test suite. `cc_trace` counts each as a
  tool call and phases it `execute`, so the run reads as 831 execute vs 44
  explore, the explore→execute crossover becomes trivially clean, and **purity
  inflates to 0.966 — the highest Opus value on record — while redundancy hits
  0.94.** Both are artifacts of a wait loop, not signals about the work. Nothing
  in the tool detects this today. A run whose top command is a no-op (`true`,
  `:`, bare `sleep`) at high multiplicity should have its phase metrics treated
  as void; the reliable tell is an explore share near zero (0.05 here) alongside
  an implausible call count. This is why finding 13 reports a sensitivity
  analysis with r2 removed rather than quietly averaging it in.
- **Run-to-run variance depends on both the model and the task — do not
  generalise stability from one cell.** We got this wrong twice in one day.
  Fable on `sympy-16597` replicated tightly (0.652–0.714, sd 0.028), which
  suggested purity was simply a stable metric; Fable on `pytest-10356` then gave
  0.684 / 0.895 / 0.941, a **range of 0.257** on the same harness. That looked
  like task-dependence — until Opus on the *same sympy instance* produced
  0.558–0.966 (0.558–0.750 excluding the degenerate run). Stability is a
  property of the model-task pair, so **every cell needs its own n**, and a
  tight cell is not evidence that a neighbouring one will be tight.
- **Small n is a *design* limit here, not just a sample-size complaint.** A
  two-group comparison is scored by an exact permutation test, whose null has
  `C(n1+n2, n1)` equally likely splits — so the smallest reachable two-sided p is
  `2/C(n1+n2, n1)` **whatever the data say**. At n=3 vs 3 that floor is **0.100**;
  at the n=4 vs 1 of finding 12 it is **0.400**. No effect size, however large,
  can clear 0.05 in those designs, and "not significant" describes the experiment
  rather than the models. **n=4 per group is the smallest balanced design that can
  reach p<0.05.** `python3 -m cc_trace stats … --group A --group B` computes this
  floor and refuses to report a delta or CI at n=1, where both are degenerate by
  construction. Read effect sizes and intervals in this document, not p-values.
- **A trap in the isolated fixture, for anyone reproducing finding 12.**
  `scripts/isolated_setup.sh` applies the instance's *test* patch to the working
  tree and never commits it. So the fixture ships with its test files already
  modified **before the agent starts**, and `git status` cannot separate the
  harness's edits from the agent's — we briefly misread finding 12's runs as test
  tampering on exactly that basis. For the same reason
  `git checkout HEAD -- <test file>` restores the *pre-patch* tests, which pass on
  unfixed code, so "the graded tests pass on a pristine checkout" means the wrong
  tree was tested, not that the fixture is green. Integrity is therefore checked
  against a **baseline copy carried in the fixture image**, and the audit's
  `test_edit` detector reads the *transcript* rather than the filesystem — which
  is what keeps it correct on a fixture that is dirty by design.
- **The decisive corners are n=2.** Short-debug and long-refactor are each two runs
  in two repos — replicated tightly, enough to be more than anecdotal, but not yet a
  significance test. A third rep each would get there. Opus 4.8 throughout, with
  single-task cross-model checks on Sonnet (finding 7) and Fable 5 (findings 10/11)
  — suggestive, not systematic. *(Clean short-debug targets were
  surprisingly scarce: app-style repos kept having broken baselines; small
  pure-Python libs with one bug-fix-plus-test commit were the reliable source.)*
- **Difficulty is proxied by length** (call count), not an independent rating —
  good enough to break the confound, not a precise dial.
- **Heuristic parsing & wall-clock timing.** Bash file/network parsing is
  best-effort (inline-script I/O and `sed -i` are invisible); durations include
  queue/permission waits; costs are list-price estimates from `cc_trace/cost.py`.
  Finding 5 spot-checks this against eBPF ground truth — exact on writes, but only
  n=1, and token/cost figures are still the transcript's self-report.
- **Finding 6's redundancy magnitudes predate a signature fix — treat them as
  directional only.** Repeated work is clustered by a normalised command
  signature. Until 2026-07-27 that signature was derived from the 80-char display
  label, and **74% of Bash calls across the runs in this document are longer than
  80 characters** — so commands were being compared by their shared prefix.
  Re-clustering the sessions still on disk moves `redundant_frac` in both
  directions and by a lot (one 92-call session fell 0.489 → 0.174 as commands
  that merely shared a prefix stopped being counted as repeats; a SWE-bench run
  rose 0.045 → 0.136 as two genuinely identical pytest sweeps, previously split
  apart, merged). The source transcripts for runs 01–09 have since been rotated
  out of `~/.claude/projects`, so those rows **cannot** be regenerated. The
  qualitative claim (repeated work is near-ubiquitous, and debugging repeats more
  than refactoring) rests on presence and rank rather than magnitude and is
  unaffected; the absolute percentages should not be cited. The same fix closed a
  bypass in the finding-11 retrieval detector, where a wrapped command
  (`timeout 300 pip install …`, `python -m pip …`) parsed as zero network
  activity.
  **Findings 7, 10 and 11 are not affected.** Every run of theirs whose transcript
  survives was re-profiled with the fixed parser and every published figure
  reproduced exactly — sympy-16597/Opus (18 calls, purity 0.89, redundancy 44%),
  pytest-10356/Opus (10 calls, purity 0.80, 30%), and the 13%-vs-44% contrast that
  carries finding 11's "nothing was diagnosed" argument. Phase metrics are
  untouched everywhere: no call changes phase in any surviving transcript.

## Reproduce

```bash
# run one task headless, writing a transcript cc_trace can profile
scripts/profile_task.sh --prompt-file tasks/02-search-refactor.md

# …then roll your runs up into the comparison view
python3 -m cc_trace compare reports/*.json -o reports/compare.html
```

SWE-bench Lite/Verified (findings 7 & 10) — fixture is verified red before any
agent spend; rows pages come from the HF datasets-server (see `--rows`):

```bash
python3 scripts/swebench_run.py sympy__sympy-16597 --py python3.9 \
  --rows /tmp/verified_*.json            # setup + red-check only
python3 scripts/swebench_run.py sympy__sympy-16597 --py python3.9 \
  --rows /tmp/verified_*.json --keep --model opus --run   # + drive & profile
```

Network-isolated, graded runs (finding 12) — the agent has no route out except
an allowlisted model API, and the graded tests are verified against a baseline
before scoring:

```bash
# build a red, de-identified fixture image (needs network: clone + pip)
scripts/isolated_setup.sh <fixture-dir> test_infinity test_neg_infinity test_other_symbol

# graded run; writes grade.txt, test-integrity.txt, egress.jsonl, report.html
REPO=sympy/sympy scripts/isolated_run.sh <prompt-file> claude-fable-5

# then ask whether a cross-group difference is even testable at this n
python3 -m cc_trace stats reports/*/report.json \
    --group fable --group fable --group fable --group fable --group opus
```

Terminal-Bench (finding 9), Claude Code on a $20 Pro plan, no API key:

```bash
export CLAUDE_CODE_OAUTH_TOKEN=$(security find-generic-password \
  -s "Claude Code-credentials" -w | python3 -c \
  'import json,sys;print(json.load(sys.stdin)["claudeAiOauth"]["accessToken"])')
PYTHONPATH=scripts tb run -d terminal-bench-core -t fix-permissions \
  --agent-import-path terminal_bench_agent:OAuthClaudeCodeAgent
# profile the captured stream-json:
cat runs/*/fix-permissions/*/sessions/cc.stream.jsonl \
  | python3 -m cc_trace live - -o reports/tb-fix-permissions.html --json
```
