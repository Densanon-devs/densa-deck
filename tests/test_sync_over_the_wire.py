"""Sync through the actual bridge, not just the library underneath it.

The unit tests prove the merge maths. These prove the thing a phone will
really do: talk HTTP to a paired desktop, with a token, over the routes as
they are actually spelled — and that an unpaired caller gets nothing.
"""

from __future__ import annotations

import json
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from densa_deck.app.api import AppApi
from densa_deck.app.phone import BIND_HOST, PhoneBridge
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall

SOL = "11111111-1111-1111-1111-111111111111"


def _raw(pid, name, set_code, num):
    return {
        "id": pid, "oracle_id": "o-sol", "name": name, "set": set_code,
        "set_name": set_code.upper(), "collector_number": num,
        "rarity": "uncommon", "lang": "en", "released_at": "2023-01-01",
        "finishes": ["nonfoil", "foil"], "frame": "2015",
        "border_color": "black", "promo_types": [], "games": ["paper"],
        "tcgplayer_id": 1,
        "prices": {"usd": "1.50", "usd_foil": "4.00", "usd_etched": None},
    }


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(tmp_path / "pair.json"))
    monkeypatch.setenv("DENSA_DEVICE_FILE", str(tmp_path / "device.json"))
    monkeypatch.setattr("densa_deck.app.api._user_prefs_path",
                        lambda: tmp_path / "config.json")


@pytest.fixture
def desktop():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_printings([printing_row_from_scryfall(
            _raw(SOL, "Sol Ring", "cmm", "410"), "t")])
        db.close()
        api = AppApi(db_path=root / "cards.db",
                     version_db_path=root / "versions.db")
        api._collection_store = CollectionStore(db_path=root / "collection.db")
        bridge = PhoneBridge(api, port=8797)
        bridge.start()
        yield api, bridge
        bridge.stop()
        api.close()


def _ctx():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _post(bridge, route, payload, token=None):
    scheme = "https" if bridge.ssl_context else "http"
    request = urllib.request.Request(
        f"{scheme}://{BIND_HOST}:{bridge.port}/api/{route}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "X-Densa-Token": bridge.token if token is None else token},
        method="POST")
    with urllib.request.urlopen(request, timeout=10, context=_ctx()) as r:
        return json.loads(r.read())


class TestReachability:
    def test_hello_identifies_the_desktop(self, desktop):
        _api, bridge = desktop
        reply = _post(bridge, "sync/hello", {"peer": "phone-1"})
        assert reply["device"]
        assert reply["protocol"] == 1

    def test_sync_needs_the_pairing_token(self, desktop):
        """Being on the tailnet is not authorisation to read a collection."""
        _api, bridge = desktop
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(bridge, "sync/pull", {"since": 0}, token="wrong")
        assert caught.value.code == 403

    def test_push_needs_the_token_too(self, desktop):
        _api, bridge = desktop
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(bridge, "sync/push", {"events": []}, token="")
        assert caught.value.code == 403


