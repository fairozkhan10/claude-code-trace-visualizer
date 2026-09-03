"""Tests for the cross-run statistics layer.

The assertions here are properties that are true by construction of the exact
permutation test (complete separation gives 2/C(n1+n2,n1); identical groups give
p=1; the test is symmetric), so they pin the semantics without depending on any
external stats package to compare against.
"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cc_trace import stats as st
from cc_trace.cli import main


class TestDescribe(unittest.TestCase):
    def test_basic(self):
        d = st.describe([1, 2, 3, 4])
        self.assertEqual(d["n"], 4)
        self.assertEqual(d["median"], 2.5)
        self.assertEqual(d["min"], 1.0)
        self.assertEqual(d["max"], 4.0)

    def test_single_value_has_no_sd(self):
        d = st.describe([7])
        self.assertEqual(d["n"], 1)
        self.assertEqual(d["median"], 7.0)
        self.assertIsNone(d["sd"])          # undefined, not 0.0

    def test_empty(self):
        self.assertEqual(st.describe([])["n"], 0)
        self.assertIsNone(st.describe([])["median"])


class TestCliffsDelta(unittest.TestCase):
    def test_complete_separation(self):
        self.assertEqual(st.cliffs_delta([4, 5, 6], [1, 2, 3]), 1.0)
        self.assertEqual(st.cliffs_delta([1, 2, 3], [4, 5, 6]), -1.0)

    def test_identical_groups(self):
        self.assertEqual(st.cliffs_delta([1, 2, 3], [1, 2, 3]), 0.0)

    def test_magnitudes(self):
        self.assertEqual(st.delta_magnitude(0.0), "negligible")
        self.assertEqual(st.delta_magnitude(0.2), "small")
        self.assertEqual(st.delta_magnitude(0.4), "medium")
        self.assertEqual(st.delta_magnitude(-1.0), "large")
        self.assertEqual(st.delta_magnitude(None), "—")


class TestDesignFloor(unittest.TestCase):
    def test_three_vs_three_cannot_reach_significance(self):
        # C(6,3) = 20 splits -> the most extreme result is 2/20
        self.assertAlmostEqual(st.min_two_sided_p(3, 3), 0.1)
        self.assertGreater(st.min_two_sided_p(3, 3), 0.05)

    def test_floor_falls_as_n_grows(self):
        self.assertLess(st.min_two_sided_p(5, 5), st.min_two_sided_p(3, 3))
        self.assertLessEqual(st.min_two_sided_p(4, 4), 0.05)   # C(8,4)=70


class TestMannWhitney(unittest.TestCase):
    def test_exact_complete_separation_hits_the_floor(self):
        r = st.mannwhitney([4, 5, 6], [1, 2, 3])
        self.assertEqual(r["method"], "exact")
        self.assertAlmostEqual(r["p"], 0.1)
        self.assertAlmostEqual(r["p"], r["p_floor"])
        self.assertEqual(r["u"], 9.0)          # 3*3, group a dominates

    def test_identical_groups_give_p_one(self):
        r = st.mannwhitney([1, 2, 3], [1, 2, 3])
        self.assertAlmostEqual(r["p"], 1.0)

    def test_symmetric_in_its_arguments(self):
        a, b = [1, 4, 9, 16], [2, 3, 10, 20]
        self.assertAlmostEqual(st.mannwhitney(a, b)["p"],
                               st.mannwhitney(b, a)["p"])

    def test_u_statistics_sum_to_n1_n2(self):
        a, b = [1, 4, 9, 16], [2, 3, 10, 20]
        u1 = st.mannwhitney(a, b)["u"]
        u2 = st.mannwhitney(b, a)["u"]
        self.assertAlmostEqual(u1 + u2, len(a) * len(b))

    def test_ties_are_handled_by_midranks(self):
        # every value tied -> no evidence of any difference
        r = st.mannwhitney([1, 1, 1], [1, 1, 1])
        self.assertAlmostEqual(r["p"], 1.0)
        self.assertAlmostEqual(r["u"], 4.5)     # exactly the null centre

    def test_falls_back_to_monte_carlo_when_too_many_splits(self):
        a = list(range(10))
        b = [x + 100 for x in range(10)]        # C(20,10) = 184756 > 50k
        r = st.mannwhitney(a, b)
        self.assertEqual(r["method"], "monte-carlo")
        self.assertGreater(r["p"], 0.0)         # +1 smoothing, never exactly 0
        self.assertLess(r["p"], 0.01)           # but still clearly separated

    def test_monte_carlo_is_seeded_and_repeatable(self):
        a, b = list(range(10)), [x + 5 for x in range(10)]
        self.assertEqual(st.mannwhitney(a, b, seed=7)["p"],
                         st.mannwhitney(a, b, seed=7)["p"])

    def test_empty_group(self):
        r = st.mannwhitney([], [1, 2])
        self.assertIsNone(r["p"])
        self.assertEqual(r["method"], "insufficient-data")


class TestBootstrap(unittest.TestCase):
    def test_deterministic_for_a_fixed_seed(self):
        a, b = [1, 2, 3], [4, 5, 6]
        c1 = st.bootstrap_diff_ci(a, b, seed=3, n_resamples=500)
        c2 = st.bootstrap_diff_ci(a, b, seed=3, n_resamples=500)
        self.assertEqual((c1["lo"], c1["hi"]), (c2["lo"], c2["hi"]))

    def test_point_estimate_is_the_median_difference(self):
        c = st.bootstrap_diff_ci([1, 2, 3], [4, 5, 6], n_resamples=200)
        self.assertEqual(c["point"], -3.0)

    def test_interval_brackets_the_point_estimate(self):
        c = st.bootstrap_diff_ci([1, 2, 3, 4, 5], [3, 4, 5, 6, 7],
                                 n_resamples=2000)
        self.assertLessEqual(c["lo"], c["point"])
        self.assertGreaterEqual(c["hi"], c["point"])

    def test_empty_group(self):
        c = st.bootstrap_diff_ci([], [1], n_resamples=10)
        self.assertIsNone(c["point"])


class TestHolm(unittest.TestCase):
    def test_step_down_with_monotonicity(self):
        # m=3: 0.01*3=0.03 ; 0.03*2=0.06 ; 0.04*1=0.04 -> clamped up to 0.06
        self.assertEqual(st.holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])

    def test_none_passes_through_and_is_excluded_from_m(self):
        out = st.holm([0.01, None])
        self.assertIsNone(out[1])
        self.assertAlmostEqual(out[0], 0.01)     # m=1, so unadjusted

    def test_clamped_at_one(self):
        self.assertEqual(st.holm([0.9, 0.95]), [1.0, 1.0])

    def test_all_none(self):
        self.assertEqual(st.holm([None, None]), [None, None])


def _row(group, **metrics):
    base = {"label": group, "group": group, "session": "abcd1234"}
    base.update(metrics)
    return base


class TestCompareGroups(unittest.TestCase):
    def _rows(self):
        return ([_row("opus", purity=p) for p in (0.80, 0.82, 0.79)]
                + [_row("fable", purity=p) for p in (0.92, 0.94, 0.93)])

    def test_flags_a_three_vs_three_design_as_underpowered(self):
        res = st.compare_groups(self._rows(), metrics=("purity",))
        t = res["pairs"][0]["tests"][0]
        self.assertTrue(t["underpowered"])
        self.assertAlmostEqual(t["p"], 0.1)      # perfectly separated, still 0.1
        self.assertEqual(t["magnitude"], "large")
        self.assertAlmostEqual(t["delta"], -1.0)

    def test_underpowered_warning_reaches_the_text_output(self):
        txt = st.render_text(st.compare_groups(self._rows(), metrics=("purity",)))
        self.assertIn("UNDERPOWERED BY DESIGN", txt)
        self.assertIn("0.100", txt)

    def test_groups_default_to_labels(self):
        rows = [{"label": "a", "purity": 0.5}, {"label": "b", "purity": 0.9}]
        res = st.compare_groups(rows, metrics=("purity",))
        self.assertEqual(res["groups"], ["a", "b"])

    def test_missing_metric_values_are_dropped_not_zeroed(self):
        rows = [_row("a", purity=0.5), _row("a", purity=None),
                _row("b", purity=0.9)]
        res = st.compare_groups(rows, metrics=("purity",))
        self.assertEqual(res["descriptives"]["purity"]["a"]["n"], 1)

    def test_more_than_two_groups_gives_every_pair(self):
        rows = [_row("a", purity=0.1), _row("b", purity=0.5), _row("c", purity=0.9)]
        res = st.compare_groups(rows, metrics=("purity",))
        self.assertEqual(len(res["pairs"]), 3)

    def test_holm_applied_across_metrics(self):
        rows = ([_row("a", purity=p, separation=s)
                 for p, s in ((0.1, 0.1), (0.2, 0.2), (0.3, 0.3))]
                + [_row("b", purity=p, separation=s)
                   for p, s in ((0.7, 0.7), (0.8, 0.8), (0.9, 0.9))])
        res = st.compare_groups(rows, metrics=("purity", "separation"))
        for t in res["pairs"][0]["tests"]:
            self.assertGreaterEqual(t["p_holm"], t["p"])

    def test_result_is_json_serialisable(self):
        res = st.compare_groups(self._rows(), metrics=("purity",))
        json.loads(json.dumps(res))      # must not raise

    def test_single_run_per_group_is_marked_degenerate(self):
        rows = [_row("a", purity=0.5), _row("b", purity=0.9)]
        t = st.compare_groups(rows, metrics=("purity",))["pairs"][0]["tests"][0]
        self.assertAlmostEqual(t["p"], 1.0)      # 1v1: floor is 1.0
        self.assertTrue(t["underpowered"])
        # delta is ±1 by construction at n=1 and must not read as a real effect
        self.assertTrue(t["degenerate"])
        self.assertEqual(t["magnitude"], "n/a (n=1)")

    def test_degenerate_comparison_hides_delta_and_ci_in_text(self):
        rows = [_row("a", purity=0.5), _row("b", purity=0.9)]
        txt = st.render_text(st.compare_groups(rows, metrics=("purity",)))
        self.assertIn("single run — no interval", txt)
        # the size column must read n/a, not a magnitude — check the data row
        # itself rather than the whole page (the footer prose says "large" too)
        # last "purity" line = the pair-test row (the first is GROUP MEDIANS)
        row = [l for l in txt.splitlines() if l.startswith("purity")][-1]
        self.assertIn("n/a (n=1)", row)
        self.assertNotIn("large", row)

    def test_three_per_group_is_not_degenerate(self):
        res = st.compare_groups(self._rows(), metrics=("purity",))
        self.assertFalse(res["pairs"][0]["tests"][0]["degenerate"])


class TestStatsCLI(unittest.TestCase):
    """End-to-end through the CLI, on the committed example report."""

    SAMPLE = str(Path(__file__).resolve().parent.parent
                 / "examples" / "example-report.json")

    def _run(self, argv):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_rejects_an_unknown_metric(self):
        # two real inputs, so the run reaches the metric check rather than
        # bailing out earlier on "no usable runs"
        rc, _ = self._run(["stats", self.SAMPLE, self.SAMPLE,
                           "--metric", "not_a_metric"])
        self.assertEqual(rc, 1)

    def test_reports_groups_and_the_design_floor(self):
        rc, out = self._run(["stats", self.SAMPLE, self.SAMPLE,
                             "--group", "opus", "--group", "fable",
                             "--metric", "purity", "--resamples", "200"])
        self.assertEqual(rc, 0)
        self.assertIn("opus", out)
        self.assertIn("fable", out)
        self.assertIn("UNDERPOWERED BY DESIGN", out)   # 1 vs 1

    def test_needs_at_least_two_runs(self):
        rc, _ = self._run(["stats", self.SAMPLE])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
