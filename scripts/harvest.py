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
