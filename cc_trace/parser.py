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

import difflib
import json
import re
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

# --- Bash file-I/O extraction -------------------------------------------------
# Agents do a lot of their file I/O through the shell (output redirects, here-docs
# writing files, `tee`, and running scripts), none of which shows up as a tool
# `file_path`. We parse the command string so file access isn't under-counted.
#
# Writes: output redirects (`>`, `>>`, `2>`, `&>`) and `tee`. Reads: the file/
# script argument of common read-or-run commands. Best-effort & heuristic.
_REDIR_RE = re.compile(r"(?:\d*>>?|&>>?)\s*(\"[^\"]+\"|'[^']+'|[^\s;|&<>]+)")
_TEE_RE = re.compile(r"\btee\s+(?:-a\s+)?(\"[^\"]+\"|'[^']+'|[^\s;|&<>]+)")
# Commands whose first path-like argument is a file being read or executed.
READ_OR_RUN_CMDS = {
    "cat", "head", "tail", "less", "more", "wc", "nl", "od", "diff",
    "python", "python3", "pytest", "node", "bash", "sh", "ruby", "go",
    "source", "grep", "rg",
}


# Characters that mean a token is shell/code, not a plain filename. Filenames
# with these are vanishingly rare; inline code fragments are full of them.
_BAD_PATH_CHARS = set("()[]{};*$=<>\"'`!\\ ")


def _is_pathish(tok: str) -> bool:
    """Cheap filter: looks like a filename/path, not a flag, glob, or code."""
    tok = tok.strip().strip("\"'").split("::")[0]   # drop pytest ::nodeid suffix
    if not tok or tok.startswith("-"):
        return False
    if tok.startswith("/dev/"):                     # /dev/null and friends
        return False
    if tok.endswith("/"):                           # a directory (e.g. grep's dir arg), not a file
        return False
    if any(ch in _BAD_PATH_CHARS for ch in tok):
        return False
    # a path separator, or an extension (a dot that isn't a leading dotfile dot)
    return ("/" in tok) or ("." in tok[1:])


def _bash_files(command: str) -> list[tuple[str, str]]:
    """Best-effort (path, mode) pairs touched by a shell command.

    ``mode`` is ``"read"`` or ``"write"``; a path written *and* read resolves to
    ``"write"`` (the side effect we care about).
    """
    ops: dict[str, str] = {}

    def add(path: str, mode: str) -> None:
        if not _is_pathish(path):
            return
        path = path.strip().strip("\"'").split("::")[0]
        if mode == "write" or path not in ops:
            ops[path] = mode if mode == "write" else ops.get(path, "read")

    for m in _REDIR_RE.finditer(command):
        add(m.group(1), "write")
    for m in _TEE_RE.finditer(command):
        add(m.group(1), "write")

    # reads / script runs: first path-like arg after a read-or-run command
    words = command.replace("|", " | ").split()
    for i, w in enumerate(words):
        if w in READ_OR_RUN_CMDS or w.split("/")[-1] in READ_OR_RUN_CMDS:
            for nxt in words[i + 1:]:
                if nxt in ("|", "&&", "||", ";", ">", ">>", "<"):
                    break
                if nxt in ("-c", "-e", "-m"):     # inline code/module, not a file
                    break
                if nxt.startswith("-"):
                    continue
                if _is_pathish(nxt):
                    add(nxt, "read")
                    break
    return list(ops.items())


