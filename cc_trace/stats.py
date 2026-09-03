"""Group-level statistics across runs — is a cross-run difference real?

``compare`` puts runs side by side; this module answers the next question:
*given how few runs we can afford, is the gap between two groups (models, task
types, prompt variants) distinguishable from noise?*

Deliberately conservative, because n is small and the findings are the product:

* **Exact permutation test** (Mann-Whitney U) whenever the split count is
  enumerable — no normal approximation, no tie-correction fudge, and correct for
  the tied metric values that phase purity produces in practice. Falls back to a
  seeded Monte-Carlo permutation test only when enumeration is too big.
* **A design floor.** With n1=n2=3 the smallest reachable two-sided p is 0.1, so
  "not significant" says nothing about the effect — it says the design cannot
  resolve it. :func:`min_two_sided_p` reports that floor and
  :func:`compare_groups` flags every test that is *underpowered by construction*.
* **Cliff's delta** alongside p — a nonparametric effect size that stays
  meaningful when the p-value cannot clear its floor.
* **Seeded bootstrap CIs** on the difference of medians, so the same inputs
  always produce the same interval (a figure in a write-up must be reproducible).

Stdlib only: no scipy, no numpy.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from itertools import combinations
from typing import Any, Iterable, Optional, Sequence

# Metrics worth testing by default, with the direction that reads as "more".
# Keys match cc_trace.compare.summarize() exactly.
DEFAULT_METRICS = (
    "purity",
    "separation",
    "explore_share",
    "redundant_frac",
    "cache_read_share",
    "n_tool_calls",
    "duration",
    "cost",
)

# Enumerate the exact permutation null while the split count stays under this.
EXACT_MAX_SPLITS = 50_000
# Otherwise sample this many permutations (seeded).
MONTE_CARLO_N = 20_000
DEFAULT_SEED = 0


# --------------------------------------------------------------------------
# descriptives
# --------------------------------------------------------------------------

def describe(xs: Sequence[float]) -> dict[str, Any]:
    """n / median / mean / spread for one group, tolerant of tiny samples."""
    vals = sorted(float(x) for x in xs)
    n = len(vals)
    if n == 0:
        return {"n": 0, "median": None, "mean": None, "sd": None,
                "min": None, "max": None, "iqr": None}
    return {
        "n": n,
        "median": statistics.median(vals),
        "mean": statistics.fmean(vals),
        # sd is undefined for n=1; report None rather than raising
        "sd": statistics.stdev(vals) if n > 1 else None,
        "min": vals[0],
        "max": vals[-1],
        "iqr": (_quantile(vals, 0.75) - _quantile(vals, 0.25)) if n > 1 else 0.0,
    }


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (numpy's default), on pre-sorted input."""
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    pos = (n - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_vals[int(pos)])
    return float(sorted_vals[lo] * (hi - pos) + sorted_vals[hi] * (pos - lo))


# --------------------------------------------------------------------------
# effect size
# --------------------------------------------------------------------------

def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Cliff's delta in [-1, 1]: P(a>b) − P(a<b).

    Nonparametric, ordinal, and unaffected by the sample sizes — which is why it
    carries the story when the p-value is floored by a small design.
    """
    if not a or not b:
        return None
    gt = sum(1 for x in a for y in b if x > y)
    lt = sum(1 for x in a for y in b if x < y)
    return (gt - lt) / (len(a) * len(b))


def delta_magnitude(d: Optional[float]) -> str:
    """Romano et al.'s conventional thresholds for |Cliff's delta|."""
    if d is None:
        return "—"
    ad = abs(d)
    if ad < 0.147:
        return "negligible"
    if ad < 0.330:
        return "small"
    if ad < 0.474:
        return "medium"
    return "large"


# --------------------------------------------------------------------------
# Mann-Whitney U, by permutation
# --------------------------------------------------------------------------

def _midranks(vals: Sequence[float]) -> list[float]:
    """Ranks 1..n with ties averaged — the tie handling U needs."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0          # ranks are 1-based
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def min_two_sided_p(n1: int, n2: int) -> Optional[float]:
    """Smallest two-sided p this design can possibly produce.

    The permutation null has ``C(n1+n2, n1)`` equally likely splits; the most
    extreme observation matches exactly one of them in each direction. So no
    amount of separation between the groups can push p below ``2/C(n1+n2, n1)``
    — with 3 vs 3 that floor is 0.1, and p<0.05 is unreachable *by design*.
    """
    if n1 < 1 or n2 < 1:
        return None
    return min(1.0, 2.0 / math.comb(n1 + n2, n1))


