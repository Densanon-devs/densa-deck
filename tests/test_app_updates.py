"""Settings -> App updates, on the Table of War pattern.

One anonymous GET of a static JSON on launch, a banner, a manual download.
On by default; off means NO request is made (not made and ignored).
"""

from __future__ import annotations

import io
import json

import pytest

from densa_deck.app.api import AppApi

FEED = {"version": "9.9.9", "releaseDate": "2026-10-01",
        "downloadUrl": "https://github.com/Densanon-devs/densa-deck/releases/download/v9.9.9/Densa-Deck-Setup-9.9.9.exe",
        "changelog": ["x"]}


@pytest.fixture
def api(tmp_path):
    a = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "versions.db")
    yield a
    a.close()


@pytest.fixture
def network(monkeypatch):
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        return io.BytesIO(json.dumps(FEED).encode())

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _d(r):
    return r.get("data", r)


def test_on_by_default(api):
    assert _d(api.get_user_preferences())["check_app_updates"] is True


def test_launch_check_reports_a_newer_version(api, network):
    r = _d(api.check_for_updates())
    assert r["update_available"] is True
    assert r["latest"] == "9.9.9"
    assert len(network) == 1


def test_off_means_no_request_at_all(api, network):
    api.set_user_preferences({"check_app_updates": False})
    r = _d(api.check_for_updates())
    assert r["disabled"] is True and r["update_available"] is False
    assert network == [], "a switched-off check still phoned home"


def test_check_now_works_even_when_launch_checks_are_off(api, network):
    api.set_user_preferences({"check_app_updates": False})
    r = _d(api.check_for_updates_now())
    assert r["update_available"] is True
    assert len(network) == 1


def test_the_setting_persists_and_other_prefs_are_untouched(api):
    api.set_user_preferences({"auto_check_card_db": True})
    api.set_user_preferences({"check_app_updates": False})
    prefs = _d(api.get_user_preferences())
    assert prefs["check_app_updates"] is False
    assert prefs["auto_check_card_db"] is True
