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
    rw = d.get("repeated_work", {}) or {}
    va = d.get("validity_audit", {}) or {}
    return {
        "validity_flags": va.get("n_flags", 0),
        "redundant_calls": rw.get("redundant_calls", 0),
        "redundant_frac": rw.get("redundant_frac"),
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
        ("redun%", None, lambda r: _fmt(r["redundant_frac"])),
        ("expl%", None, lambda r: _fmt(r["explore_share"])),
        ("sep", None, lambda r: _fmt(r["separation"])),
        ("pure", None, lambda r: _fmt(r["purity"])),
        ("cache%", None, lambda r: _fmt(r["cache_read_share"])),
        ("net", None, lambda r: r["net_total"] or "—"),
        ("flags", None, lambda r: r.get("validity_flags", 0) or "—"),
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
  :root { color-scheme:dark;
    --page:#0a0a0c; --panel:#141419; --panel-2:#191920; --line:rgba(255,255,255,.08);
    --line-2:rgba(255,255,255,.16); --ink:#f5f5f7; --ink-2:#b9bac2; --muted:#8f9099;
    --explore:#3987e5; --execute:#d95926; --error:#d03b3b; --violet:#9085e9;
    --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
    --ease:cubic-bezier(.22,.8,.24,1); }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--page); color:var(--ink);
    font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased; }
  header { padding:52px 36px 34px; border-bottom:1px solid var(--line);
    background:radial-gradient(1000px 380px at 12% -20%, rgba(57,135,229,.13), transparent 60%),
               radial-gradient(800px 340px at 92% 130%, rgba(217,89,38,.09), transparent 60%),
               var(--page); }
  .eyebrow { font:600 11px/1 var(--mono); letter-spacing:.32em; color:var(--muted);
    text-transform:uppercase; margin:0 0 14px; }
  .eyebrow b { color:var(--explore); font-weight:600; }
  h1 { margin:0; font-weight:800; letter-spacing:-.035em; line-height:.96;
    font-size:clamp(34px, 6.5vw, 76px); text-transform:uppercase; }
  h1 .dim { color:var(--muted); font-weight:200; }
  .sub { color:var(--muted); font:12px/1.7 var(--mono); margin-top:14px; }
  main { padding:32px 36px 80px; max-width:1240px; margin:0 auto; }
  section { background:var(--panel); border:1px solid var(--line); border-radius:14px;
    padding:24px 26px; margin-bottom:26px; opacity:0; transform:translateY(14px);
    animation:rev .7s var(--ease) forwards; }
  section:nth-of-type(2){ animation-delay:.12s } section:nth-of-type(3){ animation-delay:.24s }
  @keyframes rev { to { opacity:1; transform:none; } }
  h2 { margin:0 0 4px; font-size:13px; font-weight:700; letter-spacing:.22em;
    text-transform:uppercase; }
  h2::before { content:""; display:inline-block; width:8px; height:8px; border-radius:2px;
    margin-right:10px; vertical-align:1px;
    background:linear-gradient(135deg, var(--explore), var(--execute)); }
  .hint { color:var(--muted); font-size:12.5px; margin:6px 0 16px; max-width:88ch; }
  .well { overflow-x:auto; border:1px solid var(--line); border-radius:10px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line);
    white-space:nowrap; }
  thead th { position:sticky; top:0; background:var(--panel-2); color:var(--muted);
    font:600 10.5px var(--mono); letter-spacing:.14em; text-transform:uppercase;
    cursor:pointer; user-select:none; transition:color .2s; }
  thead th:hover { color:var(--ink); }
  thead th .arr { opacity:.6; font-size:9px; }
  tbody tr { transition:background .15s; }
  tbody tr:hover { background:rgba(255,255,255,.03); }
  tbody tr:last-child td { border-bottom:none; }
  td.num,th.num { text-align:right; font-variant-numeric:tabular-nums; }
  td.flag { color:var(--error); font-weight:700; }
  .pbar { display:inline-block; width:52px; height:5px; border-radius:3px;
    background:var(--panel-2); vertical-align:2px; margin-left:8px; overflow:hidden; }
  .pbar i { display:block; height:100%;
    background:linear-gradient(90deg, var(--explore), var(--execute)); }
  .seqrow { margin:14px 0; }
  .seqcap { color:var(--muted); font:12px var(--mono); margin-bottom:6px; }
  .seqcap b { color:var(--ink); font-weight:600; }
  .ribbon { display:flex; flex-wrap:wrap; gap:2.5px; }
  .ribbon i { width:13px; height:26px; border-radius:2.5px; opacity:0;
    transform:translateY(8px); }
  .ribbon i.E { background:var(--explore); }
  .ribbon i.X { background:var(--execute); }
  .ribbon i.xo { width:3px; background:var(--ink); height:32px; margin-top:-3px; }
  @keyframes rib { to { opacity:.95; transform:none; } }
  .mix { display:flex; height:16px; border-radius:4px; overflow:hidden; min-width:140px;
    gap:1px; background:var(--page); }
  .mix span { display:block; }
  .legend { display:flex; flex-wrap:wrap; gap:6px; font-size:12px; color:var(--muted);
    margin-top:12px; }
  .chip { display:inline-flex; align-items:center; gap:7px; padding:4px 11px;
    border:1px solid var(--line); border-radius:999px; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:3px; }
  code { font-family:var(--mono); font-size:12px; color:var(--ink-2); }
  footer { text-align:center; color:var(--muted); font:11px var(--mono);
    letter-spacing:.2em; text-transform:uppercase; padding:0 0 40px; }
  @media (prefers-reduced-motion: reduce) {
    *,*::before,*::after { animation-duration:.001s !important;
      transition-duration:.001s !important; }
    section,.ribbon i { opacity:1; transform:none; } }
