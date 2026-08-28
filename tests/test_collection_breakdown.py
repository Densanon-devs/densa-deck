"""What a collection is made of, and how far through a set you are.

A deck has had a breakdown since the beginning. A collection had a card count
and a total, which answers "how much stuff" and none of the questions anyone
has about a box: what colours am I deep in, is this shelf all two-drops, how
much of it is rares, which sets am I close to finishing.

Scoped by collection, so it answers for a GROUP as readily as for everything
owned — which is what makes it useful while assembling a bundle.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.collection.breakdown import breakdown, set_completion
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall
from densa_deck.models import Card, CardLayout, Legality


def _printing(pid, name, set_code, num, oracle, usd="1.00", rarity="rare",
              lang="en"):
    return {
        "id": pid, "oracle_id": oracle, "name": name, "set": set_code,
        "set_name": set_code.upper() + " Set", "collector_number": num,
        "rarity": rarity, "lang": lang, "released_at": "2023-01-01",
        "finishes": ["nonfoil", "foil"], "frame": "2015",
        "border_color": "black", "promo_types": [], "games": ["paper"],
        "tcgplayer_id": 1,
        "prices": {"usd": usd, "usd_foil": "40.00", "usd_etched": None},
    }


@pytest.fixture
def kit():
    """Three cards with genuinely different colours, types and costs."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_cards([
            Card(scryfall_id="s1", oracle_id="o1", name="Sol Ring",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
                 type_line="Artifact", color_identity=[],
                 legalities={"commander": Legality.LEGAL}),
            Card(scryfall_id="s2", oracle_id="o2", name="Death Wind",
                 layout=CardLayout.NORMAL, cmc=2, mana_cost="{X}{B}",
                 type_line="Instant", color_identity=["B"],
                 legalities={"commander": Legality.LEGAL}),
            Card(scryfall_id="s3", oracle_id="o3", name="Forest",
                 layout=CardLayout.NORMAL, cmc=0, mana_cost="",
                 type_line="Basic Land — Forest", color_identity=["G"],
                 legalities={"commander": Legality.LEGAL}),
        ])
        db.upsert_printings([
            printing_row_from_scryfall(
                _printing("s1", "Sol Ring", "cmm", "410", "o1", "16.00"), "t"),
            printing_row_from_scryfall(
                _printing("s2", "Death Wind", "cmm", "120", "o2", "0.25",
                          rarity="common"), "t"),
            printing_row_from_scryfall(
                _printing("s3", "Forest", "cmm", "500", "o3", "0.10",
                          rarity="common"), "t"),
            printing_row_from_scryfall(
                _printing("x9", "Unowned Card", "cmm", "999", "o9", "1.00",
                          rarity="uncommon"), "t"),
        ])
        store = CollectionStore(db_path=root / "collection.db")
        store.add_copies("s1", "Sol Ring", quantity=2)
        store.add_copies("s2", "Death Wind", quantity=3)
        store.add_copies("s3", "Forest", quantity=10)
        yield store, db
        db.close()


def _as_map(rows, key, value="cards"):
    return {r[key]: r[value] for r in rows}