# --- Network-activity extraction ---------------------------------------------
# The agent reaches the network mostly through the shell (curl/wget, git remote
# ops, package installs, ssh/scp) plus the WebFetch/WebSearch/MCP tools. None of
# it is a structured field, so — as with file I/O above — we parse the command
# string. This captures the network the *agent* initiates; it does NOT see Claude
# Code's own model API calls (those aren't in the transcript). Best-effort.
_URL_RE = re.compile(r"((?:https?|ftp|git|ssh)://[^\s'\"|;&>)]+)")
_SCP_RE = re.compile(r"\b([\w.-]+@[\w.-]+:[^\s'\"|;&>]+)")   # git@host:path, user@host:path
_NET_CMDS = {
    "curl": "http", "wget": "http", "http": "http", "https": "http",
    "aria2c": "http", "httpie": "http", "ssh": "ssh", "scp": "ssh",
    "sftp": "ssh", "rsync": "ssh", "nc": "socket", "netcat": "socket",
    "telnet": "socket", "ping": "probe", "dig": "dns", "nslookup": "dns",
    "host": "dns", "gh": "api", "hub": "api",
}
_PKG_CMDS = {"pip", "pip3", "uv", "npm", "yarn", "pnpm", "poetry", "pipenv",
             "gem", "cargo", "go", "apt", "apt-get", "brew", "conda", "bundle"}
_PKG_NET_SUB = {"install", "download", "add", "ci", "fetch", "update",
                "upgrade", "get", "sync", "i"}
_GIT_NET_SUB = {"clone", "fetch", "pull", "push", "ls-remote", "remote",
                "submodule"}


def _host_of(url: str) -> str:
    """Compact host[/path-head] label from a URL or scp-style target."""
    u = url.strip().strip("\"'")
    for pre in ("https://", "http://", "ftp://", "git://", "ssh://"):
        if u.startswith(pre):
            u = u[len(pre):]
            break
    if "@" in u.split("/")[0]:          # strip user@ / git@ from the host part
        u = u.split("@", 1)[1]
    return u[:60]


def _bash_network(command: str) -> list[tuple[str, str]]:
    """Best-effort (kind, target) network operations in a shell command.

    ``kind`` is http / git / package / ssh / dns / socket / probe / api; ``target``
    is a host, URL, or short descriptor. Catches curl/wget, git remote ops,
    package installs, ssh/scp and a few probes — the network the agent reaches
    through the shell. Heuristic, like :func:`_bash_files`.
    """
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, target: str) -> None:
        key = (kind, target or kind)
        if key not in seen:
            seen.add(key)
            out.append(key)

    for seg in re.split(r"[;|&\n]+", command):
        words = seg.split()
        i = 0
        while i < len(words) and (
            words[i] in ("sudo", "time", "env", "nohup", "xargs")
            or ("=" in words[i] and "/" not in words[i])
        ):
            i += 1
        if i >= len(words):
            continue
        lead = words[i].split("/")[-1]
        rest = words[i + 1:]
        urls = _URL_RE.findall(seg)
        scps = _SCP_RE.findall(seg)

        if lead == "git":
            sub = next((w for w in rest if not w.startswith("-")), "")
            if sub in _GIT_NET_SUB:
                # remote/url if present, else the named remote (e.g. "origin")
                remote = next((w for w in rest
                               if w != sub and not w.startswith("-")), "")
                tgt = (_host_of(urls[0]) if urls else
                       _host_of(scps[0]) if scps else remote or sub)
                add("git", tgt)
        elif lead in _PKG_CMDS:
            # net subcommand may not be first (e.g. `uv pip install`, `cargo …`)
            sub = next((w for w in rest if w in _PKG_NET_SUB), "")
            if sub:
                pkg = next((w for w in rest[rest.index(sub) + 1:]
                            if not w.startswith("-")), "")
                add("package", f"{lead} {sub} {pkg}".strip()[:60])
        elif lead in _NET_CMDS:
            kind = _NET_CMDS[lead]
            if urls:
                add(kind, _host_of(urls[0]))
            elif scps:
                add(kind, _host_of(scps[0]))
            else:
                tgt = next((w for w in rest if not w.startswith("-")), lead)
                add(kind, _host_of(tgt))
    return out


def _tool_network(name: str, tool_input: dict) -> list[tuple[str, str]]:
    """Network operations implied by a tool call, as (kind, target) pairs."""
    if name == "Bash":
        return _bash_network(tool_input.get("command", ""))
    if name == "WebFetch":
        return [("http", _host_of(str(tool_input.get("url", ""))))]
    if name == "WebSearch":
        return [("search", str(tool_input.get("query", ""))[:60])]
    if name.startswith("mcp__"):
        return [("mcp", name[len("mcp__"):][:60])]
    return []


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


