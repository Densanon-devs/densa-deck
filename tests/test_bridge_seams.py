"""PC halves of the phone<->PC seam audit (2026-09-28): the card index, foil
prices, wishlist forgetting, and errors the phone could not see."""

from __future__ import annotations

import pytest

from densa_deck.app.api import AppApi
from densa_deck.app.phone import PhoneBridge
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall

SOL = "11111111-1111-1111-1111-111111111111"


def _raw(pid, name, set_code, num, oracle):
    return {
        "id": pid, "oracle_id": oracle, "name": name, "set": set_code,
        "set_name": set_code.upper(), "collector_number": num,
        "rarity": "uncommon", "lang": "en", "released_at": "2023-01-01",
        "finishes": ["nonfoil", "foil"], "frame": "2015",
        "border_color": "black", "promo_types": [], "games": ["paper"],
        "tcgplayer_id": 1,
        "prices": {"usd": "1.50", "usd_foil": "4.00", "usd_etched": None},
    }


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(tmp_path / "pair.json"))
    monkeypatch.setenv("DENSA_DEVICE_FILE", str(tmp_path / "device.json"))
    db = CardDatabase(db_path=tmp_path / "cards.db")
    db.upsert_printings([printing_row_from_scryfall(
        _raw(SOL, "Sol Ring", "cmm", "410", "o-sol"), "t")])
    db.close()
    a = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "versions.db")
    yield a
    a.close()


def test_the_index_page_carries_prices_year_and_finishes(api):
    """Six columns went; the phone's upsert nulled the other five."""
    row = api.catalogue_index_page()["data"]["rows"][0]
    assert row[0] == SOL and len(row) == 11
    assert row[6] == 1.5 and row[7] == 4.0          # price_usd, price_usd_foil
    assert row[8] is None                           # no artist on the desktop
    assert row[9] == 2023                           # released_year
    assert set(row[10].split(",")) == {"nonfoil", "foil"}


def test_resolving_a_slot_carries_the_foil_price(api):
    """The phone prices a foil slot from price_usd_foil; it never came."""
    found, missing = api.resolve_deck_slots([
        {"name": "Sol Ring", "printing_id": SOL},
        {"name": "No Such Card"},
    ])["data"]["slots"]
    assert found["price_usd_foil"] == 4.0
    assert "price_usd_foil" in missing and missing["price_usd_foil"] is None


def test_forgetting_with_no_deck_clears_every_row_for_the_card(tmp_path):
    """The phone's meaning. The desktop used to keep the deck's rows."""
    store = CollectionStore(db_path=tmp_path / "collection.db")
    store.wishlist_set("Sol Ring", 1)
    store.wishlist_set("Sol Ring", 1, deck_id="edh", deck_name="EDH")
    store.wishlist_set("Arcane Signet", 1, deck_id="edh", deck_name="EDH")
    assert store.wishlist_forget("Sol Ring") == 2
    left = [r["card_name"] for r in store.wishlist()]
    assert left == ["Arcane Signet"]


def test_forgetting_for_one_deck_still_touches_only_that_deck(tmp_path):
    store = CollectionStore(db_path=tmp_path / "collection.db")
    store.wishlist_set("Sol Ring", 1, deck_id="a")
    store.wishlist_set("Sol Ring", 1, deck_id="b")
    assert store.wishlist_forget("Sol Ring", deck_id="a") == 1


@pytest.mark.parametrize("route,method", [
    ("decks/list", "list_saved_decks"),
    ("decks/history", "get_deck_history"),
])
def test_an_error_is_returned_as_an_error(api, monkeypatch, route, method):
    """Folded inside {"decks": ...} it slipped past the phone's check."""
    monkeypatch.setattr(api, method, lambda *a, **k: {"ok": False, "error": "db locked"})
    reply = PhoneBridge(api).handle_api(route, {"deck_id": "x"})
    assert reply == {"ok": False, "error": "db locked"}


def test_a_success_keeps_its_named_key(api):
    reply = PhoneBridge(api).handle_api("decks/list", {})
    assert reply == {"decks": []}
