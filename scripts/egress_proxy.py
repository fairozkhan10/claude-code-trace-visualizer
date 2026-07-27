"""Allowlist HTTP CONNECT proxy — stdlib only, no dependencies.

Why this exists: finding 11 showed that a capable agent can "solve" a SWE-bench
instance by *retrieving the upstream fix* instead of debugging, and that
de-identifying the fixture is not enough to stop it (run 4 re-derived the PR
number from the issue text). Only network isolation closes that hole.

But an agent under test still needs to reach the model API, so full isolation is
impossible — what you want is an *egress allowlist*. A proxy sees the hostname in
the plaintext ``CONNECT`` line before the TLS handshake, so allow/deny by host
needs no TLS interception, no CA certificate, and no third-party proxy.

Every decision is logged as JSONL. The denials are the measurement — they record
what the agent *tried* to fetch, which is a signal the transcript alone can't
give you (a blocked request may never appear as a tool call).

Usage (see scripts/isolated_run.sh for the full harness)::

    python3 scripts/egress_proxy.py --allow api.anthropic.com --log egress.jsonl

Note: the agent sees an HTTP 403, not a black hole, so this measures *retrieval
denied*, not *retrieval absent* — an agent can observe that it is blocked.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

_LOG_LOCK = threading.Lock()


def allowed(host: str, allow: list[str]) -> bool:
    """True if ``host`` is in the allowlist, matching whole labels only.

    ``api.anthropic.com`` allows itself and its subdomains, but not
    ``evil-api.anthropic.com`` or ``api.anthropic.com.attacker.test``.
    """
    host = host.lower().rstrip(".")
    for pat in allow:
        pat = pat.lower().rstrip(".")
        if host == pat or host.endswith("." + pat):
            return True
    return False


def log(path: Path, rec: dict) -> None:
    rec["t"] = round(time.time(), 3)
    line = json.dumps(rec, sort_keys=True)
    with _LOG_LOCK:
        with path.open("a") as fh:
            fh.write(line + "\n")
    tag = "ALLOW" if rec.get("decision") == "allow" else "DENY "
    print(f"{tag} {rec.get('host', '?')}", file=sys.stderr, flush=True)


def _pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _target_of(first_line: str) -> tuple[str, str, int] | None:
    """(method, host, port) from a proxy request line, or None if unparseable."""
    parts = first_line.split()
    if len(parts) < 2:
        return None
    method, target = parts[0], parts[1]
    if method.upper() == "CONNECT":
        host, _, port_s = target.rpartition(":")
        return method, host, int(port_s or 443)
    # plain HTTP proxy request carries an absolute-URI
    rest = target.split("://", 1)[-1]
    hostport = rest.split("/", 1)[0]
    host, _, port_s = hostport.partition(":")
    return method, host, int(port_s or 80)


def handle(client: socket.socket, allow: list[str], logpath: Path) -> None:
    upstream = None
    try:
        client.settimeout(30)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = client.recv(4096)
            if not chunk:
                return
            buf += chunk
            if len(buf) > 65536:
                return
        parsed = _target_of(buf.split(b"\r\n", 1)[0].decode("latin-1"))
        if parsed is None:
            return
        method, host, port = parsed

        if not allowed(host, allow):
            log(logpath, {"decision": "deny", "host": host, "port": port,
                          "method": method})
            client.sendall(b"HTTP/1.1 403 Forbidden\r\n"
                           b"Content-Length: 34\r\n"
                           b"Connection: close\r\n\r\n"
                           b"blocked by egress allowlist proxy\n")
            return

        log(logpath, {"decision": "allow", "host": host, "port": port,
                      "method": method})
        upstream = socket.create_connection((host, port), timeout=30)
        if method.upper() == "CONNECT":
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        else:
            upstream.sendall(buf)
        client.settimeout(None)
        upstream.settimeout(None)
        t = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
        t.start()
        _pipe(client, upstream)
        t.join(timeout=5)
    except Exception as exc:  # noqa: BLE001 — one bad conn must not kill the proxy
        log(logpath, {"decision": "error", "host": "?", "error": repr(exc)})
    finally:
        for s in (client, upstream):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def summarize(logfile: Path) -> dict:
    """Roll a denial log up into {allow: {...}, deny: {...}, n_*} counts."""
    out: dict = {"allow": {}, "deny": {}, "error": 0, "n_events": 0}
    for line in logfile.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        out["n_events"] += 1
        d = rec.get("decision")
        if d == "error":
            out["error"] += 1
        elif d in ("allow", "deny"):
            host = rec.get("host", "?")
            out[d][host] = out[d].get(host, 0) + 1
    out["n_denied"] = sum(out["deny"].values())
    out["n_denied_hosts"] = len(out["deny"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--allow", nargs="*", default=["api.anthropic.com"],
                    help="hostnames the agent may reach (default: the model API only)")
    ap.add_argument("--log", default="egress.jsonl")
    ap.add_argument("--summarize", metavar="LOGFILE",
                    help="print a rollup of an existing log and exit")
    args = ap.parse_args(argv)

    if args.summarize:
        print(json.dumps(summarize(Path(args.summarize)), indent=2, sort_keys=True))
        return 0

    logpath = Path(args.log)
    if logpath.parent != Path(""):
        logpath.parent.mkdir(parents=True, exist_ok=True)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind, args.port))
    srv.listen(128)
    print(f"egress proxy on {args.bind}:{args.port} allow={args.allow} "
          f"log={logpath}", file=sys.stderr, flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn, args.allow, logpath),
                         daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
