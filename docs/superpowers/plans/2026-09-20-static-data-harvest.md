# Static Data Harvest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead CORS-proxy runtime fetching with a nightly GitHub Action that harvests a rolling 90-day window of cycling.vlaanderen results into a static JSON file served same-origin by GitHub Pages.

**Architecture:** A Python stdlib script fetches the race list for the trailing 90 days, then each race's results serially at ~0.45 s pacing (the origin throttles concurrent bursts), keeps only lines belonging to the two configured teams, and writes `static/data/results.json`. A nightly workflow rebuilds the whole file and commits it only when changed. `static/index.html` loads that one file over a relative URL and filters by date and team in memory.

**Tech Stack:** Python 3 standard library (no dependencies), pytest for tests, GitHub Actions, Vue 3 + flatpickr + Bootstrap via CDN (unchanged).

**Spec:** `docs/superpowers/specs/2026-09-20-static-data-harvest-design.md`

## Global Constraints

- Coverage window is exactly **90 days**, ending today.
- Teams, verbatim: `ISOREX CYCLING TEAM`, `K.V.C. DEINZE VZW`.
- Output path `static/data/results.json`; client references it by the **relative** URL `data/results.json` (works under both Hugo local and Pages — do not use an absolute path).
- Harvester uses the **Python standard library only**. No pip dependencies in `scripts/harvest.py`.
- Requests are **serial** with ~0.45 s pacing and a non-empty `User-Agent`. The origin rejects an empty UA and throttles parallel bursts (measured: 5-way concurrency failed 38/55; serial succeeded 55/55).
- On any unrecoverable harvest error the job **fails and commits nothing**. Never write a partial file.
- Field names in the output mirror the upstream API (`person.first_name`, `club.club_name`, `chrono_in_seconds`, `state`, `place`, `team`) so the existing template keeps working.
- `club` may be absent upstream; normalise to `null`. `state == "did_not_finish"` renders as `DNF`.
- All races with `result == 1` are included, even with `lines: []`.

### Deviation from spec, noted

The spec says "set `min`/`max` on the Van/Tot inputs". Those inputs are driven by **flatpickr**, not native date pickers, so clamping uses flatpickr's `minDate`/`maxDate` options (Task 7). Same behaviour, correct mechanism.

---

### Task 1: Pure transform + fixtures

The transform is where all the real logic lives. Build it first, test-first, fully offline.

**Files:**
- Create: `scripts/harvest.py`
- Create: `tests/conftest.py`
- Create: `tests/test_transform.py`
- Create: `tests/fixtures/races.json`, `tests/fixtures/race_results.json`, `tests/fixtures/race_results_empty.json`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `select_races(races_payload: dict) -> list[dict]`
  - `extract_lines(results_payload: dict, teams: list[str]) -> list[dict]`
  - `build_race(race: dict, lines: list[dict]) -> dict`
  - `build_document(from_date: str, to_date: str, teams: list[str], races: list[dict], generated_at: str) -> dict`
  - Constants `API`, `TEAMS`, `WINDOW_DAYS`, `USER_AGENT`, `PACING_SECONDS`, `MAX_ATTEMPTS`, `OUTPUT`

- [ ] **Step 1: Record the fixtures from the live API**

These are real responses, trimmed. Run:

```bash
mkdir -p tests/fixtures
API='https://cycling.vlaanderen/actions/cycling-api-module/api'
curl -s "$API?method=races.json&from_date=2026-09-18&to_date=2026-09-19&provinces=&categories=" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); d['data']['races']=d['data']['races'][:5]; print(json.dumps(d,indent=1))" \
  > tests/fixtures/races.json
curl -s "$API?method=race_results.json&race_id=14344" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); rr=d['data']['race_results']; rr[0]['race_result_lines']=rr[0]['race_result_lines'][:8]; print(json.dumps(d,indent=1))" \
  > tests/fixtures/race_results.json
curl -s "$API?method=race_results.json&race_id=14345" > tests/fixtures/race_results_empty.json
```

Then hand-edit `tests/fixtures/race_results.json` so the fixture exercises every branch: set the `team` of the first line to `ISOREX CYCLING TEAM`, the second to `K.V.C. DEINZE VZW`, and leave the rest as other teams. Set the second line's `"state"` to `"did_not_finish"`. Delete the `"club"` key entirely from the first line.

