"""Phase 2 — printing-level valuation, price history, and price-aware search."""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from densa_deck.collection.prices import (
    ScryfallBulkProvider,
    capture_price_snapshot,
    is_stale,
    price_age_hours,
    price_history_for_printing,
    value_collection,
    value_deltas,
)
from densa_deck.collection.query import collection_sets, search_collection
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall

CHEAP = "aaaaaaaa-0000-0000-0000-000000000001"
DEAR = "bbbbbbbb-0000-0000-0000-000000000002"
NOPRICE = "cccccccc-0000-0000-0000-000000000003"
ORACLE = "6ad8011d-3471-4369-9d68-b264cc027487"


def _raw(pid, name, set_code, num, *, usd=None, foil=None, etched=None,
         finishes=("nonfoil",), rarity="uncommon", set_name="Test Set"):
    return {
        "id": pid, "oracle_id": ORACLE, "name": name, "set": set_code,
        "set_name": set_name, "collector_number": num, "rarity": rarity,
        "lang": "en", "released_at": "2023-01-01", "finishes": list(finishes),
        "frame": "2015", "border_color": "black", "promo_types": [],
        "games": ["paper"], "tcgplayer_id": 1,
        "prices": {"usd": usd, "usd_foil": foil, "usd_etched": etched},
    }


