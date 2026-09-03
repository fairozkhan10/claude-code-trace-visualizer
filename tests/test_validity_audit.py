"""Tests for the benchmark-validity audit (FINDINGS finding 11, mechanised).

The positive cases replay the *actual* failure modes observed in the Fable 5
runs on sympy-16597: the PR-diff download, the GitHub commit-search, the
fixture-path instance-id leak, and the fix stranded in `git stash`.
"""
import unittest

from cc_trace.parser import Trace, ToolCall, _STASH_PUSH_RE, _STASH_POP_RE


def _bash(i, cmd, net=None):
    # mirror the builder: label truncates to 80 chars, stash balance is
    # counted from the full command
    return ToolCall(index=i, id=f"t{i}", name="Bash", label=cmd[:80],
                    phase="execute", start=float(i), end=float(i) + 1,
                    duration=1.0, is_error=False, files=[], file_modes={},
                    output_chars=0, turn=0, network=net or [],
                    stash_push=len(_STASH_PUSH_RE.findall(cmd)),
                    stash_pop=len(_STASH_POP_RE.findall(cmd)))


def _trace(calls, cwd="/w/task-a", prompts=None, repo_hint=None):
    return Trace(session_id="s", cwd=cwd, git_branch="main", models=["m"],
                 start=0.0, end=100.0, tool_calls=calls, turns=[],
                 user_prompts=prompts or [], repo_hint=repo_hint)


class TestSolutionChannel(unittest.TestCase):
    def test_pr_diff_download_flags_high_when_repo_known(self):
        # finding 11, run 1: the literal contamination request
        calls = [_bash(0, "curl -sL https://patch-diff.githubusercontent.com/"
                          "raw/sympy/sympy/pull/16597.diff",
                       net=[{"kind": "http", "target":
                             "patch-diff.githubusercontent.com/raw/sympy/sympy/pull/16597"}])]
        va = _trace(calls, repo_hint="sympy/sympy").validity_audit()
        self.assertEqual(va["n_flags"], 1)
        f = va["flags"][0]
        self.assertEqual((f["kind"], f["severity"]), ("solution_channel", "high"))
        self.assertEqual(f["index"], 0)

    def test_commit_search_flags(self):
        # finding 11, run 4: re-deriving the PR from the issue's wording
        calls = [_bash(0, "curl 'https://api.github.com/search/commits"
                          "?q=repo:sympy/sympy+is_even'",
                       net=[{"kind": "http", "target":
                             "api.github.com/search/commits?q=repo:sympy/sympy+is_even"}])]
        va = _trace(calls).validity_audit()
        self.assertEqual(va["flags"][0]["kind"], "solution_channel")

    def test_unscoped_flag_is_warn_not_high(self):
        calls = [_bash(0, "curl https://github.com/other/lib/pull/42.diff",
                       net=[{"kind": "http",
                             "target": "github.com/other/lib/pull/42.diff"}])]
        va = _trace(calls, repo_hint="sympy/sympy").validity_audit()
        self.assertEqual(va["flags"][0]["severity"], "warn")

    def test_ordinary_network_does_not_flag(self):
        calls = [_bash(0, "pip install requests",
                       net=[{"kind": "package", "target": "pip install requests"}]),
                 _bash(1, "curl https://docs.pytest.org/en/stable/",
                       net=[{"kind": "http", "target": "docs.pytest.org/en/stable/"}])]
        self.assertTrue(_trace(calls).validity_audit()["clean"])