- [ ] **Step 2: Add the test bootstrap so `scripts/` is importable**

Create `tests/conftest.py`:

```python
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_transform.py`:

```python
import json
import pathlib

from scripts import harvest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TEAMS = ["ISOREX CYCLING TEAM", "K.V.C. DEINZE VZW"]


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_select_races_keeps_only_submitted_results():
    payload = load("races.json")
    payload["data"]["races"] = [
        {"id": 1, "result": 1},
        {"id": 2, "result": 0},
        {"id": 3},
    ]
    assert [r["id"] for r in harvest.select_races(payload)] == [1]


def test_select_races_handles_empty_payload():
    assert harvest.select_races({}) == []
    assert harvest.select_races({"data": {}}) == []
    assert harvest.select_races({"data": {"races": None}}) == []


def test_extract_lines_keeps_only_configured_teams():
    lines = harvest.extract_lines(load("race_results.json"), TEAMS)
    assert len(lines) == 2
    assert {l["team"] for l in lines} == set(TEAMS)


def test_extract_lines_normalises_missing_club_to_none():
    lines = harvest.extract_lines(load("race_results.json"), TEAMS)
    isorex = next(l for l in lines if l["team"] == "ISOREX CYCLING TEAM")
    assert isorex["club"] is None


def test_extract_lines_preserves_did_not_finish():
    lines = harvest.extract_lines(load("race_results.json"), TEAMS)
    deinze = next(l for l in lines if l["team"] == "K.V.C. DEINZE VZW")
    assert deinze["state"] == "did_not_finish"


def test_extract_lines_shapes_person_and_keeps_api_field_names():
    lines = harvest.extract_lines(load("race_results.json"), TEAMS)
    line = lines[0]
    assert set(line) == {"place", "state", "team", "chrono_in_seconds", "person", "club"}
    assert set(line["person"]) == {"first_name", "last_name", "uci_code"}


def test_extract_lines_on_empty_results_payload():
    assert harvest.extract_lines(load("race_results_empty.json"), TEAMS) == []


def test_extract_lines_returns_empty_when_no_team_matches():
    assert harvest.extract_lines(load("race_results.json"), ["NO SUCH TEAM"]) == []


def test_build_race_keeps_metadata_and_empty_lines():
    race = {"id": 7, "date": "2026-09-18", "city": "BEERSE",
            "description": "FUN", "result": 1, "extra": "dropped"}
    built = harvest.build_race(race, [])
    assert built == {"id": 7, "date": "2026-09-18", "city": "BEERSE",
                     "description": "FUN", "lines": []}


def test_build_document_shape():
    doc = harvest.build_document("2026-06-22", "2026-09-20", TEAMS, [], "2026-09-20T03:00:12Z")
    assert doc == {
        "generated_at": "2026-09-20T03:00:12Z",
        "from_date": "2026-06-22",
        "to_date": "2026-09-20",
        "teams": sorted(TEAMS),
        "races": [],
    }
```

- [ ] **Step 4: Run the tests and verify they fail**

Run: `python3 -m pytest tests/test_transform.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'scripts'`.

- [ ] **Step 5: Create the package marker and the transform**

Create empty `scripts/__init__.py`, then create `scripts/harvest.py`:

