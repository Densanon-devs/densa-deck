"""Phases 5 & 6 — cost basis, sales, P&L, and buy-side estimates."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.collection.reseller import (
    DEFAULT_FEES,
    FeeModel,
    acquisition_summary,
    allocate_cost_basis,
    analyze_acquisition,
    collection_resale_lines,
    create_acquisition,
    estimate_resale,
    list_acquisitions,
    list_sales,
    record_sale,
    reseller_dashboard,
)
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall

DEAR = "dear-1"
MID = "mid-1"
BULK = "bulk-1"
NOPRICE = "nop-1"


def _raw(pid, name, set_code, num, *, usd=None, oracle="o"):
    return {
        "id": pid, "oracle_id": oracle, "name": name, "set": set_code,
        "set_name": set_code.upper(), "collector_number": num, "rarity": "rare",
        "lang": "en", "released_at": "2020-01-01", "finishes": ["nonfoil"],
        "frame": "2015", "border_color": "black", "promo_types": [],
        "games": ["paper"], "tcgplayer_id": 1,
        "prices": {"usd": usd, "usd_foil": None, "usd_etched": None},
    }


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_printings([
            printing_row_from_scryfall(_raw(DEAR, "Dear Card", "aaa", "1",
                                            usd="100.00", oracle="o1"), "t"),
            printing_row_from_scryfall(_raw(MID, "Mid Card", "bbb", "2",
                                            usd="10.00", oracle="o2"), "t"),
            printing_row_from_scryfall(_raw(BULK, "Bulk Card", "ccc", "3",
                                            usd="0.10", oracle="o3"), "t"),
            printing_row_from_scryfall(_raw(NOPRICE, "No Price", "ddd", "4",
                                            usd=None, oracle="o4"), "t"),
        ])
        store = CollectionStore(db_path=root / "collection.db")
        yield store, db
        db.close()


class TestFeeModel:
    def test_defaults_round_trip(self):
        assert FeeModel.from_dict(DEFAULT_FEES.as_dict()) == DEFAULT_FEES

    def test_partial_override_keeps_other_defaults(self):
        f = FeeModel.from_dict({"marketplace_pct": 0.05})
        assert f.marketplace_pct == 0.05
        assert f.payment_pct == DEFAULT_FEES.payment_pct

    def test_none_gives_defaults(self):
        assert FeeModel.from_dict(None) == DEFAULT_FEES

    def test_junk_keys_ignored(self):
        f = FeeModel.from_dict({"nonsense": 1, "marketplace_pct": 0.2})
        assert f.marketplace_pct == 0.2


class TestResaleEstimate:
    def test_market_value_is_not_proceeds(self):
        """The headline point: fees come off before you see money."""
        est = estimate_resale([(100.00, 1)])
        assert est.market_value_usd == 100.00
        assert est.net_proceeds_usd < 100.00

    def test_unpriced_contributes_nothing_and_is_reported(self):
        est = estimate_resale([(None, 5), (10.00, 1)])
        assert est.unpriced_cards == 5
        assert est.market_value_usd == 10.00

    def test_bulk_cards_are_not_valued_at_market(self):
        """1,000 ten-cent commons are not $100 of sellable inventory.

        Treating them as individually sellable is how paper valuations of
        large collections end up several times optimistic.
        """
        est = estimate_resale([(0.10, 1000)])
        assert est.market_value_usd == 100.00
        assert est.bulk_cards == 1000
        assert est.sellable_cards == 0
        assert est.net_proceeds_usd == pytest.approx(20.00, abs=0.01)

    def test_bulk_threshold_is_configurable(self):
        fees = FeeModel(bulk_threshold_usd=0.05)
        est = estimate_resale([(0.10, 10)], fees)
        assert est.sellable_cards == 10

    def test_shipping_scales_with_orders_not_cards(self):
        few = estimate_resale([(10.0, 12)])
        many = estimate_resale([(10.0, 120)])
        assert many.shipping_usd == pytest.approx(few.shipping_usd * 10, abs=0.01)

    def test_zero_and_negative_quantities_ignored(self):
        est = estimate_resale([(10.0, 0), (10.0, -3)])
        assert est.market_value_usd == 0.0

    def test_empty_input(self):
        est = estimate_resale([])
        assert est.net_proceeds_usd == 0.0

    def test_proceeds_never_negative(self):
        # A tiny sellable value swamped by flat fees must floor at zero,
        # not report a negative pile of cardboard.
        est = estimate_resale([(0.60, 1)])
        assert est.net_proceeds_usd >= 0.0


class TestAcquisitions:
    def test_create_and_list(self, env):
        store, _ = env
        create_acquisition(store, "Mike's collection", 600.0, source="Facebook")
        rows = list_acquisitions(store)
        assert len(rows) == 1
        assert rows[0]["name"] == "Mike's collection"
        assert rows[0]["purchase_price_usd"] == 600.0

    def test_name_required(self, env):
        store, _ = env
        with pytest.raises(ValueError):
            create_acquisition(store, "   ", 100.0)

    def test_card_count_tracks_attached_cards(self, env):
        store, _ = env
        acq = create_acquisition(store, "Lot", 100.0)
        store.add_copies(DEAR, "Dear Card", quantity=3,
                         acquisition_id=acq["acquisition_id"])
        assert list_acquisitions(store)[0]["cards"] == 3


class TestCostBasis:
    def test_allocated_proportionally_to_market_value(self, env):
        store, db = env
        acq = create_acquisition(store, "Lot", 110.0)
        aid = acq["acquisition_id"]
        store.add_copies(DEAR, "Dear Card", quantity=1, acquisition_id=aid)  # $100
        store.add_copies(MID, "Mid Card", quantity=1, acquisition_id=aid)    # $10

        result = allocate_cost_basis(store, db, aid)
        assert result["market_value_usd"] == 110.00
        assert result["allocated_usd"] == pytest.approx(110.00, abs=0.01)

        items, _ = store.list_items()
        by_name = {i.card_name: i for i in items}
        # $100/$110 of the lot -> $100 of the $110 paid.
        assert by_name["Dear Card"].unit_cost_usd == pytest.approx(100.0, abs=0.01)
        assert by_name["Mid Card"].unit_cost_usd == pytest.approx(10.0, abs=0.01)

    def test_quantity_is_respected(self, env):
        store, db = env
        acq = create_acquisition(store, "Lot", 100.0)
        aid = acq["acquisition_id"]
        store.add_copies(MID, "Mid Card", quantity=10, acquisition_id=aid)
        allocate_cost_basis(store, db, aid)
        items, _ = store.list_items()
        assert items[0].unit_cost_usd == pytest.approx(10.0, abs=0.01)

    def test_unpriced_cards_reported_not_allocated(self, env):
        store, db = env
        acq = create_acquisition(store, "Lot", 100.0)
        aid = acq["acquisition_id"]
        store.add_copies(MID, "Mid Card", quantity=1, acquisition_id=aid)
        store.add_copies(NOPRICE, "No Price", quantity=1, acquisition_id=aid)
        result = allocate_cost_basis(store, db, aid)
        assert result["unpriced_stacks"] == 1
        assert result["priced_stacks"] == 1

    def test_all_unpriced_does_not_crash(self, env):
        store, db = env
        acq = create_acquisition(store, "Lot", 100.0)
        aid = acq["acquisition_id"]
        store.add_copies(NOPRICE, "No Price", quantity=1, acquisition_id=aid)
        result = allocate_cost_basis(store, db, aid)
        assert result["allocated_usd"] == 0.0

    def test_basis_is_stamped_and_frozen(self, env):
        """Basis must not drift with the market after it's set."""
        store, db = env
        acq = create_acquisition(store, "Lot", 100.0)
        aid = acq["acquisition_id"]
        store.add_copies(MID, "Mid Card", quantity=1, acquisition_id=aid)
        allocate_cost_basis(store, db, aid)
        before = store.list_items()[0][0].unit_cost_usd

        conn = db.connect()
        conn.execute("UPDATE card_printings SET price_usd = 999 WHERE printing_id = ?",
                     (MID,))
        conn.commit()
        # No re-allocation call -> basis unchanged.
        assert store.list_items()[0][0].unit_cost_usd == before
        assert list_acquisitions(store)[0]["basis_allocated_at"]

    def test_unknown_acquisition_raises(self, env):
        store, db = env
        with pytest.raises(ValueError):
            allocate_cost_basis(store, db, 999)


