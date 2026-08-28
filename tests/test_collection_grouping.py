"""Isolating part of a collection so it can leave it.

The job: you own five thousand cards and a thousand of them are going — sold
as a bundle, or a deck handed to a friend, or forty cards given away. Finding
those thousand by searching for each one is not a workflow, and a collection
that cannot let go of part of itself is a collection you cannot sell out of.

What makes this safe is that collections are FILTERS. Tagging a thousand cards
moves nothing, changes nothing you own, and is undone by untagging. Exactly
one call in this arc is destructive, and these tests are largely about keeping
it that way — that building a group cannot lose a card, and that the one thing
that can is the one thing that says so.
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
from pathlib import Path

import pytest

from densa_deck.collection.grouping import (
    group_contents,
    group_from_deck,
    retire_group,
    tag_item,
    tag_owned_printing,
    untag_item,
)
from densa_deck.collection.manifest import export_manifest, suggested_filename
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall
from densa_deck.deck.parser import parse_decklist
from densa_deck.models import Card, CardLayout, Deck, Legality

SOL = "11111111-1111-1111-1111-111111111111"
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
        "prices": {"usd": usd, "usd_foil": "40.00", "usd_etched": None},
    }


@pytest.fixture
def kit():
    """A store, a card database, and a group to put things in."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_printings([
            printing_row_from_scryfall(
                _printing(SOL, "Sol Ring", "cmm", "410", "o-sol", "16.00"), "t"),
            printing_row_from_scryfall(
                _printing(PIMP, "Sol Ring", "ltc", "285", "o-sol", "50.00"), "t"),
            printing_row_from_scryfall(
                _printing(BOLT, "Lightning Bolt", "lea", "161", "o-bolt",
                          "400.00"), "t"),
        ])
        db.upsert_cards([
            Card(scryfall_id=SOL, oracle_id="o-sol", name="Sol Ring",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
                 type_line="Artifact", legalities={"commander": Legality.LEGAL}),
            Card(scryfall_id=BOLT, oracle_id="o-bolt", name="Lightning Bolt",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{R}",
                 type_line="Instant", legalities={"commander": Legality.LEGAL}),
        ])
        store = CollectionStore(db_path=root / "collection.db")
        bundle = store.create_collection("Bundle for Dave")
        yield store, db, bundle["collection_uid"]
        db.close()


def _uid(store, name):
    return store.create_collection(name)["collection_uid"]


class TestTaggingWhatYouAlreadyOwn:
    """The scanner's second mode, and the point of the whole feature: walking
    a physical pile picking cards out, without the scanner treating each one
    as a new acquisition."""

    def test_it_tags_rather_than_adding_a_copy(self, kit):
        # The failure this exists to stop. `scan_commit` would make this two.
        store, db, bundle = kit
        store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_owned_printing(store, SOL, bundle)
        items, _ = store.list_items(printing_id=SOL)
        assert sum(i.quantity for i in items) == 1, "still one card"

    def test_the_card_lands_in_the_group(self, kit):
        store, db, bundle = kit
        store.add_copies(SOL, "Sol Ring", quantity=1)
        out = tag_owned_printing(store, SOL, bundle)
        assert out["tagged"] == 1
        lists = store.collections_for_item(out["item_id"])
        assert "Bundle for Dave" in [c["name"] for c in lists]

    def test_it_stays_in_every_other_list_it_was_in(self, kit):
        # Collections are filters. Tagging for a sale must not quietly pull a
        # card out of the deck list that also mentions it.
        store, db, bundle = kit
        other = _uid(store, "Ravnica set")
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, other)
        tag_owned_printing(store, SOL, bundle)
        names = {c["name"] for c in store.collections_for_item(item.item_id)}
        assert {"Ravnica set", "Bundle for Dave"} <= names

    def test_rescanning_says_already_in_rather_than_failing(self, kit):
        # On a physical pass you WILL scan a card twice, and "already in" is a
        # different reassurance from "added" — both are fine, neither is an
        # error, and the caller needs to be able to say which happened.
        store, db, bundle = kit
        store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_owned_printing(store, SOL, bundle)
        again = tag_owned_printing(store, SOL, bundle)
        assert again["tagged"] == 0
        assert again["already_in"] is True

    def test_a_card_you_do_not_own_is_reported_not_added(self, kit):
        """The quiet one. Silently adding it would mean a bundle manifest
        listing a card that was never in the box."""
        store, db, bundle = kit
        out = tag_owned_printing(store, SOL, bundle)
        assert out["owned"] == 0
        assert out["tagged"] == 0
        items, total = store.list_items()
        assert total == 0, "nothing was created"

    def test_owning_it_two_ways_asks_instead_of_guessing(self, kit):
        # A foil and a nonfoil are different objects worth different money.
        # Picking one for the user tags the wrong physical card.
        store, db, bundle = kit
        store.add_copies(SOL, "Sol Ring", quantity=1, finish="foil")
        store.add_copies(SOL, "Sol Ring", quantity=1, finish="nonfoil")
        out = tag_owned_printing(store, SOL, bundle)
        assert out["tagged"] == 0
        assert len(out["candidates"]) == 2
        assert {c["finish"] for c in out["candidates"]} == {"foil", "nonfoil"}

    def test_naming_the_finish_answers_the_question(self, kit):
        store, db, bundle = kit
        store.add_copies(SOL, "Sol Ring", quantity=1, finish="foil")
        store.add_copies(SOL, "Sol Ring", quantity=1, finish="nonfoil")
        out = tag_owned_printing(store, SOL, bundle, finish="foil")
        assert out["tagged"] == 1
        assert out["candidates"] == []

    def test_untagging_takes_it_out_and_leaves_the_card(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=2)
        tag_item(store, item.item_id, bundle)
        untag_item(store, item.item_id, bundle)
        assert "Bundle for Dave" not in [
            c["name"] for c in store.collections_for_item(item.item_id)]
        items, _ = store.list_items(printing_id=SOL)
        assert sum(i.quantity for i in items) == 2, "a filter cannot destroy"

    def test_an_unknown_group_is_refused(self, kit):
        store, db, _ = kit
        store.add_copies(SOL, "Sol Ring", quantity=1)
        with pytest.raises(ValueError):
            tag_owned_printing(store, SOL, "no-such-uid")


