"""One card, and everything around it.

Clicking a card asks four questions — what is it doing here, what does it
already work with, what would work with it, and which combo lines does it sit
in — and the engine could answer none of them about a SINGLE card. The
deck-wide reports existed and were the wrong shape: `detect_synergies` hands
back every pair in the deck, `find_add_candidates` answers "what is this deck
short of", and neither is the question someone asks with a card in front of
them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.analysis import card_synergy
from densa_deck.combos.matcher import MatchedCombo, NearMissCombo
from densa_deck.combos.models import Combo
from densa_deck.data.database import CardDatabase
from densa_deck.deck.parser import parse_decklist
from densa_deck.deck.resolver import resolve_deck
from densa_deck.models import Card, CardLayout, CardTag, Format, Legality


def _card(name, tags, *, cmc=2, type_line="Creature — Human", colors=None,
          oracle_text=""):
    """A catalogue card.

    The ORACLE TEXT is what matters, not the tags. `find_add_candidates`
    re-runs `classify_card` deliberately — "so we don't trust stale tags" —
    so a fixture that only sets `tags` is a card the suggester cannot see,
    and a test built on one proves nothing about the real path.
    """
    made = Card(
        scryfall_id=f"sid-{name.lower().replace(' ', '-')}",
        oracle_id=f"oid-{name.lower().replace(' ', '-')}",
        name=name, layout=CardLayout.NORMAL, cmc=cmc, mana_cost="{1}{B}",
        type_line=type_line, color_identity=colors or [],
        legalities={"commander": Legality.LEGAL},
        oracle_text=oracle_text,
    )
    made.tags = list(tags)
    return made


# A tiny aristocrats shell: an outlet, a payoff, some fodder, and a card with
# nothing to do with any of it. Text chosen so the classifier reaches the same
# tags a reader would.
CARDS = [
    _card("Viscera Seer", [CardTag.SACRIFICE_OUTLET], cmc=1,
          oracle_text="Sacrifice a creature: Scry 1."),
    _card("Blood Artist", [CardTag.ARISTOCRAT_PAYOFF], cmc=2,
          oracle_text=("Whenever Blood Artist or another creature dies, "
                       "target player loses 1 life and you gain 1 life.")),
    _card("Bitterblossom", [CardTag.TOKEN_MAKER], cmc=2,
          type_line="Enchantment",
          oracle_text=("At the beginning of your upkeep, you lose 1 life and "
                       "create a 1/1 black Faerie Rogue creature token with "
                       "flying.")),
    _card("Sol Ring", [CardTag.RAMP, CardTag.MANA_ROCK], cmc=1,
          type_line="Artifact", oracle_text="{T}: Add {C}{C}."),
    _card("Forest", [CardTag.LAND, CardTag.BASIC_LAND], cmc=0,
          type_line="Basic Land — Forest", oracle_text="({T}: Add {G}.)"),
]


@pytest.fixture
def kit():
    with tempfile.TemporaryDirectory() as tmp:
        db = CardDatabase(db_path=Path(tmp) / "cards.db")
        db.upsert_cards(CARDS)
        yield db
        db.close()


def _deck(db, text=None):
    text = text or ("Mainboard:\n1 Viscera Seer\n1 Blood Artist\n"
                    "1 Bitterblossom\n1 Sol Ring\n20 Forest\n")
    entries = parse_decklist(text)
    return resolve_deck(entries, db, name="Test", format=Format.COMMANDER)


def _by_name(rows):
    return {r["card_name"] for r in rows}


class TestWhatThisCardWorksWithInTheDeck:
    def test_an_outlet_finds_its_payoff(self, kit):
        deck = _deck(kit)
        found = card_synergy.synergies_in_deck(
            kit.lookup_by_name("Viscera Seer"), deck)
        assert "Blood Artist" in _by_name(found)

    def test_and_the_payoff_finds_the_outlet(self, kit):
        """The rules are written one way round. Read in one direction they
        answer for half the cards in a deck and silently return nothing for
        the other half."""
        deck = _deck(kit)
        found = card_synergy.synergies_in_deck(
            kit.lookup_by_name("Blood Artist"), deck)
        assert "Viscera Seer" in _by_name(found)

    def test_the_reason_is_carried_with_the_pair(self, kit):
        deck = _deck(kit)
        found = card_synergy.synergies_in_deck(
            kit.lookup_by_name("Viscera Seer"), deck)
        pair = next(f for f in found if f["card_name"] == "Blood Artist")
        assert pair["reason"], "a pairing with no reason is a list of names"
        assert 0 < pair["strength"] <= 1

    def test_a_card_never_pairs_with_itself(self, kit):
        deck = _deck(kit)
        found = card_synergy.synergies_in_deck(
            kit.lookup_by_name("Viscera Seer"), deck)
        assert "Viscera Seer" not in _by_name(found)

    def test_each_partner_appears_once_at_its_best_reason(self, kit):
        """Tokens relate to an outlet through more than one rule. Five
        restatements of one relationship read as noise."""
        deck = _deck(kit)
        found = card_synergy.synergies_in_deck(
            kit.lookup_by_name("Viscera Seer"), deck)
        names = [f["card_name"] for f in found]
        assert len(names) == len(set(names))

    def test_an_unrelated_card_finds_nothing_rather_than_everything(self, kit):
        deck = _deck(kit)
        found = card_synergy.synergies_in_deck(
            kit.lookup_by_name("Forest"), deck)
        assert found == []

    def test_the_strongest_pairing_is_first(self, kit):
        deck = _deck(kit)
        found = card_synergy.synergies_in_deck(
            kit.lookup_by_name("Viscera Seer"), deck)
        strengths = [f["strength"] for f in found]
        assert strengths == sorted(strengths, reverse=True)


class TestWhatThisCardIsDoingHere:
    def test_it_names_the_roles_the_deck_counts_in(self, kit):
        fit = card_synergy.role_fit(kit.lookup_by_name("Sol Ring"), None, None)
        assert "ramp" in fit["roles"]

    def test_without_an_analysis_it_still_describes_the_card(self, kit):
        """The browser has no deck open, and a card still has roles."""
        fit = card_synergy.role_fit(kit.lookup_by_name("Blood Artist"),
                                    None, None)
        assert fit["tags"]
        assert fit["counts"] == []

    def test_with_an_analysis_it_says_have_and_want(self, kit):
        from densa_deck.analysis.static import analyze_deck as run_static_analysis

        deck = _deck(kit)
        fit = card_synergy.role_fit(kit.lookup_by_name("Sol Ring"), deck,
                                    run_static_analysis(deck))
        ramp = next(c for c in fit["counts"] if c["role"] == "ramp")
        assert ramp["have"] >= 1
        assert ramp["want"] is not None, "a count with nothing to compare to"

    def test_a_surplus_is_never_reported_as_a_shortfall(self, kit):
        """"You have twelve ramp and wanted ten" is not a problem, and
        phrasing it as one trains people to ignore the panel."""
        from densa_deck.analysis.static import analyze_deck as run_static_analysis

        deck = _deck(kit)
        fit = card_synergy.role_fit(kit.lookup_by_name("Forest"), deck,
                                    run_static_analysis(deck))
        assert all(c["short"] >= 0 for c in fit["counts"])


class TestComboLinesAndCompletions:
    COMBO = Combo(combo_id="42",
                  cards=["Thassa's Oracle", "Demonic Consultation"],
                  produces=["Win the game"],
                  spellbook_url="https://commanderspellbook.com/combo/42/")

    def test_a_line_the_card_is_in_names_the_other_pieces(self):
        lines = card_synergy.combo_lines_for(
            "Thassa's Oracle", [MatchedCombo(combo=self.COMBO)])
        assert lines[0]["with"] == ["Demonic Consultation"]
        assert "Win the game" in lines[0]["produces"]

    def test_a_card_not_in_the_line_gets_nothing(self):
        assert card_synergy.combo_lines_for(
            "Sol Ring", [MatchedCombo(combo=self.COMBO)]) == []

    def test_a_completion_is_read_off_the_decks_missing_list(self):
        """`missing_cards` is a fact about this DECK's relationship to the
        combo and lives on the wrapper, not on the combo."""
        near = NearMissCombo(combo=self.COMBO,
                             missing_cards=["Demonic Consultation"],
                             missing_count=1)
        out = card_synergy.completions_for("Demonic Consultation", [near])
        assert out and out[0]["combo_id"] == "42"
        assert out[0]["still_missing"] == []

    def test_a_two_card_hole_reports_what_is_still_needed(self):
        near = NearMissCombo(
            combo=self.COMBO,
            missing_cards=["Thassa's Oracle", "Demonic Consultation"],
            missing_count=2)
        out = card_synergy.completions_for("Thassa's Oracle", [near])
        assert out[0]["still_missing"] == ["Demonic Consultation"]

    def test_a_card_missing_from_nothing_completes_nothing(self):
        near = NearMissCombo(combo=self.COMBO,
                             missing_cards=["Demonic Consultation"],
                             missing_count=1)
        assert card_synergy.completions_for("Sol Ring", [near]) == []


class TestWhatWouldWorkWithIt:
    def test_it_offers_partners_rather_than_role_gaps(self, kit):
        """The distinction the whole function exists for: asked about a token
        maker it should reach for outlets and payoffs, not for whatever the
        deck happens to be short of."""
        text = "Mainboard:\n1 Bitterblossom\n1 Sol Ring\n20 Forest\n"
        deck = _deck(kit, text)
        found = card_synergy.suggestions_for_card(
            kit.lookup_by_name("Bitterblossom"), deck, kit, limit=8)
        names = _by_name(found)
        # Blood Artist and Viscera Seer are the partners in the catalogue.
        assert names & {"Blood Artist", "Viscera Seer"}, names

    def test_it_never_suggests_what_is_already_in_the_deck(self, kit):
        deck = _deck(kit)
        found = card_synergy.suggestions_for_card(
            kit.lookup_by_name("Viscera Seer"), deck, kit)
        assert "Blood Artist" not in _by_name(found), "already in the deck"

    def test_it_never_suggests_the_card_itself(self, kit):
        deck = _deck(kit)
        found = card_synergy.suggestions_for_card(
            kit.lookup_by_name("Viscera Seer"), deck, kit)
        assert "Viscera Seer" not in _by_name(found)

    def test_a_combo_completer_outranks_a_merely_thematic_card(self, kit):
        text = "Mainboard:\n1 Bitterblossom\n1 Sol Ring\n20 Forest\n"
        deck = _deck(kit, text)
        found = card_synergy.suggestions_for_card(
            kit.lookup_by_name("Bitterblossom"), deck, kit,
            combo_completers={"Blood Artist"})
        assert found, "nothing to rank"
        assert found[0]["card_name"] == "Blood Artist"
        assert found[0]["completes_combo"] is True

    def test_a_card_with_no_partnerships_suggests_nothing(self, kit):
        deck = _deck(kit)
        assert card_synergy.suggestions_for_card(
            kit.lookup_by_name("Forest"), deck, kit) == []


class TestThroughTheDesktopApi:
    @pytest.fixture
    def api(self):
        from densa_deck.app.api import AppApi

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = CardDatabase(db_path=root / "cards.db")
            db.upsert_cards(CARDS)
            db.close()
            made = AppApi(db_path=root / "cards.db",
                          version_db_path=root / "versions.db")
            yield made
            made.close()

    DECK = ("Mainboard:\n1 Viscera Seer\n1 Blood Artist\n1 Bitterblossom\n"
            "1 Sol Ring\n20 Forest\n")

    def test_it_answers_with_a_deck(self, api):
        out = api.card_synergy_report("Viscera Seer", self.DECK, "commander")["data"]
        assert out["has_deck"] is True
        assert out["in_the_deck"] is True
        assert "Blood Artist" in _by_name(out["in_deck"])

    def test_it_answers_without_one(self, api):
        """A card looked at from the browser with no deck open still has
        roles. An empty decklist is a supported case, not an error."""
        out = api.card_synergy_report("Sol Ring")["data"]
        assert out["has_deck"] is False
        assert out["fit"]["roles"], "a card always has roles"
        assert out["in_deck"] == []

    def test_an_unknown_card_says_so(self, api):
        body = api.card_synergy_report("Not A Real Card")
        body = body.get("data", body)
        assert body.get("ok") is False

    def test_no_card_name_is_refused(self, api):
        body = api.card_synergy_report("   ")
        body = body.get("data", body)
        assert body.get("ok") is False

    def test_a_decklist_that_will_not_parse_still_returns_the_card(self, api):
        """Saying less beats saying nothing — the card half is unaffected by
        the deck half failing."""
        out = api.card_synergy_report("Sol Ring", "@@@ not a decklist @@@",
                                      "commander")["data"]
        assert out["card_name"] == "Sol Ring"
        assert out["fit"]["roles"]

    def test_a_card_outside_the_deck_is_reported_as_outside_it(self, api):
        text = "Mainboard:\n1 Bitterblossom\n20 Forest\n"
        out = api.card_synergy_report("Blood Artist", text, "commander")["data"]
        assert out["in_the_deck"] is False

    def test_missing_combo_data_shrinks_the_panel_rather_than_breaking_it(self, api):
        """Combo data is a separate opt-in download. Its absence must cost
        the two combo sections and nothing else."""
        out = api.card_synergy_report("Viscera Seer", self.DECK, "commander")["data"]
        assert out["combo_lines"] == []
        assert out["combo_completions"] == []
        assert out["in_deck"], "the rest of the panel still answered"
