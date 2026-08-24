"""Regression locks for three defects found while building the collection arc.

All three predate that work and all three were silent — none produced a
traceback a user would ever see.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.app.api import AppApi
from densa_deck.versioning.storage import VersionStore


@pytest.fixture
def temp_dbs():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / "cards.db", Path(tmp) / "versions.db"


@pytest.fixture
def api(temp_dbs):
    card_db, version_db = temp_dbs
    a = AppApi(db_path=card_db, version_db_path=version_db)
    yield a
    a.close()


class TestDeckSnapshotIdentity:
    """`compare_decks_analyst` and `duel_decks` raised AttributeError on every
    call because DeckSnapshot had no name/format, and @_safe turned that into
    a generic {ok: false}. Two shipped features were dead with no visible cause.
    """

    @pytest.fixture
    def store(self, tmp_path):
        return VersionStore(db_path=tmp_path / "versions.db")

    def test_snapshot_exposes_name_and_format(self, store):
        store.save_version("d1", "My Deck", "modern", {"Sol Ring": 1},
                           {"mainboard": ["Sol Ring"]})
        snap = store.get_latest("d1")
        assert snap.name == "My Deck"
        assert snap.format == "modern"

    def test_get_version_too(self, store):
        store.save_version("d1", "My Deck", "commander", {"Sol Ring": 1}, {})
        snap = store.get_version("d1", 1)
        assert snap.name == "My Deck"

    def test_all_versions_carry_identity(self, store):
        store.save_version("d1", "My Deck", "legacy", {"A": 1}, {})
        store.save_version("d1", "My Deck", "legacy", {"A": 2}, {})
        versions = store.get_all_versions("d1")
        assert len(versions) == 2
        assert all(v.name == "My Deck" and v.format == "legacy" for v in versions)

    def test_attribute_access_never_raises(self, store):
        # The literal failure mode: a bare snapshot must answer both.
        from densa_deck.versioning.storage import DeckSnapshot
        snap = DeckSnapshot()
        assert snap.name == ""
        assert snap.format == ""

    def test_missing_deck_row_degrades_to_empty(self, store):
        """A version whose parent deck row is gone must not explode."""
        store.save_version("orphan", "Gone", "modern", {"A": 1}, {})
        with store._lock if hasattr(store, "_lock") else _nullcontext():
            conn = store.connect()
            conn.execute("DELETE FROM decks WHERE deck_id = ?", ("orphan",))
            conn.commit()
        snap = store.get_latest("orphan")
        assert snap is not None
        assert snap.name == ""
        assert snap.format == ""

    def test_api_payload_carries_name_and_format(self, api):
        api._get_vstore().save_version("d1", "Nice Deck", "pioneer",
                                       {"Sol Ring": 1}, {"mainboard": ["Sol Ring"]})
        r = api.get_deck_latest("d1")
        assert r["ok"] is True
        data = r.get("data", r)
        assert data["name"] == "Nice Deck"
        assert data["format"] == "pioneer"


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


class TestMcpSearchFilters:
    """MCP silently dropped two filters and crashed on a third."""

    def _query(self, monkeypatch):
        """Capture the dict the MCP tool actually hands to AppApi."""
        from densa_deck.mcp import tools as tools_mod

        seen = {}

        class FakeApi:
            def search_cards(self, query):
                seen.update(query)
                return {"ok": True, "data": {"cards": [], "total": 0}}

        free = tools_mod.make_free_tools(FakeApi())
        return free["search_cards"], seen

    def test_type_filter_reaches_the_api(self, monkeypatch):
        search, seen = self._query(monkeypatch)
        search(type_line="creature")
        # AppApi reads `types`, not `type_line`.
        assert seen.get("types") == ["creature"]
        assert "type_line" not in seen

    def test_price_cap_reaches_the_api(self, monkeypatch):
        search, seen = self._query(monkeypatch)
        search(max_price_usd=5.0)
        assert seen.get("max_price") == 5.0
        assert "max_price_usd" not in seen

    def test_rarity_list_does_not_crash(self, monkeypatch):
        search, seen = self._query(monkeypatch)
        search(rarity=["mythic"])          # would AttributeError on .strip()
        assert seen.get("rarity") == "mythic"

    def test_rarity_string_still_works(self, monkeypatch):
        search, seen = self._query(monkeypatch)
        search(rarity="rare")
        assert seen.get("rarity") == "rare"

    def test_color_match_normalised(self, monkeypatch):
        search, seen = self._query(monkeypatch)
        search(color_match="subset")
        assert seen.get("color_match") == "identity"
        search(color_match="any")
        assert seen.get("color_match") == "any"

    def test_filters_survive_end_to_end(self, api):
        """Through the real AppApi, against a real card table."""
        from densa_deck.mcp import tools as tools_mod
        from densa_deck.models import Card, CardLayout

        api._get_db().upsert_cards([
            Card(scryfall_id="c1", oracle_id="o1", name="Cheap Guy",
                 layout=CardLayout.NORMAL, type_line="Creature — Human",
                 is_creature=True, price_usd=1.0),
            Card(scryfall_id="c2", oracle_id="o2", name="Pricey Bolt",
                 layout=CardLayout.NORMAL, type_line="Instant",
                 is_instant=True, price_usd=99.0),
        ])
        search = tools_mod.make_free_tools(api)["search_cards"]

        by_type = search(type_line="creature")
        assert [c["name"] for c in by_type["cards"]] == ["Cheap Guy"]

        by_price = search(max_price_usd=5.0)
        assert [c["name"] for c in by_price["cards"]] == ["Cheap Guy"]

        # The call that used to raise.
        assert search(rarity=["mythic"])["total"] == 0


class TestStoreScoping:
    """Iteration and playgroup stores wrote into the real ~/.densa-deck even
    when the API was pointed at a temp directory, so tests polluted the user's
    actual data directory."""

    def test_iteration_store_beside_the_card_db(self, api, temp_dbs):
        card_db, _ = temp_dbs
        store = api._get_iteration_store()
        assert Path(store.db_path).parent == card_db.parent

    def test_playgroup_store_beside_the_card_db(self, api, temp_dbs):
        card_db, _ = temp_dbs
        store = api._get_playgroup_store()
        assert Path(store.db_path).parent == card_db.parent

    def test_playgroup_writes_stay_in_the_sandbox(self, api, temp_dbs):
        card_db, _ = temp_dbs
        api.create_playgroup("Test Pod")
        assert (card_db.parent / "playgroup.db").exists()

    def test_all_stores_share_one_directory(self, api, temp_dbs):
        card_db, _ = temp_dbs
        api.add_to_collection("p1", "Sol Ring", 1)
        api.create_playgroup("Pod")
        parents = {
            Path(api._get_collection_store().db_path).parent,
            Path(api._get_iteration_store().db_path).parent,
            Path(api._get_playgroup_store().db_path).parent,
        }
        assert parents == {card_db.parent}