```python
#!/usr/bin/env python3
"""Harvest cycling.vlaanderen race results for the configured teams.

Writes a rolling 90-day window to static/data/results.json so the page can be
served same-origin with no CORS proxy.
"""
from __future__ import annotations

API = "https://cycling.vlaanderen/actions/cycling-api-module/api"
TEAMS = ["ISOREX CYCLING TEAM", "K.V.C. DEINZE VZW"]
WINDOW_DAYS = 90
USER_AGENT = "cyclingVlReport-harvester (+https://github.com/ThomasDekeyser/cyclingVlReport)"
PACING_SECONDS = 0.45
MAX_ATTEMPTS = 4
OUTPUT = "static/data/results.json"


def select_races(races_payload):
    """Races that have a submitted result, in API order."""
    data = (races_payload or {}).get("data") or {}
    return [r for r in (data.get("races") or []) if r.get("result") == 1]


def extract_lines(results_payload, teams):
    """Result lines belonging to `teams`, normalised to the data contract."""
    data = (results_payload or {}).get("data") or {}
    wanted = set(teams)
    lines = []
    for result in data.get("race_results") or []:
        for line in result.get("race_result_lines") or []:
            if line.get("team") not in wanted:
                continue
            person = line.get("person") or {}
            club = line.get("club") or None
            lines.append({
                "place": line.get("place"),
                "state": line.get("state"),
                "team": line.get("team"),
                "chrono_in_seconds": line.get("chrono_in_seconds"),
                "person": {
                    "first_name": person.get("first_name"),
                    "last_name": person.get("last_name"),
                    "uci_code": person.get("uci_code"),
                },
                "club": {"club_name": club.get("club_name")} if club else None,
            })
    return lines


def build_race(race, lines):
    """Race metadata plus its (possibly empty) team lines."""
    return {
        "id": race.get("id"),
        "date": race.get("date"),
        "city": race.get("city"),
        "description": race.get("description"),
        "lines": lines,
    }


def build_document(from_date, to_date, teams, races, generated_at):
    return {
        "generated_at": generated_at,
        "from_date": from_date,
        "to_date": to_date,
        "teams": sorted(teams),
        "races": races,
    }
```

- [ ] **Step 6: Run the tests and verify they pass**

Run: `python3 -m pytest tests/test_transform.py -v`
Expected: 10 passed.

- [ ] **Step 7: Commit**

```bash
git add scripts/__init__.py scripts/harvest.py tests/
git commit -m "feat: add harvest transform with offline fixture tests"
```

---

### Task 2: Paced fetching with retry

**Files:**
- Modify: `scripts/harvest.py` (append)
- Create: `tests/test_fetch.py`

**Interfaces:**
- Consumes: `API`, `USER_AGENT`, `MAX_ATTEMPTS` from Task 1.
- Produces:
  - `races_url(from_date: str, to_date: str) -> str`
  - `results_url(race_id: int) -> str`
  - `window(today: datetime.date, days: int = WINDOW_DAYS) -> tuple[str, str]`
  - `fetch_json(url: str, *, opener=..., attempts=MAX_ATTEMPTS, sleep=time.sleep) -> dict`
    — raises `RuntimeError` after `attempts` failures.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetch.py`:

```python
import datetime

import pytest

from scripts import harvest


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload.encode("utf-8")

    def read(self):
        return self._payload

    def close(self):
        pass


def test_races_url_contains_all_required_params():
    url = harvest.races_url("2026-06-22", "2026-09-20")
    assert url.startswith(harvest.API + "?")
    assert "method=races.json" in url
    assert "from_date=2026-06-22" in url
    assert "to_date=2026-09-20" in url
    assert "provinces=" in url
    assert "categories=" in url


def test_results_url_contains_race_id():
    url = harvest.results_url(14344)
    assert "method=race_results.json" in url
    assert "race_id=14344" in url


def test_window_is_exactly_90_days():
    frm, to = harvest.window(datetime.date(2026, 9, 20))
    assert (frm, to) == ("2026-06-22", "2026-09-20")


def test_fetch_json_returns_parsed_payload():
    calls = []

    def opener(url):
        calls.append(url)
        return FakeResponse('{"ok": true}')

    assert harvest.fetch_json("http://x", opener=opener, sleep=lambda s: None) == {"ok": True}
    assert calls == ["http://x"]


def test_fetch_json_retries_then_succeeds():
    attempts = []

    def opener(url):
        attempts.append(url)
        if len(attempts) < 3:
            raise OSError("connection reset")
        return FakeResponse('{"ok": true}')

    slept = []
    result = harvest.fetch_json("http://x", opener=opener, sleep=slept.append)
    assert result == {"ok": True}
    assert len(attempts) == 3
    assert slept == [1, 2]