def _file_ops(name: str, tool_input: dict) -> list[tuple[str, str]]:
    """Files touched by a tool call, as (path, "read"|"write") pairs."""
    if name == "Bash":
        return _bash_files(tool_input.get("command", ""))
    for k in FILE_INPUT_KEYS:
        if k in tool_input and isinstance(tool_input[k], str):
            mode = "read" if name in EXPLORE_TOOLS else "write"
            return [(tool_input[k], mode)]
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


# --- Repeated-work / near-duplicate detection --------------------------------
# Shawn's optimisation angle: an agent that re-issues the same *or similar*
# command (re-runs the same test, re-reads the same file) is doing cacheable
# work. retry_loops() already catches exact repeats that errored; to catch
# *near*-duplicates we reduce each Bash call to a normalised signature —
# numbers, quoted strings and file paths collapse to placeholders — so
# `pytest tests/a.py` and `pytest tests/b.py` cluster together while `pytest`
# and `git status` stay apart. A difflib pass then merges signatures that are
# textually very close (e.g. one carries an extra `-v` flag). Non-Bash calls
# keep their structured target (re-reading the *same* file is the repeat).
_NUM_RE = re.compile(r"\b\d[\w.]*\b")
_STR_RE = re.compile(r"'[^']*'|\"[^\"]*\"")


def _bash_signature(label: str) -> str:
    """Normalised signature of a Bash command for near-duplicate grouping."""
    s = _STR_RE.sub("STR", (label or "").replace("\n", " ").strip())
    s = _NUM_RE.sub("N", s)
    toks = ["PATH" if _is_pathish(t) else t for t in s.split()]
    return "Bash:" + " ".join(toks)


