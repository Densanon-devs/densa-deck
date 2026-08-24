"""The companion's route table, exercised over HTTP.

Two things are being checked, and the second matters more than the first:

1. every route a companion needs actually works;
2. every route it must NOT have is genuinely absent.

The second is why the bridge writes its allow-list out one route at a time
instead of forwarding method names onto `AppApi`. Forwarding is one typo away
from putting deck deletion or a 74 MB download on a phone, and nothing in the
happy path would ever reveal it.
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
from densa_deck.models import Card, CardLayout, Legality

SOL = "11111111-1111-1111-1111-111111111111"
BOLT = "22222222-2222-2222-2222-222222222222"

DECKLIST = "1 Sol Ring\n1 Lightning Bolt\n"


def _raw(pid, name, set_code, num, oracle, usd="1.50"):
    return {
        "id": pid, "oracle_id": oracle, "name": name, "set": set_code,
        "set_name": set_code.upper(), "collector_number": num,
        "rarity": "uncommon", "lang": "en", "released_at": "2023-01-01",
        "finishes": ["nonfoil", "foil"], "frame": "2015",
        "border_color": "black", "promo_types": [], "games": ["paper"],
        "tcgplayer_id": 1,
        "prices": {"usd": usd, "usd_foil": "4.00", "usd_etched": None},
    }


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(tmp_path / "pair.json"))
    monkeypatch.setenv("DENSA_DEVICE_FILE", str(tmp_path / "device.json"))
    monkeypatch.setattr("densa_deck.app.api._user_prefs_path",
                        lambda: tmp_path / "config.json")


@pytest.fixture
def paired():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        # Printings are what the collection stores; oracle cards are what the
        # analyst reasons over. A companion needs both, so both are here.
        db.upsert_printings([
            printing_row_from_scryfall(_raw(SOL, "Sol Ring", "cmm", "410",
                                            "o-sol"), "t"),
            printing_row_from_scryfall(_raw(BOLT, "Lightning Bolt", "lea",
                                            "161", "o-bolt", "400.00"), "t"),
        ])
        db.upsert_cards([
            Card(scryfall_id=SOL, oracle_id="o-sol", name="Sol Ring",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
                 type_line="Artifact", color_identity=[],
                 legalities={"commander": Legality.LEGAL},
                 oracle_text="{T}: Add {C}{C}."),
            Card(scryfall_id=BOLT, oracle_id="o-bolt", name="Lightning Bolt",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{R}",
                 type_line="Instant", color_identity=[],
                 legalities={"commander": Legality.LEGAL},
                 oracle_text="Lightning Bolt deals 3 damage to any target."),
        ])
        db.close()
        api = AppApi(db_path=root / "cards.db",
                     version_db_path=root / "versions.db")
        api._collection_store = CollectionStore(db_path=root / "collection.db")
        bridge = PhoneBridge(api, port=8798)
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


def call(bridge, route, payload=None, token=None):
    scheme = "https" if bridge.ssl_context else "http"
    request = urllib.request.Request(
        f"{scheme}://{BIND_HOST}:{bridge.port}/api/{route}",
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json",
                 "X-Densa-Token": bridge.token if token is None else token},
        method="POST")
    with urllib.request.urlopen(request, timeout=20, context=_ctx()) as r:
        return json.loads(r.read())


class TestBrowsing:
    def test_listing_the_master_collection(self, paired):
        api, bridge = paired
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        reply = call(bridge, "collection/list", {"query": {"limit": 50}})
        assert reply["total"] == 1
        assert reply["items"][0]["card_name"] == "Sol Ring"

    def test_narrowing_to_one_collection(self, paired):
        """The difference between "my binder" and "my cards"."""
        api, bridge = paired
        trade = api._get_collection_store().create_collection("Trade box")
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM",
                        collection_id=trade["collection_id"])
        api.scan_commit(BOLT, "Lightning Bolt", "nonfoil", "NM")

        everything = call(bridge, "collection/list", {"query": {}})
        just_trade = call(bridge, "collection/list",
                          {"query": {"collection_id": trade["collection_id"]}})
        assert everything["total"] == 2
        assert just_trade["total"] == 1
        assert just_trade["items"][0]["card_name"] == "Sol Ring"

    def test_searching_by_name(self, paired):
        api, bridge = paired
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        api.scan_commit(BOLT, "Lightning Bolt", "nonfoil", "NM")
        reply = call(bridge, "collection/list",
                     {"query": {"name_like": "bolt"}})
        assert reply["total"] == 1

    def test_browsing_never_writes_a_price_snapshot(self, paired):
        """A once-a-day fact the desktop owns; a phone browsing must not
        silently consume it."""
        api, bridge = paired
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        before = len(api._get_collection_store().recent_events(limit=100))
        call(bridge, "collection/value", {})
        after = len(api._get_collection_store().recent_events(limit=100))
        assert after == before

    def test_collection_list_endpoint(self, paired):
        api, bridge = paired
        api._get_collection_store().create_collection("Binder")
        reply = call(bridge, "collections", {})
        names = [c["name"] for c in reply["collections"]]
        assert "Binder" in names and "Main Collection" in names
        assert "master" in reply

    def test_moving_a_card_between_collections(self, paired):
        api, bridge = paired
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        trade = api._get_collection_store().create_collection("Trade box")
        items, _ = api._get_collection_store().list_items(limit=10)
        reply = call(bridge, "collection/move",
                     {"item_id": items[0].item_id,
                      "collection_id": trade["collection_id"]})
        assert reply["moved"] == 1
        rows = {c["name"]: c["cards"]
                for c in api._get_collection_store().list_collections()}
        assert rows["Trade box"] == 1

    def test_sets_and_status_are_readable(self, paired):
        api, bridge = paired
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        assert "sets" in call(bridge, "collection/sets", {"limit": 10})
        assert call(bridge, "collection/status", {}) is not None


class TestDecks:
    def test_saving_and_listing_a_deck(self, paired):
        _api, bridge = paired
        saved = call(bridge, "decks/save",
                     {"deck_id": "shopbrew", "decklist_text": DECKLIST,
                      "name": "Shop brew", "format": "commander"})
        assert saved.get("ok") is not False, saved
        listed = call(bridge, "decks/list", {})
        # Every response is a JSON object, so a typed client models one
        # envelope shape rather than asking per route whether it is about to
        # receive a list.
        assert isinstance(listed, dict), listed
        assert any(d.get("name") == "Shop brew" for d in listed["decks"])

    def test_reading_a_deck_back(self, paired):
        _api, bridge = paired
        call(bridge, "decks/save",
             {"deck_id": "shopbrew", "decklist_text": DECKLIST,
              "name": "Shop brew", "format": "commander"})
        deck = call(bridge, "decks/get", {"deck_id": "shopbrew"})
        assert deck.get("name") == "Shop brew"

    def test_the_name_and_the_decklist_do_not_get_swapped(self, paired):
        """save_deck_version takes (deck_id, name, decklist_text).

        Passing them in the order the payload happens to list them saved a
        deck whose name was its own decklist. Nothing in a happy-path test
        would have shown it.
        """
        _api, bridge = paired
        call(bridge, "decks/save",
             {"deck_id": "orderd", "decklist_text": DECKLIST,
              "name": "Shop brew", "format": "commander"})
        deck = call(bridge, "decks/get", {"deck_id": "orderd"})
        assert deck.get("name") == "Shop brew"
        assert "Sol Ring" not in str(deck.get("name", ""))

    def test_ownership_against_the_collection(self, paired):
        """What a deck needs versus what is actually owned — the question
        you ask standing in a shop."""
        api, bridge = paired
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        reply = call(bridge, "decks/ownership", {"decklist_text": DECKLIST})
        assert reply is not None


class TestAnalyst:
    """The PC does the thinking; the phone shows the answer."""

    def test_analyzing_a_deck(self, paired):
        _api, bridge = paired
        reply = call(bridge, "analyst/analyze",
                     {"decklist_text": DECKLIST, "name": "Shop brew"})
        assert reply is not None
        assert reply.get("ok") is not False

    def test_combo_detection(self, paired):
        _api, bridge = paired
        reply = call(bridge, "analyst/combos", {"decklist_text": DECKLIST})
        assert reply is not None

    def test_rule_zero_worksheet(self, paired):
        _api, bridge = paired
        reply = call(bridge, "analyst/rule0", {"decklist_text": DECKLIST})
        assert reply is not None


class TestTheSurfaceIsScoped:
    """What the phone must NOT be able to do.

    Nothing here is hypothetical: each of these is a real AppApi method that
    a forwarding router would have exposed for free.
    """

    FORBIDDEN = [
        "delete_deck",                  # destroys work
        "delete_collection",            # can discard an entire collection
        "delete_collection_item",       # destroys inventory
        "clear_collection",             # destroys everything
        "activate_license",             # licence handling
        "ingest_cards",                 # a 74 MB download, on mobile data
        "update_all_content_start",     # ditto, several of them
        "install_scan_support_start",   # runs pip on the desktop
        "phone_unpair",                 # a phone must not unpair itself
        "sync_status_reset",
    ]

    @pytest.mark.parametrize("method", FORBIDDEN)
    def test_destructive_methods_are_not_routes(self, paired, method):
        _api, bridge = paired
        reply = bridge.handle_api(method, {})
        assert reply.get("ok") is False
        assert "unknown route" in str(reply.get("error", ""))

    def test_a_dotted_path_cannot_reach_the_api(self, paired):
        _api, bridge = paired
        for attempt in ("_api.delete_deck", "api.delete_deck",
                        "__class__", "collection/../decks/list"):
            reply = bridge.handle_api(attempt, {})
            assert reply.get("ok") is False

    def test_the_allow_list_is_finite(self, paired):
        """A route that was never written out cannot be called."""
        _api, bridge = paired
        assert bridge.handle_api("definitely/not/a/route", {})["ok"] is False

    def test_everything_needs_the_token(self, paired):
        _api, bridge = paired
        for route in ("collection/list", "decks/list", "analyst/analyze",
                      "sync/pull"):
            with pytest.raises(urllib.error.HTTPError) as caught:
                call(bridge, route, {}, token="not-the-token")
            assert caught.value.code == 403, route


class TestWireShape:
    """One envelope shape, every route.

    A route that returned a bare array while its neighbours returned objects
    forced a typed client to know, per route, what it was about to receive.
    That is exactly the kind of inconsistency that produces a client bug
    months later, so it is pinned here.
    """

    ROUTES = [
        ("collections", {}),
        ("collection/list", {"query": {}}),
        ("collection/status", {}),
        ("decks/list", {}),
        ("sync/hello", {"peer": "phone-1"}),
        ("sync/pull", {"since": 0, "peer": "phone-1"}),
        ("sync/status", {}),
    ]

    @pytest.mark.parametrize("route,payload", ROUTES)
    def test_every_response_is_an_object(self, paired, route, payload):
        _api, bridge = paired
        assert isinstance(call(bridge, route, payload), dict), route

    def test_an_unknown_route_still_answers_in_that_shape(self, paired):
        _api, bridge = paired
        reply = call(bridge, "nope/not/a/route", {})
        assert isinstance(reply, dict)
        assert reply["ok"] is False
