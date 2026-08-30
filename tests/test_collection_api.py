"""AppApi surface for the collection + printings feature."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.app.api import AppApi
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall

CMM = "11111111-1111-1111-1111-111111111111"
SLD = "22222222-2222-2222-2222-222222222222"
ORACLE = "6ad8011d-3471-4369-9d68-b264cc027487"


def _raw(pid, name, set_code, num, *, usd="1.50", foil=None,
         finishes=("nonfoil",), set_name="Test Set", released="2023-01-01"):
    return {
        "id": pid, "oracle_id": ORACLE, "name": name, "set": set_code,
        "set_name": set_name, "collector_number": num, "rarity": "uncommon",
        "lang": "en", "released_at": released, "finishes": list(finishes),
        "frame": "2015", "border_color": "black", "promo_types": [],
        "games": ["paper"], "tcgplayer_id": 1,
        "prices": {"usd": usd, "usd_foil": foil, "usd_etched": None},
    }


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


@pytest.fixture
def api_with_printings(temp_dbs):
    card_db, version_db = temp_dbs
    db = CardDatabase(db_path=card_db)
    db.upsert_printings([
        printing_row_from_scryfall(
            _raw(CMM, "Sol Ring", "cmm", "410", usd="1.50",
                 finishes=("nonfoil", "foil"), foil="4.00"), "t"),
        printing_row_from_scryfall(
            _raw(SLD, "Sol Ring", "sld", "99", usd="21.72",
                 finishes=("nonfoil", "foil"), foil="35.00",
                 set_name="Secret Lair Drop"), "t"),
    ])
    db.close()
    a = AppApi(db_path=card_db, version_db_path=version_db)
    yield a
    a.close()


def _data(envelope):
    """Unwrap the @_safe envelope the way the frontend's callApi does."""
    assert envelope["ok"] is True, envelope.get("error")
    return envelope.get("data", envelope)


class TestCollectionStatus:
    def test_status_on_a_fresh_install(self, api):
        d = _data(api.get_collection_status())
        assert d["collection"]["total_cards"] == 0
        assert d["printings"]["ready"] is False
        assert d["printings"]["printing_count"] == 0

    def test_status_reports_catalogue_once_present(self, api_with_printings):
        d = _data(api_with_printings.get_collection_status())
        assert d["printings"]["ready"] is True
        assert d["printings"]["printing_count"] == 2

    def test_progress_pollable_before_any_download(self, api):
        # The Collection view polls on open; this must not KeyError.
        d = _data(api.printings_download_progress())
        assert d["running"] is False and d["done"] is True

    def test_store_is_scoped_to_the_temp_db(self, api, temp_dbs):
        card_db, _ = temp_dbs
        api.add_to_collection(CMM, "Sol Ring", 1)
        assert (card_db.parent / "collection.db").exists()