def mannwhitney(a: Sequence[float], b: Sequence[float],
                exact_max_splits: int = EXACT_MAX_SPLITS,
                n_perm: int = MONTE_CARLO_N,
                seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """Two-sided Mann-Whitney U by permutation. Exact when enumerable.

    Returns ``u`` (for group ``a``), the two-sided ``p``, the ``method`` used,
    and ``p_floor`` — the smallest p the design could have produced.
    """
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return {"u": None, "p": None, "method": "insufficient-data",
                "n1": n1, "n2": n2, "p_floor": None, "n_splits": None}

    pooled = list(a) + list(b)
    ranks = _midranks(pooled)
    idx_all = range(n1 + n2)
    offset = n1 * (n1 + 1) / 2.0

    def u_of(idx: Iterable[int]) -> float:
        return sum(ranks[i] for i in idx) - offset

    u_obs = u_of(range(n1))
    centre = n1 * n2 / 2.0
    dev = abs(u_obs - centre)
    # float ranks make exact equality fragile; compare with a tolerance
    tol = 1e-9

    n_splits = math.comb(n1 + n2, n1)
    if n_splits <= exact_max_splits:
        extreme = sum(1 for c in combinations(idx_all, n1)
                      if abs(u_of(c) - centre) >= dev - tol)
        p = extreme / n_splits
        method = "exact"
    else:
        rng = random.Random(seed)
        pool = list(idx_all)
        extreme = 0
        for _ in range(n_perm):
            rng.shuffle(pool)
            if abs(u_of(pool[:n1]) - centre) >= dev - tol:
                extreme += 1
        # +1 smoothing: a Monte-Carlo p is never reported as an impossible 0
        p = (extreme + 1) / (n_perm + 1)
        method = "monte-carlo"

    return {"u": u_obs, "p": min(1.0, p), "method": method,
            "n1": n1, "n2": n2, "n_splits": n_splits,
            "p_floor": min_two_sided_p(n1, n2)}


# --------------------------------------------------------------------------
# bootstrap
# --------------------------------------------------------------------------

def bootstrap_diff_ci(a: Sequence[float], b: Sequence[float],
                      stat: str = "median", n_resamples: int = 10_000,
                      level: float = 0.95,
                      seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """Percentile bootstrap CI for ``stat(a) − stat(b)``. Seeded, so repeatable.

    With very small groups the resampling space is itself tiny (3 values give 10
    distinct resamples), so the interval is wide and lumpy — that honesty is the
    point, not a defect to smooth over.
    """
    if not a or not b:
        return {"point": None, "lo": None, "hi": None, "level": level,
                "stat": stat, "n_resamples": 0}
    f = statistics.median if stat == "median" else statistics.fmean
    A, B = [float(x) for x in a], [float(x) for x in b]
    point = f(A) - f(B)

    rng = random.Random(seed)
    diffs = []
    for _ in range(n_resamples):
        ra = [A[rng.randrange(len(A))] for _ in range(len(A))]
        rb = [B[rng.randrange(len(B))] for _ in range(len(B))]
        diffs.append(f(ra) - f(rb))
    diffs.sort()
    alpha = (1.0 - level) / 2.0
    return {"point": point,
            "lo": _quantile(diffs, alpha),
            "hi": _quantile(diffs, 1.0 - alpha),
            "level": level, "stat": stat, "n_resamples": n_resamples}


# --------------------------------------------------------------------------
# multiple comparisons
# --------------------------------------------------------------------------

def holm(pvals: Sequence[Optional[float]]) -> list[Optional[float]]:
    """Holm-Bonferroni step-down adjustment, preserving input order.

    Testing eight metrics on one set of runs is eight chances to find something;
    Holm controls the family-wise error rate without Bonferroni's bluntness.
    ``None`` entries (untestable metrics) pass through and don't count toward m.
    """
    idx = [i for i, p in enumerate(pvals) if p is not None]
    m = len(idx)
    out: list[Optional[float]] = [None] * len(pvals)
    if m == 0:
        return out
    ordered = sorted(idx, key=lambda i: pvals[i])  # type: ignore[index]
    running = 0.0
    for rank, i in enumerate(ordered):
        adj = (m - rank) * float(pvals[i])         # type: ignore[arg-type]
        running = max(running, adj)                # enforce monotonicity
        out[i] = min(1.0, running)
    return out


# --------------------------------------------------------------------------
# the group comparison
# --------------------------------------------------------------------------

def group_values(rows: Sequence[dict], metric: str) -> "dict[str, list[float]]":
    """Collect ``metric`` per group, dropping runs where it is None."""
    groups: dict[str, list[float]] = {}
    for r in rows:
        g = r.get("group") or r.get("label") or "?"
        v = r.get(metric)
        if v is None:
            continue
        groups.setdefault(g, []).append(float(v))
    return groups


def compare_groups(rows: Sequence[dict],
                   metrics: Sequence[str] = DEFAULT_METRICS,
                   seed: int = DEFAULT_SEED,
                   n_resamples: int = 10_000,
                   level: float = 0.95) -> dict[str, Any]:
    """Full stats rollup: descriptives + a pairwise test per metric.

    Each row is a ``compare.summarize()`` mapping carrying a ``group`` key. Two
    groups gives one test per metric; more gives every pair. Holm adjustment is
    applied across metrics *within* each pair.
    """
    names: list[str] = []
    for r in rows:
        g = r.get("group") or r.get("label") or "?"
        if g not in names:
            names.append(g)

    per_metric_groups = {m: group_values(rows, m) for m in metrics}
    descriptives = {
        m: {g: describe(per_metric_groups[m].get(g, [])) for g in names}
        for m in metrics
    }

    pairs = []
    for g1, g2 in combinations(names, 2):
        tests = []
        for m in metrics:
            a = per_metric_groups[m].get(g1, [])
            b = per_metric_groups[m].get(g2, [])
            if len(a) < 1 or len(b) < 1:
                tests.append({"metric": m, "p": None, "delta": None,
                              "method": "insufficient-data",
                              "n1": len(a), "n2": len(b),
                              "underpowered": None, "ci": None,
                              "median_a": None, "median_b": None})
                continue
            mw = mannwhitney(a, b, seed=seed)
            ci = bootstrap_diff_ci(a, b, n_resamples=n_resamples,
                                   level=level, seed=seed)
            d = cliffs_delta(a, b)
            floor = mw["p_floor"]
            # With a single run in either group every comparison is a forced
            # win: delta is ±1 whatever the values, and the bootstrap can only
            # resample one number, so the CI collapses onto the point estimate.
            # Neither is evidence, and neither should be rendered as if it were.
            degenerate = min(len(a), len(b)) < 2
            tests.append({
                "metric": m,
                "median_a": statistics.median(a),
                "median_b": statistics.median(b),
                "delta": d,
                "magnitude": "n/a (n=1)" if degenerate else delta_magnitude(d),
                "degenerate": degenerate,
                "u": mw["u"], "p": mw["p"], "method": mw["method"],
                "n1": mw["n1"], "n2": mw["n2"],
                "p_floor": floor,
                # the design, not the data, decides this
                "underpowered": (floor is not None and floor > 0.05),
                "ci": ci,
            })
        adj = holm([t["p"] for t in tests])
        for t, pa in zip(tests, adj):
            t["p_holm"] = pa
        pairs.append({"a": g1, "b": g2, "tests": tests})

    return {
        "groups": names,
        "n_runs": len(rows),
        "runs": [{"label": r.get("label"), "group": r.get("group"),
                  "session": r.get("session")} for r in rows],
        "metrics": list(metrics),
        "descriptives": descriptives,
        "pairs": pairs,
        "seed": seed,
        "level": level,
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _f(v: Optional[float], nd: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return "—"
    return f"{v:.{nd}f}"


def _table(head: Sequence[str], body: Sequence[Sequence[str]]) -> list[str]:
    if not body:
        return []
    widths = [max(len(head[i]), *(len(r[i]) for r in body))
              for i in range(len(head))]
    line = lambda cells: "  ".join(str(c).ljust(widths[i])
                                   for i, c in enumerate(cells))
    return [line(head), line(["-" * w for w in widths])] + [line(r) for r in body]


def render_text(res: dict[str, Any]) -> str:
    out: list[str] = []
    groups = res["groups"]
    out.append(f"{res['n_runs']} run(s) in {len(groups)} group(s): "
               + ", ".join(f"{g} (n={sum(1 for r in res['runs'] if (r.get('group') or r.get('label')) == g)})"
                           for g in groups))
    out.append("")

    out.append("GROUP MEDIANS")
    head = ["metric"] + [f"{g}" for g in groups]
    body = []
    for m in res["metrics"]:
        row = [m]
        for g in groups:
            d = res["descriptives"][m][g]
            row.append("—" if d["n"] == 0
                       else f"{_f(d['median'])} (n={d['n']})")
        body.append(row)
    out += _table(head, body)
    out.append("")

    for pair in res["pairs"]:
        out.append(f"{pair['a']}  vs  {pair['b']}")
        head = ["metric", f"med({pair['a'][:8]})", f"med({pair['b'][:8]})",
                "delta", "size", "p", "p_holm", "95% CI (diff of medians)"]
        body = []
        for t in pair["tests"]:
            if t["p"] is None:
                body.append([t["metric"], "—", "—", "—", "—", "—", "—",
                             f"({t['method']})"])
                continue
            ci = t["ci"]
            deg = t.get("degenerate")
            body.append([
                t["metric"],
                _f(t["median_a"]), _f(t["median_b"]),
                "—" if deg else _f(t["delta"], 2), t["magnitude"],
                _f(t["p"], 3), _f(t.get("p_holm"), 3),
                "(single run — no interval)" if deg
                else f"[{_f(ci['lo'])}, {_f(ci['hi'])}]",
            ])
        out += _table(head, body)

        testable = [t for t in pair["tests"] if t["p"] is not None]
        if testable and all(t["underpowered"] for t in testable):
            floor = testable[0]["p_floor"]
            out.append("")
            out.append(f"  !! UNDERPOWERED BY DESIGN — n={testable[0]['n1']} vs "
                       f"{testable[0]['n2']} puts the smallest reachable "
                       f"two-sided p at {floor:.3f}.")
            out.append("     No p here can clear 0.05 no matter how large the "
                       "effect. Read the deltas and CIs, not the p-values,")
            out.append("     and treat 'not significant' as 'not yet tested'. "
                       + _needed_n_hint())
        out.append("")

    out.append(f"exact permutation test where enumerable; bootstrap seed={res['seed']}, "
               f"{int(res['level']*100)}% percentile CI")
    out.append("delta = Cliff's delta (P(a>b) − P(a<b)); p_holm = Holm-adjusted "
               "across the metrics above")
    return "\n".join(out)


def _needed_n_hint() -> str:
    """Smallest balanced n whose exact floor clears 0.05."""
    n = 2
    while n < 40:
        if (min_two_sided_p(n, n) or 1.0) <= 0.05:
            return f"n={n} per group is the smallest balanced design that can reach p<0.05."
        n += 1
    return ""