class TestGivingAwayADeck:
    def _deck(self, text):
        return Deck(name="Atraxa", entries=parse_decklist(text))

    def test_the_deck_becomes_a_group_of_real_cards(self, kit):
        store, db, bundle = kit
        store.add_copies(SOL, "Sol Ring", quantity=1)
        store.add_copies(BOLT, "Lightning Bolt", quantity=1)
        out = group_from_deck(store, db, self._deck("1 Sol Ring\n1 Lightning Bolt"),
                              bundle)
        assert out["stacks_tagged"] == 2
        assert out["cards_found"] == 2
        assert out["missing"] == []

    def test_cards_you_do_not_own_are_reported_not_dropped(self, kit):
        # Handing over a group silently eleven cards short is worse than one
        # that says so — the point is to hand over a KNOWN quantity.
        store, db, bundle = kit
        store.add_copies(SOL, "Sol Ring", quantity=1)
        out = group_from_deck(store, db, self._deck("1 Sol Ring\n2 Lightning Bolt"),
                              bundle)
        assert out["missing"] == [
            {"card_name": "Lightning Bolt", "needed": 2, "found": 0, "short": 2},
        ]

    def test_it_gives_away_the_cheap_copy_first(self, kit):
        """Owning both, the deck being handed over should not quietly take the
        $50 one. Same reasoning `deck_value` uses for what a deck is worth."""
        store, db, bundle = kit
        cheap = store.add_copies(SOL, "Sol Ring", quantity=1)
        dear = store.add_copies(PIMP, "Sol Ring", quantity=1)
        group_from_deck(store, db, self._deck("1 Sol Ring"), bundle)
        tagged = {c["name"] for c in store.collections_for_item(cheap.item_id)}
        untouched = {c["name"] for c in store.collections_for_item(dear.item_id)}
        assert "Bundle for Dave" in tagged
        assert "Bundle for Dave" not in untouched

    def test_a_substring_name_does_not_drag_in_another_card(self, kit):
        # `list_items(name_like=...)` is a substring match, so a deck asking
        # for "Bolt" must not sweep up "Lightning Bolt".
        store, db, bundle = kit
        store.add_copies(BOLT, "Lightning Bolt", quantity=1)
        out = group_from_deck(store, db, self._deck("1 Bolt"), bundle)
        assert out["stacks_tagged"] == 0
        assert out["missing"][0]["card_name"] == "Bolt"

    def test_it_takes_as_many_copies_as_the_deck_wants(self, kit):
        store, db, bundle = kit
        store.add_copies(BOLT, "Lightning Bolt", quantity=4)
        out = group_from_deck(store, db, self._deck("4 Lightning Bolt"), bundle)
        assert out["cards_found"] == 4
        assert out["missing"] == []

    def test_an_empty_deck_tags_nothing(self, kit):
        store, db, bundle = kit
        out = group_from_deck(store, db, Deck(name="Empty", entries=[]), bundle)
        assert out["stacks_tagged"] == 0