class TestAddAndList:
    def test_add_then_list(self, api):
        _data(api.add_to_collection(CMM, "Sol Ring", 2))
        d = _data(api.list_collection())
        assert d["total"] == 1
        assert d["items"][0]["quantity"] == 2
        assert d["items"][0]["card_name"] == "Sol Ring"

    def test_negative_quantity_removes(self, api):
        _data(api.add_to_collection(CMM, "Sol Ring", 3))
        r = _data(api.add_to_collection(CMM, "Sol Ring", -2))
        assert r["owned_total"] == 1

    def test_add_infers_identity_from_catalogue(self, api_with_printings):
        # Caller supplies only the printing id; name/oracle come from the
        # catalogue so the stack is self-describing afterwards.
        r = _data(api_with_printings.add_to_collection(CMM, "", 1))
        assert r["card_name"] == "Sol Ring"
        items = _data(api_with_printings.list_collection())["items"]
        assert items[0]["oracle_id"] == ORACLE

    def test_list_enriches_with_set_and_price(self, api_with_printings):
        _data(api_with_printings.add_to_collection(SLD, "Sol Ring", 1))
        item = _data(api_with_printings.list_collection())["items"][0]
        assert item["set_code"] == "sld"
        assert item["set_name"] == "Secret Lair Drop"
        assert item["unit_price_usd"] == 21.72
        assert item["known_printing"] is True

    def test_foil_is_priced_as_foil(self, api_with_printings):
        """The specific inaccuracy printing-level pricing exists to fix."""
        _data(api_with_printings.add_to_collection(CMM, "Sol Ring", 1, finish="foil"))
        item = _data(api_with_printings.list_collection())["items"][0]
        assert item["unit_price_usd"] == 4.00  # not the 1.50 nonfoil price

    def test_condition_discounts_the_stack_value(self, api_with_printings):
        _data(api_with_printings.add_to_collection(SLD, "Sol Ring", 2, condition="MP"))
        item = _data(api_with_printings.list_collection())["items"][0]
        assert item["condition_adjusted_price"] == 15.20  # 21.72 * 0.70
        assert item["stack_value_usd"] == 30.40

    def test_unknown_printing_still_lists(self, api):
        """The collection works before printings are ever downloaded."""
        _data(api.add_to_collection("some-unknown-id", "Mystery Card", 1))
        item = _data(api.list_collection())["items"][0]
        assert item["card_name"] == "Mystery Card"
        assert item["known_printing"] is False
        assert item["unit_price_usd"] is None

    def test_page_value_excludes_unpriced(self, api_with_printings):
        _data(api_with_printings.add_to_collection(SLD, "Sol Ring", 1))
        _data(api_with_printings.add_to_collection("unknown", "Mystery", 1))
        d = _data(api_with_printings.list_collection())
        assert d["page_value_usd"] == 21.72
        assert d["page_unpriced"] == 1

    def test_filter_by_name(self, api):
        _data(api.add_to_collection(CMM, "Sol Ring", 1))
        _data(api.add_to_collection(SLD, "Arcane Signet", 1))
        d = _data(api.list_collection({"name_like": "arcane"}))
        assert d["total"] == 1

    def test_limit_is_capped(self, api):
        d = _data(api.list_collection({"limit": 9999}))
        assert d["limit"] == 300


class TestPrintingsDrilldown:
    def test_lists_printings_with_owned_counts(self, api_with_printings):
        _data(api_with_printings.add_to_collection(SLD, "Sol Ring", 2))
        d = _data(api_with_printings.get_card_printings("Sol Ring"))
        assert d["owned_total"] == 2
        by_set = {p["set_code"]: p for p in d["printings"]}
        assert by_set["sld"]["owned"] == 2
        assert by_set["cmm"]["owned"] == 0

    def test_exposes_available_finishes(self, api_with_printings):
        # Stops the UI offering an etched copy of a printing never made etched.
        d = _data(api_with_printings.get_card_printings("Sol Ring"))
        assert d["printings"][0]["finishes"] == ["nonfoil", "foil"]

    def test_empty_when_catalogue_missing(self, api):
        d = _data(api.get_card_printings("Sol Ring"))
        assert d["printings"] == []
        assert d["catalogue_ready"] is False


class TestMutations:
    def test_set_quantity(self, api):
        r = _data(api.add_to_collection(CMM, "Sol Ring", 1))
        _data(api.set_collection_item_quantity(r["item_id"], 5))
        assert _data(api.list_collection())["items"][0]["quantity"] == 5

    def test_set_quantity_missing_item(self, api):
        r = api.set_collection_item_quantity(4242, 3)
        assert r["ok"] is False and r["error_type"] == "NotFound"

    def test_update_metadata(self, api):
        r = _data(api.add_to_collection(CMM, "Sol Ring", 1))
        _data(api.update_collection_item(r["item_id"], {"location": "Binder 2"}))
        assert _data(api.list_collection())["items"][0]["location"] == "Binder 2"

    def test_update_rejects_empty_patch(self, api):
        r = _data(api.add_to_collection(CMM, "Sol Ring", 1))
        bad = api.update_collection_item(r["item_id"], {"quantity": 99})
        assert bad["ok"] is False

    def test_delete(self, api):
        r = _data(api.add_to_collection(CMM, "Sol Ring", 1))
        assert _data(api.delete_collection_item(r["item_id"]))["deleted"] is True
        assert _data(api.list_collection())["total"] == 0

    def test_events_recorded(self, api):
        _data(api.add_to_collection(CMM, "Sol Ring", 2))
        _data(api.add_to_collection(CMM, "Sol Ring", -1))
        events = _data(api.collection_recent_events())["events"]
        assert [e["delta"] for e in events] == [-1, 2]


