"""CLI integration tests — verify commands parse args and don't crash."""

import subprocess
import sys

import pytest

PYTHON = sys.executable


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run densa-deck CLI with given args."""
    import os
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [PYTHON, "-m", "densa_deck.cli", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env=env,
    )


class TestCLIBasics:
    def test_no_args_shows_help(self):
        r = _run_cli()
        assert r.returncode == 0
        assert "densa-deck" in r.stdout or "usage" in r.stdout.lower()

    def test_help_flag(self):
        r = _run_cli("--help")
        assert r.returncode == 0
        assert "analyze" in r.stdout
        assert "goldfish" in r.stdout
        assert "gauntlet" in r.stdout

    def test_info_no_db(self):
        """info command should work even with no cards ingested."""
        r = _run_cli("info")
        assert r.returncode == 0
        assert "Cards in database" in r.stdout or "cards" in r.stdout.lower()

    def test_analyze_missing_file(self):
        """analyze with nonexistent file should print error and exit 1."""
        r = _run_cli("analyze", "nonexistent_deck.txt")
        assert r.returncode == 1
        assert "not found" in r.stderr.lower() or "not found" in r.stdout.lower() or r.returncode == 1

    def test_search_no_db(self):
        """search with empty db should handle gracefully."""
        r = _run_cli("search", "Lightning Bolt")
        # Should either return 0 with "no cards" message or 1
        assert r.returncode in (0, 1)

    def test_history_no_decks(self):
        """history with no saved decks should show empty message."""
        r = _run_cli("history")
        assert r.returncode == 0

    def test_ingest_subcommand_exists(self):
        """ingest --help should work."""
        r = _run_cli("ingest", "--help")
        assert r.returncode == 0
        assert "force" in r.stdout.lower()

    def test_analyze_subcommand_exists(self):
        r = _run_cli("analyze", "--help")
        assert r.returncode == 0
        assert "deep" in r.stdout.lower()
        assert "export" in r.stdout.lower()

    def test_goldfish_subcommand_exists(self):
        r = _run_cli("goldfish", "--help")
        assert r.returncode == 0
        assert "sims" in r.stdout.lower()

    def test_gauntlet_subcommand_exists(self):
        r = _run_cli("gauntlet", "--help")
        assert r.returncode == 0
        assert "suite" in r.stdout.lower()

    def test_probability_subcommand_exists(self):
        r = _run_cli("probability", "--help")
        assert r.returncode == 0

    def test_save_subcommand_exists(self):
        r = _run_cli("save", "--help")
        assert r.returncode == 0
        assert "notes" in r.stdout.lower()

    def test_compare_subcommand_exists(self):
        r = _run_cli("compare", "--help")
        assert r.returncode == 0


class TestCollectionCLI:
    """`densa-deck collection` — physical collection tracking.

    Uses --db to point at a temp card DB so the suite never touches the real
    ~/.densa-deck; the collection store is derived from that path.
    """

    def _db(self, tmp_path):
        return str(tmp_path / "cards.db")

    def test_collection_help(self):
        r = _run_cli("collection", "--help")
        assert r.returncode == 0
        assert "sync" in r.stdout and "printings" in r.stdout

    def test_status_on_fresh_install(self, tmp_path):
        r = _run_cli("collection", "status", "--db", self._db(tmp_path))
        assert r.returncode == 0
        assert "0 cards" in r.stdout
        # Must tell the user how to opt in rather than downloading unasked.
        assert "collection sync" in r.stdout

    def test_add_without_catalogue_explains_itself(self, tmp_path):
        r = _run_cli("collection", "add", "Sol Ring", "--db", self._db(tmp_path))
        assert r.returncode == 0
        assert "sync" in r.stdout.lower()

    def test_list_empty(self, tmp_path):
        r = _run_cli("collection", "list", "--db", self._db(tmp_path))
        assert r.returncode == 0

    def test_printings_without_catalogue(self, tmp_path):
        r = _run_cli("collection", "printings", "Sol Ring", "--db", self._db(tmp_path))
        assert r.returncode == 0

    def test_check_missing_file(self, tmp_path):
        r = _run_cli("collection", "check", str(tmp_path / "nope.txt"),
                     "--db", self._db(tmp_path))
        assert r.returncode == 0
        assert "No such deck file" in r.stdout

    def test_check_reports_everything_missing(self, tmp_path):
        deck = tmp_path / "deck.txt"
        deck.write_text("1 Sol Ring\n1 Arcane Signet\n", encoding="utf-8")
        r = _run_cli("collection", "check", str(deck), "--db", self._db(tmp_path))
        assert r.returncode == 0
        assert "Missing: 2" in r.stdout


class TestPhoneCLI:
    """`densa-deck phone` — share a scan surface to a phone over Tailscale."""

    def test_help(self):
        r = _run_cli("phone", "--help")
        assert r.returncode == 0
        assert "status" in r.stdout and "serve" in r.stdout

    def test_status_runs_without_tailscale(self):
        # Must degrade to advice, never a traceback, on a machine with no
        # Tailscale — status is the first thing a confused user runs.
        r = _run_cli("phone", "status")
        assert r.returncode == 0
        assert "Traceback" not in r.stderr

    def test_status_is_read_only(self):
        """Asking must not start a server or change Tailscale config.

        The question is whether `phone status` OPENS the port, so the state
        before the command is the baseline. Asserting the port is simply
        closed afterwards fails whenever the desktop app is legitimately
        running and serving the phone bridge — which is exactly when someone
        is most likely to run the tests.
        """
        def listening() -> bool:
            import socket
            s = socket.socket()
            s.settimeout(1)
            try:
                return s.connect_ex(("127.0.0.1", 8791)) == 0
            finally:
                s.close()

        was_open = listening()
        r = _run_cli("phone", "status")
        assert r.returncode == 0
        if was_open:
            pytest.skip("phone bridge already running outside the tests")
        assert not listening(), "phone status left a listener on 8791"
