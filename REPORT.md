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
- **Live mode** — profile a run in-flight from `--output-format stream-json`.
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
parsing is heuristic, there's no automated test suite, and it's single-machine.
Done for its purpose; not hardened for strangers.

## What we learned

The tool was the deliverable, but using it produced a genuinely interesting result
— and the *way* it evolved is the real lesson.

1. **The headline finding got better by being wrong twice.** It went from a clean
   *refactor-vs-debug dichotomy* → a *difficulty continuum* → finally a
   **task-kind × difficulty interaction**. Each correction came from adding one
   *controlled* run, not from re-analyzing old data. When it was tempting to pad the
   sample size to confirm a confounded story, the right move was to **control the
   confound instead** — and that turned a dead end into a sharper claim.

2. **Three findings are solid:** KV-cache reuse is universal (~94–99% of context
   reused per turn); **cost tracks context accumulation, not output** (one session
   spent its money on 72M cache-read tokens against 457K output); and the phase
   shift is an interaction that **reproduced on real, non-benchmark sessions**.

3. **Where it stands honestly:** directionally strong, not yet
   publication-significant — n=2 in the decisive corners, one model (Opus 4.8). The
   next steps are *research* (more reps, a second model), not *features*. The tool
   is basically done; the science isn't.

## Takeaway

The most valuable habit this project rewarded wasn't writing code or landing a
result — it was keeping the **tool layer and the research layer distinct**, and
being willing to overturn the write-up whenever a new measurement disagreed with
it. A profiler cheap enough to add a controlled run and *immediately* see whether
it breaks your story is worth more than any single number it prints. That's the
thing worth keeping.
