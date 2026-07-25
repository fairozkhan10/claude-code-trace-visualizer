"""Render a :class:`~cc_trace.parser.Trace` into a self-contained HTML report.

No external dependencies or CDNs: the trace is embedded as JSON and all charts
are drawn with inline SVG/JS, so the report opens offline straight from disk.

Design system ("flight-recorder cinema"): dark cinematic ground, editorial
display typography, the run's explore→execute sequence as the hero visual.
Phase colors are the project-wide identity — explore #3987e5 (blue), execute
#d95926 (orange) — a categorical pair validated for CVD separation and 3:1
contrast on the panel surface (see fable5-prompts.md provenance: dataviz
validator, dark mode, surface #141419).
"""

from __future__ import annotations

import json
from .parser import Trace

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Code Trace — __SESSION__</title>
<style>
  :root {
    color-scheme: dark;
    --page:#0a0a0c; --panel:#141419; --panel-2:#191920; --line:rgba(255,255,255,.08);
    --line-2:rgba(255,255,255,.14); --ink:#f5f5f7; --ink-2:#b9bac2; --muted:#8f9099;
    --explore:#3987e5; --execute:#d95926; --other:#6c6d75; --error:#d03b3b;
    --aqua:#199e70; --violet:#9085e9; --yellow:#c98500; --magenta:#d55181;
    --good:#0ca30c;
    --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
    --ease:cubic-bezier(.22,.8,.24,1);
  }
  * { box-sizing:border-box; }
  html { scroll-behavior:smooth; scroll-padding-top:64px; }
  body { margin:0; background:var(--page); color:var(--ink);
         font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         -webkit-font-smoothing:antialiased; }

  /* ---------- hero ---------- */
  .hero { position:relative; padding:64px 40px 48px; overflow:hidden;
          border-bottom:1px solid var(--line);
          background:
            radial-gradient(1200px 500px at 15% -10%, rgba(57,135,229,.14), transparent 60%),
            radial-gradient(1000px 460px at 90% 110%, rgba(217,89,38,.10), transparent 60%),
            var(--page); }
  .hero .grid-bg { position:absolute; inset:0; pointer-events:none; opacity:.5;
    background-image:linear-gradient(var(--line) 1px, transparent 1px),
                     linear-gradient(90deg, var(--line) 1px, transparent 1px);
    background-size:56px 56px;
    -webkit-mask-image:radial-gradient(70% 90% at 50% 10%, #000 0%, transparent 100%);
            mask-image:radial-gradient(70% 90% at 50% 10%, #000 0%, transparent 100%); }
  .eyebrow { font:600 12px/1 var(--mono); letter-spacing:.34em; color:var(--muted);
             text-transform:uppercase; margin:0 0 18px; }
  .eyebrow b { color:var(--explore); font-weight:600; }
  .hero h1 { margin:0; font-weight:800; letter-spacing:-.035em; line-height:.94;
             font-size:clamp(44px, 8.5vw, 108px); text-transform:uppercase; }
  .hero h1 .dim { color:var(--muted); font-weight:200; }
  .hero-row { position:relative; display:flex; flex-wrap:wrap; gap:40px;
              align-items:flex-end; justify-content:space-between; }
  .hero-meta { margin-top:22px; font:12px/1.9 var(--mono); color:var(--ink-2);
               max-width:720px; overflow-wrap:anywhere; }
  .hero-meta b { color:var(--ink); font-weight:600; }

  /* the run's DNA: phase ribbon */
  .ribbon { display:flex; flex-wrap:wrap; gap:3px; margin-top:30px; max-width:900px; }
  .ribbon i { width:16px; height:34px; border-radius:3px; opacity:0;
              transform:translateY(10px); }
  .ribbon i.E { background:var(--explore); }
  .ribbon i.X { background:var(--execute); }
  .ribbon i.xo { width:3px; background:var(--ink); height:42px; margin-top:-4px; }
  .ribbon-cap { font:11px var(--mono); color:var(--muted); letter-spacing:.14em;
                text-transform:uppercase; margin-top:10px; }
  @keyframes rib { to { opacity:.95; transform:none; } }

  /* purity gauge */
  .gauge { text-align:center; min-width:170px; }
  .gauge svg { width:150px; height:150px; display:block; margin:0 auto; }
  .gauge .val { font:800 34px/1 var(--mono); letter-spacing:-.02em; fill:var(--ink); }
  .gauge .cap { font:600 10px var(--mono); letter-spacing:.3em; fill:var(--muted); }
  .gauge .track { stroke:var(--line-2); }
  .gauge .arc { stroke:url(#pgrad); stroke-dasharray:1; stroke-dashoffset:1;
                transition:stroke-dashoffset 1.4s var(--ease) .35s; }
  .gauge .sub { font:11px var(--mono); color:var(--muted); letter-spacing:.14em;
                text-transform:uppercase; margin-top:6px; }

  /* ---------- sticky nav ---------- */
  nav { position:sticky; top:0; z-index:20; display:flex; gap:2px; align-items:center;
        padding:0 28px; height:48px; background:rgba(10,10,12,.82);
        backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
        border-bottom:1px solid var(--line); overflow-x:auto; scrollbar-width:none; }
  nav::-webkit-scrollbar { display:none; }
  nav .brand { font:700 12px var(--mono); letter-spacing:.22em; color:var(--ink);
               margin-right:14px; white-space:nowrap; }
  nav a { color:var(--muted); text-decoration:none; font:12px/1 var(--mono);
          padding:8px 10px; border-radius:6px; white-space:nowrap;
          transition:color .2s, background .2s; }
  nav a:hover { color:var(--ink); background:var(--panel-2); }
  #progress { position:fixed; top:0; left:0; height:2px; width:0; z-index:30;
              background:linear-gradient(90deg, var(--explore), var(--execute)); }

  /* ---------- layout ---------- */
  main { padding:36px 40px 90px; max-width:1240px; margin:0 auto; }
  section { background:var(--panel); border:1px solid var(--line); border-radius:14px;
            padding:24px 26px; margin-bottom:26px; }
  .rev { transition:opacity .7s var(--ease), transform .7s var(--ease); }
  .rev.pre { opacity:0; transform:translateY(14px); }
  h2 { margin:0 0 4px; font-size:13px; font-weight:700; letter-spacing:.22em;
       text-transform:uppercase; color:var(--ink); }
  h2::before { content:""; display:inline-block; width:8px; height:8px; border-radius:2px;
               margin-right:10px; vertical-align:1px;
               background:linear-gradient(135deg, var(--explore), var(--execute)); }
  .hint { color:var(--muted); font-size:12.5px; margin:6px 0 16px; max-width:88ch; }
  svg { width:100%; display:block; }
  .legend { display:flex; flex-wrap:wrap; gap:6px; font-size:12px; color:var(--muted);
            margin-top:12px; }
  .legend .chip { display:inline-flex; align-items:center; gap:7px; padding:5px 11px;
                  border:1px solid var(--line); border-radius:999px; cursor:default;
                  transition:border-color .2s, opacity .2s, background .2s; user-select:none; }
  .legend .chip.click { cursor:pointer; }
  .legend .chip.click:hover { border-color:var(--line-2); background:var(--panel-2); }
  .legend .chip.off { opacity:.35; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:3px; }

  /* ---------- stat tiles ---------- */
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
           gap:12px; margin:0 0 28px; }
  .tile { background:var(--panel); border:1px solid var(--line); border-radius:12px;
          padding:16px 18px 14px; position:relative; overflow:hidden;
          transition:transform .35s var(--ease), border-color .35s; }
  .tile:hover { transform:translateY(-3px); border-color:var(--line-2); }
  .tile::before { content:""; position:absolute; top:0; left:0; right:0; height:2px;
                  background:linear-gradient(90deg, var(--explore), transparent 70%);
                  opacity:.7; }
  .tile .v { font:700 26px/1.15 var(--mono); letter-spacing:-.02em;
             font-variant-numeric:tabular-nums; }
  .tile .k { color:var(--muted); font-size:10.5px; text-transform:uppercase;
             letter-spacing:.18em; margin-top:6px; }
  .tile.warn::before { background:linear-gradient(90deg, var(--error), transparent 70%); }
  .tile.warn .v { color:var(--error); }

  /* ---------- tables ---------- */
  .well { max-height:360px; overflow:auto; border:1px solid var(--line);
          border-radius:10px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); }
  thead th { position:sticky; top:0; background:var(--panel-2); color:var(--muted);
             font:600 10.5px var(--mono); letter-spacing:.16em; text-transform:uppercase;
             z-index:1; }
  tbody tr { transition:background .15s; }
  tbody tr:hover { background:rgba(255,255,255,.03); }
  tbody tr:last-child td { border-bottom:none; }
  td.num,th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .pill { font:600 10.5px var(--mono); letter-spacing:.06em; padding:2px 9px;
          border-radius:999px; border:1px solid var(--line-2); color:var(--ink-2); }
  .pill.err { background:rgba(208,59,59,.14); color:#ef7c7c; border-color:rgba(208,59,59,.4); }
  .pill.ok { background:rgba(12,163,12,.12); color:#4fc24f; border-color:rgba(12,163,12,.35); }
  code { font-family:var(--mono); font-size:12px; color:var(--ink-2); }
  .empty { color:var(--muted); padding:14px 4px; font-size:13px; }
  .empty b { color:#4fc24f; }

  /* ---------- bar rows ---------- */
  .bar-row { display:flex; align-items:center; gap:12px; margin:7px 0; }
  .bar-row .name { width:120px; font-size:13px; }
  .bar-row .track { flex:1; background:var(--panel-2); border-radius:4px; height:16px;
                    overflow:hidden; }
  .bar-row .fill { display:block; height:100%; border-radius:4px;
                   background:linear-gradient(90deg, var(--explore), #5ea2f0);
                   transform-origin:left;
                   transition:transform .9s var(--ease); }
  .pre .bar-row .fill { transform:scaleX(0); }
  .bar-row .cnt { width:110px; text-align:right; color:var(--muted); font-size:12px;
                  font-variant-numeric:tabular-nums; }

  /* ---------- charts ---------- */
  rect.grow { transform-origin:center bottom; transform-box:fill-box;
              transition:transform .8s var(--ease); }
  .pre rect.grow { transform:scaleY(0); }
  #tl rect[data-i] { transform-origin:left center; transform-box:fill-box;
                     transition:transform .6s var(--ease), opacity .3s; }
  .pre #tl rect[data-i] { transform:scaleX(0); }
  #tl rect.dimmed { opacity:.10; }
  polyline.draw { stroke-dasharray:1; stroke-dashoffset:0;
                  transition:stroke-dashoffset 1.5s var(--ease) .15s; }
  .pre polyline.draw { stroke-dashoffset:1; }
  #fg circle { transition:opacity .25s, r .25s; cursor:pointer; }
  #fg line { transition:stroke-opacity .25s; }
  #fg.focus circle:not(.hi) { opacity:.18; }
  #fg.focus line:not(.hi) { stroke-opacity:.04 !important; }
  #fg.focus text:not(.hi) { opacity:.15; }
  @keyframes errPulse { 0%,100% { opacity:.92; } 50% { opacity:.45; } }
  #tl rect.err-bar { animation:errPulse 2.2s ease-in-out infinite; }

  .tip { position:fixed; pointer-events:none; background:rgba(14,14,18,.94);
         border:1px solid var(--line-2); border-radius:9px; padding:8px 11px;
         font-size:12px; max-width:360px; display:none; z-index:40;
         white-space:pre-wrap; word-break:break-word;
         box-shadow:0 10px 34px rgba(0,0,0,.55); backdrop-filter:blur(8px); }

  .xhair { stroke:var(--ink-2); stroke-width:1; stroke-dasharray:3 3; opacity:0;
           pointer-events:none; }

  footer { text-align:center; color:var(--muted); font:11px var(--mono);
           letter-spacing:.2em; text-transform:uppercase; padding:10px 0 40px; }

  @media (prefers-reduced-motion: reduce) {
    html { scroll-behavior:auto; }
    *, *::before, *::after { animation-duration:.001s !important;
      transition-duration:.001s !important; animation-iteration-count:1 !important; }
    .ribbon i { opacity:.95; transform:none; }
  }
</style>
</head>
<body>
<div id="progress"></div>

<header class="hero">
  <div class="grid-bg"></div>
  <div class="hero-row">
    <div style="min-width:0">
      <p class="eyebrow"><b>●</b> Claude Code · Workload Trace</p>
      <h1><span class="dim">Session</span> __SESSION__</h1>
      <div class="ribbon" id="ribbon"></div>
      <div class="ribbon-cap" id="ribbon-cap"></div>
      <div class="hero-meta" id="meta"></div>
    </div>
    <div class="gauge" id="gauge"></div>
  </div>
</header>

<nav>
  <span class="brand">CC_TRACE</span>
  <a href="#s-timeline">Timeline</a><a href="#s-phases">Phases</a>
  <a href="#s-tokens">Tokens</a><a href="#s-tools">Tools</a>
  <a href="#s-net">Network</a><a href="#s-audit">Audit</a>
  <a href="#s-graph">Files</a><a href="#s-retries">Retries</a>
  <a href="#s-repeats">Repeats</a><a href="#s-errors">Errors</a>
</nav>

<main>
  <div class="tiles" id="cards"></div>

  <section id="s-timeline" class="rev">
    <h2>Timeline of tool calls</h2>
    <p class="hint">Each bar is one tool call, placed at its start offset and sized
      by wall-clock duration. Color = phase. Hover for detail; click a legend chip
      to isolate a phase.</p>
    <svg id="timeline"></svg>
    <div class="legend" id="tl-legend">
      <span class="chip click" data-phase="explore"><i class="dot" style="background:var(--explore)"></i>explore / read</span>
      <span class="chip click" data-phase="execute"><i class="dot" style="background:var(--execute)"></i>execute / write</span>
      <span class="chip click" data-phase="other"><i class="dot" style="background:var(--other)"></i>other</span>
      <span class="chip click" data-phase="error"><i class="dot" style="background:var(--error)"></i>error</span>
    </div>
  </section>

  <section id="s-phases" class="rev">
    <h2>Phase progression — read/explore vs. execute/write</h2>
    <p class="hint">Cumulative share of explore vs. execute tool calls across the run.
      The workload paper predicts explore dominates early, execute later.</p>
    <svg id="phases"></svg>
  </section>

  <section id="s-tokens" class="rev chart-anim">
    <h2>Context growth &amp; token usage per turn</h2>
    <p class="hint">Context (cache-read + fresh input) per model turn. Agentic runs are
      decode-dominated with heavy KV-cache reuse — watch the cache-read segment dominate.
      Hover a turn for the exact split.</p>
    <svg id="tokens"></svg>
    <div class="legend">
      <span class="chip"><i class="dot" style="background:var(--violet)"></i>cache read (reused context)</span>
      <span class="chip"><i class="dot" style="background:var(--explore)"></i>fresh input</span>
      <span class="chip"><i class="dot" style="background:var(--execute)"></i>output</span>
    </div>
  </section>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:26px" id="s-tools">
    <section style="margin:0" class="rev">
      <h2>Tool-call breakdown</h2>
      <p class="hint">Count per tool (bar) with total time and error count.</p>
      <div id="breakdown"></div>
    </section>
    <section style="margin:0" class="rev">
      <h2>File access</h2>
      <p class="hint">Files touched, by read vs. write operations.</p>
      <div class="well"><table id="files"></table></div>
    </section>
  </div>

  <section id="s-net" class="rev" style="margin-top:26px">
    <h2>Network activity</h2>
    <p class="hint">Network the agent reached <em>through its tools</em> — curl/wget,
      git remote ops, package installs, ssh/scp, plus WebFetch/WebSearch/MCP. Parsed
      from command strings (best-effort). Does <b>not</b> include Claude Code's own
      model API calls — those never appear in the transcript.</p>
    <div id="net-summary" class="hint" style="margin-bottom:10px"></div>
    <div class="well"><table id="net"></table></div>
  </section>

  <section id="s-audit" class="rev">
    <h2>Benchmark validity audit</h2>
    <p class="hint">Flags the capability-dependent benchmark failure modes from
      FINDINGS finding 11 — <b>solution-channel</b> network (the agent fetching a
      fix's provenance: PR diffs, commit searches), <b>leak exposure</b> (an
      instance id in the fixture path/prompt), and <b>stranded work</b> (unbalanced
      <code>git stash</code>). Flags are cues for human review, not verdicts.</p>
    <div id="audit-summary" class="hint" style="margin-bottom:10px"></div>
    <table id="audit"></table>
  </section>

  <section id="s-graph" class="rev">
    <h2>File co-access graph</h2>
    <p class="hint">Files touched close together in the run are linked; thicker links =
      worked on together more often. Node size = how often a file is accessed,
      color = read- vs. write-dominated. Hover a node to isolate its neighborhood.</p>
    <svg id="filegraph"></svg>
    <div class="legend">
      <span class="chip"><i class="dot" style="background:var(--explore)"></i>read-dominated</span>
      <span class="chip"><i class="dot" style="background:var(--execute)"></i>write-dominated</span>
    </div>
  </section>

  <section id="s-retries" class="rev">
    <h2>Retry loops</h2>
    <p class="hint">Same tool + same target attempted repeatedly with at least one
      failure — where the agent got stuck (a command that keeps failing, an edit redone).</p>
    <table id="retries"></table>
  </section>

  <section id="s-repeats" class="rev">
    <h2>Repeated work</h2>
    <p class="hint" id="repeat-summary"></p>
    <p class="hint">Clusters of identical or <em>near-identical</em> calls — the agent
      re-running the same test, re-reading the same file, re-issuing a slightly varied
      command. Each is a candidate a cache/memoiser could collapse. "near" = signature
      match after numbers/strings/paths are normalised away.</p>
    <table id="repeats"></table>
  </section>

  <section id="s-errors" class="rev">
    <h2>Errors &amp; retries</h2>
    <p class="hint">Tool calls that returned an error (candidate retry loops).</p>
    <table id="errors"></table>
  </section>
</main>
<footer>cc_trace · offline · self-contained</footer>
<div class="tip" id="tip"></div>

<script id="data" type="application/json">__DATA__</script>
<script>
const T = JSON.parse(document.getElementById('data').textContent);
const tip = document.getElementById('tip');
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const fmtDur = s => s==null ? '—' : (s<1 ? (s*1000|0)+'ms' : s<90 ? s.toFixed(1)+'s'
                   : (s/60|0)+'m '+Math.round(s%60)+'s');
const fmtN = n => n.toLocaleString();
const esc = x => String(x==null?'':x).replace(/&/g,'&amp;').replace(/</g,'&lt;');
const phaseColor = p => p==='explore'?'#3987e5':p==='execute'?'#d95926':'#6c6d75';
function showTip(e,html){ tip.innerHTML=html; tip.style.display='block';
  tip.style.left=Math.min(e.clientX+14,innerWidth-380)+'px';
  tip.style.top=Math.min(e.clientY+14,innerHeight-140)+'px'; }
function hideTip(){ tip.style.display='none'; }

// ---- scroll progress + section reveal ----
addEventListener('scroll',()=>{ const h=document.documentElement;
  document.getElementById('progress').style.width =
    (100*h.scrollTop/(h.scrollHeight-h.clientHeight||1))+'%'; },{passive:true});
// Scroll-reveal is pure enhancement: content is visible by default; the hidden
// "pre" state is only applied when IntersectionObserver is there to undo it.
if(!REDUCED && 'IntersectionObserver' in window){
  const io=new IntersectionObserver(es=>es.forEach(x=>{
    if(x.isIntersecting){ x.target.classList.remove('pre'); io.unobserve(x.target); }}),
    {threshold:.12});
  document.querySelectorAll('.rev').forEach(s=>{
    s.classList.add('pre');
    if(s.getBoundingClientRect().top < innerHeight){
      requestAnimationFrame(()=>requestAnimationFrame(()=>s.classList.remove('pre')));
    } else io.observe(s);
  });
}

// ---- meta + hero ----
const t0=T.start, dur=T.duration||0;
document.getElementById('meta').innerHTML =
  `<b>cwd</b> ${esc(T.cwd||'—')} · <b>branch</b> ${esc(T.git_branch||'—')}<br>`+
  `<b>models</b> ${esc(T.models.join(', '))} · <b>wall-clock</b> ${fmtDur(dur)}`;

// phase ribbon — the run's DNA
(function(){
  const seq=T.tool_calls.filter(c=>c.phase!=='other')
    .map(c=>c.phase==='explore'?'E':'X');
  const xo=(T.phase_crossover||{}).index;
  const host=document.getElementById('ribbon');
  const cap=document.getElementById('ribbon-cap');
  if(!seq.length){ cap.textContent='no phased tool calls'; return; }
  let html='';
  seq.forEach((ch,i)=>{
    if(xo!=null && i===xo) html+='<i class="xo" style="animation:rib .5s var(--ease) '+(i*35)+'ms forwards"></i>';
    html+=`<i class="${ch}" title="#${i+1} ${ch==='E'?'explore':'execute'}"
      style="animation:rib .5s var(--ease) ${i*35}ms forwards"></i>`;
  });
  if(xo!=null && xo===seq.length) html+='<i class="xo" style="animation:rib .5s var(--ease) '+(seq.length*35)+'ms forwards"></i>';
  host.innerHTML=html;
  cap.textContent=`phase sequence · ${seq.length} phased calls`+
    (xo!=null?` · │ marks the explore→execute crossover`:``);
})();

// purity gauge
(function(){
  const xo=T.phase_crossover||{}, p=xo.purity;
  const host=document.getElementById('gauge');
  if(p==null){ host.innerHTML=''; return; }
  const R=62, C=2*Math.PI*R;
  host.innerHTML=`<svg viewBox="0 0 150 150">
    <defs><linearGradient id="pgrad" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="#3987e5"/><stop offset="1" stop-color="#d95926"/>
    </linearGradient></defs>
    <circle class="track" cx="75" cy="75" r="${R}" fill="none" stroke-width="9"/>
    <circle class="arc" cx="75" cy="75" r="${R}" fill="none" stroke-width="9"
      stroke-linecap="round" pathLength="1" transform="rotate(-90 75 75)"/>
    <text class="val" x="75" y="80" text-anchor="middle">${p.toFixed(2)}</text>
    <text class="cap" x="75" y="99" text-anchor="middle">PURITY</text>
  </svg><div class="sub">phase-shift cleanliness</div>`;
  requestAnimationFrame(()=>requestAnimationFrame(()=>{
    host.querySelector('.arc').style.strokeDashoffset = String(1-Math.max(0,Math.min(1,p)));
  }));
})();

// ---- stat tiles with count-up ----
const tt=T.token_totals;
const tiles=[
  ['Wall-clock', dur, v=>fmtDur(v)],
  ['Tool calls', T.n_tool_calls, v=>fmtN(Math.round(v))],
  ['Model turns', T.n_turns, v=>fmtN(Math.round(v))],
  ['Errors', T.n_errors, v=>fmtN(Math.round(v)), T.n_errors>0],
  ['Retry loops', (T.retry_loops||[]).length, v=>fmtN(Math.round(v)),
    (T.retry_loops||[]).length>0],
  ['Output tokens', tt.output, v=>fmtN(Math.round(v))],
  ['Cache-read tokens', tt.cache_read, v=>fmtN(Math.round(v))],
  ['Est. cost', T.total_cost, v=>'$'+v.toFixed(3)],
];
document.getElementById('cards').innerHTML = tiles.map(([k,,f,warn],i)=>
  `<div class="tile${warn?' warn':''}"><div class="v" data-t="${i}">${f(0)}</div>
   <div class="k">${k}</div></div>`).join('');
tiles.forEach(([,target,f],i)=>{
  const el=document.querySelector(`[data-t="${i}"]`);
  if(REDUCED || !target){ el.textContent=f(target); return; }
  const t1=performance.now(), D=900+i*60;
  (function step(now){ const k=Math.min(1,(now-t1)/D), e=1-Math.pow(1-k,3);
    el.textContent=f(target*e);
    if(k<1) requestAnimationFrame(step); })(t1);
});

// ---- timeline (gantt) with crosshair + phase filter ----
(function(){
  const calls=T.tool_calls, n=calls.length;
  const rowH=16, pad=6, W=1160, labelW=0;
  const H=n*rowH+pad*2+14;
  const span=Math.max(dur,0.001);
  const x=t=> (t==null? 0 : (t-t0)/span)*(W-labelW-8)+labelW+4;
  let s=`<svg viewBox="0 0 ${W} ${H}" id="tl">`;
  for(let i=0;i<=10;i++){ const gx=labelW+4+(i/10)*(W-labelW-8);
    s+=`<line x1="${gx}" y1="0" x2="${gx}" y2="${H-14}" stroke="rgba(255,255,255,.05)"/>`;
    s+=`<text x="${gx}" y="${H-2}" fill="#8f9099" font-size="9" text-anchor="middle">${(span*i/10).toFixed(0)}s</text>`; }
  calls.forEach((c,i)=>{
    const xs=x(c.start), xe=Math.max(x(c.end), xs+2);
    const col=c.is_error?'#d03b3b':phaseColor(c.phase);
    const y=pad+i*rowH;
    s+=`<rect data-i="${i}" data-ph="${c.is_error?'error':c.phase}" class="${c.is_error?'err-bar':''}"
      x="${xs}" y="${y}" width="${xe-xs}" height="${rowH-3}" rx="2.5" fill="${col}"
      opacity="0.92" style="transition-delay:${Math.min(i*22,900)}ms"/>`;
  });
  s+=`<line id="xh" class="xhair" x1="0" y1="0" x2="0" y2="${H-14}"/>`;
  s+=`<text id="xh-t" fill="#b9bac2" font-size="10" font-family="var(--mono)" opacity="0"></text>`;
  s+=`</svg>`;
  document.getElementById('timeline').outerHTML=s;
  const svg=document.getElementById('tl');
  const xh=document.getElementById('xh'), xht=document.getElementById('xh-t');
  svg.addEventListener('mousemove',e=>{
    const r=svg.getBoundingClientRect(), px=(e.clientX-r.left)/r.width*W;
    xh.setAttribute('x1',px); xh.setAttribute('x2',px); xh.style.opacity=.6;
    xht.setAttribute('x',Math.min(px+6,W-70)); xht.setAttribute('y',12);
    xht.textContent='+'+((px-4)/(W-8)*span).toFixed(1)+'s'; xht.style.opacity=.9; });
  svg.addEventListener('mouseleave',()=>{ xh.style.opacity=0; xht.style.opacity=0; });
  svg.querySelectorAll('rect[data-i]').forEach(rc=>{
    rc.addEventListener('mousemove',e=>{ const c=calls[+rc.dataset.i];
      showTip(e,`<b>${esc(c.name)}</b> <span class="pill ${c.is_error?'err':''}">${c.phase}${c.is_error?' · error':''}</span><br>`+
        `<code>${esc(c.label||'')}</code><br>start +${((c.start-t0)||0).toFixed(1)}s · dur ${fmtDur(c.duration)} · ${fmtN(c.output_chars)} chars out`); });
    rc.addEventListener('mouseleave',hideTip);
  });
  // legend chips filter phases
  let active=null;
  document.querySelectorAll('#tl-legend .chip.click').forEach(ch=>{
    ch.addEventListener('click',()=>{
      const ph=ch.dataset.phase;
      active = (active===ph) ? null : ph;
      document.querySelectorAll('#tl-legend .chip.click').forEach(c2=>
        c2.classList.toggle('off', active!=null && c2.dataset.phase!==active));
      svg.querySelectorAll('rect[data-i]').forEach(rc=>
        rc.classList.toggle('dimmed', active!=null && rc.dataset.ph!==active));
    });
  });
})();

// ---- phase progression (cumulative explore vs execute) ----
(function(){
  const calls=T.tool_calls.filter(c=>c.phase!=='other');
  const W=1160,H=210,m=26;
  if(!calls.length){document.getElementById('phases').outerHTML='<p class="hint">No phased tool calls.</p>';return;}
  let ex=0,exc=0; const pts={explore:[],execute:[]};
  calls.forEach((c,i)=>{ if(c.phase==='explore')ex++; else exc++;
    pts.explore.push([i,ex]); pts.execute.push([i,exc]); });
  const N=calls.length, maxY=Math.max(ex,exc,1);
  const X=i=>m+ (i/(N-1||1))*(W-2*m), Y=v=>H-m-(v/maxY)*(H-2*m);
  const line=(arr,col)=>{
    const p=arr.map(([i,v])=>X(i)+','+Y(v)).join(' ');
    return `<polygon fill="${col}" opacity="0.07" points="${X(arr[0][0])},${H-m} ${p} ${X(arr[arr.length-1][0])},${H-m}"/>`+
      `<polyline class="draw" pathLength="1" fill="none" stroke="${col}" stroke-width="2.5" stroke-linejoin="round" points="${p}"/>`; };
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  s+=`<line x1="${m}" y1="${H-m}" x2="${W-m}" y2="${H-m}" stroke="rgba(255,255,255,.14)"/>`;
  s+=`<line x1="${m}" y1="${m}" x2="${m}" y2="${H-m}" stroke="rgba(255,255,255,.14)"/>`;
  const xo=T.phase_crossover||{};
  if(xo.index!=null && N>1){ const cx=X(Math.min(xo.index, N-1));
    s+=`<line x1="${cx}" y1="${m}" x2="${cx}" y2="${H-m}" stroke="#9085e9" stroke-width="1.5" stroke-dasharray="4 3"/>`;
    s+=`<text x="${cx+5}" y="${m+11}" fill="#9085e9" font-size="10.5" font-family="var(--mono)">crossover ${(100*xo.pos).toFixed(0)}% · purity ${xo.purity}</text>`;
  }
  s+=line(pts.explore,'#3987e5')+line(pts.execute,'#d95926');
  s+=`<text x="${W-m}" y="${Y(ex)-6}" fill="#3987e5" font-size="11.5" font-weight="600" text-anchor="end">explore ${ex}</text>`;
  s+=`<text x="${W-m}" y="${Y(exc)-6}" fill="#d95926" font-size="11.5" font-weight="600" text-anchor="end">execute ${exc}</text>`;
  s+=`<text x="${m}" y="${H-6}" fill="#8f9099" font-size="10">tool call #1</text>`;
  s+=`<text x="${W-m}" y="${H-6}" fill="#8f9099" font-size="10" text-anchor="end">#${N}</text>`;
  s+=`</svg>`;
  document.getElementById('phases').outerHTML=s;
})();

// ---- token usage per turn (stacked bars, animated, hoverable) ----
(function(){
  const turns=T.turns; const W=1160,H=210,m=28;
  if(!turns.length){document.getElementById('tokens').outerHTML='<p class="hint">No turns.</p>';return;}
  const maxY=Math.max(1,...turns.map(t=>t.cache_read_tokens+t.input_tokens+t.output_tokens));
  const bw=(W-2*m)/turns.length;
  const Y=v=>(v/maxY)*(H-2*m);
  let s=`<svg viewBox="0 0 ${W} ${H}" id="tok">`;
  s+=`<line x1="${m}" y1="${H-m}" x2="${W-m}" y2="${H-m}" stroke="rgba(255,255,255,.14)"/>`;
  turns.forEach((t,i)=>{ const x=m+i*bw; let y=H-m;
    const segs=[[t.cache_read_tokens,'#9085e9'],[t.input_tokens,'#3987e5'],[t.output_tokens,'#d95926']];
    segs.forEach(([v,c],si)=>{ const h=Y(v); y-=h;
      const gap = si>0 && h>2 ? 1 : 0;
      s+=`<rect class="grow" x="${x+1}" y="${y+gap}" width="${Math.max(bw-2,1)}" height="${Math.max(h-gap,0)}" fill="${c}" data-i="${i}" style="transition-delay:${Math.min(i*30,900)}ms"/>`; });
  });
  s+=`<text x="${m}" y="${m-10}" fill="#8f9099" font-size="10" font-family="var(--mono)">peak ${fmtN(maxY)} tok</text></svg>`;
  document.getElementById('tokens').outerHTML=s;
  document.querySelectorAll('#tok rect[data-i]').forEach(rc=>{
    rc.addEventListener('mousemove',e=>{ const t=turns[+rc.dataset.i];
      showTip(e,`<b>turn ${+rc.dataset.i+1}</b> · ${esc(t.model||'')}<br>`+
        `cache read ${fmtN(t.cache_read_tokens)} · fresh in ${fmtN(t.input_tokens)} · out ${fmtN(t.output_tokens)}`); });
    rc.addEventListener('mouseleave',hideTip);
  });
})();

// ---- tool breakdown bars ----
(function(){
  const b=T.tool_breakdown, max=Math.max(1,...b.map(x=>x.count));
  document.getElementById('breakdown').innerHTML=b.map((x,i)=>
    `<div class="bar-row"><span class="name">${esc(x.name)}</span>`+
    `<span class="track"><span class="fill" style="width:${100*x.count/max}%;transition-delay:${i*70}ms"></span></span>`+
    `<span class="cnt">${x.count}× · ${fmtDur(x.duration)}${x.errors?` · <span style="color:var(--error)">${x.errors}e</span>`:''}</span></div>`).join('');
})();

// ---- file access ----
(function(){
  const f=T.file_access;
  let s=`<thead><tr><th>File</th><th class="num">reads</th><th class="num">writes</th></tr></thead><tbody>`;
  s+= f.length? f.map(r=>`<tr><td><code>${esc(shorten(r.file))}</code></td><td class="num">${r.reads}</td><td class="num">${r.writes}</td></tr>`).join('')
            : `<tr><td colspan="3" class="empty">No file operations.</td></tr>`;
  document.getElementById('files').innerHTML=s+'</tbody>';
})();

// ---- network activity ----
(function(){
  const na=T.network_activity||{total:0,by_kind:[],requests:[]};
  const kc={http:'#3987e5',api:'#3987e5',git:'#d95926',package:'#9085e9',
    ssh:'#199e70',dns:'#c98500',socket:'#d55181',probe:'#8f9099',
    search:'#199e70',mcp:'#c98500'};
  const col=k=>kc[k]||'#8f9099';
  const sum=document.getElementById('net-summary');
  if(!na.total){ sum.innerHTML='<span class="empty">No agent-initiated network activity detected.</span>';
    document.querySelector('#s-net .well').outerHTML=''; return; }
  sum.innerHTML=`<b style="font:700 18px var(--mono)">${na.total}</b> request${na.total>1?'s':''} &nbsp;`+
    na.by_kind.map(k=>`<span class="pill" style="border-color:${col(k.kind)};color:${col(k.kind)}">`+
      `${k.kind} ${k.count}</span>`).join(' ');
  let s=`<thead><tr><th class="num">#</th><th>kind</th><th>target</th><th>via</th></tr></thead><tbody>`;
  s+= na.requests.map(r=>`<tr><td class="num">${r.index}</td>`+
    `<td><span class="pill" style="border-color:${col(r.kind)};color:${col(r.kind)}">${r.kind}</span></td>`+
    `<td><code>${esc(shorten(r.target||''))}</code>${r.error?' <span class="pill err">err</span>':''}</td>`+
    `<td>${esc(r.tool)}</td></tr>`).join('');
  document.getElementById('net').innerHTML=s+'</tbody>';
})();

// ---- benchmark validity audit ----
(function(){
  const va=T.validity_audit||{flags:[]};
  const sum=document.getElementById('audit-summary');
  const repo=va.repo_under_test
    ? `repo under test: <code>${esc(va.repo_under_test)}</code> (${esc(va.repo_source)})`
    : 'repo under test: not identified — solution-channel flags are unscoped';
  if(!va.flags.length){
    sum.innerHTML=`<span class="pill ok">clean</span> No validity flags. · ${repo}`;
    document.getElementById('audit').outerHTML=''; return; }
  sum.innerHTML=`<b style="font:700 18px var(--mono);color:var(--error)">${va.n_flags}</b> flag${va.n_flags>1?'s':''} · ${repo}`;
  let s=`<thead><tr><th>severity</th><th>kind</th><th>detail</th><th class="num">at</th></tr></thead><tbody>`;
  s+=va.flags.map(f=>`<tr>`+
    `<td><span class="pill ${f.severity==='high'?'err':''}">${f.severity}</span></td>`+
    `<td>${esc(f.kind.replace('_',' '))}</td>`+
    `<td><code>${esc(f.detail||'')}</code></td>`+
    `<td class="num">${f.index==null?'—':'#'+f.index}</td></tr>`).join('');
  document.getElementById('audit').innerHTML=s+'</tbody>';
})();

// ---- retry loops ----
(function(){
  const loops=T.retry_loops||[];
  let s=`<thead><tr><th>Tool</th><th>Target</th><th class="num">attempts</th><th class="num">errors</th><th class="num">span</th><th class="num">at</th></tr></thead><tbody>`;
  s+= loops.length? loops.map(r=>`<tr><td>${esc(r.tool)}</td><td><code>${esc(shorten(r.target||''))}</code></td>`+
        `<td class="num">${r.attempts}</td><td class="num"><span class="pill err">${r.errors}</span></td>`+
        `<td class="num">${fmtDur(r.span_s)}</td><td class="num">#${r.first_index}–${r.last_index}</td></tr>`).join('')
      : `<tr><td colspan="6" class="empty"><b>clean</b> — no retry loops detected.</td></tr>`;
  document.getElementById('retries').innerHTML=s+'</tbody>';
})();

// ---- repeated work ----
(function(){
  const rw=T.repeated_work||{clusters:[]}, cl=rw.clusters||[];
  const pct=rw.redundant_frac!=null?(100*rw.redundant_frac).toFixed(0):'0';
  document.getElementById('repeat-summary').innerHTML = cl.length
    ? `<b>${fmtN(rw.redundant_calls)}</b> of ${fmtN(rw.n_tool_calls)} tool calls (${pct}%) `+
      `were repeats of earlier work, across <b>${fmtN(rw.n_clusters)}</b> clusters — `+
      `${fmtDur(rw.redundant_s)} spent on the repeat invocations.`
    : `<b style="color:#4fc24f">clean</b> — no repeated work detected.`;
  let s=`<thead><tr><th>Tool</th><th>Example</th><th class="num">calls</th><th class="num">redundant</th>`+
        `<th class="num">kind</th><th class="num">errors</th><th class="num">time</th><th class="num">at</th></tr></thead><tbody>`;
  s+= cl.length? cl.map(r=>`<tr><td>${esc(r.tool)}</td>`+
        `<td><code>${esc(shorten(r.example||''))}</code></td>`+
        `<td class="num">${r.count}</td><td class="num"><b>${r.redundant}</b></td>`+
        `<td class="num">${r.exact?'exact':'near ('+r.distinct+')'}</td>`+
        `<td class="num">${r.errors?'<span class="pill err">'+r.errors+'</span>':'—'}</td>`+
        `<td class="num">${fmtDur(r.redundant_s)}</td><td class="num">#${r.first_index}–${r.last_index}</td></tr>`).join('')
      : `<tr><td colspan="8" class="empty"><b>clean</b> — no repeated work detected.</td></tr>`;
  document.getElementById('repeats').innerHTML=s+'</tbody>';
})();

// ---- errors ----
(function(){
  const errs=T.tool_calls.filter(c=>c.is_error);
  let s=`<thead><tr><th>#</th><th>Tool</th><th>Detail</th><th class="num">at</th></tr></thead><tbody>`;
  s+= errs.length? errs.map(c=>`<tr><td>${c.index}</td><td>${esc(c.name)}</td><td><code>${esc(c.label||'')}</code></td><td class="num">+${((c.start-t0)||0).toFixed(1)}s</td></tr>`).join('')
            : `<tr><td colspan="4" class="empty"><b>clean</b> — no tool errors.</td></tr>`;
  document.getElementById('errors').innerHTML=s+'</tbody>';
})();

// ---- file co-access graph (circular layout, hover isolates neighborhood) ----
(function(){
  const g=T.file_graph||{nodes:[],edges:[]}, nodes=g.nodes||[], edges=g.edges||[];
  const host=document.getElementById('filegraph');
  if(nodes.length<2){ host.outerHTML='<p class="hint">Not enough file access to graph.</p>'; return; }
  const W=1160, H=Math.max(380, 60+nodes.length*16), cx=W/2, cy=H/2;
  const R=Math.min(cx,cy)-95;
  const pos={}, base=p=>p.split('/').pop();
  nodes.forEach((n,i)=>{ const a=-Math.PI/2 + 2*Math.PI*i/nodes.length;
    pos[n.file]={x:cx+R*Math.cos(a), y:cy+R*Math.sin(a), a}; });
  const maxAcc=Math.max(1,...nodes.map(n=>n.reads+n.writes));
  const maxW=Math.max(1,...edges.map(e=>e.weight));
  let s=`<svg viewBox="0 0 ${W} ${H}" id="fg">`;
  edges.forEach((e,ei)=>{ const p=pos[e.source],q=pos[e.target]; if(!p||!q) return;
    s+=`<line data-e="${ei}" x1="${p.x}" y1="${p.y}" x2="${q.x}" y2="${q.y}" stroke="#9085e9" `+
       `stroke-width="${0.6+3.4*e.weight/maxW}" stroke-opacity="${0.18+0.5*e.weight/maxW}"/>`; });
  nodes.forEach((n,ni)=>{ const p=pos[n.file], acc=n.reads+n.writes;
    const r=4+9*Math.sqrt(acc/maxAcc), col=n.writes>n.reads?'#d95926':'#3987e5';
    const right=Math.cos(p.a)>=0, lx=p.x+(right?1:-1)*(r+6);
    s+=`<circle data-n="${ni}" data-f="${encodeURIComponent(n.file)}" data-r="${n.reads}" data-w="${n.writes}" `+
       `cx="${p.x}" cy="${p.y}" r="${r}" fill="${col}" opacity="0.92"/>`;
    s+=`<text data-tn="${ni}" x="${lx}" y="${p.y+3}" fill="#8f9099" font-size="10.5" `+
       `text-anchor="${right?'start':'end'}">${esc(base(n.file))}</text>`; });
  s+=`</svg>`;
  host.outerHTML=s;
  const svg=document.getElementById('fg');
  const touching=(e,f)=> e.source===f || e.target===f;
  svg.querySelectorAll('circle[data-n]').forEach(c=>{
    const file=decodeURIComponent(c.dataset.f);
    c.addEventListener('mouseenter',()=>{
      svg.classList.add('focus'); c.classList.add('hi');
      svg.querySelector(`text[data-tn="${c.dataset.n}"]`).classList.add('hi');
      edges.forEach((e,ei)=>{ if(!touching(e,file)) return;
        const ln=svg.querySelector(`line[data-e="${ei}"]`); if(ln) ln.classList.add('hi');
        const other=e.source===file?e.target:e.source;
        const oi=nodes.findIndex(n=>n.file===other);
        if(oi>=0){ const oc=svg.querySelector(`circle[data-n="${oi}"]`);
          if(oc) oc.classList.add('hi');
          const ot=svg.querySelector(`text[data-tn="${oi}"]`); if(ot) ot.classList.add('hi'); }
      });
    });
    c.addEventListener('mouseleave',()=>{ svg.classList.remove('focus');
      svg.querySelectorAll('.hi').forEach(x=>x.classList.remove('hi')); });
    c.addEventListener('mousemove',e=>showTip(e,
      `<code>${esc(file)}</code><br>reads ${c.dataset.r} · writes ${c.dataset.w}`));
    c.addEventListener('mouseleave',hideTip);
  });
})();

function shorten(p){ return p.length>52? '…'+p.slice(-50) : p; }
</script>
</body>
</html>
"""


def render_html(trace: Trace) -> str:
    data = json.dumps(trace.as_dict(), separators=(",", ":"))
    return (_TEMPLATE
            .replace("__DATA__", data)
            .replace("__SESSION__", (trace.session_id or "")[:8]))