</style>
</head>
<body>
<header>
  <p class="eyebrow"><b>&#9679;</b> Claude Code · Workload Traces</p>
  <h1><span class="dim">Run</span> Comparison</h1>
  <div class="sub" id="meta"></div>
</header>
<main>
  <section>
    <h2>Cross-run metrics</h2>
    <p class="hint">One row per run — click a column header to sort. <b>sep</b> =
      execute_pos − explore_pos: higher means a cleaner explore→execute phase shift;
      near zero means explore and execute interleave (an explore/act loop).
      <b>cache%</b> = cache-read share of read tokens — the KV-cache-reuse /
      decode-dominated signal.</p>
    <div class="well"><table id="tbl"></table></div>
  </section>

  <section>
    <h2>Phase sequence per run</h2>
    <p class="hint">Each block is one phased tool call in order — front of the run
      on the left; the white tick marks the explore→execute crossover. Watch whether
      <span style="color:var(--explore)">explore</span> front-loads before
      <span style="color:var(--execute)">execute</span>.</p>
    <div id="seqs"></div>
    <div class="legend">
      <span class="chip"><i class="dot" style="background:var(--explore)"></i>explore / read</span>
      <span class="chip"><i class="dot" style="background:var(--execute)"></i>execute / write</span>
    </div>
  </section>

  <section>
    <h2>Tool mix per run</h2>
    <p class="hint">Relative share of tool calls by tool — search/read-heavy vs.
      Bash/edit-heavy.</p>
    <table id="mix"></table>
  </section>
</main>
<footer>cc_trace · offline · self-contained</footer>

<script id="data" type="application/json">__DATA__</script>
<script>
const R = JSON.parse(document.getElementById('data').textContent);
const esc = x => String(x==null?'':x).replace(/&/g,'&amp;').replace(/</g,'&lt;');
const fmt = (v,nd=2)=> v==null ? '—' : (typeof v==='number' ? (Number.isInteger(v)?v:v.toFixed(nd)) : v);
document.getElementById('meta').textContent = `${R.length} run${R.length===1?'':'s'} compared`;

// ---- metrics table (sortable) ----
const COLS=[['run','label'],['calls','n_tool_calls'],['turns','n_turns'],
  ['dur(s)','duration'],['cost$','cost'],['err','n_errors'],['loops','n_retry_loops'],
  ['redun%','redundant_frac'],
  ['expl%','explore_share'],['sep','separation'],['pure','purity'],
  ['cache%','cache_read_share'],['net','net_total'],['flags','validity_flags'],
  ['top tool','top_tool']];
