"""Ordering a collection, both ways round.

Direction used to be baked into the sort key — `value_desc`, `value_asc` —
which meant it had to be enumerated, and half the sorts were never given
their other half: there was no `quantity_asc`, no `oldest`, and no way to
read a set backwards. There was also no way to sort by mana value at all,
which is the first thing anyone asks a collection.

So direction is its own control. Every sort reverses, including ones added
after this was written.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.collection.query import (
    SORT_COLUMNS,
    resolve_order,
    search_collection,
)
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase
from densa_deck.data.printings import printing_row_from_scryfall
from densa_deck.models import Card, CardLayout, Legality


def _raw(pid, name, set_code, num, *, usd=None, rarity="common"):
    return {
        "id": pid, "oracle_id": f"o-{name.lower().replace(' ', '-')}",
        "name": name, "set": set_code, "set_name": set_code.upper(),
        "collector_number": num, "rarity": rarity, "lang": "en",
        "released_at": "2020-01-01", "finishes": ["nonfoil"],
        "frame": "2015", "border_color": "black", "promo_types": [],
        "games": ["paper"], "tcgplayer_id": 1,
        "prices": {"usd": usd, "usd_foil": None, "usd_etched": None},
    }


def _card(name, cmc):
    return Card(
        scryfall_id=f"s-{name}", oracle_id=f"o-{name.lower().replace(' ', '-')}",
        name=name, layout=CardLayout.NORMAL, cmc=cmc, mana_cost="{%d}" % cmc,
        type_line="Artifact", color_identity=[],
        legalities={"commander": Legality.LEGAL}, oracle_text="",
    )


@pytest.fixture
def env():
    """Three cards with deliberately different mana values and prices —
    including one the catalogue has no price for, which is the case a
    reverse sort gets wrong."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_cards([_card("Sol Ring", 1), _card("Big Thing", 8),
                         _card("Middle Card", 4)])
        db.upsert_printings([
            printing_row_from_scryfall(
                _raw("p-sol", "Sol Ring", "cmm", "410", usd="1.50"), "t"),
            printing_row_from_scryfall(
                _raw("p-big", "Big Thing", "cmm", "20", usd="30.00",
                     rarity="mythic"), "t"),
            printing_row_from_scryfall(
                _raw("p-mid", "Middle Card", "cmm", "5"), "t"),  # no price
        ])
        store = CollectionStore(db_path=root / "collection.db")
        # Distinct quantities on purpose: equal ones tie, and a tie is
        # decided by the tiebreaker, which does not flip — so a reversal
        # test over tied rows tests nothing.
        for pid, name, qty in (("p-sol", "Sol Ring", 4),
                               ("p-big", "Big Thing", 1),
                               ("p-mid", "Middle Card", 2)):
            store.add_copies(pid, name, quantity=qty,
                             oracle_id=f"o-{name.lower().replace(' ', '-')}")
        yield store, db
        db.close()


def _names(store, db, **kw):
    items, _, _ = search_collection(store, db, limit=50, **kw)
    return [i.card_name for i in items]


class TestManaValue:
    def test_it_can_sort_by_mana_value_at_all(self, env):
        store, db = env
        assert _names(store, db, sort="cmc") == [
            "Sol Ring", "Middle Card", "Big Thing"]

    def test_and_reverse_runs_the_other_way(self, env):
        store, db = env
        assert _names(store, db, sort="cmc", direction="desc") == [
            "Big Thing", "Middle Card", "Sol Ring"]

    def test_reversing_is_the_same_rows_backwards(self, env):
        """Not a reshuffle. A reversed page that is a different SET of rows
        is a paging bug wearing a sort's clothes."""
        store, db = env
        up = _names(store, db, sort="cmc")
        down = _names(store, db, sort="cmc", direction="desc")
        assert down == list(reversed(up))


class TestEveryOtherSortReversesToo:
    @pytest.mark.parametrize("sort", list(SORT_COLUMNS))
    def test_every_sort_has_both_directions(self, sort):
        """The whole point of splitting direction out: no sort is one-way,
        and that holds for sorts added after this was written, because the
        list is read from the code rather than repeated here."""
        assert " ASC" in resolve_order(sort, "asc"), sort
        assert " DESC" in resolve_order(sort, "desc"), sort

    @pytest.mark.parametrize("sort", ["name", "cmc", "quantity", "set"])
    def test_and_reversing_one_really_turns_the_rows_around(self, env, sort):
        """Only over columns where these three rows genuinely differ.

        Value and unit are deliberately absent: one of the three has no
        price, and an unpriced card stays at the bottom BOTH ways, so their
        order is not a plain reversal. That is the subject of
        TestWhatReverseMustNotDo rather than a flaw here.
        """
        store, db = env
        up = _names(store, db, sort=sort, direction="asc")
        down = _names(store, db, sort=sort, direction="desc")
        assert down == list(reversed(up)), sort

    def test_rarity_ranks_rather_than_spelling_it(self, env):
        """"common" before "mythic" is alphabetical nonsense — c before m
        happens to look right and r before u does not."""
        store, db = env
        assert _names(store, db, sort="rarity", direction="desc")[0] == "Big Thing"


class TestWhatReverseMustNotDo:
    def test_an_unpriced_card_does_not_become_the_most_valuable(self, env):
        """The trap. NULL sorted DESC puts the rows the database knows
        LEAST about at the top of a list about value."""
        store, db = env
        order = _names(store, db, sort="value", direction="desc")
        assert order[-1] == "Middle Card", order

    def test_and_is_still_last_ascending(self, env):
        store, db = env
        order = _names(store, db, sort="value", direction="asc")
        assert order[-1] == "Middle Card", order

    def test_the_tiebreaker_does_not_flip(self, env):
        """Cards that tie stay alphabetical whichever way the list runs, so
        paging is stable and a reversed page is the same rows backwards
        rather than a reshuffle.

        Sorted by a column where all three tie — every card was added in the
        same instant — so the tiebreaker is the only thing deciding.
        """
        store, db = env
        for way in ("asc", "desc"):
            order = _names(store, db, sort="added", direction=way)
            assert order == sorted(order), way


class TestTheOldSpellingsStillWork:
    """They are stored in saved views and sent by the phone. A sort that
    silently becomes name-order is worse than one that errors."""

    @pytest.mark.parametrize("old,new_sort,way", [
        ("value_desc", "value", "desc"), ("value_asc", "value", "asc"),
        ("unit_desc", "unit", "desc"), ("unit_asc", "unit", "asc"),
        ("quantity_desc", "quantity", "desc"), ("newest", "added", "desc"),
    ])
    def test_an_old_key_means_what_it_always_meant(self, env, old, new_sort, way):
        store, db = env
        assert _names(store, db, sort=old) == _names(
            store, db, sort=new_sort, direction=way)

    def test_an_unknown_sort_still_shows_cards(self, env):
        # Reached from a dropdown and from stored views; a stale one should
        # not empty the screen.
        store, db = env
        assert len(_names(store, db, sort="nonsense")) == 3


class TestTheOrderByItself:
    def test_direction_beats_the_natural_one(self):
        assert "ASC" in resolve_order("value", "asc")
        assert "DESC" in resolve_order("value")

    def test_no_sort_key_can_inject_sql(self):
        # The order string is interpolated into the query, so the key must
        # never reach it unmapped.
        order = resolve_order("name; DROP TABLE collection_items--", "desc")
        assert "DROP" not in order

    def test_nor_can_the_direction(self):
        order = resolve_order("value", "asc; DROP TABLE collection_items--")
        assert "DROP" not in order
