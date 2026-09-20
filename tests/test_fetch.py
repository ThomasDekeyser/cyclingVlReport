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
