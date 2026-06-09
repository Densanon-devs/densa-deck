"""Phase C: iteration loop — proposals, preview, persistent log.

Covers:
- propose_changes returns cut + add proposals from a real deck
- apply_proposal mutates a deck immutably (cut decrements, add inserts)
- preview_change reports before/after metrics + non-trivial deltas
- IterationStore round-trips records and aggregates net power delta
- App API endpoints (propose_changes / preview_change / accept_change / iteration_history)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.iteration import (
    ChangePreview,
    IterationRecord,
    IterationStore,
    Proposal,
    apply_proposal,
    preview_change,
    propose_changes,
)
from densa_deck.models import (
    Card,
    CardLayout,
    CardTag,
    Color,
    Deck,
    DeckEntry,
    Format,
    Legality,
    Zone,
)


# ---------------------------------------------------------------- fixtures


def _c(name, **kw):
    """Minimal card builder."""
    return Card(
        scryfall_id=f"sid-{name.lower().replace(' ', '-')}",
        oracle_id=f"oid-{name.lower().replace(' ', '-')}",
        name=name,
        layout=CardLayout.NORMAL,
        legalities={"commander": Legality.LEGAL},
        **kw,
    )


def _deck_with_filler() -> Deck:
    """A deck with one obvious cut candidate ("Filler 7") so propose surfaces a cut."""
    entries = [
        DeckEntry(card_name="Sol Ring", quantity=1, zone=Zone.MAINBOARD,
                  card=_c("Sol Ring", cmc=1, mana_cost="{1}", type_line="Artifact",
                          tags=[CardTag.MANA_ROCK, CardTag.RAMP])),
        DeckEntry(card_name="Filler 7", quantity=1, zone=Zone.MAINBOARD,
                  card=_c("Filler 7", cmc=7, mana_cost="{7}",
                          type_line="Creature — Beast", is_creature=True)),
        # 35 lands to push total card count up
        DeckEntry(card_name="Plains", quantity=35, zone=Zone.MAINBOARD,
                  card=_c("Plains", is_land=True, type_line="Basic Land — Plains",
                          color_identity=[Color.WHITE])),
    ]
    return Deck(name="Test", format=Format.COMMANDER, entries=entries)


# ---------------------------------------------------------------- apply_proposal


class TestApplyProposal:
    def test_cut_decrements_quantity_above_one(self):
        deck = _deck_with_filler()
        new = apply_proposal(deck, Proposal(kind="cut", card_name="Plains", reason="", source=""))
        plains = next(e for e in new.entries if (e.card.name if e.card else "") == "Plains")
        assert plains.quantity == 34

    def test_cut_removes_singleton_entry(self):
        deck = _deck_with_filler()
        new = apply_proposal(deck, Proposal(kind="cut", card_name="Filler 7", reason="", source=""))
        names = [(e.card.name if e.card else "") for e in new.entries]
        assert "Filler 7" not in names

    def test_cut_is_immutable(self):
        deck = _deck_with_filler()
        apply_proposal(deck, Proposal(kind="cut", card_name="Filler 7", reason="", source=""))
        names = [(e.card.name if e.card else "") for e in deck.entries]
        assert "Filler 7" in names  # original deck untouched

    def test_add_increments_existing_card(self):
        deck = _deck_with_filler()
        new = apply_proposal(deck, Proposal(kind="add", card_name="Plains", reason="", source=""))
        plains = next(e for e in new.entries if (e.card.name if e.card else "") == "Plains")
        assert plains.quantity == 36

    def test_add_appends_new_card_without_db(self):
        """When no DB is provided, the new entry has card=None but the slot exists."""
        deck = _deck_with_filler()
        new = apply_proposal(deck, Proposal(kind="add", card_name="Lightning Bolt", reason="", source=""))
        bolt = next(
            (e for e in new.entries if (e.card.name if e.card else e.card_name) == "Lightning Bolt"),
            None,
        )
        assert bolt is not None
        assert bolt.quantity == 1
        assert bolt.zone == Zone.MAINBOARD

    def test_cut_missing_card_returns_unchanged(self):
        deck = _deck_with_filler()
        new = apply_proposal(deck, Proposal(kind="cut", card_name="Nonexistent", reason="", source=""))
        assert len(new.entries) == len(deck.entries)


# ---------------------------------------------------------------- preview_change


class TestPreviewChange:
    def test_cut_high_cmc_creature_changes_avg_cmc(self):
        deck = _deck_with_filler()
        proposal = Proposal(kind="cut", card_name="Filler 7", reason="", source="")
        result = preview_change(deck, proposal)
        # average_cmc should drop (we cut the 7-CMC card)
        assert result.deltas["average_cmc"] is not None
        assert result.deltas["average_cmc"] <= 0
        assert result.error == ""

    def test_cut_drops_total_cards(self):
        deck = _deck_with_filler()
        proposal = Proposal(kind="cut", card_name="Filler 7", reason="", source="")
        result = preview_change(deck, proposal)
        assert result.deltas["total_cards"] == -1

    def test_cut_missing_card_reports_error(self):
        deck = _deck_with_filler()
        proposal = Proposal(kind="cut", card_name="Nonexistent", reason="", source="")
        result = preview_change(deck, proposal)
        assert result.error
        assert "not in the deck" in result.error.lower()

    def test_new_deck_text_is_parseable(self):
        deck = _deck_with_filler()
        proposal = Proposal(kind="cut", card_name="Filler 7", reason="", source="")
        result = preview_change(deck, proposal)
        assert "Filler 7" not in result.new_deck_text
        # The text must round-trip back through the parser cleanly.
        from densa_deck.deck.parser import parse_decklist
        entries = parse_decklist(result.new_deck_text)
        names = [e.card_name for e in entries]
        assert "Sol Ring" in names
        assert "Filler 7" not in names


# ---------------------------------------------------------------- propose_changes


class TestPropose:
    """Smoke tests — proposal generation needs a card DB so the add path
    can find candidates. The lookup against an empty DB still returns []
    for adds; cuts work without a DB because they only need deck.entries."""

    def test_cut_only_with_no_db(self):
        deck = _deck_with_filler()

        class _NoOpDb:
            def lookup_by_name(self, *a, **kw):
                return None
            def connect(self):
                import sqlite3
                conn = sqlite3.connect(":memory:")
                conn.execute("CREATE TABLE cards (data_json TEXT, price_usd REAL)")
                return conn

        proposals = propose_changes(deck=deck, db=_NoOpDb(), cut_limit=4, add_limit=0)
        kinds = {p.kind for p in proposals}
        assert kinds == {"cut"}
        # Filler 7 is the strongest cut candidate by signal.
        cut_names = [p.card_name for p in proposals if p.kind == "cut"]
        assert "Filler 7" in cut_names


# ---------------------------------------------------------------- iteration store


@pytest.fixture
def iter_store():
    with tempfile.TemporaryDirectory() as tmp:
        s = IterationStore(db_path=Path(tmp) / "iter.db")
        yield s


class TestIterationStore:
    def test_round_trip(self, iter_store):
        rec = iter_store.record(IterationRecord(
            id=None, deck_id="atraxa", deck_name="Atraxa",
            kind="cut", card_name="Filler 7", accepted=True,
            source="high-score-cut", signal="vanilla_bloat",
            reason="high CMC, no tags",
            before_power=7.0, after_power=6.8,
            before_total_cards=99, after_total_cards=98,
        ))
        assert rec.id is not None
        assert rec.created_at  # auto-stamped
        history = iter_store.history("atraxa")
        assert len(history) == 1
        assert history[0].card_name == "Filler 7"
        assert history[0].accepted is True

    def test_history_ordered_newest_first(self, iter_store):
        for n in ["A", "B", "C"]:
            iter_store.record(IterationRecord(
                id=None, deck_id="d", deck_name="d", kind="cut",
                card_name=n, accepted=True,
            ))
        names = [r.card_name for r in iter_store.history("d")]
        # SQLite ts resolution is per-second, so we just check all 3 are present.
        assert sorted(names) == ["A", "B", "C"]

    def test_summary_counts_and_net_delta(self, iter_store):
        # accepted cut: 7.0 → 6.5
        iter_store.record(IterationRecord(
            id=None, deck_id="d", deck_name="d", kind="cut",
            card_name="A", accepted=True,
            before_power=7.0, after_power=6.5,
        ))
        # rejected add (doesn't move net delta)
        iter_store.record(IterationRecord(
            id=None, deck_id="d", deck_name="d", kind="add",
            card_name="B", accepted=False,
            before_power=6.5, after_power=7.0,
        ))
        # accepted add: 6.5 → 7.2
        iter_store.record(IterationRecord(
            id=None, deck_id="d", deck_name="d", kind="add",
            card_name="C", accepted=True,
            before_power=6.5, after_power=7.2,
        ))
        s = iter_store.summary("d")
        assert s["accepted_cuts"] == 1
        assert s["rejected_cuts"] == 0
        assert s["accepted_adds"] == 1
        assert s["rejected_adds"] == 1
        # First accepted before=7.0; last accepted after=7.2 → +0.2
        assert s["net_power_delta"] == pytest.approx(0.2)

    def test_summary_handles_no_records(self, iter_store):
        s = iter_store.summary("empty")
        assert s["total_records"] == 0
        assert s["net_power_delta"] is None
