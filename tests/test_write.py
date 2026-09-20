import datetime
import json

import pytest

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


def test_main_raises_and_writes_nothing_when_races_empty(monkeypatch, tmp_path):
    # A valid-JSON-but-unrecognised races.json payload (throttle page, shape
    # change, ...) makes select_races() -> harvest() return races: []. main()
    # must refuse to write that over a known-good file rather than silently
    # publish an empty dataset.
    fake_document = {
        "generated_at": "2026-09-20T03:00:00Z",
        "from_date": "2026-06-22",
        "to_date": "2026-09-20",
        "teams": sorted(harvest.TEAMS),
        "races": [],
    }
    monkeypatch.setattr(harvest, "harvest", lambda today: fake_document)

    output = tmp_path / "results.json"
    monkeypatch.setattr(harvest, "OUTPUT", str(output))

    with pytest.raises(RuntimeError, match="zero races"):
        harvest.main()

    assert not output.exists()


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
