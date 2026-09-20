#!/usr/bin/env python3
"""Harvest cycling.vlaanderen race results for the configured teams.

Writes a rolling 90-day window to static/data/results.json so the page can be
served same-origin with no CORS proxy.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import tempfile
import time
import urllib.parse
import urllib.request

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
    races = document["races"]
    if not races:
        raise RuntimeError(
            "harvest returned zero races for "
            f"{document['from_date']} to {document['to_date']}; refusing to "
            "overwrite the existing data file (the window always contains "
            "hundreds of races, so zero indicates an unrecognised or "
            "throttled upstream response, not a real empty window)"
        )
    write_json(OUTPUT, document)
    riders = sum(len(race["lines"]) for race in races)
    print(f"{OUTPUT}: {len(races)} races, {riders} rider lines, "
          f"{document['from_date']} to {document['to_date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
