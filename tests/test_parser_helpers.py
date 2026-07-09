"""Unit tests for the parser's heuristic helpers.

These pin the behaviour of the best-effort command-string parsing that the
findings rest on (file I/O, network, phase classification, signatures). Each
table row is a real-world-shaped command; if a heuristic changes, a row here
should fail loudly.
"""
import unittest

from cc_trace.parser import (
    _is_pathish, _bash_files, _classify_bash, _bash_signature,
    _bash_network, _host_of, _tool_network, _label, _file_ops,
)
from cc_trace.cost import turn_cost


class TestIsPathish(unittest.TestCase):
    def test_accepts_paths_and_extensions(self):
        for tok in ("src/parser.py", "tests/test_x.py", "a/b/c", "file.txt",
                    "'notes.md'", "pkg/mod.py::test_case"):
            self.assertTrue(_is_pathish(tok), tok)

    def test_rejects_flags_globs_devices_dirs(self):
        for tok in ("-q", "--verbose", "/dev/null", "boltons/", "*.py",
                    "$(pwd)/x", "a;b", "", "README"):
            self.assertFalse(_is_pathish(tok), tok)


class TestBashFiles(unittest.TestCase):
    def _files(self, cmd):
        return dict(_bash_files(cmd))

    def test_redirect_write(self):
        self.assertEqual(self._files("python scripts/gen.py > out/expected.json"),
                         {"scripts/gen.py": "read", "out/expected.json": "write"})

    def test_heredoc_write(self):
        ops = self._files("cat > CHANGELOG.md << 'EOF'\n- fix\nEOF")
        self.assertEqual(ops.get("CHANGELOG.md"), "write")

    def test_append_and_stderr_redirects(self):
        self.assertEqual(self._files("echo hi >> log.txt"), {"log.txt": "write"})
        self.assertEqual(self._files("pytest -q 2> err.log"), {"err.log": "write"})

    def test_tee(self):
        self.assertEqual(self._files("make | tee build.log"), {"build.log": "write"})
        self.assertEqual(self._files("make | tee -a build.log"), {"build.log": "write"})

    def test_curl_output_flag_is_write(self):
        self.assertEqual(self._files("curl -o fix.diff https://github.com/x.diff"),
                         {"fix.diff": "write"})
        self.assertEqual(self._files("wget -O page.html https://example.com"),
                         {"page.html": "write"})

    def test_curl_remote_name_url_not_a_file(self):
        # `curl -O <url>` — the capture is a URL, must not count as a local write
        self.assertEqual(self._files("curl -O https://example.com/a.tar.gz"), {})

    def test_read_or_run_first_path_arg(self):
        self.assertEqual(self._files("python scripts/run.py --fast"),
                         {"scripts/run.py": "read"})
        self.assertEqual(self._files("cat notes.md"), {"notes.md": "read"})

    def test_inline_code_flags_stop_the_scan(self):
        self.assertEqual(self._files("python -c 'print(1)'"), {})
        self.assertEqual(self._files("python -m pytest -q"), {})

    def test_write_beats_read_for_same_path(self):
        ops = self._files("python gen.py > gen.py")   # degenerate but defined
        self.assertEqual(ops, {"gen.py": "write"})

    def test_pytest_nodeid_suffix_dropped(self):
        self.assertEqual(self._files("pytest tests/test_a.py::test_one -q"),
                         {"tests/test_a.py": "read"})


class TestClassifyBash(unittest.TestCase):
    def test_readonly_leaders_are_explore(self):
        for cmd in ("ls -la", "grep -r foo src", "cat a.txt | grep b",
                    "find . -name '*.py'", "wc -l parser.py"):
            self.assertEqual(_classify_bash(cmd), "explore", cmd)

    def test_git_subcommands_split(self):
        self.assertEqual(_classify_bash("git status"), "explore")
        self.assertEqual(_classify_bash("git log --oneline -5"), "explore")
        self.assertEqual(_classify_bash("git diff HEAD~1"), "explore")
        self.assertEqual(_classify_bash("git commit -m 'x'"), "execute")
        self.assertEqual(_classify_bash("git stash"), "execute")

    def test_mutating_default_is_execute(self):
        for cmd in ("python -m pytest -q", "rm -rf build", "pip install x",
                    "make", ""):
            self.assertEqual(_classify_bash(cmd), "execute", cmd)

    def test_only_first_segment_counts(self):
        # read-only leader first, mutation later: classified by the leader
        self.assertEqual(_classify_bash("ls && rm -rf build"), "explore")


