"""Build MOLI issue metrics and a static development-observatory dashboard."""

from __future__ import annotations

import argparse
import json
import os
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

GITHUB_API = "https://api.github.com"
DEFAULT_MOLI_REGISTRY_URL = "https://raw.githubusercontent.com/uibcdf/moli/main/moli.toml"
DEFAULT_SUITE_REGISTRY_URL = "https://raw.githubusercontent.com/uibcdf/molsyssuite/main/suite.toml"
AGE_BUCKETS = (("<7 days", 0, 7), ("7–13 days", 7, 14), ("14–29 days", 14, 30), ("30–59 days", 30, 60), ("60+ days", 60, None))


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "moli-development-observatory"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


def discover_scope(moli: dict[str, Any], suite: dict[str, Any]) -> list[dict[str, str]]:
    scope: dict[str, dict[str, str]] = {
        "uibcdf/moli": {"repository": "uibcdf/moli", "layer": "MOLI", "role": "platform", "source": "moli.toml"}
    }
    for name, item in moli.get("components", {}).items():
        repo = item.get("repository")
        if repo:
            scope[repo] = {"repository": repo, "layer": "MolSysSuite" if name == "molsyssuite" else "MOLI", "role": item.get("role", name), "source": "moli.toml"}
    for item in suite.get("members", []):
        repo, role = item.get("repository"), item.get("role", "member")
        if repo:
            scope[repo] = {"repository": repo, "layer": "Scientific components" if role == "scientific-component" else "Infrastructure", "role": role, "source": "suite.toml"}
    for item in moli.get("support_infrastructure", {}).get("resources", []):
        repo = item.get("repository")
        if repo and repo not in scope:
            scope[repo] = {"repository": repo, "layer": "Infrastructure", "role": item.get("kind", "support-infrastructure"), "source": "moli.toml"}
    return sorted(scope.values(), key=lambda item: (item["layer"], item["repository"]))


class GitHubAPIError(RuntimeError):
    """GitHub API failure with response metadata that callers can classify."""

    def __init__(
        self,
        status: int,
        url: str,
        body: str,
        *,
        rate_limit_remaining: str | None = None,
        rate_limit_reset: str | None = None,
    ) -> None:
        super().__init__(f"GitHub API request failed ({status}) for {url}: {body}")
        self.status = status
        self.url = url
        self.body = body
        self.rate_limit_remaining = rate_limit_remaining
        self.rate_limit_reset = rate_limit_reset

    @property
    def is_rate_limited(self) -> bool:
        return self.rate_limit_remaining == "0" or "rate limit" in self.body.lower()


def api_json(url: str, token: str | None) -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "moli-development-observatory", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise GitHubAPIError(
            exc.code,
            url,
            body,
            rate_limit_remaining=exc.headers.get("X-RateLimit-Remaining"),
            rate_limit_reset=exc.headers.get("X-RateLimit-Reset"),
        ) from exc


def repository_issues(repository: str, token: str | None) -> list[dict[str, Any]]:
    result, page = [], 1
    while True:
        query = urllib.parse.urlencode({"state": "all", "per_page": 100, "page": page, "sort": "created", "direction": "asc"})
        payload = api_json(f"{GITHUB_API}/repos/{repository}/issues?{query}", token)
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected issue payload for {repository}")
        result.extend(item for item in payload if "pull_request" not in item)
        if len(payload) < 100:
            return result
        page += 1


def normalize(repository: str, issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "repository": repository,
        "id": issue.get("id"),
        "number": issue.get("number"),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "state_reason": issue.get("state_reason"),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "closed_at": issue.get("closed_at"),
        "labels": [label.get("name") for label in issue.get("labels", []) if label.get("name")],
        "html_url": issue.get("html_url"),
    }


