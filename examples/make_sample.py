"""Generate a synthetic Claude Code transcript for demos/tests.

Produces examples/sample-session.jsonl — a fabricated but realistic "fix the
failing test" run: explore early (read/grep/ls), execute later (edit, run tests),
with one failed test that gets retried. No real/personal data.
"""
from __future__ import annotations
import json, datetime, pathlib

T0 = datetime.datetime(2026, 6, 18, 9, 0, 0, tzinfo=datetime.timezone.utc)
SID = "5a17e000-deadbeef-demo-0000-000000000001"


def ts(sec: float) -> str:
    return (T0 + datetime.timedelta(seconds=sec)).isoformat().replace("+00:00", "Z")


# (tool, input, dur_seconds, is_error)
STEPS = [
    ("Bash",  {"command": "ls -R src tests", "description": "list project"}, 0.4, False),
    ("Bash",  {"command": "git fetch origin && git log --oneline -5",
               "description": "sync & check recent history"}, 0.9, False),
    ("Read",  {"file_path": "tests/test_parser.py"}, 0.2, False),
    ("Grep",  {"pattern": "def normalize", "path": "src"}, 0.3, False),
    ("Read",  {"file_path": "src/parser.py"}, 0.2, False),
    ("Bash",  {"command": "python -m pytest -q", "description": "run tests"}, 4.1, True),
    ("Bash",  {"command": "pip install pytest-mock", "description": "install missing test dep"}, 2.3, False),
    ("WebFetch", {"url": "https://docs.pytest.org/en/stable/how-to/monkeypatch.html",
                  "prompt": "how to patch a function"}, 1.2, False),
    ("Read",  {"file_path": "src/normalize.py"}, 0.2, False),
    ("Edit",  {"file_path": "src/normalize.py"}, 0.3, False),
    ("Bash",  {"command": "python -m pytest -q", "description": "rerun tests"}, 3.8, True),
    ("Edit",  {"file_path": "src/normalize.py"}, 0.3, False),
    ("Bash",  {"command": "python -m pytest -q", "description": "rerun tests"}, 3.9, False),
    # file I/O done through the shell — caught by the Bash file-tracking parser
    ("Bash",  {"command": "python scripts/gen_fixture.py > tests/fixtures/expected.json",
               "description": "regenerate fixture"}, 0.6, False),
    ("Bash",  {"command": "cat > CHANGELOG.md << 'EOF'\n- fix normalize()\nEOF",
               "description": "write changelog"}, 0.2, False),
]

lines = [
    {"type": "mode", "mode": "normal", "sessionId": SID},
    {"type": "user", "sessionId": SID, "timestamp": ts(0),
     "message": {"role": "user", "content": "Fix the failing test in the parser package."}},
]

clock = 1.0
cache = 6000  # grows over the run (context accumulation)
for i, (name, tin, dur, err) in enumerate(STEPS):
    cache += 1500 + len(json.dumps(tin)) * 3
    uid = f"toolu_demo_{i:03d}"
    lines.append({
        "type": "assistant", "sessionId": SID, "timestamp": ts(clock),
        "cwd": "/Users/demo/project", "gitBranch": "main",
        "message": {
            "model": "claude-sonnet-4-6", "role": "assistant",
            "usage": {
                "input_tokens": 180,
                "output_tokens": 140 + i * 12,
                "cache_read_input_tokens": cache,
                "cache_creation_input_tokens": 320,
            },
            "content": [
                {"type": "text", "text": f"step {i}"},
                {"type": "tool_use", "id": uid, "name": name, "input": tin},
            ],
        },
    })
    clock += dur
    lines.append({
        "type": "user", "sessionId": SID, "timestamp": ts(clock),
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": uid,
             "is_error": err,
             "content": ("FAILED tests/test_parser.py::test_normalize" if err
                         else "ok") + " " + "x" * (200 if err else 40)},
        ]},
    })
    clock += 0.5

out = pathlib.Path(__file__).with_name("sample-session.jsonl")
out.write_text("\n".join(json.dumps(o) for o in lines) + "\n")
print("wrote", out, f"({len(STEPS)} tool calls)")
