"""Shared test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _keep_tests_out_of_the_real_home(tmp_path_factory, monkeypatch):
    """Point the home directory at a temp dir for every test.

    Anything that resolves `~/.densa-deck/...` at call time -- coach
    sessions, app state, drafts, config -- otherwise lands in the user's
    real data. It did: over 1,000 test coach sessions had piled up in one
    user's Coach list. Paths frozen at import time (module constants) are
    not covered by this and must take an explicit path in tests.
    """
    home = tmp_path_factory.mktemp("home")
    for var in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(var, str(home))


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
