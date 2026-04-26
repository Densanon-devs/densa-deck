"""Tests for the densa_deck.mcp package — the MCP server surface.

Coverage:
  - Tool registration: free-only mode vs full mode.
  - License gate: Pro tools refuse on a free user, succeed on Pro.
  - Wrapper unwrap: AppApi {ok, data} envelope flattens to bare dict;
    {ok: false} raises with a clear message.
  - Server smoke: build_server() returns a FastMCP with the expected
    tool names registered.

These tests don't actually run the JSON-RPC stdio loop — that's tested
end-to-end manually with `densa-deck mcp serve | mcp inspect` (or via
Claude desktop). Here we exercise the in-process tool callables which is
where 90% of the bug surface lives.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from densa_deck.mcp.license_gate import (
    ProRequiredError,
    assert_pro,
    current_tier,
    is_pro,
)
from densa_deck.mcp.tools import _unwrap, make_free_tools, make_pro_tools


class TestUnwrap:
    def test_ok_envelope_flattens_to_data(self):
        assert _unwrap({"ok": True, "data": {"hello": 1}}) == {"hello": 1}

    def test_no_envelope_returns_as_is(self):
        # Some AppApi paths return raw lists/dicts. Pass-through.
        assert _unwrap({"foo": "bar"}) == {"foo": "bar"}

    def test_error_envelope_raises_with_message(self):
        with pytest.raises(RuntimeError, match="ProRequired: Need Pro"):
            _unwrap({"ok": False, "error": "Need Pro", "error_type": "ProRequired"})

    def test_error_without_type_uses_default_kind(self):
        with pytest.raises(RuntimeError, match="EngineError: Boom"):
            _unwrap({"ok": False, "error": "Boom"})


class TestLicenseGate:
    def test_free_tier_blocks_pro_features(self, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "free")
        assert is_pro() is False
        with pytest.raises(ProRequiredError) as exc_info:
            assert_pro("goldfish_simulation")
        # Error message must name the feature so the AI can explain.
        assert "goldfish_simulation" in str(exc_info.value)

    def test_pro_tier_allows_pro_features(self, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        assert is_pro() is True
        # Should not raise
        assert_pro("goldfish_simulation")
        assert_pro("analyst")
        assert_pro("compare_decks")

    def test_free_tier_allows_free_features(self, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "free")
        # No raise even on free.
        assert_pro("card_search")
        assert_pro("combos")


class TestToolRegistration:
    def test_free_tools_dict_has_expected_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "free")
        # AppApi wants a writable home dir; sandbox it.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        from densa_deck.app.api import AppApi
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            free = make_free_tools(api)
            # Must include the headline read-only tools.
            for required in ("get_tier", "search_cards", "analyze_deck",
                             "list_saved_decks", "detect_combos_for_deck",
                             "build_rule0_worksheet", "assess_bracket_fit"):
                assert required in free, f"missing free tool: {required}"
            # Pro-only tools MUST NOT be in the free dict.
            for forbidden in ("run_goldfish", "run_gauntlet",
                              "explain_card_in_deck", "compare_decks_analyst"):
                assert forbidden not in free, f"free dict leaked pro tool: {forbidden}"
        finally:
            api.close()

    def test_pro_tools_dict_has_expected_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        from densa_deck.app.api import AppApi
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            pro = make_pro_tools(api)
            for required in ("run_goldfish", "run_gauntlet", "duel_decks",
                             "compare_decks_analyst", "explain_card_in_deck",
                             "save_deck_version", "coach_start", "coach_ask",
                             "coach_close"):
                assert required in pro, f"missing pro tool: {required}"
        finally:
            api.close()


class TestProGateAtToolLevel:
    """Defense in depth: even if the Pro tool dict is registered on a free
    user (full-mode server), invoking the tool should still refuse."""

    def test_run_goldfish_refuses_on_free(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "free")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        from densa_deck.app.api import AppApi
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            pro = make_pro_tools(api)
            with pytest.raises(ProRequiredError):
                # Args don't matter — the assert_pro happens before any
                # AppApi work.
                pro["run_goldfish"]("Sol Ring", sims=10)
        finally:
            api.close()


class TestServerBuilds:
    # async def + pytest-asyncio (auto mode in pyproject.toml). Avoids the
    # asyncio.run() loop-close that breaks `asyncio.get_event_loop()`-based
    # tests later in the suite (e.g. test_new_features.py's Moxfield 403
    # check) on Python 3.10.

    async def test_full_mode_registers_free_and_pro_tools(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        # Skip if `mcp` SDK isn't installed in the test env.
        pytest.importorskip("mcp.server.fastmcp")
        from densa_deck.app.api import AppApi
        from densa_deck.mcp.server import build_server
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            server = build_server(read_only=False, api=api)
            tools = await server.list_tools()
            names = {t.name for t in tools}
            # Spot-check both surfaces.
            assert "search_cards" in names
            assert "run_goldfish" in names
        finally:
            api.close()

    async def test_read_only_mode_excludes_pro_tools(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        pytest.importorskip("mcp.server.fastmcp")
        from densa_deck.app.api import AppApi
        from densa_deck.mcp.server import build_server
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            server = build_server(read_only=True, api=api)
            tools = await server.list_tools()
            names = {t.name for t in tools}
            assert "search_cards" in names  # free still present
            # Pro tools must not be visible at all in read-only mode.
            for forbidden in ("run_goldfish", "run_gauntlet",
                              "compare_decks_analyst", "coach_start"):
                assert forbidden not in names, (
                    f"read-only mode leaked pro tool: {forbidden}"
                )
        finally:
            api.close()


class TestCliWiring:
    """The `densa-deck mcp` subcommand should at least parse without
    importing the optional MCP SDK — that lets us test it on environments
    where `mcp` isn't installed."""

    def test_mcp_subcommand_help_parses(self):
        import subprocess
        import sys
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run(
            [sys.executable, "-m", "densa_deck.cli", "mcp", "--help"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=10, env=env,
        )
        assert r.returncode == 0
        assert "serve" in r.stdout.lower()