def test_fetch_json_gives_up_and_raises():
    def opener(url):
        raise OSError("always down")

    with pytest.raises(RuntimeError) as excinfo:
        harvest.fetch_json("http://x", opener=opener, attempts=2, sleep=lambda s: None)
    assert "http://x" in str(excinfo.value)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m pytest tests/test_fetch.py -v`
Expected: FAIL with `AttributeError: module 'scripts.harvest' has no attribute 'races_url'`.

- [ ] **Step 3: Implement**

Add these imports at the top of `scripts/harvest.py`, directly under `from __future__ import annotations`:

```python
import contextlib
import datetime
import json
import time
import urllib.parse
import urllib.request
```

Append to `scripts/harvest.py`:

```python
def races_url(from_date, to_date):
    query = urllib.parse.urlencode({
        "method": "races.json",
        "from_date": from_date,
        "to_date": to_date,
        "provinces": "",
        "categories": "",
    })
    return f"{API}?{query}"


def results_url(race_id):
    query = urllib.parse.urlencode({
        "method": "race_results.json",
        "race_id": race_id,
    })
    return f"{API}?{query}"


def window(today, days=WINDOW_DAYS):
    return (today - datetime.timedelta(days=days)).isoformat(), today.isoformat()


def _urlopen(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=30)


def fetch_json(url, *, opener=_urlopen, attempts=MAX_ATTEMPTS, sleep=time.sleep):
    """GET `url` and parse JSON, retrying with backoff. Raises on give-up."""
    last_error = None
    for attempt in range(attempts):
        try:
            with contextlib.closing(opener(url)) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:  # noqa: BLE001 - retry on anything transient
            last_error = error
            if attempt < attempts - 1:
                sleep(2 ** attempt)
    raise RuntimeError(f"giving up on {url} after {attempts} attempts: {last_error}")
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/harvest.py tests/test_fetch.py
git commit -m "feat: add paced fetching with bounded retry"
```

---

### Task 3: Entrypoint and atomic write

**Files:**
- Modify: `scripts/harvest.py` (append)
- Create: `tests/test_write.py`

**Interfaces:**
- Consumes: everything from Tasks 1 and 2.
- Produces:
  - `write_json(path: str, document: dict) -> None` — atomic via temp file + `os.replace`
  - `harvest(today: datetime.date, *, fetch=fetch_json, sleep=time.sleep) -> dict`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_write.py`:

```python
import datetime
import json

from scripts import harvest


def test_write_json_creates_parent_directories(tmp_path):
    target = tmp_path / "static" / "data" / "results.json"
    harvest.write_json(str(target), {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_write_json_leaves_no_temp_files(tmp_path):
    target = tmp_path / "results.json"
    harvest.write_json(str(target), {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["results.json"]


def test_write_json_overwrites_existing(tmp_path):
    target = tmp_path / "results.json"
    harvest.write_json(str(target), {"a": 1})
    harvest.write_json(str(target), {"b": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"b": 2}


def test_harvest_paces_and_builds_document():
    races_payload = {"data": {"races": [
        {"id": 1, "date": "2026-09-18", "city": "A", "description": "d1", "result": 1},
        {"id": 2, "date": "2026-09-18", "city": "B", "description": "d2", "result": 0},
        {"id": 3, "date": "2026-09-19", "city": "C", "description": "d3", "result": 1},
    ]}}
    results_payload = {"data": {"race_results": [{"race_result_lines": [
        {"team": "ISOREX CYCLING TEAM", "place": 1, "state": "finished",
         "chrono_in_seconds": 10, "person": {"first_name": "A", "last_name": "B", "uci_code": "X"},
         "club": {"club_name": "C"}},
        {"team": "OTHER", "place": 2, "state": "finished", "chrono_in_seconds": 11,
         "person": {}, "club": None},
    ]}]}}

    seen = []

    def fake_fetch(url):
        seen.append(url)
        return races_payload if "races.json" in url else results_payload

    slept = []
    doc = harvest.harvest(datetime.date(2026, 9, 20), fetch=fake_fetch, sleep=slept.append)

    assert doc["from_date"] == "2026-06-22"
    assert doc["to_date"] == "2026-09-20"
    assert doc["teams"] == sorted(harvest.TEAMS)
    assert [r["id"] for r in doc["races"]] == [1, 3]
    assert all(len(r["lines"]) == 1 for r in doc["races"])
    assert len(seen) == 3
    assert slept == [harvest.PACING_SECONDS]


def test_harvest_keeps_races_with_no_team_lines():
    races_payload = {"data": {"races": [
        {"id": 1, "date": "2026-09-18", "city": "A", "description": "d", "result": 1},
    ]}}
    empty = {"data": {"race_results": [{"race_result_lines": [
        {"team": "SOMEONE ELSE", "person": {}, "club": None},
    ]}]}}

    def fake_fetch(url):
        return races_payload if "races.json" in url else empty

    doc = harvest.harvest(datetime.date(2026, 9, 20), fetch=fake_fetch, sleep=lambda s: None)
    assert doc["races"] == [{"id": 1, "date": "2026-09-18", "city": "A",
                             "description": "d", "lines": []}]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m pytest tests/test_write.py -v`
