"""Command-line entry point.

Examples
--------
    # profile the most recent Claude Code session
    python -m cc_trace --latest

    # profile a specific transcript and open it
    python -m cc_trace path/to/session.jsonl --open

    # list available sessions
    python -m cc_trace --list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import glob
import webbrowser
from pathlib import Path

from .parser import parse_transcript
from .report import render_html

PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _compare_main(argv: list[str]) -> int:
    """`cc-trace compare A.json B.jsonl <session-id> …` — cross-run rollup."""
    from . import compare as cmp

    ap = argparse.ArgumentParser(
        prog="cc-trace compare",
        description="Compare workload characteristics across several runs.")
    ap.add_argument("targets", nargs="+",
                    help="parsed .json reports, .jsonl transcripts, or session ids")
    ap.add_argument("-o", "--out", default=None,
                    help="write an HTML rollup to this path (in addition to the table)")
    ap.add_argument("--open", action="store_true",
                    help="open the HTML rollup in a browser when done")
    args = ap.parse_args(argv)

    rows = []
    for tgt in args.targets:
        try:
            rows.append(cmp.summarize(cmp.load_trace_dict(tgt, _resolve)))
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
            print(f"skipping {tgt}: {e}", file=sys.stderr)
    if not rows:
        print("no usable runs to compare", file=sys.stderr)
        return 1

    print(cmp.render_text(rows))
    if args.out:
        out = Path(args.out)
        if out.parent != Path(""):
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(cmp.render_html(rows), encoding="utf-8")
        print(f"\nwrote {out}", file=sys.stderr)
        if args.open:
            webbrowser.open(f"file://{out.resolve()}")
    return 0


def _all_transcripts() -> list[Path]:
    return sorted(
        PROJECTS_DIR.glob("*/*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _resolve(target: str | None, latest: bool) -> Path | None:
    if latest:
        ts = _all_transcripts()
        return ts[0] if ts else None
    if not target:
        return None
    p = Path(target)
    if p.is_file():
        return p
    # treat as a session id (possibly a prefix)
    for t in _all_transcripts():
        if t.stem == target or t.stem.startswith(target):
            return t
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "compare":
        return _compare_main(argv[1:])

    ap = argparse.ArgumentParser(
        prog="cc-trace",
        description="Trace and visualize what Claude Code does during a run.",
    )
    ap.add_argument("target", nargs="?",
                    help="path to a .jsonl transcript, or a session id / prefix")
    ap.add_argument("--latest", action="store_true",
                    help="use the most recent Claude Code session")
    ap.add_argument("--list", action="store_true",
                    help="list available sessions and exit")
    ap.add_argument("-o", "--out", default=None,
                    help="output HTML path (default: ./<session>.html)")
    ap.add_argument("--json", action="store_true",
                    help="also write the parsed trace as <out>.json")
    ap.add_argument("--open", action="store_true",
                    help="open the report in a browser when done")
    args = ap.parse_args(argv)

    if args.list:
        ts = _all_transcripts()
        if not ts:
            print(f"No transcripts under {PROJECTS_DIR}", file=sys.stderr)
            return 1
        for t in ts[:40]:
            mtime = t.stat().st_mtime
            from datetime import datetime
            print(f"{datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}  "
                  f"{t.stem[:8]}  {t.parent.name}")
        return 0

    path = _resolve(args.target, args.latest)
    if path is None:
        ap.error("no transcript found — pass a path/session id or --latest "
                 "(try --list)")
    print(f"parsing {path}", file=sys.stderr)
    trace = parse_transcript(str(path))

    out = Path(args.out) if args.out else Path(f"{trace.session_id or path.stem}.html")
    if out.parent != Path(""):
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(trace), encoding="utf-8")
    print(f"wrote {out}  "
          f"({len(trace.tool_calls)} tool calls, {len(trace.turns)} turns, "
          f"{trace.duration:.0f}s, ${trace.total_cost:.3f})")

    if args.json:
        jpath = out.with_suffix(".json")
        jpath.write_text(json.dumps(trace.as_dict(), indent=2), encoding="utf-8")
        print(f"wrote {jpath}")

    if args.open:
        webbrowser.open(f"file://{out.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