class TestLookingBeforeYouLeap:
    def test_it_counts_and_prices_what_is_in_there(self, kit):
        store, db, bundle = kit
        item = store.add_copies(PIMP, "Sol Ring", quantity=2)
        tag_item(store, item.item_id, bundle)
        review = group_contents(store, db, bundle)
        assert review["stacks"] == 1
        assert review["copies"] == 2
        assert review["value_usd"] == 100.0

    def test_it_names_the_cards_so_a_manifest_can_be_checked(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, bundle)
        card = group_contents(store, db, bundle)["cards"][0]
        assert card["card_name"] == "Sol Ring"
        assert card["set_code"] == "cmm"
        assert card["collector_number"] == "410"

    def test_it_warns_about_a_card_your_decks_still_want(self, kit):
        """The honest version of "are you sure". One copy, two lists claiming
        it — the alternative is finding out at the table."""
        store, db, bundle = kit
        deck_list = _uid(store, "Atraxa")
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, deck_list)
        tag_item(store, item.item_id, bundle)
        review = group_contents(store, db, bundle)
        assert [w["card_name"] for w in review["wanted_elsewhere"]] == ["Sol Ring"]

    def test_a_card_with_copies_to_spare_is_not_a_warning(self, kit):
        # Selling one of the two you own leaves one for the deck. Warning
        # about that would make the warning worth ignoring.
        store, db, bundle = kit
        other = _uid(store, "Atraxa")
        item = store.add_copies(SOL, "Sol Ring", quantity=2)
        tag_item(store, item.item_id, other)
        tag_item(store, item.item_id, bundle, quantity=1)
        assert group_contents(store, db, bundle)["wanted_elsewhere"] == []

    def test_selling_the_whole_stack_out_from_under_a_deck_does_warn(self, kit):
        # Same two copies, but the bundle takes both. The deck is left with
        # nothing, and that is exactly the thing you find out at the table.
        store, db, bundle = kit
        other = _uid(store, "Atraxa")
        item = store.add_copies(SOL, "Sol Ring", quantity=2)
        tag_item(store, item.item_id, other)
        tag_item(store, item.item_id, bundle)
        warned = group_contents(store, db, bundle)["wanted_elsewhere"]
        assert [w["card_name"] for w in warned] == ["Sol Ring"]
        assert warned[0]["collections"] == ["Atraxa"], "not itself, not the default"

    def test_where_a_card_lives_is_not_a_claim_on_it(self, kit):
        # Every stack is filed in the default collection. Counting that as a
        # list wanting the card would flag every single card in the bundle.
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, bundle)
        assert group_contents(store, db, bundle)["wanted_elsewhere"] == []

    def test_a_group_can_take_part_of_a_stack(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=4)
        tag_item(store, item.item_id, bundle, quantity=2)
        review = group_contents(store, db, bundle)
        assert review["copies"] == 2, "two are leaving"
        assert review["cards"][0]["owned"] == 4, "four are owned"
        assert review["value_usd"] == 32.0, "priced on what leaves"

    def test_an_empty_group_reviews_as_empty_rather_than_failing(self, kit):
        store, db, bundle = kit
        review = group_contents(store, db, bundle)
        assert review["stacks"] == 0 and review["cards"] == []


