"""Live profiling from ``claude -p --output-format stream-json``.

The streaming format emits one JSON event per line (NDJSON) as the agent runs —
the same ``message`` shapes the saved transcript uses, but without the
transcript's per-line ``timestamp``. So here we stamp each event by its
wall-clock *arrival* time, which makes durations reflect real elapsed time.

Two ways in:

* pipe an existing run:  ``claude -p '…' --output-format stream-json --verbose | \
  python -m cc_trace live -``
* let the tool spawn it:  ``python -m cc_trace live '…'`` (extra args after ``--``
  are passed through to ``claude``).
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Callable, Iterable, Iterator, Optional, Union

from .parser import _Builder, Trace


def _to_objects(source: Iterable[Union[str, dict]]) -> Iterator[dict]:
    """Yield event dicts from raw NDJSON lines or already-parsed dicts."""
    for item in source:
        if isinstance(item, dict):
            yield item
            continue
        line = item.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def parse_stream(source: Iterable[Union[str, dict]],
                 clock: Callable[[], float] = time.time,
                 on_event: Optional[Callable[[dict, _Builder], None]] = None) -> Trace:
    """Build a :class:`Trace` from a live stream of stream-json events.

    ``clock`` supplies the arrival timestamp for each event (injectable for
    tests). ``on_event(obj, builder)`` fires after each event is folded in, for
    live progress display.
    """
    b = _Builder()
    for obj in _to_objects(source):
        b.feed(obj, clock())
        if on_event is not None:
            on_event(obj, b)
    return b.build()


def claude_stream(prompt: str, extra_args: Iterable[str] = ()) -> Iterator[str]:
    """Spawn ``claude -p`` in stream-json mode and yield its stdout lines live."""
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           *extra_args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, bufsize=1)
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            yield line
    finally:
        proc.stdout.close()
        proc.wait()
