"""Tests for the event builder and live-stream parsing.

The single most important invariant here is the per-``message.id`` usage dedup:
one assistant message spans several transcript lines that all repeat the same
message-level ``usage``, and counting it more than once inflated tokens/cost
~2-3x (the bug fixed in b56da34, wire-verified via MITM). These tests make that
regression impossible to reintroduce silently.
"""
import json
import tempfile
import unittest
from pathlib import Path

from cc_trace.parser import _Builder, parse_transcript, _parse_ts
from cc_trace.stream import parse_stream


USAGE = {"input_tokens": 100, "output_tokens": 50,
         "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 20}


def _assistant_line(msg_id, blocks, usage=USAGE, model="claude-opus-4-8"):
    return {"type": "assistant", "sessionId": "sess-1",
            "message": {"id": msg_id, "model": model, "usage": usage,
                        "content": blocks}}


def _tool_use(uid, name="Bash", tin=None):
    return {"type": "tool_use", "id": uid, "name": name,
            "input": tin or {"command": "ls"}}


def _tool_result(uid, is_error=False, content="ok"):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": uid,
         "is_error": is_error, "content": content}]}}


class TestUsageDedup(unittest.TestCase):
    def test_multi_line_message_counts_usage_once(self):
        b = _Builder()
        # one logical message split across 3 transcript lines (1 block each),
        # all repeating the same message-level usage
        b.feed(_assistant_line("msg_1", [{"type": "text", "text": "hi"}]), 1.0)
        b.feed(_assistant_line("msg_1", [_tool_use("u1")]), 1.0)
        b.feed(_assistant_line("msg_1", [_tool_use("u2")]), 1.0)
        t = b.build()
        self.assertEqual(len(t.turns), 1)                     # ONE turn
        self.assertEqual(t.token_totals()["output"], 50)      # counted once
        self.assertEqual(t.turns[0].n_tool_calls, 2)          # both calls kept
        self.assertEqual(len(t.tool_calls), 2)

    def test_distinct_messages_count_separately(self):
        b = _Builder()
        b.feed(_assistant_line("msg_1", [_tool_use("u1")]), 1.0)
        b.feed(_assistant_line("msg_2", [_tool_use("u2")]), 2.0)
        t = b.build()
        self.assertEqual(len(t.turns), 2)
        self.assertEqual(t.token_totals()["output"], 100)

    def test_missing_message_id_counts_every_line(self):
        # older transcripts / the synthetic sample have no message.id
        b = _Builder()
        line = _assistant_line(None, [_tool_use("u1")])
        del line["message"]["id"]
        b.feed(line, 1.0)
        line2 = _assistant_line(None, [_tool_use("u2")])
        del line2["message"]["id"]
        b.feed(line2, 2.0)
        self.assertEqual(len(b.build().turns), 2)


class TestCallPairing(unittest.TestCase):
    def test_result_sets_duration_error_and_output(self):
        b = _Builder()
        b.feed(_assistant_line("m1", [_tool_use("u1")]), 10.0)
        b.feed(_tool_result("u1", is_error=True, content="boom"), 12.5)
        tc = b.build().tool_calls[0]
        self.assertEqual(tc.duration, 2.5)
        self.assertTrue(tc.is_error)
        self.assertEqual(tc.output_chars, 4)

    def test_orphan_result_is_ignored(self):
        b = _Builder()
        b.feed(_tool_result("nope"), 1.0)
        self.assertEqual(len(b.build().tool_calls), 0)

    def test_unanswered_call_keeps_none_duration(self):
        b = _Builder()
        b.feed(_assistant_line("m1", [_tool_use("u1")]), 10.0)
        tc = b.build().tool_calls[0]
        self.assertIsNone(tc.end)
        self.assertIsNone(tc.duration)

    def test_user_prompt_captured(self):
        b = _Builder()
        b.feed({"type": "user", "sessionId": "s",
                "message": {"content": "Fix the failing test."}}, 0.0)
        self.assertEqual(b.build().user_prompts, ["Fix the failing test."])


class TestParseTranscript(unittest.TestCase):
    def test_roundtrip_through_a_file(self):
        lines = [
            {"type": "user", "sessionId": "abc", "timestamp": "2026-01-01T00:00:00Z",
             "message": {"content": "go"}},
            {**_assistant_line("m1", [_tool_use("u1")]),
             "timestamp": "2026-01-01T00:00:01Z", "cwd": "/w", "gitBranch": "main"},
            {**_tool_result("u1"), "timestamp": "2026-01-01T00:00:03Z"},
            "not json at all",
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            for ln in lines:
                fh.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")
            path = fh.name
        try:
            t = parse_transcript(path)
        finally:
            Path(path).unlink()
        self.assertEqual(t.session_id, "abc")
        self.assertEqual(t.cwd, "/w")
        self.assertEqual(len(t.tool_calls), 1)
        self.assertEqual(t.tool_calls[0].duration, 2.0)
        self.assertEqual(t.duration, 3.0)

    def test_parse_ts(self):
        self.assertIsNone(_parse_ts(None))
        self.assertIsNone(_parse_ts("garbage"))
        self.assertEqual(_parse_ts("1970-01-01T00:00:10Z"), 10.0)


class TestStream(unittest.TestCase):
    def test_arrival_clock_stamps_durations(self):
        events = [
            json.dumps({"type": "assistant", "session_id": "live-1",
                        "message": {"id": "m1", "model": "x", "usage": USAGE,
                                    "content": [_tool_use("u1")]}}),
            "",                       # blank + junk lines must be skipped
            "junk{",
            json.dumps(_tool_result("u1")),
        ]
        clock_vals = iter([100.0, 103.0, 103.0, 103.0])
        t = parse_stream(events, clock=lambda: next(clock_vals))
        self.assertEqual(t.session_id, "live-1")
        self.assertEqual(t.tool_calls[0].duration, 3.0)

    def test_accepts_pre_parsed_dicts(self):
        t = parse_stream([_assistant_line("m1", [_tool_use("u1")])],
                         clock=lambda: 1.0)
        self.assertEqual(len(t.tool_calls), 1)


if __name__ == "__main__":
    unittest.main()
