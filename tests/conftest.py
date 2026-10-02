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
    # tiers._CONFIG_PATH is computed from the home directory at IMPORT, so the
    # env redirect above never reaches it -- and set_user_preferences writes
    # through it. A test that switched update checks off turned them off in
    # the developer's real config.json. Point it at this test's home.
    import densa_deck.tiers as tiers
    monkeypatch.setattr(tiers, "_CONFIG_PATH", home / ".densa-deck" / "config.json")


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


@pytest.fixture(autouse=True)
def _keep_tests_off_the_real_densanon_hub(tmp_path_factory, monkeypatch):
    """Give every test its own Densanon hub home and port.

    The phone bridge joins the shared hub when it starts, and the analyst's
    model goes through the shared model service. Both read
    `DENSANON_HUB_HOME` and `DENSANON_HUB_PORT` when used, so without this a
    test would register routes on, or take over, the real hub on 8770 and
    write its secret, certificate and model catalogue into the developer's
    real `~/.densanon`. The home redirect above already moves `~`, but the
    port is the part a running Densanon app would actually see.
    """
    import socket

    monkeypatch.setenv("DENSANON_HUB_HOME", str(tmp_path_factory.mktemp("densanon-hub")))
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    finally:
        probe.close()
    monkeypatch.setenv("DENSANON_HUB_PORT", str(port))
    yield
    # A test that reached the analyst may have started this process's model
    # provider, which is a process-wide singleton with a lease thread. Stop
    # it so it cannot outlive the test, or carry one test's fake loader into
    # the next.
    import sys
    shared_models = sys.modules.get("densa_deck._vendor.densanon_hub.models")
    if shared_models is not None:
        shared_models.stop_provider()
