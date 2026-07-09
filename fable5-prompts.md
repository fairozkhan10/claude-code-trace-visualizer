# Fable 5 prompts for this project

Prompt kit for when I run **Claude Fable 5** (`claude-fable-5`) on the trace-visualizer
research. Use Fable 5 selectively for the most ambitious updates; cheaper models (Sonnet/Haiku)
for routine profiling and doc edits.

**Sources:** the steering block, guardrails, and effort guidance below are adapted from
Anthropic's [Prompting Claude Fable 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5)
guide, folded into this project's rules (stdlib-only, honest-n, I commit/push myself). The
per-task templates are project-specific, built on the guide's "give the reason, not only the
request" principle.

---

## Session setup

Set **effort = high** (use `xhigh` for the hardest analysis — stats, phase-interaction
reasoning, findings write-ups). Paste the steering block below at the top of the first message.

### Steering block

```text
When you have enough information to act, act. Don't re-derive established facts or narrate
options you won't pursue.

Before reporting any research result or progress claim, audit it against an actual tool result
from this session — a regenerated report, a transcript, a computed number. Only report what you
can point to evidence for; if a metric isn't verified, say so. If a run failed or a fixture
didn't go red→green, say so with the output. State honest n and significance limits.

When I'm describing a problem or thinking out loud, the deliverable is your assessment — report
findings and stop. Don't commit, push, or apply a fix until I ask; give me the commands instead.

Keep the tool stdlib-only. Don't add features, refactor, or add abstractions beyond the task.
Reference the memory dir for prior lessons and record new durable ones there.
```

---

## Per-task templates

### Benchmark run

```text
I'm characterizing agentic-workload phase structure (explore→execute) for my summer research
with Shawn. I need a data point in the [long-debug / short-refactor] corner. Run <task> on
<repo> via claude -p ... --dangerously-skip-permissions, profile the transcript BY EXPLICIT
PATH (not --latest), regenerate html+json+flame, and give me purity/sep/cache%/decode-share.
Say whether it fits or breaks the 2x2 interaction. Don't commit anything.
```

### Verify a finding (spin a fresh subagent)

```text
Independently recompute <metric> across the regenerated run-*.json and check it against the
FINDINGS.md table. Report only discrepancies with file and number. Don't edit FINDINGS.
```

---

## Codebase-wide upgrades (Fable 5 as a builder, not just a research driver)

The templates above drive *research runs*. This section is for using Fable 5 to make
**significant upgrades to the `cc_trace/` tool itself** — the use case the Anthropic guide
calls Fable 5's strongest ("apply it to your hardest unsolved problems; start at the top of
your difficulty range"). Set **effort = high** (`xhigh` for a change that touches the whole
package at once).

### Step 1 — let Fable scope it (don't hand it a small diff)

Per the guide's "start at the top of your difficulty range": give it the ambitious goal and
have it scope + ask clarifying questions *before* writing code.

```text
I'm building cc_trace, the measurement layer for my summer agentic-workload research with
Shawn (UW–Madison). I want to make a significant upgrade to the tool as a whole, not a patch:
<the ambitious goal>. Read the codebase (cc_trace/parser.py, report.py, flame.py, compare.py,
stream.py, cli.py) and the memory dir first. Then scope the change: what you'd touch, the
design trade-offs, what could break the existing findings, and any open questions for me.
Ask before you start building. Don't write code yet — give me the plan and a recommendation.
```

### Step 2 — the build prompt (paste alongside the steering block)

Add this to the steering block so a whole-package change doesn't sprawl (Anthropic's
anti-over-engineering block, kept intact — it matters most on big changes at high effort):

```text
Don't add features, refactor, or introduce abstractions beyond what the task requires. A bug
fix doesn't need surrounding cleanup and a one-shot operation usually doesn't need a helper.
Don't design for hypothetical future requirements: do the simplest thing that works well.
Avoid premature abstraction and half-finished implementations. Don't add error handling,
fallbacks, or validation for scenarios that cannot happen. Trust internal code and framework
guarantees. Only validate at system boundaries. Keep it stdlib-only — no new deps.
```

### Step 3 — verify as it builds (fresh-context subagents beat self-critique)

```text
Establish a way to check your own work as you go. After each module you change, spin a fresh
subagent to verify it against the spec: run cc_trace end to end on examples/ and on a real
transcript, confirm the numbers in FINDINGS.md still reproduce (purity/sep/cache%/decode-share
unchanged unless the change intends to change them), and confirm no personal paths leak into
committed files. Report only what a tool result backs up.
```

### Step 4 — parallel subagents for a multi-module change

```text
Delegate independent subtasks to subagents and keep working while they run. Intervene if a
subagent goes off track or is missing relevant context.
```

**Candidate upgrades worth Fable 5 (finding-sized, per the model-selection policy):** the
automated test suite (none exists — the longest-standing robustness gap, top-value if others
build on cc_trace); network-isolated Docker harness for provenance-clean SWE-bench runs
(closes finding 11's retrieval leak); the hand-built E/G/D long-debug cross-model lane.
Routine parser fixes / report tweaks stay on Sonnet.

---

## Guardrails while prompting Fable 5

- **Don't** write "show your reasoning" / "explain your thinking" — trips the
  `reasoning_extraction` refusal. Ask for conclusions + evidence instead.
- Frame the eBPF/MITM work as **defensive measurement** ("observe our own agent's traffic to
  validate the parser"), and keep **Opus 4.8 fallback** on, so the cyber-safety classifier
  doesn't bounce the request.
- Turns run long on hard tasks — check long runs asynchronously, don't assume it hung.
