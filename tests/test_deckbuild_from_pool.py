"""Making a deck out of a collection, rather than out of Magic.

Every other suggestion path in this engine reaches for the whole catalogue,
which answers "what should I buy". This answers the question someone asks
standing over a box of cards: make me a deck out of THIS. The answer has to
be constrained to what is physically present, respect how many copies are
there, and — because a real collection usually cannot fill a format's
targets — say plainly what it could not do. A builder that hands back sixty
cards with four lands and no comment has told you nothing.

Deterministic on purpose. The analyst can explain the result afterwards; the
deck itself is arithmetic, so it comes out the same twice and works with no
model loaded.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.app.api import AppApi
from densa_deck.collection.deckbuild import (
    build_from_pool,
    decklist_text,
    pool_from_collection,
)
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase
from densa_deck.models import Card, CardLayout, Color, Format, Legality


def _card(name, type_line, cmc=2, identity=(), text="", power=None):
    return Card(
        scryfall_id=name.lower().replace(" ", "-"), oracle_id=name.lower(),
        name=name, layout=CardLayout.NORMAL, cmc=cmc, type_line=type_line,
        oracle_text=text, color_identity=list(identity), power=power,
        is_land="Land" in type_line, is_creature="Creature" in type_line,
        legalities={"commander": Legality.LEGAL, "modern": Legality.LEGAL},
    )


CATALOGUE = [
    _card("Forest", "Basic Land - Forest", 0),
    _card("Island", "Basic Land - Island", 0),
    _card("Mountain", "Basic Land - Mountain", 0),
    _card("Green Commander", "Legendary Creature - Elf", 3, [Color.GREEN],
          power="4"),
    _card("Red Creature", "Creature - Goblin", 2, [Color.RED], power="5"),
    _card("Blue Drawer", "Instant", 3, [Color.BLUE], "Draw two cards."),
    _card("Green Ramp", "Sorcery", 2, [Color.GREEN],
          "Search your library for a basic land card."),
    _card("Green Bear", "Creature - Bear", 2, [Color.GREEN], power="2"),
    _card("Colourless Rock", "Artifact", 2, [], "Add one mana of any color."),
    _card("Blue Removal", "Instant", 2, [Color.BLUE],
          "Destroy target creature."),
]


@pytest.fixture
def api():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_cards(CATALOGUE)
        db.close()
        a = AppApi(db_path=root / "cards.db",
                   version_db_path=root / "versions.db")
        a._collection_store = CollectionStore(db_path=root / "collection.db")
        yield a
        a.close()


def _stock(api, **counts) -> str:
    """Put cards in a named collection and return its uid."""
    store = api._get_collection_store()
    shelf = store.create_collection("Shoebox")
    for i, (name, quantity) in enumerate(counts.items()):
        item = store.add_copies(f"p{i}", name.replace("_", " "),
                                quantity=quantity)
        store.add_to_collection(item.item_id, shelf["collection_id"])
    return shelf["collection_uid"]


def _pool(api, uid):
    return pool_from_collection(api._get_collection_store(), api._get_db(), uid)


class TestOnlyWhatIsInTheBox:
    def test_it_uses_nothing_you_do_not_own(self, api):
        uid = _stock(api, Forest=20, Green_Bear=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        assert set(built["decklist"]) <= {"Forest", "Green Bear"}

    def test_it_never_uses_more_copies_than_you_have(self, api):
        # A four-of format and one copy in the box is one copy in the deck.
        uid = _stock(api, Mountain=30, Red_Creature=1)
        built = build_from_pool(_pool(api, uid), Format.MODERN)
        assert built["decklist"].get("Red Creature", 0) == 1

    def test_basics_are_the_exception_to_the_copy_limit(self, api):
        uid = _stock(api, Forest=40, Green_Bear=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        assert built["decklist"]["Forest"] > 1

    def test_singleton_is_respected_even_when_you_own_a_playset(self, api):
        uid = _stock(api, Forest=40, Green_Bear=4)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        assert built["decklist"].get("Green Bear", 0) == 1

    def test_a_card_the_catalogue_has_never_heard_of_is_left_out(self, api):
        # It cannot be judged for colour or legality, and guessing would put
        # an illegal card in a deck someone takes to a table.
        uid = _stock(api, Forest=20, Nonsense_Card=4)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        assert "Nonsense Card" not in built["decklist"]


class TestColours:
    def test_a_commander_decides_the_colours(self, api):
        uid = _stock(api, Forest=20, Green_Commander=1, Blue_Drawer=4)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER,
                                commander_name="Green Commander")
        assert built["colors"] == ["G"]
        assert "Blue Drawer" not in built["decklist"]

    def test_colourless_cards_go_in_any_deck(self, api):
        uid = _stock(api, Forest=20, Green_Commander=1, Colourless_Rock=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER,
                                commander_name="Green Commander")
        assert "Colourless Rock" in built["decklist"]

    def test_with_no_commander_it_follows_what_the_pool_supports(self, api):
        """A collection with two blue cards in it should not produce a deck
        that is nominally blue."""
        uid = _stock(api, Forest=30, Green_Bear=4, Green_Ramp=4, Blue_Drawer=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        assert "G" in built["colors"]

    def test_being_told_the_colours_outright_wins(self, api):
        uid = _stock(api, Forest=20, Green_Bear=4, Blue_Drawer=4)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER,
                                colors={"U"})
        assert built["colors"] == ["U"]
        assert "Green Bear" not in built["decklist"]

    def test_a_commander_that_is_not_in_the_box_is_refused(self, api):
        uid = _stock(api, Forest=20)
        with pytest.raises(ValueError, match="not in that collection"):
            build_from_pool(_pool(api, uid), Format.COMMANDER,
                            commander_name="Green Commander")


class TestSayingWhatItCouldNotDo:
    def test_a_thin_collection_reports_every_hole(self, api):
        """The point of the report. Handing back fourteen cards while saying
        nothing would be worse than refusing to build at all."""
        uid = _stock(api, Mountain=12, Red_Creature=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        assert built["total_cards"] < built["target_size"]
        assert built["short_by"] > 0
        short = {r["role"]: r["short"] for r in built["roles"]}
        assert short["lands"] > 0 and short["draw"] > 0

    def test_a_deep_collection_reports_no_holes(self, api):
        # Enough cards to actually reach 100. A pool of 63 cannot make a
        # hundred-card deck however good the builder is, and asserting it
        # could would be testing the test.
        uid = _stock(api, Forest=200, Green_Bear=1, Green_Ramp=1,
                     Colourless_Rock=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        assert built["total_cards"] == built["target_size"]
        assert built["short_by"] == 0

    def test_it_stops_at_what_the_pool_holds(self, api):
        """A 63-card collection makes a 63-card deck and says it is short.
        Padding it with cards that are not there would be a lie someone
        discovers at a table."""
        uid = _stock(api, Forest=60, Green_Bear=1, Green_Ramp=1,
                     Colourless_Rock=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        assert built["total_cards"] == 63
        assert built["short_by"] == 37

    def test_a_card_is_only_counted_once(self, api):
        """Ramp counted as ramp is not also counted as a threat. Without that
        a deck reports twelve of each out of twelve cards."""
        uid = _stock(api, Forest=60, Colourless_Rock=1, Green_Bear=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        counted = sum(r["filled"] for r in built["roles"])
        assert counted <= built["total_cards"]

    def test_creatures_count_as_threats_when_nothing_else_does(self, api):
        """`_is_threat` wants power 4 - right for judging a deck, wrong for
        building one, where a deck of bears is still a creature deck."""
        uid = _stock(api, Forest=40, Green_Bear=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        threats = next(r for r in built["roles"] if r["role"] == "threats")
        assert threats["filled"] > 0


class TestWhatComesBack:
    def test_the_commander_gets_its_own_zone_in_the_text(self, api):
        # Listed in the maindeck it is a 101-card deck with a rules violation
        # in it, and every downstream reader treats the zone differently.
        uid = _stock(api, Forest=40, Green_Commander=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER,
                                commander_name="Green Commander")
        text = decklist_text(built)
        assert "Commander:" in text
        assert text.count("Green Commander") == 1

    def test_the_text_parses_back_into_the_same_deck(self, api):
        from densa_deck.deck.parser import parse_auto
        uid = _stock(api, Forest=40, Green_Bear=1, Green_Ramp=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        entries = parse_auto(decklist_text(built))
        assert sum(e.quantity for e in entries) == built["total_cards"]

    def test_it_is_deterministic(self, api):
        # A build that came out different every time would be worse than one
        # that is merely good.
        uid = _stock(api, Forest=40, Green_Bear=1, Green_Ramp=1,
                     Colourless_Rock=1)
        first = build_from_pool(_pool(api, uid), Format.COMMANDER)
        second = build_from_pool(_pool(api, uid), Format.COMMANDER)
        assert first["decklist"] == second["decklist"]


class TestThroughTheApi:
    def test_it_builds(self, api):
        uid = _stock(api, Forest=40, Green_Bear=1, Green_Ramp=1)
        out = api.build_deck_from_collection(uid, "commander")
        out = out.get("data", out)
        assert out["total_cards"] > 0
        assert out["decklist_text"].strip()

    def test_an_empty_collection_says_so_rather_than_building_nothing(self, api):
        store = api._get_collection_store()
        empty = store.create_collection("Empty")
        out = api.build_deck_from_collection(empty["collection_uid"])
        assert out["ok"] is False
        assert "no cards" in out["error"].lower()

    def test_an_unknown_collection_is_refused(self, api):
        out = api.build_deck_from_collection("no-such-uid")
        assert out["ok"] is False

    def test_the_phone_can_ask_for_it(self, api):
        from densa_deck.app.phone import PhoneBridge
        uid = _stock(api, Forest=40, Green_Bear=1)
        reply = PhoneBridge(api).handle_api("group/build-deck", {
            "collection_uid": uid, "format": "commander",
        })
        assert reply["total_cards"] > 0

    def test_a_missing_model_costs_the_prose_and_not_the_deck(self, api):
        """`explain` is the optional half. Failing the whole call because no
        model is loaded would make the useful part depend on the extra."""
        uid = _stock(api, Forest=40, Green_Bear=1)
        out = api.build_deck_from_collection(uid, "commander", explain=True)
        out = out.get("data", out)
        assert out["total_cards"] > 0
        assert "decklist" in out


class TestFortyCardLimited:
    """Draft and sealed, which is the case this builder fits best of all.

    Limited IS a pool format: you open cards and make a deck out of exactly
    those. Three things separate it from everything else here, and each one
    breaks something that assumed constructed:

    * **No legality key.** Scryfall publishes none, because there is nothing
      to publish — everything you opened is legal. A check that reads
      `legalities['limited']` finds it absent and rejects the whole pool.
    * **No four-copy limit.** Someone who opened seven of a common may play
      all seven.
    * **Forty cards, seventeen of them lands.** Not a scaled-down sixty.
    """

    def _limited_catalogue(self):
        # Deliberately NO limited legality on any of these, mirroring real
        # card data.
        cards = [
            _card("Plains", "Basic Land - Plains", 0),
            _card("Swamp", "Basic Land - Swamp", 0),
            _card("Off Colour Drake", "Creature - Drake", 4, [Color.BLUE],
                  power="3"),
        ]
        for i in range(14):
            cards.append(_card(f"White Soldier {i}", "Creature - Soldier",
                               2 + i % 4, [Color.WHITE], power="3"))
        for i in range(8):
            cards.append(_card(f"Black Fiend {i}", "Creature - Horror",
                               3 + i % 3, [Color.BLACK], power="4"))
        for i in range(5):
            cards.append(_card(f"Doom Blade {i}", "Instant", 2, [Color.BLACK],
                               "Destroy target creature."))
        for card in cards:
            card.legalities = {"standard": Legality.LEGAL}
        return cards

    @pytest.fixture
    def sealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cards = self._limited_catalogue()
            db = CardDatabase(db_path=root / "cards.db")
            db.upsert_cards(cards)
            db.close()
            a = AppApi(db_path=root / "cards.db",
                       version_db_path=root / "versions.db")
            a._collection_store = CollectionStore(db_path=root / "collection.db")
            store = a._collection_store
            shelf = store.create_collection("Sealed pool")
            for i, card in enumerate(cards):
                quantity = (30 if "Basic Land" in card.type_line
                            else 3 if card.name == "White Soldier 0" else 1)
                item = store.add_copies(f"p{i}", card.name, quantity=quantity)
                store.add_to_collection(item.item_id, shelf["collection_id"])
            yield a, shelf["collection_uid"]
            a.close()

    def _build(self, sealed):
        api, uid = sealed
        return build_from_pool(_pool(api, uid), Format.LIMITED)

    def test_it_builds_forty(self, sealed):
        built = self._build(sealed)
        assert built["target_size"] == 40
        assert built["total_cards"] == 40
        assert built["short_by"] == 0

    def test_a_missing_legality_does_not_reject_the_whole_pool(self, sealed):
        """There is no `limited` legality to read. Reading one anyway finds
        it absent and builds a forty-card deck of nothing."""
        built = self._build(sealed)
        assert built["decklist"], "the pool was not thrown away"

    def test_you_may_play_everything_you_opened(self, sealed):
        # No four-copy rule in a pool format, and enforcing one would call a
        # legal deck illegal.
        built = self._build(sealed)
        assert built["decklist"].get("White Soldier 0", 0) == 3

    def test_seventeen_lands_and_twenty_three_spells(self, sealed):
        """The land count is decided by the lands role. The filler used to
        sort the whole pool by mana value, which put basics — costing nothing
        — at the front of every remaining slot: forty cards came out as
        twenty-four lands and sixteen spells."""
        api, _uid = sealed
        built = self._build(sealed)
        lands = sum(q for name, q in built["decklist"].items()
                    if name in ("Plains", "Swamp"))
        assert 16 <= lands <= 18, f"{lands} lands in a 40-card deck"
        assert built["total_cards"] - lands >= 22

    def test_it_is_still_two_colours(self, sealed):
        built = self._build(sealed)
        assert len(built["colors"]) == 2
        assert "Off Colour Drake" not in built["decklist"]

    def test_creatures_carry_a_limited_deck(self, sealed):
        # Threats is the biggest role in limited, not the smallest.
        built = self._build(sealed)
        threats = next(r for r in built["roles"] if r["role"] == "threats")
        assert threats["filled"] >= 14

    def test_commander_is_unaffected_by_the_filler_change(self, api):
        # The non-lands-first filler applies to every format; a Commander
        # deck must still reach 100 when the pool allows it.
        uid = _stock(api, Forest=200, Green_Bear=1, Green_Ramp=1)
        built = build_from_pool(_pool(api, uid), Format.COMMANDER)
        assert built["total_cards"] == 100

    def test_the_validator_knows_about_forty_card_decks(self):
        from densa_deck.deck.validator import FORMAT_RULES
        rules = FORMAT_RULES[Format.LIMITED]
        assert rules["min_deck"] == 40
        assert rules["max_copies"] > 4, "no four-copy limit in a pool format"
        assert rules["requires_commander"] is False
