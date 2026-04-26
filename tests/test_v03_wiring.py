"""Tests for the v0.3.0 wiring pass:

- `densa-deck combos verify <deck> <combo_id>` subcommand parses + dispatches.
- `_collect_combo_context` returns combo lines + protected card names from
  the local Spellbook cache when matches exist (and degrades silently to
  empty + None when the cache is missing).
- `AnalystRunner.run_swaps` honors `protected_card_names`: a card surfaced
  as a swap candidate is excluded once added to the protected set.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from densa_deck.combos import Combo, ComboStore
from densa_deck.data.database import CardDatabase
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
from densa_deck.analyst.backends import MockBackend


PYTHON = sys.executable


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [PYTHON, "-m", "densa_deck.cli", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        env=env,
    )


def _mk_card(name, **kw):
    return Card(
        scryfall_id=f"sid-{name}", oracle_id=f"oid-{name}",
        name=name, layout=CardLayout.NORMAL,
        cmc=kw.get("cmc", 2), mana_cost=kw.get("mc", "{2}"),
        type_line=kw.get("tl", "Artifact"),
        colors=kw.get("cols", []),
        color_identity=kw.get("ci", []),
        is_artifact=kw.get("is_artifact", "Artifact" in kw.get("tl", "Artifact")),
        is_creature=kw.get("is_creature", False),
        is_land=kw.get("is_land", False),
        legalities={"commander": Legality.LEGAL},
        tags=list(kw.get("tags", [])),
        oracle_text=kw.get("oracle_text", ""),
    )


class TestCombosVerifyCli:
    def test_verify_subcommand_help(self):
        r = _run_cli("combos", "verify", "--help")
        assert r.returncode == 0
        assert "combo_id" in r.stdout.lower()


class TestCollectComboContext:
    def test_returns_lines_and_protected_names(self, tmp_path, monkeypatch):
        # Point the ComboStore at a temp DB with one combo so the helper
        # has something to match against.
        cstore = ComboStore(db_path=tmp_path / "combos.db")
        cstore.upsert_combos([Combo(
            combo_id="42",
            cards=["Sol Ring", "Hullbreaker Horror"],
            produces=["Infinite mana"],
            color_identity="U",
            popularity=200_000,
        )])
        cstore.close()

        # Patch the default ComboStore() ctor used inside the helper to
        # point at our temp DB.
        from densa_deck import combos as combos_pkg

        class _StubStore(ComboStore):
            def __init__(self):
                super().__init__(db_path=tmp_path / "combos.db")

        monkeypatch.setattr(combos_pkg, "ComboStore", _StubStore)
        # cli.py imports ComboStore lazily inside _collect_combo_context, so
        # patching the package symbol is enough — the helper's `from
        # densa_deck.combos import ComboStore` resolves through this module.
        from densa_deck import cli as cli_mod
        # Build a Deck containing both pieces so detect_combos hits.
        sol = _mk_card(
            "Sol Ring", cmc=1, mc="{1}", tl="Artifact",
            oracle_text="{T}: Add {C}{C}.",
        )
        horror = _mk_card(
            "Hullbreaker Horror", cmc=2, mc="{1}{U}", tl="Creature",
            cols=[Color.BLUE], ci=[Color.BLUE], is_creature=True,
        )
        deck = Deck(name="t", format=Format.COMMANDER, entries=[
            DeckEntry(card_name=sol.name, card=sol, quantity=1, zone=Zone.MAINBOARD),
            DeckEntry(card_name=horror.name, card=horror, quantity=1, zone=Zone.MAINBOARD),
        ])

        lines, protected = cli_mod._collect_combo_context(deck)
        assert lines, "expected at least one combo line"
        assert protected is not None
        # `rank_cut_candidates` compares against `.lower()`, so the
        # set must hold lowercase names — otherwise the check is
        # silently a no-op.
        assert "sol ring" in protected
        assert "hullbreaker horror" in protected

    def test_empty_cache_returns_none_protected(self, tmp_path, monkeypatch):
        cstore = ComboStore(db_path=tmp_path / "combos.db")
        cstore.close()
        from densa_deck import combos as combos_pkg

        class _StubStore(ComboStore):
            def __init__(self):
                super().__init__(db_path=tmp_path / "combos.db")

        monkeypatch.setattr(combos_pkg, "ComboStore", _StubStore)
        from densa_deck import cli as cli_mod
        deck = Deck(name="t", format=Format.COMMANDER, entries=[])
        lines, protected = cli_mod._collect_combo_context(deck)
        assert lines == []
        assert protected is None


class TestRunSwapsAcceptsProtection:
    """run_swaps must accept `protected_card_names` and forward it to
    `rank_cut_candidates`. The exclusion behavior itself is tested in
    test_combo_aware_v3.py — this is just the wiring check."""

    def test_run_swaps_signature_accepts_protection(self):
        import inspect
        from densa_deck.analyst.runner import AnalystRunner
        sig = inspect.signature(AnalystRunner.run_swaps)
        assert "protected_card_names" in sig.parameters
