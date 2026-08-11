"""Tests for the opt-in rulings dataset.

Rulings are Wizards' official clarifications, published by Scryfall as a
separate bulk file. Nothing in the simulator or validator uses them, so the
contract is: never downloaded unless asked, always removable, and their
absence changes nothing else.
"""

import pytest

from densa_deck.data.rulings import (
    RULINGS_ATTRIBUTION,
    RulingsStore,
)


@pytest.fixture
def store(tmp_path):
    return RulingsStore(db_path=tmp_path / "rulings.db")


RECORDS = [
    {"oracle_id": "abc", "published_at": "2019-01-01", "source": "wotc",
     "comment": "First ruling."},
    {"oracle_id": "abc", "published_at": "2020-02-02", "source": "wotc",
     "comment": "Second ruling."},
    {"oracle_id": "xyz", "published_at": "2021-03-03", "source": "wotc",
     "comment": "Other card."},
]


class TestOptIn:
    def test_a_fresh_store_is_not_installed(self, store):
        assert store.is_installed() is False
        assert store.ruling_count() == 0

    def test_lookup_on_an_empty_store_returns_nothing(self, store):
        assert store.rulings_for_oracle_id("abc") == []

    def test_storing_records_marks_it_installed(self, store):
        assert store.replace_all(RECORDS) == 3
        assert store.is_installed() is True
        assert store.ruling_count() == 3

    def test_rulings_are_returned_for_the_right_card_oldest_first(self, store):
        store.replace_all(RECORDS)
        rulings = store.rulings_for_oracle_id("abc")
        assert [r["comment"] for r in rulings] == ["First ruling.", "Second ruling."]

    def test_unknown_oracle_id_returns_nothing(self, store):
        store.replace_all(RECORDS)
        assert store.rulings_for_oracle_id("nope") == []

    def test_blank_oracle_id_is_handled(self, store):
        store.replace_all(RECORDS)
        assert store.rulings_for_oracle_id("") == []


class TestOptOut:
    def test_remove_deletes_the_dataset(self, store):
        store.replace_all(RECORDS)
        assert store.remove() is True
        assert store.is_installed() is False

    def test_remove_on_a_missing_dataset_is_not_an_error(self, store):
        assert store.remove() is False

    def test_the_store_is_usable_again_after_removal(self, store):
        store.replace_all(RECORDS)
        store.remove()
        assert store.replace_all(RECORDS) == 3
        assert store.is_installed() is True


class TestIngestHygiene:
    def test_records_without_a_comment_are_skipped(self, store):
        assert store.replace_all([
            {"oracle_id": "abc", "comment": "kept"},
            {"oracle_id": "abc"},                       # no comment
            {"comment": "no oracle id"},                # no oracle_id
        ]) == 1

    def test_replace_all_replaces_rather_than_appends(self, store):
        store.replace_all(RECORDS)
        store.replace_all([{"oracle_id": "abc", "comment": "only one now"}])
        assert store.ruling_count() == 1

    def test_progress_callback_reports_counts(self, store):
        seen = []
        store.replace_all(RECORDS, progress=seen.append)
        assert seen and seen[-1] == 3

    def test_metadata_round_trips(self, store):
        store.set_metadata("scryfall_rulings_updated_at", "2026-08-09")
        assert store.get_metadata("scryfall_rulings_updated_at") == "2026-08-09"

    def test_attribution_names_both_rights_holders(self):
        assert "Wizards of the Coast" in RULINGS_ATTRIBUTION
        assert "Scryfall" in RULINGS_ATTRIBUTION


class TestCardIngestStaysIndependent:
    def test_card_ingest_targets_cards_not_rulings(self):
        from densa_deck.data import scryfall
        assert scryfall.BULK_TYPE == "oracle_cards"

    def test_card_ingest_does_not_pull_rulings(self):
        """The whole point of opt-in: a normal ingest must not fetch them."""
        import inspect

        from densa_deck.data import scryfall
        body = inspect.getsource(scryfall.ingest).lower()
        assert "ruling" not in body, (
            "card ingest must not fetch rulings — it would stop being opt-in"
        )

    def test_bulk_type_targets_the_rulings_file(self):
        from densa_deck.data.rulings import BULK_TYPE
        assert BULK_TYPE == "rulings"


class TestDesktopApiSurface:
    def test_status_reports_not_installed_without_a_download(self, tmp_path):
        from densa_deck.app.api import AppApi
        api = AppApi()
        api._rulings_store = RulingsStore(db_path=tmp_path / "rulings.db")
        res = api.get_rulings_status()
        assert res["ok"] is True
        assert res["data"]["installed"] is False
        assert res["data"]["ruling_count"] == 0

    def test_card_rulings_are_empty_when_not_opted_in(self, tmp_path):
        from densa_deck.app.api import AppApi
        api = AppApi()
        api._rulings_store = RulingsStore(db_path=tmp_path / "rulings.db")
        data = api.get_card_rulings("Doubling Season")["data"]
        assert data["installed"] is False
        assert data["rulings"] == []

    def test_remove_is_exposed_and_safe_when_absent(self, tmp_path):
        from densa_deck.app.api import AppApi
        api = AppApi()
        api._rulings_store = RulingsStore(db_path=tmp_path / "rulings.db")
        assert api.rulings_remove()["ok"] is True
