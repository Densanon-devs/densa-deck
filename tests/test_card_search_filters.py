"""Finding a card you cannot name.

Deckbuilding asks questions like "a cheap green creature with deathtouch from
one of these two sets". The search took a name, one set and one rarity, and no
sort — so that question could not be asked at all.

Each filter here is checked for the way it goes quietly wrong: a multi-select
that means AND returns nothing, a rules-text search folded into the name
search finds nothing, and a sort with no tie-break drops cards between pages.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.data.database import CardDatabase
from densa_deck.models import Card, CardLayout


def _card(name, *, set_code="aaa", rarity="common", cmc=1.0, text="",
          keywords=(), type_line="Creature — Human", price=1.0):
    return Card(
        scryfall_id=f"id-{name}", oracle_id=f"o-{name}", name=name,
        layout=CardLayout.NORMAL, cmc=cmc, mana_cost="{G}",
        type_line=type_line, oracle_text=text, keywords=list(keywords),
        rarity=rarity, set_code=set_code, color_identity=["G"],
        price_usd=price,
    )


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        d = CardDatabase(db_path=Path(tmp) / "cards.db")
        d.upsert_cards([
            _card("Deathtouch Snake", set_code="one", rarity="common",
                  cmc=2, keywords=["Deathtouch"], text="Deathtouch"),
            _card("Venom Adept", set_code="two", rarity="rare", cmc=4,
                  text="This creature has deathtouch as long as you control a Swamp."),
            _card("Plain Bear", set_code="one", rarity="uncommon", cmc=3),
            _card("Costly Titan", set_code="three", rarity="mythic", cmc=8,
                  price=40.0),
            _card("Free Sprout", set_code="two", rarity="common", cmc=0),
        ])
        yield d
        d.close()


def _names(db, **kwargs):
    cards, _ = db.search_structured(limit=50, **kwargs)
    return [c.name for c in cards]


class TestRulesText:
    def test_a_keyword_finds_every_card_with_it(self, db):
        """"deathtouch" is a question about what a card DOES.

        Folding it into the name search would find nothing at all — no card
        is called Deathtouch — which is why it is its own filter.
        """
        assert set(_names(db, text="deathtouch")) == {
            "Deathtouch Snake", "Venom Adept"}

    def test_it_reads_the_keyword_list_too(self, db):
        """A keyword is often only in `keywords` and never spelled out."""
        assert "Deathtouch Snake" in _names(db, text="Deathtouch")

    def test_it_is_case_insensitive(self, db):
        assert _names(db, text="DEATHTOUCH") == _names(db, text="deathtouch")

    def test_it_searches_the_type_line(self, db):
        assert "Plain Bear" in _names(db, text="Human")

    def test_it_combines_with_the_other_filters(self, db):
        # The actual question: a cheap one with deathtouch.
        assert _names(db, text="deathtouch", cmc_max=2) == ["Deathtouch Snake"]


class TestSeveralSetsAtOnce:
    def test_two_sets_means_either(self, db):
        """A card cannot be in two sets, so an AND returns nothing and looks
        like the filter is broken."""
        assert set(_names(db, set_codes=["one", "two"])) == {
            "Deathtouch Snake", "Venom Adept", "Plain Bear", "Free Sprout"}

    def test_one_set_still_works(self, db):
        assert set(_names(db, set_codes=["three"])) == {"Costly Titan"}

    def test_the_old_single_field_still_works(self, db):
        # Kept so the desktop's Build tab does not have to change.
        assert set(_names(db, set_code="three")) == {"Costly Titan"}

    def test_an_empty_list_does_not_filter_everything_out(self, db):
        assert len(_names(db, set_codes=[])) == 5


class TestSeveralRarities:
    def test_two_rarities_means_either(self, db):
        assert set(_names(db, rarities=["rare", "mythic"])) == {
            "Venom Adept", "Costly Titan"}

    def test_it_combines_with_sets(self, db):
        assert _names(db, rarities=["common"], set_codes=["two"]) == ["Free Sprout"]


class TestSorting:
    def test_by_cost(self, db):
        assert _names(db, sort="cmc")[0] == "Free Sprout"

    def test_by_cost_descending(self, db):
        assert _names(db, sort="cmc_desc")[0] == "Costly Titan"

    def test_by_rarity_is_scarcity_not_the_alphabet(self, db):
        """Alphabetically that is common, mythic, rare, uncommon — which is
        the wrong answer to every question anyone asks with it."""
        assert _names(db, sort="rarity")[0] == "Costly Titan"   # mythic
        assert _names(db, sort="rarity")[-1] in {"Deathtouch Snake", "Free Sprout"}

    def test_by_price(self, db):
        assert _names(db, sort="price")[0] == "Costly Titan"

    def test_every_sort_breaks_ties_by_name(self, db):
        """Without a tie-break, two cards of the same cost swap places
        between pages and one of them is never seen."""
        first, _ = db.search_structured(sort="cmc", limit=2, offset=0)
        second, _ = db.search_structured(sort="cmc", limit=2, offset=2)
        assert len({c.name for c in [*first, *second]}) == 4

    def test_an_unknown_sort_falls_back_rather_than_failing(self, db):
        assert _names(db, sort="by-vibes") == _names(db, sort="name")

    def test_the_default_is_still_by_name(self, db):
        assert _names(db) == sorted(_names(db), key=str.lower)


class TestSearchingForCharactersThatMeanSomethingToSQL:
    """`%` and `_` are LIKE wildcards, and card text is full of neither —
    but the box people type into does not know that.

    Before escaping, `draw_a` matched "draw a" and returned 3,160 cards for a
    phrase that appears on almost none. The user cannot see why, and the
    answer looks like the search being bad at its job rather than the string
    being interpreted.
    """

    def test_a_plus_one_counter_is_findable(self, db):
        """The thing people actually search for, and it needs no escaping —
        which is exactly why it is worth a test that says so."""
        db.upsert_cards([_card("Counter Bear", text="Put a +1/+1 counter on it.")])
        assert _names(db, text="+1/+1") == ["Counter Bear"]

    def test_an_underscore_is_a_character_not_a_wildcard(self, db):
        assert _names(db, text="draw_a") == []

    def test_a_percent_is_a_character_not_a_wildcard(self, db):
        assert _names(db, text="50%") == []

    def test_the_escape_character_itself_is_searchable(self, db):
        # `!` is the escape char. If it were not escaped in turn, searching
        # for it would produce broken SQL rather than a result.
        db.upsert_cards([_card("Shout", text="Do it!")])
        assert _names(db, text="it!") == ["Shout"]

    def test_escaping_did_not_break_ordinary_words(self, db):
        assert set(_names(db, text="deathtouch")) == {
            "Deathtouch Snake", "Venom Adept"}

    def test_names_are_escaped_too(self, db):
        assert _names(db, name="Plain_Bear") == []


class TestOneBoxForTheWholeCard:
    """Two fields meant knowing which one a word lived in before you could
    look for it, and you often do not: "Bolt" is a name, "deathtouch" is
    rules text, "Goblin" is both.

    So one box searches everything and the user says how terms combine.
    """

    def test_it_finds_a_name(self, db):
        assert "Plain Bear" in _names(db, anywhere="Plain")

    def test_it_finds_rules_text(self, db):
        assert set(_names(db, anywhere="deathtouch")) == {
            "Deathtouch Snake", "Venom Adept"}

    def test_it_finds_a_type(self, db):
        assert "Plain Bear" in _names(db, anywhere="Human")

    def test_and_needs_both(self, db):
        # Venom Adept's text mentions a Swamp; the Snake's does not.
        assert _names(db, anywhere="deathtouch && Swamp") == ["Venom Adept"]

    def test_or_takes_either(self, db):
        assert set(_names(db, anywhere="Swamp || Titan")) == {
            "Venom Adept", "Costly Titan"}

    def test_or_binds_looser_than_and(self, db):
        """`a && b || c` is `(a && b) || c`, as in every language with both.

        Read the other way it would be `a && (b || c)` and quietly return a
        different set — the kind of wrong that looks like a bad search.
        """
        got = set(_names(db, anywhere="deathtouch && Swamp || Titan"))
        assert got == {"Venom Adept", "Costly Titan"}

    def test_spaces_around_the_operators_do_not_matter(self, db):
        assert _names(db, anywhere="deathtouch&&Swamp") == ["Venom Adept"]

    def test_a_term_with_a_wildcard_character_is_still_literal(self, db):
        assert _names(db, anywhere="draw_a") == []

    def test_an_empty_side_is_ignored_rather_than_matching_everything(self, db):
        # "deathtouch &&" is a half-typed query; the trailing empty term must
        # not become "match anything" and silently widen the result.
        assert set(_names(db, anywhere="deathtouch &&")) == {
            "Deathtouch Snake", "Venom Adept"}

    def test_it_combines_with_the_other_filters(self, db):
        assert _names(db, anywhere="deathtouch", cmc_max=2) == ["Deathtouch Snake"]
