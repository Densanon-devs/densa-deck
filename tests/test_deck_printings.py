"""A deck slot that names a printing, from the text box to the wishlist.

A decklist has always been a list of card NAMES, and for legality, combos and
goldfishing that is exactly right — those are facts about cards. It is wrong
for two questions people actually ask: what is this deck worth, and which of
my copies is sleeved in it. A full-art at $50 and a beat-up common at $16 are
not interchangeable to the person who owns both.

So a slot may now carry a set code and a collector number — the pair printed
in the bottom-left corner of the card, and the only form of "which exact
printing" that survives a plain text file. Empty means what it always meant:
any copy will do.

Every way this goes wrong is SILENT. A stripped set code, a printing-level
slot filled by the wrong card, a wishlist row that collides with another —
none of them raise, and all of them are wrong on a screen that looks right.
That is what this file is for.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from densa_deck.app.api import AppApi
from densa_deck.app.phone import PhoneBridge
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall
from densa_deck.deck.parser import parse_decklist
from densa_deck.models import Card, CardLayout, Legality
from densa_deck.versioning.storage import VersionStore

# Two printings of one card, at prices far enough apart that confusing them
# could not be a rounding error.
CHEAP = "11111111-1111-1111-1111-111111111111"
PIMP = "33333333-3333-3333-3333-333333333333"
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
def api():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_printings([
            printing_row_from_scryfall(
                _printing(CHEAP, "Sol Ring", "cmm", "410", "o-sol", "16.00"), "t"),
            printing_row_from_scryfall(
                _printing(PIMP, "Sol Ring", "ltc", "285", "o-sol", "50.00"), "t"),
            printing_row_from_scryfall(
                _printing(BOLT, "Lightning Bolt", "lea", "161", "o-bolt",
                          "400.00"), "t"),
        ])
        db.upsert_cards([
            Card(scryfall_id=CHEAP, oracle_id="o-sol", name="Sol Ring",
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


class TestTheParserKeepsWhatItUsedToThrowAway:
    """The set code used to be stripped outright, which made a printing-level
    list impossible to import — and impossible to export, since nothing
    downstream had it to write back."""

    def test_the_pair_lands_on_the_entry_and_not_on_the_name(self):
        entry = parse_decklist("1 Sol Ring (CMM) 410")[0]
        assert entry.card_name == "Sol Ring"
        assert entry.set_code == "CMM"
        assert entry.collector_number == "410"

    def test_a_bare_name_still_means_any_printing(self):
        entry = parse_decklist("1 Sol Ring")[0]
        assert entry.card_name == "Sol Ring"
        assert entry.set_code == ""
        assert entry.collector_number == ""

    def test_the_bracket_spelling_carries_a_set_and_no_number(self):
        entry = parse_decklist("2 Arcane Signet [ELD]")[0]
        assert entry.card_name == "Arcane Signet"
        assert entry.set_code == "ELD"
        assert entry.collector_number == ""

    def test_a_foil_marker_does_not_hide_the_number(self):
        # "*F*" sits AFTER the collector number, so stripping it second would
        # leave the set-code regex looking at the wrong end of the line.
        entry = parse_decklist("1 Sol Ring (CMM) 410 *F*")[0]
        assert entry.set_code == "CMM"
        assert entry.collector_number == "410"

    def test_lowercase_set_codes_survive(self):
        assert parse_decklist("1 Sol Ring (cmm) 410")[0].set_code == "cmm"


def _resolve(api, slots):
    """`@_safe` wraps every return in an envelope; the phone bridge unwraps it.

    Unwrapped here too, so a test reads the payload the caller sees rather
    than the transport around it.
    """
    reply = api.resolve_deck_slots(slots)
    return reply.get("data", reply)


class TestResolvingSlotsToPrintings:
    """One call answers the three questions a deck screen asks at once: which
    picture, what price, and — for a slot that came back from a text box with
    only a set and a number — which printing id."""

    def test_a_printing_id_comes_back_with_its_set_and_number(self, api):
        out = _resolve(api, [{"name": "Sol Ring", "printing_id": PIMP}])
        slot = out["slots"][0]
        assert slot["found"] is True
        assert slot["set_code"] == "ltc"
        assert slot["collector_number"] == "285"
        assert slot["price_usd"] == 50.0

    def test_a_set_and_number_resolve_back_to_an_id(self, api):
        # The trip a printing takes through the text box: the pair survives,
        # the id does not, and this is what puts it back.
        out = _resolve(api, [
            {"name": "Sol Ring", "set_code": "CMM", "collector_number": "410"},
        ])
        assert out["slots"][0]["printing_id"] == CHEAP

    def test_a_bare_name_gets_a_printing_to_stand_for_it(self, api):
        # Cheapest, matching the "build value" convention the rest of the
        # collection layer holds: what it would cost to put this card in a deck.
        out = _resolve(api, [{"name": "Sol Ring"}])
        assert out["slots"][0]["printing_id"] == CHEAP
        assert out["slots"][0]["price_usd"] == 16.0

    def test_the_catalogue_spelling_wins_over_the_one_typed(self, api):
        out = _resolve(api, [{"name": "sol ring"}])
        assert out["slots"][0]["name"] == "Sol Ring"

    def test_an_unknown_card_comes_back_as_not_found_rather_than_missing(self, api):
        # A reply shorter than the request leaves the caller working out which
        # slots went missing, and getting that wrong shows the wrong picture.
        out = _resolve(api, [
            {"name": "Sol Ring"}, {"name": "Black Lotus"},
        ])
        assert len(out["slots"]) == 2
        assert out["slots"][1]["found"] is False

    def test_no_slots_is_not_an_error(self, api):
        assert _resolve(api, [])["slots"] == []

    def test_the_phone_can_reach_it_and_cannot_ask_for_the_world(self, api):
        bridge = PhoneBridge(api)
        reply = bridge.handle_api("decks/resolve", {
            "slots": [{"name": "Sol Ring"}] * 900,
        })
        # Bounded on the way in: a phone must not be able to ask the desktop
        # to walk the whole catalogue in one request.
        assert len(reply["slots"]) == 400

    def test_a_malformed_payload_is_refused_rather_than_crashing(self, api):
        reply = PhoneBridge(api).handle_api("decks/resolve", {"slots": "nope"})
        assert reply["ok"] is False


class TestTheSaveRemembersWhichPrinting:
    def test_the_name_keyed_decklist_is_untouched(self, api):
        # Everything downstream — diff, trends, the eleven combo-aware layers,
        # the analyst — reads `decklist` as {name: quantity} and is right to.
        out = api.save_deck_version("d1", "Pimped", "1 Sol Ring (LTC) 285")
        snap = api._get_vstore().get_latest("d1")
        assert snap.decklist == {"Sol Ring": 1}

    def test_the_printing_rides_along_beside_it(self, api):
        api.save_deck_version("d1", "Pimped", "1 Sol Ring (LTC) 285")
        snap = api._get_vstore().get_latest("d1")
        assert snap.printings == [{
            "card_name": "Sol Ring", "set_code": "LTC",
            "collector_number": "285", "quantity": 1, "zone": "mainboard",
        }]

    def test_a_name_only_deck_records_no_printings_at_all(self, api):
        api.save_deck_version("d1", "Plain", "1 Sol Ring")
        assert api._get_vstore().get_latest("d1").printings == []

    def test_a_version_saved_before_printings_existed_reads_back_empty(self):
        # Rows written by an earlier build have two keys in decklist_json, not
        # three. Reading one must not raise and must not invent a printing.
        with tempfile.TemporaryDirectory() as tmp:
            store = VersionStore(db_path=Path(tmp) / "versions.db")
            store.save_version(deck_id="old", name="Legacy", format="commander",
                               decklist={"Sol Ring": 1}, zones={})
            conn = store.connect()
            conn.execute(
                "UPDATE deck_versions SET decklist_json = ?",
                ('{"cards": {"Sol Ring": 1}, "zones": {}}',))
            conn.commit()
            snap = store.get_latest("old")
            assert snap.decklist == {"Sol Ring": 1}
            assert snap.printings == []
            store.close()


class TestTheWishlistKnowsWhichCopyIsWanted:
    def test_owning_the_wrong_printing_does_not_finish_the_deck(self, api):
        """The failure this exists to stop: you own the $16 common, the deck
        asks for the $50 full-art, and the app says you are done."""
        api._get_collection_store().add_copies(CHEAP, "Sol Ring", quantity=1)
        api.save_deck_version("d1", "Pimped", "1 Sol Ring (LTC) 285")
        rows = api._get_collection_store().wishlist_for_deck("d1")
        assert len(rows) == 1
        assert rows[0]["card_name"] == "Sol Ring"
        assert rows[0]["set_code"] == "LTC"
        assert rows[0]["quantity"] == 1

    def test_owning_the_right_printing_does(self, api):
        api._get_collection_store().add_copies(PIMP, "Sol Ring", quantity=1)
        api.save_deck_version("d1", "Pimped", "1 Sol Ring (LTC) 285")
        assert api._get_collection_store().wishlist_for_deck("d1") == []

    def test_a_name_only_slot_is_still_filled_by_anything(self, api):
        # The old default has to survive untouched. An import is name-only and
        # must not suddenly report every card missing.
        api._get_collection_store().add_copies(CHEAP, "Sol Ring", quantity=1)
        api.save_deck_version("d1", "Plain", "1 Sol Ring")
        assert api._get_collection_store().wishlist_for_deck("d1") == []

    def test_one_physical_card_cannot_fill_two_slots(self, api):
        """The exact slot claims the copy first. Without taking it out of the
        name pool too, the loose slot would find it again and the deck would
        read as complete with a sleeve empty."""
        api._get_collection_store().add_copies(PIMP, "Sol Ring", quantity=1)
        api.save_deck_version("d1", "Both", "1 Sol Ring (LTC) 285\n1 Sol Ring")
        rows = api._get_collection_store().wishlist_for_deck("d1")
        assert len(rows) == 1
        assert rows[0]["set_code"] == "", "the loose slot is the short one"

    def test_two_printings_of_one_card_are_two_rows(self, api):
        # They are two purchases. A list that could only hold one of them —
        # which the old unique index enforced — would hide a real gap.
        api.save_deck_version("d1", "Both",
                              "1 Sol Ring (LTC) 285\n1 Sol Ring (CMM) 410")
        rows = api._get_collection_store().wishlist_for_deck("d1")
        assert {r["set_code"] for r in rows} == {"LTC", "CMM"}

    def test_a_printing_the_catalogue_cannot_place_is_still_listed(self, api):
        # A card you want and cannot match is a stronger reason to list it,
        # not a weaker one.
        api.save_deck_version("d1", "Odd", "1 Sol Ring (ZZZ) 999")
        rows = api._get_collection_store().wishlist_for_deck("d1")
        assert rows and rows[0]["set_code"] == "ZZZ"

    def test_saving_again_replaces_rather_than_accumulates(self, api):
        api.save_deck_version("d1", "Pimped", "2 Sol Ring (LTC) 285")
        api.save_deck_version("d1", "Pimped", "1 Sol Ring (LTC) 285")
        rows = api._get_collection_store().wishlist_for_deck("d1")
        assert len(rows) == 1 and rows[0]["quantity"] == 1


class TestBuyingOnePrintingDoesNotClearAnother:
    """`wishlist_acquire` files the card and takes it off the lists that
    wanted it. Both halves have to respect which printing was actually
    bought, or the list quietly says you own a card still sitting in a shop.
    """

    def test_buying_the_right_printing_clears_the_row(self, api):
        api.save_deck_version("d1", "Pimped", "1 Sol Ring (LTC) 285")
        assert api._get_collection_store().wishlist_for_deck("d1")
        api.wishlist_acquire(PIMP, "Sol Ring", 1)
        assert api._get_collection_store().wishlist_for_deck("d1") == []

    def test_buying_a_different_printing_leaves_it_alone(self, api):
        api.save_deck_version("d1", "Pimped", "1 Sol Ring (LTC) 285")
        api.wishlist_acquire(CHEAP, "Sol Ring", 1)
        rows = api._get_collection_store().wishlist_for_deck("d1")
        assert len(rows) == 1, "the full-art is still wanted"
        assert rows[0]["set_code"] == "LTC"

    def test_the_row_is_rewritten_in_place_not_duplicated(self, api):
        # Writing back without the printing would leave the checked row
        # untouched and create a second, name-only one beside it.
        api.save_deck_version("d1", "Pimped", "2 Sol Ring (LTC) 285")
        api.wishlist_acquire(PIMP, "Sol Ring", 1)
        rows = api._get_collection_store().wishlist_for_deck("d1")
        assert len(rows) == 1
        assert rows[0]["set_code"] == "LTC"
        assert rows[0]["quantity"] == 1

    def test_a_name_only_row_is_still_cleared_by_any_printing(self, api):
        api.save_deck_version("d1", "Plain", "1 Sol Ring")
        api.wishlist_acquire(CHEAP, "Sol Ring", 1)
        assert api._get_collection_store().wishlist_for_deck("d1") == []


class TestWhatTheWishlistSaysItCosts:
    def test_a_printing_level_row_is_quoted_at_its_own_price(self, api):
        # Quoting the cheapest printing would say the full-art your deck asked
        # for costs $16 — the exact number that made recording it worth doing.
        api.save_deck_version("d1", "Pimped", "1 Sol Ring (LTC) 285")
        out = api.get_wishlist()
        data = out.get("data", out)
        assert data["cards"][0]["price_usd"] == 50.0
        assert data["cost_usd"] == 50.0

    def test_a_name_only_row_is_quoted_at_the_cheapest(self, api):
        api.save_deck_version("d1", "Plain", "1 Sol Ring")
        data = api.get_wishlist()
        data = data.get("data", data)
        assert data["cards"][0]["price_usd"] == 16.0

    def test_a_printing_the_catalogue_cannot_place_is_unpriced_not_free(self, api):
        api.save_deck_version("d1", "Odd", "1 Sol Ring (ZZZ) 999")
        data = api.get_wishlist()
        data = data.get("data", data)
        assert data["cards"][0]["price_usd"] is None
        assert data["cost_usd"] == 0.0
        assert data["unpriced_cards"] == 1


class TestTakingSomethingOffTheList:
    def test_removing_by_name_takes_every_printing_of_it(self, api):
        # The intent behind the button is about the card. Leaving one row
        # behind reads as the button having done nothing.
        api.save_deck_version("d1", "Both",
                              "1 Sol Ring (LTC) 285\n1 Sol Ring (CMM) 410")
        assert len(api._get_collection_store().wishlist_for_deck("d1")) == 2
        api.wishlist_remove("Sol Ring", "d1")
        assert api._get_collection_store().wishlist_for_deck("d1") == []

    def test_the_deck_rewrite_is_still_exact_by_printing(self, api):
        # The other direction, and it must NOT behave like the button: a deck
        # can legitimately want two printings, and rewriting one slot's
        # shortfall must leave the other alone.
        store = api._get_collection_store()
        store.wishlist_set("Sol Ring", 1, deck_id="d1", set_code="LTC",
                           collector_number="285")
        store.wishlist_set("Sol Ring", 1, deck_id="d1", set_code="CMM",
                           collector_number="410")
        store.wishlist_set("Sol Ring", 0, deck_id="d1", set_code="LTC",
                           collector_number="285")
        rows = store.wishlist_for_deck("d1")
        assert [r["set_code"] for r in rows] == ["CMM"]


class TestAnExistingWishlistSurvivesTheColumns:
    def test_a_database_without_them_opens_and_keeps_its_rows(self):
        """The migration runs BEFORE the schema statements, because those now
        create a unique index over the new columns — and on a database that
        predates them that statement fails outright and the app will not open.

        A fresh database never shows this, which is exactly how this class of
        bug survives a test suite and appears only against a real one.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collection.db"
            # A wishlist table as an earlier build wrote it: no set_code, no
            # collector_number, and the old two-column unique index.
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE wishlist_items (
                wish_id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_name TEXT NOT NULL,
                oracle_id TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                deck_id TEXT NOT NULL DEFAULT '',
                deck_name TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)""")
            conn.execute("""CREATE UNIQUE INDEX idx_wish_card_deck
                ON wishlist_items(card_name COLLATE NOCASE, deck_id)""")
            conn.execute(
                "INSERT INTO wishlist_items (card_name, quantity, deck_id,"
                " created_at, updated_at) VALUES ('Black Lotus', 1, 'd1', 't', 't')")
            conn.commit()
            conn.close()

            store = CollectionStore(db_path=path)
            rows = store.wishlist()
            assert len(rows) == 1
            assert rows[0]["card_name"] == "Black Lotus"
            assert rows[0]["set_code"] == "", "an old row wants any printing"

            # And the old index is gone, so the second printing can go on.
            store.wishlist_set("Sol Ring", 1, deck_id="d1", set_code="LTC",
                               collector_number="285")
            store.wishlist_set("Sol Ring", 1, deck_id="d1", set_code="CMM",
                               collector_number="410")
            assert len([r for r in store.wishlist()
                        if r["card_name"] == "Sol Ring"]) == 2


class TestOwnershipByPrinting:
    def test_copies_are_counted_per_printing(self, api):
        store = api._get_collection_store()
        store.add_copies(CHEAP, "Sol Ring", quantity=2)
        store.add_copies(PIMP, "Sol Ring", quantity=1)
        by_printing = store.owned_by_printing()
        assert by_printing[CHEAP] == 2
        assert by_printing[PIMP] == 1
        # And the name-level answer is unchanged, because both are true.
        assert store.owned_by_name()["sol ring"] == 3

    def test_finishes_of_one_printing_are_one_stack_here(self, api):
        # A foil and a nonfoil are different objects and different prices, but
        # both are copies of the same printing — which is the question a deck
        # slot asks.
        store = api._get_collection_store()
        store.add_copies(PIMP, "Sol Ring", quantity=1, finish="foil")
        store.add_copies(PIMP, "Sol Ring", quantity=1, finish="nonfoil")
        assert store.owned_by_printing()[PIMP] == 2


class TestClearingFromTheDesktop:
    """The half a phone wipe cannot do.

    Wiping a phone removes its mirror and leaves NOTHING behind to tell the
    other machine what happened — no deltas, no events. The cards live on the
    PC, so that is where a clear has to happen, and it has to be logged or the
    two devices land straight back in disagreement.
    """

    def test_it_refuses_without_the_word(self, api):
        api._get_collection_store().add_copies(CHEAP, "Sol Ring", quantity=2)
        out = api.clear_all_cards()
        assert out["ok"] is False
        assert out["error_type"] == "ConfirmationRequired"
        assert api._get_collection_store().list_items()[1] == 1, "nothing went"

    def test_a_wrong_word_is_not_close_enough(self, api):
        api._get_collection_store().add_copies(CHEAP, "Sol Ring", quantity=2)
        assert api.clear_all_cards("yes")["ok"] is False
        assert api._get_collection_store().list_items()[1] == 1

    def test_the_word_clears_it(self, api):
        store = api._get_collection_store()
        store.add_copies(CHEAP, "Sol Ring", quantity=2)
        store.add_copies(BOLT, "Lightning Bolt", quantity=4)
        out = api.clear_all_cards("CLEAR")
        data = out.get("data", out)
        assert data["cards_removed"] == 6
        assert store.list_items()[1] == 0

    def test_lower_case_is_accepted_because_the_intent_is_the_same(self, api):
        api._get_collection_store().add_copies(CHEAP, "Sol Ring", quantity=1)
        out = api.clear_all_cards("clear")
        assert out.get("data", out)["cards_removed"] == 1

    def test_the_phone_is_told(self, api):
        """The whole point. A clear that only happened locally puts the two
        devices right back where they started."""
        store = api._get_collection_store()
        store.add_copies(CHEAP, "Sol Ring", quantity=2)
        store.add_copies(BOLT, "Lightning Bolt", quantity=1)
        out = api.clear_all_cards("CLEAR")
        assert out.get("data", out)["synced_events"] == 2

        events, _cursor = api._get_sync().log.since(0)
        kinds = [e.kind for e in events]
        assert kinds.count("stack-delta") >= 2

    def test_the_removals_are_negative_and_name_the_card(self, api):
        store = api._get_collection_store()
        store.add_copies(CHEAP, "Sol Ring", quantity=3)
        api.clear_all_cards("CLEAR")
        events, _cursor = api._get_sync().log.since(0)
        deltas = [e.payload for e in events if e.kind == "stack-delta"]
        assert any(d["delta"] == -3 and d["printing_id"] == CHEAP
                   for d in deltas)

    def test_clearing_an_empty_collection_sends_nothing(self, api):
        out = api.clear_all_cards("CLEAR")
        assert out.get("data", out)["synced_events"] == 0
