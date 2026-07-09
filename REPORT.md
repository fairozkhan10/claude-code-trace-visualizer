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
  repeated-work clusters (a caching/optimization signal), errors.
- **Cross-run compare** — roll many runs into one table to spot patterns.
- **Flame graph** — a run stacked `phase → tool → target`, *coloured by the
  explore→execute phase* (interactive HTML, or `.folded` for speedscope/pprof).
- **Live mode** — profile a run in-flight from `--output-format stream-json`.
- **SWE-bench harness** — run standard-benchmark instances through the same
  profiler (clone → drive `claude -p` → confirm red→green → profile).
- **Honest Bash parsing** — file *and* network I/O done through the shell (the way
  agents actually work) is parsed out of command strings, not ignored.

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
— and the *way* it evolved is the real lesson. (Full detail, 11 findings, in
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

4. **Where it stands honestly:** substantially more than directional now, but still
   one model family on small *n*, and the *long-debug* corner that breaks the phase
   shift hasn't been captured on a standard benchmark (SWE-bench Lite is too tractable
   to elicit it — that needs harder, under-specified bugs). The tool is done; the
   science has a clear, bounded reach left, not an open horizon.

## Takeaway

The most valuable habit this project rewarded wasn't writing code or landing a
result — it was keeping the **tool layer and the research layer distinct**, and
being willing to overturn the write-up whenever a new measurement disagreed with
it. A profiler cheap enough to add a controlled run and *immediately* see whether
it breaks your story is worth more than any single number it prints. That's the
thing worth keeping.
