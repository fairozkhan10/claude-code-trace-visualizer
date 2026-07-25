"""Flame-graph / folded-stack export for Claude Code runs.

A CPU profiler stacks *call frames*; AgentSight's `agentpprof` stacks *semantic
intent* (debug / review / code).  `cc_trace` stacks each tool call as

    phase ; tool ; target

so the flame graph is partitioned and **coloured by the explore→execute phase**
that is this project's whole axis — you can *see* a refactor front-load (a wide
blue `explore` base) versus a long debug interleave (blue and orange shredded
together) at a glance, which no other agent flamegraph shows.

Output is either:
  * **folded-stack text** (`.folded`) — the universal flame-graph interchange
    format; feed it to speedscope, flamegraph.pl / inferno, etc.; or
  * a **self-contained interactive HTML** flame graph (no deps, offline) — click
    a frame to zoom, hover for the full path + share.

Views (what each frame's *width* means), mirroring agentpprof plus our own:
  * ``calls``   — number of tool calls (default)
  * ``time``    — wall-clock seconds spent in the tool call
  * ``tokens``  — output tokens, attributed from each turn across its tool calls
  * ``files``   — file-operation count (one sample per path touched)
  * ``net``     — network-operation count (one sample per request)

Several transcripts can be aggregated into one graph (a ``run`` frame is added
as the root) — the cross-run view that becomes useful at benchmark volume.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Iterable

from .parser import _bash_signature

VIEWS = ("calls", "time", "tokens", "files", "net")

_PHASE_COLOR = {"explore": "#3987e5", "execute": "#d95926", "other": "#6c6d75"}


def _target(tc) -> str:
    """A compact, clustering-friendly leaf label for one tool call."""
    if tc.name == "Bash":
        sig = _bash_signature(tc.label)[len("Bash:"):].strip()
        return (sig[:48] + "…") if len(sig) > 49 else (sig or "bash")
    if tc.files:
        return tc.files[0]
    lbl = (tc.label or tc.name).replace("\n", " ").strip()
    return (lbl[:48] + "…") if len(lbl) > 49 else (lbl or tc.name)


def folded(traces: list, view: str = "calls") -> dict[str, float]:
    """Build ``{"frame;frame;…": value}`` folded stacks for ``view``."""
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}; pick one of {', '.join(VIEWS)}")
    multi = len(traces) > 1
    stacks: dict[str, float] = {}

    def add(prefix: str, value: float) -> None:
        if value:
            stacks[prefix] = stacks.get(prefix, 0.0) + value

    for trace in traces:
        run = (trace.session_id or "run")[:8]
        root = f"{run};" if multi else ""

        # per-turn output tokens, shared across the calls that turn issued
        out_by_turn = {t.index: t.output_tokens for t in trace.turns}
        calls_by_turn: dict[int, int] = {}
        for tc in trace.tool_calls:
            calls_by_turn[tc.turn] = calls_by_turn.get(tc.turn, 0) + 1

        for tc in trace.tool_calls:
            base = f"{root}{tc.phase};{tc.name}"
            tgt = _target(tc)
            if view == "calls":
                add(f"{base};{tgt}", 1)
            elif view == "time":
                add(f"{base};{tgt}", round(tc.duration or 0.0, 3))
            elif view == "tokens":
                n = calls_by_turn.get(tc.turn, 1) or 1
                add(f"{base};{tgt}", out_by_turn.get(tc.turn, 0) / n)
            elif view == "files":
                for f in tc.files:
                    mode = (tc.file_modes or {}).get(
                        f, "read" if tc.phase == "explore" else "write")
                    add(f"{root}{tc.phase};{tc.name};{mode};{f}", 1)
            elif view == "net":
                for op in tc.network:
                    add(f"{root}{tc.phase};{tc.name};{op['kind']};{op['target']}", 1)

        # tokens emitted by turns that issued no tool call would vanish otherwise
        if view == "tokens":
            for t in trace.turns:
                if calls_by_turn.get(t.index, 0) == 0 and t.output_tokens:
                    add(f"{root}other;message", t.output_tokens)

    return stacks


def render_folded(stacks: dict[str, float]) -> str:
    """Folded-stack text: one ``frame;frame  value`` line per stack, sorted."""
    lines = []
    for stack, value in sorted(stacks.items()):
        v = int(round(value)) if abs(value - round(value)) < 1e-9 else round(value, 3)
        lines.append(f"{stack} {v}")
    return "\n".join(lines) + "\n"


def _tree(stacks: dict[str, float]) -> dict:
    """Fold the stacks into a nested {name,value,phase,children[]} tree."""
    root = {"name": "all", "value": 0.0, "phase": "other", "children": {}}
    for stack, value in stacks.items():
        node = root
        node["value"] += value
        phase = "other"
        for i, frame in enumerate(stack.split(";")):
            if frame in _PHASE_COLOR:
                phase = frame
            child = node["children"].get(frame)
            if child is None:
                child = {"name": frame, "value": 0.0, "phase": phase, "children": {}}
                node["children"][frame] = child
            child["value"] += value
            node = child

    def finish(n: dict) -> dict:
        kids = sorted((finish(c) for c in n["children"].values()),
                      key=lambda c: -c["value"])
        return {"name": n["name"], "value": round(n["value"], 3),
                "phase": n["phase"], "children": kids}

    return finish(root)


def render_html(traces: list, view: str = "calls", title: str | None = None) -> str:
    """A self-contained, dependency-free interactive flame graph."""
    stacks = folded(traces, view)
    tree = _tree(stacks)
    runs = ", ".join((t.session_id or "?")[:8] for t in traces)
    unit = {"calls": "calls", "time": "s", "tokens": "out-tok",
            "files": "ops", "net": "reqs"}[view]
    title = title or f"cc_trace flame · {view}"
    data = json.dumps(tree, separators=(",", ":"))
    colors = json.dumps(_PHASE_COLOR)
    return _HTML.replace("__TITLE__", html.escape(title)) \
                .replace("__SUB__", html.escape(f"{len(traces)} run(s): {runs}  ·  "
                                                 f"width = {view} ({unit})  ·  "
                                                 f"colour = phase")) \
                .replace("__UNIT__", html.escape(unit)) \
                .replace("__COLORS__", colors) \
                .replace("__DATA__", data)


_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
 :root{color-scheme:dark;--page:#0a0a0c;--panel:#141419;--line:rgba(255,255,255,.08);
   --line-2:rgba(255,255,255,.16);--ink:#f5f5f7;--muted:#8f9099;
   --explore:#3987e5;--execute:#d95926;--other:#6c6d75;
   --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
   --ease:cubic-bezier(.22,.8,.24,1)}
 *{box-sizing:border-box}
 body{margin:0;background:var(--page);color:var(--ink);
   font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
   -webkit-font-smoothing:antialiased}
 header{padding:38px 32px 24px;border-bottom:1px solid var(--line);
   background:radial-gradient(900px 320px at 10% -20%,rgba(57,135,229,.13),transparent 60%),
              radial-gradient(700px 300px at 95% 130%,rgba(217,89,38,.09),transparent 60%),var(--page)}
 .eyebrow{font:600 11px/1 var(--mono);letter-spacing:.32em;color:var(--muted);
   text-transform:uppercase;margin:0 0 12px}
 .eyebrow b{color:var(--explore);font-weight:600}
 h1{margin:0;font-weight:800;letter-spacing:-.03em;line-height:1;
   font-size:clamp(26px,4.5vw,44px);text-transform:uppercase}
 .sub{color:var(--muted);font:12px/1.7 var(--mono);margin-top:12px}
 #reset{cursor:pointer;color:var(--explore);margin-left:14px;user-select:none;
   border:1px solid var(--line);border-radius:999px;padding:3px 12px;
   transition:border-color .2s,background .2s}
 #reset:hover{border-color:var(--line-2);background:var(--panel)}
 #crumb{color:var(--ink);font-weight:600}
 #legend{margin-top:14px;font-size:12px;color:var(--muted);display:flex;gap:6px;
   align-items:center;flex-wrap:wrap}
 .chip{display:inline-flex;align-items:center;gap:7px;padding:4px 11px;
   border:1px solid var(--line);border-radius:999px}
 .swatch{display:inline-block;width:10px;height:10px;border-radius:3px}
 #chart{padding:18px 24px 60px}
 .frame{position:absolute;height:19px;box-sizing:border-box;border-radius:3px;
   overflow:hidden;white-space:nowrap;font:600 11px/1.5 var(--mono);color:#0a0a0c;
   padding:2px 5px;cursor:pointer;box-shadow:inset 0 0 0 1px rgba(10,10,12,.55);
   transition:left .5s var(--ease),width .5s var(--ease),top .5s var(--ease),
     filter .15s;animation:fin .45s var(--ease) both}
 @keyframes fin{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
 .frame:hover{filter:brightness(1.25) saturate(1.1);z-index:2}
 #tip{position:fixed;pointer-events:none;background:rgba(14,14,18,.94);
   border:1px solid var(--line-2);border-radius:9px;padding:8px 11px;font-size:12px;
   display:none;max-width:60ch;z-index:9;box-shadow:0 10px 34px rgba(0,0,0,.55);
   backdrop-filter:blur(8px)}
 @media (prefers-reduced-motion:reduce){*{transition-duration:.001s!important}
   .frame{opacity:1}}
</style></head><body>
<header><p class="eyebrow"><b>&#9679;</b> cc_trace &middot; flame</p>
<h1>__TITLE__</h1>
<div class="sub">__SUB__<span id="crumb"></span><span id="reset">&#10226; reset zoom</span></div>
<div id="legend"><span style="margin-right:4px">phase</span>
 <span class="chip"><i class="swatch" style="background:var(--explore)"></i>explore</span>
 <span class="chip"><i class="swatch" style="background:var(--execute)"></i>execute</span>
 <span class="chip"><i class="swatch" style="background:var(--other)"></i>other</span>
 <span style="margin-left:8px">click a frame to zoom</span></div>
</header>
<div id="chart"></div><div id="tip"></div>
<script>
const DATA=__DATA__, COLORS=__COLORS__, UNIT="__UNIT__", ROWH=21;
const chart=document.getElementById('chart'), tip=document.getElementById('tip');
const crumb=document.getElementById('crumb');
const REDUCED=matchMedia('(prefers-reduced-motion: reduce)').matches;
function flatten(node,depth,x0,total,out){
  const w=node.value/total;
  out.push({node,depth,x:x0,w});
  let cx=x0;
  for(const c of node.children){ flatten(c,depth+1,cx,total,out); cx+=c.value/total; }
}
function draw(root){
  chart.innerHTML='';
  crumb.textContent = root===DATA ? '' : (' ▸ zoomed: '+root.name+'  ');
  const rows=[]; flatten(root,0,0,root.value,rows);
  const maxd=Math.max(...rows.map(r=>r.depth));
  chart.style.position='relative'; chart.style.height=((maxd+1)*ROWH+4)+'px';
  const W=chart.clientWidth-48;
  rows.forEach((r,i)=>{
    if(r.w*W<0.4) return;
    const d=document.createElement('div'); d.className='frame';
    d.style.left=(r.x*W)+'px'; d.style.width=Math.max(1,r.w*W-1.5)+'px';
    d.style.top=(r.depth*ROWH)+'px';
    d.style.background=COLORS[r.node.phase]||COLORS.other;
    d.textContent=r.node.name;
    const pct=(100*r.node.value/DATA.value).toFixed(1);
    d.onmousemove=e=>{tip.style.display='block';
      tip.style.left=Math.min(e.clientX+12,innerWidth-320)+'px';
      tip.style.top=(e.clientY+12)+'px';
      tip.innerHTML='<b>'+d.textContent.replace(/&/g,'&amp;').replace(/</g,'&lt;')+
        '</b><br>'+r.node.value+' '+UNIT+' &middot; '+pct+'% of total';};
    d.onmouseleave=()=>tip.style.display='none';
    d.onclick=()=>draw(r.node);
    if(!REDUCED) d.style.animationDelay=Math.min(r.depth*70+i*4,900)+'ms';
    chart.appendChild(d);
  });
}
document.getElementById('reset').onclick=()=>draw(DATA);
draw(DATA); window.addEventListener('resize',()=>draw(DATA));
</script></body></html>"""