Expected: FAIL with `AttributeError: module 'scripts.harvest' has no attribute 'write_json'`.

- [ ] **Step 3: Implement**

Add `import os` and `import tempfile` to the import block in `scripts/harvest.py`. Append:

```python
def write_json(path, document):
    """Write `document` to `path` atomically."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def harvest(today, *, fetch=fetch_json, sleep=time.sleep):
    """Fetch the whole window and return the output document."""
    from_date, to_date = window(today)
    races = select_races(fetch(races_url(from_date, to_date)))
    built = []
    for index, race in enumerate(races):
        payload = fetch(results_url(race["id"]))
        built.append(build_race(race, extract_lines(payload, TEAMS)))
        if index + 1 < len(races):
            sleep(PACING_SECONDS)
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return build_document(from_date, to_date, TEAMS, built, generated_at)


def main(argv=None):
    document = harvest(datetime.date.today())
    write_json(OUTPUT, document)
    races = document["races"]
    riders = sum(len(race["lines"]) for race in races)
    print(f"{OUTPUT}: {len(races)} races, {riders} rider lines, "
          f"{document['from_date']} to {document['to_date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the full suite and verify it passes**

Run: `python3 -m pytest tests/ -v`
Expected: 21 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/harvest.py tests/test_write.py
git commit -m "feat: add harvest entrypoint with atomic write"
```

---

### Task 4: Generate the first data file

This runs against the live API and takes about 5–6 minutes (703 requests at 0.45 s pacing). Run it before touching the client, so the client has real data to develop against.

**Files:**
- Create: `static/data/results.json` (generated)

**Interfaces:**
- Consumes: `main()` from Task 3.
- Produces: the committed data file the client and Task 7 read.

- [ ] **Step 1: Run the harvester**

`OUTPUT` is a repo-relative path, so run this from the repository root.

Run: `python3 -m scripts.harvest`
Expected: a summary line like `static/data/results.json: 702 races, 51 rider lines, 2026-06-22 to 2026-09-20`. Exit code 0. Takes ~5–6 minutes.

- [ ] **Step 2: Sanity-check the output against the data contract**

```bash
python3 - <<'CHECK'
import json
d = json.load(open("static/data/results.json"))
assert set(d) == {"generated_at", "from_date", "to_date", "teams", "races"}, set(d)
assert d["teams"] == ["ISOREX CYCLING TEAM", "K.V.C. DEINZE VZW"], d["teams"]
races = d["races"]
lines = [l for r in races for l in r["lines"]]
assert races, "no races harvested"
assert all(set(r) == {"id", "date", "city", "description", "lines"} for r in races)
assert all(l["team"] in d["teams"] for l in lines), "foreign team leaked in"
assert any(r["lines"] == [] for r in races), "expected some races with no team riders"
print(f"OK: {len(races)} races, {len(lines)} lines, "
      f"{sum(1 for r in races if r['lines'])} races with riders")
print("size:", round(len(open('static/data/results.json','rb').read())/1024), "KB")
CHECK
```

Expected: `OK: ~700 races, ~50 lines, ~20 races with riders`, size roughly 150–250 KB.

- [ ] **Step 3: Commit**

```bash
git add static/data/results.json
git commit -m "data: initial 90-day harvest"
```

---

### Task 5: Nightly workflow

**Files:**
- Create: `.github/workflows/harvest.yml`

**Interfaces:**
- Consumes: `scripts/harvest.py`, `tests/`.
- Produces: a scheduled job that keeps `static/data/results.json` current.

