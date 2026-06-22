"""Compare several Claude Code runs against each other.

Where the single-session report answers "what did this run do?", ``compare``
answers the cross-run / research question: *do the workload characteristics from
the paper hold, and how do they vary by task type?* It aggregates, per run:

* **phase mix** — explore vs. execute share, plus the explore/execute *position*
  in the run and the raw ``EEXX…`` sequence (the front-loading signal).
* **tool mix** — which tools dominate (search-heavy vs. Bash-heavy, …).
* **cache-read share** — how decode-dominated / KV-cache-heavy the run is.
* **cost & duration**.

Inputs may be saved ``.json`` reports (from ``--json``), raw ``.jsonl``
transcripts, or session ids — all normalised to the :meth:`Trace.as_dict` shape
so a directory of past reports can be compared without re-running anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .parser import parse_transcript

# How "Task category: **<x>**" is written in the benchmark prompts under tasks/.
_TASK_TAG = "Task category:"


def load_trace_dict(target: str, resolve) -> dict[str, Any]:
    """Normalise one input to a Trace.as_dict() mapping.

    ``target`` may be a parsed ``.json`` report, a ``.jsonl`` transcript, or a
    session id/prefix. ``resolve`` is the CLI's transcript resolver (reused so a
    bare session id works the same way it does for the single-session report).
    """
    p = Path(target)
    if p.is_file() and p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    path = p if (p.is_file() and p.suffix == ".jsonl") else resolve(target, False)
    if path is None:
        raise FileNotFoundError(f"no transcript/report for {target!r}")
    return parse_transcript(str(path)).as_dict()


def _label(d: dict) -> str:
    """Short, human-friendly run label: task category if tagged, else cwd dir."""
    for pr in d.get("user_prompts", []):
        if _TASK_TAG in pr:
            after = pr.split(_TASK_TAG, 1)[1].strip().strip("*").strip()
            cat = after.splitlines()[0].strip().strip("*").strip()
            if cat:
                return cat[:24]
    cwd = d.get("cwd") or ""
    return Path(cwd).name[:24] or (d.get("session_id") or "?")[:8]


def _phase_positions(calls: list[dict]) -> dict[str, Any]:
    """Mean normalised position (0..1) of explore vs. execute calls + sequence.

    Positions are taken over the sub-sequence of phased (non-"other") calls, so
    they describe *ordering*: explore-pos < execute-pos means explore front-loads.
    ``separation`` = execute_pos - explore_pos: higher = cleaner explore→execute
    phase shift; near zero = the two interleave (an explore/act loop).
    """
    phased = [c for c in calls if c.get("phase") in ("explore", "execute")]
    n = len(phased)
    seq = "".join("E" if c["phase"] == "explore" else "X" for c in phased)

    def mean_pos(phase: str) -> float | None:
        idx = [i for i, c in enumerate(phased) if c["phase"] == phase]
        if not idx or n <= 1:
            return 0.0 if idx else None
        return round(sum(idx) / len(idx) / (n - 1), 3)

    ep, xp = mean_pos("explore"), mean_pos("execute")
    sep = round(xp - ep, 3) if (ep is not None and xp is not None) else None
    return {"sequence": seq, "explore_pos": ep, "execute_pos": xp, "separation": sep}


def summarize(d: dict) -> dict[str, Any]:
    """Reduce one Trace.as_dict() to the comparable cross-run metrics."""
    pc = d.get("phase_counts", {}) or {}
    explore, execute = pc.get("explore", 0), pc.get("execute", 0)
    phased = explore + execute
    tt = d.get("token_totals", {}) or {}
    cache_read, fresh_in, out = (tt.get("cache_read", 0), tt.get("input", 0),
                                 tt.get("output", 0))
    tb = d.get("tool_breakdown", []) or []
    xover = d.get("phase_crossover", {}) or {}
    na = d.get("network_activity", {}) or {}
    return {
        "net_total": na.get("total", 0),
        "net_kinds": na.get("by_kind", []) or [],
        "crossover_pos": xover.get("pos"),
        "purity": xover.get("purity"),
        "crossover_index": xover.get("index"),
        "label": _label(d),
        "session": (d.get("session_id") or "")[:8],
        "n_tool_calls": d.get("n_tool_calls", 0),
        "n_turns": d.get("n_turns", 0),
        "duration": d.get("duration", 0.0),
        "cost": d.get("total_cost", 0.0),
        "n_errors": d.get("n_errors", 0),
        "n_retry_loops": len(d.get("retry_loops", []) or []),
        "explore": explore,
        "execute": execute,
        "explore_share": round(explore / phased, 3) if phased else None,
        "tool_mix": {t["name"]: t["count"] for t in tb},
        "top_tool": tb[0]["name"] if tb else None,
        # cache-read share of *read* tokens: the KV-cache-reuse / decode signal.
        "cache_read_share": round(cache_read / (cache_read + fresh_in), 3)
        if (cache_read + fresh_in) else None,
        "output_tokens": out,
        **_phase_positions(d.get("tool_calls", []) or []),
    }


def _fmt(v, nd=2):
    return "—" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def render_text(rows: list[dict]) -> str:
    """A compact comparison table for the terminal."""
    cols = [
        ("run", "label", lambda r: r["label"]),
        ("calls", None, lambda r: r["n_tool_calls"]),
        ("turns", None, lambda r: r["n_turns"]),
        ("dur(s)", None, lambda r: f"{r['duration']:.0f}"),
        ("cost$", None, lambda r: f"{r['cost']:.2f}"),
        ("err", None, lambda r: r["n_errors"]),
        ("loops", None, lambda r: r["n_retry_loops"]),
        ("expl%", None, lambda r: _fmt(r["explore_share"])),
        ("sep", None, lambda r: _fmt(r["separation"])),
        ("pure", None, lambda r: _fmt(r["purity"])),
        ("cache%", None, lambda r: _fmt(r["cache_read_share"])),
        ("net", None, lambda r: r["net_total"] or "—"),
        ("top tool", None, lambda r: r["top_tool"] or "—"),
    ]
    head = [c[0] for c in cols]
    body = [[str(c[2](r)) for c in cols] for r in rows]
    widths = [max(len(head[i]), *(len(b[i]) for b in body)) for i in range(len(cols))]
    line = lambda cells: "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))
    out = [line(head), line(["-" * w for w in widths])]
    out += [line(b) for b in body]
    out.append("")
    out.append("sep  = execute_pos − explore_pos  (↑ cleaner explore→execute shift; "
               "~0 = interleaved loop)")
    out.append("pure = how cleanly the run splits at the explore→execute crossover "
               "(1.0 = perfect phase shift, ~0.5 = interleaved)")
    out.append("cache% = cache-read share of read tokens (↑ more KV-cache-heavy / "
               "decode-dominated)")
    out.append("phase sequence — '|' marks the explore→execute crossover point:")
    for r in rows:
        seq = r["sequence"] or ""
        k = r["crossover_index"]
        marked = (seq[:k] + "|" + seq[k:]) if (seq and k is not None) else (seq or "(no phased calls)")
        out.append(f"  {r['label']:<24} {marked}")
    return "\n".join(out)


def render_html(rows: list[dict]) -> str:
    data = json.dumps(rows, separators=(",", ":"))
    return _TEMPLATE.replace("__DATA__", data)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Code Trace — compare</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --txt:#e6edf3;
    --muted:#8b949e; --explore:#3fb950; --execute:#f0883e; --accent:#58a6ff; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  header { padding:24px 28px; border-bottom:1px solid var(--line); }
  h1 { margin:0 0 4px; font-size:20px; }
  .sub { color:var(--muted); font-size:13px; }
  main { padding:20px 28px 60px; max-width:1180px; }
  section { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:18px 20px; margin-bottom:20px; }
  h2 { margin:0 0 4px; font-size:15px; }
  .hint { color:var(--muted); font-size:12px; margin:0 0 14px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-weight:500; font-size:12px; }
  td.num,th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .seq { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:1px; }
  .seq i { font-style:normal; }
  .seq .E { color:var(--explore); }
  .seq .X { color:var(--execute); }
  .seq .xover { color:var(--accent); font-weight:700; }
  .mix { display:flex; height:14px; border-radius:3px; overflow:hidden; min-width:120px; }
  .mix span { display:block; }
  .legend { display:flex; gap:16px; font-size:12px; color:var(--muted); margin-top:10px; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:2px;
    margin-right:5px; vertical-align:middle; }
  code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>Claude Code — run comparison</h1>
  <div class="sub" id="meta"></div>
</header>
<main>
  <section>
    <h2>Cross-run metrics</h2>
    <p class="hint">One row per run. <b>sep</b> = execute_pos − explore_pos: higher
      means a cleaner explore→execute phase shift; near zero means explore and
      execute interleave (an explore/act loop). <b>cache%</b> = cache-read share of
      read tokens — the KV-cache-reuse / decode-dominated signal.</p>
    <table id="tbl"></table>
  </section>

  <section>
    <h2>Phase sequence per run</h2>
    <p class="hint">Each square is one phased tool call in order — front of the run
      on the left. Watch whether <span style="color:var(--explore)">explore</span>
      front-loads before <span style="color:var(--execute)">execute</span>.</p>
    <div id="seqs"></div>
    <div class="legend">
      <span><i class="dot" style="background:var(--explore)"></i>explore / read</span>
      <span><i class="dot" style="background:var(--execute)"></i>execute / write</span>
    </div>
  </section>

  <section>
    <h2>Tool mix per run</h2>
    <p class="hint">Relative share of tool calls by tool — search/read-heavy vs.
      Bash/edit-heavy.</p>
    <table id="mix"></table>
  </section>
</main>

<script id="data" type="application/json">__DATA__</script>
<script>
const R = JSON.parse(document.getElementById('data').textContent);
const fmt = (v,nd=2)=> v==null ? '—' : (typeof v==='number' ? (Number.isInteger(v)?v:v.toFixed(nd)) : v);
document.getElementById('meta').textContent = `${R.length} run${R.length===1?'':'s'} compared`;

// ---- metrics table ----
(function(){
  const cols=[['run','label'],['calls','n_tool_calls'],['turns','n_turns'],
    ['dur(s)','duration'],['cost$','cost'],['err','n_errors'],['loops','n_retry_loops'],
    ['expl%','explore_share'],['sep','separation'],['pure','purity'],
    ['cache%','cache_read_share'],['net','net_total'],['top tool','top_tool']];
  let s=`<tr>${cols.map(([h],i)=>`<th class="${i?'num':''}">${h}</th>`).join('')}</tr>`;
  s+=R.map(r=>`<tr>${cols.map(([h,k],i)=>{
    let v=r[k];
    if(k==='duration') v=v.toFixed(0);
    else if(k==='cost') v=v.toFixed(2);
    else v=fmt(v);
    return `<td class="${i?'num':''}">${v}</td>`;
  }).join('')}</tr>`).join('');
  document.getElementById('tbl').innerHTML=s;
})();

// ---- phase sequences (with '|' marking the explore→execute crossover) ----
(function(){
  document.getElementById('seqs').innerHTML = R.map(r=>{
    const raw=r.sequence||'', k=r.crossover_index;
    const cells=raw.split('').map((ch,i)=>
      (k!=null && i===k ? '<i class="xover">|</i>' : '') + `<i class="${ch}">${ch}</i>`).join('')
      + (k!=null && k===raw.length ? '<i class="xover">|</i>' : '');
    return `<div style="margin:8px 0"><div style="color:var(--muted);font-size:12px">${r.label} `+
      `· sep ${fmt(r.separation)} · purity ${fmt(r.purity)} · n=${raw.length}</div>`+
      `<div class="seq">${cells||'(no phased calls)'}</div></div>`;
  }).join('');
})();

// ---- tool mix ----
(function(){
  const names=[...new Set(R.flatMap(r=>Object.keys(r.tool_mix||{})))];
  const palette=['#58a6ff','#f0883e','#3fb950','#a371f7','#db61a2','#e3b341','#8b949e'];
  const color=n=>palette[names.indexOf(n)%palette.length];
  let s=`<tr><th>run</th><th>mix</th><th class="num">total</th></tr>`;
  s+=R.map(r=>{
    const mix=r.tool_mix||{}, total=Object.values(mix).reduce((a,b)=>a+b,0)||1;
    const bar=Object.entries(mix).sort((a,b)=>b[1]-a[1]).map(([n,c])=>
      `<span title="${n}: ${c}" style="width:${100*c/total}%;background:${color(n)}"></span>`).join('');
    const lbl=Object.entries(mix).sort((a,b)=>b[1]-a[1]).map(([n,c])=>`${n} ${c}`).join(' · ');
    return `<tr><td>${r.label}</td><td><div class="mix">${bar}</div>`+
      `<div style="color:var(--muted);font-size:11px;margin-top:3px">${lbl}</div></td>`+
      `<td class="num">${total}</td></tr>`;
  }).join('');
  document.getElementById('mix').innerHTML=s;
  const legend=names.map(n=>`<span><i class="dot" style="background:${color(n)}"></i>${n}</span>`).join('');
  document.getElementById('mix').insertAdjacentHTML('afterend',`<div class="legend">${legend}</div>`);
})();
</script>
</body>
</html>
"""
