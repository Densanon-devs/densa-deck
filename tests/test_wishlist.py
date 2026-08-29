"""The wishlist: cards a deck wants that you do not own.

The rule everything here defends is that **a wishlist is not ownership**. A
wished-for card must never count toward what you have, what your collection is
worth, or what a deck still needs — the last of those being the nastiest,
because a card that looked owned would silently disappear from the list of
things to buy.

That is why the wishlist has its own table rather than living in
`collection_items` as a "wishlist collection". Ownership is computed by
queries spread across six modules; an exclusion missed in any one of them
would produce exactly that failure. A separate table cannot be got wrong.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.app.api import AppApi
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall
from densa_deck.models import Card, CardLayout, Legality

SOL = "11111111-1111-1111-1111-111111111111"
BOLT = "22222222-2222-2222-2222-222222222222"


def _printing(pid, name, set_code, num, oracle, usd="1.50"):
    return {
        "id": pid, "oracle_id": oracle, "name": name, "set": set_code,
        "set_name": set_code.upper(), "collector_number": num,
        "rarity": "uncommon", "lang": "en", "released_at": "2023-01-01",
        "finishes": ["nonfoil", "foil"], "frame": "2015",
        "border_color": "black", "promo_types": [], "games": ["paper"],
        "tcgplayer_id": 1,
        "prices": {"usd": usd, "usd_foil": "4.00", "usd_etched": None},
    }


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield CollectionStore(db_path=Path(tmp) / "collection.db")


@pytest.fixture
def api():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_printings([
            printing_row_from_scryfall(
                _printing(SOL, "Sol Ring", "cmm", "410", "o-sol"), "t"),
            printing_row_from_scryfall(
                _printing(BOLT, "Lightning Bolt", "lea", "161", "o-bolt",
                          "400.00"), "t"),
        ])
        db.upsert_cards([
            Card(scryfall_id=SOL, oracle_id="o-sol", name="Sol Ring",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
                 type_line="Artifact", color_identity=[],
                 legalities={"commander": Legality.LEGAL}),
            Card(scryfall_id=BOLT, oracle_id="o-bolt", name="Lightning Bolt",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{R}",
                 type_line="Instant", color_identity=[],
                 legalities={"commander": Legality.LEGAL}),
        ])
        db.close()
        a = AppApi(db_path=root / "cards.db",
                   version_db_path=root / "versions.db")
        a._collection_store = CollectionStore(db_path=root / "collection.db")
        yield a
        a.close()


def _owned(store) -> int:
    items, _ = store.list_items(limit=500)
    return sum(i.quantity for i in items)


class TestAWishlistIsNotOwnership:
    """The whole point. Every assertion here is a way of getting it wrong."""

    def test_wanting_a_card_does_not_make_you_own_it(self, store):
        store.wishlist_set("Black Lotus", 1, deck_id="d1")
        assert _owned(store) == 0

    def test_it_does_not_touch_the_collection_summary(self, store):
        store.add_copies("p1", "Sol Ring", quantity=2)
        before = store.summary()
        store.wishlist_set("Black Lotus", 4, deck_id="d1")
        after = store.summary()
        assert after.total_cards == before.total_cards == 2
        assert after.unique_cards == before.unique_cards

    def test_it_does_not_appear_in_the_collection_listing(self, store):
        store.wishlist_set("Black Lotus", 1, deck_id="d1")
        items, total = store.list_items(limit=50)
        assert items == [] and total == 0

    def test_it_does_not_count_as_owned_by_name(self, store):
        """The nastiest failure: a wished card looking owned would vanish
        from the list of things to buy."""
        store.wishlist_set("Black Lotus", 1, deck_id="d1")
        assert "Black Lotus" not in store.owned_by_name()
        assert store.owned_count("Black Lotus") == 0

    def test_it_survives_being_next_to_real_cards(self, store):
        store.add_copies("p1", "Sol Ring", quantity=3)
        store.wishlist_set("Sol Ring", 1, deck_id="d1")   # want a fourth
        assert _owned(store) == 3
        assert store.owned_count("Sol Ring") == 3


class TestKeepingTheList:
    def test_setting_replaces_rather_than_accumulates(self, store):
        """The shortfall is a fact about the current decklist.

        Adding would leave the list growing every time a deck was saved.
        """
        store.wishlist_set("Black Lotus", 2, deck_id="d1")
        store.wishlist_set("Black Lotus", 3, deck_id="d1")
        assert store.wishlist()[0]["quantity"] == 3

    def test_zero_removes_the_entry(self, store):
        store.wishlist_set("Black Lotus", 2, deck_id="d1")
        store.wishlist_set("Black Lotus", 0, deck_id="d1")
        assert store.wishlist() == []

    def test_two_decks_wanting_one_card_are_kept_apart(self, store):
        """Collapsing them loses the answer to "why is this on my list"."""
        store.wishlist_set("Black Lotus", 1, deck_id="d1", deck_name="Brew")
        store.wishlist_set("Black Lotus", 1, deck_id="d2", deck_name="Other")
        row = store.wishlist()[0]
        assert row["deck_count"] == 2
        assert {s["deck_name"] for s in row["wanted_by"]} == {"Brew", "Other"}

    def test_the_headline_number_is_what_one_deck_needs(self, store):
        """Two decks each wanting one card need ONE card between them unless
        both are built at once. Quoting two sends you shopping for a card you
        do not need."""
        store.wishlist_set("Black Lotus", 1, deck_id="d1")
        store.wishlist_set("Black Lotus", 1, deck_id="d2")
        row = store.wishlist()[0]
        assert row["quantity"] == 1
        assert row["quantity_across_decks"] == 2   # both, still available

    def test_clearing_one_deck_leaves_the_others(self, store):
        store.wishlist_set("Black Lotus", 1, deck_id="d1")
        store.wishlist_set("Mox Jet", 1, deck_id="d2")
        store.wishlist_clear_deck("d1")
        assert [w["card_name"] for w in store.wishlist()] == ["Mox Jet"]

    def test_a_card_added_by_hand_needs_no_deck(self, store):
        store.wishlist_set("Black Lotus", 1)
        assert store.wishlist()[0]["card_name"] == "Black Lotus"

    def test_a_nameless_entry_is_refused(self, store):
        with pytest.raises(ValueError):
            store.wishlist_set("   ", 1)


class TestDecksDriveTheList:
    """Saving a deck is what puts things on it."""

    def test_saving_a_deck_wants_what_it_lacks(self, api):
        api.save_deck_version("brew", "Shop brew",
                              "1 Sol Ring\n4 Lightning Bolt", "commander", "")
        wanted = {w["card_name"]: w["quantity"]
                  for w in api._get_collection_store().wishlist()}
        assert wanted == {"Sol Ring": 1, "Lightning Bolt": 4}

    def test_cards_you_own_do_not_go_on_it(self, api):
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        api.save_deck_version("brew", "Shop brew", "1 Sol Ring", "commander", "")
        assert api._get_collection_store().wishlist() == []

    def test_only_the_copies_you_lack_are_wanted(self, api):
        api.scan_commit(SOL, "Sol Ring", "nonfoil", "NM")
        api.save_deck_version("brew", "Brew", "4 Sol Ring", "commander", "")
        assert api._get_collection_store().wishlist()[0]["quantity"] == 3

    def test_re_saving_replaces_rather_than_piles_up(self, api):
        """Otherwise a card cut from a deck stays wanted forever."""
        api.save_deck_version("brew", "Brew", "4 Lightning Bolt", "commander", "")
        api.save_deck_version("brew", "Brew", "1 Lightning Bolt", "commander", "")
        rows = api._get_collection_store().wishlist()
        assert len(rows) == 1
        assert rows[0]["quantity"] == 1

    def test_a_card_cut_from_a_deck_leaves_the_list(self, api):
        api.save_deck_version("brew", "Brew", "1 Lightning Bolt", "commander", "")
        api.save_deck_version("brew", "Brew", "1 Sol Ring", "commander", "")
        names = [w["card_name"] for w in api._get_collection_store().wishlist()]
        assert names == ["Sol Ring"]

    def test_deleting_a_deck_forgets_what_it_wanted(self, api):
        """A deleted deck leaving cards behind has you shopping for something
        that no longer exists, with nothing to say why."""
        api.save_deck_version("brew", "Brew", "4 Lightning Bolt", "commander", "")
        api.delete_deck("brew")
        assert api._get_collection_store().wishlist() == []

    def test_saving_still_succeeds_if_the_wishlist_cannot_be_written(
            self, api, monkeypatch):
        """A worse list is survivable. A lost save is not."""
        monkeypatch.setattr(
            api, "_refresh_wishlist_for_deck",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
        result = api.save_deck_version("brew", "Brew", "1 Sol Ring",
                                       "commander", "")
        assert result.get("ok") is not False


class TestBuyingSomething:
    def test_acquiring_files_the_card_and_clears_the_want(self, api):
        """Filing it without clearing the list leaves you shopping for a card
        already in your box."""
        api.save_deck_version("brew", "Brew", "1 Sol Ring", "commander", "")
        assert api._get_collection_store().wishlist()[0]["card_name"] == "Sol Ring"

        api.wishlist_acquire(SOL, "Sol Ring", 1)

        assert _owned(api._get_collection_store()) == 1
        assert api._get_collection_store().wishlist() == []

    def test_buying_one_of_four_leaves_three_wanted(self, api):
        api.save_deck_version("brew", "Brew", "4 Sol Ring", "commander", "")
        api.wishlist_acquire(SOL, "Sol Ring", 1)
        assert api._get_collection_store().wishlist()[0]["quantity"] == 3

    def test_an_unknown_printing_is_refused(self, api):
        result = api.wishlist_acquire("not-a-printing", "Nothing", 1)
        assert result.get("ok") is False


class TestReadingTheList:
    def test_it_reports_what_finishing_would_cost(self, api):
        api.save_deck_version("brew", "Brew", "1 Lightning Bolt", "commander", "")
        listed = api.get_wishlist()["data"]
        assert listed["distinct_cards"] == 1
        assert listed["cost_usd"] == pytest.approx(400.00)

    def test_an_unknown_price_is_reported_not_counted_as_free(self, api):
        api.wishlist_add("A Card Nobody Has Priced", 1)
        listed = api.get_wishlist()["data"]
        assert listed["unpriced_cards"] == 1
        assert listed["cost_usd"] == 0

    def test_an_empty_list_is_empty_rather_than_an_error(self, api):
        listed = api.get_wishlist()["data"]
        assert listed["cards"] == []
        assert listed["cost_usd"] == 0


class TestNamingAPrinting:
    """Wanting a card and wanting THIS copy of it are different wants.

    A wish that names no printing is priced at whichever copy is cheapest
    each day — right for a shopping list. Somebody watching the Alpha Bolt
    wants ITS price, which is the whole reason to name one, so the name has
    to survive the trip from the button to the database.
    """

    def test_a_named_printing_reaches_the_store(self, api):
        api.wishlist_add("Lightning Bolt", 1, "", "", "", "lea", "161")
        row = api.get_wishlist()["data"]["cards"][0]
        assert row["set_code"] == "lea"
        assert row["collector_number"] == "161"

    def test_naming_none_still_means_any_copy(self, api):
        api.wishlist_add("Lightning Bolt", 1)
        row = api.get_wishlist()["data"]["cards"][0]
        assert not row["set_code"], "a wish for the card got pinned to a printing"

    def test_the_phone_can_name_one_too(self, api):
        """Same want, different door. The route used to drop the printing,
        so watching one from a shop quietly watched the cheapest instead."""
        from densa_deck.app.phone import PhoneBridge

        reply = PhoneBridge(api).handle_api("wishlist/add", {
            "card_name": "Lightning Bolt", "quantity": 1,
            "set_code": "lea", "collector_number": "161",
        })
        # A good reply is the payload itself; the envelope is stripped at
        # the route. An "ok" key here would mean it FAILED.
        assert "error" not in reply, reply
        row = api.get_wishlist()["data"]["cards"][0]
        assert row["set_code"] == "lea"
