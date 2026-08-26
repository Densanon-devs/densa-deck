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
def desktop_factory():
    """A bridge with options, for the cases that need a non-default one."""
    made = []

    def build(**kwargs):
        tmp = tempfile.mkdtemp()
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_printings([printing_row_from_scryfall(
            _raw(SOL, "Sol Ring", "cmm", "410"), "t")])
        db.close()
        api = AppApi(db_path=root / "cards.db",
                     version_db_path=root / "versions.db")
        api._collection_store = CollectionStore(db_path=root / "collection.db")
        bridge = PhoneBridge(api, port=8801, companion_port=8802, **kwargs)
        bridge.start()
        made.append((api, bridge))
        return api, bridge

    yield build
    for api, bridge in made:
        try:
            bridge.stop()
            api.close()
        except Exception:
            pass


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
        # Distinct ports on BOTH listeners: a real desktop app running on
        # this machine holds the defaults, and a test that quietly bound
        # nothing would pass alone and fail in a full run.
        bridge = PhoneBridge(api, port=8797, companion_port=8799)
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
    def test_a_phone_starting_from_nothing_gets_the_collection(self, desktop):
        """`since=0` is a baseline, not a replay.

        The log only holds what happened since logging existed, so replaying
        it from zero misses every card that predates it — see
        tests/test_sync_baseline.py, and a real installation where that was
        half the collection.
        """
        api, bridge = desktop
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        pulled = _post(bridge, "sync/pull", {"since": 0, "peer": "phone-1"})
        assert pulled["baseline"] is True
        sets = [e for e in pulled["events"] if e["kind"] == "stack-set"]
        assert [e["payload"]["card_name"] for e in sets] == ["Sol Ring"]
        assert pulled["cursor"] > 0

    def test_a_later_desktop_edit_is_offered_as_a_delta(self, desktop):
        """Past the baseline it is deltas again, which is what commutes."""
        api, bridge = desktop
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        first = _post(bridge, "sync/pull", {"since": 0, "peer": "phone-1"})

        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        second = _post(bridge, "sync/pull",
                       {"since": first["cursor"], "peer": "phone-1"})
        assert [e["kind"] for e in second["events"]] == ["stack-delta"]

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


class TestTheCompanionPort:
    """The native app talks in the clear, on its own port.

    The certificate on the main port exists so a BROWSER gets a secure
    context, without which it has no `getUserMedia` and the web scan page
    cannot open a viewfinder at all. A native app has direct camera access and
    needs none of that — and Android refuses a self-signed certificate
    outright, with no way to override it from JavaScript, so pointing the app
    at the TLS port fails on its very first request.

    The boundary was never the certificate. It is the tailnet, with the
    pairing token beneath it, and both still apply here.
    """

    def _plain(self, bridge, route, payload, token=None):
        request = urllib.request.Request(
            f"http://{BIND_HOST}:{bridge.companion_port}/api/{route}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "X-Densa-Token": bridge.token if token is None else token},
            method="POST")
        with urllib.request.urlopen(request, timeout=10) as r:
            return json.loads(r.read())

    def test_the_app_can_reach_it_without_a_certificate(self, desktop):
        _api, bridge = desktop
        reply = self._plain(bridge, "sync/hello", {"peer": "phone-1"})
        assert reply["protocol"] == 1

    def test_it_still_needs_the_pairing_token(self, desktop):
        """Plain HTTP is not open house."""
        _api, bridge = desktop
        with pytest.raises(urllib.error.HTTPError) as caught:
            self._plain(bridge, "sync/pull", {"since": 0}, token="wrong")
        assert caught.value.code == 403

    def test_it_serves_the_same_routes(self, desktop):
        api, bridge = desktop
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        pulled = self._plain(bridge, "sync/pull", {"since": 0, "peer": "p1"})
        assert any(e["kind"] == "stack-set" for e in pulled["events"])

    def test_it_is_a_different_port_from_the_browser(self, desktop):
        _api, bridge = desktop
        assert bridge.companion_port != bridge.port

    def test_it_actually_bound(self, desktop):
        """A listener that silently failed to bind would make the tests above
        vacuous: they would be asserting about a port nothing is serving."""
        _api, bridge = desktop
        assert bridge.companion_hosts, "the companion port bound nothing"

    def test_the_pairing_link_tells_the_app_where_to_go(self, desktop):
        """One QR code has to serve both the web page and the app.

        The app cannot use the address the browser uses, and making it guess
        at port arithmetic would break the moment either port moved.
        """
        from densa_deck.app.phone import pairing_url

        _api, bridge = desktop
        status = {**bridge.status(), "tailnet_host": "100.64.0.1",
                  "scheme": "https"}
        url = pairing_url(status, {}, {}, bridge.token)
        assert "/scan?t=" in url, url
        assert f"&api=http://100.64.0.1:{bridge.companion_port}" in url, url


