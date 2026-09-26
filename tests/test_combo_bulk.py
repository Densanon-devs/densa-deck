"""Combo refresh downloads Commander Spellbook's bulk file, not 1,100 pages.

The paged API rate-limits a full walk about a tenth of the way through the
~110k combos (2026-09), so "Update everything" finished, saved ~11k more,
and immediately offered the same update again. The bulk file is one
download from a host that does not rate-limit; the walk is only a fallback.
"""

from __future__ import annotations

import gzip
import json

import pytest

from densa_deck.combos import data as combo_data
from densa_deck.combos.data import (
    ComboStore,
    _iter_bulk_variants,
    refresh_combo_snapshot,
)


def _variant(i: int, *, status: str = "OK") -> dict:
    return {
        "id": f"{i}-{i + 1}",
        "status": status,
        "uses": [{"card": {"name": f"Card {i}"}}, {"card": {"name": f"Card {i + 1}"}}],
        "requires": [],
        "produces": [{"feature": {"name": "Infinite mana"}}],
        "identity": "G",
        "legalities": {"commander": True},
        # Braces, brackets and commas inside strings must not confuse the reader.
        "description": "Tap {T}: add [G], then, repeat } ] ,",
    }


def _write_bulk(path, variants, *, trailing: bool = True) -> None:
    body = {"timestamp": "2026-09-26T03:10:25", "version": 1, "variants": variants}
    if trailing:
        body["aliases"] = [{"id": "x", "variant": "y"}]
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        # Pretty-printed, so whitespace and newlines sit between objects.
        json.dump(body, fh, indent=2)


@pytest.mark.parametrize("read_chars", [1, 7, 64, 1 << 20])
def test_reader_yields_every_variant_across_read_boundaries(tmp_path, monkeypatch, read_chars):
    monkeypatch.setattr(combo_data, "_BULK_READ_CHARS", read_chars)
    variants = [_variant(i) for i in range(25)]
    path = tmp_path / "bulk.json.gz"
    _write_bulk(path, variants)

    assert list(_iter_bulk_variants(path)) == variants


def test_reader_handles_an_empty_array(tmp_path):
    path = tmp_path / "bulk.json.gz"
    _write_bulk(path, [])
    assert list(_iter_bulk_variants(path)) == []


def test_reader_refuses_a_truncated_file(tmp_path, monkeypatch):
    monkeypatch.setattr(combo_data, "_BULK_READ_CHARS", 16)
    text = json.dumps({"variants": [_variant(1), _variant(2)]})
    path = tmp_path / "bulk.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text[: len(text) // 2])

    with pytest.raises(ValueError):
        list(_iter_bulk_variants(path))


def _fake_download(variants):
    def download(dest, *, user_agent, progress_cb=None):
        _write_bulk(dest, variants)
    return download


def test_bulk_refresh_writes_everything_and_clears_the_incomplete_flag(tmp_path, monkeypatch):
    store = ComboStore(tmp_path / "combos.db")
    # The state that kept re-offering the update: a walk cut off partway.
    store.set_metadata("last_refresh_partial", "1")
    store.set_metadata("last_refresh_next_url", "https://example.test/variants/?offset=33600")

    variants = [_variant(i) for i in range(40)] + [_variant(999, status="NR")]
    monkeypatch.setattr(combo_data, "_download_bulk", _fake_download(variants))

    async def walk_must_not_run(**kwargs):
        raise AssertionError("paged walk used although the bulk file worked")
    monkeypatch.setattr(combo_data, "_walk_variants", walk_must_not_run)

    written = refresh_combo_snapshot(store=store)

    assert written == 40              # the non-OK variant is skipped, as before
    assert store.combo_count() == 40
    assert store.get_metadata("last_refresh_partial") == ""
    assert store.get_metadata("last_refresh_next_url") == ""
    assert store.get_metadata("source") == combo_data.SPELLBOOK_BULK_URL
    # The downloaded file is a cache input, not something to leave lying around.
    assert list(tmp_path.glob("combos-bulk*")) == []


def test_falls_back_to_the_paged_walk_when_the_bulk_file_fails(tmp_path, monkeypatch):
    store = ComboStore(tmp_path / "combos.db")

    def broken_download(dest, *, user_agent, progress_cb=None):
        raise RuntimeError("HTTP 503")
    monkeypatch.setattr(combo_data, "_download_bulk", broken_download)

    walked = []

    async def walk(*, user_agent, progress_cb=None, start_url=None):
        walked.append(start_url)
        return [combo_data._parse_variant(_variant(i)) for i in range(3)]
    monkeypatch.setattr(combo_data, "_walk_variants", walk)

    notes = []
    written = refresh_combo_snapshot(
        store=store, progress_cb=lambda p, c, note=None: notes.append(note))

    assert written == 3
    assert walked == [None]
    assert any(n and "page by page" in n for n in notes)


def test_two_argument_progress_callbacks_still_work(tmp_path, monkeypatch):
    # The CLI's callback takes (pages, seen) only.
    store = ComboStore(tmp_path / "combos.db")

    def download(dest, *, user_agent, progress_cb=None):
        combo_data._report(progress_cb, 10, 0, "Downloading...")
        _write_bulk(dest, [_variant(1)])
    monkeypatch.setattr(combo_data, "_download_bulk", download)

    calls = []
    assert refresh_combo_snapshot(store=store,
                                  progress_cb=lambda p, c: calls.append((p, c))) == 1
    assert calls == [(10, 0)]