def collect(
    scope: list[dict[str, str]],
    token: str | None,
    *,
    moli_registry_source: str = "moli.toml",
    molsyssuite_registry_source: str = "uibcdf/molsyssuite:suite.toml",
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    collected_scope: list[dict[str, str]] = []
    excluded_scope: list[dict[str, Any]] = []
    for entry in scope:
        try:
            issues = repository_issues(entry["repository"], token)
        except GitHubAPIError as exc:
            if exc.status == 404:
                excluded_scope.append(
                    {
                        **entry,
                        "status": exc.status,
                        "reason": "inaccessible with the current GitHub credential",
                    }
                )
                continue
            if exc.status == 403 and exc.is_rate_limited:
                reset = (
                    datetime.fromtimestamp(int(exc.rate_limit_reset), UTC)
                    .replace(microsecond=0)
                    .isoformat()
                    if exc.rate_limit_reset
                    else "unknown"
                )
                raise RuntimeError(
                    "GitHub API rate limit exhausted while collecting "
                    f"{entry['repository']}. Reset: {reset}. "
                    "Wait for the anonymous limit to reset or set "
                    "MOLI_OBSERVATORY_GITHUB_TOKEN for a higher authenticated limit."
                ) from exc
            raise
        collected_scope.append(entry)
        records.extend(normalize(entry["repository"], issue) for issue in issues)

    records.sort(key=lambda item: (item.get("created_at") or "", item["repository"], item.get("number") or 0))
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "provenance": {
            "moli_registry": moli_registry_source,
            "molsyssuite_registry": molsyssuite_registry_source,
            "github_api": GITHUB_API,
            "issue_lifecycle_model": "snapshot-v1",
            "limitations": [
                "Historical close/reopen cycles are not reconstructed.",
                "Pull requests are excluded.",
                "Registry repositories inaccessible to the current credential are recorded in excluded_scope and omitted from metrics.",
            ],
        },
        "scope": collected_scope,
        "excluded_scope": excluded_scope,
        "issues": records,
    }


def stamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def local_day(value: str | None, timezone: ZoneInfo) -> date | None:
    parsed = stamp(value)
    return parsed.astimezone(timezone).date() if parsed else None


def ma(values: list[int], window: int = 7) -> list[float]:
    return [round(sum(values[max(0, i - window + 1) : i + 1]) / len(values[max(0, i - window + 1) : i + 1]), 2) for i in range(len(values))]


def metrics(dataset: dict[str, Any], days: int = 90, timezone_name: str = "UTC") -> dict[str, Any]:
    timezone = ZoneInfo(timezone_name)
    generated = stamp(dataset["generated_at"]) or datetime.now(UTC)
    end = generated.astimezone(timezone).date()
    start = end - timedelta(days=days - 1)
    dates = [start + timedelta(days=i) for i in range(days)]
    pos = {day: i for i, day in enumerate(dates)}
    opened, closed = [0] * days, [0] * days
    layer_by_repo = {item["repository"]: item["layer"] for item in dataset["scope"]}
    layers: dict[str, dict[str, int]] = defaultdict(lambda: {"opened": 0, "closed": 0, "current_open": 0})
    repos: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "opened": 0,
            "closed": 0,
            "current_open": 0,
            "current_closed": 0,
            "total": 0,
        }
    )
    for item in dataset["scope"]:
        _ = repos[item["repository"]]
        _ = layers[item["layer"]]
    ages = {label: 0 for label, _low, _high in AGE_BUCKETS}
    now = generated.astimezone(timezone)

    for issue in dataset["issues"]:
        repo, layer = issue["repository"], layer_by_repo.get(issue["repository"], "Unclassified")
        repos[repo]["total"] += 1
        if issue.get("state") == "closed":
            repos[repo]["current_closed"] += 1
        created, done = local_day(issue.get("created_at"), timezone), local_day(issue.get("closed_at"), timezone)
        if created in pos:
            opened[pos[created]] += 1; layers[layer]["opened"] += 1; repos[repo]["opened"] += 1
        if done in pos:
            closed[pos[done]] += 1; layers[layer]["closed"] += 1; repos[repo]["closed"] += 1
        if issue.get("state") == "open":
            layers[layer]["current_open"] += 1; repos[repo]["current_open"] += 1
            created_at = stamp(issue.get("created_at"))
            age = max(0, (now - created_at.astimezone(timezone)).days) if created_at else 0
            for label, low, high in AGE_BUCKETS:
                if age >= low and (high is None or age < high): ages[label] += 1; break

    opened_ma, closed_ma, running, daily = ma(opened), ma(closed), 0, []
    for i, day in enumerate(dates):
        running += opened[i] - closed[i]
        daily.append({"date": day.isoformat(), "opened": opened[i], "closed": closed[i], "opened_ma7": opened_ma[i], "closed_ma7": closed_ma[i], "net_change": opened[i] - closed[i], "cumulative_net_change": running})

    def aggregate(items: dict[str, dict[str, int]], key: str, layer_lookup: bool = False) -> list[dict[str, Any]]:
        rows = []
        for name, values in items.items():
            row: dict[str, Any] = {key: name, **values, "net_change": values["opened"] - values["closed"], "closure_ratio": round(values["closed"] / values["opened"], 3) if values["opened"] else None}
            if layer_lookup: row["layer"] = layer_by_repo.get(name, "Unclassified")
            rows.append(row)
        return sorted(rows, key=lambda row: (-row["net_change"], row[key]))

    return {
        "schema_version": 1,
        "generated_at": dataset["generated_at"],
        "timezone": timezone_name,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "summary": {"opened": sum(opened), "closed": sum(closed), "net_change": sum(opened) - sum(closed), "current_open": sum(issue.get("state") == "open" for issue in dataset["issues"]), "repositories": len(dataset["scope"]), "excluded_repositories": len(dataset.get("excluded_scope", []))},
        "daily": daily,
        "layers": aggregate(layers, "layer"),
        "repositories": aggregate(repos, "repository", True),
        "issue_age": [{"bucket": label, "count": ages[label]} for label, _low, _high in AGE_BUCKETS],
        "excluded_scope": dataset.get("excluded_scope", []),
        "provenance": dataset["provenance"],
    }