def _call_signature(name: str, label: str, files: list[str]) -> Optional[str]:
    """Grouping key for repeated-work detection; ``None`` to ignore the call."""
    if name == "Bash":
        return _bash_signature(label)
    target = files[0] if files else (label or "")
    return f"{name}:{target}" if target else None


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
    files: list[str]                 # paths touched (any mode)
    file_modes: dict[str, str]       # path -> "read" | "write"
    output_chars: int                # size of returned tool_result content
    turn: int                        # which assistant turn issued it
    network: list[dict] = field(default_factory=list)  # [{kind, target}] net ops


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
                # prefer the per-file mode; fall back to the call's phase
                mode = tc.file_modes.get(f) if tc.file_modes else None
                if mode is None:
                    mode = "read" if tc.phase == "explore" else "write"
                a["writes" if mode == "write" else "reads"] += 1
        return sorted(agg.values(),
                      key=lambda x: x["reads"] + x["writes"], reverse=True)

    def network_activity(self) -> dict:
        """Network the agent reached *through its tools* — curl/wget, git remote
        ops, package installs, ssh/scp, plus WebFetch/WebSearch/MCP calls.

        Parsed from command strings & tool inputs (see the network-extraction
        helpers); best-effort. Does **not** include Claude Code's own model API
        calls — those never appear in the transcript.
        """
        by_kind: dict[str, int] = {}
        reqs: list[dict] = []
        for tc in self.tool_calls:
            for op in tc.network:
                by_kind[op["kind"]] = by_kind.get(op["kind"], 0) + 1
                reqs.append({"index": tc.index, "turn": tc.turn, "tool": tc.name,
                             "kind": op["kind"], "target": op["target"],
                             "error": tc.is_error})
        return {
            "total": len(reqs),
            "by_kind": [{"kind": k, "count": c}
                        for k, c in sorted(by_kind.items(), key=lambda kv: -kv[1])],
            "requests": reqs,
        }

    def phase_counts(self) -> dict[str, int]:
        out = {"explore": 0, "execute": 0, "other": 0}
        for tc in self.tool_calls:
            out[tc.phase] += 1
        return out

    def phase_crossover(self) -> dict:
        """Where the run flips from explore-heavy to execute-heavy.

        Finds the split point ``k`` over the ordered explore/execute calls that
        best separates an explore prefix from an execute suffix (maximises
        ``#explore before k + #execute after k``). ``pos`` is that point as a
        fraction of the run; ``purity`` is how cleanly the run splits there
        (1.0 = a perfect read-then-write phase shift, ~0.5 = no structure /
        fully interleaved). Complements ``sep`` from the compare view.
        """
        phases = [tc.phase for tc in self.tool_calls
                  if tc.phase in ("explore", "execute")]
        n = len(phases)
        if n == 0:
            return {"index": None, "pos": None, "purity": None, "n": 0}
        exec_after = sum(1 for p in phases if p == "execute")
        expl_before = 0
        best_k, best_score = 0, exec_after          # k = 0: empty prefix
        for k in range(1, n + 1):
            if phases[k - 1] == "explore":
                expl_before += 1
            else:
                exec_after -= 1
            score = expl_before + exec_after
            if score > best_score:
                best_score, best_k = score, k
        return {"index": best_k, "pos": round(best_k / n, 3),
                "purity": round(best_score / n, 3), "n": n}

    def retry_loops(self, min_attempts: int = 2) -> list[dict]:
        """Where the agent repeated the same tool on the same target with errors.

        Groups tool calls by (tool, target) — target being the first file touched,
        else the (truncated) command/label — and flags any group hit
        ``min_attempts`` times with at least one error. These are the candidate
        retry loops: a command that keeps failing, an edit that keeps being redone.
        """
        groups: dict[tuple[str, str], dict] = {}
        for tc in self.tool_calls:
            target = tc.files[0] if tc.files else (tc.label or "")
            if not target:
                continue
            g = groups.setdefault((tc.name, target), {
                "tool": tc.name, "target": target, "attempts": 0,
                "errors": 0, "indices": [], "starts": []})
            g["attempts"] += 1
            g["errors"] += 1 if tc.is_error else 0
            g["indices"].append(tc.index)
            if tc.start is not None:
                g["starts"].append(tc.start)

        loops = []
        for g in groups.values():
            if g["attempts"] >= min_attempts and g["errors"] >= 1:
                starts = g["starts"]
                loops.append({
                    "tool": g["tool"], "target": g["target"],
                    "attempts": g["attempts"], "errors": g["errors"],
                    "first_index": g["indices"][0], "last_index": g["indices"][-1],
                    "span_s": (max(starts) - min(starts)) if len(starts) > 1 else 0.0,
                })
        return sorted(loops, key=lambda x: (x["errors"], x["attempts"]), reverse=True)

    def repeated_work(self, min_repeats: int = 2, similarity: float = 0.9) -> dict:
        """Repeated / near-duplicate work — the caching-opportunity signal.

        Generalises :meth:`retry_loops` (which only flags repeats that *errored*)
        to ALL repetition: re-running the same test, re-reading the same file,
        re-issuing a slightly varied command. Bash calls are grouped by a
        normalised signature (see :func:`_bash_signature`) and near-identical
        signatures are then merged with a ``difflib`` ratio pass, so
        ``pytest a.py`` and ``pytest b.py -q`` land together; non-Bash calls are
        grouped by their structured target (re-reading the *same* file). Each
        cluster of >= ``min_repeats`` calls is work a cache/memoiser could
        collapse. Returns a summary (how much of the run was redundant) plus the
        clusters, ranked by redundant count then time spent on the repeats.
        """
        members: dict[str, list[ToolCall]] = {}
        for tc in self.tool_calls:
            sig = _call_signature(tc.name, tc.label, tc.files)
            if sig is not None:
                members.setdefault(sig, []).append(tc)

        # union-find merge of textually near-identical Bash signatures
        bash_sigs = [s for s in members if s.startswith("Bash:")]
        parent = {s: s for s in bash_sigs}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, a in enumerate(bash_sigs):
            for b in bash_sigs[i + 1:]:
                if find(a) != find(b) and \
                        difflib.SequenceMatcher(None, a, b).ratio() >= similarity:
                    parent[find(b)] = find(a)

        merged: dict[str, list[ToolCall]] = {}
        for sig, tcs in members.items():
            merged.setdefault(find(sig) if sig in parent else sig, []).extend(tcs)

        clusters = []
        for tcs in merged.values():
            if len(tcs) < min_repeats:
                continue
            tcs.sort(key=lambda t: t.index)
            labels = [t.label or "" for t in tcs]
            starts = [t.start for t in tcs if t.start is not None]
            durs = [t.duration or 0.0 for t in tcs]
            clusters.append({
                "tool": tcs[0].name,
                "example": min((l for l in labels if l), key=len, default=tcs[0].name),
                "count": len(tcs),
                "redundant": len(tcs) - 1,
                "distinct": len(set(labels)),
                "exact": len(set(labels)) == 1,
                "errors": sum(1 for t in tcs if t.is_error),
                "first_index": tcs[0].index, "last_index": tcs[-1].index,
                "span_s": round(max(starts) - min(starts), 1) if len(starts) > 1 else 0.0,
                "redundant_s": round(sum(durs[1:]), 1),   # time on repeat invocations
            })
        clusters.sort(key=lambda c: (c["redundant"], c["redundant_s"]), reverse=True)

        n = len(self.tool_calls)
        redundant = sum(c["redundant"] for c in clusters)
        return {
            "n_tool_calls": n,
            "n_clusters": len(clusters),
            "redundant_calls": redundant,
            "redundant_frac": round(redundant / n, 3) if n else 0.0,
            "redundant_s": round(sum(c["redundant_s"] for c in clusters), 1),
            "clusters": clusters,
        }

    def file_graph(self, window: int = 4, max_nodes: int = 24) -> dict:
        """Co-access graph: files worked on near each other in the run.

        Nodes are files (with read/write counts); an edge joins two files that
        are touched within ``window`` consecutive file-accesses of each other,
        weighted by how often that happens. This surfaces the clusters of files
        an agent edits together. Capped to the ``max_nodes`` most-accessed files
        to keep the picture legible.
        """
        seq: list[tuple[str, str]] = []   # ordered (file, mode) accesses
        nodes: dict[str, dict] = {}
        for tc in self.tool_calls:
            for f in tc.files:
                mode = tc.file_modes.get(f) if tc.file_modes else None
                if mode is None:
                    mode = "read" if tc.phase == "explore" else "write"
                seq.append((f, mode))
                n = nodes.setdefault(f, {"file": f, "reads": 0, "writes": 0})
                n["writes" if mode == "write" else "reads"] += 1

        keep = {n["file"] for n in sorted(
            nodes.values(), key=lambda x: x["reads"] + x["writes"], reverse=True
        )[:max_nodes]}

        edges: dict[tuple[str, str], int] = {}
        files = [f for f, _ in seq]
        for i, a in enumerate(files):
            if a not in keep:
                continue
            for b in files[i + 1:i + window]:
                if b == a or b not in keep:
                    continue
                edges[tuple(sorted((a, b)))] = edges.get(tuple(sorted((a, b))), 0) + 1

        return {
            "window": window,
            "nodes": [n for n in sorted(
                nodes.values(), key=lambda x: x["reads"] + x["writes"], reverse=True)
                if n["file"] in keep],
            "edges": [{"source": a, "target": b, "weight": w}
                      for (a, b), w in sorted(edges.items(), key=lambda kv: -kv[1])],
        }

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
            "network_activity": self.network_activity(),
            "file_graph": self.file_graph(),
            "phase_crossover": self.phase_crossover(),
            "retry_loops": self.retry_loops(),
            "repeated_work": self.repeated_work(),
            "n_tool_calls": len(self.tool_calls),
            "n_turns": len(self.turns),
            "n_errors": sum(1 for tc in self.tool_calls if tc.is_error),
            "user_prompts": self.user_prompts,
            "tool_calls": [asdict(tc) for tc in self.tool_calls],
            "turns": [asdict(t) for t in self.turns],
        }


