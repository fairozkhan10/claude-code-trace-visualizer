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

## Guardrails while prompting Fable 5

- **Don't** write "show your reasoning" / "explain your thinking" — trips the
  `reasoning_extraction` refusal. Ask for conclusions + evidence instead.
- Frame the eBPF/MITM work as **defensive measurement** ("observe our own agent's traffic to
  validate the parser"), and keep **Opus 4.8 fallback** on, so the cyber-safety classifier
  doesn't bounce the request.
- Turns run long on hard tasks — check long runs asynchronously, don't assume it hung.