class TestAcquisitionSummary:
    def test_spread_is_labelled_estimated(self, env):
        store, db = env
        acq = create_acquisition(store, "Lot", 50.0)
        aid = acq["acquisition_id"]
        store.add_copies(DEAR, "Dear Card", quantity=1, acquisition_id=aid)
        s = acquisition_summary(store, db, aid)
        assert s["purchase_price_usd"] == 50.0
        assert "estimated_profit_usd" in s
        # Net is below market because fees exist.
        assert s["net_proceeds_usd"] < s["market_value_usd"]

    def test_roi_none_when_free(self, env):
        store, db = env
        acq = create_acquisition(store, "Gift", 0.0)
        s = acquisition_summary(store, db, acq["acquisition_id"])
        assert s["estimated_roi_pct"] is None

    def test_only_counts_its_own_cards(self, env):
        store, db = env
        a = create_acquisition(store, "A", 10.0)["acquisition_id"]
        b = create_acquisition(store, "B", 10.0)["acquisition_id"]
        store.add_copies(DEAR, "Dear Card", quantity=1, acquisition_id=a)
        store.add_copies(MID, "Mid Card", quantity=1, acquisition_id=b)
        assert acquisition_summary(store, db, a)["market_value_usd"] == 100.0
        assert acquisition_summary(store, db, b)["market_value_usd"] == 10.0


