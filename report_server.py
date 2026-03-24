"""
report_server.py — repomap interactive dashboard server.

Serves a rich single-page HTML dashboard from a repomap JSON report.
Zero external dependencies — uses only Python stdlib + inline CSS/JS.

Usage:
  python report_server.py repomap_pallets_flask.json
  python report_server.py repomap_pallets_flask.json --port 8765 --no-open
  python report_server.py repomap_pallets_flask.json --symbols repomap_pallets_flask_symbols.json
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# ──────────────────────────────────────────────────────────────
# HTML TEMPLATE
# ──────────────────────────────────────────────────────────────
def _build_html(report: dict, symbols: Optional[dict]) -> str:
    repo_url  = report.get("repo_url", "Unknown")
    repo_name = repo_url.rstrip("/").split("/")[-1]
    score     = report.get("score", {})
    score_val = score.get("value", 0)
    score_lbl = score.get("label", "")
    scanned   = report.get("scanned_at", "")
    languages = report.get("languages", {})
    analysis  = report.get("analysis", {})
    arch      = analysis.get("architecture", {})
    epc       = analysis.get("entry_point_confidence", {})
    fdp       = analysis.get("first_day_path", {})
    routes    = analysis.get("route_detection", {})
    params    = analysis.get("parameter_tracking", {})
    hc        = analysis.get("hidden_complexity", {})
    dep       = analysis.get("dependency_impact", {})
    naming    = analysis.get("naming_consistency", {})
    flow      = analysis.get("flow_trace", {})
    friction  = report.get("friction", [])

    score_color = ("#22c55e" if score_val >= 75
                   else "#eab308" if score_val >= 50
                   else "#ef4444")

    # Serialise full data for JS
    report_json   = json.dumps(report,  ensure_ascii=False)
    symbols_json  = json.dumps(symbols or {}, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>repomap — {repo_name}</title>
<style>
/* ── Reset & base ─────────────────────────────────── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --bg:       #0d1117;
  --surface:  #161b22;
  --surface2: #21262d;
  --border:   #30363d;
  --text:     #e6edf3;
  --muted:    #8b949e;
  --accent:   #58a6ff;
  --green:    #3fb950;
  --yellow:   #d29922;
  --red:      #f85149;
  --purple:   #bc8cff;
  --orange:   #ffa657;
  --score:    {score_color};
  --radius:   8px;
  --font:     'Segoe UI', system-ui, -apple-system, sans-serif;
  --mono:     'Cascadia Code', 'Fira Code', 'Consolas', monospace;
}}
html {{ scroll-behavior: smooth; }}
body {{
  font-family: var(--font);
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  line-height: 1.6;
}}

/* ── Layout ───────────────────────────────────────── */
.layout {{ display: flex; min-height: 100vh; }}
.sidebar {{
  width: 220px; min-width: 220px;
  background: var(--surface);
  border-right: 1px solid var(--border);
  position: sticky; top: 0; height: 100vh; overflow-y: auto;
  padding: 20px 0;
  flex-shrink: 0;
}}
.main {{ flex: 1; overflow-x: hidden; padding: 32px 40px; max-width: 1400px; }}

/* ── Sidebar ──────────────────────────────────────── */
.sidebar-logo {{
  padding: 0 20px 20px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 12px;
}}
.sidebar-logo span {{ color: var(--accent); font-weight: 700; font-size: 16px; }}
.sidebar-logo small {{ display: block; color: var(--muted); font-size: 11px; margin-top: 2px; }}
.nav-item {{
  display: block; padding: 7px 20px;
  color: var(--muted); text-decoration: none;
  font-size: 13px; border-left: 2px solid transparent;
  transition: all .15s; cursor: pointer;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  user-select: none;
}}
.nav-item:hover, .nav-item.active {{
  color: var(--text); background: var(--surface2);
  border-left-color: var(--accent);
}}
.nav-section {{
  padding: 14px 20px 4px;
  color: var(--muted); font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: .08em;
}}

/* ── Cards ────────────────────────────────────────── */
.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 24px;
  margin-bottom: 16px;
}}
.card-title {{
  font-size: 13px; font-weight: 600;
  color: var(--muted); text-transform: uppercase;
  letter-spacing: .06em; margin-bottom: 14px;
  display: flex; align-items: center; gap: 8px;
}}
.card-title .icon {{ font-size: 16px; }}

/* ── Hero ─────────────────────────────────────────── */
.hero {{
  display: grid; grid-template-columns: 1fr auto;
  gap: 24px; align-items: start;
  margin-bottom: 24px;
}}
.hero-title {{ font-size: 24px; font-weight: 700; color: var(--text); }}
.hero-title a {{ color: var(--accent); text-decoration: none; }}
.hero-title a:hover {{ text-decoration: underline; }}
.hero-meta {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
.score-ring {{
  width: 90px; height: 90px;
  border-radius: 50%;
  background: conic-gradient(var(--score) {score_val * 3.6}deg, var(--surface2) 0deg);
  display: flex; align-items: center; justify-content: center;
  position: relative; flex-shrink: 0;
}}
.score-ring::before {{
  content: ''; position: absolute;
  width: 70px; height: 70px;
  border-radius: 50%; background: var(--surface);
}}
.score-inner {{
  position: relative; z-index: 1;
  text-align: center; line-height: 1.2;
}}
.score-num {{ font-size: 22px; font-weight: 700; color: var(--score); }}
.score-label {{ font-size: 10px; color: var(--muted); }}

/* ── Stats grid ───────────────────────────────────── */
.stats-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px; margin-bottom: 24px;
}}
.stat-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
}}
.stat-val {{ font-size: 26px; font-weight: 700; color: var(--text); }}
.stat-label {{ font-size: 12px; color: var(--muted); margin-top: 2px; }}

/* ── Sections ─────────────────────────────────────── */
.section {{ margin-bottom: 48px; scroll-margin-top: 16px; }}
.section-title {{
  font-size: 18px; font-weight: 600;
  margin-bottom: 16px; color: var(--text);
  display: flex; align-items: center; gap: 10px;
}}
.section-title::after {{
  content: ''; flex: 1; height: 1px; background: var(--border);
}}

/* ── Tables ───────────────────────────────────────── */
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{
  text-align: left; padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--muted); font-weight: 500;
  white-space: nowrap;
}}
td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }}
tr:last-child td {{ border-bottom: none; }}
tr:hover td {{ background: var(--surface2); }}

/* ── Badges ───────────────────────────────────────── */
.badge {{
  display: inline-block; padding: 2px 8px;
  border-radius: 12px; font-size: 11px; font-weight: 500;
  white-space: nowrap;
}}
.badge-green  {{ background: #1a3a27; color: var(--green); }}
.badge-yellow {{ background: #352a00; color: var(--yellow); }}
.badge-red    {{ background: #3a1414; color: var(--red); }}
.badge-blue   {{ background: #0d2645; color: var(--accent); }}
.badge-purple {{ background: #281f3a; color: var(--purple); }}
.badge-orange {{ background: #3a2000; color: var(--orange); }}
.badge-muted  {{ background: var(--surface2); color: var(--muted); }}

/* ── Code ─────────────────────────────────────────── */
code {{
  font-family: var(--mono); font-size: 12px;
  background: var(--surface2); color: var(--accent);
  padding: 1px 5px; border-radius: 4px;
  word-break: break-all;
}}
pre code {{
  display: block; padding: 10px 14px;
  background: var(--surface2); border-radius: var(--radius);
  color: var(--text); overflow-x: auto; line-height: 1.5;
}}

/* ── Progress bars ────────────────────────────────── */
.bar-wrap {{
  background: var(--surface2); border-radius: 4px;
  height: 8px; overflow: hidden; min-width: 60px;
}}
.bar {{ height: 100%; border-radius: 4px; transition: width .3s; }}

/* ── Friction items ───────────────────────────────── */
.friction-item {{
  display: flex; gap: 12px; align-items: flex-start;
  padding: 10px 0; border-bottom: 1px solid var(--border);
}}
.friction-item:last-child {{ border-bottom: none; }}
.friction-icon {{ font-size: 18px; flex-shrink: 0; margin-top: 1px; }}

/* ── First-day steps ──────────────────────────────── */
.step {{
  display: flex; gap: 14px; align-items: flex-start;
  padding: 10px 14px; border-radius: var(--radius);
  margin-bottom: 6px; background: var(--surface2);
}}
.step-num {{
  width: 26px; height: 26px; border-radius: 50%;
  background: var(--border); color: var(--muted);
  font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}}
.step.found .step-num  {{ background: #1a3a27; color: var(--green); }}
.step.missing .step-num{{ background: #3a1414; color: var(--red); }}
.step.manual .step-num {{ background: #352a00; color: var(--yellow); }}
.step-body {{ flex: 1; }}
.step-action {{ font-weight: 600; font-size: 13px; }}
.step-target {{ font-family: var(--mono); font-size: 12px; color: var(--accent); margin-top: 3px; }}
.step-note   {{ font-size: 12px; color: var(--muted); margin-top: 3px; }}

/* ── Search ───────────────────────────────────────── */
.search-wrap {{ position: relative; margin-bottom: 16px; }}
.search-input {{
  width: 100%; padding: 8px 12px 8px 34px;
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius); color: var(--text); font-size: 13px;
  outline: none; transition: border-color .15s;
}}
.search-input:focus {{ border-color: var(--accent); }}
.search-icon {{ position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--muted); }}

/* ── Symbol graph ─────────────────────────────────── */
#sym-canvas {{
  width: 100%; height: 480px;
  background: var(--surface2); border-radius: var(--radius);
  cursor: grab;
}}
#sym-canvas:active {{ cursor: grabbing; }}
.sym-tooltip {{
  position: fixed; pointer-events: none;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 8px 12px;
  font-size: 12px; max-width: 280px; z-index: 100;
  box-shadow: 0 4px 20px rgba(0,0,0,.4); display: none;
}}

/* ── Route method pills ───────────────────────────── */
.method {{ font-family: var(--mono); font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 4px; }}
.method-GET    {{ background: #0d2a1a; color: #3fb950; }}
.method-POST   {{ background: #1a2040; color: #79c0ff; }}
.method-PUT    {{ background: #2a1a00; color: #d29922; }}
.method-PATCH  {{ background: #2a1a00; color: #d29922; }}
.method-DELETE {{ background: #3a1414; color: #f85149; }}
.method-ANY    {{ background: var(--surface2); color: var(--muted); }}

/* ── Scrollbar ────────────────────────────────────── */
::-webkit-scrollbar {{ width: 6px; height: 6px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}

/* ── Responsive ───────────────────────────────────── */
@media (max-width: 900px) {{
  .sidebar {{ display: none; }}
  .main {{ padding: 20px; }}
  .hero {{ grid-template-columns: 1fr; }}
  .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}
</style>
</head>
<body>
<div class="layout">

<!-- ── Sidebar ──────────────────────────────────── -->
<nav class="sidebar">
  <div class="sidebar-logo">
    <span>repomap</span>
    <small>{repo_name}</small>
  </div>
  <div class="nav-section">Overview</div>
  <a class="nav-item active" onclick="navTo('overview',this)">📊 Summary</a>
  <a class="nav-item" onclick="navTo('friction',this)">⚡ Friction</a>
  <a class="nav-item" onclick="navTo('firstday',this)">🗓️ First Day</a>
  <div class="nav-section">Analysis</div>
  <a class="nav-item" onclick="navTo('architecture',this)">🏛️ Architecture</a>
  <a class="nav-item" onclick="navTo('entrypoints',this)">🚀 Entry Points</a>
  <a class="nav-item" onclick="navTo('routes',this)">🛣️ Routes</a>
  <a class="nav-item" onclick="navTo('params',this)">🔑 Parameters</a>
  <a class="nav-item" onclick="navTo('complexity',this)">🧩 Complexity</a>
  <a class="nav-item" onclick="navTo('deps',this)">📦 Dependencies</a>
  <a class="nav-item" onclick="navTo('naming',this)">✏️ Naming</a>
  <a class="nav-item" onclick="navTo('flow',this)">🔀 Flow</a>
  <div class="nav-section">Symbols</div>
  <a class="nav-item" onclick="navTo('symbols',this)">🔗 Symbol Graph</a>
</nav>

<!-- ── Main ─────────────────────────────────────── -->
<main class="main">

<!-- Hero -->
<div id="overview" class="section">
  <div class="hero">
    <div>
      <div class="hero-title">
        <a href="{repo_url}" target="_blank">{repo_url}</a>
      </div>
      <div class="hero-meta">Scanned {scanned}</div>
      <div class="hero-meta" style="margin-top:6px">
        {arch.get('label','') and f'<span class="badge badge-blue">🏛️ {arch.get("label","")}</span>' or ''}
        {' '.join(f'<span class="badge badge-muted">{lang}</span>' for lang in list(languages.keys())[:5])}
      </div>
    </div>
    <div style="text-align:center">
      <div class="score-ring">
        <div class="score-inner">
          <div class="score-num">{score_val}</div>
          <div class="score-label">{score_lbl}</div>
        </div>
      </div>
      <div style="color:var(--muted);font-size:11px;margin-top:6px">Onboarding score</div>
    </div>
  </div>

  <!-- Stats -->
  <div class="stats-grid" id="stats-grid"></div>
</div>

<!-- Friction -->
<div id="friction" class="section">
  <div class="section-title">⚡ Friction Analysis</div>
  <div class="card" id="friction-list"></div>
</div>

<!-- First Day -->
<div id="firstday" class="section">
  <div class="section-title" id="firstday-title">🗓️ First Day Path</div>
  <div id="firstday-steps"></div>
</div>

<!-- Architecture -->
<div id="architecture" class="section">
  <div class="section-title">🏛️ Architecture</div>
  <div id="arch-content"></div>
</div>

<!-- Entry Points -->
<div id="entrypoints" class="section">
  <div class="section-title">🚀 Entry Point Confidence</div>
  <div class="card">
    <div id="ep-list"></div>
  </div>
</div>

<!-- Routes -->
<div id="routes" class="section">
  <div class="section-title" id="routes-title">🛣️ Routes</div>
  <div class="search-wrap">
    <span class="search-icon">🔍</span>
    <input class="search-input" id="route-search" placeholder="Filter routes…" oninput="filterRoutes()"/>
  </div>
  <div class="table-wrap"><table id="routes-table">
    <thead><tr><th>Method</th><th>Path</th><th>File</th><th>Framework</th></tr></thead>
    <tbody id="routes-body"></tbody>
  </table></div>
</div>

<!-- Parameters -->
<div id="params" class="section">
  <div class="section-title" id="params-title">🔑 Parameter Tracking</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px" id="params-grid"></div>
</div>

<!-- Complexity -->
<div id="complexity" class="section">
  <div class="section-title">🧩 Hidden Complexity</div>
  <div id="complexity-list"></div>
</div>

<!-- Dependencies -->
<div id="deps" class="section">
  <div class="section-title" id="deps-title">📦 Dependency Impact</div>
  <div class="search-wrap">
    <span class="search-icon">🔍</span>
    <input class="search-input" id="dep-search" placeholder="Filter packages…" oninput="filterDeps()"/>
  </div>
  <div class="table-wrap"><table id="deps-table">
    <thead><tr><th>Package</th><th>Version</th><th>Impact</th><th>Files</th><th>Dirs</th></tr></thead>
    <tbody id="deps-body"></tbody>
  </table></div>
</div>

<!-- Naming -->
<div id="naming" class="section">
  <div class="section-title">✏️ Naming Consistency</div>
  <div id="naming-content"></div>
</div>

<!-- Flow -->
<div id="flow" class="section">
  <div class="section-title" id="flow-title">🔀 Flow Trace</div>
  <div id="flow-content"></div>
</div>

<!-- Symbol Graph -->
<div id="symbols" class="section">
  <div class="section-title" id="sym-title">🔗 Cross-File Symbol Map</div>
  <div class="search-wrap">
    <span class="search-icon">🔍</span>
    <input class="search-input" id="sym-search" placeholder="Search symbols…" oninput="filterSymbols()"/>
  </div>
  <canvas id="sym-canvas"></canvas>
  <div class="sym-tooltip" id="sym-tooltip"></div>
  <div style="margin-top:16px">
    <div class="table-wrap"><table id="sym-table">
      <thead><tr><th>Symbol</th><th>Kind</th><th>Defined in</th><th>Used in</th><th>Refs</th></tr></thead>
      <tbody id="sym-body"></tbody>
    </table></div>
  </div>
</div>

</main>
</div>

<!-- ── Tooltip ─────────────────────────────────────── -->
<div id="sym-tooltip" class="sym-tooltip"></div>

<script>
// ── Data ─────────────────────────────────────────────
const R = {report_json};
const S = {symbols_json};
const ana = R.analysis || {{}};
const friction = R.friction || [];
const languages = R.languages || {{}};

// ── Nav ───────────────────────────────────────────────
const SECTION_IDS = [
  'overview','friction','firstday','architecture','entrypoints',
  'routes','params','complexity','deps','naming','flow','symbols'
];

function navTo(id, el) {{
  // Prevent default anchor behaviour
  const target = document.getElementById(id);
  if (!target) return;

  // Offset scroll to account for any sticky headers
  const top = target.getBoundingClientRect().top + window.scrollY - 12;
  window.scrollTo({{ top, behavior: 'smooth' }});

  // Update active state immediately on click
  setActiveNav(el);
}}

function setActiveNav(activeEl) {{
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  if (activeEl) activeEl.classList.add('active');
}}

// Scroll-spy: update nav highlight as user scrolls
function updateNavFromScroll() {{
  const scrollY = window.scrollY + 80; // 80px offset so active fires before top
  let current = SECTION_IDS[0];
  for (const id of SECTION_IDS) {{
    const el = document.getElementById(id);
    if (el && el.offsetTop <= scrollY) current = id;
  }}
  // Find the nav item that targets `current`
  document.querySelectorAll('.nav-item').forEach(el => {{
    const match = el.getAttribute('onclick')?.includes(`'${{current}}'`);
    el.classList.toggle('active', !!match);
  }});
}}

window.addEventListener('scroll', updateNavFromScroll, {{ passive: true }});

// ── Stats grid ────────────────────────────────────────
function renderStats() {{
  const g = document.getElementById('stats-grid');
  const stats = [
    ['Files', R.file_count?.toLocaleString()],
    ['Directories', R.dir_count?.toLocaleString()],
    ['Languages', Object.keys(languages).length],
    ['Routes', ana.route_detection?.total ?? '—'],
    ['Env Vars', ana.parameter_tracking?.env_var_count ?? '—'],
    ['Dependencies', ana.dependency_impact?.total ?? '—'],
    ['Symbols', S.total_symbols ?? '—'],
    ['Complexity signals', ana.hidden_complexity?.total_signals ?? '—'],
  ];
  g.innerHTML = stats.map(([l,v]) => `
    <div class="stat-card">
      <div class="stat-val">${{v ?? '—'}}</div>
      <div class="stat-label">${{l}}</div>
    </div>`).join('');
}}

// ── Friction ──────────────────────────────────────────
function renderFriction() {{
  const el = document.getElementById('friction-list');
  if (!friction.length) {{ el.innerHTML = '<p style="color:var(--muted)">No friction issues detected.</p>'; return; }}
  el.innerHTML = friction.map(f => {{
    const icon = f.severity === 'positive' ? '✅'
               : f.severity === 'high'     ? '🔴'
               : f.severity === 'medium'   ? '🟡' : '⚪';
    const cls  = f.severity === 'positive' ? 'badge-green'
               : f.severity === 'high'     ? 'badge-red'
               : f.severity === 'medium'   ? 'badge-yellow' : 'badge-muted';
    return `<div class="friction-item">
      <div class="friction-icon">${{icon}}</div>
      <div>
        <span class="badge ${{cls}}">${{f.severity}}</span>
        <span style="margin-left:8px">${{f.message}}</span>
      </div>
    </div>`;
  }}).join('');
}}

// ── First day path ────────────────────────────────────
function renderFirstDay() {{
  const fdp = ana.first_day_path;
  if (!fdp) return;
  document.getElementById('firstday-title').textContent =
    `🗓️ First Day Path  (${{fdp.completeness}}% automatable)`;
  const el = document.getElementById('firstday-steps');
  el.innerHTML = (fdp.steps || []).map(s => `
    <div class="step ${{s.status}}">
      <div class="step-num">${{s.order}}</div>
      <div class="step-body">
        <div class="step-action">${{s.action}}</div>
        ${{s.target && s.target !== 'unknown' ? `<div class="step-target">→ ${{s.target}}</div>` : ''}}
        ${{s.note ? `<div class="step-note">${{s.note}}</div>` : ''}}
      </div>
      <div class="badge ${{s.status==='found'?'badge-green':s.status==='missing'?'badge-red':'badge-yellow'}}">
        ${{s.status}}
      </div>
    </div>`).join('');
}}

// ── Architecture ──────────────────────────────────────
function renderArch() {{
  const arch = ana.architecture;
  if (!arch) return;
  const conf_cls = arch.confidence==='high'?'badge-green':arch.confidence==='medium'?'badge-yellow':'badge-muted';
  const cands = (arch.candidates||[]).slice(1,5);
  document.getElementById('arch-content').innerHTML = `
    <div class="card">
      <div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">
        <div style="flex:1">
          <div style="font-size:22px;font-weight:700;color:var(--accent)">${{arch.label||'Unknown'}}</div>
          <div style="color:var(--muted);margin-top:6px">${{arch.description||''}}</div>
          <div style="margin-top:10px">
            <span class="badge ${{conf_cls}}">${{arch.confidence}} confidence</span>
          </div>
        </div>
        ${{cands.length ? `<div>
          <div style="color:var(--muted);font-size:12px;margin-bottom:6px">Also matches</div>
          ${{cands.map(c=>`<div class="badge badge-muted" style="display:block;margin-bottom:4px">${{c.label}} (${{c.score}})</div>`).join('')}}
        </div>` : ''}}
      </div>
      ${{(arch.candidates||[]).length ? `
      <div style="margin-top:16px">
        ${{(arch.candidates||[]).map(c => {{
          const pct = Math.min(100, Math.round(c.score / ((arch.candidates[0]?.score||1)) * 100));
          return `<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
            <div style="width:140px;color:var(--muted);font-size:12px">${{c.label}}</div>
            <div class="bar-wrap" style="flex:1"><div class="bar" style="width:${{pct}}%;background:var(--accent)"></div></div>
            <div style="width:30px;text-align:right;color:var(--muted);font-size:12px">${{c.score}}</div>
          </div>`;
        }}).join('')}}
      </div>` : ''}}
    </div>`;
}}

// ── Entry points ──────────────────────────────────────
function renderEntryPoints() {{
  const epc = ana.entry_point_confidence;
  if (!epc || !epc.scored) return;
  const el = document.getElementById('ep-list');
  el.innerHTML = epc.scored.map(ep => {{
    const col = ep.confidence==='high'?'var(--green)':ep.confidence==='medium'?'var(--yellow)':'var(--red)';
    const cls = ep.confidence==='high'?'badge-green':ep.confidence==='medium'?'badge-yellow':'badge-red';
    return `<div style="display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--border)">
      <div style="width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        <code>${{ep.path}}</code>
      </div>
      <div class="bar-wrap" style="flex:1">
        <div class="bar" style="width:${{ep.score}}%;background:${{col}}"></div>
      </div>
      <div style="width:35px;text-align:right;font-weight:700;color:${{col}}">${{ep.score}}</div>
      <span class="badge ${{cls}}" style="width:60px;text-align:center">${{ep.confidence}}</span>
      <div style="color:var(--muted);font-size:11px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${{(ep.signals||[]).join(' · ')}}
      </div>
    </div>`;
  }}).join('');
}}

// ── Routes ────────────────────────────────────────────
let allRoutes = [];
function renderRoutes() {{
  const rd = ana.route_detection;
  if (!rd || !rd.total) return;
  document.getElementById('routes-title').textContent = `🛣️ Routes (${{rd.total}} total)`;
  allRoutes = rd.routes || [];
  renderRouteRows(allRoutes);
}}
function renderRouteRows(rows) {{
  document.getElementById('routes-body').innerHTML = rows.slice(0,200).map(r => `
    <tr>
      <td><span class="method method-${{r.method}}">${{r.method}}</span></td>
      <td><code>${{r.path}}</code></td>
      <td><code style="color:var(--muted)">${{r.file}}</code></td>
      <td><span class="badge badge-muted">${{r.framework}}</span></td>
    </tr>`).join('');
  if (rows.length > 200)
    document.getElementById('routes-body').innerHTML +=
      `<tr><td colspan="4" style="color:var(--muted);text-align:center">… ${{rows.length-200}} more</td></tr>`;
}}
function filterRoutes() {{
  const q = document.getElementById('route-search').value.toLowerCase();
  renderRouteRows(q ? allRoutes.filter(r =>
    r.path.toLowerCase().includes(q) || r.file.toLowerCase().includes(q) ||
    r.method.toLowerCase().includes(q) || r.framework.toLowerCase().includes(q)
  ) : allRoutes);
}}

// ── Parameters ────────────────────────────────────────
function renderParams() {{
  const pt = ana.parameter_tracking;
  if (!pt) return;
  document.getElementById('params-title').textContent =
    `🔑 Parameter Tracking (${{pt.env_var_count}} env vars)`;
  const undoc = pt.undocumented_env_vars || [];
  const grid = document.getElementById('params-grid');
  grid.innerHTML = `
    <div class="card">
      <div class="card-title"><span class="icon">🌍</span> Env Variables (${{pt.env_var_count}})</div>
      ${{undoc.length ? `<div class="badge badge-red" style="margin-bottom:10px">⚠ ${{undoc.length}} undocumented</div>` : '<div class="badge badge-green" style="margin-bottom:10px">✓ All documented</div>'}}
      <div style="max-height:300px;overflow-y:auto">
        ${{(pt.env_vars||[]).slice(0,40).map(v => `
          <div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--border)">
            <code style="color:${{v.documented?'var(--green)':'var(--red)'}}">${{v.var}}</code>
            <div style="display:flex;gap:8px;align-items:center">
              <span style="color:var(--muted);font-size:11px">${{v.referenced_in.length}} file${{v.referenced_in.length!==1?'s':''}}</span>
              <span class="badge ${{v.documented?'badge-green':'badge-red'}}">${{v.documented?'✓ docs':'✗ undoc'}}</span>
            </div>
          </div>`).join('')}}
      </div>
    </div>
    <div class="card">
      <div class="card-title"><span class="icon">⚙️</span> CLI Flags (${{(pt.cli_flags||[]).length}})</div>
      ${{(pt.cli_flags||[]).length === 0 ? '<p style="color:var(--muted)">No CLI flags detected.</p>' :
        (pt.cli_flags||[]).map(f => `
          <div style="padding:5px 0;border-bottom:1px solid var(--border)">
            <code>--${{f.flag}}</code>
            <span style="color:var(--muted);font-size:11px;margin-left:8px">${{(f.defined_in||[]).join(', ')}}</span>
          </div>`).join('')
      }}
    </div>`;
}}

// ── Hidden complexity ─────────────────────────────────
function renderComplexity() {{
  const hc = ana.hidden_complexity;
  if (!hc) return;
  const el = document.getElementById('complexity-list');
  const findings = hc.findings || [];
  if (!findings.length) {{
    el.innerHTML = '<div class="card"><p style="color:var(--green)">✅ No significant complexity signals detected.</p></div>';
    return;
  }}
  el.innerHTML = findings.map(f => {{
    const cls = f.severity==='high'?'badge-red':f.severity==='medium'?'badge-yellow':'badge-muted';
    const examples = (f.examples||[]).slice(0,3);
    return `<div class="card" style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
        <div style="flex:1">
          <div style="font-weight:600;margin-bottom:4px">${{f.description}}</div>
          <div style="color:var(--muted);font-size:12px">${{f.file_count}} file(s) · ${{f.total_occurrences}} occurrence(s)</div>
        </div>
        <span class="badge ${{cls}}">${{f.severity}}</span>
      </div>
      ${{examples.length ? `<div style="margin-top:10px">
        ${{examples.map(e => `<div style="font-size:12px;color:var(--muted);padding:3px 0">
          <code>${{e.file||e.path||''}}</code>${{e.lines?' · '+e.lines+' lines':e.first_line?' · line '+e.first_line:''}}
        </div>`).join('')}}
      </div>` : ''}}
    </div>`;
  }}).join('');
}}

// ── Dependencies ──────────────────────────────────────
let allDeps = [];
function renderDeps() {{
  const di = ana.dependency_impact;
  if (!di || !di.deps?.length) return;
  document.getElementById('deps-title').textContent =
    `📦 Dependency Impact (${{di.total}} declared, ${{di.referenced_count}} referenced)`;
  allDeps = di.deps || [];
  renderDepRows(allDeps);
}}
function renderDepRows(rows) {{
  document.getElementById('deps-body').innerHTML = rows.slice(0,100).map(d => {{
    const cls = d.impact==='high'?'badge-red':d.impact==='medium'?'badge-yellow':'badge-muted';
    return `<tr>
      <td><code>${{d.name}}</code></td>
      <td><code style="color:var(--muted)">${{d.version}}</code></td>
      <td><span class="badge ${{cls}}">${{d.impact}}</span></td>
      <td>${{d.file_count}}</td>
      <td style="color:var(--muted);font-size:11px">${{(d.dirs_affected||[]).slice(0,3).join(', ')}}</td>
    </tr>`;
  }}).join('');
}}
function filterDeps() {{
  const q = document.getElementById('dep-search').value.toLowerCase();
  renderDepRows(q ? allDeps.filter(d => d.name.toLowerCase().includes(q)) : allDeps);
}}

// ── Naming ────────────────────────────────────────────
function renderNaming() {{
  const nm = ana.naming_consistency;
  if (!nm) return;
  const scopes = [['files','Files'],['dirs','Directories'],['functions','Functions'],['classes','Classes']];
  const issues = nm.issues || [];
  document.getElementById('naming-content').innerHTML = `
    <div class="card">
      ${{issues.length
        ? issues.map(i=>`<div class="friction-item"><div class="friction-icon">⚠️</div><div>${{i}}</div></div>`).join('')
        : '<p style="color:var(--green)">✅ Consistent naming across the codebase.</p>'
      }}
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-top:16px">
        ${{scopes.map(([key,lbl]) => {{
          const s = nm[key]||{{}};
          if (!s.dominant) return '';
          const pct = 100 - (s.clash_pct||0);
          return `<div>
            <div style="color:var(--muted);font-size:12px;margin-bottom:6px">${{lbl}}</div>
            <code style="font-size:13px">${{s.dominant}}</code>
            <div class="bar-wrap" style="margin-top:6px">
              <div class="bar" style="width:${{pct}}%;background:${{pct>90?'var(--green)':pct>75?'var(--yellow)':'var(--red)'}}"></div>
            </div>
            <div style="font-size:11px;color:var(--muted);margin-top:3px">${{pct}}% consistent</div>
          </div>`;
        }}).join('')}}
      </div>
    </div>`;
}}

// ── Flow trace ────────────────────────────────────────
function renderFlow() {{
  const ft = ana.flow_trace;
  if (!ft || !ft.node_count) return;
  document.getElementById('flow-title').textContent =
    `🔀 Flow Trace (${{ft.node_count}} nodes, ${{ft.edge_count}} edges)`;
  const graph = ft.graph || {{}};
  const entries = ft.entry_points_traced || [];
  const most = ft.most_imported || [];
  document.getElementById('flow-content').innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div class="card">
        <div class="card-title">Import chains from entry points</div>
        ${{entries.map(ep => {{
          const children = graph[ep] || [];
          return `<div style="margin-bottom:12px">
            <div style="font-weight:600"><code>${{ep}}</code></div>
            <div style="margin-top:6px;padding-left:16px;border-left:2px solid var(--border)">
              ${{children.slice(0,6).map(c=>`<div style="padding:3px 0;color:var(--muted);font-size:12px">
                <code>${{c}}</code>
              </div>`).join('')}}
              ${{children.length>6?`<div style="color:var(--muted);font-size:11px">… ${{children.length-6}} more</div>`:''}}
            </div>
          </div>`;
        }}).join('')}}
      </div>
      <div class="card">
        <div class="card-title">Most imported modules</div>
        ${{most.map(m => `
          <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)">
            <code style="font-size:12px">${{m.file}}</code>
            <span class="badge badge-blue">${{m.import_count}}×</span>
          </div>`).join('')}}
      </div>
    </div>`;
}}

// ── Symbol table ──────────────────────────────────────
let allSyms = [];
function renderSymbols() {{
  if (!S.total_symbols) {{
    document.getElementById('sym-title').textContent = '🔗 Symbol Graph (run with --symbols to enable)';
    document.getElementById('sym-canvas').style.display = 'none';
    document.getElementById('sym-search').style.display = 'none';
    document.getElementById('sym-table').style.display = 'none';
    return;
  }}
  document.getElementById('sym-title').textContent =
    `🔗 Cross-File Symbol Map (${{S.total_symbols}} symbols)`;
  allSyms = S.symbols || [];
  drawSymbolGraph();
  renderSymbolRows(allSyms);
}}
function kindColor(k) {{
  return k==='function'?'#3fb950':k==='class'?'#58a6ff':k==='constant'?'#d29922':k==='variable'?'#ffa657':'#bc8cff';
}}
function renderSymbolRows(rows) {{
  document.getElementById('sym-body').innerHTML = rows.slice(0,100).map(s => `
    <tr>
      <td style="font-weight:600;color:${{kindColor(s.kind)}}">${{s.name}}</td>
      <td><span class="badge badge-muted">${{s.kind}}</span></td>
      <td><code style="font-size:11px">${{s.defined_in}}</code></td>
      <td>
        <span class="badge badge-blue">${{s.file_count}} file${{s.file_count!==1?'s':''}}</span>
        <div style="font-size:11px;color:var(--muted);margin-top:3px;max-width:280px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
          ${{s.used_in.slice(0,3).join(', ')}}${{s.used_in.length>3?' …':''}}
        </div>
      </td>
      <td style="color:var(--muted)">${{s.use_count}}</td>
    </tr>`).join('');
  if (rows.length > 100)
    document.getElementById('sym-body').innerHTML +=
      `<tr><td colspan="5" style="color:var(--muted);text-align:center">… ${{rows.length-100}} more</td></tr>`;
}}
function filterSymbols() {{
  const q = document.getElementById('sym-search').value.toLowerCase();
  renderSymbolRows(q ? allSyms.filter(s =>
    s.name.toLowerCase().includes(q) || s.defined_in.toLowerCase().includes(q) ||
    s.kind.toLowerCase().includes(q)
  ) : allSyms);
}}

// ── Symbol canvas (force-directed graph) ─────────────
function drawSymbolGraph() {{
  const canvas = document.getElementById('sym-canvas');
  if (!canvas || !allSyms.length) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  canvas.width = W * dpr; canvas.height = H * dpr;
  ctx.scale(dpr, dpr);

  // Build nodes and edges
  const top = allSyms.slice(0, 60);
  const fileSet = new Set();
  top.forEach(s => {{ fileSet.add(s.defined_in); s.used_in.forEach(f => fileSet.add(f)); }});
  const files = [...fileSet];
  const fileIdx = Object.fromEntries(files.map((f,i) => [f,i]));
  const symNodes = top.map((s,i) => ({{
    id: 'sym_'+i, label: s.name, kind: s.kind, type: 'sym',
    x: W/2 + (Math.random()-.5)*W*.6,
    y: H/2 + (Math.random()-.5)*H*.6,
    vx:0, vy:0, r: 5+Math.min(s.file_count*2,14),
  }}));
  const fileNodes = files.map((f,i) => {{
    const short = f.split('/').pop();
    return {{
      id: 'file_'+i, label: short, fullpath: f, type: 'file',
      x: W/2 + (Math.random()-.5)*W*.8,
      y: H/2 + (Math.random()-.5)*H*.8,
      vx:0, vy:0, r: 6,
    }};
  }});
  const nodes = [...symNodes, ...fileNodes];
  const nodeIdx = Object.fromEntries(nodes.map((n,i) => [n.id,i]));
  const edges = [];
  top.forEach((s,si) => {{
    const src = nodeIdx['sym_'+si];
    [s.defined_in, ...s.used_in].forEach(f => {{
      const fi = fileSet.has(f) ? nodeIdx['file_'+fileIdx[f]] : -1;
      if (fi>=0) edges.push([src,fi]);
    }});
  }});

  // Pan/zoom state
  let tx=0, ty=0, scale=1;
  let dragging=null, lastMX=0, lastMY=0;

  canvas.addEventListener('mousedown', e => {{
    const [mx,my] = mouse(e);
    dragging = hitNode(mx,my) ?? 'pan';
    lastMX=mx; lastMY=my;
  }});
  canvas.addEventListener('mousemove', e => {{
    const [mx,my] = mouse(e);
    if (dragging==='pan') {{ tx+=mx-lastMX; ty+=my-lastMY; }}
    else if (dragging) {{ dragging.x=mx; dragging.y=my; dragging.vx=0; dragging.vy=0; }}
    else showTooltip(e, hitNode(mx,my));
    lastMX=mx; lastMY=my;
  }});
  canvas.addEventListener('mouseup', () => dragging=null);
  canvas.addEventListener('mouseleave', () => {{ dragging=null; hideTooltip(); }});
  canvas.addEventListener('wheel', e => {{
    e.preventDefault();
    const [mx,my] = mouse(e);
    const ds = e.deltaY<0?1.1:.9;
    tx = mx-(mx-tx)*ds; ty = my-(my-ty)*ds; scale*=ds;
  }}, {{passive:false}});

  function mouse(e) {{
    const r = canvas.getBoundingClientRect();
    return [e.clientX-r.left, e.clientY-r.top];
  }}
  function toWorld(x,y) {{ return [(x-tx)/scale, (y-ty)/scale]; }}
  function hitNode(mx,my) {{
    const [wx,wy] = toWorld(mx,my);
    return nodes.find(n => Math.hypot(n.x-wx, n.y-wy) < n.r+4) ?? null;
  }}
  const tooltip = document.getElementById('sym-tooltip');
  function showTooltip(e, n) {{
    if (!n) {{ hideTooltip(); return; }}
    tooltip.style.display='block';
    tooltip.style.left=(e.clientX+14)+'px'; tooltip.style.top=(e.clientY-10)+'px';
    if (n.type==='sym') {{
      const sym = top[parseInt(n.id.split('_')[1])];
      tooltip.innerHTML = `<strong>${{sym.name}}</strong> <em style="color:var(--muted)">${{sym.kind}}</em><br>
        <span style="color:var(--muted)">Defined: ${{sym.defined_in}}</span><br>
        Used in ${{sym.file_count}} file(s) · ${{sym.use_count}} refs`;
    }} else {{
      tooltip.innerHTML = `<code>${{n.fullpath}}</code>`;
    }}
  }}
  function hideTooltip() {{ tooltip.style.display='none'; }}

  // Simulation
  let frame=0;
  function simulate() {{
    const K=0.05, REPEL=800, DAMP=0.85, LEN=120;
    nodes.forEach(n => {{ n.fx=0; n.fy=0; }});
    // Repulsion
    for(let i=0;i<nodes.length;i++) for(let j=i+1;j<nodes.length;j++) {{
      const dx=nodes[j].x-nodes[i].x, dy=nodes[j].y-nodes[i].y;
      const d=Math.max(1,Math.hypot(dx,dy));
      const f=REPEL/(d*d);
      nodes[i].fx-=f*dx/d; nodes[i].fy-=f*dy/d;
      nodes[j].fx+=f*dx/d; nodes[j].fy+=f*dy/d;
    }}
    // Attraction (edges)
    edges.forEach(([a,b]) => {{
      const dx=nodes[b].x-nodes[a].x, dy=nodes[b].y-nodes[a].y;
      const d=Math.max(1,Math.hypot(dx,dy));
      const f=K*(d-LEN);
      nodes[a].fx+=f*dx/d; nodes[a].fy+=f*dy/d;
      nodes[b].fx-=f*dx/d; nodes[b].fy-=f*dy/d;
    }});
    // Centre gravity
    nodes.forEach(n => {{
      n.fx += (W/2-n.x)*0.005; n.fy += (H/2-n.y)*0.005;
      if (dragging && n===dragging) return;
      n.vx=(n.vx+n.fx)*DAMP; n.vy=(n.vy+n.fy)*DAMP;
      n.x+=n.vx; n.y+=n.vy;
    }});
  }}

  function draw() {{
    ctx.clearRect(0,0,W,H);
    ctx.save(); ctx.translate(tx,ty); ctx.scale(scale,scale);
    // Edges
    ctx.strokeStyle='rgba(48,54,61,0.8)'; ctx.lineWidth=1;
    edges.forEach(([a,b]) => {{
      ctx.beginPath(); ctx.moveTo(nodes[a].x,nodes[a].y); ctx.lineTo(nodes[b].x,nodes[b].y); ctx.stroke();
    }});
    // Nodes
    nodes.forEach(n => {{
      ctx.beginPath(); ctx.arc(n.x,n.y,n.r,0,Math.PI*2);
      ctx.fillStyle = n.type==='sym' ? kindColor(n.kind) : '#30363d';
      ctx.fill();
      ctx.strokeStyle=n.type==='sym'?'rgba(255,255,255,.15)':'rgba(255,255,255,.05)';
      ctx.lineWidth=1; ctx.stroke();
      // Label
      if (n.r>7 || scale>1.3) {{
        ctx.fillStyle=n.type==='sym'?'#e6edf3':'#8b949e';
        ctx.font = `${{n.type==='sym'?'bold ':''}}${{Math.max(9,n.r*.9)}}px var(--font)`;
        ctx.textAlign='center'; ctx.textBaseline='middle';
        ctx.fillText(n.label.length>16?n.label.slice(0,14)+'…':n.label, n.x, n.y+n.r+9);
      }}
    }});
    ctx.restore();
    if (frame<120) simulate();
    frame++;
    requestAnimationFrame(draw);
  }}
  draw();
}}

// ── Boot ──────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {{
  renderStats();
  renderFriction();
  renderFirstDay();
  renderArch();
  renderEntryPoints();
  renderRoutes();
  renderParams();
  renderComplexity();
  renderDeps();
  renderNaming();
  renderFlow();
  renderSymbols();
}});
</script>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────
# HTTP SERVER
# ──────────────────────────────────────────────────────────────
class _Handler(http.server.BaseHTTPRequestHandler):
    html: str = ""

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            body = self.html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_):
        pass  # silence request logs


def serve(
    report_path: str,
    symbols_path: Optional[str] = None,
    port: int = 7878,
    open_browser: bool = True,
):
    """
    Load reports, build HTML, start server, optionally open browser.
    Blocks until Ctrl-C.
    """
    report_file = Path(report_path)
    if not report_file.exists():
        print(f"Error: report file not found: {report_path}", file=sys.stderr)
        sys.exit(1)

    report = json.loads(report_file.read_text(encoding="utf-8"))

    symbols: Optional[dict] = None
    if symbols_path:
        sp = Path(symbols_path)
        if sp.exists():
            symbols = json.loads(sp.read_text(encoding="utf-8"))
        else:
            print(f"Warning: symbols file not found: {symbols_path}", file=sys.stderr)

    html = _build_html(report, symbols)
    _Handler.html = html

    # Find a free port if default is taken
    import socket
    for attempt in range(20):
        try:
            server = http.server.HTTPServer(("127.0.0.1", port + attempt), _Handler)
            port = port + attempt
            break
        except OSError:
            continue
    else:
        print("Could not find a free port in range 7878–7897", file=sys.stderr)
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    print(f"\n  \033[1m\033[36m◆ repomap dashboard\033[0m  →  \033[4m{url}\033[0m")
    print(f"  \033[2mCtrl-C to stop\033[0m\n")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.shutdown()


# CLI
if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="repomap dashboard — serve an interactive HTML report from a repomap JSON file."
    )
    p.add_argument("report", help="Path to repomap_<slug>.json")
    p.add_argument("--symbols", default=None, metavar="PATH",
                   help="Path to repomap_<slug>_symbols.json (optional)")
    p.add_argument("--port", type=int, default=7878, help="Port to serve on (default: 7878)")
    p.add_argument("--no-open", action="store_true", help="Don't open browser automatically")
    args = p.parse_args()
    serve(args.report, args.symbols, args.port, not args.no_open)