def dashboard_html() -> str:
    return '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>MOLI Development Observatory</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>body{font:15px system-ui;margin:auto;max-width:1200px;padding:24px;background:#0f172a;color:#e2e8f0}a{color:#7dd3fc}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}.card,section{background:#111827;border:1px solid #334155;border-radius:12px;padding:16px;margin:14px 0}.n{font-size:28px;font-weight:700}canvas{max-height:340px}table{width:100%;border-collapse:collapse}td,th{padding:7px;border-bottom:1px solid #334155;text-align:right}td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){text-align:left}.muted{color:#94a3b8}.sort-button{background:none;border:0;color:inherit;font:inherit;font-weight:700;cursor:pointer;padding:0}.sort-button:hover,.sort-button:focus{text-decoration:underline}.sort-indicator{display:inline-block;min-width:1em;margin-left:3px;color:#7dd3fc}</style></head><body>
<h1>MOLI Development Observatory</h1><p class="muted" id="meta">Loading…</p><div class="grid" id="cards"></div>
<section><h2>Daily issue flow</h2><canvas id="flow"></canvas></section><section><h2>7-day moving average</h2><canvas id="ma"></canvas></section><section><h2>Cumulative net backlog change</h2><canvas id="net"></canvas></section><section><h2>Net change by platform layer</h2><canvas id="layers"></canvas></section><section><h2>Open issue age</h2><canvas id="age"></canvas></section>
<section><h2>Repositories</h2><p class="muted">Click a numeric column to sort; click again to reverse the order. Total, Open and Closed describe the current all-time issue state. Δ backlog is the net change within the selected time window.</p><table><thead><tr><th>Repository</th><th>Layer</th><th><button class="sort-button" data-sort="total">Total<span class="sort-indicator"></span></button></th><th><button class="sort-button" data-sort="current_open">Open<span class="sort-indicator"></span></button></th><th><button class="sort-button" data-sort="current_closed">Closed<span class="sort-indicator"></span></button></th><th><button class="sort-button" data-sort="net_change">Δ backlog<span class="sort-indicator">↓</span></button></th></tr></thead><tbody id="repos"></tbody></table></section>
<section><h2>Data</h2><p><a href="issues.json">Normalized issue snapshot</a> · <a href="metrics.json">Derived metrics</a></p><p class="muted">V1 does not reconstruct reopen cycles. JSON is independent of this renderer and can feed Grafana or another backend later.</p></section>
<script>const C=['#38bdf8','#34d399','#f59e0b','#a78bfa'];let repoRows=[];let repoSort={key:'net_change',direction:'desc'};function line(id,labels,sets){new Chart(document.getElementById(id),{type:'line',data:{labels,datasets:sets},options:{responsive:true,interaction:{mode:'index',intersect:false},scales:{y:{beginAtZero:true}}}})}function bar(id,labels,data){new Chart(document.getElementById(id),{type:'bar',data:{labels,datasets:[{data,backgroundColor:C}]},options:{responsive:true,plugins:{legend:{display:false}}}})}function renderRepositories(){const factor=repoSort.direction==='asc'?1:-1;const rows=[...repoRows].sort((a,b)=>{const delta=(a[repoSort.key]-b[repoSort.key])*factor;return delta||a.repository.localeCompare(b.repository)});repos.innerHTML=rows.map(x=>`<tr><td>${x.repository}</td><td>${x.layer}</td><td>${x.total}</td><td>${x.current_open}</td><td>${x.current_closed}</td><td>${x.net_change}</td></tr>`).join('');document.querySelectorAll('[data-sort]').forEach(button=>{const active=button.dataset.sort===repoSort.key;button.setAttribute('aria-sort',active?(repoSort.direction==='asc'?'ascending':'descending'):'none');button.querySelector('.sort-indicator').textContent=active?(repoSort.direction==='asc'?'↑':'↓'):''})}function configureRepositorySorting(){document.querySelectorAll('[data-sort]').forEach(button=>button.addEventListener('click',()=>{const key=button.dataset.sort;if(repoSort.key===key){repoSort.direction=repoSort.direction==='desc'?'asc':'desc'}else{repoSort={key,direction:'desc'}}renderRepositories()}))}fetch('metrics.json').then(r=>r.json()).then(m=>{meta.textContent=`Generated ${m.generated_at} · ${m.window.start} → ${m.window.end} · ${m.timezone} · ${m.summary.excluded_repositories} excluded`;cards.innerHTML=[['Opened',m.summary.opened],['Closed',m.summary.closed],['Net change',m.summary.net_change],['Currently open',m.summary.current_open],['Repositories',m.summary.repositories],['Excluded',m.summary.excluded_repositories]].map(x=>`<div class="card"><div class="muted">${x[0]}</div><div class="n">${x[1]}</div></div>`).join('');let l=m.daily.map(x=>x.date);line('flow',l,[{label:'Opened',data:m.daily.map(x=>x.opened),borderColor:C[0]},{label:'Closed',data:m.daily.map(x=>x.closed),borderColor:C[1]}]);line('ma',l,[{label:'Opened/day',data:m.daily.map(x=>x.opened_ma7),borderColor:C[0]},{label:'Closed/day',data:m.daily.map(x=>x.closed_ma7),borderColor:C[1]}]);line('net',l,[{label:'Net backlog change',data:m.daily.map(x=>x.cumulative_net_change),borderColor:C[2]}]);bar('layers',m.layers.map(x=>x.layer),m.layers.map(x=>x.net_change));bar('age',m.issue_age.map(x=>x.bucket),m.issue_age.map(x=>x.count));repoRows=m.repositories;configureRepositorySorting();renderRepositories()})</script></body></html>'''


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "
", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--moli-registry", type=Path)
    parser.add_argument(
        "--moli-registry-url",
        default=DEFAULT_MOLI_REGISTRY_URL,
    )
    parser.add_argument("--suite-registry", type=Path)
    parser.add_argument(
        "--suite-registry-url",
        default=DEFAULT_SUITE_REGISTRY_URL,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build/development-observatory"),
    )
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--timezone", default="America/Mexico_City")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be >= 1")

    if args.moli_registry:
        moli = load_toml(args.moli_registry)
        moli_source = str(args.moli_registry)
    else:
        moli = tomllib.loads(fetch_text(args.moli_registry_url))
        moli_source = args.moli_registry_url

    if args.suite_registry:
        suite = load_toml(args.suite_registry)
        suite_source = str(args.suite_registry)
    else:
        suite = tomllib.loads(fetch_text(args.suite_registry_url))
        suite_source = args.suite_registry_url

    requested_scope = discover_scope(moli, suite)
    dataset = collect(
        requested_scope,
        os.environ.get("MOLI_OBSERVATORY_GITHUB_TOKEN") or None,
        moli_registry_source=moli_source,
        molsyssuite_registry_source=suite_source,
    )
    print(
        "Development Observatory collection: "
        f"{len(dataset['issues'])} issues from "
        f"{len(dataset['scope'])}/{len(requested_scope)} repositories."
    )
    if dataset["excluded_scope"]:
        print(
            "Excluded repositories: "
            + ", ".join(item["repository"] for item in dataset["excluded_scope"])
        )

    derived = metrics(dataset, args.days, args.timezone)
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "issues.json", dataset)
    write_json(args.output / "metrics.json", derived)

    from .drilldown import generate_drilldowns

    generate_drilldowns(
        args.output,
        dataset,
        days=args.days,
        timezone_name=args.timezone,
        metrics_fn=metrics,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
