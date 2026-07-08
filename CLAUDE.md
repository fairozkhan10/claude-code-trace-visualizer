# CLAUDE.md

Model selection is a per-session choice (`/model`) — not pinned. Use **Claude Fable 5**
(`claude-fable-5`) selectively for the most ambitious updates; cheaper models (Sonnet/Haiku)
for routine profiling runs and doc edits.

**When running Fable 5 on this project, read [`fable5-prompts.md`](fable5-prompts.md) first** —
it has the steering block, per-task prompt templates, and Fable 5 guardrails (reasoning-echo
refusal, cyber/MITM classifier, long turns) adapted from Anthropic's Fable 5 prompting guide.