Note: the spec listed the job as checkout → harvest → commit. This adds a `pytest` step before the harvest, so a broken transform fails the run instead of committing bad data. That directly serves the spec's "fail loudly, commit nothing" rule.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/harvest.yml`:

```yaml
name: Harvest race results

on:
  schedule:
    - cron: "0 3 * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: harvest
  cancel-in-progress: false

jobs:
  harvest:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Run unit tests
        run: |
          python -m pip install --quiet pytest
          python -m pytest tests/ -q

      - name: Harvest
        run: python -m scripts.harvest

      - name: Commit if changed
        run: |
          if git diff --quiet -- static/data/results.json; then
            echo "No change; nothing to commit."
            exit 0
          fi
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add static/data/results.json
          git commit -m "data: refresh 90-day harvest"
          git push
```

- [ ] **Step 2: Validate the YAML parses**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/harvest.yml')); print('valid')"`
Expected: `valid`. (If PyYAML is missing: `python3 -m pip install --quiet pyyaml` first.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/harvest.yml
git commit -m "ci: nightly harvest workflow"
```

- [ ] **Step 4: Verify on GitHub after the branch is merged**

Trigger the workflow manually from the Actions tab (`workflow_dispatch`) and confirm it completes green. If it fails on push permissions, enable *Settings → Actions → General → Workflow permissions → Read and write*.

---

### Task 6: Client loads the static file

Strip out the proxy layer and read the committed JSON instead.

**Files:**
- Modify: `static/index.html` — replace lines 148-162 (the `<script>` opening through `proxyFetch`), the `data()` block, `search()`, and delete `fetchRaceResults`; remove the `race.loading` branch in the template at lines 107-110.

**Interfaces:**
- Consumes: `static/data/results.json` from Task 4.
- Produces: `this.dataset` (the parsed document) for Task 7 to read `teams`, `from_date`, `to_date`, `generated_at`.

- [ ] **Step 1: Replace the proxy constants and `proxyFetch`**

In `static/index.html`, delete these lines:

```javascript
const CYCLING_API = 'https://cycling.vlaanderen/actions/cycling-api-module/api';
const CORS_PROXY  = 'https://corsproxy.io/?reqHeaders=Accept-Encoding:%20identity&url=';
const CORS_PROXY_2  = 'https://api.codetabs.com/v1/proxy?quest=';


function proxyFetch(params) {
  const targetUrl = `${CYCLING_API}?${new URLSearchParams(params)}`;
  const fetchVia = proxy => fetch(`${proxy}${encodeURIComponent(targetUrl)}`).then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  });
  return fetchVia(CORS_PROXY).catch(() => fetchVia(CORS_PROXY_2));
}
```

and put this in their place:

```javascript
const DATA_URL = 'data/results.json';