class TestSales:
    def test_sale_nets_out_fees_and_removes_the_card(self, env):
        store, _ = env
        store.add_copies(MID, "Mid Card", quantity=2)
        r = record_sale(store, printing_id=MID, card_name="Mid Card",
                        sale_price_usd=12.0, fees_usd=1.5, shipping_usd=0.75,
                        platform="eBay")
        assert r["net_usd"] == 9.75
        assert store.owned_count("Mid Card") == 1

    def test_realized_profit_uses_basis(self, env):
        store, db = env
        acq = create_acquisition(store, "Lot", 10.0)
        aid = acq["acquisition_id"]
        item = store.add_copies(MID, "Mid Card", quantity=1, acquisition_id=aid)
        allocate_cost_basis(store, db, aid)
        r = record_sale(store, printing_id=MID, card_name="Mid Card",
                        sale_price_usd=20.0, fees_usd=2.0, shipping_usd=1.0,
                        item_id=item.item_id)
        assert r["cost_basis_usd"] == pytest.approx(10.0, abs=0.01)
        assert r["realized_profit_usd"] == pytest.approx(7.0, abs=0.01)

    def test_profit_is_none_without_basis(self, env):
        store, _ = env
        store.add_copies(MID, "Mid Card", quantity=1)
        r = record_sale(store, printing_id=MID, card_name="Mid Card",
                        sale_price_usd=20.0)
        assert r["realized_profit_usd"] is None

    def test_can_keep_the_card(self, env):
        store, _ = env
        store.add_copies(MID, "Mid Card", quantity=1)
        record_sale(store, printing_id=MID, card_name="Mid Card",
                    sale_price_usd=5.0, remove_from_collection=False)
        assert store.owned_count("Mid Card") == 1

    def test_sales_list(self, env):
        store, _ = env
        store.add_copies(MID, "Mid Card", quantity=1)
        record_sale(store, printing_id=MID, card_name="Mid Card",
                    sale_price_usd=12.0, fees_usd=2.0)
        rows = list_sales(store)
        assert rows[0]["net_usd"] == 10.0