class TestOwnership:
    def test_batch_ownership_lookup(self, api):
        _data(api.add_to_collection(CMM, "Sol Ring", 3))
        d = _data(api.get_card_ownership(["Sol Ring", "Phyrexian Crusader"]))
        assert d["ownership"]["sol ring"]["owned"] == 3
        assert d["ownership"]["phyrexian crusader"]["owned"] == 0

    def test_deck_ownership_splits_owned_and_missing(self, api):
        _data(api.add_to_collection(CMM, "Sol Ring", 1))
        d = _data(api.get_deck_ownership("1 Sol Ring\n1 Phyrexian Crusader"))
        assert d["owned_distinct"] == 1
        assert d["missing_distinct"] == 1
        assert d["missing_copies"] == 1

    def test_deck_ownership_counts_commitment_from_saved_decks(self, api):
        _data(api.add_to_collection(CMM, "Sol Ring", 1))
        # Write the competing deck straight to the version store — saving via
        # the API would require an ingested card DB, which this feature
        # deliberately does not depend on.
        api._get_vstore().save_version("other-deck", "Other", "commander",
                                       {"Sol Ring": 1}, {"mainboard": ["Sol Ring"]})
        d = _data(api.get_deck_ownership("1 Sol Ring", deck_id="mine"))
        row = d["cards"][0]
        # Owned but sleeved elsewhere: unsleeve, don't buy.
        assert row["missing"] == 0
        assert row["blocked"] == 1

    def test_deck_does_not_block_itself(self, api):
        _data(api.add_to_collection(CMM, "Sol Ring", 1))
        api._get_vstore().save_version("mine", "Mine", "commander",
                                       {"Sol Ring": 1}, {"mainboard": ["Sol Ring"]})
        d = _data(api.get_deck_ownership("1 Sol Ring", deck_id="mine"))
        assert d["cards"][0]["blocked"] == 0

    def test_empty_decklist_errors_cleanly(self, api):
        r = api.get_deck_ownership("")
        assert r["ok"] is False

    def test_works_without_an_ingested_card_database(self, api):
        """Ownership must not require the 250 MB oracle ingest.

        It reads card_name and quantity; resolving cards would add nothing
        and would block the whole Collection feature on a fresh install.
        """
        _data(api.add_to_collection(CMM, "Sol Ring", 2))
        assert api._get_db().card_count() == 0
        d = _data(api.get_deck_ownership("2 Sol Ring\n1 Black Lotus"))
        assert d["owned_distinct"] == 1
        assert d["missing_copies"] == 1


class TestPrintingsRemoval:
    def test_remove_clears_catalogue_but_keeps_collection(self, api_with_printings):
        """Opting out of printings must never touch owned cardboard."""
        _data(api_with_printings.add_to_collection(SLD, "Sol Ring", 2))
        removed = _data(api_with_printings.printings_remove())["removed"]
        assert removed == 2

        status = _data(api_with_printings.get_collection_status())
        assert status["printings"]["ready"] is False
        assert status["collection"]["total_cards"] == 2

        item = _data(api_with_printings.list_collection())["items"][0]
        assert item["card_name"] == "Sol Ring"
        assert item["quantity"] == 2
        assert item["known_printing"] is False  # set/price detail goes dark


