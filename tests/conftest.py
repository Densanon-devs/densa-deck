"""Shared test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_combo_bulk_download(monkeypatch):
    """Keep combo refreshes off the network unless a test says otherwise.

    `refresh_combo_snapshot` tries Commander Spellbook's 28 MB bulk file
    before the paged walk. Tests that mock only the walk would otherwise
    download it for real -- and pass or fail on the state of the internet.
    Failing here sends them down the walk they meant to test; tests of the
    bulk path replace `_download_bulk` themselves.
    """
    from densa_deck.combos import data as combo_data

    def offline(dest, *, user_agent, progress_cb=None):
        raise RuntimeError("bulk combo download disabled in tests")

    monkeypatch.setattr(combo_data, "_download_bulk", offline)
