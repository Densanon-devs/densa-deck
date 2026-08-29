"""Tests for card classification."""

from densa_deck.classification.tagger import classify_card
from densa_deck.models import Card, CardLayout, CardTag, Legality


def _make_card(**kwargs) -> Card:
    """Helper to create a test card with minimal required fields."""
    defaults = {
        "scryfall_id": "test-id",
        "oracle_id": "test-oracle",
        "name": "Test Card",
        "layout": CardLayout.NORMAL,
    }
    defaults.update(kwargs)
    return Card(**defaults)


def test_basic_land():
    card = _make_card(
        name="Forest",
        type_line="Basic Land — Forest",
        is_land=True,
    )
    tags = classify_card(card)
    assert CardTag.LAND in tags
    assert CardTag.BASIC_LAND in tags


def test_fetch_land():
    card = _make_card(
        name="Flooded Strand",
        type_line="Land",
        oracle_text="Pay 1 life, Sacrifice Flooded Strand: Search your library for a Plains or Island card, put it onto the battlefield tapped, then shuffle.",
        is_land=True,
    )
    tags = classify_card(card)
    assert CardTag.LAND in tags
    assert CardTag.FETCH_LAND in tags


def test_mana_rock():
    card = _make_card(
        name="Sol Ring",
        type_line="Artifact",
        oracle_text="{T}: Add {C}{C}.",
        is_artifact=True,
        produced_mana=["C"],
    )
    tags = classify_card(card)
    assert CardTag.RAMP in tags
    assert CardTag.MANA_ROCK in tags


def test_mana_dork():
    card = _make_card(
        name="Llanowar Elves",
        type_line="Creature — Elf Druid",
        oracle_text="{T}: Add {G}.",
        is_creature=True,
        produced_mana=["G"],
    )
    tags = classify_card(card)
    assert CardTag.RAMP in tags
    assert CardTag.MANA_DORK in tags


def test_card_draw():
    card = _make_card(
        name="Harmonize",
        type_line="Sorcery",
        oracle_text="Draw three cards.",
        is_sorcery=True,
        cmc=4.0,
    )
    tags = classify_card(card)
    assert CardTag.CARD_DRAW in tags


def test_targeted_removal():
    card = _make_card(
        name="Swords to Plowshares",
        type_line="Instant",
        oracle_text="Exile target creature. Its controller gains life equal to its power.",
        is_instant=True,
    )
    tags = classify_card(card)
    assert CardTag.TARGETED_REMOVAL in tags


def test_board_wipe():
    card = _make_card(
        name="Wrath of God",
        type_line="Sorcery",
        oracle_text="Destroy all creatures. They can't be regenerated.",
        is_sorcery=True,
    )
    tags = classify_card(card)
    assert CardTag.BOARD_WIPE in tags


def test_counterspell():
    card = _make_card(
        name="Counterspell",
        type_line="Instant",
        oracle_text="Counter target spell.",
        is_instant=True,
    )
    tags = classify_card(card)
    assert CardTag.COUNTERSPELL in tags


def test_tutor():
    card = _make_card(
        name="Demonic Tutor",
        type_line="Sorcery",
        oracle_text="Search your library for a card, put that card into your hand, then shuffle.",
        is_sorcery=True,
    )
    tags = classify_card(card)
    assert CardTag.TUTOR in tags


def test_threat_high_power():
    card = _make_card(
        name="Gigantosaurus",
        type_line="Creature — Dinosaur",
        oracle_text="",
        is_creature=True,
        power="10",
        toughness="10",
        cmc=5.0,
    )
    tags = classify_card(card)
    assert CardTag.FINISHER in tags  # power >= 7


def test_token_maker():
    card = _make_card(
        name="Raise the Alarm",
        type_line="Instant",
        oracle_text="Create two 1/1 white Soldier creature tokens.",
        is_instant=True,
    )
    tags = classify_card(card)
    assert CardTag.TOKEN_MAKER in tags


def test_equipment():
    card = _make_card(
        name="Lightning Greaves",
        type_line="Artifact — Equipment",
        oracle_text="Equipped creature has haste and shroud.\nEquip {0}",
        is_artifact=True,
        keywords=["Equip"],
    )
    tags = classify_card(card)
    assert CardTag.EQUIPMENT in tags
    assert CardTag.PROTECTION in tags


def test_dual_land():
    card = _make_card(
        name="Breeding Pool",
        type_line="Land — Forest Island",
        oracle_text="As Breeding Pool enters, you may pay 2 life. If you don't, it enters tapped.\n{T}: Add {G} or {U}.",
        is_land=True,
        produced_mana=["G", "U"],
    )
    tags = classify_card(card)
    assert CardTag.LAND in tags
    assert CardTag.DUAL_LAND in tags