class TestTheManifest:
    def test_csv_has_the_four_things_that_decide_what_a_card_is_worth(self, kit):
        store, db, bundle = kit
        item = store.add_copies(PIMP, "Sol Ring", quantity=2, finish="foil",
                                condition="LP")
        tag_item(store, item.item_id, bundle)
        text, meta = export_manifest(store, db, collection_uid=bundle, fmt="csv")

        body = "\n".join(l for l in text.splitlines() if not l.startswith("#"))
        row = next(iter(csv.DictReader(io.StringIO(body))))
        assert row["card_name"] == "Sol Ring"
        assert row["set_code"] == "LTC"
        assert row["collector_number"] == "285"
        assert row["finish"] == "foil"
        assert row["condition"] == "LP"
        assert row["quantity"] == "2"

    def test_the_provenance_sits_after_the_data_not_before_it(self, kit):
        """A commented banner above the header is friendly to a person and
        breaks every importer ever written — and this file exists to be
        imported."""
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, bundle)
        text, _ = export_manifest(store, db, collection_uid=bundle, fmt="csv")
        lines = text.splitlines()
        assert lines[0].startswith("card_name,"), "the header is the first line"
        assert any(l.startswith("#") for l in lines), "and the note is somewhere"
        assert "snapshot" in text, "prices are dated, not quoted"

    def test_an_unpriced_card_is_an_empty_cell_not_a_zero(self, kit):
        # A spreadsheet SUMs a zero and quietly under-reports the total.
        store, db, bundle = kit
        item = store.add_copies("unknown-printing", "Mystery Card", quantity=1)
        tag_item(store, item.item_id, bundle)
        text, _ = export_manifest(store, db, collection_uid=bundle, fmt="csv")
        body = "\n".join(l for l in text.splitlines() if not l.startswith("#"))
        row = next(iter(csv.DictReader(io.StringIO(body))))
        assert row["unit_price_usd"] == ""

    def test_the_decklist_form_is_what_every_site_imports(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=3)
        tag_item(store, item.item_id, bundle)
        text, _ = export_manifest(store, db, collection_uid=bundle, fmt="decklist")
        assert "3 Sol Ring (CMM) 410" in text

    def test_two_finishes_of_one_printing_collapse_in_the_decklist(self, kit):
        # That format cannot say what separates them, and a reader would see
        # the same card listed twice with no explanation.
        store, db, bundle = kit
        for finish in ("foil", "nonfoil"):
            item = store.add_copies(SOL, "Sol Ring", quantity=1, finish=finish)
            tag_item(store, item.item_id, bundle)
        text, _ = export_manifest(store, db, collection_uid=bundle, fmt="decklist")
        assert text.count("Sol Ring") == 1
        assert "2 Sol Ring (CMM) 410" in text

    def test_json_round_trips(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, bundle)
        text, _ = export_manifest(store, db, collection_uid=bundle, fmt="json")
        data = json.loads(text)
        assert data["cards"][0]["card_name"] == "Sol Ring"
        assert data["manifest"]["name"] == "Bundle for Dave"

    def test_no_group_means_everything_you_own(self, kit):
        store, db, bundle = kit
        store.add_copies(SOL, "Sol Ring", quantity=1)
        store.add_copies(BOLT, "Lightning Bolt", quantity=1)
        _, meta = export_manifest(store, db, fmt="csv")
        assert meta["stacks"] == 2
        assert meta["name"] == "Everything I own"

    def test_a_group_is_only_its_own_cards(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        store.add_copies(BOLT, "Lightning Bolt", quantity=1)
        tag_item(store, item.item_id, bundle)
        _, meta = export_manifest(store, db, collection_uid=bundle, fmt="csv")
        assert meta["stacks"] == 1

    def test_it_lists_what_is_LEAVING_not_what_the_stack_holds(self, kit):
        """A bundle taking two of the four you own must promise two.

        Listing four is a manifest the buyer counts against the box, and it
        comes up short — which is the kind of mistake that ends a sale.
        """
        store, db, bundle = kit
        item = store.add_copies(BOLT, "Lightning Bolt", quantity=4)
        tag_item(store, item.item_id, bundle, quantity=2)
        text, meta = export_manifest(store, db, collection_uid=bundle, fmt="csv")
        assert meta["copies"] == 2
        body = "\n".join(l for l in text.splitlines() if not l.startswith("#"))
        row = next(iter(csv.DictReader(io.StringIO(body))))
        assert row["quantity"] == "2"
        assert row["line_price_usd"] == "800.0", "priced on what leaves"

    def test_the_whole_collection_means_whole_stacks(self, kit):
        # A backup is not a bundle. With no group, every copy is in scope.
        store, db, bundle = kit
        item = store.add_copies(BOLT, "Lightning Bolt", quantity=4)
        tag_item(store, item.item_id, bundle, quantity=2)
        _, meta = export_manifest(store, db, fmt="csv")
        assert meta["copies"] == 4

    def test_an_unknown_format_says_which_ones_exist(self, kit):
        store, db, bundle = kit
        with pytest.raises(ValueError, match="csv"):
            export_manifest(store, db, collection_uid=bundle, fmt="pdf")

    def test_the_filename_says_what_it_is(self, kit):
        # This lands in a Downloads folder next to other people's files, and
        # `export.csv` is one nobody can identify a week later.
        name = suggested_filename(
            {"name": "Bundle for Dave", "exported_at": "2026-08-27T10:00:00+00:00"},
            "csv")
        assert name == "Bundle-for-Dave-2026-08-27.csv"


class TestLettingItGo:
    """The one destructive call in the arc. Everything before it is a filter."""

    def test_the_copies_leave_the_collection(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=3)
        tag_item(store, item.item_id, bundle)
        out = retire_group(store, db, bundle)
        assert out["copies_removed"] == 3
        items, total = store.list_items()
        assert total == 0

    def test_cards_outside_the_group_are_untouched(self, kit):
        # The obvious catastrophe, and the one worth a test of its own.
        store, db, bundle = kit
        going = store.add_copies(SOL, "Sol Ring", quantity=1)
        staying = store.add_copies(BOLT, "Lightning Bolt", quantity=4)
        tag_item(store, going.item_id, bundle)
        retire_group(store, db, bundle)
        left, _ = store.list_items()
        assert [i.printing_id for i in left] == [BOLT]
        assert left[0].quantity == 4

    def test_it_reports_what_left_and_what_it_was_worth(self, kit):
        # A destructive action that reports only "done" gives you nothing to
        # check against afterwards.
        store, db, bundle = kit
        item = store.add_copies(PIMP, "Sol Ring", quantity=2)
        tag_item(store, item.item_id, bundle)
        out = retire_group(store, db, bundle)
        assert out["stacks_removed"] == 1
        assert out["value_usd"] == 100.0

    def test_a_giveaway_records_no_sale(self, kit):
        # A real case, and not one that should have to pretend it earned
        # nothing by recording a zero-pound sale.
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, bundle)
        out = retire_group(store, db, bundle)
        assert out["sale_recorded"] is False
        from densa_deck.collection.reseller import list_sales
        assert list_sales(store) == []

    def test_a_sale_lands_in_the_ledger(self, kit):
        store, db, bundle = kit
        item = store.add_copies(PIMP, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, bundle)
        out = retire_group(store, db, bundle, sale_price_usd=40.0, sold_to="Dave")
        assert out["sale_recorded"] is True
        from densa_deck.collection.reseller import list_sales
        sales = list_sales(store)
        assert len(sales) == 1
        assert sales[0]["card_name"] == "Sol Ring"

    def test_the_lot_price_is_split_by_what_each_card_is_worth(self, kit):
        """Not the total on every row, and not a zero. A $66 bundle of a $50
        card and a $16 card is roughly a $50 line and a $16 line."""
        store, db, bundle = kit
        for pid in (PIMP, SOL):
            item = store.add_copies(pid, "Sol Ring", quantity=1)
            tag_item(store, item.item_id, bundle)
        retire_group(store, db, bundle, sale_price_usd=66.0)
        from densa_deck.collection.reseller import list_sales
        prices = sorted(s["sale_price_usd"] for s in list_sales(store))
        assert prices == [16.0, 50.0]

    def test_the_sale_does_not_remove_the_copies_twice(self, kit):
        # `record_sale` removes copies by default; so does this. Both firing
        # would take four off a stack of two.
        store, db, bundle = kit
        item = store.add_copies(PIMP, "Sol Ring", quantity=2)
        tag_item(store, item.item_id, bundle)
        out = retire_group(store, db, bundle, sale_price_usd=80.0)
        assert out["copies_removed"] == 2
        _, total = store.list_items()
        assert total == 0

    def test_the_empty_group_goes_with_it(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, bundle)
        retire_group(store, db, bundle)
        assert store.collection_by_uid(bundle) == {}

    def test_the_group_can_be_kept_if_you_are_refilling_it(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, bundle)
        retire_group(store, db, bundle, delete_group=False)
        assert store.collection_by_uid(bundle) != {}

    def test_retiring_nothing_is_a_no_op_not_an_error(self, kit):
        store, db, bundle = kit
        out = retire_group(store, db, bundle)
        assert out["copies_removed"] == 0
        assert store.collection_by_uid(bundle) != {}, "an empty group is not deleted"


