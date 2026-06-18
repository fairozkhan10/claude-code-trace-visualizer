"""Parse a Claude Code session transcript (.jsonl) into a structured trace.

The transcript is one JSON object per line. The lines we care about:

* ``type == "assistant"`` — a model turn. ``message.content`` may contain
  ``tool_use`` blocks (the tool calls). ``message.usage`` has the token counts
  and ``message.model`` the model id. ``timestamp`` is when the turn completed,
  which we treat as the start of any tool calls it issued.
* ``type == "user"`` — when ``message.content`` holds ``tool_result`` blocks,
  this is a returned tool result. ``timestamp`` is when the result came back, so
  ``result_ts - tool_use_ts`` approximates the tool's wall-clock duration.
  ``is_error`` flags failures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

from .cost import turn_cost

# Tools that gather information vs. tools that change the world. The paper's core
# temporal finding is the shift from read/explore early to execute/write later.
EXPLORE_TOOLS = {
    "Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "ToolSearch",
    "NotebookRead", "TaskList", "TaskGet", "TaskOutput",
}
EXECUTE_TOOLS = {
    "Edit", "Write", "NotebookEdit", "Bash", "TaskCreate", "TaskUpdate",
    "TaskStop", "MultiEdit",
}

# Read-only shell command leaders — used to reclassify a Bash call as "explore"
# instead of "execute" when it's really just inspecting state.
READONLY_SHELL = {
    "ls", "cat", "head", "tail", "grep", "rg", "find", "pwd", "echo", "wc",
    "stat", "file", "which", "tree", "du", "df", "ps", "env", "printenv",
    "sort", "uniq", "cut", "awk", "sed", "diff", "less", "more", "type",
}
READONLY_GIT = {"status", "log", "diff", "show", "branch", "remote", "ls-files", "blame"}

FILE_INPUT_KEYS = ("file_path", "path", "notebook_path", "filePath")


def _phase_of(name: str, tool_input: dict) -> str:
    if name == "Bash":
        return _classify_bash(tool_input.get("command", ""))
    if name in EXPLORE_TOOLS:
        return "explore"
    if name in EXECUTE_TOOLS:
        return "execute"
    return "other"


def _classify_bash(command: str) -> str:
    """Best-effort read-only vs. mutating classification of a shell command."""
    # look at the first "real" token of the first segment
    seg = command.strip().split("&&")[0].split("|")[0].strip()
    parts = seg.split()
    if not parts:
        return "execute"
    lead = parts[0]
    if lead == "git" and len(parts) > 1:
        return "explore" if parts[1] in READONLY_GIT else "execute"
    return "explore" if lead in READONLY_SHELL else "execute"


def _files_touched(name: str, tool_input: dict) -> list[str]:
    for k in FILE_INPUT_KEYS:
        if k in tool_input and isinstance(tool_input[k], str):
            return [tool_input[k]]
    return []


def _label(name: str, tool_input: dict) -> str:
    """Short human-readable label for a tool call (for timeline tooltips)."""
    if name == "Bash":
        cmd = tool_input.get("command", "").replace("\n", " ")
        return cmd[:80]
    for k in FILE_INPUT_KEYS:
        if k in tool_input:
            return str(tool_input[k])
    if name in ("Grep", "Glob"):
        return str(tool_input.get("pattern", ""))[:80]
    if name in ("WebFetch", "WebSearch"):
        return str(tool_input.get("url") or tool_input.get("query", ""))[:80]
    if name == "Task" or name == "Agent":
        return str(tool_input.get("description", ""))[:80]
    return ""


def _parse_ts(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


@dataclass
class ToolCall:
    index: int
    id: str
    name: str
    label: str
    phase: str                       # explore | execute | other
    start: Optional[float]           # epoch seconds
    end: Optional[float]
    duration: Optional[float]        # seconds, end - start
    is_error: bool
    files: list[str]
    output_chars: int                # size of returned tool_result content
    turn: int                        # which assistant turn issued it


@dataclass
class Turn:
    index: int
    model: Optional[str]
    timestamp: Optional[float]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    n_tool_calls: int


@dataclass
class Trace:
    session_id: Optional[str]
    cwd: Optional[str]
    git_branch: Optional[str]
    models: list[str]
    start: Optional[float]
    end: Optional[float]
    tool_calls: list[ToolCall] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    user_prompts: list[str] = field(default_factory=list)

    # ---- derived summaries -------------------------------------------------
    @property
    def duration(self) -> float:
        if self.start is None or self.end is None:
            return 0.0
        return self.end - self.start

    @property
    def total_cost(self) -> float:
        return sum(t.cost_usd for t in self.turns)

    def token_totals(self) -> dict[str, int]:
        return {
            "input": sum(t.input_tokens for t in self.turns),
            "output": sum(t.output_tokens for t in self.turns),
            "cache_read": sum(t.cache_read_tokens for t in self.turns),
            "cache_write": sum(t.cache_write_tokens for t in self.turns),
        }

    def tool_breakdown(self) -> list[dict]:
        agg: dict[str, dict] = {}
        for tc in self.tool_calls:
            a = agg.setdefault(tc.name, {"name": tc.name, "count": 0,
                                         "duration": 0.0, "errors": 0})
            a["count"] += 1
            a["duration"] += tc.duration or 0.0
            a["errors"] += 1 if tc.is_error else 0
        return sorted(agg.values(), key=lambda x: x["count"], reverse=True)

    def file_access(self) -> list[dict]:
        agg: dict[str, dict] = {}
        for tc in self.tool_calls:
            for f in tc.files:
                a = agg.setdefault(f, {"file": f, "reads": 0, "writes": 0})
                if tc.phase == "explore":
                    a["reads"] += 1
                else:
                    a["writes"] += 1
        return sorted(agg.values(),
                      key=lambda x: x["reads"] + x["writes"], reverse=True)

    def phase_counts(self) -> dict[str, int]:
        out = {"explore": 0, "execute": 0, "other": 0}
        for tc in self.tool_calls:
            out[tc.phase] += 1
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "git_branch": self.git_branch,
            "models": self.models,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "total_cost": self.total_cost,
            "token_totals": self.token_totals(),
            "phase_counts": self.phase_counts(),
            "tool_breakdown": self.tool_breakdown(),
            "file_access": self.file_access(),
            "n_tool_calls": len(self.tool_calls),
            "n_turns": len(self.turns),
            "n_errors": sum(1 for tc in self.tool_calls if tc.is_error),
            "user_prompts": self.user_prompts,
            "tool_calls": [asdict(tc) for tc in self.tool_calls],
            "turns": [asdict(t) for t in self.turns],
        }


def parse_transcript(path: str) -> Trace:
    """Read a transcript .jsonl file and build a :class:`Trace`."""
    pending: dict[str, ToolCall] = {}       # tool_use_id -> ToolCall (awaiting result)
    tool_calls: list[ToolCall] = []
    turns: list[Turn] = []
    user_prompts: list[str] = []
    models: list[str] = []
    session_id = cwd = git_branch = None
    timestamps: list[float] = []
    turn_idx = 0
    call_idx = 0

    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            session_id = session_id or obj.get("sessionId")
            cwd = cwd or obj.get("cwd")
            git_branch = git_branch or obj.get("gitBranch")
            ts = _parse_ts(obj.get("timestamp"))
            if ts:
                timestamps.append(ts)

            typ = obj.get("type")
            msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}

            if typ == "assistant":
                model = msg.get("model")
                if model and model not in models:
                    models.append(model)
                usage = msg.get("usage", {}) or {}
                content = msg.get("content", []) or []
                calls_here = [c for c in content
                              if isinstance(c, dict) and c.get("type") == "tool_use"]
                turns.append(Turn(
                    index=turn_idx,
                    model=model,
                    timestamp=ts,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                    cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
                    cost_usd=turn_cost(model, usage),
                    n_tool_calls=len(calls_here),
                ))
                for c in calls_here:
                    name = c.get("name", "?")
                    tin = c.get("input", {}) if isinstance(c.get("input"), dict) else {}
                    tc = ToolCall(
                        index=call_idx,
                        id=c.get("id", f"call-{call_idx}"),
                        name=name,
                        label=_label(name, tin),
                        phase=_phase_of(name, tin),
                        start=ts,
                        end=None,
                        duration=None,
                        is_error=False,
                        files=_files_touched(name, tin),
                        output_chars=0,
                        turn=turn_idx,
                    )
                    pending[tc.id] = tc
                    tool_calls.append(tc)
                    call_idx += 1
                turn_idx += 1

            elif typ == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    # a plain user prompt
                    if content.strip():
                        user_prompts.append(content.strip()[:500])
                elif isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "text" and c.get("text", "").strip():
                            user_prompts.append(c["text"].strip()[:500])
                        elif c.get("type") == "tool_result":
                            tc = pending.pop(c.get("tool_use_id", ""), None)
                            if tc is None:
                                continue
                            tc.end = ts
                            if tc.start is not None and ts is not None:
                                tc.duration = max(0.0, ts - tc.start)
                            tc.is_error = bool(c.get("is_error"))
                            tc.output_chars = _content_len(c.get("content"))

    start = min(timestamps) if timestamps else None
    end = max(timestamps) if timestamps else None
    return Trace(
        session_id=session_id,
        cwd=cwd,
        git_branch=git_branch,
        models=models,
        start=start,
        end=end,
        tool_calls=tool_calls,
        turns=turns,
        user_prompts=user_prompts,
    )


def _content_len(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for c in content:
            if isinstance(c, dict):
                total += len(c.get("text", "") or "")
            elif isinstance(c, str):
                total += len(c)
        return total
    return 0
