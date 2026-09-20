# Cycling VL Report

A static single-page app built with Vue.js and Bootstrap that shows race results for a selected team within a chosen date range, using data harvested nightly from the [cycling.vlaanderen](https://cycling.vlaanderen) API. Results can be exported to Excel.

**Live:** https://ThomasDekeyser.github.io/cyclingVlReport/static/index.html

---

## Local setup with Hugo

The page is plain static HTML; Hugo's built-in server is just a convenient way
to serve it locally so that the relative `data/results.json` path resolves.

### Prerequisites

- [Hugo](https://gohugo.io/) extended edition (v0.100+)

```bash
brew install hugo
```

### Run locally

```bash
git clone https://github.com/ThomasDekeyser/cyclingVlReport.git
cd cyclingVlReport
hugo server
```

The app is then available at http://localhost:1313/index.html.

---

## Data

The page does **not** call the cycling.vlaanderen API at runtime. That API sends
no `Access-Control-Allow-Origin` header, and the public CORS proxies this project
used to rely on have all failed permanently.

Instead, `.github/workflows/harvest.yml` runs the test suite and then
`scripts/harvest.py` nightly at 03:00 UTC (also runnable on demand via
`workflow_dispatch`). The harvester walks a rolling 90-day window and writes
`static/data/results.json`; the workflow commits the file only when it changed,
using the repository's built-in `GITHUB_TOKEN`. GitHub Pages serves that file
same-origin, and the page loads it once and filters by date and team in the
browser.

As of the most recent harvest, the file spans 2026-06-22 to 2026-09-20: 702
races, 820 rider lines, 257 KB on disk (about 23 KB gzipped over the wire).

### Running the harvest by hand

```bash
python3 -m scripts.harvest      # roughly 700 requests, about 6 minutes
python3 -m pytest tests/ -q     # 23 offline unit tests
```

### Adding a team

Add the exact team name to `TEAMS` in `scripts/harvest.py`, then re-run the
harvest. The dropdown is built from the `teams` array in the data file, so no
HTML change is needed.

### Data contract

`static/data/results.json`:

- `generated_at`, `from_date`, `to_date` — window metadata, shown in the UI
- `teams` — the configured team names; drives the dropdown
- `races[]` — every race with a submitted result in the window, including those
  with no riders from the configured teams (`lines: []`)
- `races[].lines[]` — only lines whose `team` is in `teams`. Field names mirror
  the upstream API. `club` is `null` when absent upstream.

### Upstream endpoints (used by the harvester only)

```
GET https://cycling.vlaanderen/actions/cycling-api-module/api?method=races.json
      &from_date=YYYY-MM-DD&to_date=YYYY-MM-DD&provinces=&categories=
GET https://cycling.vlaanderen/actions/cycling-api-module/api?method=race_results.json
      &race_id={id}
```

Only races with `result = 1` are harvested. The origin throttles concurrent
requests, so the harvester is deliberately serial and paced.