let sortK=null, sortDir=1;
function drawTbl(){
  let rows=[...R];
  if(sortK) rows.sort((a,b)=>{ const x=a[sortK], y=b[sortK];
    if(x==null) return 1; if(y==null) return -1;
    return (typeof x==='number' ? x-y : String(x).localeCompare(String(y)))*sortDir; });
  let s=`<thead><tr>${COLS.map(([h,k],i)=>
    `<th class="${i?'num':''}" data-k="${k}">${h}${sortK===k?` <span class="arr">${sortDir>0?'▲':'▼'}</span>`:''}</th>`).join('')}</tr></thead><tbody>`;
  s+=rows.map(r=>`<tr>${COLS.map(([h,k],i)=>{
    let v=r[k];
    if(k==='duration') v=v.toFixed(0);
    else if(k==='cost') v=v.toFixed(2);
    else if(k==='purity' && v!=null)
      v=`${v.toFixed(2)}<span class="pbar"><i style="width:${100*Math.max(0,Math.min(1,r[k]))}%"></i></span>`;
    else v=esc(fmt(v));
    const cls=(i?'num':'')+((k==='validity_flags'&&r[k]>0)?' flag':'');
    return `<td class="${cls}">${v}</td>`;
  }).join('')}</tr>`).join('');
  document.getElementById('tbl').innerHTML=s+'</tbody>';
  document.querySelectorAll('#tbl thead th').forEach(th=>
    th.addEventListener('click',()=>{ const k=th.dataset.k;
      sortDir = (sortK===k)? -sortDir : (k==='label'?1:-1); sortK=k; drawTbl(); }));
}
drawTbl();

// ---- phase sequences as animated ribbons ----
(function(){
  document.getElementById('seqs').innerHTML = R.map((r,ri)=>{
    const raw=r.sequence||'', k=r.crossover_index;
    const cells=raw.split('').map((ch,i)=>
      (k!=null && i===k ? `<i class="xo" style="animation:rib .45s var(--ease) ${ri*120+i*28}ms forwards"></i>` : '')
      + `<i class="${ch}" title="#${i+1} ${ch==='E'?'explore':'execute'}" style="animation:rib .45s var(--ease) ${ri*120+i*28}ms forwards"></i>`).join('')
      + (k!=null && k===raw.length ? `<i class="xo" style="animation:rib .45s var(--ease) ${ri*120+raw.length*28}ms forwards"></i>` : '');
    return `<div class="seqrow"><div class="seqcap"><b>${esc(r.label)}</b> `+
      `· sep ${fmt(r.separation)} · purity ${fmt(r.purity)} · n=${raw.length}</div>`+
      `<div class="ribbon">${cells||'(no phased calls)'}</div></div>`;
  }).join('');
})();

// ---- tool mix ----
(function(){
  const names=[...new Set(R.flatMap(r=>Object.keys(r.tool_mix||{})))];
  const palette=['#3987e5','#d95926','#199e70','#9085e9','#c98500','#d55181','#8f9099'];
  const color=n=>palette[names.indexOf(n)%palette.length];
  let s=`<thead><tr><th>run</th><th>mix</th><th class="num">total</th></tr></thead><tbody>`;
  s+=R.map(r=>{
    const mix=r.tool_mix||{}, total=Object.values(mix).reduce((a,b)=>a+b,0)||1;
    const bar=Object.entries(mix).sort((a,b)=>b[1]-a[1]).map(([n,c])=>
      `<span title="${esc(n)}: ${c}" style="width:${100*c/total}%;background:${color(n)}"></span>`).join('');
    const lbl=Object.entries(mix).sort((a,b)=>b[1]-a[1]).map(([n,c])=>`${esc(n)} ${c}`).join(' · ');
    return `<tr><td>${esc(r.label)}</td><td><div class="mix">${bar}</div>`+
      `<div style="color:var(--muted);font-size:11px;margin-top:4px">${lbl}</div></td>`+
      `<td class="num">${total}</td></tr>`;
  }).join('');
  document.getElementById('mix').innerHTML=s+'</tbody>';
  const legend=names.map(n=>`<span class="chip"><i class="dot" style="background:${color(n)}"></i>${esc(n)}</span>`).join('');
  document.getElementById('mix').insertAdjacentHTML('afterend',`<div class="legend">${legend}</div>`);
})();
</script>
</body>
</html>
"""
