"""What free gets, and where it stops.

The philosophy is taste-then-pay, not a locked door. Somebody who has never
saved a deck cannot want deck history — they have to have used it once to
know what they would be buying. So free gets each feature genuinely WORKING
at a small scale, and the limit is a COUNT rather than a crippled version of
the thing: a suggestion list that is quietly worse on free teaches people the
feature is bad rather than that it is limited.

Two real holes are covered here, because both were live:

* `save_builder_as_deck` checked the tier and then delegated straight to
  `save_deck_version`, which checked nothing. The gate and the way around it
  were two lines apart.
* The phone bridge had no tier awareness whatsoever, so a free user with the
  companion could reach `analyst/analyze` and `analyst/explain` — the whole
  paywall, walked around by installing an app.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from densa_deck.data.database import CardDatabase
from densa_deck.models import Card, CardLayout, Legality

DECK = "Commander:\n1 Sol Ring\n\nMainboard:\n30 Island\n"
EDITED = "Commander:\n1 Sol Ring\n\nMainboard:\n1 Brainstorm\n29 Island\n"


@pytest.fixture
def free(monkeypatch):
    """A free installation.

    Forced through the environment because this machine has a licence file,
    and a paywall test that silently ran as Pro would pass no matter what the
    gates did.
    """
    monkeypatch.setenv("MTG_ENGINE_TIER", "free")
    from densa_deck.tiers import Tier, get_user_tier

    assert get_user_tier() == Tier.FREE, "the tier override did not take"
    yield


@pytest.fixture
def pro(monkeypatch):
    monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
    yield


@pytest.fixture
def api():
    from densa_deck.app.api import AppApi

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_cards([
            Card(scryfall_id="s1", oracle_id="o1", name="Sol Ring",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
                 type_line="Artifact",
                 legalities={"commander": Legality.LEGAL}),
            Card(scryfall_id="s2", oracle_id="o2", name="Island",
                 layout=CardLayout.NORMAL, cmc=0, mana_cost="",
                 type_line="Basic Land — Island",
                 legalities={"commander": Legality.LEGAL}),
            Card(scryfall_id="s3", oracle_id="o3", name="Brainstorm",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{U}",
                 type_line="Instant",
                 legalities={"commander": Legality.LEGAL}),
        ])
        db.close()
        made = AppApi(db_path=root / "cards.db",
                      version_db_path=root / "versions.db")
        yield made
        made.close()


def _body(reply):
    return reply.get("data", reply) if isinstance(reply, dict) else reply


def _save(api, deck_id, text=DECK):
    return _body(api.save_deck_version(deck_id, deck_id, text, "commander", ""))


class TestOneDeckIsARealDeck:
    """The free deck has to be a whole deck, or the taste is worthless."""

    def test_the_first_deck_saves(self, free, api):
        assert _save(api, "first")["version_number"] == 1

    def test_the_second_does_not(self, free, api):
        _save(api, "first")
        refused = _save(api, "second")
        assert refused["ok"] is False
        assert refused["error_type"] == "ProRequired"

    def test_and_says_what_it_would_buy(self, free, api):
        _save(api, "first")
        refused = _save(api, "second")
        assert "Pro" in refused["error"]
        assert "lost" in refused["error"], "it must say the draft is safe"

    def test_editing_the_one_deck_still_works(self, free, api):
        """The limit counts DECKS, not saves. A limit that stopped you
        improving the deck you have teaches the wrong thing about Pro."""
        _save(api, "first")
        again = _save(api, "first", EDITED)
        assert again["version_number"] == 2

    def test_that_deck_gets_its_whole_history(self, free, api):
        _save(api, "first")
        _save(api, "first", EDITED)
        history = _body(api.get_deck_history("first"))
        assert len(history) == 2

    def test_and_its_whole_record(self, free, api):
        _save(api, "first")
        api.record_deck_game("first", "win")
        api.record_deck_game("first", "loss")
        assert _body(api.get_deck_record("first"))["record"]["record"] == "1-1"

    def test_deleting_the_deck_frees_the_slot(self, free, api):
        _save(api, "first")
        assert _save(api, "second")["ok"] is False
        api.delete_deck("first")
        assert _save(api, "second")["version_number"] == 1

    def test_pro_keeps_as_many_as_it_likes(self, pro, api):
        for n in range(4):
            assert _save(api, f"deck-{n}")["version_number"] == 1


class TestTheEditorHoleIsClosed:
    """`save_builder_as_deck` refused free users and then delegated to an
    endpoint that accepted them. Saving from the Decks tab walked straight
    around the gate."""

    def test_the_builder_save_obeys_the_same_limit(self, free, api):
        first = _body(api.save_builder_as_deck("a", "A", "commander", DECK, ""))
        assert first.get("ok") is not False
        second = _body(api.save_builder_as_deck("b", "B", "commander", DECK, ""))
        assert second["ok"] is False

    def test_free_can_save_its_one_deck_from_the_builder(self, free, api):
        """It used to refuse every free save outright, which is a locked door
        rather than a taste."""
        made = _body(api.save_builder_as_deck("a", "A", "commander", DECK, ""))
        assert made.get("ok") is not False
        assert made["version_number"] == 1

    def test_both_doors_lead_to_the_same_count(self, free, api):
        _save(api, "editor")
        refused = _body(api.save_builder_as_deck("builder", "B", "commander", DECK, ""))
        assert refused["ok"] is False, "the builder ignored the editor's deck"


class TestSuggestionsAreFewerNotWorse:
    DECKLIST = ("Commander:\n1 Sol Ring\n\nMainboard:\n1 Brainstorm\n"
                "30 Island\n")

    @pytest.fixture
    def rich(self):
        """A catalogue with enough classifiable cards that the cap BITES.

        With three cards the suggester returns fewer than the free allowance
        anyway, so a trim that never ran would satisfy every assertion — which
        is exactly what these did when first written.
        """
        from densa_deck.app.api import AppApi

        cards = [
            Card(scryfall_id="s1", oracle_id="o1", name="Sol Ring",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
                 type_line="Artifact", legalities={"commander": Legality.LEGAL},
                 oracle_text="{T}: Add {C}{C}."),
            Card(scryfall_id="s2", oracle_id="o2", name="Island",
                 layout=CardLayout.NORMAL, cmc=0, mana_cost="",
                 type_line="Basic Land — Island",
                 legalities={"commander": Legality.LEGAL},
                 oracle_text="({T}: Add {U}.)"),
            Card(scryfall_id="s3", oracle_id="o3", name="Brainstorm",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{U}",
                 type_line="Instant", legalities={"commander": Legality.LEGAL},
                 oracle_text="Draw three cards, then put two cards back."),
        ]
        # Cards that match the roles this deck is actually SHORT of, which
        # for a 32-card list is draw and removal. Filling it with ramp
        # produced nothing to suggest and a cap that never ran.
        for i in range(8):
            cards.append(Card(
                scryfall_id=f"d{i}", oracle_id=f"od{i}", name=f"Divination {i}",
                layout=CardLayout.NORMAL, cmc=3, mana_cost="{2}{U}",
                type_line="Sorcery", legalities={"commander": Legality.LEGAL},
                oracle_text="Draw two cards."))
            cards.append(Card(
                scryfall_id=f"k{i}", oracle_id=f"ok{i}", name=f"Doom Blade {i}",
                layout=CardLayout.NORMAL, cmc=2, mana_cost="{1}{U}",
                type_line="Instant", legalities={"commander": Legality.LEGAL},
                oracle_text="Destroy target creature."))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = CardDatabase(db_path=root / "cards.db")
            db.upsert_cards(cards)
            db.close()
            made = AppApi(db_path=root / "cards.db",
                          version_db_path=root / "versions.db")
            yield made
            made.close()

    def _suggest(self, api):
        return _body(api.suggest_deckbuild_additions(self.DECKLIST, "commander",
                                                     "D", 8))

    def test_the_allowance_actually_bites(self, pro, rich):
        """Guards the fixture: if Pro itself sees two or fewer, every
        assertion below is vacuous."""
        assert len(self._suggest(rich)["suggestions"]) > 2

    def test_free_sees_exactly_its_allowance(self, free, rich):
        assert len(self._suggest(rich)["suggestions"]) == 2

    def test_and_is_told_how_many_it_is_not_seeing(self, free, rich):
        out = self._suggest(rich)
        assert out["withheld"] > 0
        assert out["locked_feature"] == "deckbuild_suggestions"

    def test_pro_sees_the_lot(self, pro, rich):
        out = self._suggest(rich)
        assert out["withheld"] == 0
        assert len(out["suggestions"]) > 2

    def test_the_free_ones_are_the_same_ones_pro_sees_first(self, free, pro_api=None):
        """Order is not reshuffled for free — the taste is the top of the
        real list, not a worse list."""
        # Built inside the test so both tiers see the same catalogue.
        from densa_deck.app.api import AppApi

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = CardDatabase(db_path=root / "cards.db")
            db.upsert_cards([
                Card(scryfall_id=f"s{i}", oracle_id=f"o{i}", name=f"Card {i}",
                     layout=CardLayout.NORMAL, cmc=i % 5, mana_cost="{1}",
                     type_line="Artifact",
                     legalities={"commander": Legality.LEGAL})
                for i in range(12)
            ])
            db.close()
            api = AppApi(db_path=root / "cards.db",
                         version_db_path=root / "versions.db")
            try:
                os.environ["MTG_ENGINE_TIER"] = "pro"
                full = _body(api.suggest_deckbuild_additions(
                    self.DECKLIST, "commander", "D", 8))["suggestions"]
                os.environ["MTG_ENGINE_TIER"] = "free"
                taste = _body(api.suggest_deckbuild_additions(
                    self.DECKLIST, "commander", "D", 8))["suggestions"]
            finally:
                api.close()
        names = lambda rows: [r.get("name") or r.get("card_name") for r in rows]
        assert names(taste) == names(full)[:len(taste)]


class TestTheCollectionSplit:
    """Describing your own cards is free. What a BUNDLE is worth is the
    reseller workflow, which is the portfolio layer Pro is for."""

    @pytest.fixture
    def stocked(self, api):
        from densa_deck.collection.storage import CollectionStore

        store = CollectionStore(db_path=Path(api._get_db().db_path).parent
                                / "collection.db")
        api._collection_store = store
        store.add_copies("s1", "Sol Ring", quantity=2)
        store.add_copies("s2", "Island", quantity=10)
        return api, store

    def test_composition_is_free(self, free, stocked):
        api, _store = stocked
        out = _body(api.get_collection_breakdown())
        assert out["total_cards"] == 12
        assert out["colors"] and out["types"] and out["curve"]

    def test_what_it_is_all_worth_is_free(self, free, stocked):
        api, _store = stocked
        assert _body(api.get_collection_breakdown())["value_usd"] is not None

    def test_what_a_bundle_is_worth_is_not(self, free, stocked):
        api, store = stocked
        made = store.create_collection("Bundle")
        items, _ = store.list_items(limit=5)
        store.add_to_collection(items[0].item_id, made["collection_id"])

        out = _body(api.get_collection_breakdown(made["collection_uid"]))
        assert out["value_usd"] is None
        assert out["locked_feature"] == "collection_analytics"
        assert out["total_cards"], "the composition still answered"

    def test_pro_prices_the_bundle(self, pro, stocked):
        api, store = stocked
        made = store.create_collection("Bundle")
        items, _ = store.list_items(limit=5)
        store.add_to_collection(items[0].item_id, made["collection_id"])
        out = _body(api.get_collection_breakdown(made["collection_uid"]))
        assert out["value_usd"] is not None
        assert "locked_feature" not in out


class TestThePhoneIsNotAWayRound:
    """It had no tier awareness at all: a free user with the companion could
    call the analyst routes and get every Pro capability for nothing."""

    @pytest.fixture
    def bridge(self, api):
        from densa_deck.app.phone import PhoneBridge

        return PhoneBridge(api)

    def test_the_analyst_is_refused_on_free(self, free, bridge):
        """Through `handle_api`, which is the door the phone knocks on.

        Testing `_route_locked` alone proves the rule exists and nothing at
        all about whether the dispatcher consults it — and a gate the
        dispatcher skips is not a gate.
        """
        for route in ("analyst/analyze", "analyst/explain"):
            reply = bridge.handle_api(route, {"decklist_text": "1 Sol Ring"})
            assert reply.get("error_type") == "ProRequired", route

    def test_the_rule_itself_names_those_routes(self, free, bridge):
        assert bridge._route_locked("analyst/analyze") is not None
        assert bridge._route_locked("analyst/explain") is not None

    def test_the_refusal_says_why(self, free, bridge):
        locked = bridge._route_locked("analyst/analyze")
        assert locked["error_type"] == "ProRequired"
        assert "Pro" in locked["error"]

    def test_free_routes_stay_open(self, free, bridge):
        for route in ("cards/search", "collection/list", "decks/list",
                      "sync/pull", "capture"):
            assert bridge._route_locked(route) is None, route

    def test_pro_reaches_the_analyst(self, pro, bridge):
        assert bridge._route_locked("analyst/analyze") is None

    def test_the_phone_can_ask_what_it_may_do(self, free, bridge):
        snap = bridge.handle_api("tier", {})
        assert snap["tier"] == "free"
        assert snap["is_pro"] is False
        assert snap["allowances"]["saved_decks"] == 1

    def test_and_gets_the_answer_beside_its_capabilities(self, free, bridge):
        """So activating Pro on the desktop reaches the phone without
        reinstalling it."""
        out = bridge.handle_api("capabilities", {})
        assert out["tier"]["is_pro"] is False

    def test_pro_is_told_it_is_pro(self, pro, bridge):
        assert bridge.handle_api("tier", {})["is_pro"] is True

    def test_an_unreadable_tier_fails_open(self, free, bridge, monkeypatch):
        """A paying user must not be locked out of their own phone because a
        licence file could not be read. The desktop UI and the licence are
        the real record; this is one of several places that reads them."""
        import densa_deck.tiers as tiers

        def boom(*_args, **_kwargs):
            raise OSError("licence unreadable")

        monkeypatch.setattr(tiers, "check_access", boom)
        assert bridge._route_locked("analyst/analyze") is None


class TestTheCardPanelSplitsDownTheMiddle:
    """What a card DOES here is free; what to ADD is not.

    The panel reaches the same recommendation engine `suggest_deckbuild_additions`
    charges for, so it costs the same wherever it is reached from — otherwise
    the cheaper door is the one everybody uses.
    """

    @pytest.fixture
    def aristocrats(self):
        """An outlet, a payoff, and enough partners that the cap bites."""
        from densa_deck.app.api import AppApi

        cards = [
            Card(scryfall_id="a1", oracle_id="oa1", name="Viscera Seer",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{B}",
                 type_line="Creature — Vampire Wizard",
                 legalities={"commander": Legality.LEGAL},
                 oracle_text="Sacrifice a creature: Scry 1."),
            Card(scryfall_id="a2", oracle_id="oa2", name="Bitterblossom",
                 layout=CardLayout.NORMAL, cmc=2, mana_cost="{1}{B}",
                 type_line="Enchantment",
                 legalities={"commander": Legality.LEGAL},
                 oracle_text=("At the beginning of your upkeep, you lose 1 "
                              "life and create a 1/1 black Faerie Rogue "
                              "creature token with flying.")),
            Card(scryfall_id="a3", oracle_id="oa3", name="Swamp",
                 layout=CardLayout.NORMAL, cmc=0, mana_cost="",
                 type_line="Basic Land — Swamp",
                 legalities={"commander": Legality.LEGAL},
                 oracle_text="({T}: Add {B}.)"),
        ]
        # Eight payoffs the panel can offer for a token maker.
        for i in range(8):
            cards.append(Card(
                scryfall_id=f"p{i}", oracle_id=f"op{i}", name=f"Blood Artist {i}",
                layout=CardLayout.NORMAL, cmc=2, mana_cost="{1}{B}",
                type_line="Creature — Vampire",
                legalities={"commander": Legality.LEGAL},
                oracle_text=("Whenever another creature dies, target player "
                             "loses 1 life and you gain 1 life.")))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = CardDatabase(db_path=root / "cards.db")
            db.upsert_cards(cards)
            db.close()
            made = AppApi(db_path=root / "cards.db",
                          version_db_path=root / "versions.db")
            yield made
            made.close()

    DECK = "Mainboard:\n1 Viscera Seer\n1 Bitterblossom\n30 Swamp\n"

    def _panel(self, api):
        return _body(api.card_synergy_report("Bitterblossom", self.DECK,
                                             "commander"))

    def test_the_allowance_actually_bites(self, pro, aristocrats):
        assert len(self._panel(aristocrats)["suggestions"]) > 2

    def test_free_gets_what_the_card_does_here(self, free, aristocrats):
        """The free half has to be genuinely useful, or the panel reads as
        broken rather than as limited."""
        out = self._panel(aristocrats)
        assert out["fit"]["roles"] or out["fit"]["tags"]
        assert out["in_deck"], "the deck-side synergies are free"

    def test_free_gets_a_taste_of_what_to_add(self, free, aristocrats):
        out = self._panel(aristocrats)
        assert len(out["suggestions"]) == 2
        assert out["withheld"] > 0
        assert out["locked_feature"] == "deckbuild_suggestions"

    def test_pro_gets_the_whole_list(self, pro, aristocrats):
        out = self._panel(aristocrats)
        assert out.get("withheld", 0) == 0
        assert len(out["suggestions"]) > 2