class TestDrawWasMostlyInvisible:
    """A third of the draw in the game classified as nothing.

    The phrase list read naturally and caught 64% of the paper cards whose
    text plainly draws. It missed the plural verb — "target player DRAWS two
    cards", which is how Sign in Blood is written — and it missed
    "when this enters, draw a card" on a creature, because `_is_cantrip`
    refuses creatures and `_is_card_draw` wanted "whenever".

    That did not stay in the classifier. `draw_engine_count` feeds the
    card-advantage score and the role gaps, and the role gaps decide what the
    app SUGGESTS — so every deck looked short of draw and got told to add
    more of what it already had.
    """

    def _card(self, name, text, *, type_line="Instant", cmc=3):
        return Card(
            scryfall_id=f"x-{name}", oracle_id=f"o-{name}", name=name,
            layout=CardLayout.NORMAL, cmc=cmc, mana_cost="{2}{B}",
            type_line=type_line, oracle_text=text,
            legalities={"commander": Legality.LEGAL},
        )

    def test_the_plural_verb_counts(self):
        card = self._card("Sign in Blood",
                          "Target player draws two cards and loses 2 life.")
        assert CardTag.CARD_DRAW in classify_card(card)

    def test_a_creature_that_draws_on_arrival_counts(self):
        card = self._card("Baleful Strix",
                          "Flying, deathtouch\nWhen this creature enters, "
                          "draw a card.",
                          type_line="Artifact Creature — Bird", cmc=2)
        assert CardTag.CARD_DRAW in classify_card(card)

    def test_a_plain_draw_rider_counts(self):
        """Three mana, so `_is_cantrip` will not have it, and no "whenever",
        so the old phrase list would not either."""
        card = self._card("Instill Infection",
                          "Put a -1/-1 counter on target creature.\n"
                          "Draw a card.")
        assert CardTag.CARD_DRAW in classify_card(card)

    def test_an_opponent_drawing_is_not_your_card_advantage(self):
        card = self._card("Rites of Refusal",
                          "Each opponent draws a card.")
        assert CardTag.CARD_DRAW not in classify_card(card)

    def test_but_a_symmetric_draw_still_is(self):
        """You are one of the players a Howling Mine draws for, and it is a
        draw engine in the deck that runs it."""
        card = self._card("Howling Mine",
                          "At the beginning of each player's draw step, that "
                          "player draws an additional card.",
                          type_line="Artifact")
        assert CardTag.CARD_DRAW in classify_card(card)

    def test_drawing_beside_an_opponent_still_counts(self):
        card = self._card("Font of Mythos",
                          "Draw two cards. Each opponent draws a card.")
        assert CardTag.CARD_DRAW in classify_card(card)

    def test_a_card_that_never_draws_is_left_alone(self):
        card = self._card("Lightning Bolt",
                          "Lightning Bolt deals 3 damage to any target.")
        assert CardTag.CARD_DRAW not in classify_card(card)

    def test_lands_are_still_excluded(self):
        card = self._card("Some Land", "{T}: Draw a card.",
                          type_line="Land", cmc=0)
        card.is_land = True
        assert CardTag.CARD_DRAW not in classify_card(card)

    def test_the_original_phrases_still_work(self):
        """Kept as a union, so nothing that was recognised stops being."""
        for text in ("Draw two cards.",
                     "Draw cards equal to the number of creatures you control.",
                     "Whenever a creature dies, draw a card."):
            assert CardTag.CARD_DRAW in classify_card(self._card("X", text)), text


class TestAPermanentThatCountersIsInteraction:
    """The rule was "an instant, a sorcery, or something with flash".

    That is most counterspells and it missed the other kind: a permanent with
    an activated counter ability, which sits on the battlefield and answers at
    instant speed. Forty-nine cards read as zero interaction, so a deck built
    around them was told it had none.
    """

    def _card(self, name, text, type_line="Creature — Human Wizard"):
        return Card(
            scryfall_id=f"c-{name}", oracle_id=f"o-{name}", name=name,
            layout=CardLayout.NORMAL, cmc=2, mana_cost="{1}{U}",
            type_line=type_line, oracle_text=text,
            legalities={"commander": Legality.LEGAL},
        )

    def test_an_activated_counter_ability_counts(self):
        card = self._card("Wizard Replica",
                          "Flying\n{U}, Sacrifice this creature: Counter "
                          "target spell.")
        assert CardTag.COUNTERSPELL in classify_card(card)

    def test_a_plain_counterspell_still_counts(self):
        card = self._card("Counterspell", "Counter target spell.",
                          type_line="Instant")
        card.is_instant = True
        assert CardTag.COUNTERSPELL in classify_card(card)

    def test_a_permanent_that_only_mentions_countering_does_not(self):
        """The cost separator is what makes it an ability with a price rather
        than a permanent whose text happens to contain the phrase."""
        card = self._card(
            "Sigil Bearer",
            "Whenever an opponent would counter target spell, they may not.")
        assert CardTag.COUNTERSPELL not in classify_card(card)

    def test_a_creature_with_no_counter_text_is_left_alone(self):
        assert CardTag.COUNTERSPELL not in classify_card(
            self._card("Grizzly Bears", "Vanilla."))