class TestWhatIsInThePile:
    def test_it_counts_copies_not_distinct_cards(self, kit):
        """Someone with forty Forests owns forty green cards. Reporting one
        would be describing a list rather than a box."""
        store, db = kit
        out = breakdown(store, db)
        assert out["total_cards"] == 15
        assert out["distinct_cards"] == 3

    def test_colours_count_every_copy(self, kit):
        store, db = kit
        colours = _as_map(breakdown(store, db)["colors"], "color")
        assert colours["G"] == 10
        assert colours["B"] == 3

    def test_colourless_is_its_own_bucket_not_a_missing_one(self, kit):
        store, db = kit
        colours = _as_map(breakdown(store, db)["colors"], "color")
        assert colours["C"] == 2, "the Sol Rings went nowhere"

    def test_types_are_grouped_by_the_type_that_matters(self, kit):
        store, db = kit
        types = _as_map(breakdown(store, db)["types"], "type")
        assert types == {"Lands": 10, "Instants": 3, "Artifacts": 2}

    def test_the_buckets_sum_to_the_collection(self, kit):
        """An "Artifact Creature" counted twice makes the buckets sum to more
        than the pile, and the panel stops being arithmetic."""
        store, db = kit
        out = breakdown(store, db)
        assert sum(t["cards"] for t in out["types"]) == out["total_cards"]

    def test_the_curve_leaves_lands_out(self, kit):
        """A collection is mostly lands by volume and they all cost nothing.
        Left in, the curve is one enormous bar at zero."""
        store, db = kit
        curve = {c["label"]: c["cards"] for c in breakdown(store, db)["curve"]}
        assert curve["0"] == 0, "ten Forests are in the zero bar"
        assert curve["1"] == 2 and curve["2"] == 3

    def test_the_curve_always_has_every_bar(self, kit):
        """A curve with holes in it is read as a curve with zeroes, so the
        bars have to exist to be zero."""
        store, db = kit
        labels = [c["label"] for c in breakdown(store, db)["curve"]]
        assert labels == ["0", "1", "2", "3", "4", "5", "6", "7+"]

    def test_rarity_comes_off_the_printing(self, kit):
        store, db = kit
        rarities = _as_map(breakdown(store, db)["rarities"], "rarity")
        assert rarities["rare"] == 2
        assert rarities["common"] == 13

    def test_sets_are_counted_and_named(self, kit):
        store, db = kit
        sets = breakdown(store, db)["sets"]
        assert sets[0]["set_code"] == "CMM"
        assert sets[0]["cards"] == 15
        assert sets[0]["set_name"]

    def test_the_total_is_priced_per_printing(self, kit):
        # 2x16.00 + 3x0.25 + 10x0.10
        store, db = kit
        assert breakdown(store, db)["value_usd"] == pytest.approx(33.75)

    def test_an_empty_collection_answers_rather_than_failing(self, kit):
        _store, db = kit
        # Made beside the kit's own databases rather than in a nested
        # tempdir: `CollectionStore` has no close(), and Windows will not
        # delete a file SQLite still holds — the cleanup then fails the test
        # on teardown rather than on anything it checked.
        empty = CollectionStore(db_path=db.db_path.parent / "empty.db")
        out = breakdown(empty, db)
        assert out["ready"] is True
        assert out["total_cards"] == 0
        assert out["colors"] == []


class TestScopedToOneGroup:
    """The same report about a shelf, which is what makes it useful while
    putting a bundle together."""

    def test_a_group_reports_only_its_own_cards(self, kit):
        store, db = kit
        bundle = store.create_collection("Bundle")["collection_id"]
        items, _ = store.list_items(printing_id="s1")
        store.add_to_collection(items[0].item_id, bundle)

        out = breakdown(store, db, collection_id=bundle)
        assert out["total_cards"] == 2
        assert _as_map(out["types"], "type") == {"Artifacts": 2}

    def test_a_card_filed_in_the_group_counts_even_without_membership(self, kit):
        """Membership OR filing, as everywhere else — a breakdown that knew
        only one of the two would describe a different pile than the screen."""
        store, db = kit
        box = store.create_collection("Box")["collection_id"]
        items, _ = store.list_items(printing_id="s2")
        store.move_to_collection(items[0].item_id, box)

        out = breakdown(store, db, collection_id=box)
        assert out["total_cards"] == 3

    def test_an_empty_group_is_empty_rather_than_everything(self, kit):
        """The failure a missing scope produces: a group nobody has filled
        reporting the whole collection."""
        store, db = kit
        empty = store.create_collection("Nothing in here")["collection_id"]
        assert breakdown(store, db, collection_id=empty)["total_cards"] == 0


