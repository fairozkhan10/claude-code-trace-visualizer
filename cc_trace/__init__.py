"""cc_trace — a workload profiler / trace visualizer for Claude Code.

Parses Claude Code session transcripts (the JSONL files under
``~/.claude/projects/<project>/<session>.jsonl``) into a structured trace and
renders a self-contained HTML dashboard showing what the agent did over time:
tool calls, timing, file access, retries/failures, and token/cost.

Motivated by *Agentic AI Workload Characteristics* (Yuan, Nayak, Kundu, Talati),
which finds agentic workloads are decode-dominated, lean heavily on KV-cache
reuse, and move through distinct phases (read/explore early -> execute/write
later). This tool measures those same characteristics for a real agent.
"""

__version__ = "0.1.0"

from .parser import parse_transcript, Trace, ToolCall  # noqa: F401