class TestScannerApi:
    def test_capabilities_reported_honestly(self, api):
        d = _data(api.get_scan_capabilities())
        assert "ocr_backends" in d and "camera" in d
        # Manual entry always works, so the feature is never fully blocked.
        assert d["manual_always_available"] is True
        assert d["catalogue_ready"] is False

    def test_identify_requires_the_catalogue(self, api):
        r = api.scan_identify("Sol Ring")
        assert r["ok"] is False
        assert r["error_type"] == "PrintingsRequired"

    def test_identify_exact_from_footer_and_name(self, api_with_printings):
        """A footer plus the card's own name is what auto-adds now.

        The footer alone used to be enough. One misread digit lands on a
        different real printing, so the key has to be corroborated \u2014 the whole
        argument is in test_collection_scanner.py.
        """
        d = _data(api_with_printings.scan_identify(
            "Sol Ring\n0410/0500 U\nCMM \u2022 EN"))
        assert d["confidence"] == "exact"
        assert d["auto_addable"] is True
        assert d["candidates"][0]["printing_id"] == CMM

    def test_identify_footer_alone_offers_without_filing(self, api_with_printings):
        d = _data(api_with_printings.scan_identify("0410/0500 U\nCMM \u2022 EN"))
        assert d["auto_addable"] is False
        assert d["candidates"][0]["printing_id"] == CMM

    def test_identify_ambiguous_by_name(self, api_with_printings):
        d = _data(api_with_printings.scan_identify("Sol Ring\nArtifact"))
        assert d["confidence"] == "ambiguous"
        assert d["auto_addable"] is False
        assert len(d["candidates"]) == 2

    def test_candidates_expose_available_finishes(self, api_with_printings):
        d = _data(api_with_printings.scan_identify("0410/0500 U\nCMM \u2022 EN"))
        assert d["candidates"][0]["finishes"] == ["nonfoil", "foil"]

    def test_commit_adds_and_tracks_session(self, api_with_printings):
        d = _data(api_with_printings.scan_commit(CMM, "Sol Ring"))
        assert d["session"]["scanned"] == 1
        assert d["session"]["added"] == 1
        assert d["session"]["value_usd"] == 1.50
        assert _data(api_with_printings.list_collection())["total"] == 1

    def test_commit_foil_uses_foil_price(self, api_with_printings):
        d = _data(api_with_printings.scan_commit(CMM, "Sol Ring", finish="foil"))
        assert d["session"]["value_usd"] == 4.00

    def test_commit_unknown_printing_refused(self, api_with_printings):
        r = api_with_printings.scan_commit("nope", "Mystery")
        assert r["ok"] is False and r["error_type"] == "NotFound"

    def test_skip_keeps_the_count_honest(self, api_with_printings):
        d = _data(api_with_printings.scan_skip())
        assert d["session"]["scanned"] == 1
        assert d["session"]["added"] == 0
        assert d["session"]["skipped"] == 1

    def test_session_accumulates_then_resets(self, api_with_printings):
        api_with_printings.scan_commit(CMM, "Sol Ring")
        api_with_printings.scan_commit(SLD, "Sol Ring")
        assert _data(api_with_printings.get_scan_session())["session"]["added"] == 2
        d = _data(api_with_printings.reset_scan_session())
        assert d["session"]["added"] == 0
        # Resetting the tally must not remove the cards already banked.
        assert _data(api_with_printings.list_collection())["total"] == 2