class TestLanAndHealth:
    """Reaching the desktop over Wi-Fi as well as the tailnet.

    "Connect like LAN from anywhere": the local address is faster and keeps
    traffic in the house; the tailnet works from a shop. The desktop is one
    machine at two addresses and the phone uses whichever answers.
    """

    def _health(self, bridge, host="127.0.0.1", token=None):
        query = f"?token={token}" if token else ""
        url = f"http://{host}:{bridge.companion_port}/health{query}"
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read())

    def test_health_answers_without_a_token(self, desktop):
        """A phone has to be able to ask "are you there" before it can prove
        anything, so reachability itself is open."""
        _api, bridge = desktop
        assert self._health(bridge)["ok"] is True

    def test_it_reports_the_callers_own_address(self, desktop):
        """The only honest answer to "which path did we take".

        Dialling a LAN URL and arriving from CGNAT means the packets went
        through the tunnel, whatever the client believed it was doing.
        """
        _api, bridge = desktop
        assert self._health(bridge)["peer"] == "127.0.0.1"

    def test_the_lan_hint_needs_the_token(self, desktop):
        """It describes this machine's network, so it is not handed to
        anything that cannot prove it is paired."""
        _api, bridge = desktop
        assert "lan" not in self._health(bridge)
        assert "lan" in self._health(bridge, token=bridge.token)

    def test_the_lan_hint_is_this_machines_address(self, desktop):
        from densa_deck.app.phone import is_private_lan_address

        _api, bridge = desktop
        hint = self._health(bridge, token=bridge.token)["lan"]
        # Empty is legitimate on a machine with no private address at all.
        assert hint == "" or is_private_lan_address(hint), hint

    def test_binding_the_lan_can_be_declined(self, desktop_factory):
        """Not everyone wants the collection served to their whole Wi-Fi."""
        api, bridge = desktop_factory(bind_lan=False)
        try:
            assert bridge.lan_host == ""
            assert all(not h.startswith("192.168.") and not h.startswith("10.")
                       for h in bridge.bound_hosts)
        finally:
            bridge.stop()
            api.close()


class TestPrivateAddresses:
    """What may be bound, and what may never be.

    A specific private address is bound, never 0.0.0.0 — that distinction is
    the whole safety argument, because 0.0.0.0 would also answer on whatever
    untrusted Wi-Fi this machine joins next.
    """

    def test_the_private_ranges(self):
        from densa_deck.app.phone import is_private_lan_address

        for ip in ("10.0.0.5", "192.168.1.1", "172.16.0.1", "172.31.255.254"):
            assert is_private_lan_address(ip), ip

    def test_everything_else(self):
        from densa_deck.app.phone import is_private_lan_address

        for ip in ("172.15.0.1", "172.32.0.1", "8.8.8.8", "100.124.242.11",
                   "127.0.0.1", "0.0.0.0", "", "not an address"):
            assert not is_private_lan_address(ip), ip

    def test_a_tailnet_address_is_not_a_lan_address(self):
        """They are handled by different code paths for different reasons."""
        from densa_deck.app.phone import (
            is_private_lan_address,
            is_tailnet_address,
        )

        assert is_tailnet_address("100.124.242.11")
        assert not is_private_lan_address("100.124.242.11")