class TestListsDoNotOutliveTheirCards:
    """A membership for a card you no longer own is a row that outlives its
    card, and it is how a collection you have cleared keeps reporting things
    you do not have.

    `add_copies` has always cleaned up on the way to zero, with a comment
    saying exactly why. Three other paths that delete a stack did not.
    """

    def _memberships(self, store) -> int:
        with store._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM collection_membership").fetchone()[0]

    def test_setting_a_stack_to_zero_takes_its_lists_with_it(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=2)
        tag_item(store, item.item_id, bundle)
        store.set_item_quantity(item.item_id, 0)
        assert self._memberships(store) == 0

    def test_retiring_a_group_leaves_no_memberships_behind(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, bundle)
        retire_group(store, db, bundle)
        assert self._memberships(store) == 0

    def test_clearing_everything_out_clears_the_lists_too(self, kit):
        """The largest source of dangling rows there is — one per stack per
        list — and the one most likely to be hit right before someone looks
        at their collection and wonders why the numbers are wrong."""
        store, db, bundle = kit
        for pid in (SOL, BOLT):
            item = store.add_copies(pid, "Card", quantity=2)
            tag_item(store, item.item_id, bundle)
        default_id = store.default_collection_id()
        store.delete_collection(default_id, discard_cards=True)
        assert self._memberships(store) == 0

    def test_a_moved_stack_stops_being_in_the_box_it_left(self, kit):
        """Filing is one of the memberships, so a physical move has to carry
        it. Left behind, the source collection keeps counting a card it does
        not hold — cards that will not go away however often you clear them."""
        store, db, bundle = kit
        trade = store.create_collection("Trade box")["collection_id"]
        item = store.add_copies(SOL, "Sol Ring", quantity=4)
        store.move_copies(item.item_id, trade)
        counts = {c["name"]: c["cards"] for c in store.list_collections()}
        assert counts["Trade box"] == 4
        assert counts["Main Collection"] == 0

    def test_other_lists_survive_a_move(self, kit):
        # Only "where it lives" moves. A card in a deck list that changes box
        # is still in the deck list.
        store, db, bundle = kit
        trade = store.create_collection("Trade box")["collection_id"]
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, bundle)
        store.move_copies(item.item_id, trade)
        names = {c["name"] for c in store.collections_for_item(item.item_id)}
        assert "Bundle for Dave" in names

    def test_the_count_beside_a_group_matches_what_is_in_it(self, kit):
        """It counted where cards are FILED while the list read filing OR
        membership, so tagging a thousand cards into a group left the number
        beside it reading zero — which looks like the tagging not working."""
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=3)
        tag_item(store, item.item_id, bundle)
        counts = {c["name"]: c["cards"] for c in store.list_collections()}
        assert counts["Bundle for Dave"] == 3

    def test_an_emptied_collection_counts_zero(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=3)
        tag_item(store, item.item_id, bundle)
        store.set_item_quantity(item.item_id, 0)
        counts = {c["name"]: c["cards"] for c in store.list_collections()}
        assert counts["Bundle for Dave"] == 0
        assert counts["Main Collection"] == 0