class TestResellerApi:
    def test_fee_model_round_trips_through_prefs(self, api, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        _data(api.set_fee_model({"marketplace_pct": 0.05}))
        assert _data(api.get_fee_model())["fees"]["marketplace_pct"] == 0.05

    def test_acquisition_lifecycle(self, api_with_printings):
        a = _data(api_with_printings.create_acquisition("Mike's box", 100.0))
        aid = a["acquisition_id"]
        assert _data(api_with_printings.list_acquisitions())["acquisitions"][0]["name"] \
            == "Mike's box"

        api_with_printings._get_collection_store().add_copies(
            SLD, "Sol Ring", quantity=1, acquisition_id=aid)
        alloc = _data(api_with_printings.allocate_acquisition_basis(aid))
        assert alloc["allocated_usd"] == pytest.approx(100.0, abs=0.01)

        s = _data(api_with_printings.get_acquisition_summary(aid))
        assert s["purchase_price_usd"] == 100.0
        assert s["net_proceeds_usd"] < s["market_value_usd"]  # fees exist

    def test_sale_records_and_removes(self, api_with_printings):
        _data(api_with_printings.add_to_collection(SLD, "Sol Ring", 1))
        r = _data(api_with_printings.record_sale(SLD, "Sol Ring", 25.0,
                                                 fees_usd=2.5, shipping_usd=1.0))
        assert r["net_usd"] == 21.5
        assert _data(api_with_printings.list_collection())["total"] == 0
        assert len(_data(api_with_printings.list_sales())["sales"]) == 1

    def test_dashboard_shape(self, api_with_printings):
        d = _data(api_with_printings.get_reseller_dashboard())
        for key in ("capital_invested_usd", "realized_profit_usd",
                    "inventory_market_value_usd", "sales_missing_basis"):
            assert key in d

    def test_appraisal_has_bands_and_caveats_but_no_verdict(self, api_with_printings):
        _data(api_with_printings.add_to_collection(SLD, "Sol Ring", 4))
        a = _data(api_with_printings.appraise_collection())
        assert a["target_prices"]["conservative_usd"] < a["target_prices"]["aggressive_usd"]
        assert a["caveats"]
        for banned in ("verdict", "recommendation", "should_buy"):
            assert banned not in a

    def test_appraisal_reports_price_age(self, api_with_printings):
        _data(api_with_printings.add_to_collection(SLD, "Sol Ring", 1))
        a = _data(api_with_printings.appraise_collection())
        assert "price_age_hours" in a and "confidence" in a

    def test_scan_session_appraisal(self, api_with_printings):
        api_with_printings.scan_commit(SLD, "Sol Ring")
        a = _data(api_with_printings.appraise_scan_session())
        assert a["session"]["added"] == 1
        assert a["market_value_usd"] == 21.72


class TestAllocationApi:
    def test_allocate_and_read_back(self, api_with_printings):
        r = _data(api_with_printings.add_to_collection(SLD, "Sol Ring", 1, finish="foil"))
        _data(api_with_printings.allocate_copy("edh", r["item_id"]))
        d = _data(api_with_printings.get_deck_allocations("edh"))
        assert len(d["allocations"]) == 1
        assert d["allocations"][0]["finish"] == "foil"
        assert d["allocated_value_usd"] == 35.00   # foil price

    def test_over_allocation_refused_with_a_usable_message(self, api_with_printings):
        r = _data(api_with_printings.add_to_collection(CMM, "Sol Ring", 1))
        _data(api_with_printings.allocate_copy("deck-a", r["item_id"]))
        bad = api_with_printings.allocate_copy("deck-b", r["item_id"])
        assert bad["ok"] is False
        assert bad["error_type"] == "AllocationRefused"
        assert "free a copy" in bad["error"]

    def test_deallocate_frees_it(self, api_with_printings):
        r = _data(api_with_printings.add_to_collection(CMM, "Sol Ring", 1))
        _data(api_with_printings.allocate_copy("deck-a", r["item_id"]))
        _data(api_with_printings.deallocate_copy("deck-a", r["item_id"]))
        _data(api_with_printings.allocate_copy("deck-b", r["item_id"]))

    def test_deleting_a_deck_frees_its_copies(self, api_with_printings):
        r = _data(api_with_printings.add_to_collection(CMM, "Sol Ring", 1))
        _data(api_with_printings.allocate_copy("doomed", r["item_id"]))
        d = _data(api_with_printings.delete_deck("doomed"))
        assert d["copies_freed"] == 1
        # The copy is available again.
        _data(api_with_printings.allocate_copy("other", r["item_id"]))

    def test_reconcile_reports_what_it_changed(self, api_with_printings):
        r = _data(api_with_printings.add_to_collection(CMM, "Sol Ring", 1))
        _data(api_with_printings.allocate_copy("deck-a", r["item_id"]))
        _data(api_with_printings.delete_collection_item(r["item_id"]))
        d = _data(api_with_printings.reconcile_allocations())
        assert d["removed"] == 1


class TestContentUpkeep:
    """The app flags what's missing itself; the user gets one button."""

    def test_fresh_install_flags_required_items(self, api):
        d = _data(api.get_content_status())
        keys = {i["key"] for i in d["items"]}
        # No cards and no printings on a bare install.
        assert "cards" in keys and "printings" in keys
        assert d["required"] >= 1
        assert d["total_mb"] > 0

    def test_each_item_explains_itself(self, api):
        d = _data(api.get_content_status())
        for item in d["items"]:
            assert item["label"] and item["detail"]
            assert item["severity"] in ("required", "update")

    def test_printings_present_clears_that_item(self, api_with_printings):
        d = _data(api_with_printings.get_content_status())
        printing_items = [i for i in d["items"] if i["key"] == "printings"]
        # Printings exist; only a staleness update may remain, never "required".
        assert all(i["severity"] != "required" for i in printing_items)

    def test_partial_combo_refresh_is_flagged_for_resume(self, api):
        store = api._get_combo_store()
        from densa_deck.combos.models import Combo
        store.upsert_combos([Combo(combo_id="c1", cards=["Sol Ring"],
                                   color_identity="U")])
        store.set_metadata("last_refresh_partial", "1")
        d = _data(api.get_content_status())
        combo = [i for i in d["items"] if i["key"] == "combos"]
        assert combo and "Incomplete" in combo[0]["detail"]

    def test_status_is_read_only(self, api):
        """Asking must never start a download."""
        before = api._get_db().card_count()
        _data(api.get_content_status())
        assert api._get_db().card_count() == before
        p = _data(api.update_all_content_progress())
        assert p["running"] is False

    def test_progress_pollable_before_any_update(self, api):
        d = _data(api.update_all_content_progress())
        assert d["done"] is True and d["running"] is False

    def test_double_start_refused(self, api, monkeypatch):
        # Freeze the worker so the second call sees it running.
        import threading
        gate = threading.Event()
        monkeypatch.setattr(api, "_do_content_update", lambda: gate.wait(5))
        first = _data(api.update_all_content_start())
        assert first["started"] is True
        second = api.update_all_content_start()
        assert second["ok"] is False
        gate.set()


class TestSingleBannerSurface:
    """One banner, one button.

    Three banners stacked on launch — each with its own "Later" — because the
    consolidated one was added without retiring the card-database and
    combo-staleness banners it supersedes. These lock the replacement's
    coverage so retiring them stays safe.
    """

    def test_card_db_update_is_reported(self, api, monkeypatch):
        # Needs an installed card DB — with zero cards the item is "required"
        # and the update branch never runs.
        from densa_deck.models import Card, CardLayout
        api._get_db().upsert_cards([
            Card(scryfall_id="s1", oracle_id="o1", name="Sol Ring",
                 layout=CardLayout.NORMAL, type_line="Artifact")])
        monkeypatch.setattr(api, "check_card_db_update",
                            lambda: {"ok": True, "data": {"available": True,
                                                          "size_mb": 24}})
        d = _data(api.get_content_status())
        cards = [i for i in d["items"] if i["key"] == "cards"]
        assert cards and cards[0]["severity"] == "update"

    def test_missing_card_db_is_required_not_optional(self, api):
        d = _data(api.get_content_status())
        cards = [i for i in d["items"] if i["key"] == "cards"]
        assert cards and cards[0]["severity"] == "required"

    def test_stale_combo_data_is_reported(self, api):
        from densa_deck.combos.models import Combo
        store = api._get_combo_store()
        store.upsert_combos([Combo(combo_id="c1", cards=["Sol Ring"],
                                   color_identity="U")])
        store.set_metadata("last_refresh_partial", "1")
        d = _data(api.get_content_status())
        assert any(i["key"] == "combos" for i in d["items"])

    def test_unreachable_update_check_does_not_hide_the_rest(self, api, monkeypatch):
        """One dead service must not blank the whole banner."""
        def _boom():
            raise RuntimeError("network down")
        monkeypatch.setattr(api, "check_card_db_update", _boom)
        d = _data(api.get_content_status())
        # Printings are still missing on this fixture and must still surface.
        assert any(i["key"] == "printings" for i in d["items"])

    def test_size_total_is_the_sum(self, api):
        d = _data(api.get_content_status())
        assert d["total_mb"] == sum(i.get("size_mb") or 0 for i in d["items"])


class TestScanningIntoSeveralLists:
    """One pass over a box, several answers at once.

    These are mine, these are for the Modern deck, these are going in the
    sale binder. Scan time is the only cheap moment to say so — afterwards
    the cards are back in the box and the knowledge is gone.
    """

    def _lists(self, api, *names):
        store = api._get_collection_store()
        return [store.create_collection(n)["collection_id"] for n in names]

    def _only_stack(self, api):
        items = _data(api.list_collection())["items"]
        assert len(items) == 1, f"expected one stack, got {len(items)}"
        return items[0]

    def _names_for(self, api, item_id):
        store = api._get_collection_store()
        return {c["name"] for c in store.collections_for_item(int(item_id))}

    def test_a_card_scanned_into_three_lists_is_still_one_card(self, api_with_printings):
        """The whole hazard. A stack is keyed by the list it lives in, so
        filing it three times would mint three stacks and claim you own
        three of a card you scanned once."""
        api = api_with_printings
        modern, sale = self._lists(api, "Modern binder", "For sale")

        _data(api.scan_commit(CMM, "Sol Ring", also_collection_ids=[modern, sale]))

        stack = self._only_stack(api)
        assert stack["quantity"] == 1, stack

    def test_and_it_is_in_all_of_them(self, api_with_printings):
        api = api_with_printings
        modern, sale = self._lists(api, "Modern binder", "For sale")
        _data(api.scan_commit(CMM, "Sol Ring", also_collection_ids=[modern, sale]))

        names = self._names_for(api, self._only_stack(api)["item_id"])
        assert {"Modern binder", "For sale"} <= names, names

    def test_the_reply_says_which_lists_it_went_into(self, api_with_printings):
        """A scanner that files silently is one you have to go and check."""
        api = api_with_printings
        modern, sale = self._lists(api, "Modern binder", "For sale")
        d = _data(api.scan_commit(CMM, "Sol Ring",
                                  also_collection_ids=[modern, sale]))
        assert len(d["tagged_into"]) == 2, d.get("tagged_into")

    def test_naming_the_home_list_again_does_not_double_tag(self, api_with_printings):
        """Two controls can reasonably tick the same box."""
        api = api_with_printings
        store = api._get_collection_store()
        home = store.default_collection_id()
        modern, = self._lists(api, "Modern binder")

        d = _data(api.scan_commit(CMM, "Sol Ring",
                                  also_collection_ids=[home, modern]))
        assert len(d["tagged_into"]) == 1, d.get("tagged_into")
        assert self._only_stack(api)["quantity"] == 1

    def test_a_list_that_no_longer_exists_does_not_fail_the_scan(self, api_with_printings):
        """The card is in your hand. Losing it over a stale checkbox would
        be the worst possible trade."""
        api = api_with_printings
        d = _data(api.scan_commit(CMM, "Sol Ring",
                                  also_collection_ids=[9999, "", None]))
        assert self._only_stack(api)["quantity"] == 1
        assert d["tagged_into"] == []

    def test_scanning_none_behaves_exactly_as_before(self, api_with_printings):
        api = api_with_printings
        d = _data(api.scan_commit(CMM, "Sol Ring"))
        assert self._only_stack(api)["quantity"] == 1
        assert d["tagged_into"] == []

    def test_a_second_copy_lands_in_the_same_lists(self, api_with_printings):
        '''"Four of these" must not split a playset across groups by an
        accident of which button was pressed.'''
        api = api_with_printings
        modern, = self._lists(api, "Modern binder")

        _data(api.scan_commit(CMM, "Sol Ring", also_collection_ids=[modern]))
        _data(api.scan_adjust(CMM, 1, card_name="Sol Ring",
                              also_collection_ids=[modern]))

        stack = self._only_stack(api)
        assert stack["quantity"] == 2, stack
        assert "Modern binder" in self._names_for(api, stack["item_id"])

    def test_the_phone_is_told_about_the_tags(self, api_with_printings):
        """A tag that never syncs is a list that only exists on one device."""
        from densa_deck.sync.log import KIND_MEMBERSHIP
        api = api_with_printings
        modern, = self._lists(api, "Modern binder")
        _data(api.scan_commit(CMM, "Sol Ring", also_collection_ids=[modern]))

        events, _ = api._get_sync().log.since(0)
        kinds = [e.kind for e in events]
        assert KIND_MEMBERSHIP in kinds, kinds