@pytest.fixture
def env():
    """Collection + card DB wired together, with three deliberate cases:
    a cheap printing, an expensive foil, and one with no price at all."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_printings([
            printing_row_from_scryfall(
                _raw(CHEAP, "Sol Ring", "cmm", "410", usd="1.50", foil="4.00",
                     finishes=("nonfoil", "foil")), "t"),
            printing_row_from_scryfall(
                _raw(DEAR, "Sol Ring", "sld", "99", usd="21.72", foil="100.00",
                     finishes=("nonfoil", "foil"), rarity="mythic",
                     set_name="Secret Lair Drop"), "t"),
            printing_row_from_scryfall(
                _raw(NOPRICE, "Obscure Card", "old", "1"), "t"),
        ])
        db.set_metadata("printings_synced_at", "2026-08-14T12:00:00+00:00")
        store = CollectionStore(db_path=root / "collection.db")
        yield store, db
        db.close()


class TestProvider:
    def test_finish_selects_its_own_price(self, env):
        _, db = env
        p = ScryfallBulkProvider(db)
        printing = db.get_printing(CHEAP)
        assert p.price_for(printing, "nonfoil") == 1.50
        assert p.price_for(printing, "foil") == 4.00

    def test_unknown_finish_price_is_none_not_zero(self, env):
        _, db = env
        p = ScryfallBulkProvider(db)
        assert p.price_for(db.get_printing(CHEAP), "etched") is None
        assert p.price_for(db.get_printing(NOPRICE), "nonfoil") is None

    def test_missing_printing_is_none(self, env):
        _, db = env
        assert ScryfallBulkProvider(db).price_for(None, "nonfoil") is None

    def test_reports_sync_time(self, env):
        _, db = env
        assert ScryfallBulkProvider(db).synced_at().startswith("2026-08-14")


class TestStaleness:
    def test_never_synced_is_stale(self):
        assert is_stale("") is True
        assert price_age_hours("") is None

    def test_garbage_timestamp_is_stale(self):
        assert is_stale("not-a-date") is True

    def test_fresh_is_not_stale(self):
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc).isoformat()
        assert is_stale(now) is False

    def test_old_is_stale(self):
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(tz=timezone.utc) - timedelta(hours=30)).isoformat()
        assert is_stale(old) is True
        assert price_age_hours(old) > 24


class TestValuation:
    def test_empty_collection(self, env):
        store, db = env
        v = value_collection(store, db)
        assert v["total_value_usd"] == 0.0
        assert v["total_copies"] == 0

    def test_sums_condition_adjusted(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=2)             # 21.72 NM
        store.add_copies(CHEAP, "Sol Ring", quantity=1, condition="MP")  # 1.50*0.7
        v = value_collection(store, db)
        assert v["total_value_usd"] == pytest.approx(21.72 * 2 + 1.05, abs=0.01)

    def test_raw_mode_skips_condition(self, env):
        store, db = env
        store.add_copies(CHEAP, "Sol Ring", quantity=1, condition="DMG")
        v = value_collection(store, db, condition_adjusted=False)
        assert v["total_value_usd"] == 1.50

    def test_foil_valued_as_foil(self, env):
        """The specific inaccuracy this phase exists to fix."""
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1, finish="foil")
        assert value_collection(store, db)["total_value_usd"] == 100.00

    def test_unpriced_counted_separately_never_as_zero(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        store.add_copies(NOPRICE, "Obscure Card", quantity=5)
        v = value_collection(store, db)
        assert v["total_value_usd"] == 21.72
        assert v["unpriced_copies"] == 5
        assert v["unpriced_stacks"] == 1
        assert v["total_copies"] == 6  # unpriced still counted as owned

    def test_unknown_printing_is_unpriced_not_crash(self, env):
        store, db = env
        store.add_copies("not-in-catalogue", "Mystery", quantity=2)
        v = value_collection(store, db)
        assert v["total_value_usd"] == 0.0
        assert v["unpriced_copies"] == 2

    def test_most_valuable_ranked_by_stack(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1)          # 21.72
        store.add_copies(CHEAP, "Sol Ring", quantity=100)        # 150.00
        top = value_collection(store, db)["most_valuable"]
        assert top[0]["stack_value_usd"] == 150.00

    def test_reports_price_provenance(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        v = value_collection(store, db)
        assert v["price_source"] == "scryfall"
        assert v["prices_synced_at"].startswith("2026-08-14")
        assert "estimate" in v["attribution"].lower()
        assert "prices_stale" in v


class TestPriceHistory:
    def test_snapshot_captures_owned_only(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        written = capture_price_snapshot(store, db)
        assert written == 1  # not all three catalogue printings

    def test_snapshot_skips_unpriced(self, env):
        store, db = env
        store.add_copies(NOPRICE, "Obscure Card", quantity=1)
        assert capture_price_snapshot(store, db) == 0

    def test_snapshot_is_idempotent_per_day(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        capture_price_snapshot(store, db)
        capture_price_snapshot(store, db)
        points = price_history_for_printing(store, DEAR)
        assert len(points) == 1

    def test_history_reads_back_in_order(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        d1 = (date.today() - timedelta(days=2)).isoformat()
        d2 = (date.today() - timedelta(days=1)).isoformat()
        capture_price_snapshot(store, db, on_date=d1)
        capture_price_snapshot(store, db, on_date=d2)
        points = price_history_for_printing(store, DEAR)
        assert [p["captured_on"] for p in points] == [d1, d2]

    def test_history_is_per_finish(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1, finish="foil")
        store.add_copies(DEAR, "Sol Ring", quantity=1, finish="nonfoil")
        capture_price_snapshot(store, db)
        assert price_history_for_printing(store, DEAR, "foil")[0]["price_usd"] == 100.00
        assert price_history_for_printing(store, DEAR, "nonfoil")[0]["price_usd"] == 21.72


class TestDeltas:
    def test_no_history_reports_unavailable(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        assert value_deltas(store, db)["deltas"]["7d"] is None

    def test_delta_measures_price_move(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        old_day = (date.today() - timedelta(days=7)).isoformat()
        capture_price_snapshot(store, db, on_date=old_day)

        # Price rises 21.72 -> 30.00.
        conn = db.connect()
        conn.execute("UPDATE card_printings SET price_usd = 30.0 WHERE printing_id = ?",
                     (DEAR,))
        conn.commit()

        d = value_deltas(store, db, windows=(7,))["deltas"]["7d"]
        assert d["then_usd"] == 21.72
        assert d["now_usd"] == 30.00
        assert d["delta_usd"] == pytest.approx(8.28, abs=0.01)
        assert d["pct"] == pytest.approx(38.12, abs=0.1)

    def test_buying_a_card_is_not_a_price_move(self, env):
        """Deltas value TODAY's holdings at THEN's prices.

        Otherwise acquiring cards reads as the market going up, which tells
        you nothing about the market.
        """
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        old_day = (date.today() - timedelta(days=7)).isoformat()
        capture_price_snapshot(store, db, on_date=old_day)
        # Buy a second copy today; prices unchanged.
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        d = value_deltas(store, db, windows=(7,))["deltas"]["7d"]
        assert d["delta_usd"] == 0.0


class TestPriceAwareSearch:
    def test_min_price_filter(self, env):
        store, db = env
        store.add_copies(CHEAP, "Sol Ring", quantity=1)
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        items, total, _ = search_collection(store, db, min_price=10.0)
        assert total == 1
        assert items[0].set_code == "sld"

    def test_max_price_filter(self, env):
        store, db = env
        store.add_copies(CHEAP, "Sol Ring", quantity=1)
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        items, total, _ = search_collection(store, db, max_price=10.0)
        assert total == 1
        assert items[0].set_code == "cmm"

    def test_price_filter_excludes_unpriced(self, env):
        """Here NULL price must NOT pass — the user asked a money question."""
        store, db = env
        store.add_copies(NOPRICE, "Obscure Card", quantity=1)
        _, total, _ = search_collection(store, db, min_price=0.0)
        assert total == 0

    def test_unpriced_only_finds_them(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        store.add_copies(NOPRICE, "Obscure Card", quantity=1)
        items, total, _ = search_collection(store, db, unpriced_only=True)
        assert total == 1
        assert items[0].card_name == "Obscure Card"

    def test_condition_affects_the_price_filter(self, env):
        # A damaged $21.72 card is estimated at $6.52 and must fall below a
        # $10 floor — filtering on sticker price would be wrong.
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1, condition="DMG")
        _, total, _ = search_collection(store, db, min_price=10.0)
        assert total == 0

    def test_sort_by_value_desc(self, env):
        store, db = env
        store.add_copies(CHEAP, "Sol Ring", quantity=1)
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        items, _, _ = search_collection(store, db, sort="value_desc")
        assert items[0].set_code == "sld"

    def test_sort_by_value_asc_puts_unpriced_last(self, env):
        store, db = env
        store.add_copies(NOPRICE, "Obscure Card", quantity=1)
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        items, _, _ = search_collection(store, db, sort="value_asc")
        assert items[-1].card_name == "Obscure Card"

    def test_unknown_sort_falls_back_to_name(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        items, _, _ = search_collection(store, db, sort="'; DROP TABLE x;--")
        assert len(items) == 1

    def test_filter_by_set_and_rarity(self, env):
        store, db = env
        store.add_copies(CHEAP, "Sol Ring", quantity=1)
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        _, total, _ = search_collection(store, db, set_code="sld")
        assert total == 1
        _, total, _ = search_collection(store, db, rarity="mythic")
        assert total == 1

    def test_page_totals(self, env):
        store, db = env
        store.add_copies(DEAR, "Sol Ring", quantity=2)
        _, _, totals = search_collection(store, db)
        assert totals["value_usd"] == pytest.approx(43.44, abs=0.01)
        assert totals["copies"] == 2

    def test_works_with_no_catalogue(self, env):
        """Price search must degrade, not explode, without printings."""
        store, db = env
        conn = db.connect()
        conn.execute("DELETE FROM card_printings")
        conn.commit()
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        items, total, totals = search_collection(store, db)
        assert total == 1
        assert items[0].unit_price_usd is None
        assert totals["value_usd"] == 0.0

    def test_sets_breakdown(self, env):
        store, db = env
        store.add_copies(CHEAP, "Sol Ring", quantity=3)
        store.add_copies(DEAR, "Sol Ring", quantity=1)
        sets = collection_sets(store, db)
        assert sets[0]["set_code"] == "cmm"
        assert sets[0]["copies"] == 3