async function loadDataset() {
  const response = await fetch(DATA_URL, { cache: 'no-cache' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const dataset = await response.json();
  if (!Array.isArray(dataset?.races)) throw new Error('Ongeldig gegevensbestand');
  return dataset;
}
```

- [ ] **Step 2: Add `dataset` to `data()`**

Change the returned object in `data()` so it reads:

```javascript
    return {
      from:     threeDaysAgo,
      to:       today,
      teamName: '',
      races:    null,
      loading:  false,
      error:    null,
      dataset:  null,
    };
```

- [ ] **Step 3: Replace `search()` and delete `fetchRaceResults`**

Replace the whole `async search() { ... }` method with:

```javascript
    search() {
      this.error = null;
      this.races = null;

      if (!this.dataset) {
        this.error = 'Gegevens zijn nog niet geladen.';
        return;
      }

      const from = this.isoDate(this.from);
      const to   = this.isoDate(this.to);

      this.races = this.dataset.races
        .filter(race => race.date >= from && race.date <= to)
        .map(race => ({
          ...race,
          lines: race.lines.filter(line => line.team === this.teamName),
        }));
    },
```

Delete the entire `async fetchRaceResults(race) { ... }` method — nothing calls it any more.

- [ ] **Step 4: Remove the per-race loading branch from the template**

Delete this block:

```html
      <!-- Loading race results -->
      <div v-if="race.loading" class="card-body py-2 text-muted small">
        <span class="spinner-border spinner-border-sm me-1"></span> Uitslagen laden…
      </div>

```

and change the next block's `v-else-if` to `v-if` so it reads:

```html
      <!-- No club results -->
      <div v-if="race.lines && race.lines.length === 0" class="card-body py-2 text-muted fst-italic small">
```

- [ ] **Step 5: Load the dataset on mount**

At the end of the existing `mounted()` hook, after the `this.fpTo = flatpickr(...)` assignment, append:

```javascript
    this.loading = true;
    loadDataset()
      .then(dataset => { this.dataset = dataset; })
      .catch(error => { this.error = `Kon de gegevens niet laden: ${error.message}`; })
      .finally(() => { this.loading = false; });
```

- [ ] **Step 6: Verify in the browser**

Run: `hugo server`
Then open `http://localhost:1313/index.html`, pick a team, and search a range inside the harvested window.

Expected: results appear with no network calls to `corsproxy.io` or `codetabs` (check the Network tab — the only data request should be `data/results.json`, status 200). Races with no riders from the team still show "Geen renners van … in deze uitslag." Export to Excel still works.

- [ ] **Step 7: Commit**

```bash
git add static/index.html
git commit -m "feat: read race data from the static harvest instead of CORS proxies"
```

---

### Task 7: Dropdown from data, clamped dates, freshness

**Files:**
- Modify: `static/index.html` — the team `<select>` (lines 56-60), the Periode block (lines 33-52), `mounted()`.

**Interfaces:**
- Consumes: `this.dataset` from Task 6 (`teams`, `from_date`, `to_date`, `generated_at`).
- Produces: nothing downstream.

- [ ] **Step 1: Build the team dropdown from the dataset**

Replace the hardcoded options:

```html
          <select id="team-select" class="form-select form-select-sm" v-model="teamName">
            <option value="">— Selecteer een team —</option>
            <option value="K.V.C. DEINZE VZW">K.V.C. DEINZE VZW</option>
            <option value="ISOREX CYCLING TEAM">ISOREX CYCLING TEAM</option>
          </select>
```

with:

```html
          <select id="team-select" class="form-select form-select-sm" v-model="teamName">
            <option value="">— Selecteer een team —</option>
            <option v-for="team in dataset?.teams ?? []" :key="team" :value="team">{{ team }}</option>
          </select>
```

- [ ] **Step 2: Add the availability note under the Periode block**

Immediately after the closing `</div>` of the `row g-2` that holds the Van and Tot inputs, and before the closing `</div>` of the Periode column, insert:

```html
          <div v-if="dataset" class="form-text small mt-1">
            Gegevens beschikbaar van {{ displayDate(dataset.from_date) }}
            tot {{ displayDate(dataset.to_date) }}
            <span class="text-muted">· bijgewerkt {{ displayDateTime(dataset.generated_at) }}</span>
          </div>
```

- [ ] **Step 3: Add the two formatting helpers**

In `methods`, directly after `isoDate`, add:

```javascript
    displayDate(iso) {
      if (!iso) return '';
      const [y, m, d] = iso.split('-');
      return `${d}/${m}/${y}`;
    },

    displayDateTime(iso) {
      if (!iso) return '';
      const parsed = new Date(iso);
      return Number.isNaN(parsed.getTime())
        ? iso
        : parsed.toLocaleString('nl-BE', { dateStyle: 'short', timeStyle: 'short' });
    },
```

- [ ] **Step 4: Clamp the pickers to the harvested window**

The Van/Tot inputs are flatpickr instances, so clamping uses `minDate`/`maxDate`, not HTML attributes. In `mounted()`, extend the dataset-loading block from Task 6 Step 5 so it reads:

```javascript
    this.loading = true;
    loadDataset()
      .then(dataset => {
        this.dataset = dataset;
        this.fpFrom.set('minDate', dataset.from_date);
        this.fpFrom.set('maxDate', dataset.to_date);
        this.fpTo.set('minDate', dataset.from_date);
        this.fpTo.set('maxDate', dataset.to_date);
        if (this.isoDate(this.from) < dataset.from_date) {
          this.fpFrom.setDate(dataset.from_date, true);
        }
        if (this.isoDate(this.to) > dataset.to_date) {
          this.fpTo.setDate(dataset.to_date, true);
        }
      })
      .catch(error => { this.error = `Kon de gegevens niet laden: ${error.message}`; })
      .finally(() => { this.loading = false; });
```

`setDate(..., true)` fires `onChange`, which keeps `this.from` / `this.to` in sync.

- [ ] **Step 5: Verify in the browser**

Run: `hugo server` and open `http://localhost:1313/index.html`.

Expected:
- the Team dropdown lists both teams, read from the file
- the note reads `Gegevens beschikbaar van 22/06/2026 tot 20/09/2026 · bijgewerkt …`
- opening either calendar greys out every date before `from_date` and after `to_date`
- the default 10-day range still lands inside the window and searching works

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "feat: drive team list and date bounds from the harvested data"
```

---

### Task 8: Update the README

The README currently tells the reader that requests are routed through corsproxy.io and that a local web server is needed "to bypass CORS issues". Both are now false.

**Files:**
- Modify: `README.md`

**Interfaces:** none.

- [ ] **Step 1: Rewrite the stale sections**

Replace the "Local setup with Hugo" intro sentence:

> A local running web server is required to bypass CORS issues when fetching data from the cycling.vlaanderen API using corsproxy.io
> Hugo's built-in server is a convenient option for this.

with:

```markdown
The page is plain static HTML; Hugo's built-in server is just a convenient way
to serve it locally so that the relative `data/results.json` path resolves.
```

Then replace the whole `## API` section with:

```markdown
## Data

The page does **not** call the cycling.vlaanderen API at runtime. That API sends
no `Access-Control-Allow-Origin` header, and the public CORS proxies this project
used to rely on have all failed permanently.

Instead, `.github/workflows/harvest.yml` runs `scripts/harvest.py` nightly. It
harvests a rolling 90-day window and writes `static/data/results.json`, which
GitHub Pages serves same-origin. The page loads that one file and filters by
date and team in the browser.

### Running the harvest by hand

```bash
python3 -m scripts.harvest      # ~5-6 minutes, ~700 requests
python3 -m pytest tests/ -q     # offline unit tests
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: describe the static harvest architecture"
```

---

## Self-review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Coverage — rolling 90-day window | Global Constraints, Task 2 (`window`), Task 4 |
| Full rebuild each run | Task 3 (`harvest`), Task 5 |
| Path `static/data/results.json`, relative URL | Task 3, Task 6 |
| Data contract, all fields | Task 1, verified in Task 4 Step 2 |
| Empty races retained | Task 1, Task 3 tests, Task 4 check |
| `club` null-safe, `DNF` preserved | Task 1 tests |
| `teams` as single source of truth | Task 1, Task 7 Step 1 |
| Workflow: cron, dispatch, GITHUB_TOKEN, commit-if-changed, concurrency | Task 5 |
| Harvester: stdlib, pacing, UA, retry, atomic write | Tasks 2, 3 |
| Client: delete proxies, load file, filter in memory | Task 6 |
| Client: clamp pickers, availability note, generated_at | Task 7 |
| Remove vestigial per-race spinner | Task 6 Step 4 |
| Error handling: fail loudly, commit nothing | Task 3 (raise), Task 5 (job fails before commit) |
| Client error on failed load | Task 6 Step 5 |
| Testing: transform, fetch/retry, fixtures, offline | Tasks 1, 2, 3 |
| README | Task 8 |

No gaps.

**Out of scope, carried forward from the spec:** the plaintext PAT in
`.git/config` should be rotated and replaced with a credential helper. No task
covers it because no code change can.

**Placeholder scan:** none. Every code step carries real code; every verify
step names a command and its expected output.

**Type consistency:** `select_races`, `extract_lines`, `build_race`,
`build_document`, `races_url`, `results_url`, `window`, `fetch_json`,
`write_json`, `harvest`, `main` are each defined once in Tasks 1–3 and used
with matching signatures thereafter. Client-side, `loadDataset` (Task 6) and
`this.dataset` (Tasks 6, 7) agree; `displayDate`/`displayDateTime` are defined
in Task 7 Step 3 and used in Task 7 Step 2.