class TestBashSignature(unittest.TestCase):
    def test_paths_numbers_strings_normalise(self):
        a = _bash_signature("pytest tests/a.py -k 'foo' --maxfail 3")
        b = _bash_signature("pytest tests/b.py -k 'bar' --maxfail 5")
        self.assertEqual(a, b)

    def test_different_commands_stay_apart(self):
        self.assertNotEqual(_bash_signature("pytest -q"),
                            _bash_signature("git status"))


class TestBashNetwork(unittest.TestCase):
    def _net(self, cmd):
        return _bash_network(cmd)

    def test_curl_url(self):
        self.assertEqual(self._net("curl -s https://api.github.com/repos/a/b"),
                         [("http", "api.github.com/repos/a/b")])

    def test_git_push_named_remote(self):
        self.assertEqual(self._net("git push origin main"), [("git", "origin")])

    def test_git_clone_url(self):
        self.assertEqual(self._net("git clone https://github.com/a/b.git"),
                         [("git", "github.com/a/b.git")])

    def test_git_status_is_not_network(self):
        self.assertEqual(self._net("git status"), [])

    def test_pip_install(self):
        self.assertEqual(self._net("pip install requests"),
                         [("package", "pip install requests")])

    def test_uv_pip_install_nested_subcommand(self):
        self.assertEqual(self._net("uv pip install requests"),
                         [("package", "uv install requests")])

    def test_env_var_prefix_skipped(self):
        self.assertEqual(self._net("FOO=1 curl https://example.com/x"),
                         [("http", "example.com/x")])

    def test_plain_shell_is_silent(self):
        self.assertEqual(self._net("ls -la && pytest -q"), [])

    def test_segments_split_on_pipes_and_ands(self):
        ops = self._net("curl https://a.com/1 && curl https://b.com/2")
        self.assertEqual(ops, [("http", "a.com/1"), ("http", "b.com/2")])


class TestHostOf(unittest.TestCase):
    def test_strips_scheme_and_user(self):
        self.assertEqual(_host_of("https://github.com/a/b"), "github.com/a/b")
        self.assertEqual(_host_of("git@github.com:a/b.git"), "github.com:a/b.git")

    def test_truncates_to_60(self):
        self.assertEqual(len(_host_of("https://x.com/" + "a" * 100)), 60)


class TestToolNetwork(unittest.TestCase):
    def test_webfetch_and_websearch(self):
        self.assertEqual(_tool_network("WebFetch", {"url": "https://docs.pytest.org/x"}),
                         [("http", "docs.pytest.org/x")])
        self.assertEqual(_tool_network("WebSearch", {"query": "pytest marks MRO"}),
                         [("search", "pytest marks MRO")])

    def test_mcp_tools(self):
        self.assertEqual(_tool_network("mcp__gmail__send", {}),
                         [("mcp", "gmail__send")])

    def test_local_tools_are_silent(self):
        self.assertEqual(_tool_network("Read", {"file_path": "a.py"}), [])


class TestFileOpsAndLabel(unittest.TestCase):
    def test_structured_tools_use_file_path_and_phase_mode(self):
        self.assertEqual(_file_ops("Read", {"file_path": "src/a.py"}),
                         [("src/a.py", "read")])
        self.assertEqual(_file_ops("Edit", {"file_path": "src/a.py"}),
                         [("src/a.py", "write")])

    def test_label_shapes(self):
        self.assertEqual(_label("Bash", {"command": "ls\n-la"}), "ls -la")
        self.assertEqual(_label("Read", {"file_path": "a.py"}), "a.py")
        self.assertEqual(_label("Grep", {"pattern": "def foo"}), "def foo")


class TestCost(unittest.TestCase):
    def test_opus_rates_and_default_fallback(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 0,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        self.assertAlmostEqual(turn_cost("claude-opus-4-8", usage), 15.00)
        self.assertAlmostEqual(turn_cost("mystery-model", usage), 3.00)
        self.assertAlmostEqual(turn_cost(None, {}), 0.0)


if __name__ == "__main__":
    unittest.main()
