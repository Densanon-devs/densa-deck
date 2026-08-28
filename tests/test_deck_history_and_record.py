"""Versions worth keeping, and a win/loss record that survives keeping them.

Three things that only work together.

**Automatic versions.** Saving on every edit produces forty snapshots of an
afternoon's tinkering and buries the three that meant something. A version is
minted when the deck actually differs, and not otherwise.

**A record that follows the version in part and the deck as a whole.** Which
means the games are attached to a version NUMBER rather than to a version
row — because of the third thing.

**A history cap.** The moment history is trimmed is the moment a record
attached to version rows would silently restate a deck's lifetime results.
The snapshot is what expires; the fact that you played it and won is not.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.versioning.storage import (
    DEFAULT_MAX_VERSIONS,
    VersionStore,
)


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        made = VersionStore(db_path=Path(tmp) / "versions.db")
        yield made
        # Windows will not delete a file SQLite still has open, and the
        # tempdir cleanup fails the test on teardown rather than on anything
        # the test did.
        made.close()


def _save(store, deck_id="atraxa", cards=None, zones=None, printings=None):
    cards = cards if cards is not None else {"Sol Ring": 1}
    return store.save_version_if_changed(
        deck_id, "Atraxa", "commander", cards,
        zones if zones is not None else {"main": sorted(cards)},
        printings=printings)


class TestAVersionIsOnlyMintedByARealChange:
    def test_the_first_save_always_makes_one(self, store):
        snapshot, created = _save(store)
        assert created is True
        assert snapshot.version_number == 1

    def test_saving_the_same_deck_again_does_not(self, store):
        """The whole reason automatic versioning is bearable."""
        _save(store)
        snapshot, created = _save(store)
        assert created is False
        assert snapshot.version_number == 1
        assert len(store.get_all_versions("atraxa")) == 1

    def test_one_added_card_does(self, store):
        _save(store)
        _snapshot, created = _save(store, cards={"Sol Ring": 1, "Llanowar Elves": 1})
        assert created is True

    def test_one_cut_card_does(self, store):
        _save(store, cards={"Sol Ring": 1, "Llanowar Elves": 1})
        _snapshot, created = _save(store, cards={"Sol Ring": 1})
        assert created is True

    def test_a_changed_quantity_does(self, store):
        _save(store, cards={"Forest": 10})
        _snapshot, created = _save(store, cards={"Forest": 11})
        assert created is True

    def test_a_card_moving_zone_does(self, store):
        """A commander moved to the maindeck is an edit a save persists, so a
        version that ignored it would read back as a deck never held."""
        _save(store, cards={"Atraxa": 1}, zones={"commander": ["Atraxa"]})
        _snapshot, created = _save(store, cards={"Atraxa": 1},
                                   zones={"main": ["Atraxa"]})
        assert created is True

    def test_a_different_printing_of_the_same_card_does(self, store):
        _save(store, printings=[{"name": "Sol Ring", "printing_id": "aaa"}])
        _snapshot, created = _save(
            store, printings=[{"name": "Sol Ring", "printing_id": "bbb"}])
        assert created is True

    def test_reordering_alone_does_not(self, store):
        """Dict and list order are artefacts of parsing, not decisions. A
        version per re-save would defeat the point."""
        _save(store, cards={"Sol Ring": 1, "Forest": 2},
              zones={"main": ["Sol Ring", "Forest"]})
        _snapshot, created = _save(store, cards={"Forest": 2, "Sol Ring": 1},
                                   zones={"main": ["Forest", "Sol Ring"]})
        assert created is False

    def test_scores_moving_on_their_own_does_not(self, store):
        """Scores are derived from the decklist and shift when the card
        database is re-ingested. Comparing them would mint a version for
        every deck the morning after an ingest."""
        store.save_version_if_changed(
            "atraxa", "Atraxa", "commander", {"Sol Ring": 1}, {},
            scores={"power": 5.0})
        _snapshot, created = store.save_version_if_changed(
            "atraxa", "Atraxa", "commander", {"Sol Ring": 1}, {},
            scores={"power": 7.5})
        assert created is False


class TestTheRecordFollowsBothTheVersionAndTheDeck:
    def test_a_game_lands_on_the_current_version(self, store):
        _save(store)
        _save(store, cards={"Sol Ring": 1, "Forest": 1})     # v2
        store.record_game("atraxa", "win")
        assert store.version_record("atraxa", 2)["wins"] == 1
        assert store.version_record("atraxa", 1)["wins"] == 0

    def test_a_game_can_be_entered_against_an_older_version(self, store):
        _save(store)
        _save(store, cards={"Sol Ring": 1, "Forest": 1})
        store.record_game("atraxa", "loss", version_number=1)
        assert store.version_record("atraxa", 1)["losses"] == 1

    def test_the_deck_total_is_every_version_together(self, store):
        _save(store)
        store.record_game("atraxa", "win", version_number=1)
        _save(store, cards={"Sol Ring": 1, "Forest": 1})
        store.record_game("atraxa", "win", version_number=2)
        store.record_game("atraxa", "loss", version_number=2)
        assert store.deck_record("atraxa")["record"] == "2-1"

    def test_a_game_before_any_version_is_still_counted(self, store):
        """Playing a deck you have not saved is a real thing to do."""
        store.record_game("brand-new", "win")
        assert store.deck_record("brand-new")["wins"] == 1

    def test_draws_are_neither_wins_nor_losses(self, store):
        _save(store)
        store.record_game("atraxa", "win")
        store.record_game("atraxa", "draw")
        record = store.deck_record("atraxa")
        assert (record["wins"], record["losses"], record["draws"]) == (1, 0, 1)
        assert record["win_rate"] == 0.5, "a draw was a game played"
        assert record["decisive_win_rate"] == 1.0, "and not a game lost"

    def test_the_shorthand_reads_the_way_people_say_it(self, store):
        store.record_game("d", "win")
        store.record_game("d", "win")
        store.record_game("d", "loss")
        assert store.deck_record("d")["record"] == "2-1"
        store.record_game("d", "draw")
        assert store.deck_record("d")["record"] == "2-1-1"

    def test_a_deck_with_no_games_has_no_rate_rather_than_zero(self, store):
        """0% and "never played" are different, and a deck that reads 0%
        looks bad rather than new."""
        record = store.deck_record("never-played")
        assert record["games"] == 0
        assert record["win_rate"] is None

    def test_nonsense_results_are_refused(self, store):
        with pytest.raises(ValueError):
            store.record_game("atraxa", "victory")

    def test_a_game_can_be_taken_back(self, store):
        store.record_game("d", "win")
        games = store.games_for_deck("d")
        assert store.forget_game(games[0]["game_id"]) is True
        assert store.deck_record("d")["games"] == 0

    def test_forgetting_a_game_that_is_not_there_says_so(self, store):
        assert store.forget_game(999_999) is False


class TestTheCapTrimsHistoryWithoutRewritingTheRecord:
    """The reason games are keyed by version NUMBER and not by version row.

    A foreign key would have taken the games with the snapshots, and a deck's
    lifetime record would quietly improve or worsen the moment its history
    was trimmed — with nothing on screen to say why.
    """

    def _ten_versions_one_game_each(self, store, deck_id="d"):
        for i in range(10):
            store.save_version_if_changed(
                deck_id, "D", "commander", {f"Card {i}": 1}, {})
            store.record_game(deck_id, "win")

    def test_only_the_newest_snapshots_are_kept(self, store):
        store.set_history_limit("d", 3)
        self._ten_versions_one_game_each(store)
        kept = [s.version_number for s in store.get_all_versions("d")]
        assert sorted(kept) == [8, 9, 10]

    def test_every_game_survives_the_trim(self, store):
        store.set_history_limit("d", 3)
        self._ten_versions_one_game_each(store)
        assert store.deck_record("d")["games"] == 10, "the cap ate the record"

    def test_a_pruned_version_still_reports_its_games(self, store):
        store.set_history_limit("d", 3)
        self._ten_versions_one_game_each(store)
        assert store.version_record("d", 1)["wins"] == 1

    def test_zero_means_keep_everything(self, store):
        store.set_history_limit("d", 0)
        self._ten_versions_one_game_each(store)
        assert len(store.get_all_versions("d")) == 10

    def test_a_deck_without_its_own_cap_follows_the_default(self, store):
        assert store.history_limit("untouched") == DEFAULT_MAX_VERSIONS
        store.set_default_history_limit(4)
        assert store.history_limit("untouched") == 4

    def test_a_deck_with_its_own_cap_ignores_the_default(self, store):
        store.set_default_history_limit(4)
        store.set_history_limit("special", 9)
        assert store.history_limit("special") == 9

    def test_clearing_a_decks_cap_puts_it_back_on_the_default(self, store):
        store.set_default_history_limit(7)
        store.set_history_limit("d", 2)
        assert store.set_history_limit("d", None) == 7
        assert store.history_limit("d") == 7

    def test_lowering_a_cap_does_not_prune_on_the_spot(self, store):
        """Destroying history as a side effect of changing a setting is not
        something anyone asked for. It happens on the next save, where it is
        the consequence of an action just taken."""
        self._ten_versions_one_game_each(store)
        store.set_history_limit("d", 2)
        assert len(store.get_all_versions("d")) == 10

        store.save_version_if_changed("d", "D", "commander", {"New": 1}, {})
        assert len(store.get_all_versions("d")) == 2, "and now it does"

    def test_a_negative_cap_is_read_as_uncapped(self, store):
        assert store.set_history_limit("d", -5) == 0
        self._ten_versions_one_game_each(store)
        assert len(store.get_all_versions("d")) == 10


class TestDeletingADeckLeavesNothingBehind:
    """A deck id can be reused — someone deletes "Atraxa" and builds another.
    A surviving game row would credit the new deck with the old one's record.
    """

    def test_the_games_go_with_it(self, store):
        _save(store)
        store.record_game("atraxa", "win")
        store.delete_deck("atraxa")
        assert store.deck_record("atraxa")["games"] == 0

    def test_the_history_setting_goes_with_it(self, store):
        store.set_default_history_limit(30)
        store.set_history_limit("atraxa", 2)
        store.delete_deck("atraxa")
        assert store.history_limit("atraxa") == 30, "it kept the old deck's cap"

    def test_the_versions_still_go_with_it(self, store):
        _save(store)
        store.delete_deck("atraxa")
        assert store.get_all_versions("atraxa") == []


class TestPrintingRowsAreComparedWhicheverWayTheyAreSpelled:
    """The desktop save path writes `card_name`; rows built by hand tend to
    write `name`. Reading only one key compares every row's name as "" — so
    two different cards in the same slot of the same set look identical and
    the edit is dropped on the floor.
    """

    def test_a_swap_written_the_desktops_way_is_seen(self, store):
        store.save_version_if_changed(
            "d", "D", "commander", {"Sol Ring": 1}, {},
            printings=[{"card_name": "Sol Ring", "set_code": "cmm",
                        "collector_number": "410"}])
        _snapshot, created = store.save_version_if_changed(
            "d", "D", "commander", {"Sol Ring": 1}, {},
            printings=[{"card_name": "Sol Ring", "set_code": "ltc",
                        "collector_number": "285"}])
        assert created is True

    def test_the_same_printing_written_the_desktops_way_is_not_a_change(self, store):
        rows = [{"card_name": "Sol Ring", "set_code": "cmm",
                 "collector_number": "410"}]
        store.save_version_if_changed("d", "D", "commander", {"Sol Ring": 1},
                                      {}, printings=list(rows))
        _snapshot, created = store.save_version_if_changed(
            "d", "D", "commander", {"Sol Ring": 1}, {}, printings=list(rows))
        assert created is False

    def test_two_different_cards_in_one_set_are_told_apart(self, store):
        """The failure an unread name key produces: same set, same number
        field, different card — indistinguishable without the name."""
        store.save_version_if_changed(
            "d", "D", "commander", {"Sol Ring": 1}, {},
            printings=[{"card_name": "Sol Ring", "set_code": "cmm"}])
        _snapshot, created = store.save_version_if_changed(
            "d", "D", "commander", {"Lightning Bolt": 1}, {},
            printings=[{"card_name": "Lightning Bolt", "set_code": "cmm"}])
        assert created is True


class TestANoteIsNotACardButMustNotBeLost:
    """Versions are minted by card changes, so a note cannot mint one. It
    still has to go somewhere — otherwise someone types a note about the deck
    and watches it disappear with nothing said."""

    def test_a_note_on_an_unchanged_deck_lands_on_the_current_version(self, store):
        _save(store)
        snapshot, created = store.save_version_if_changed(
            "atraxa", "Atraxa", "commander", {"Sol Ring": 1},
            {"main": ["Sol Ring"]}, notes="swapped sleeves, same 100")
        assert created is False, "a note is not a card"
        assert snapshot.notes == "swapped sleeves, same 100"
        assert store.get_latest("atraxa").notes == "swapped sleeves, same 100"

    def test_it_does_not_mint_a_version(self, store):
        _save(store)
        store.save_version_if_changed(
            "atraxa", "Atraxa", "commander", {"Sol Ring": 1},
            {"main": ["Sol Ring"]}, notes="a thought")
        assert len(store.get_all_versions("atraxa")) == 1

    def test_an_empty_note_does_not_wipe_the_one_there(self, store):
        _save(store)
        store.save_version_if_changed(
            "atraxa", "Atraxa", "commander", {"Sol Ring": 1},
            {"main": ["Sol Ring"]}, notes="worth keeping")
        store.save_version_if_changed(
            "atraxa", "Atraxa", "commander", {"Sol Ring": 1},
            {"main": ["Sol Ring"]}, notes="")
        assert store.get_latest("atraxa").notes == "worth keeping"


class TestThroughTheDesktopApi:
    """The same rules, over the wire the UI actually calls."""

    @pytest.fixture
    def api(self):
        from densa_deck.app.api import AppApi
        from densa_deck.data.database import CardDatabase
        from densa_deck.models import Card, CardLayout, Legality

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = CardDatabase(db_path=root / "cards.db")
            db.upsert_cards([
                Card(scryfall_id="s1", oracle_id="o1", name="Sol Ring",
                     layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
                     type_line="Artifact",
                     legalities={"commander": Legality.LEGAL}),
                Card(scryfall_id="s2", oracle_id="o2", name="Forest",
                     layout=CardLayout.NORMAL, cmc=0, mana_cost="",
                     type_line="Basic Land — Forest",
                     legalities={"commander": Legality.LEGAL}),
                Card(scryfall_id="s3", oracle_id="o3", name="Arcane Signet",
                     layout=CardLayout.NORMAL, cmc=2, mana_cost="{2}",
                     type_line="Artifact",
                     legalities={"commander": Legality.LEGAL}),
            ])
            db.close()
            made = AppApi(db_path=root / "cards.db",
                          version_db_path=root / "versions.db")
            yield made
            made.close()

    def _save(self, api, text, deck_id="d1"):
        return api.save_deck_version(deck_id, "D1", text, "commander", "")

    DECK = "Commander:\n1 Sol Ring\n\nMainboard:\n30 Forest\n"
    EDITED = "Commander:\n1 Sol Ring\n\nMainboard:\n1 Arcane Signet\n29 Forest\n"

    def test_an_unchanged_save_reports_that_it_made_nothing(self, api):
        self._save(api, self.DECK)
        again = self._save(api, self.DECK)["data"]
        assert again["created"] is False
        assert again["version_number"] == 1

    def test_an_edited_save_reports_the_new_version(self, api):
        self._save(api, self.DECK)
        edited = self._save(api, self.EDITED)["data"]
        assert edited["created"] is True
        assert edited["version_number"] == 2

    def test_a_game_is_logged_against_the_current_version(self, api):
        self._save(api, self.DECK)
        out = api.record_deck_game("d1", "win")["data"]
        assert out["record"]["record"] == "1-0"
        assert out["by_version"][1]["wins"] == 1

    def test_a_bad_result_is_refused_with_a_readable_reason(self, api):
        reply = api.record_deck_game("d1", "victory")
        # @_safe wraps failures, so the envelope is where the answer is.
        body = reply.get("data", reply)
        assert body.get("ok") is False
        assert "win" in body.get("error", "")

    def test_the_record_endpoint_answers_for_deck_and_version(self, api):
        self._save(api, self.DECK)
        api.record_deck_game("d1", "win")
        self._save(api, self.EDITED)
        api.record_deck_game("d1", "loss")

        out = api.get_deck_record("d1")["data"]
        assert out["record"]["record"] == "1-1"
        assert out["by_version"][1]["record"] == "1-0"
        assert out["by_version"][2]["record"] == "0-1"
        assert len(out["games"]) == 2

    def test_a_game_can_be_taken_back_over_the_wire(self, api):
        api.record_deck_game("d1", "win")
        game_id = api.get_deck_record("d1")["data"]["games"][0]["game_id"]
        assert api.forget_deck_game(game_id, "d1")["data"]["removed"] is True
        assert api.get_deck_record("d1")["data"]["record"]["games"] == 0

    def test_the_default_limit_can_be_moved(self, api):
        out = api.set_history_limit(12)["data"]
        assert out["default_history_limit"] == 12
        assert api.get_history_limits()["data"]["default_history_limit"] == 12

    def test_one_deck_can_have_its_own_limit(self, api):
        api.set_history_limit(12)
        api.set_history_limit(3, "d1")
        limits = api.get_history_limits("d1")["data"]
        assert limits["deck_history_limit"] == 3
        assert limits["effective"] == 3
        assert limits["default_history_limit"] == 12

    def test_clearing_a_decks_limit_returns_it_to_the_default(self, api):
        api.set_history_limit(12)
        api.set_history_limit(3, "d1")
        api.set_history_limit(None, "d1")
        limits = api.get_history_limits("d1")["data"]
        assert limits["deck_history_limit"] is None
        assert limits["effective"] == 12

    def test_the_default_cannot_be_cleared_to_nothing(self, api):
        """`None` means "follow the default" — the default has nothing to
        follow, so it needs a number, and 0 is how you say "keep everything"."""
        body = api.set_history_limit(None)
        body = body.get("data", body)
        assert body.get("ok") is False

    def test_a_capped_deck_trims_snapshots_but_not_its_record(self, api):
        api.set_history_limit(2, "d1")
        for n in range(5):
            api.save_deck_version(
                "d1", "D1",
                f"Commander:\n1 Sol Ring\n\nMainboard:\n{30 - n} Forest\n",
                "commander", "")
            api.record_deck_game("d1", "win")

        out = api.get_deck_record("d1")["data"]
        assert out["record"]["games"] == 5, "the cap ate the record"
        history = api.get_deck_history("d1")
        assert len(history.get("data", history)) == 2


class TestUpgradingADatabaseThatAlreadyHasGames:
    """Games logged before sync existed have no travelling identity.

    They are local history and perfectly real — someone may have been
    recording results for a month — so they get a uid minted rather than
    being left unsyncable or, worse, dropped. And the uid has to be UNIQUE,
    because that index is what makes applying a game from a peer idempotent.
    """

    def _legacy_db(self, path):
        """A `deck_games` table in the shape that shipped before sync."""
        import sqlite3

        conn = sqlite3.connect(str(path))
        conn.executescript("""
            CREATE TABLE decks (
                deck_id TEXT PRIMARY KEY, name TEXT NOT NULL, format TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE deck_versions (
                version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                deck_id TEXT NOT NULL, version_number INTEGER NOT NULL,
                saved_at TEXT NOT NULL, notes TEXT DEFAULT '',
                decklist_json TEXT NOT NULL, scores_json TEXT DEFAULT '{}',
                metrics_json TEXT DEFAULT '{}');
            CREATE TABLE deck_games (
                game_id INTEGER PRIMARY KEY AUTOINCREMENT,
                deck_id TEXT NOT NULL, version_number INTEGER NOT NULL DEFAULT 0,
                result TEXT NOT NULL, opponent TEXT DEFAULT '',
                notes TEXT DEFAULT '', played_at TEXT NOT NULL);
        """)
        conn.executemany(
            """INSERT INTO deck_games (deck_id, version_number, result, played_at)
               VALUES (?, ?, ?, ?)""",
            [("old", 1, "win", "2026-01-01"), ("old", 1, "loss", "2026-01-02"),
             ("old", 2, "win", "2026-01-03")])
        conn.commit()
        conn.close()

    def test_the_old_games_survive_the_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "versions.db"
            self._legacy_db(path)
            store = VersionStore(db_path=path)
            try:
                assert store.deck_record("old")["record"] == "2-1"
            finally:
                store.close()

    def test_and_every_one_gains_an_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "versions.db"
            self._legacy_db(path)
            store = VersionStore(db_path=path)
            try:
                games = store.games_for_sync("old")
                assert len(games) == 3
                assert all(g["game_uid"] for g in games), "unsyncable forever"
                assert len({g["game_uid"] for g in games}) == 3
            finally:
                store.close()

    def test_the_uid_index_is_unique_so_applying_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "versions.db"
            self._legacy_db(path)
            store = VersionStore(db_path=path)
            try:
                store.record_game("old", "win", game_uid="from-peer")
                store.record_game("old", "win", game_uid="from-peer")
                assert store.deck_record("old")["games"] == 4, (
                    "the same game was counted twice")
            finally:
                store.close()

    def test_reopening_does_not_re_mint_the_uids(self):
        """The backfill must not run again and renumber history — a uid that
        changed would look like a brand-new game to the other device."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "versions.db"
            self._legacy_db(path)
            first = VersionStore(db_path=path)
            before = {g["game_uid"] for g in first.games_for_sync("old")}
            first.close()

            again = VersionStore(db_path=path)
            try:
                assert {g["game_uid"] for g in again.games_for_sync("old")} == before
            finally:
                again.close()