class TestPickingColoursExactly:
    """Selecting U and B means three different things, and the search had a
    name for the third mode without any code behind it.

    `exact` was declared in the wire protocol and the SQL never implemented
    it, so asking for it quietly got you `identity` — a strictly larger set,
    with nothing in the results to say so.
    """

    @pytest.fixture
    def catalogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = CardDatabase(db_path=Path(tmp) / "cards.db")
            def card(name, identity):
                return Card(
                    scryfall_id=name.lower().replace(" ", "-"),
                    oracle_id=name.lower(), name=name,
                    layout=CardLayout.NORMAL, cmc=2, type_line="Creature",
                    color_identity=identity,
                    legalities={"commander": Legality.LEGAL})
            from densa_deck.models import Color
            db.upsert_cards([
                card("Dimir Thing", [Color.BLUE, Color.BLACK]),
                card("Mono Blue", [Color.BLUE]),
                card("Mono Black", [Color.BLACK]),
                card("Grixis Thing", [Color.BLUE, Color.BLACK, Color.RED]),
                card("Mono Red", [Color.RED]),
                card("Colourless Thing", []),
            ])
            yield db
            db.close()

    def _names(self, db, mode):
        cards, _ = db.search_structured(colors=["U", "B"], color_match=mode,
                                        limit=50)
        return sorted(c.name for c in cards)

    def test_exact_is_the_pair_and_nothing_else(self, catalogue):
        # What "I put in B and U and want cards that are exactly both" means.
        assert self._names(catalogue, "exact") == ["Dimir Thing"]

    def test_any_is_everything_touching_either_colour(self, catalogue):
        assert self._names(catalogue, "any") == [
            "Dimir Thing", "Grixis Thing", "Mono Black", "Mono Blue",
        ]

    def test_identity_is_what_a_dimir_deck_could_play(self, catalogue):
        # The commander rule: a subset of the selected, colourless included.
        assert self._names(catalogue, "identity") == [
            "Colourless Thing", "Dimir Thing", "Mono Black", "Mono Blue",
        ]

    def test_exact_on_one_colour_excludes_the_pairs(self, catalogue):
        cards, _ = catalogue.search_structured(colors=["U"], color_match="exact",
                                               limit=50)
        assert sorted(c.name for c in cards) == ["Mono Blue"]

    def test_exactly_colourless_is_the_only_sense_C_alone_can_carry(self, catalogue):
        cards, _ = catalogue.search_structured(colors=["C"], color_match="exact",
                                               limit=50)
        assert sorted(c.name for c in cards) == ["Colourless Thing"]

    def test_the_three_modes_are_genuinely_different(self, catalogue):
        # If two of them ever return the same set for this catalogue, one of
        # them has silently stopped being its own mode — which is exactly how
        # `exact` went unnoticed.
        sets = {tuple(self._names(catalogue, m))
                for m in ("any", "exact", "identity")}
        assert len(sets) == 3