class TestDashboard:
    def test_capital_sales_and_inventory(self, env):
        store, db = env
        aid = create_acquisition(store, "Lot", 100.0)["acquisition_id"]
        store.add_copies(DEAR, "Dear Card", quantity=1, acquisition_id=aid)
        store.add_copies(MID, "Mid Card", quantity=1, acquisition_id=aid)
        allocate_cost_basis(store, db, aid)
        item = store.list_items()[0][0]
        record_sale(store, printing_id=item.printing_id, card_name=item.card_name,
                    sale_price_usd=120.0, fees_usd=12.0, shipping_usd=1.0,
                    item_id=item.item_id)

        d = reseller_dashboard(store, db)
        assert d["capital_invested_usd"] == 100.0
        assert d["gross_sales_usd"] == 120.0
        assert d["net_sales_usd"] == 107.0
        assert d["sales_count"] == 1
        assert d["inventory_market_value_usd"] > 0

    def test_sales_without_basis_are_flagged(self, env):
        store, db = env
        store.add_copies(MID, "Mid Card", quantity=1)
        record_sale(store, printing_id=MID, card_name="Mid Card", sale_price_usd=10.0)
        d = reseller_dashboard(store, db)
        assert d["sales_missing_basis"] == 1

    def test_roi_none_without_capital(self, env):
        store, db = env
        assert reseller_dashboard(store, db)["roi_pct"] is None

    def test_resale_lines_are_condition_adjusted(self, env):
        store, db = env
        store.add_copies(DEAR, "Dear Card", quantity=1, condition="MP")
        lines = collection_resale_lines(store, db)
        assert lines[0][0] == pytest.approx(70.0, abs=0.01)


class TestAcquisitionAnalyzer:
    def test_target_bands_scale_with_proceeds(self):
        a = analyze_acquisition([(100.0, 10)])
        t = a["target_prices"]
        assert t["conservative_usd"] < t["normal_usd"] < t["aggressive_usd"]
        assert t["aggressive_usd"] <= a["net_proceeds_usd"]

    def test_no_verdict_field_exists(self):
        """Deliberately absent.

        The price feed's own publisher says it isn't fit to power a sales
        system; rendering a green BUY tick over someone's $1,400 decision on
        that basis would be indefensible.
        """
        a = analyze_acquisition([(10.0, 5)])
        for banned in ("verdict", "recommendation", "should_buy", "buy"):
            assert banned not in a

    def test_unpriced_cards_drive_confidence_down(self):
        good = analyze_acquisition([(10.0, 100)])
        bad = analyze_acquisition([(10.0, 50), (None, 50)])
        assert good["confidence"] == "high"
        assert bad["confidence"] == "low"
        assert bad["price_coverage_pct"] == 50.0

    def test_stale_prices_reduce_confidence(self):
        fresh = analyze_acquisition([(10.0, 100)], price_age_hours=1)
        stale = analyze_acquisition([(10.0, 100)], price_age_hours=100)
        assert fresh["confidence"] == "high"
        assert stale["confidence"] == "medium"

    def test_caveats_always_present(self):
        a = analyze_acquisition([(10.0, 5)])
        assert a["caveats"]
        assert any("estimate" in c.lower() for c in a["caveats"])

    def test_unpriced_caveat_names_the_gap(self):
        a = analyze_acquisition([(10.0, 40), (None, 60)])
        assert any("60 card(s) have no price" in c for c in a["caveats"])

    def test_custom_margins(self):
        a = analyze_acquisition([(10.0, 10)], margins=(0.3, 0.4, 0.5))
        assert a["margins"]["conservative"] == 0.3

    def test_empty_pile(self):
        a = analyze_acquisition([])
        assert a["net_proceeds_usd"] == 0.0
        assert a["total_cards"] == 0
        assert a["confidence"] == "low"

    def test_bulk_heavy_pile_is_not_overvalued(self):
        """A shoebox of commons must not read as a goldmine."""
        a = analyze_acquisition([(0.10, 1000), (50.0, 2)])
        assert a["market_value_usd"] == 200.0
        assert a["net_proceeds_usd"] < 130.0
        assert a["bulk_cards"] == 1000