class TestExchange:
    def test_a_desktop_edit_is_offered_to_the_phone(self, desktop):
        api, bridge = desktop
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        pulled = _post(bridge, "sync/pull", {"since": 0, "peer": "phone-1"})
        kinds = [e["kind"] for e in pulled["events"]]
        assert "stack-delta" in kinds
        assert pulled["cursor"] > 0

    def test_pulling_resumes_from_the_cursor(self, desktop):
        api, bridge = desktop
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        first = _post(bridge, "sync/pull", {"since": 0, "peer": "phone-1"})
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        second = _post(bridge, "sync/pull",
                       {"since": first["cursor"], "peer": "phone-1"})
        assert len(second["events"]) == 1        # only what is new

    def test_a_phone_edit_reaches_the_desktop(self, desktop):
        """The whole point: cards added at a shop turn up on the PC."""
        api, bridge = desktop
        uid = api._get_collection_store().collection_uid(
            api._get_collection_store().default_collection_id())
        event = {
            "event_uid": "e-phone-1", "device": "phone-1", "seq": 1,
            "kind": "stack-delta",
            "payload": {"printing_id": SOL, "card_name": "Sol Ring",
                        "delta": 3, "collection_uid": uid,
                        "finish": "nonfoil", "condition": "NM",
                        "language": "en", "location": ""},
            "created_at": "2026-08-23T10:00:00.000+00:00",
        }
        reply = _post(bridge, "sync/push",
                      {"events": [event], "peer": "phone-1"})
        assert reply["applied"] == 1
        items, _ = api._get_collection_store().list_items(limit=10)
        assert sum(i.quantity for i in items) == 3

    def test_pushing_the_same_events_twice_is_safe(self, desktop):
        """A phone unsure whether its push landed should just send again."""
        api, bridge = desktop
        uid = api._get_collection_store().collection_uid(
            api._get_collection_store().default_collection_id())
        event = {
            "event_uid": "e-phone-2", "device": "phone-1", "seq": 1,
            "kind": "stack-delta",
            "payload": {"printing_id": SOL, "card_name": "Sol Ring",
                        "delta": 2, "collection_uid": uid},
            "created_at": "2026-08-23T10:00:00.000+00:00",
        }
        _post(bridge, "sync/push", {"events": [event], "peer": "phone-1"})
        again = _post(bridge, "sync/push", {"events": [event], "peer": "phone-1"})
        assert again["applied"] == 0
        assert again["duplicates"] == 1
        items, _ = api._get_collection_store().list_items(limit=10)
        assert sum(i.quantity for i in items) == 2      # not four

    def test_both_sides_edited_apart(self, desktop):
        """The scenario the design exists for.

        The desktop was used at home and the phone at a shop; neither knew
        about the other until now. Both sets of cards must survive.
        """
        api, bridge = desktop
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")     # desktop: +1
        uid = api._get_collection_store().collection_uid(
            api._get_collection_store().default_collection_id())
        _post(bridge, "sync/push", {"peer": "phone-1", "events": [{
            "event_uid": "e-shop-1", "device": "phone-1", "seq": 1,
            "kind": "stack-delta",
            "payload": {"printing_id": SOL, "card_name": "Sol Ring",
                        "delta": 4, "collection_uid": uid},
            "created_at": "2026-08-23T11:00:00.000+00:00"}]})
        items, _ = api._get_collection_store().list_items(limit=10)
        assert sum(i.quantity for i in items) == 5

    def test_the_phones_own_events_are_not_echoed_back(self, desktop):
        api, bridge = desktop
        uid = api._get_collection_store().collection_uid(
            api._get_collection_store().default_collection_id())
        _post(bridge, "sync/push", {"peer": "phone-1", "events": [{
            "event_uid": "e-echo-1", "device": "phone-1", "seq": 1,
            "kind": "stack-delta",
            "payload": {"printing_id": SOL, "card_name": "Sol Ring",
                        "delta": 1, "collection_uid": uid},
            "created_at": "2026-08-23T11:00:00.000+00:00"}]})
        pulled = _post(bridge, "sync/pull", {"since": 0, "peer": "phone-1"})
        assert all(e["device"] != "phone-1" for e in pulled["events"])

    def test_a_collection_made_on_the_phone_arrives(self, desktop):
        api, bridge = desktop
        _post(bridge, "sync/push", {"peer": "phone-1", "events": [
            {"event_uid": "e-col-1", "device": "phone-1", "seq": 1,
             "kind": "collection-upsert",
             "payload": {"collection_uid": "uid-trade", "name": "Trade box"},
             "created_at": "2026-08-23T11:00:00.000+00:00"},
            {"event_uid": "e-col-2", "device": "phone-1", "seq": 2,
             "kind": "stack-delta",
             "payload": {"printing_id": SOL, "card_name": "Sol Ring",
                         "delta": 2, "collection_uid": "uid-trade"},
             "created_at": "2026-08-23T11:00:01.000+00:00"}]})
        rows = {c["name"]: c["cards"]
                for c in api._get_collection_store().list_collections()}
        assert rows.get("Trade box") == 2


class TestStatus:
    def test_status_reports_peers(self, desktop):
        api, bridge = desktop
        _post(bridge, "sync/pull", {"since": 0, "peer": "phone-1"})
        status = _post(bridge, "sync/status", {})
        assert any(p["peer"] == "phone-1" for p in status["peers"])

    def test_scanning_writes_to_the_log(self, desktop):
        """An edit that changes the database without logging is invisible
        to the other device forever, which is the worst kind of sync bug."""
        api, bridge = desktop
        before = _post(bridge, "sync/status", {})["events"]
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        after = _post(bridge, "sync/status", {})["events"]
        assert after > before
