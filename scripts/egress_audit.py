"""Turn an egress-proxy log into a signal/noise breakdown, and check the parser.

Shawn's question (2026-07-27): is tracing *all* non-model network requests
useful, or does it drown the interesting behaviour in noise?

The answer is that raw CONNECT volume is almost entirely noise, but it partitions
cleanly into three classes and only the third is about the agent at all:

* **model**       — the inference API. Unavoidable, and ~92% of all connections
                    on the run we have. Never interesting.
* **vendor**      — the harness's own control plane (crash/telemetry endpoints).
                    Constant-rate background, independent of the task.
* **agent**       — reached because the agent ran a command. This is the class
                    finding 11 cares about, and it is small enough to read by eye.

Two things come out of the split. The denials inside the ``agent`` class are the
retrieval-attempt record — a blocked request may never surface as a tool call.
And because the proxy sees every connection the sandbox made, the ``agent`` class
is *ground truth* for `cc_trace`'s command-string network parsing, which is
otherwise unvalidated on the positive case: pass ``--transcript`` to score it.

Usage::

    python3 scripts/egress_audit.py reports/<run>/egress.jsonl \
        --transcript reports/<run>/transcript.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# Hosts that are the harness talking, not the agent. Substring match on the
# host, so regional shards (`http-intake.logs.us5.datadoghq.com`) are covered.
MODEL_HOSTS = ("api.anthropic.com",)
VENDOR_HOSTS = ("datadoghq.com", "sentry.io", "statsig.com", "statsig.antmetrics.com")

# A package install is recorded by the parser as a descriptor (`pip install
# mpmath`) with no hostname in it, because the host never appears in the command.
# To score it against the proxy's hostnames we need the index each tool reaches.
PKG_INDEX = {
    "pip": "pypi.org", "pip3": "pypi.org", "uv": "pypi.org",
    "poetry": "pypi.org", "pipenv": "pypi.org", "conda": "anaconda.com",
    "npm": "npmjs.org", "yarn": "npmjs.org", "pnpm": "npmjs.org",
    "cargo": "crates.io", "gem": "rubygems.org", "go": "golang.org",
    "apt": "debian.org", "apt-get": "debian.org", "brew": "githubusercontent.com",
}


def classify(host: str) -> str:
    h = (host or "").lower()
    if any(m in h for m in MODEL_HOSTS):
        return "model"
    if any(v in h for v in VENDOR_HOSTS):
        return "vendor"
    return "agent"


def load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log", type=Path, help="egress.jsonl from egress_proxy.py")
    ap.add_argument("--transcript", type=Path,
                    help="score cc_trace's network parsing against the agent class")
    args = ap.parse_args(argv)

    rows = load(args.log)
    if not rows:
        print(f"no usable records in {args.log}", file=sys.stderr)
        return 1

    by_class = Counter(classify(r.get("host", "")) for r in rows)
    span = max(r["t"] for r in rows) - min(r["t"] for r in rows)

    print(f"{len(rows)} connections over {span/60:.0f} min\n")
    print(f"{'class':8s} {'conns':>6s} {'share':>7s}  hosts")
    for cls in ("model", "vendor", "agent"):
        sub = [r for r in rows if classify(r.get("host", "")) == cls]
        if not sub:
            continue
        hosts = Counter(r.get("host", "?") for r in sub)
        detail = ", ".join(f"{h} x{n}" for h, n in hosts.most_common(4))
        print(f"{cls:8s} {len(sub):6d} {len(sub)/len(rows):6.1%}  {detail}")

    denied = [r for r in rows if r.get("decision") == "deny"]
    agent_denied = [r for r in denied if classify(r.get("host", "")) == "agent"]
    print(f"\ndenied: {len(denied)} ({len(agent_denied)} agent-initiated)")
    for h, n in Counter(r.get("host", "?") for r in agent_denied).most_common():
        print(f"  {n:4d}  {h}")
    if not agent_denied:
        print("  (none — the agent attempted no blocked retrieval)")

    if args.transcript:
        # ground-truth check: every host the agent's commands actually reached,
        # against every host cc_trace inferred from those command strings
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from cc_trace.parser import parse_transcript

        # `?` is the proxy's placeholder for a connection it could not read a
        # host from — not a reached host, so it can't be scored
        truth = {r.get("host", "") for r in rows
                 if classify(r.get("host", "")) == "agent" and "." in r.get("host", "")}
        trace = parse_transcript(str(args.transcript))
        # resolve each parsed op to the host(s) it implies, so descriptors and
        # URL targets can both be compared against what the proxy actually saw
        got, implied = set(), set()
        for tc in trace.tool_calls:
            for op in tc.network:
                got.add(op["target"])
                if op["kind"] == "package":
                    tool = op["target"].split()[0] if op["target"] else ""
                    implied.add(PKG_INDEX.get(tool, tool))
                else:
                    implied.add(op["target"].split("/")[0])
        matched = {h for h in truth
                   if any(g and (g in h or h in g) for g in implied)}
        print(f"\nparser vs proxy (agent class): {len(truth)} host(s) reached, "
              f"{len(got)} network op(s) parsed")
        for h in sorted(truth):
            print(f"  {'HIT ' if h in matched else 'MISS'}  {h}")
        if truth:
            print(f"  recall {len(matched)/len(truth):.2f}")
        if not got and truth:
            print("  parser saw no network at all — check for a wrapped command "
                  "(`timeout … pip install`) or an inline-script fetch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
