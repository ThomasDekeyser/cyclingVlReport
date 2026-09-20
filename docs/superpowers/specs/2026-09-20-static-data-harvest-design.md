# Static data harvest via GitHub Actions

**Date:** 2026-09-20
**Status:** Approved, pending implementation plan

## Problem

The app fetches the cycling.vlaanderen API from the browser at request time.
That API sends no `Access-Control-Allow-Origin` header, so every call is routed
through a public CORS proxy. Both configured proxies are now dead:

| Proxy | Status | Cause |
|---|---|---|
| `corsproxy.io` | `401` | Now requires a paid API key (`console.corsproxy.io`). Permanent. |
| `api.codetabs.com` | `522` | Entire service down — its root URL and unrelated targets also 522. |

Because the codetabs 522 page carries no CORS header, the browser blocks the
response before JS sees it, so `fetch` rejects with a `TypeError` and the UI
reports `NetworkError when attempting to fetch resource` rather than a status.

Five replacement proxies were tested; four are unusable (429, dead, or
IP-banned) and the one that works, allorigins, measured 50% success over 8
attempts. Free anonymous CORS proxies are a repeatedly-exhausted commons — the
third fix against this architecture would not be the last.

### Two facts that shape the solution

1. **Team filtering is already client-side.** `static/index.html:302` filters
   `line.team === this.teamName` in the browser, against a hardcoded two-entry
   `<select>`. The team name never reaches the API. The only real query
   parameters are the date range and `race_id`.
2. **The origin throttles concurrent bursts.** A 5-way parallel harvest failed
   38 of 55 requests; the identical set run serially at 0.4 s pacing succeeded
   55 of 55 in 25 s. The app's `Promise.all` over ~29 races (`index.html:260`)
   is therefore already losing races to throttling — silently, because
   `fetchRaceResults` swallows every failure as `race.lines = []`.

## Goals

- Remove the CORS proxy dependency entirely, rather than replacing it.
- Serve race data same-origin from GitHub Pages, so there is no runtime
  third-party dependency at all.
- Fail loudly on harvest errors instead of silently rendering "no riders".

## Non-goals

- Arbitrary historical queries. Coverage is a rolling 90-day window.
- Supporting teams beyond the two in the dropdown (adding one is a config
  change plus a re-run, not a design change).
- Changing the visible behaviour of the results table.

## Coverage

A rolling **90-day** window ending today ("3 months" throughout this document
means exactly 90 days). Measured for 2026-06-22 → 2026-09-20:

| Metric | Value |
|---|---|
| Races in window | 817 |
| With results (`result == 1`) — one request each | 702 |
| Requests per run | 703 |
| Runtime at 0.45 s pacing | ~5.3 min |
| Output file | ~200 KB raw, ~25 KB gzipped on the wire |

At this size the Action **rebuilds the entire file every run**. This removes
incremental merging, trailing re-fetch windows, and shard management, and it
picks up late corrections to older results for free.

## Architecture

```
GitHub Actions (nightly cron)
  └─ scripts/harvest.py
       ├─ GET races.json for [today-90d, today]
       ├─ for each race with result==1:  GET race_results.json   (serial, paced)
       ├─ keep lines whose team ∈ TEAMS; keep all races as metadata
       └─ write static/data/results.json
  └─ commit iff content changed

GitHub Pages (raw repo root of main)
  └─ static/index.html  ──fetch('data/results.json')──>  static/data/results.json
```

Pages serves this repo's **raw root from `main`** (verified: `README.md` is
reachable and the live `index.html` is byte-identical to the repo's). Hugo is
local-dev convenience only; `public/` is gitignored and not deployed. So a
committed JSON file is live with no build step.

### Path

The file lives at `static/data/results.json` and is referenced by the
**relative** URL `data/results.json`. This resolves correctly in both
environments with no `baseURL` change:

- Hugo local: page `/index.html` → `/data/results.json` (Hugo maps `static/*` to root)
- Pages: page `/cyclingVlReport/static/index.html` → `/cyclingVlReport/static/data/results.json`

## Data contract

`static/data/results.json`. Field names and nesting mirror the upstream API so
the Vue template and the Excel export need minimal change.

```json
{
  "generated_at": "2026-09-20T03:00:12Z",
  "from_date": "2026-06-22",
  "to_date": "2026-09-20",
  "teams": ["ISOREX CYCLING TEAM", "K.V.C. DEINZE VZW"],
  "races": [
    {
      "id": 14344,
      "date": "2026-09-18",
      "city": "BEERSE",
      "description": "FUNwedstrijd Mannen - Club",
      "lines": [
        {
          "place": 1,
          "state": "finished",
          "team": "ISOREX CYCLING TEAM",
          "chrono_in_seconds": 4017,
          "person": { "first_name": "Elyass", "last_name": "PEETERS", "uci_code": "BEL1990..." },
          "club": { "club_name": "BEERSE KOERST" }
        }
      ]
    }
  ]
}
```

