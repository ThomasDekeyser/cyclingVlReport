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
