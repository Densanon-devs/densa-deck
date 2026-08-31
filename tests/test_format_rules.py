"""Which rules a deck is judged by, and being able to change your mind.

Three things came out of building the format switcher:

* six formats had no rules entry at all, and a missing entry did not mean
  "no rules" — it meant NO CHECKS. A three-card Historic deck running twenty
  copies of one card validated clean, and so did a deck full of cards banned
  in it;
* `_check_sideboard` could not handle a format with no sideboard cap, so
  validating any Limited deck that had one crashed outright;
* and changing a deck's format had nowhere to be stored, because an
  unchanged decklist returns before anything is written.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.deck.validator import FORMAT_RULES
from densa_deck.models import Format


class TestEveryFormatHasRules:
    """A format with no entry skipped every check, including legality."""

    def test_no_format_is_left_unchecked(self):
        missing = [f.value for f in Format if f not in FORMAT_RULES]
        assert not missing, missing

    @pytest.mark.parametrize("fmt", list(Format))
    def test_each_one_says_how_big_a_deck_is(self, fmt):
        assert FORMAT_RULES[fmt].get("min_deck"), fmt.value

    def test_sixty_card_formats_have_no_maximum(self):
        """Sixty is a FLOOR. A 61-card Standard deck is legal and a validator
        that calls it illegal is worse than one that says nothing."""
        for fmt in (Format.STANDARD, Format.MODERN, Format.PIONEER,
                    Format.LEGACY, Format.VINTAGE, Format.PAUPER,
                    Format.HISTORIC, Format.EXPLORER, Format.ALCHEMY,
                    Format.PENNY, Format.PREMODERN):
            assert FORMAT_RULES[fmt]["max_deck"] is None, fmt.value

    def test_the_exact_size_formats_say_so(self):
        for fmt in (Format.COMMANDER, Format.DUEL):
            rules = FORMAT_RULES[fmt]
            assert rules["min_deck"] == rules["max_deck"] == 100, fmt.value
        for fmt in (Format.BRAWL, Format.OATHBREAKER):
            rules = FORMAT_RULES[fmt]
            assert rules["min_deck"] == rules["max_deck"] == 60, fmt.value

    def test_limited_has_no_copy_limit(self):
        """You play what you opened; enforcing four would call a legal deck
        illegal."""
        assert FORMAT_RULES[Format.LIMITED]["max_copies"] >= 60
        assert FORMAT_RULES[Format.LIMITED]["min_deck"] == 40


@pytest.fixture
def api():
    from densa_deck.app.api import AppApi
    from densa_deck.data.database import CardDatabase
    from densa_deck.models import Card, CardLayout, Legality

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_cards([
            Card(scryfall_id="s1", oracle_id="o1", name="Lightning Bolt",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{R}",
                 type_line="Instant", color_identity=["R"],
                 legalities={"modern": Legality.LEGAL,
                             "pauper": Legality.LEGAL,
                             "standard": Legality.NOT_LEGAL,
                             "commander": Legality.LEGAL,
                             "limited": Legality.LEGAL}),
            Card(scryfall_id="s2", oracle_id="o2", name="Snapcaster Mage",
                 layout=CardLayout.NORMAL, cmc=2, mana_cost="{1}{U}",
                 type_line="Creature — Human Wizard", color_identity=["U"],
                 legalities={"modern": Legality.LEGAL,
                             "pauper": Legality.NOT_LEGAL,
                             "commander": Legality.LEGAL}),
            Card(scryfall_id="s3", oracle_id="o3", name="Mountain",
                 layout=CardLayout.NORMAL, cmc=0, mana_cost="",
                 type_line="Basic Land — Mountain", color_identity=["R"],
                 legalities={"modern": Legality.LEGAL,
                             "pauper": Legality.LEGAL,
                             "standard": Legality.LEGAL,
                             "commander": Legality.LEGAL,
                             "limited": Legality.LEGAL}),
        ])
        db.close()
        made = AppApi(db_path=root / "cards.db",
                      version_db_path=root / "versions.db")
        yield made
        made.close()


def _errors(api, text, fmt):
    body = api.analyze_deck(text, fmt, "T")
    body = body.get("data", body)
    assert body.get("ok") is not False, body
    return [i["message"] for i in body["issues"] if i["severity"] == "error"]


class TestSizeIsAFloorOrAFigure:
    SIXTY = "Mainboard:\n4 Lightning Bolt\n56 Mountain\n"

    def test_a_sixtyone_card_modern_deck_is_legal(self, api):
        errs = _errors(api, "Mainboard:\n4 Lightning Bolt\n57 Mountain\n", "modern")
        assert not [e for e in errs if "maximum" in e], errs

    def test_a_ninetynine_card_commander_deck_is_not(self, api):
        errs = _errors(api, "Mainboard:\n99 Mountain\n", "commander")
        assert any("minimum is 100" in e for e in errs), errs

    def test_a_hundredandone_card_commander_deck_is_not_either(self, api):
        errs = _errors(api, "Mainboard:\n101 Mountain\n", "commander")
        assert any("maximum is 100" in e for e in errs), errs

    def test_a_previously_unchecked_format_now_checks(self, api):
        """Historic had no rules entry, so this deck came back clean."""
        errs = _errors(api, "Mainboard:\n20 Lightning Bolt\n", "historic")
        assert any("minimum is 60" in e for e in errs), errs
        assert any("copies" in e.lower() for e in errs), errs


class TestPauperIsCommonsOnly:
    """The rule is enforced through Scryfall's own `pauper` legality rather
    than re-derived from rarity here: it already accounts for which printings
    count and which sets are excluded."""

    def test_a_card_never_printed_at_common_is_refused(self, api):
        errs = _errors(
            api, "Mainboard:\n4 Lightning Bolt\n1 Snapcaster Mage\n55 Mountain\n",
            "pauper")
        assert any("Snapcaster Mage" in e for e in errs), errs

    def test_and_a_card_that_has_been_common_is_not(self, api):
        errs = _errors(api, "Mainboard:\n4 Lightning Bolt\n56 Mountain\n", "pauper")
        assert not any("Lightning Bolt" in e for e in errs), errs


class TestASideboardWithNoCap:
    """`.get("max_sideboard", 15)` returns the stored None rather than the
    default, and comparing an int to it raised a TypeError — so validating
    any Limited deck that had a sideboard crashed rather than passing."""

    def test_a_limited_deck_with_a_sideboard_validates(self, api):
        errs = _errors(
            api,
            "Mainboard:\n40 Mountain\n\nSideboard:\n30 Lightning Bolt\n",
            "limited")
        assert not any("Sideboard" in e for e in errs), errs

    def test_a_constructed_sideboard_is_still_capped(self, api):
        errs = _errors(
            api,
            "Mainboard:\n60 Mountain\n\nSideboard:\n20 Lightning Bolt\n",
            "modern")
        assert any("Sideboard" in e for e in errs), errs


class TestChangingYourMindAboutTheFormat:
    """Deciding a pile of cards is Modern rather than Commander changes what
    is legal about it and nothing about the deck — so it must not mint a
    version, and it must not be quietly discarded either."""

    DECK = "Mainboard:\n4 Lightning Bolt\n56 Mountain\n"

    def _save(self, api, fmt="modern"):
        return api.save_deck_version("d", "D", self.DECK, fmt, "")

    def test_the_new_format_sticks(self, api, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        self._save(api, "modern")
        api.set_deck_format("d", "pauper")
        assert api._get_vstore().get_latest("d").format == "pauper"

    def test_it_does_not_mint_a_version(self, api, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        self._save(api, "modern")
        api.set_deck_format("d", "pauper")
        history = api.get_deck_history("d")
        assert len(history.get("data", history)) == 1

    def test_it_answers_with_the_new_verdict(self, api, monkeypatch):
        """The point of switching is to find out what it means. Making
        somebody press Save to hear the answer is a worse way of telling
        them."""
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        self._save(api, "modern")
        out = api.set_deck_format("d", "commander")
        out = out.get("data", out)
        assert any("minimum is 100" in i["message"] for i in out["issues"])

    def test_an_unknown_format_is_refused(self, api, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        self._save(api)
        out = api.set_deck_format("d", "zzz")
        out = out.get("data", out)
        assert out["error_type"] == "UnknownFormat"

    def test_an_unknown_deck_is_refused(self, api):
        out = api.set_deck_format("nope", "modern")
        out = out.get("data", out)
        assert out["ok"] is False

    def test_saving_afterwards_keeps_the_new_format(self, api, monkeypatch):
        """The format lives on the deck row; a later save must not put the
        old one back."""
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        self._save(api, "modern")
        api.set_deck_format("d", "pauper")
        api.save_deck_version("d", "D", self.DECK + "1 Mountain\n", "pauper", "")
        assert api._get_vstore().get_latest("d").format == "pauper"


class TestDuplicatingADeck:
    DECK = "Mainboard:\n4 Lightning Bolt\n56 Mountain\n"

    @pytest.fixture(autouse=True)
    def pro(self, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")

    def _saved(self, api):
        api.save_deck_version("d", "Burn", self.DECK, "modern", "")
        return api

    def test_the_copy_has_the_same_cards(self, api):
        self._saved(api)
        out = api.duplicate_deck("d")
        out = out.get("data", out)
        assert out["copied"] is True
        copy = api._get_vstore().get_latest(out["deck_id"])
        assert copy.decklist == api._get_vstore().get_latest("d").decklist

    def test_and_the_same_format(self, api):
        self._saved(api)
        out = api.duplicate_deck("d")["data"]
        assert api._get_vstore().get_latest(out["deck_id"]).format == "modern"

    def test_it_starts_at_v1_with_no_history(self, api):
        """You wanted these cards as a starting point, not somebody else's
        record of playing them."""
        self._saved(api)
        api.save_deck_version("d", "Burn", self.DECK + "1 Mountain\n", "modern", "")
        out = api.duplicate_deck("d")["data"]
        assert out["version_number"] == 1
        history = api.get_deck_history(out["deck_id"])
        assert len(history.get("data", history)) == 1

    def test_and_no_win_loss_record(self, api):
        self._saved(api)
        api.record_deck_game("d", "win")
        out = api.duplicate_deck("d")["data"]
        record = api.get_deck_record(out["deck_id"])
        assert record.get("data", record)["record"]["games"] == 0

    def test_the_original_is_untouched(self, api):
        self._saved(api)
        api.record_deck_game("d", "win")
        api.duplicate_deck("d")
        record = api.get_deck_record("d")
        assert record.get("data", record)["record"]["games"] == 1

    def test_copying_twice_does_not_collide(self, api):
        self._saved(api)
        first = api.duplicate_deck("d")["data"]["deck_id"]
        second = api.duplicate_deck("d")["data"]["deck_id"]
        assert first != second

    def test_an_unknown_deck_is_refused(self, api):
        out = api.duplicate_deck("nope")
        out = out.get("data", out)
        assert out["ok"] is False

    def test_a_copy_costs_a_deck_slot_on_free(self, api, monkeypatch):
        """A duplicate is a new deck, so the deck limit applies to it."""
        from densa_deck.tiers import FREE_SAVED_DECKS

        self._saved(api)
        # Fill the remaining slots, so the copy is the one over the line
        # rather than one the allowance had room for.
        for n in range(FREE_SAVED_DECKS):
            api.save_deck_version(f"filler{n}", f"Filler {n}",
                                  "1 Sol Ring", "commander", "")
        monkeypatch.setenv("MTG_ENGINE_TIER", "free")
        out = api.duplicate_deck("d")
        out = out.get("data", out)
        assert out["ok"] is False
        assert out["error_type"] == "ProRequired"


class TestTheStoreItself:
    """Two guards the API layer happens to make unreachable, so they are
    exercised where they live. Both matter for a caller that is not the
    desktop — the sync applier writes through this store too."""

    @pytest.fixture
    def store(self):
        from densa_deck.versioning.storage import VersionStore

        with tempfile.TemporaryDirectory() as tmp:
            made = VersionStore(db_path=Path(tmp) / "versions.db")
            yield made
            made.close()

    def test_saving_an_unchanged_deck_in_a_new_format_updates_it(self, store):
        """`save_version_if_changed` returns early when the cards match, and
        the format would have gone with it."""
        cards, zones = {"Mountain": 60}, {"mainboard": ["Mountain"]}
        store.save_version_if_changed("d", "D", "modern", cards, zones)
        snapshot, created = store.save_version_if_changed(
            "d", "D", "pauper", cards, zones)
        assert created is False, "the cards did not change, so no version"
        assert snapshot.format == "pauper", "but the format did"
        assert store.get_latest("d").format == "pauper"

    def test_the_same_format_again_changes_nothing(self, store):
        cards, zones = {"Mountain": 60}, {"mainboard": ["Mountain"]}
        store.save_version_if_changed("d", "D", "modern", cards, zones)
        _snapshot, created = store.save_version_if_changed(
            "d", "D", "modern", cards, zones)
        assert created is False
        assert len(store.get_all_versions("d")) == 1

    def test_copying_onto_an_existing_deck_is_refused(self, store):
        """Otherwise the copy silently becomes a new VERSION of whatever was
        already there, and that deck's history grows a snapshot nobody made."""
        cards, zones = {"Mountain": 60}, {"mainboard": ["Mountain"]}
        store.save_version_if_changed("a", "A", "modern", cards, zones)
        store.save_version_if_changed("b", "B", "modern",
                                      {"Island": 60}, {"mainboard": ["Island"]})

        result = store.duplicate("a", "b", "Clash")
        assert result["copied"] is False
        assert "exists" in result["reason"]
        assert store.get_latest("b").decklist == {"Island": 60}, "b was overwritten"
