# Project Report — Claude Code Trace Visualizer

A short, honest account of what this project set out to do, what got built, and
what it taught us. For *how to use* the tool see [`README.md`](README.md); for the
*research* it produced see [`FINDINGS.md`](FINDINGS.md).

## The brief

Caeden's original ask was simple: **visualize what tool calls, operations, and
network requests Claude Code makes while it runs — with timing, cost, and
retries.** A way to actually *see* the inside of an agent run, instead of guessing.

## What got built

`cc_trace` — a dependency-free (stdlib-only) Python tool that reads the session
transcripts Claude Code already writes and renders a self-contained, offline HTML
dashboard. One session in, one shareable HTML file out. It grew a few capabilities
beyond the brief along the way:

- **Per-session dashboard** — timeline, phase view, token/context growth, tool
  breakdown, file access, network panel, file co-access graph, retry loops,
  repeated-work clusters (a caching/optimization signal), errors, and a
  **benchmark-validity audit** (the finding-11 failure modes — provenance
  retrieval, instance-id leaks, stranded stashes — flagged automatically).
- **Cross-run compare** — roll many runs into one table to spot patterns.
- **Flame graph** — a run stacked `phase → tool → target`, *coloured by the
  explore→execute phase* (interactive HTML, or `.folded` for speedscope/pprof).
- **Live mode** — profile a run in-flight from `--output-format stream-json`.
- **SWE-bench harness** — run standard-benchmark instances through the same
  profiler (clone → drive `claude -p` → confirm red→green → profile).
- **Honest Bash parsing** — file *and* network I/O done through the shell (the way
  agents actually work) is parsed out of command strings, not ignored.
- **Network-isolated benchmark harness** — the agent runs on an `--internal`
  Docker network with no DNS and no route out, reaching the world only through a
  stdlib allowlist proxy that logs every blocked attempt. Grades `FAIL_TO_PASS`,
  verifies the graded tests weren't edited, and profiles the run. Built because
  finding 11 caught a model downloading the upstream fix; runs can go in parallel
  via a `TAG` namespace.
- **Statistics (`cc_trace stats`)** — exact permutation tests, Cliff's delta,
  seeded bootstrap CIs, Holm correction. It leads with the *design floor*
  `2/C(n1+n2,n1)`: at n=3 per group no result can reach p&lt;0.05 whatever the
  data, so the tool says so instead of printing a reassuring number.

## Did we meet the brief?

Yes — every element, with one clearly-flagged exception:

| Fairoz was asked for | Status |
|---|---|
| Tool calls | ✅ timeline + breakdown |
| Operations | ✅ file access, phases, co-access graph |
| Network requests | ✅ network panel (per-session **and** rollup) — *agent-initiated traffic* |
| Timing | ✅ per-call duration |
| Cost | ✅ per-turn, per-model |
| Retries | ✅ retry-loop detection + near-duplicate repeated-work clusters + errors list |

**The one asterisk:** "network requests" covers traffic the *agent* initiates
(curl/git/pip/web/MCP). It does **not** capture Claude Code's own model-API calls —
those aren't in the transcript and would need a proxy in front of the CLI. That's a
different tool (a sniffer), not a transcript parser, and the limit is stated in the
UI and docs rather than hidden.

**Maturity:** research-grade and ready to clone-and-use, not productized. The Bash
parsing is heuristic and it's single-machine, but the heuristics and metric
definitions are now pinned by an automated test suite (`tests/`, stdlib
`unittest`, run in CI on Python 3.9 and 3.13) — including the token-dedup
invariant and a golden-metric snapshot of the committed example.

## What we learned

The tool was the deliverable, but using it produced a genuinely interesting result
— and the *way* it evolved is the real lesson. (Full detail, 13 findings, in
[`FINDINGS.md`](FINDINGS.md).)

1. **The headline finding got better by being wrong twice.** It went from a clean
   *refactor-vs-debug dichotomy* → a *difficulty continuum* → finally a
   **task-kind × difficulty interaction**. Each correction came from adding one
   *controlled* run, not from re-analyzing old data. When it was tempting to pad the
   sample size to confirm a confounded story, the right move was to **control the
   confound instead** — and that turned a dead end into a sharper claim.

2. **The core findings are solid:** KV-cache reuse is universal (≥95% of context
   reused per turn); **cost tracks context accumulation, not output** (one session
   spent its money on tens of millions of cache-read tokens against a few hundred
   thousand output); and the phase shift is an interaction that **reproduced on real,
   non-benchmark sessions**.

3. **It got validated, three ways.** (a) *Ground truth:* we ran an eBPF tracer
   (AgentSight) + a MITM proxy alongside the tool — file writes were exact, and the
   transcript's token counts matched the wire (after fixing a per-message
   double-count the proxy exposed). (b) *A standard benchmark:* five **SWE-bench Lite**
   instances — tasks we didn't pick — reproduced the clean-phase result, and a
   second model (**Sonnet**) gave an identical phase signature to Opus, so the
   structure is a property of the *task*, not the model. (c) *A peer tool:* running
   **agentpprof** on the same transcripts corroborated our token fix — it reports
   ~1.5–1.9× higher because it doesn't dedupe per message.

4. **Closing the network turned the benchmark itself into the finding.** Once the
   agent physically could not fetch the answer, eleven graded isolated runs
   across two models all *passed* — and disagreed with each other about the fix.
   Solution scope ranged from one file to three; some runs found the second-order
   bug the fix itself introduces (a nondeterministic hang, an `Idx` regression)
   and others shipped it. **`FAIL_TO_PASS` scores them identically.** The
   thorough runs also cost 2–3× the median, so scoring on the grade alone
   selects against them. That's not a model failing a benchmark; it's a benchmark
   that cannot see what it claims to measure (findings 12–13).

5. **Replication killed our most quotable claim, and that's the point.** Earlier
   single runs suggested a stronger model splits phases more cleanly (0.944 vs
   0.89). At n=4 per model the gap is **gone** — medians 0.688 vs 0.692, Cliff's
   delta 0.00, p=1.000 — in the first design here with the power to detect one.
   Related: two "obvious" conclusions about run-to-run variance were drawn and
   retracted within a day, because stability turned out to be a property of the
   *model-task pair*, not of the metric. Every cell needs its own *n*.

6. **Each measurement tool we built came from a mistake we made.** The
   design-floor warning came from over-reading a single run; the test-integrity
   check came from wrongly concluding a model had rewritten its graded tests
   (the fixture ships them already modified, so `git` cannot attribute them); the
   polling-loop detector came from nearly publishing a wait loop as the cleanest
   phase purity on record — 792 no-op calls out of 880 had inflated it. The
   pattern held every time: when a number surprised us, the fix was to mechanise
   the check, not to eyeball it once and move on.

7. **Where it stands honestly:** the phase and cache results are solid and
   replicated; the *cross-model* claims are now a null result rather than a
   gradient; and the benchmark-validity findings are existence proofs, not rates —
   we can show that a grade hides fix quality, not how often. Still one model
   family plus two frontier models, small *n* per cell by construction (an
   isolated run is minutes-to-hours of real compute), and the raw run artifacts
   live outside the repo. The tool is done; the science has a clear, bounded
   reach left, not an open horizon.

## Takeaway

The most valuable habit this project rewarded wasn't writing code or landing a
result — it was keeping the **tool layer and the research layer distinct**, and
being willing to overturn the write-up whenever a new measurement disagreed with
it. A profiler cheap enough to add a controlled run and *immediately* see whether
it breaks your story is worth more than any single number it prints. That's the
thing worth keeping.
