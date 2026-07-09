"""Golden-metric snapshot of the committed synthetic example, plus a PII guard.

The sample transcript is committed and stable, so its parsed metrics are pinned
exactly: any parser change that moves a published-metric definition shows up
here as a diff you have to consciously accept.

The PII test enforces the repo rule that committed artifacts never carry
personal paths or addresses (reports/ is gitignored for exactly this reason).
"""
import unittest
from pathlib import Path

from cc_trace.parser import parse_transcript

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "examples" / "sample-session.jsonl"


class TestGoldenSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace = parse_transcript(str(SAMPLE))
        cls.d = cls.trace.as_dict()

    def test_shape(self):
        self.assertEqual(self.d["n_tool_calls"], 15)
        self.assertEqual(self.d["n_turns"], 15)
        self.assertEqual(self.d["n_errors"], 2)
        self.assertEqual(self.d["session_id"],
                         "5a17e000-deadbeef-demo-0000-000000000001")

    def test_phase_metrics(self):
        xo = self.d["phase_crossover"]
        seq = "".join("E" if c["phase"] == "explore" else "X"
                      for c in self.d["tool_calls"] if c["phase"] != "other")
        # pinned actuals: `git fetch` classifies execute, `cat >` explore —
        # heuristic warts included on purpose, so any reclassification is loud
        self.assertEqual(seq, "EXEEEXXEEXXXXXE")
        self.assertEqual(xo["n"], 15)
        self.assertEqual(xo["purity"], 0.733)
        self.assertEqual(xo["pos"], 0.333)

    def test_token_totals_and_cache_share(self):
        tt = self.d["token_totals"]
        self.assertEqual(tt["output"], 3360)          # sum of 140+12i, i=0..14
        self.assertEqual(tt["input"], 180 * 15)
        share = tt["cache_read"] / (tt["cache_read"] + tt["input"])
        self.assertGreater(share, 0.97)               # KV-cache-heavy by design

    def test_bash_file_io_extracted(self):
        files = {r["file"]: r for r in self.d["file_access"]}
        # shell-redirect write and here-doc write are both caught
        self.assertEqual(files["tests/fixtures/expected.json"]["writes"], 1)
        self.assertEqual(files["CHANGELOG.md"]["writes"], 1)

    def test_network_panel_seeded(self):
        kinds = {k["kind"] for k in self.d["network_activity"]["by_kind"]}
        self.assertEqual(kinds, {"git", "package", "http"})

    def test_retry_loop_detected(self):
        loops = self.d["retry_loops"]
        self.assertEqual(len(loops), 1)
        self.assertEqual(loops[0]["attempts"], 3)     # pytest x3, 2 errors
        self.assertEqual(loops[0]["errors"], 2)


class TestNoPersonalData(unittest.TestCase):
    """Committed artifacts must never leak personal paths/emails."""

    FORBIDDEN = ("saifyfairozkhan", "saify2001", "fkhan35",
                 "/Users/saify", "148651031+")

    def test_committed_examples_and_docs_are_clean(self):
        targets = [SAMPLE, *(REPO / "examples").glob("*.html"),
                   *(REPO / "examples").glob("*.json"),
                   REPO / "README.md", REPO / "FINDINGS.md", REPO / "REPORT.md"]
        for path in targets:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for needle in self.FORBIDDEN:
                # the GitHub org name in repo URLs is fine; personal paths are not
                self.assertNotIn(needle, text,
                                 f"{path.name} contains forbidden {needle!r}")


if __name__ == "__main__":
    unittest.main()
