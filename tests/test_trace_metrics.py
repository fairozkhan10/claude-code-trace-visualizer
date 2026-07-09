"""Tests for the Trace-level derived metrics — the numbers the findings cite.

purity/crossover, retry loops, repeated-work clustering, file access/graph and
network rollups are all computed here from hand-built ToolCall sequences with
known answers, so a change in metric semantics can't slip through silently.
"""
import unittest

from cc_trace.parser import Trace, ToolCall, Turn


def _tc(i, phase, name="Bash", label="", files=None, modes=None, err=False,
        start=None, dur=None, net=None, turn=0):
    files = files or []
    return ToolCall(
        index=i, id=f"t{i}", name=name, label=label, phase=phase,
        start=start, end=(start + dur) if (start is not None and dur is not None) else None,
        duration=dur, is_error=err, files=files,
        file_modes=modes or {}, output_chars=0, turn=turn,
        network=net or [],
    )


def _trace(calls, turns=None):
    return Trace(session_id="s", cwd="/w", git_branch="main", models=["m"],
                 start=0.0, end=100.0, tool_calls=calls, turns=turns or [])


def _phase_trace(seq):
    """Build a trace whose phased calls spell out ``seq`` ('E'/'X')."""
    return _trace([_tc(i, "explore" if ch == "E" else "execute")
                   for i, ch in enumerate(seq)])


class TestPhaseCrossover(unittest.TestCase):
    def test_perfect_shift(self):
        xo = _phase_trace("EEXX").phase_crossover()
        self.assertEqual(xo, {"index": 2, "pos": 0.5, "purity": 1.0, "n": 4})

    def test_fully_interleaved(self):
        xo = _phase_trace("EXEXEXEX").phase_crossover()
        self.assertAlmostEqual(xo["purity"], 0.625)

    def test_run_f_shape(self):
        # the trivial-debug run F from FINDINGS: XEXX -> purity 0.75
        xo = _phase_trace("XEXX").phase_crossover()
        self.assertEqual(xo["purity"], 0.75)

    def test_all_execute_has_full_purity(self):
        xo = _phase_trace("XXXXX").phase_crossover()
        self.assertEqual((xo["index"], xo["purity"]), (0, 1.0))

    def test_empty(self):
        xo = _trace([]).phase_crossover()
        self.assertEqual(xo, {"index": None, "pos": None, "purity": None, "n": 0})


class TestRetryLoops(unittest.TestCase):
    def test_flags_repeated_target_with_error(self):
        calls = [
            _tc(0, "execute", label="pytest -q", err=True, start=0.0),
            _tc(1, "execute", label="pytest -q", err=False, start=10.0),
            _tc(2, "explore", name="Read", files=["a.py"], start=20.0),
        ]
        loops = _trace(calls).retry_loops()
        self.assertEqual(len(loops), 1)
        self.assertEqual(loops[0]["attempts"], 2)
        self.assertEqual(loops[0]["errors"], 1)
        self.assertEqual(loops[0]["span_s"], 10.0)

    def test_repeats_without_error_are_not_retries(self):
        calls = [_tc(0, "execute", label="pytest -q"),
                 _tc(1, "execute", label="pytest -q")]
        self.assertEqual(_trace(calls).retry_loops(), [])


class TestRepeatedWork(unittest.TestCase):
    def test_near_duplicate_bash_merges(self):
        # three pytest variants must land in ONE cluster (signature + difflib)
        calls = [
            _tc(0, "execute", label="pytest tests/a.py", start=0.0, dur=2.0),
            _tc(1, "execute", label="pytest tests/b.py -q", start=5.0, dur=2.0),
            _tc(2, "execute", label="pytest tests/c.py", start=9.0, dur=2.0),
            _tc(3, "explore", label="git status"),
        ]
        rw = _trace(calls).repeated_work()
        self.assertEqual(rw["n_clusters"], 1)
        self.assertEqual(rw["clusters"][0]["count"], 3)
        self.assertEqual(rw["redundant_calls"], 2)
        self.assertEqual(rw["redundant_frac"], 0.5)     # 2 of 4 calls
        self.assertFalse(rw["clusters"][0]["exact"])

    def test_same_file_reread_clusters(self):
        calls = [_tc(0, "explore", name="Read", files=["a.py"]),
                 _tc(1, "explore", name="Read", files=["a.py"]),
                 _tc(2, "explore", name="Read", files=["b.py"])]
        rw = _trace(calls).repeated_work()
        self.assertEqual(rw["n_clusters"], 1)
        self.assertEqual(rw["clusters"][0]["tool"], "Read")
        self.assertTrue(rw["clusters"][0]["exact"])

    def test_no_repeats(self):
        calls = [_tc(0, "execute", label="pytest -q"),
                 _tc(1, "explore", label="git status")]
        rw = _trace(calls).repeated_work()
        self.assertEqual(rw["redundant_calls"], 0)
        self.assertEqual(rw["redundant_frac"], 0.0)


class TestFileAndNetworkRollups(unittest.TestCase):
    def test_file_access_prefers_per_file_mode(self):
        calls = [
            _tc(0, "execute", files=["gen.py", "out.json"],
                modes={"gen.py": "read", "out.json": "write"}),
            _tc(1, "explore", name="Read", files=["gen.py"],
                modes={"gen.py": "read"}),
        ]
        fa = {r["file"]: r for r in _trace(calls).file_access()}
        self.assertEqual((fa["gen.py"]["reads"], fa["gen.py"]["writes"]), (2, 0))
        self.assertEqual((fa["out.json"]["reads"], fa["out.json"]["writes"]), (0, 1))

    def test_network_activity_rollup(self):
        calls = [
            _tc(0, "execute", net=[{"kind": "http", "target": "a.com"}]),
            _tc(1, "execute", net=[{"kind": "http", "target": "b.com"},
                                   {"kind": "git", "target": "origin"}]),
        ]
        na = _trace(calls).network_activity()
        self.assertEqual(na["total"], 3)
        self.assertEqual(na["by_kind"][0], {"kind": "http", "count": 2})

    def test_file_graph_links_co_accessed_files(self):
        calls = [_tc(0, "explore", name="Read", files=["a.py"]),
                 _tc(1, "execute", name="Edit", files=["b.py"],
                     modes={"b.py": "write"})]
        g = _trace(calls).file_graph()
        self.assertEqual(len(g["nodes"]), 2)
        self.assertEqual(g["edges"][0]["weight"], 1)


class TestTokenTotalsAndCost(unittest.TestCase):
    def test_totals_sum_turns(self):
        turns = [Turn(0, "m", 0.0, 10, 20, 1000, 5, 0.5, 1),
                 Turn(1, "m", 1.0, 15, 25, 2000, 5, 0.25, 0)]
        t = _trace([], turns=turns)
        self.assertEqual(t.token_totals(),
                         {"input": 25, "output": 45, "cache_read": 3000,
                          "cache_write": 10})
        self.assertAlmostEqual(t.total_cost, 0.75)

    def test_as_dict_has_all_findings_keys(self):
        d = _phase_trace("EEXX").as_dict()
        for key in ("phase_crossover", "phase_counts", "token_totals",
                    "network_activity", "repeated_work", "retry_loops",
                    "file_access", "file_graph", "tool_breakdown",
                    "validity_audit"):
            self.assertIn(key, d)


if __name__ == "__main__":
    unittest.main()
