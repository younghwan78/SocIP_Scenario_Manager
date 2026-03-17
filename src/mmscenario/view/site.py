"""GitHub Pages static site builder.

Scans scenarios/usecase/**/*.yaml, renders each to docs/scenarios/...,
and generates docs/index.html with project/scenario navigation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Files to skip by default (authoring artifacts, not final scenarios)
_SKIP_SUFFIXES = ("_compact",)
_SKIP_PREFIXES = ("draft_",)


def build_site(
    usecase_dir: Path,
    output_dir: Path,
    static_dir: Path,
    include_all: bool = False,
) -> int:
    """Scan usecase_dir, render all scenarios, generate index.html.

    Returns exit code (0 = success, 1 = partial failure).
    """
    from mmscenario.dag import ScenarioPipeline
    from mmscenario.schema import load_full_scenario
    from mmscenario.view import ViewRenderer
    from mmscenario.view.renderer import slugify

    yaml_files = sorted(usecase_dir.rglob("*.yaml"))
    if not include_all:
        yaml_files = [
            f for f in yaml_files
            if not any(f.stem.endswith(s) for s in _SKIP_SUFFIXES)
            and not any(f.stem.startswith(p) for p in _SKIP_PREFIXES)
        ]

    renderer = ViewRenderer(static_dir=static_dir)
    projects: dict[str, dict] = {}
    errors: list[str] = []

    for yaml_path in yaml_files:
        rel = yaml_path.relative_to(usecase_dir)
        if len(rel.parts) == 1:
            project_id   = "__root__"
            project_name = "General"
        else:
            project_id   = rel.parts[0]
            project_name = project_id.replace("_", " ").replace("-", " ").title()

        try:
            scenario = load_full_scenario(yaml_path)
        except Exception as exc:
            logger.warning("Skipping %s: %s", yaml_path.name, exc)
            errors.append(yaml_path.name)
            continue

        sc = scenario.scenario
        scenario_slug = slugify(sc.name)

        if project_id == "__root__":
            html_rel = f"scenarios/{scenario_slug}.html"
        else:
            html_rel = f"scenarios/{project_id}/{scenario_slug}.html"

        html_path = output_dir / html_rel
        pipeline = ScenarioPipeline(scenario.pipeline)
        renderer.render(scenario, pipeline, output_path=html_path)
        logger.info("  rendered: %s → %s", yaml_path.name, html_rel)

        if project_id not in projects:
            projects[project_id] = {
                "id":        project_id,
                "name":      project_name,
                "scenarios": [],
            }
        desc = (sc.description or "").strip()
        projects[project_id]["scenarios"].append({
            "id":          scenario_slug,
            "name":        sc.name,
            "category":    sc.category,
            "version":     sc.version,
            "description": desc[:220] + ("…" if len(desc) > 220 else ""),
            "risks":       [
                {"severity": r.severity.value, "description": r.description}
                for r in sc.risks
            ],
            "html_path":   html_rel,
        })

    # Build ordered project list: __root__ first, then alphabetical
    project_list: list[dict] = []
    root = projects.pop("__root__", None)
    if root:
        project_list.append(root)
    project_list.extend(sorted(projects.values(), key=lambda p: p["id"]))

    manifest = {"projects": project_list}
    index_html = _build_index_html(manifest)
    index_path = output_dir / "index.html"
    index_path.write_text(index_html, encoding="utf-8")

    total = sum(len(p["scenarios"]) for p in project_list)
    logger.info("Site built: %d scenario(s) across %d project(s) → %s",
                total, len(project_list), output_dir.resolve())
    if errors:
        logger.warning("Skipped %d file(s): %s", len(errors), ", ".join(errors))
    return 0 if not errors else 1


# ---------------------------------------------------------------------------
# index.html generator
# ---------------------------------------------------------------------------

def _build_index_html(manifest: dict) -> str:
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2)
    total = sum(len(p["scenarios"]) for p in manifest["projects"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Multimedia Scenario DB</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#f0f2f5;color:#1a2a4a;min-height:100vh;display:flex;flex-direction:column}}

/* ── Header ── */
header{{background:linear-gradient(135deg,#1a2a4a 0%,#2a3f6a 100%);
  color:#fff;padding:18px 28px;display:flex;align-items:center;gap:16px;
  box-shadow:0 2px 8px rgba(0,0,0,0.25)}}
header h1{{font-size:20px;font-weight:700;letter-spacing:0.3px}}
header .sub{{font-size:12px;color:#a8c0e8;margin-top:2px}}
header .spacer{{flex:1}}
header a{{color:#a8c0e8;font-size:12px;text-decoration:none;
  border:1px solid rgba(168,192,232,0.4);border-radius:4px;padding:4px 10px}}
header a:hover{{background:rgba(255,255,255,0.1)}}

/* ── Layout ── */
.layout{{display:flex;flex:1;min-height:0}}

/* ── Sidebar ── */
.sidebar{{width:220px;min-width:180px;background:#fff;
  border-right:1px solid #dde3ec;padding:16px 12px;
  display:flex;flex-direction:column;gap:4px;overflow-y:auto}}
.sidebar-title{{font-size:10px;font-weight:700;color:#888;
  letter-spacing:0.8px;text-transform:uppercase;padding:4px 8px 8px}}
.proj-btn{{width:100%;text-align:left;background:none;border:none;cursor:pointer;
  padding:8px 10px;border-radius:6px;font-size:13px;color:#2c3e50;
  display:flex;align-items:center;justify-content:space-between;
  transition:background 0.12s}}
.proj-btn:hover{{background:#eef2f8}}
.proj-btn.active{{background:#ebf5fb;color:#1a6fa8;font-weight:600}}
.proj-btn .count{{font-size:11px;color:#aaa;background:#f0f2f5;
  border-radius:10px;padding:1px 7px;min-width:22px;text-align:center}}
.proj-btn.active .count{{background:#d6eaf8;color:#1a6fa8}}

/* ── Main content ── */
.content{{flex:1;padding:24px 28px;overflow-y:auto}}
.content-header{{margin-bottom:20px}}
.content-header h2{{font-size:18px;font-weight:700;color:#1a2a4a}}
.content-header .hint{{font-size:12px;color:#888;margin-top:3px}}
.scenario-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));
  gap:16px}}

/* ── Scenario card ── */
.card{{background:#fff;border-radius:10px;border:1px solid #dde3ec;
  padding:18px 20px;display:flex;flex-direction:column;gap:10px;
  transition:box-shadow 0.15s,transform 0.15s;cursor:pointer;text-decoration:none;color:inherit}}
.card:hover{{box-shadow:0 4px 18px rgba(26,42,74,0.12);transform:translateY(-2px)}}
.card-top{{display:flex;align-items:flex-start;gap:8px}}
.card-name{{font-size:15px;font-weight:700;color:#1a2a4a;line-height:1.3;flex:1}}
.card-ver{{font-size:10px;color:#888;background:#f4f6f9;border-radius:3px;
  padding:2px 6px;white-space:nowrap;align-self:flex-start;margin-top:2px}}
.cat-badge{{display:inline-block;font-size:10px;font-weight:600;
  border-radius:4px;padding:2px 8px;color:#fff}}
.card-desc{{font-size:12px;color:#555;line-height:1.55;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
.risks{{display:flex;flex-direction:column;gap:4px}}
.risk{{font-size:11px;padding:3px 8px;border-radius:4px;line-height:1.4}}
.risk-high   {{background:#fdecea;color:#c0392b;border-left:3px solid #e74c3c}}
.risk-medium {{background:#fef9e7;color:#9a6a00;border-left:3px solid #f39c12}}
.risk-low    {{background:#eafaf1;color:#1e6f42;border-left:3px solid #27ae60}}
.card-footer{{display:flex;align-items:center;justify-content:flex-end;
  border-top:1px solid #eef0f4;padding-top:10px;margin-top:2px}}
.view-btn{{font-size:12px;font-weight:600;color:#1a6fa8;
  background:#ebf5fb;border:none;border-radius:5px;padding:5px 14px;cursor:pointer}}
.card:hover .view-btn{{background:#d6eaf8}}

/* Category colors */
.cat-video_recording{{background:#c0622b}}
.cat-video_playback{{background:#2471a3}}
.cat-camera{{background:#117a65}}
.cat-audio{{background:#7d3c98}}
.cat-display{{background:#1e6f42}}
.cat-default{{background:#555e6b}}

/* ── Empty state ── */
.empty{{text-align:center;padding:60px 20px;color:#aaa}}
.empty .icon{{font-size:40px;margin-bottom:10px}}
.empty p{{font-size:13px}}
</style>
</head>
<body>

<header>
  <div>
    <h1>&#127909; Multimedia Scenario DB</h1>
    <div class="sub">Android multimedia pipeline scenario repository</div>
  </div>
  <div class="spacer"></div>
  <a href="https://github.com" id="gh-link" target="_blank" rel="noopener">&#128279; GitHub</a>
</header>

<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-title">Projects</div>
    <div id="proj-list"></div>
  </aside>
  <main class="content">
    <div class="content-header">
      <h2 id="content-title">All Scenarios</h2>
      <div class="hint" id="content-hint">{total} scenario(s) total</div>
    </div>
    <div class="scenario-grid" id="scenario-grid"></div>
  </main>
</div>

<script>
var MANIFEST = {manifest_json};

var _activeProject = '__all__';

// Fix GitHub Pages link
(function() {{
  var m = location.href.match(/https?:\\/\\/[^/]+\\/([^/]+)\\//);
  if (m) document.getElementById('gh-link').href = 'https://github.com/' + m[1];
}})();

function catClass(cat) {{
  var known = ['video_recording','video_playback','camera','audio','display'];
  return 'cat-badge cat-' + (known.indexOf(cat) >= 0 ? cat : 'default');
}}

function catLabel(cat) {{
  return cat ? cat.replace(/_/g,' ') : '';
}}

function renderSidebar() {{
  var all = MANIFEST.projects.reduce(function(n,p){{return n+p.scenarios.length;}},0);
  var html = '<button class="proj-btn' + (_activeProject==='__all__'?' active':'') +
    '" onclick="selectProject(\\'__all__\\')">' +
    '<span>All Projects</span><span class="count">' + all + '</span></button>';
  MANIFEST.projects.forEach(function(p) {{
    html += '<button class="proj-btn' + (_activeProject===p.id?' active':'') +
      '" onclick="selectProject(\\'' + p.id + '\\')">' +
      '<span>' + escHtml(p.name) + '</span>' +
      '<span class="count">' + p.scenarios.length + '</span></button>';
  }});
  document.getElementById('proj-list').innerHTML = html;
}}

function renderScenarios(projectId) {{
  var scenarios = [];
  if (projectId === '__all__') {{
    MANIFEST.projects.forEach(function(p) {{
      p.scenarios.forEach(function(s) {{ scenarios.push({{proj: p.name, s: s}}); }});
    }});
  }} else {{
    var proj = MANIFEST.projects.find(function(p){{return p.id===projectId;}});
    if (proj) proj.scenarios.forEach(function(s) {{ scenarios.push({{proj: proj.name, s: s}}); }});
  }}

  if (!scenarios.length) {{
    document.getElementById('scenario-grid').innerHTML =
      '<div class="empty"><div class="icon">&#128203;</div><p>No scenarios found.</p></div>';
    return;
  }}

  var html = '';
  scenarios.forEach(function(item) {{
    var s = item.s;
    var risksHtml = '';
    (s.risks || []).forEach(function(r) {{
      risksHtml += '<div class="risk risk-' + r.severity + '">' +
        '<strong>' + r.severity.toUpperCase() + '</strong> &nbsp;' + escHtml(r.description) + '</div>';
    }});
    html += '<a class="card" href="' + escHtml(s.html_path) + '" target="_blank" rel="noopener">';
    html += '<div class="card-top">';
    html += '<span class="card-name">' + escHtml(s.name) + '</span>';
    html += '<span class="card-ver">v' + escHtml(s.version) + '</span>';
    html += '</div>';
    html += '<span class="' + catClass(s.category) + '">' + escHtml(catLabel(s.category)) + '</span>';
    if (s.description) html += '<div class="card-desc">' + escHtml(s.description) + '</div>';
    if (risksHtml) html += '<div class="risks">' + risksHtml + '</div>';
    html += '<div class="card-footer"><button class="view-btn">View Diagram &#8594;</button></div>';
    html += '</a>';
  }});
  document.getElementById('scenario-grid').innerHTML = html;
}}

function selectProject(id) {{
  _activeProject = id;
  var proj = id === '__all__' ? null :
    MANIFEST.projects.find(function(p){{return p.id===id;}});
  document.getElementById('content-title').textContent =
    id === '__all__' ? 'All Scenarios' : (proj ? proj.name : id);
  var total = id === '__all__'
    ? MANIFEST.projects.reduce(function(n,p){{return n+p.scenarios.length;}},0)
    : (proj ? proj.scenarios.length : 0);
  document.getElementById('content-hint').textContent = total + ' scenario(s)';
  renderSidebar();
  renderScenarios(id);
}}

function escHtml(s) {{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

// Init
renderSidebar();
renderScenarios('__all__');
</script>
</body>
</html>
"""