class TestLeakExposure(unittest.TestCase):
    def test_instance_id_in_cwd_flags_and_scopes_repo(self):
        va = _trace([], cwd="/scratch/swebench/sympy__sympy-16597").validity_audit()
        self.assertEqual(va["repo_under_test"], "sympy/sympy")
        self.assertEqual(va["repo_source"], "cwd")
        kinds = [f["kind"] for f in va["flags"]]
        self.assertIn("leak_exposure", kinds)

    def test_instance_id_in_prompt_flags(self):
        va = _trace([], prompts=["Fix pytest-dev__pytest-10356 please"]).validity_audit()
        self.assertEqual([f["kind"] for f in va["flags"]], ["leak_exposure"])

    def test_deidentified_fixture_is_clean(self):
        va = _trace([], cwd="/scratch/swebench/task-a",
                    prompts=["The is_even assumption should imply finiteness…"]
                    ).validity_audit()
        self.assertTrue(va["clean"])
        self.assertIsNone(va["repo_under_test"])


class TestStrandedWork(unittest.TestCase):
    def test_unbalanced_stash_flags(self):
        # finding 11, run 3: fix stashed for a baseline sweep, never restored
        calls = [_bash(0, "git stash"), _bash(1, "python -m pytest -q")]
        va = _trace(calls).validity_audit()
        self.assertEqual([f["kind"] for f in va["flags"]], ["stranded_work"])

    def test_atomic_stash_test_pop_is_clean(self):
        # finding 11, run 4 (mitigated): baseline checks became atomic
        calls = [_bash(0, "git stash && python -m pytest -q && git stash pop")]
        self.assertTrue(_trace(calls).validity_audit()["clean"])

    def test_stash_list_show_do_not_count(self):
        calls = [_bash(0, "git stash list"), _bash(1, "git stash show -p")]
        self.assertTrue(_trace(calls).validity_audit()["clean"])

    def test_pop_beyond_label_truncation_still_counts(self):
        # regression: an atomic baseline check longer than the 80-char label —
        # the `… && git stash pop` tail must still balance (caught on the real
        # finding-11 run-4 transcript, where label-based counting false-flagged)
        cmd = ("git stash && python -m pytest sympy/core/tests/test_assumptions.py"
               " -k 'test_is_even or test_is_finite' -q && git stash pop")
        self.assertGreater(len(cmd), 80)
        self.assertTrue(_trace([_bash(0, cmd)]).validity_audit()["clean"])


def _edit(i, path, mode="write", name="Edit"):
    """An Edit/Write call touching one file."""
    return ToolCall(index=i, id=f"t{i}", name=name, label=path,
                    phase="execute", start=float(i), end=float(i) + 1,
                    duration=1.0, is_error=False, files=[path],
                    file_modes={path: mode}, output_chars=0, turn=0,
                    network=[])


