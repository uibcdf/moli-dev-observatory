"""Static drill-down views for the MOLI Development Observatory."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Callable

MetricsFunction = Callable[[dict[str, Any], int, str], dict[str, Any]]


def slugify(value: str) -> str:
    """Return a stable URL-safe slug for observatory paths."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def repository_slug(repository: str) -> str:
    """Return a stable repository path while preserving owner/name separation."""
    owner, separator, name = repository.partition("/")
    if not separator:
        return slugify(repository)
    return f"{slugify(owner)}--{slugify(name)}"


def subset_dataset(
    dataset: dict[str, Any],
    *,
    layer: str | None = None,
    repository: str | None = None,
) -> dict[str, Any]:
    """Filter one canonical snapshot without changing collection semantics."""
    if layer is not None and repository is not None:
        raise ValueError("Choose either layer or repository, not both.")

    def matches(item: dict[str, Any]) -> bool:
        if repository is not None:
            return item.get("repository") == repository
        if layer is not None:
            return item.get("layer") == layer
        return True

    scope = [item for item in dataset["scope"] if matches(item)]
    repositories = {item["repository"] for item in scope}
    excluded_scope = [
        item for item in dataset.get("excluded_scope", []) if matches(item)
    ]
    return {
        "schema_version": dataset["schema_version"],
        "generated_at": dataset["generated_at"],
        "provenance": dataset["provenance"],
        "scope": scope,
        "excluded_scope": excluded_scope,
        "issues": [
            issue
            for issue in dataset["issues"]
            if issue["repository"] in repositories
        ],
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def dashboard_html(
    *,
    title: str,
    metrics_href: str,
    issues_href: str,
    overview_href: str,
    layer_href_prefix: str,
    repository_href_prefix: str,
    view_kind: str,
    current_layer: str | None = None,
    current_repository: str | None = None,
) -> str:
    """Render one static dashboard consumer."""
    if view_kind not in {"overview", "layer", "repository"}:
        raise ValueError(f"Unsupported dashboard view: {view_kind}")

    escaped_title = html.escape(title)
    breadcrumb_parts: list[str] = []
    if view_kind != "overview":
        breadcrumb_parts.append(
            f'<a href="{html.escape(overview_href)}">Overview</a>'
        )
    if view_kind == "layer" and current_layer:
        breadcrumb_parts.append(html.escape(current_layer))
    if view_kind == "repository" and current_layer and current_repository:
        layer_href = f"{layer_href_prefix}{slugify(current_layer)}/"
        breadcrumb_parts.append(
            f'<a href="{html.escape(layer_href)}">{html.escape(current_layer)}</a>'
        )
        breadcrumb_parts.append(html.escape(current_repository))
    breadcrumb = (
        '<nav class="breadcrumbs">' + " / ".join(breadcrumb_parts) + "</nav>"
        if breadcrumb_parts
        else ""
    )

    config = json.dumps(
        {
            "metricsHref": metrics_href,
            "layerHrefPrefix": layer_href_prefix,
            "repositoryHrefPrefix": repository_href_prefix,
            "viewKind": view_kind,
        }
    )

    layer_section = (
        '<section><h2>Layers</h2><div class="link-grid" id="layer-links"></div></section>'
        if view_kind == "overview"
        else ""
    )

    if view_kind == "overview":
        comparison_section = (
            '<section><h2>Net change by platform layer</h2>'
            '<canvas id="comparison"></canvas></section>'
        )
    elif view_kind == "layer":
        comparison_section = (
            '<section><h2>Net change by repository</h2>'
            '<canvas id="comparison"></canvas></section>'
        )
    else:
        comparison_section = ""

    repository_section = (
        '<section><h2>Repositories</h2>'
        '<p class="muted">Click a repository to drill down. Click a numeric column '
        'to sort; click again to reverse the order. Total, Open and Closed describe '
        'the current all-time issue state. Δ backlog is the net change within the '
        'selected time window.</p>'
        '<table><thead><tr><th>Repository</th><th>Layer</th>'
        '<th><button class="sort-button" data-sort="total">Total'
        '<span class="sort-indicator"></span></button></th>'
        '<th><button class="sort-button" data-sort="current_open">Open'
        '<span class="sort-indicator"></span></button></th>'
        '<th><button class="sort-button" data-sort="current_closed">Closed'
        '<span class="sort-indicator"></span></button></th>'
        '<th><button class="sort-button" data-sort="net_change">Δ backlog'
        '<span class="sort-indicator">↓</span></button></th>'
        '</tr></thead><tbody id="repos"></tbody></table></section>'
        if view_kind != "repository"
        else ""
    )

    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{escaped_title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>body{{font:15px system-ui;margin:auto;max-width:1200px;padding:24px;background:#0f172a;color:#e2e8f0}}a{{color:#7dd3fc}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}.card,section{{background:#111827;border:1px solid #334155;border-radius:12px;padding:16px;margin:14px 0}}.n{{font-size:28px;font-weight:700}}canvas{{max-height:340px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:7px;border-bottom:1px solid #334155;text-align:right}}td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){{text-align:left}}.muted{{color:#94a3b8}}.breadcrumbs{{margin-bottom:14px;color:#94a3b8}}.link-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}.link-card{{display:block;padding:12px;border:1px solid #334155;border-radius:10px;text-decoration:none;background:#0f172a}}.link-card:hover{{border-color:#7dd3fc}}.sort-button{{background:none;border:0;color:inherit;font:inherit;font-weight:700;cursor:pointer;padding:0}}.sort-button:hover,.sort-button:focus{{text-decoration:underline}}.sort-indicator{{display:inline-block;min-width:1em;margin-left:3px;color:#7dd3fc}}</style></head><body>
{breadcrumb}<h1>{escaped_title}</h1><p class="muted" id="meta">Loading…</p><div class="grid" id="cards"></div>
{layer_section}
<section><h2>Daily issue flow</h2><canvas id="flow"></canvas></section>
<section><h2>7-day moving average</h2><canvas id="ma"></canvas></section>
<section><h2>Cumulative net backlog change</h2><canvas id="net"></canvas></section>
{comparison_section}
<section><h2>Open issue age</h2><canvas id="age"></canvas></section>
{repository_section}
<section><h2>Data</h2><p><a href="{html.escape(issues_href)}">Canonical normalized issue snapshot</a> · <a href="{html.escape(metrics_href)}">Derived metrics for this view</a></p><p class="muted">V1 does not reconstruct reopen cycles. JSON remains independent of this renderer and can feed Grafana or another backend later.</p></section>
<script>
const CONFIG={config};
const C=['#38bdf8','#34d399','#f59e0b','#a78bfa'];
let repoRows=[];let repoSort={{key:'net_change',direction:'desc'}};
function slug(value){{return value.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}}
function repoSlug(value){{const parts=value.split('/',2);return parts.length===2?slug(parts[0])+'--'+slug(parts[1]):slug(value)}}
function line(id,labels,sets){{new Chart(document.getElementById(id),{{type:'line',data:{{labels:labels,datasets:sets}},options:{{responsive:true,interaction:{{mode:'index',intersect:false}},scales:{{y:{{beginAtZero:true}}}}}}}})}}
function bar(id,labels,data){{new Chart(document.getElementById(id),{{type:'bar',data:{{labels:labels,datasets:[{{data:data,backgroundColor:C}}]}},options:{{responsive:true,plugins:{{legend:{{display:false}}}}}}}})}}
function repoHref(repository){{return CONFIG.repositoryHrefPrefix+repoSlug(repository)+'/'}} 
function renderRepositories(){{
  const factor=repoSort.direction==='asc'?1:-1;
  const rows=[...repoRows].sort(function(a,b){{const delta=(a[repoSort.key]-b[repoSort.key])*factor;return delta||a.repository.localeCompare(b.repository)}});
  repos.innerHTML=rows.map(function(x){{return '<tr><td><a href="'+repoHref(x.repository)+'">'+x.repository+'</a></td><td>'+x.layer+'</td><td>'+x.total+'</td><td>'+x.current_open+'</td><td>'+x.current_closed+'</td><td>'+x.net_change+'</td></tr>'}}).join('');
  document.querySelectorAll('[data-sort]').forEach(function(button){{const active=button.dataset.sort===repoSort.key;button.setAttribute('aria-sort',active?(repoSort.direction==='asc'?'ascending':'descending'):'none');button.querySelector('.sort-indicator').textContent=active?(repoSort.direction==='asc'?'↑':'↓'):''}})
}}
function configureRepositorySorting(){{document.querySelectorAll('[data-sort]').forEach(function(button){{button.addEventListener('click',function(){{const key=button.dataset.sort;if(repoSort.key===key){{repoSort.direction=repoSort.direction==='desc'?'asc':'desc'}}else{{repoSort={{key:key,direction:'desc'}}}}renderRepositories()}})}})}}
fetch(CONFIG.metricsHref).then(function(r){{return r.json()}}).then(function(m){{
  meta.textContent='Generated '+m.generated_at+' · '+m.window.start+' → '+m.window.end+' · '+m.timezone+' · '+m.summary.excluded_repositories+' excluded';
  if(CONFIG.viewKind==='repository'){{
    const row=m.repositories[0]||{{total:0,current_open:0,current_closed:0,net_change:0,opened:0,closed:0}};
    cards.innerHTML=[['Total',row.total],['Open',row.current_open],['Closed',row.current_closed],['Δ backlog',row.net_change],['Opened in window',row.opened],['Closed in window',row.closed]].map(function(x){{return '<div class="card"><div class="muted">'+x[0]+'</div><div class="n">'+x[1]+'</div></div>'}}).join('');
  }}else{{
    cards.innerHTML=[['Opened',m.summary.opened],['Closed',m.summary.closed],['Net change',m.summary.net_change],['Currently open',m.summary.current_open],['Repositories',m.summary.repositories],['Excluded',m.summary.excluded_repositories]].map(function(x){{return '<div class="card"><div class="muted">'+x[0]+'</div><div class="n">'+x[1]+'</div></div>'}}).join('');
  }}
  const labels=m.daily.map(function(x){{return x.date}});
  line('flow',labels,[{{label:'Opened',data:m.daily.map(function(x){{return x.opened}}),borderColor:C[0]}},{{label:'Closed',data:m.daily.map(function(x){{return x.closed}}),borderColor:C[1]}}]);
  line('ma',labels,[{{label:'Opened/day',data:m.daily.map(function(x){{return x.opened_ma7}}),borderColor:C[0]}},{{label:'Closed/day',data:m.daily.map(function(x){{return x.closed_ma7}}),borderColor:C[1]}}]);
  line('net',labels,[{{label:'Net backlog change',data:m.daily.map(function(x){{return x.cumulative_net_change}}),borderColor:C[2]}}]);
  if(CONFIG.viewKind==='overview'){{bar('comparison',m.layers.map(function(x){{return x.layer}}),m.layers.map(function(x){{return x.net_change}}))}}
  if(CONFIG.viewKind==='layer'){{bar('comparison',m.repositories.map(function(x){{return x.repository}}),m.repositories.map(function(x){{return x.net_change}}))}}
  bar('age',m.issue_age.map(function(x){{return x.bucket}}),m.issue_age.map(function(x){{return x.count}}));
  if(CONFIG.viewKind==='overview'){{
    const layerLinks=document.getElementById('layer-links');
    layerLinks.innerHTML=m.layers.map(function(x){{return '<a class="link-card" href="'+CONFIG.layerHrefPrefix+slug(x.layer)+'/"><strong>'+x.layer+'</strong><br><span class="muted">Δ backlog '+x.net_change+' · '+x.current_open+' open</span></a>'}}).join('');
  }}
  if(CONFIG.viewKind!=='repository'){{repoRows=m.repositories;configureRepositorySorting();renderRepositories()}}
}})
</script></body></html>"""


def generate_drilldowns(
    output: Path,
    dataset: dict[str, Any],
    *,
    days: int,
    timezone_name: str,
    metrics_fn: MetricsFunction,
) -> None:
    """Generate Overview, layer and repository dashboards from one snapshot."""
    (output / "index.html").write_text(
        dashboard_html(
            title="MOLI Development Observatory",
            metrics_href="metrics.json",
            issues_href="issues.json",
            overview_href="./",
            layer_href_prefix="layers/",
            repository_href_prefix="repositories/",
            view_kind="overview",
        ),
        encoding="utf-8",
    )

    layers = sorted({item["layer"] for item in dataset["scope"]})
    for layer in layers:
        layer_dataset = subset_dataset(dataset, layer=layer)
        layer_output = output / "layers" / slugify(layer)
        layer_output.mkdir(parents=True, exist_ok=True)
        write_json(
            layer_output / "metrics.json",
            metrics_fn(layer_dataset, days, timezone_name),
        )
        (layer_output / "index.html").write_text(
            dashboard_html(
                title=f"MOLI Development Observatory — {layer}",
                metrics_href="metrics.json",
                issues_href="../../issues.json",
                overview_href="../../",
                layer_href_prefix="../",
                repository_href_prefix="../../repositories/",
                view_kind="layer",
                current_layer=layer,
            ),
            encoding="utf-8",
        )

    for entry in dataset["scope"]:
        repository = entry["repository"]
        layer = entry["layer"]
        repository_dataset = subset_dataset(dataset, repository=repository)
        repository_output = output / "repositories" / repository_slug(repository)
        repository_output.mkdir(parents=True, exist_ok=True)
        write_json(
            repository_output / "metrics.json",
            metrics_fn(repository_dataset, days, timezone_name),
        )
        (repository_output / "index.html").write_text(
            dashboard_html(
                title=f"MOLI Development Observatory — {repository}",
                metrics_href="metrics.json",
                issues_href="../../issues.json",
                overview_href="../../",
                layer_href_prefix="../../layers/",
                repository_href_prefix="../",
                view_kind="repository",
                current_layer=layer,
                current_repository=repository,
            ),
            encoding="utf-8",
        )