Rules:

- `races` contains **every** race with `result == 1` in the window, including
  those with no riders from either team (`"lines": []`). This preserves the
  current UI, which lists all races and shows "Geen renners van … in deze
  uitslag" for the ~64% without team riders. Cost is ~89 KB of the ~200 KB.
- `lines` contains only lines whose `team` is in `teams`.
- `club` may be absent upstream (see commit `d388094`); it is normalised to
  `null` and rendered as an empty string.
- `state == "did_not_finish"` is preserved verbatim; the client renders `DNF`
  in the Plaats column (see commit `f6a56b9`).
- `teams` is the single source of truth for the dropdown.

## Components

### 1. `.github/workflows/harvest.yml`

- Triggers: `schedule` cron `0 3 * * *` (UTC) and `workflow_dispatch`.
- Permissions: `contents: write`, using the built-in `GITHUB_TOKEN`. No PAT.
- Steps: checkout → run `scripts/harvest.py` → commit `static/data/results.json`
  only if `git diff --quiet` reports a change.
- `concurrency` guard so a manual dispatch cannot overlap the nightly run.

### 2. `scripts/harvest.py`

Python 3 standard library only (`urllib`, `json`, `datetime`, `time`) — no
dependencies, no lockfile; GitHub runners ship Python 3. Responsibilities:

- `TEAMS` constant — the single config point for team names.
- Serial fetching with ~0.45 s pacing, a real `User-Agent` (the origin rejects
  an empty one), and bounded retries with backoff.
- A **pure transform function** mapping raw API responses to the data contract.
- Atomic write of the output file.

### 3. `static/index.html`

- Delete `CORS_PROXY`, `CORS_PROXY_2`, `proxyFetch`, and `fetchRaceResults`.
- `search()` loads `data/results.json` once (cached after first call) and
  filters in memory by date range and selected team.
- Build the team `<select>` from the file's `teams` array.
- Set `min`/`max` on the Van/Tot inputs from `from_date`/`to_date`, and show a
  line such as `Gegevens beschikbaar vanaf 22/06/2026 tot 20/09/2026`.
- Remove the now-vestigial per-race `loading` flag and spinner, since all data
  arrives in one response.

## Error handling

**Harvest.** Each request retries a few times with backoff. If any race still
fails, the job fails loudly and **commits nothing**; the previous good file
stays live and GitHub emails the failure. This is the deliberate inverse of
today's `catch { race.lines = [] }`, which presents failure as absence of data.

**Client.** A failed or malformed load of `results.json` surfaces a real error
message. There is no silent empty state: an empty result must mean the data
says so, never that a fetch failed.

**Staleness.** The client renders `generated_at` so a stalled harvest is
visible rather than silently serving old data.

## Testing

No test framework exists in the repo today; add a minimal `pytest` setup for
the harvester. Tests must run offline.

- **Transform (primary value), against recorded fixtures:**
  - filters lines to `TEAMS`, drops all others
  - keeps races with no matching lines, with `lines: []`
  - handles a missing/null `club` without raising
  - preserves `did_not_finish`
  - skips races with `result != 1`
  - handles the empty `race_results` payload (observed: a 21-byte response)
- **Fetch/retry loop:** thin tests with a stubbed opener covering retry,
  eventual success, and give-up-and-fail.
- Fixtures are trimmed real responses committed under `tests/fixtures/`.

## Documentation

`README.md` currently documents the corsproxy.io routing and says a local web
server is needed "to bypass CORS issues". Both become false. It needs: the new
architecture, the data contract, how to run the harvester by hand, and how to
add a team.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Upstream API shape changes | Transform tests fail against fixtures; harvest fails loudly rather than committing garbage. |
| Origin throttles even serial requests | Pacing is a constant; retries with backoff absorb transient throttling. |
| Data goes stale unnoticed | `generated_at` rendered in the UI; failed runs email the owner. |
| Repo grows from a daily commit | One ~200 KB file, committed only when changed. Acceptable; can move to a `data` branch if churn becomes a problem. |
| Someone picks a date outside the window | Picker is clamped to `from_date`/`to_date` and the range is stated in the UI. |

## Out of scope, noted

The git remote in `.git/config` contains an embedded personal access token in
plaintext. Not committed and therefore not public, but it should be rotated and
replaced with a credential helper. The Action does not need it.
