"""Tests for the egress allowlist proxy (network-isolated runs).

The allowlist is the thing standing between a graded benchmark run and finding
11's retrieval hole, so the host matching has to be exact about label
boundaries: a suffix check that isn't anchored on a dot would let
``api.anthropic.com.attacker.test`` through.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from egress_proxy import _target_of, allowed, summarize  # noqa: E402


class TestAllowlist(unittest.TestCase):
    ALLOW = ["api.anthropic.com"]

    def test_exact_host_allowed(self):
        self.assertTrue(allowed("api.anthropic.com", self.ALLOW))

    def test_subdomain_allowed(self):
        self.assertTrue(allowed("eu.api.anthropic.com", self.ALLOW))

    def test_case_and_trailing_dot_normalized(self):
        self.assertTrue(allowed("API.Anthropic.Com.", self.ALLOW))

    def test_retrieval_hosts_denied(self):
        # the two paths Fable 5 actually used on sympy-16597 (finding 11)
        for host in ("github.com", "patch-diff.githubusercontent.com",
                     "api.github.com", "raw.githubusercontent.com"):
            self.assertFalse(allowed(host, self.ALLOW), host)

    def test_suffix_confusion_denied(self):
        """The bypasses an unanchored endswith() would let through."""
        for host in ("evil-api.anthropic.com",
                     "api.anthropic.com.attacker.test",
                     "notanthropic.com",
                     "xapi.anthropic.com.evil"):
            self.assertFalse(allowed(host, self.ALLOW), host)

    def test_empty_allowlist_denies_everything(self):
        self.assertFalse(allowed("api.anthropic.com", []))


class TestRequestParsing(unittest.TestCase):
    def test_connect_default_port(self):
        self.assertEqual(_target_of("CONNECT api.anthropic.com:443 HTTP/1.1"),
                         ("CONNECT", "api.anthropic.com", 443))

    def test_plain_http_absolute_uri(self):
        self.assertEqual(_target_of("GET http://pypi.org/simple/ HTTP/1.1"),
                         ("GET", "pypi.org", 80))

    def test_plain_http_explicit_port(self):
        self.assertEqual(_target_of("GET http://example.test:8080/x HTTP/1.1"),
                         ("GET", "example.test", 8080))

    def test_garbage_returns_none(self):
        self.assertIsNone(_target_of("garbage"))


class TestSummarize(unittest.TestCase):
    def test_rollup_counts_denials(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "egress.jsonl"
            rows = [
                {"decision": "allow", "host": "api.anthropic.com"},
                {"decision": "allow", "host": "api.anthropic.com"},
                {"decision": "deny", "host": "github.com"},
                {"decision": "deny", "host": "pypi.org"},
                {"decision": "deny", "host": "pypi.org"},
                {"decision": "error", "host": "?"},
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            s = summarize(p)
        self.assertEqual(s["allow"]["api.anthropic.com"], 2)
        self.assertEqual(s["deny"]["pypi.org"], 2)
        self.assertEqual(s["n_denied"], 3)
        self.assertEqual(s["n_denied_hosts"], 2)
        self.assertEqual(s["error"], 1)
        self.assertEqual(s["n_events"], 6)


if __name__ == "__main__":
    unittest.main()


class TestEgressAudit(unittest.TestCase):
    """The signal/noise split that answers 'is non-model tracing too noisy?'."""

    def test_classes_separate_model_vendor_and_agent(self):
        from egress_audit import classify
        self.assertEqual(classify("api.anthropic.com"), "model")
        self.assertEqual(classify("http-intake.logs.us5.datadoghq.com"), "vendor")
        self.assertEqual(classify("pypi.org"), "agent")
        self.assertEqual(classify("patch-diff.githubusercontent.com"), "agent")

    def test_package_descriptor_resolves_to_its_index_host(self):
        # the parser records `pip install mpmath` — no hostname in the command —
        # so scoring it against the proxy needs the index each tool reaches
        from egress_audit import PKG_INDEX
        self.assertEqual(PKG_INDEX["pip"], "pypi.org")
        self.assertEqual(PKG_INDEX["cargo"], "crates.io")

    def test_load_skips_malformed_lines(self):
        from egress_audit import load
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "e.jsonl"
            p.write_text('{"host":"pypi.org","decision":"deny","t":1}\n'
                         'not json\n\n', encoding="utf-8")
            self.assertEqual(len(load(p)), 1)