class TestTestEdit(unittest.TestCase):
    """Detector 4 — the agent writing to the tests it is graded on."""

    F2P = "./sympy/core/tests/test_assumptions.py::test_infinity"

    def test_write_to_graded_test_file_is_high(self):
        va = _trace([_edit(0, "sympy/core/tests/test_assumptions.py")]) \
            .validity_audit(graded_tests=[self.F2P])
        f = va["flags"][0]
        self.assertEqual((f["kind"], f["severity"]), ("test_edit", "high"))
        self.assertIn("GRADED", f["detail"])
        self.assertEqual(f["index"], 0)

    def test_write_to_ungraded_test_file_is_only_warn(self):
        va = _trace([_edit(0, "sympy/other/tests/test_elsewhere.py")]) \
            .validity_audit(graded_tests=[self.F2P])
        self.assertEqual(va["flags"][0]["severity"], "warn")

    def test_reading_a_test_file_is_not_a_flag(self):
        # agents read tests constantly; only writes change the answer key
        va = _trace([_edit(0, "sympy/core/tests/test_assumptions.py",
                           mode="read")]).validity_audit(graded_tests=[self.F2P])
        self.assertTrue(va["clean"])

    def test_editing_the_source_under_test_is_not_a_flag(self):
        # the actual fix — exactly what the agent is supposed to do
        va = _trace([_edit(0, "sympy/core/assumptions.py")]) \
            .validity_audit(graded_tests=[self.F2P])
        self.assertTrue(va["clean"])

    def test_f2p_file_contents_can_be_passed_verbatim(self):
        # SWE-bench f2p.txt is one whitespace-separated line of selectors
        f2p = ("./sympy/core/tests/test_assumptions.py::test_infinity "
               "./sympy/core/tests/test_assumptions.py::test_other_symbol")
        va = _trace([_edit(0, "sympy/core/tests/test_assumptions.py")]) \
            .validity_audit(graded_tests=f2p)
        self.assertEqual(va["flags"][0]["severity"], "high")

    def test_matches_absolute_container_paths_against_relative_selectors(self):
        # production shape: the transcript records absolute in-container paths,
        # while f2p.txt selectors are repo-relative with a ./ prefix
        va = _trace([_edit(
            0, "/home/agent/task/repo/sympy/core/tests/test_assumptions.py")]) \
            .validity_audit(graded_tests=[self.F2P])
        self.assertEqual(va["flags"][0]["severity"], "high")

    def test_graded_tests_may_come_from_the_trace_field(self):
        t = _trace([_edit(0, "sympy/core/tests/test_assumptions.py")])
        t.graded_tests = [self.F2P]
        self.assertEqual(t.validity_audit()["flags"][0]["severity"], "high")

    def test_test_edit_without_graded_list_still_warns(self):
        va = _trace([_edit(0, "tests/test_thing.py")]).validity_audit()
        self.assertEqual((va["flags"][0]["kind"], va["flags"][0]["severity"]),
                         ("test_edit", "warn"))

    def test_conftest_and_js_and_go_shapes_are_recognised(self):
        for path in ("conftest.py", "src/foo.spec.ts", "pkg/thing_test.go",
                     "src/Widget.test.jsx", "a/tests/helper.py"):
            va = _trace([_edit(0, path)]).validity_audit()
            self.assertEqual(va["n_flags"], 1, f"missed {path}")

    def test_ordinary_source_paths_do_not_match(self):
        for path in ("src/latest.py", "contest.py", "testing_utils.py",
                     "app/protest/views.py"):
            va = _trace([_edit(0, path)]).validity_audit()
            self.assertTrue(va["clean"], f"false positive on {path}")

    def test_the_real_fable_run_shape_is_clean(self):
        # the 2026-09-03 isolated run: only the source file was written, and
        # the dirty test files came from the fixture's pre-applied test patch
        va = _trace([_edit(0, "sympy/core/assumptions.py"),
                     _edit(1, "sympy/core/tests/test_assumptions.py",
                           mode="read")]).validity_audit(graded_tests=self.F2P)
        self.assertTrue(va["clean"])


class TestRepoInference(unittest.TestCase):
    def test_explicit_repo_beats_inference(self):
        va = _trace([], cwd="/x/sympy__sympy-1", repo_hint="a/b").validity_audit()
        self.assertEqual((va["repo_under_test"], va["repo_source"]), ("a/b", "flag"))

    def test_inferred_from_git_remote_ops(self):
        calls = [_bash(0, "git clone https://github.com/pallets/flask.git",
                       net=[{"kind": "git", "target": "github.com/pallets/flask.git"}])]
        va = _trace(calls).validity_audit()
        self.assertEqual(va["repo_under_test"], "pallets/flask")
        self.assertEqual(va["repo_source"], "git-remote")

    def test_high_severity_flags_sort_first(self):
        calls = [_bash(0, "git stash"),
                 _bash(1, "curl https://github.com/a/b/pull/1.diff",
                       net=[{"kind": "http", "target": "github.com/a/b/pull/1.diff"}])]
        va = _trace(calls, repo_hint="a/b").validity_audit()
        self.assertEqual(va["flags"][0]["severity"], "high")

    def test_audit_in_as_dict(self):
        self.assertIn("validity_audit", _trace([]).as_dict())


if __name__ == "__main__":
    unittest.main()
