"""Render a :class:`~cc_trace.parser.Trace` into a self-contained HTML report.

No external dependencies or CDNs: the trace is embedded as JSON and all charts
are drawn with inline SVG/JS, so the report opens offline straight from disk.
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
    --bg:#0d1117; --panel:#161b22; --line:#30363d; --txt:#e6edf3;
    --muted:#8b949e; --explore:#3fb950; --execute:#f0883e; --other:#8b949e;
    --error:#f85149; --accent:#58a6ff;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
         font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  header { padding:24px 28px; border-bottom:1px solid var(--line); }
  h1 { margin:0 0 4px; font-size:20px; }
  .sub { color:var(--muted); font-size:13px; }
  main { padding:20px 28px 60px; max-width:1180px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
           gap:12px; margin:18px 0 28px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
          padding:14px 16px; }
  .card .v { font-size:22px; font-weight:600; }
  .card .k { color:var(--muted); font-size:12px; text-transform:uppercase;
             letter-spacing:.04em; margin-top:2px; }
  section { background:var(--panel); border:1px solid var(--line); border-radius:10px;
            padding:18px 20px; margin-bottom:20px; }
  h2 { margin:0 0 4px; font-size:15px; }
  .hint { color:var(--muted); font-size:12px; margin:0 0 14px; }
  svg { width:100%; display:block; }
  .legend { display:flex; gap:16px; font-size:12px; color:var(--muted); margin-top:10px; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:2px;
         margin-right:5px; vertical-align:middle; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-weight:500; font-size:12px; }
  td.num,th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .pill { font-size:11px; padding:1px 7px; border-radius:10px; }
  .pill.err { background:rgba(248,81,73,.15); color:var(--error); }
  code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
  .bar-row { display:flex; align-items:center; gap:10px; margin:5px 0; }
  .bar-row .name { width:120px; font-size:13px; }
  .bar-row .track { flex:1; background:#21262d; border-radius:4px; height:18px; position:relative; }
  .bar-row .fill { height:100%; border-radius:4px; background:var(--accent); }
  .bar-row .cnt { width:84px; text-align:right; color:var(--muted); font-size:12px;
                  font-variant-numeric:tabular-nums; }
  .tip { position:fixed; pointer-events:none; background:#000; border:1px solid var(--line);
         border-radius:6px; padding:6px 9px; font-size:12px; max-width:340px; display:none;
         z-index:10; white-space:pre-wrap; word-break:break-word; }
</style>
</head>
<body>
<header>
  <h1>Claude Code Trace Visualizer</h1>
  <div class="sub" id="meta"></div>
</header>
<main>
  <div class="cards" id="cards"></div>

  <section>
    <h2>Timeline of tool calls</h2>
    <p class="hint">Each bar is one tool call, placed at its start offset and sized
      by wall-clock duration. Color = phase. Hover for detail.</p>
    <svg id="timeline"></svg>
    <div class="legend">
      <span><i class="dot" style="background:var(--explore)"></i>explore / read</span>
      <span><i class="dot" style="background:var(--execute)"></i>execute / write</span>
      <span><i class="dot" style="background:var(--other)"></i>other</span>
      <span><i class="dot" style="background:var(--error)"></i>error</span>
    </div>
  </section>

  <section>
    <h2>Phase progression — read/explore vs. execute/write</h2>
    <p class="hint">Cumulative share of explore vs. execute tool calls across the run.
      The workload paper predicts explore dominates early, execute later.</p>
    <svg id="phases"></svg>
  </section>

  <section>
    <h2>Context growth &amp; token usage per turn</h2>
    <p class="hint">Context (cache-read + fresh input) per model turn. Agentic runs are
      decode-dominated with heavy KV-cache reuse — watch the cache-read line dominate.</p>
    <svg id="tokens"></svg>
    <div class="legend">
      <span><i class="dot" style="background:var(--accent)"></i>cache read (reused context)</span>
      <span><i class="dot" style="background:var(--explore)"></i>fresh input</span>
      <span><i class="dot" style="background:var(--execute)"></i>output</span>
    </div>
  </section>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
    <section style="margin:0">
      <h2>Tool-call breakdown</h2>
      <p class="hint">Count per tool (bar) with total time and error count.</p>
      <div id="breakdown"></div>
    </section>
    <section style="margin:0">
      <h2>File access</h2>
      <p class="hint">Files touched, by read vs. write operations.</p>
      <div style="max-height:340px;overflow:auto"><table id="files"></table></div>
    </section>
  </div>

  <section>
    <h2>Network activity</h2>
    <p class="hint">Network the agent reached <em>through its tools</em> — curl/wget,
      git remote ops, package installs, ssh/scp, plus WebFetch/WebSearch/MCP. Parsed
      from command strings (best-effort). Does <b>not</b> include Claude Code's own
      model API calls — those never appear in the transcript.</p>
    <div id="net-summary" class="hint" style="margin-bottom:10px"></div>
    <div style="max-height:340px;overflow:auto"><table id="net"></table></div>
  </section>

  <section>
    <h2>Benchmark validity audit</h2>
    <p class="hint">Flags the capability-dependent benchmark failure modes from
      FINDINGS finding 11 — <b>solution-channel</b> network (the agent fetching a
      fix's provenance: PR diffs, commit searches), <b>leak exposure</b> (an
      instance id in the fixture path/prompt), and <b>stranded work</b> (unbalanced
      <code>git stash</code>). Flags are cues for human review, not verdicts.</p>
    <div id="audit-summary" class="hint" style="margin-bottom:10px"></div>
    <table id="audit"></table>
  </section>

  <section>
    <h2>File co-access graph</h2>
    <p class="hint">Files touched close together in the run are linked; thicker links =
      worked on together more often. Node size = how often a file is accessed,
      color = read- vs. write-dominated. Reveals the clusters the agent edits as a unit.</p>
    <svg id="filegraph"></svg>
    <div class="legend">
      <span><i class="dot" style="background:var(--explore)"></i>read-dominated</span>
      <span><i class="dot" style="background:var(--execute)"></i>write-dominated</span>
    </div>
  </section>

  <section>
    <h2>Retry loops</h2>
    <p class="hint">Same tool + same target attempted repeatedly with at least one
      failure — where the agent got stuck (a command that keeps failing, an edit redone).</p>
    <table id="retries"></table>
  </section>

  <section>
    <h2>Repeated work</h2>
    <p class="hint" id="repeat-summary"></p>
    <p class="hint">Clusters of identical or <em>near-identical</em> calls — the agent
      re-running the same test, re-reading the same file, re-issuing a slightly varied
      command. Each is a candidate a cache/memoiser could collapse. "near" = signature
      match after numbers/strings/paths are normalised away.</p>
    <table id="repeats"></table>
  </section>

  <section>
    <h2>Errors &amp; retries</h2>
    <p class="hint">Tool calls that returned an error (candidate retry loops).</p>
    <table id="errors"></table>
  </section>
</main>
<div class="tip" id="tip"></div>

<script id="data" type="application/json">__DATA__</script>
<script>
const T = JSON.parse(document.getElementById('data').textContent);
const tip = document.getElementById('tip');
const fmtDur = s => s==null ? '—' : (s<1 ? (s*1000|0)+'ms' : s.toFixed(1)+'s');
const fmtN = n => n.toLocaleString();
const phaseColor = p => p==='explore'?'#3fb950':p==='execute'?'#f0883e':'#8b949e';
function showTip(e,html){ tip.innerHTML=html; tip.style.display='block';
  tip.style.left=Math.min(e.clientX+12,innerWidth-360)+'px'; tip.style.top=(e.clientY+12)+'px'; }
function hideTip(){ tip.style.display='none'; }

// ---- meta + cards ----
const t0=T.start, dur=T.duration||0;
document.getElementById('meta').textContent =
  `session ${ (T.session_id||'').slice(0,8) } · ${T.cwd||''} · ${(T.git_branch||'')} · models: ${T.models.join(', ')}`;
const tt=T.token_totals;
const cards=[
  ['Wall-clock', fmtDur(dur)],
  ['Tool calls', fmtN(T.n_tool_calls)],
  ['Model turns', fmtN(T.n_turns)],
  ['Errors', fmtN(T.n_errors)],
  ['Retry loops', fmtN((T.retry_loops||[]).length)],
  ['Output tokens', fmtN(tt.output)],
  ['Cache-read tokens', fmtN(tt.cache_read)],
  ['Est. cost', '$'+T.total_cost.toFixed(3)],
];
document.getElementById('cards').innerHTML = cards.map(([k,v])=>
  `<div class="card"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');

// ---- timeline (gantt) ----
(function(){
  const calls=T.tool_calls, n=calls.length;
  const rowH=16, pad=6, W=1120, labelW=0;
  const H=n*rowH+pad*2;
  const span=Math.max(dur,0.001);
  const x=t=> (t==null? 0 : (t-t0)/span)*(W-labelW-8)+labelW+4);
  let s=`<svg viewBox="0 0 ${W} ${H}" id="tl">`;
  // vertical gridlines (10% steps)
  for(let i=0;i<=10;i++){ const gx=labelW+4+(i/10)*(W-labelW-8);
    s+=`<line x1="${gx}" y1="0" x2="${gx}" y2="${H}" stroke="#21262d"/>`;
    s+=`<text x="${gx}" y="${H-1}" fill="#8b949e" font-size="9" text-anchor="middle">${(span*i/10).toFixed(0)}s</text>`; }
  calls.forEach((c,i)=>{
    const xs=x(c.start), xe=Math.max(x(c.end), xs+2);
    const col=c.is_error?'#f85149':phaseColor(c.phase);
    const y=pad+i*rowH;
    s+=`<rect data-i="${i}" x="${xs}" y="${y}" width="${xe-xs}" height="${rowH-3}" rx="2" fill="${col}" opacity="0.92"/>`;
  });
  s+=`</svg>`;
  const host=document.getElementById('timeline'); host.outerHTML=s;
  document.querySelectorAll('#tl rect[data-i]').forEach(r=>{
    r.addEventListener('mousemove',e=>{ const c=calls[+r.dataset.i];
      showTip(e,`<b>${c.name}</b> <span class="pill ${c.is_error?'err':''}">${c.phase}${c.is_error?' · error':''}</span><br>`+
        `<code>${(c.label||'').replace(/</g,'&lt;')}</code><br>start +${((c.start-t0)||0).toFixed(1)}s · dur ${fmtDur(c.duration)} · ${fmtN(c.output_chars)} chars out`); });
    r.addEventListener('mouseleave',hideTip);
  });
})();

// ---- phase progression (cumulative explore vs execute) ----
(function(){
  const calls=T.tool_calls.filter(c=>c.phase!=='other');
  const W=1120,H=200,m=24;
  if(!calls.length){document.getElementById('phases').outerHTML='<p class="hint">No phased tool calls.</p>';return;}
  let ex=0,exc=0; const pts={explore:[],execute:[]};
  calls.forEach((c,i)=>{ if(c.phase==='explore')ex++; else exc++;
    pts.explore.push([i,ex]); pts.execute.push([i,exc]); });
  const N=calls.length, maxY=Math.max(ex,exc,1);
  const X=i=>m+ (i/(N-1||1))*(W-2*m), Y=v=>H-m-(v/maxY)*(H-2*m);
  const line=(arr,col)=>`<polyline fill="none" stroke="${col}" stroke-width="2" points="${arr.map(([i,v])=>X(i)+','+Y(v)).join(' ')}"/>`;
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  s+=`<line x1="${m}" y1="${H-m}" x2="${W-m}" y2="${H-m}" stroke="#30363d"/>`;
  s+=`<line x1="${m}" y1="${m}" x2="${m}" y2="${H-m}" stroke="#30363d"/>`;
  // explore→execute crossover marker
  const xo=T.phase_crossover||{};
  if(xo.index!=null && N>1){ const cx=X(Math.min(xo.index, N-1));
    s+=`<line x1="${cx}" y1="${m}" x2="${cx}" y2="${H-m}" stroke="#58a6ff" stroke-width="1.5" stroke-dasharray="4 3"/>`;
    s+=`<text x="${cx+4}" y="${m+10}" fill="#58a6ff" font-size="10">crossover ${(100*xo.pos).toFixed(0)}% · purity ${xo.purity}</text>`;
  }
  s+=line(pts.explore,'#3fb950')+line(pts.execute,'#f0883e');
  s+=`<text x="${W-m}" y="${Y(ex)-4}" fill="#3fb950" font-size="11" text-anchor="end">explore ${ex}</text>`;
  s+=`<text x="${W-m}" y="${Y(exc)-4}" fill="#f0883e" font-size="11" text-anchor="end">execute ${exc}</text>`;
  s+=`<text x="${m}" y="${H-6}" fill="#8b949e" font-size="10">tool call #1</text>`;
  s+=`<text x="${W-m}" y="${H-6}" fill="#8b949e" font-size="10" text-anchor="end">#${N}</text>`;
  s+=`</svg>`;
  document.getElementById('phases').outerHTML=s;
})();

// ---- token usage per turn (stacked-ish bars) ----
(function(){
  const turns=T.turns; const W=1120,H=200,m=28;
  if(!turns.length){document.getElementById('tokens').outerHTML='<p class="hint">No turns.</p>';return;}
  const maxY=Math.max(1,...turns.map(t=>t.cache_read_tokens+t.input_tokens+t.output_tokens));
  const bw=(W-2*m)/turns.length;
  const Y=v=>(v/maxY)*(H-2*m);
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  s+=`<line x1="${m}" y1="${H-m}" x2="${W-m}" y2="${H-m}" stroke="#30363d"/>`;
  turns.forEach((t,i)=>{ const x=m+i*bw; let y=H-m;
    const segs=[[t.cache_read_tokens,'#58a6ff'],[t.input_tokens,'#3fb950'],[t.output_tokens,'#f0883e']];
    segs.forEach(([v,c])=>{ const h=Y(v); y-=h;
      s+=`<rect x="${x+1}" y="${y}" width="${Math.max(bw-2,1)}" height="${h}" fill="${c}" data-i="${i}"/>`; });
  });
  s+=`<text x="${m}" y="${m-10}" fill="#8b949e" font-size="10">peak ${fmtN(maxY)} tok</text></svg>`;
  document.getElementById('tokens').outerHTML=s;
})();

// ---- tool breakdown bars ----
(function(){
  const b=T.tool_breakdown, max=Math.max(1,...b.map(x=>x.count));
  document.getElementById('breakdown').innerHTML=b.map(x=>
    `<div class="bar-row"><span class="name">${x.name}</span>`+
    `<span class="track"><span class="fill" style="width:${100*x.count/max}%"></span></span>`+
    `<span class="cnt">${x.count}× · ${fmtDur(x.duration)}${x.errors?` · <span style="color:var(--error)">${x.errors}e</span>`:''}</span></div>`).join('');
})();

// ---- file access ----
(function(){
  const f=T.file_access;
  let s=`<tr><th>File</th><th class="num">reads</th><th class="num">writes</th></tr>`;
  s+= f.length? f.map(r=>`<tr><td><code>${shorten(r.file)}</code></td><td class="num">${r.reads}</td><td class="num">${r.writes}</td></tr>`).join('')
            : `<tr><td colspan="3" style="color:var(--muted)">No file operations.</td></tr>`;
  document.getElementById('files').innerHTML=s;
})();

// ---- network activity ----
(function(){
  const na=T.network_activity||{total:0,by_kind:[],requests:[]};
  const kc={http:'#58a6ff',api:'#58a6ff',git:'#f0883e',package:'#a371f7',
    ssh:'#3fb950',dns:'#d29922',socket:'#db61a2',probe:'#8b949e',
    search:'#56d4dd',mcp:'#e3b341'};
  const col=k=>kc[k]||'#8b949e';
  const sum=document.getElementById('net-summary');
  if(!na.total){ sum.textContent='No agent-initiated network activity detected.';
    document.getElementById('net').outerHTML=''; return; }
  sum.innerHTML=`<b>${na.total}</b> request${na.total>1?'s':''} · `+
    na.by_kind.map(k=>`<span class="pill" style="border:1px solid ${col(k.kind)};color:${col(k.kind)}">`+
      `${k.kind} ${k.count}</span>`).join(' ');
  let s=`<tr><th class="num">#</th><th>kind</th><th>target</th><th>via</th></tr>`;
  s+= na.requests.map(r=>`<tr><td class="num">${r.index}</td>`+
    `<td><span class="pill" style="border:1px solid ${col(r.kind)};color:${col(r.kind)}">${r.kind}</span></td>`+
    `<td><code>${shorten((r.target||'').replace(/</g,'&lt;'))}</code>${r.error?' <span class="pill err">err</span>':''}</td>`+
    `<td>${r.tool}</td></tr>`).join('');
  document.getElementById('net').innerHTML=s;
})();

// ---- benchmark validity audit ----
(function(){
  const va=T.validity_audit||{flags:[]};
  const sum=document.getElementById('audit-summary');
  const repo=va.repo_under_test
    ? `repo under test: <code>${va.repo_under_test}</code> (${va.repo_source})`
    : 'repo under test: not identified — solution-channel flags are unscoped';
  if(!va.flags.length){
    sum.innerHTML=`No validity flags. 🎉 · ${repo}`;
    document.getElementById('audit').outerHTML=''; return; }
  sum.innerHTML=`<b>${va.n_flags}</b> flag${va.n_flags>1?'s':''} · ${repo}`;
  let s=`<tr><th>severity</th><th>kind</th><th>detail</th><th class="num">at</th></tr>`;
  s+=va.flags.map(f=>`<tr>`+
    `<td><span class="pill ${f.severity==='high'?'err':''}" style="${f.severity!=='high'?'border:1px solid var(--muted);color:var(--muted)':''}">${f.severity}</span></td>`+
    `<td>${f.kind.replace('_',' ')}</td>`+
    `<td><code>${(f.detail||'').replace(/</g,'&lt;')}</code></td>`+
    `<td class="num">${f.index==null?'—':'#'+f.index}</td></tr>`).join('');
  document.getElementById('audit').innerHTML=s;
})();

// ---- retry loops ----
(function(){
  const loops=T.retry_loops||[];
  let s=`<tr><th>Tool</th><th>Target</th><th class="num">attempts</th><th class="num">errors</th><th class="num">span</th><th class="num">at</th></tr>`;
  s+= loops.length? loops.map(r=>`<tr><td>${r.tool}</td><td><code>${shorten((r.target||'').replace(/</g,'&lt;'))}</code></td>`+
        `<td class="num">${r.attempts}</td><td class="num"><span class="pill err">${r.errors}</span></td>`+
        `<td class="num">${fmtDur(r.span_s)}</td><td class="num">#${r.first_index}–${r.last_index}</td></tr>`).join('')
      : `<tr><td colspan="6" style="color:var(--muted)">No retry loops detected. 🎉</td></tr>`;
  document.getElementById('retries').innerHTML=s;
})();

// ---- repeated work ----
(function(){
  const rw=T.repeated_work||{clusters:[]}, cl=rw.clusters||[];
  const pct=rw.redundant_frac!=null?(100*rw.redundant_frac).toFixed(0):'0';
  document.getElementById('repeat-summary').innerHTML = cl.length
    ? `<b>${fmtN(rw.redundant_calls)}</b> of ${fmtN(rw.n_tool_calls)} tool calls (${pct}%) `+
      `were repeats of earlier work, across <b>${fmtN(rw.n_clusters)}</b> clusters — `+
      `${fmtDur(rw.redundant_s)} spent on the repeat invocations.`
    : `No repeated work detected. 🎉`;
  let s=`<tr><th>Tool</th><th>Example</th><th class="num">calls</th><th class="num">redundant</th>`+
        `<th class="num">kind</th><th class="num">errors</th><th class="num">time</th><th class="num">at</th></tr>`;
  s+= cl.length? cl.map(r=>`<tr><td>${r.tool}</td>`+
        `<td><code>${shorten((r.example||'').replace(/</g,'&lt;'))}</code></td>`+
        `<td class="num">${r.count}</td><td class="num"><b>${r.redundant}</b></td>`+
        `<td class="num">${r.exact?'exact':'near ('+r.distinct+')'}</td>`+
        `<td class="num">${r.errors?'<span class="pill err">'+r.errors+'</span>':'—'}</td>`+
        `<td class="num">${fmtDur(r.redundant_s)}</td><td class="num">#${r.first_index}–${r.last_index}</td></tr>`).join('')
      : `<tr><td colspan="8" style="color:var(--muted)">No repeated work detected. 🎉</td></tr>`;
  document.getElementById('repeats').innerHTML=s;
})();

// ---- errors ----
(function(){
  const errs=T.tool_calls.filter(c=>c.is_error);
  let s=`<tr><th>#</th><th>Tool</th><th>Detail</th><th class="num">at</th></tr>`;
  s+= errs.length? errs.map(c=>`<tr><td>${c.index}</td><td>${c.name}</td><td><code>${(c.label||'').replace(/</g,'&lt;')}</code></td><td class="num">+${((c.start-t0)||0).toFixed(1)}s</td></tr>`).join('')
            : `<tr><td colspan="4" style="color:var(--muted)">No tool errors. 🎉</td></tr>`;
  document.getElementById('errors').innerHTML=s;
})();

// ---- file co-access graph (circular layout) ----
(function(){
  const g=T.file_graph||{nodes:[],edges:[]}, nodes=g.nodes||[], edges=g.edges||[];
  const host=document.getElementById('filegraph');
  if(nodes.length<2){ host.outerHTML='<p class="hint">Not enough file access to graph.</p>'; return; }
  const W=1120, H=Math.max(360, 60+nodes.length*16), cx=W/2, cy=H/2;
  const R=Math.min(cx,cy)-90;
  const pos={}, base=p=>p.split('/').pop();
  nodes.forEach((n,i)=>{ const a=-Math.PI/2 + 2*Math.PI*i/nodes.length;
    pos[n.file]={x:cx+R*Math.cos(a), y:cy+R*Math.sin(a), a}; });
  const maxAcc=Math.max(1,...nodes.map(n=>n.reads+n.writes));
  const maxW=Math.max(1,...edges.map(e=>e.weight));
  let s=`<svg viewBox="0 0 ${W} ${H}" id="fg">`;
  edges.forEach(e=>{ const p=pos[e.source],q=pos[e.target]; if(!p||!q) return;
    s+=`<line x1="${p.x}" y1="${p.y}" x2="${q.x}" y2="${q.y}" stroke="#58a6ff" `+
       `stroke-width="${0.6+3.4*e.weight/maxW}" stroke-opacity="${0.18+0.5*e.weight/maxW}"/>`; });
  nodes.forEach(n=>{ const p=pos[n.file], acc=n.reads+n.writes;
    const r=4+9*Math.sqrt(acc/maxAcc), col=n.writes>n.reads?'#f0883e':'#3fb950';
    const right=Math.cos(p.a)>=0, lx=p.x+(right?1:-1)*(r+5);
    s+=`<circle data-f="${encodeURIComponent(n.file)}" data-r="${n.reads}" data-w="${n.writes}" `+
       `cx="${p.x}" cy="${p.y}" r="${r}" fill="${col}" opacity="0.92"/>`;
    s+=`<text x="${lx}" y="${p.y+3}" fill="#8b949e" font-size="10" `+
       `text-anchor="${right?'start':'end'}">${base(n.file)}</text>`; });
  s+=`</svg>`;
  host.outerHTML=s;
  document.querySelectorAll('#fg circle[data-f]').forEach(c=>{
    c.addEventListener('mousemove',e=>showTip(e,
      `<code>${decodeURIComponent(c.dataset.f).replace(/</g,'&lt;')}</code><br>`+
      `reads ${c.dataset.r} · writes ${c.dataset.w}`));
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