class _Builder:
    """Incrementally assemble a :class:`Trace` from transcript/stream events.

    ``feed(obj, ts)`` processes one event dict; ``ts`` is its epoch time — from
    the transcript's ``timestamp`` when replaying a file, or wall-clock arrival
    time when reading a live ``--output-format stream-json`` stream. This is the
    shared core behind both :func:`parse_transcript` and the live stream parser.
    """

    def __init__(self) -> None:
        self.pending: dict[str, ToolCall] = {}     # tool_use_id -> awaiting result
        self.tool_calls: list[ToolCall] = []
        self.turns: list[Turn] = []
        self.user_prompts: list[str] = []
        self.models: list[str] = []
        self.session_id = self.cwd = self.git_branch = None
        self.timestamps: list[float] = []
        self.turn_idx = 0
        self.call_idx = 0

    def feed(self, obj: dict, ts: Optional[float] = None) -> None:
        if not isinstance(obj, dict):
            return
        # stream-json uses session_id/snake; transcript uses sessionId/camel
        self.session_id = self.session_id or obj.get("sessionId") or obj.get("session_id")
        self.cwd = self.cwd or obj.get("cwd")
        self.git_branch = self.git_branch or obj.get("gitBranch")
        if ts:
            self.timestamps.append(ts)

        typ = obj.get("type")
        msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}

        if typ == "assistant":
            model = msg.get("model")
            if model and model not in self.models:
                self.models.append(model)
            usage = msg.get("usage", {}) or {}
            content = msg.get("content", []) or []
            calls_here = [c for c in content
                          if isinstance(c, dict) and c.get("type") == "tool_use"]
            self.turns.append(Turn(
                index=self.turn_idx,
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
                ops = _file_ops(name, tin)
                net = _tool_network(name, tin)
                tc = ToolCall(
                    index=self.call_idx,
                    id=c.get("id", f"call-{self.call_idx}"),
                    name=name,
                    label=_label(name, tin),
                    phase=_phase_of(name, tin),
                    start=ts,
                    end=None,
                    duration=None,
                    is_error=False,
                    files=[p for p, _ in ops],
                    file_modes=dict(ops),
                    output_chars=0,
                    turn=self.turn_idx,
                    network=[{"kind": k, "target": t} for k, t in net],
                )
                self.pending[tc.id] = tc
                self.tool_calls.append(tc)
                self.call_idx += 1
            self.turn_idx += 1

        elif typ == "user":
            content = msg.get("content")
            if isinstance(content, str):
                if content.strip():
                    self.user_prompts.append(content.strip()[:500])
            elif isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "text" and c.get("text", "").strip():
                        self.user_prompts.append(c["text"].strip()[:500])
                    elif c.get("type") == "tool_result":
                        tc = self.pending.pop(c.get("tool_use_id", ""), None)
                        if tc is None:
                            continue
                        tc.end = ts
                        if tc.start is not None and ts is not None:
                            tc.duration = max(0.0, ts - tc.start)
                        tc.is_error = bool(c.get("is_error"))
                        tc.output_chars = _content_len(c.get("content"))

    def build(self) -> Trace:
        return Trace(
            session_id=self.session_id,
            cwd=self.cwd,
            git_branch=self.git_branch,
            models=self.models,
            start=min(self.timestamps) if self.timestamps else None,
            end=max(self.timestamps) if self.timestamps else None,
            tool_calls=self.tool_calls,
            turns=self.turns,
            user_prompts=self.user_prompts,
        )


def parse_transcript(path: str) -> Trace:
    """Read a transcript .jsonl file and build a :class:`Trace`."""
    b = _Builder()
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            b.feed(obj, _parse_ts(obj.get("timestamp")))
    return b.build()


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