class TestStartingOver:
    """Clearing a collection, which the desktop could not do at all.

    Not from the UI, and not as one operation: emptying the default
    collection left anything filed in a named one untouched, so "start over"
    was not expressible. Someone who wanted it had no lever on the machine the
    cards are actually on — which is how you end up wiping a PHONE to delete
    cards that live on a PC, where the wipe cannot work because it leaves
    nothing behind to tell the other machine what happened.
    """

    def test_it_takes_every_card_from_every_collection(self, kit):
        store, db, bundle = kit
        trade = store.create_collection("Trade box")["collection_id"]
        store.add_copies(SOL, "Sol Ring", quantity=2)
        store.add_copies(BOLT, "Lightning Bolt", quantity=4,
                         collection_id=trade)
        out = store.clear_all_cards()
        assert out["cards_removed"] == 6
        assert store.list_items()[1] == 0

    def test_a_card_in_a_named_collection_is_not_spared(self, kit):
        # The gap that made "start over" impossible: clearing the default left
        # everything filed elsewhere sitting there.
        store, db, bundle = kit
        trade = store.create_collection("Trade box")["collection_id"]
        store.add_copies(SOL, "Sol Ring", quantity=1, collection_id=trade)
        store.clear_all_cards()
        assert store.list_items()[1] == 0

    def test_the_collections_themselves_survive(self, kit):
        # They are the shelves. Someone clearing the cards off them has not
        # asked to throw the shelves out.
        store, db, bundle = kit
        store.add_copies(SOL, "Sol Ring", quantity=1)
        store.clear_all_cards()
        names = {c["name"] for c in store.list_collections()}
        assert "Bundle for Dave" in names
        assert "Main Collection" in names

    def test_no_list_outlives_the_cards(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=1)
        tag_item(store, item.item_id, bundle)
        store.clear_all_cards()
        with store._connect() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM collection_membership").fetchone()[0] == 0

    def test_every_collection_counts_zero_afterwards(self, kit):
        store, db, bundle = kit
        item = store.add_copies(SOL, "Sol Ring", quantity=3)
        tag_item(store, item.item_id, bundle)
        store.clear_all_cards()
        assert {c["cards"] for c in store.list_collections()} == {0}

    def test_it_is_written_to_the_ledger(self, kit):
        # The event log is what cost basis and P&L are built on; a silent mass
        # deletion leaves both describing cards that are gone.
        store, db, bundle = kit
        store.add_copies(SOL, "Sol Ring", quantity=2)
        store.clear_all_cards()
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT delta, reason FROM collection_events "
                "WHERE reason = 'cleared'").fetchall()
        assert rows == [(-2, "cleared")]

    def test_clearing_nothing_is_not_an_error(self, kit):
        store, db, bundle = kit
        assert store.clear_all_cards()["cards_removed"] == 0

    def test_the_stacks_are_readable_for_sync_before_they_go(self, kit):
        """Local row ids mean nothing on the other machine, so a removal has
        to be described by the card itself — and read BEFORE the clear, since
        afterwards there is nothing left to describe."""
        store, db, bundle = kit
        store.add_copies(SOL, "Sol Ring", quantity=2, finish="foil")
        rows = store.all_stacks_for_sync()
        assert len(rows) == 1
        assert rows[0]["printing_id"] == SOL
        assert rows[0]["finish"] == "foil"
        assert rows[0]["quantity"] == 2
        assert rows[0]["collection_uid"], "the phone keys a stack by collection too"

    def test_a_named_collection_reports_its_own_uid(self, kit):
        # Described against the default instead, the removal names a stack the
        # phone does not have and quietly applies to nothing.
        store, db, bundle = kit
        trade = store.create_collection("Trade box")
        store.add_copies(SOL, "Sol Ring", quantity=1,
                         collection_id=trade["collection_id"])
        rows = store.all_stacks_for_sync()
        assert rows[0]["collection_uid"] == trade["collection_uid"]


