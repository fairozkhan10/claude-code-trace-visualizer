"""Tests for the three output surfaces: report HTML, flame graph, compare.

The flame invariants (calls-view total == n_tool_calls, tokens-view total ==
output tokens, no leakage) were hand-verified when the feature shipped; here
they become permanent.
"""
import json
import re
import unittest

from cc_trace.parser import Trace, ToolCall, Turn
from cc_trace.report import render_html as report_html
from cc_trace import flame
from cc_trace.compare import summarize, render_text, render_html as compare_html


def _tc(i, phase, name="Bash", label="ls", files=None, turn=0, dur=1.0, net=None):
    return ToolCall(index=i, id=f"t{i}", name=name, label=label, phase=phase,
                    start=float(i), end=float(i) + dur, duration=dur,
                    is_error=False, files=files or [], file_modes={},
                    output_chars=10, turn=turn, network=net or [])


def _trace():
    calls = [
        _tc(0, "explore", name="Read", label="a.py", files=["a.py"], turn=0),
        _tc(1, "explore", label="git status", turn=0),
        _tc(2, "execute", name="Edit", label="a.py", files=["a.py"], turn=1),
        _tc(3, "execute", label="pytest -q", turn=1,
            net=[{"kind": "http", "target": "x.com"}]),
    ]
    turns = [Turn(0, "claude-opus-4-8", 0.0, 10, 100, 1000, 5, 0.1, 2),
             Turn(1, "claude-opus-4-8", 2.0, 10, 200, 2000, 5, 0.2, 2),
             Turn(2, "claude-opus-4-8", 4.0, 10, 40, 3000, 5, 0.1, 0)]
    return Trace(session_id="sess-xyz-1", cwd="/w/proj", git_branch="main",
                 models=["claude-opus-4-8"], start=0.0, end=10.0,
                 tool_calls=calls, turns=turns)


class TestReportHtml(unittest.TestCase):
    def test_template_fully_substituted_and_data_embedded(self):
        html = report_html(_trace())
        self.assertNotIn("__DATA__", html)
        self.assertNotIn("__SESSION__", html)
        m = re.search(r'<script id="data" type="application/json">(.*?)</script>',
                      html, re.S)
        self.assertIsNotNone(m)
        data = json.loads(m.group(1))
        self.assertEqual(data["n_tool_calls"], 4)
        self.assertEqual(data["session_id"], "sess-xyz-1")


class TestFlame(unittest.TestCase):
    def test_calls_view_total_equals_tool_calls(self):
        t = _trace()
        stacks = flame.folded([t], "calls")
        self.assertEqual(sum(stacks.values()), len(t.tool_calls))

    def test_tokens_view_total_equals_output_tokens(self):
        t = _trace()
        stacks = flame.folded([t], "tokens")
        # includes the no-tool-call turn's tokens via the other;message bucket
        self.assertAlmostEqual(sum(stacks.values()),
                               t.token_totals()["output"])
        self.assertIn("other;message", stacks)

    def test_net_view_counts_requests(self):
        stacks = flame.folded([_trace()], "net")
        self.assertEqual(sum(stacks.values()), 1)

    def test_multi_trace_gets_run_root(self):
        stacks = flame.folded([_trace(), _trace()], "calls")
        self.assertTrue(all(s.startswith("sess-xyz") for s in stacks))

    def test_unknown_view_raises(self):
        with self.assertRaises(ValueError):
            flame.folded([_trace()], "nope")

    def test_folded_text_and_html_render(self):
        t = _trace()
        text = flame.render_folded(flame.folded([t], "calls"))
        self.assertIn("explore;Read;a.py 1", text)
        html = flame.render_html([t], "calls")
        self.assertNotIn("__DATA__", html)


class TestCompare(unittest.TestCase):
    def test_summarize_metrics(self):
        row = summarize(_trace().as_dict())
        self.assertEqual(row["sequence"], "EEXX")
        self.assertEqual(row["explore_share"], 0.5)
        # perfect front-load: purity 1.0, positive separation
        self.assertEqual(row["purity"], 1.0)
        self.assertGreater(row["separation"], 0)
        # cache 6000 / (6000 + 30) fresh
        self.assertAlmostEqual(row["cache_read_share"], 0.995)
        self.assertEqual(row["net_total"], 1)
        self.assertEqual(row["top_tool"], "Bash")

    def test_render_text_and_html(self):
        rows = [summarize(_trace().as_dict())]
        text = render_text(rows)
        self.assertIn("EE|XX", text)          # crossover marked in sequence
        html = compare_html(rows)
        self.assertNotIn("__DATA__", html)


if __name__ == "__main__":
    unittest.main()


class TestCompareLabels(unittest.TestCase):
    """Row labels have to distinguish runs, or a cross-model table is unreadable."""

    def test_generic_fixture_dir_walks_up(self):
        # SWE-bench fixtures all check out into `<task>/repo`, so the basename
        # alone labelled every row "repo"
        from cc_trace.compare import _label
        self.assertEqual(_label({"cwd": "/x/swebench/task-a/repo"}), "task-a")
        self.assertEqual(_label({"cwd": "/x/swebench/task-b/src"}), "task-b")

    def test_informative_basename_is_kept(self):
        from cc_trace.compare import _label
        self.assertEqual(_label({"cwd": "/home/me/tinycss2"}), "tinycss2")

    def test_task_tag_still_wins(self):
        from cc_trace.compare import _label
        self.assertEqual(
            _label({"cwd": "/x/repo", "user_prompts": ["Task category: refactor"]}),
            "refactor")

    def test_session_id_is_the_last_resort(self):
        from cc_trace.compare import _label
        self.assertEqual(_label({"cwd": "", "session_id": "abcd1234-x"}), "abcd1234")