class TestHowFarThroughASetYouAre:
    def test_it_counts_slots_owned_against_slots_in_the_set(self, kit):
        store, db = kit
        out = set_completion(store, db)
        cmm = next(s for s in out["sets"] if s["set_code"] == "CMM")
        assert (cmm["owned"], cmm["in_set"]) == (3, 4)
        assert cmm["percent"] == 75.0
        assert cmm["complete"] is False

    def test_four_copies_of_one_card_are_one_slot(self, kit):
        """Otherwise a playset of Forests reads as four cards of the set."""
        store, db = kit
        store.add_copies("s3", "Forest", quantity=4, location="another box")
        cmm = next(s for s in set_completion(store, db)["sets"]
                   if s["set_code"] == "CMM")
        assert cmm["owned"] == 3, "the extra Forests are the same slot"

    def test_a_finished_set_says_so(self, kit):
        store, db = kit
        store.add_copies("x9", "Unowned Card", quantity=1)
        cmm = next(s for s in set_completion(store, db)["sets"]
                   if s["set_code"] == "CMM")
        assert cmm["complete"] is True
        assert cmm["percent"] == 100.0

    def test_nobody_can_exceed_one_hundred_percent(self, kit):
        """Promos and variants can put owned above the slot count. "104%
        complete" reads as a bug rather than as a full set."""
        store, db = kit
        db.upsert_printings([
            printing_row_from_scryfall(
                _printing("p1", "Sol Ring", "cmm", "410★", "o1", "50.00"), "t"),
        ])
        store.add_copies("p1", "Sol Ring", quantity=1)
        store.add_copies("x9", "Unowned Card", quantity=1)
        cmm = next(s for s in set_completion(store, db)["sets"]
                   if s["set_code"] == "CMM")
        assert cmm["percent"] <= 100.0

    def test_other_languages_do_not_inflate_the_denominator(self, kit):
        """A Japanese printing is the same slot. Counted, every set would
        report a denominator several times too large and nothing would ever
        look close to done."""
        store, db = kit
        db.upsert_printings([
            printing_row_from_scryfall(
                _printing("jp1", "Sol Ring", "cmm", "410", "o1", "20.00",
                          lang="ja"), "t"),
        ])
        cmm = next(s for s in set_completion(store, db)["sets"]
                   if s["set_code"] == "CMM")
        assert cmm["in_set"] == 4

    def test_without_the_printing_catalogue_it_says_so(self, kit):
        """The catalogue is an opt-in download. Zeroes would read as an empty
        collection rather than as a missing file."""
        store, db = kit
        bare = CardDatabase(db_path=db.db_path.parent / "bare.db")
        try:
            out = set_completion(store, bare)
            assert out["catalogue_ready"] is False
            assert out["sets"] == []
        finally:
            bare.close()

    def test_it_is_scoped_to_a_group_too(self, kit):
        store, db = kit
        bundle = store.create_collection("Bundle")["collection_id"]
        items, _ = store.list_items(printing_id="s1")
        store.add_to_collection(items[0].item_id, bundle)
        cmm = next(s for s in set_completion(store, db, collection_id=bundle)["sets"]
                   if s["set_code"] == "CMM")
        assert cmm["owned"] == 1

    def test_the_closest_to_finished_comes_first(self, kit):
        store, db = kit
        db.upsert_printings([
            printing_row_from_scryfall(
                _printing(f"z{i}", f"Other {i}", "abc", str(i), f"oz{i}", "1.00")
                , "t") for i in range(10)
        ])
        store.add_copies("z0", "Other 0", quantity=1)
        percents = [s["percent"] for s in set_completion(store, db)["sets"]]
        assert percents == sorted(percents, reverse=True)


class TestThroughTheDesktopApi:
    @pytest.fixture
    def api(self, kit):
        from densa_deck.app.api import AppApi

        store, db = kit
        made = AppApi(db_path=db.db_path,
                      version_db_path=db.db_path.parent / "versions.db")
        made._collection_store = store
        yield made
        made.close()

    def test_the_breakdown_comes_back_whole(self, api):
        out = api.get_collection_breakdown()["data"]
        assert out["total_cards"] == 15
        assert out["colors"] and out["types"] and out["curve"]

    def test_a_group_is_asked_for_by_uid(self, api):
        store = api._get_collection_store()
        made = store.create_collection("Bundle")
        items, _ = store.list_items(printing_id="s1")
        store.add_to_collection(items[0].item_id, made["collection_id"])

        out = api.get_collection_breakdown(made["collection_uid"])["data"]
        assert out["total_cards"] == 2

    def test_an_unknown_group_says_so(self, api):
        body = api.get_collection_breakdown("no-such-uid")
        body = body.get("data", body)
        assert body.get("ok") is False

    def test_set_completion_comes_back(self, api):
        out = api.get_set_completion()["data"]
        assert out["catalogue_ready"] is True
        assert any(s["set_code"] == "CMM" for s in out["sets"])