class TestBuildingOutOfOneCollection:
    """Deckbuilding scoped to a grouping rather than to everything owned.

    "Only the cards in my Modern binder" is a different and far more useful
    question while building than "cards I own somewhere" — a grouping you
    have made is usually the shape of the deck you are making. The
    collections were already there; nothing in the search could reach them.
    """

    @pytest.fixture
    def built(self):
        from densa_deck.app.api import AppApi

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = CardDatabase(db_path=root / "cards.db")
            from densa_deck.models import Color
            db.upsert_cards([
                Card(scryfall_id=f"id{i}", oracle_id=f"o{i}", name=name,
                     layout=CardLayout.NORMAL, cmc=1, type_line="Artifact",
                     legalities={"commander": Legality.LEGAL})
                for i, name in enumerate(
                    ["Sol Ring", "Lightning Bolt", "Black Lotus"])
            ])
            db.close()
            api = AppApi(db_path=root / "cards.db",
                         version_db_path=root / "versions.db")
            api._collection_store = CollectionStore(
                db_path=root / "collection.db")
            yield api
            api.close()

    def _names(self, api, **query):
        reply = api.search_cards({"ownership": "owned", **query})
        reply = reply.get("data", reply)
        return sorted(c["name"] for c in reply.get("cards", [])), reply

    def test_without_a_collection_it_is_everything_owned(self, built):
        store = built._get_collection_store()
        store.add_copies("p1", "Sol Ring", quantity=1)
        store.add_copies("p2", "Lightning Bolt", quantity=1)
        names, _ = self._names(built)
        assert names == ["Lightning Bolt", "Sol Ring"]

    def test_naming_one_narrows_to_it(self, built):
        store = built._get_collection_store()
        binder = store.create_collection("Modern binder")
        store.add_copies("p1", "Sol Ring", quantity=1)
        item = store.add_copies("p2", "Lightning Bolt", quantity=1)
        store.add_to_collection(item.item_id, binder["collection_id"])

        names, _ = self._names(built, owned_in=binder["collection_uid"])
        assert names == ["Lightning Bolt"]

    def test_a_card_filed_there_counts_as_much_as_one_tagged(self, built):
        # Membership OR filing, as everywhere else — a card belongs to a list
        # either because it was put there or because that is where it lives.
        store = built._get_collection_store()
        binder = store.create_collection("Modern binder")
        store.add_copies("p1", "Sol Ring", quantity=1,
                         collection_id=binder["collection_id"])
        names, _ = self._names(built, owned_in=binder["collection_uid"])
        assert names == ["Sol Ring"]

    def test_an_empty_collection_finds_nothing_rather_than_everything(self, built):
        """The dangerous default. Falling back to "everything owned" answers
        a question nobody asked and reads as the filter being ignored."""
        store = built._get_collection_store()
        empty = store.create_collection("Nothing here")
        store.add_copies("p1", "Sol Ring", quantity=1)
        names, _ = self._names(built, owned_in=empty["collection_uid"])
        assert names == []

    def test_a_collection_that_no_longer_exists_says_so(self, built):
        store = built._get_collection_store()
        store.add_copies("p1", "Sol Ring", quantity=1)
        names, reply = self._names(built, owned_in="no-such-uid")
        assert names == []
        assert "no longer exists" in reply.get("error", "")

    def test_it_does_nothing_without_an_ownership_filter(self, built):
        # On its own it would mean "cards in this collection AND every card in
        # Magic", which is not a question.
        store = built._get_collection_store()
        binder = store.create_collection("Modern binder")
        store.add_copies("p1", "Sol Ring", quantity=1)
        reply = built.search_cards({"owned_in": binder["collection_uid"]})
        reply = reply.get("data", reply)
        assert len(reply["cards"]) == 3, "the whole catalogue, unfiltered"

    def test_unowned_can_be_scoped_too(self, built):
        # "What is NOT in my Modern binder" is a real restocking question.
        store = built._get_collection_store()
        binder = store.create_collection("Modern binder")
        item = store.add_copies("p1", "Sol Ring", quantity=1)
        store.add_to_collection(item.item_id, binder["collection_id"])
        reply = built.search_cards({"ownership": "unowned",
                                    "owned_in": binder["collection_uid"]})
        reply = reply.get("data", reply)
        names = sorted(c["name"] for c in reply["cards"])
        assert names == ["Black Lotus", "Lightning Bolt"]
