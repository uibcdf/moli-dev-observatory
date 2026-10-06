# MOLI Development Observatory

Development observability for the [MOLI](https://github.com/uibcdf/moli) platform and its registered ecosystem.

The Observatory collects GitHub issue metadata from repositories registered by MOLI and MolSysSuite, preserves a normalized snapshot, derives reproducible metrics, and renders a static **Overview → Layer → Repository** site.

## Architecture

```text
uibcdf/moli:moli.toml
uibcdf/molsyssuite:suite.toml
GitHub public issue data
            ↓
         collector
            ↓
   normalized issues.json
            ↓
         analytics
            ↓
       metrics.json
            ↓
 Overview → Layer → Repository
            ↓
      GitHub Pages
```

The normalized records and metric semantics are independent of the HTML renderer so the same data model can later feed Grafana or another observability frontend.

## Public observatory

The production Pages build is intentionally **public-data-only**.

The scheduled workflow does not use a broad PAT or organization secret to read the observed repositories. Public GitHub resources are collected anonymously; registered repositories that are private or otherwise inaccessible are recorded as excluded and do not receive repository pages.

This prevents a future private repository from becoming public merely because it is registered in MOLI or MolSysSuite.

## Time reference and refresh cadence

The production observatory uses:

- timezone: `America/Mexico_City`;
- analytics window: 90 days;
- refresh cadence: every 6 hours;
- scheduled local times: approximately **00:17, 06:17, 12:17 and 18:17** in Mexico City;
- manual refresh: available through `workflow_dispatch`;
- pushes to `main`: also rebuild and publish.

GitHub may delay a scheduled run slightly under load.

## Local build

Python 3.11+ is sufficient and there are no runtime Python dependencies.

```bash
python -m moli_dev_observatory \
  --output build/development-observatory \
  --days 90 \
  --timezone America/Mexico_City

python -m http.server --directory build/development-observatory 8000
```

Then open <http://localhost:8000/>.

By default the authoritative registries are fetched from:

- `https://raw.githubusercontent.com/uibcdf/moli/main/moli.toml`
- `https://raw.githubusercontent.com/uibcdf/molsyssuite/main/suite.toml`

Local registry files can be supplied when developing:

```bash
python -m moli_dev_observatory \
  --moli-registry /path/to/moli.toml \
  --suite-registry /path/to/suite.toml
```

### Optional authenticated local collection

Repeated local runs may exhaust GitHub's anonymous API limit. If you want a higher rate limit, you can use your already authenticated GitHub CLI token:

```bash
export MOLI_OBSERVATORY_GITHUB_TOKEN="$(gh auth token)"
```

**Important:** the token's access determines the effective scope. If it can read private repositories, the generated local site may contain private metadata. Do not publish such a build as the public Observatory.

## Tests

```bash
python -m unittest discover -s tests -v
python -m compileall moli_dev_observatory
```

## Generated site

```text
build/development-observatory/
├── index.html
├── issues.json
├── metrics.json
├── layers/
│   └── <layer>/
│       ├── index.html
│       └── metrics.json
└── repositories/
    └── <owner>--<repository>/
        ├── index.html
        └── metrics.json
```

`issues.json` is the canonical normalized snapshot for a run. Drill-down pages contain derived metrics only.

## Data semantics

The repository table distinguishes current state from time-window flow:

- **Total** — all issues currently known for the repository;
- **Open** — issues whose current GitHub state is open;
- **Closed** — issues whose current GitHub state is closed;
- **Δ backlog** — issues opened minus issues closed inside the selected time window.

The time-series charts use **Opened** and **Closed** as events per day.

V1 uses current issue snapshots and does not reconstruct historical close → reopen → close cycles.

## Development

Migration/bootstrap work is tracked in [issue #1](https://github.com/uibcdf/moli-dev-observatory/issues/1).

Longer-term ideas such as PR throughput, CI health, releases, historical storage, Grafana, alerts and private/internal observability should remain separate from the current static public Observatory until their value and semantics are established.
