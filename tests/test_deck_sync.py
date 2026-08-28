"""Decks and results, in both directions.

The plumbing existed and had never been connected. `SyncApplier` took a
`deck_store` argument that nothing supplied, so every deck event arriving
from a peer answered "no deck store" and was dropped — silently, on both
sides. `upsert_from_sync` was called by the applier and did not exist
anywhere. Nothing emitted a deck event in the first place, so none of it was
ever reached.

Three things have to hold for two-way sync to be trustworthy:

* **Nothing is lost in the crossing.** A deck is zones and printings as much
  as it is a list of names, and a payload that carried only the name-keyed
  map would round-trip a deck back flattened.
* **The newer edit wins, whichever way it travels.** Events are replayed,
  arrive out of order, and ride in baselines long after they were made.
* **Applying is not editing.** A device that applies a peer's deck must not
  then broadcast it as its own change, or two devices hand one deck back and
  forth forever.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from densa_deck.data.database import CardDatabase
from densa_deck.models import Card, CardLayout, Legality

CARDS = [
    Card(scryfall_id="s1", oracle_id="o1", name="Sol Ring",
         layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
         type_line="Artifact", legalities={"commander": Legality.LEGAL}),
    Card(scryfall_id="s2", oracle_id="o2", name="Island",
         layout=CardLayout.NORMAL, cmc=0, mana_cost="",
         type_line="Basic Land — Island",
         legalities={"commander": Legality.LEGAL}),
    Card(scryfall_id="s3", oracle_id="o3", name="Brainstorm",
         layout=CardLayout.NORMAL, cmc=1, mana_cost="{U}",
         type_line="Instant", legalities={"commander": Legality.LEGAL}),
]

DECK = "Commander:\n1 Sol Ring\n\nMainboard:\n1 Brainstorm\n30 Island\n"
EDITED = "Commander:\n1 Sol Ring\n\nMainboard:\n2 Brainstorm\n29 Island\n"


def _unwrap(reply):
    """@_safe wraps returns in {ok, data}; the tests want the body."""
    return reply.get("data", reply) if isinstance(reply, dict) else reply


class Pair:
    """Two installations that can exchange events, like a phone and a PC."""

    def __init__(self, root: Path):
        self.root = root
        self.a, self.dev_a = self._make("A")
        self.b, self.dev_b = self._make("B")

    def _make(self, name):
        from densa_deck.app.api import AppApi

        home = self.root / name
        home.mkdir()
        db = CardDatabase(db_path=home / "cards.db")
        db.upsert_cards(CARDS)
        db.close()
        api = AppApi(db_path=home / "cards.db",
                     version_db_path=home / "versions.db")
        # A distinct identity each, the way two real machines have one. They
        # otherwise share ~/.densa-deck/device.json and every event looks
        # like the reader's own.
        os.environ["DENSA_DEVICE_FILE"] = str(home / "device.json")
        api._get_sync()
        return api, _unwrap(api.sync_status())["device"]

    def sync(self, src, dst, dst_device, since=0):
        """Everything src has that dst may not. Returns how many moved."""
        cursor, moved, rounds = since, 0, 0
        while rounds < 40:
            reply = _unwrap(src.sync_pull(since=cursor, limit=200,
                                          peer=dst_device))
            if reply["events"]:
                dst.sync_push(events=reply["events"], peer=reply["device"],
                              cursor=reply["cursor"])
                moved += len(reply["events"])
            cursor = reply["cursor"]
            rounds += 1
            if not reply.get("more"):
                break
        return moved

    def a_to_b(self, since=0):
        return self.sync(self.a, self.b, self.dev_b, since)

    def b_to_a(self, since=0):
        return self.sync(self.b, self.a, self.dev_a, since)

    def close(self):
        self.a.close()
        self.b.close()


@pytest.fixture
def pair():
    with tempfile.TemporaryDirectory() as tmp:
        made = Pair(Path(tmp))
        yield made
        made.close()


def _record(api, deck_id="blue"):
    return _unwrap(api.get_deck_record(deck_id))["record"]["record"]


def _cards(api, deck_id="blue"):
    return _unwrap(api.get_deck_latest(deck_id))["decklist"]


class TestADeckCrossesIntact:
    def test_a_deck_saved_on_one_side_arrives_on_the_other(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        assert [d["deck_id"] for d in _unwrap(pair.b.list_saved_decks())] == ["blue"]

    def test_the_cards_arrive_with_it(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        assert _cards(pair.b) == {"Sol Ring": 1, "Brainstorm": 1, "Island": 30}

    def test_the_zones_survive_the_crossing(self, pair):
        """The commander has to still be the commander. A payload carrying
        only {name: count} loses which zone every card was in, and the deck
        comes back as a 32-card pile with a rules violation in it."""
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        zones = _unwrap(pair.b.get_deck_latest("blue")).get("zones") or {}
        assert "Sol Ring" in zones.get("commander", [])

    def test_a_deck_deleted_on_one_side_goes_on_the_other(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        head = _unwrap(pair.a.sync_status())["head"]
        pair.a.delete_deck("blue")
        pair.a_to_b(since=head)
        assert _unwrap(pair.b.list_saved_decks()) == []


class TestResultsCrossToo:
    def test_games_logged_on_one_side_count_on_the_other(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a.record_deck_game("blue", "win")
        pair.a.record_deck_game("blue", "loss")
        pair.a_to_b()
        assert _record(pair.b) == "1-1"

    def test_both_devices_records_merge_rather_than_replace(self, pair):
        """The point of syncing a record at all: games logged at a table on
        one device and at a desk on the other are the same deck's history."""
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a.record_deck_game("blue", "win")
        pair.a_to_b()
        pair.b.record_deck_game("blue", "loss")
        pair.b_to_a()
        assert _record(pair.a) == "1-1"
        assert _record(pair.b) == "1-1"

    def test_a_game_taken_back_is_taken_back_everywhere(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a.record_deck_game("blue", "win")
        pair.a_to_b()
        assert _record(pair.b) == "1-0"

        head = _unwrap(pair.a.sync_status())["head"]
        game_id = _unwrap(pair.a.get_deck_record("blue"))["games"][0]["game_id"]
        pair.a.forget_deck_game(game_id, "blue")
        pair.a_to_b(since=head)
        assert _record(pair.b) == "0-0"

    def test_the_version_a_game_was_played_on_travels_with_it(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a.record_deck_game("blue", "win")
        pair.a.save_deck_version("blue", "Blue Deck", EDITED, "commander", "")
        pair.a.record_deck_game("blue", "loss")
        pair.a_to_b()
        by_version = _unwrap(pair.b.get_deck_record("blue"))["by_version"]
        assert by_version[1]["record"] == "1-0"
        assert by_version[2]["record"] == "0-1"


class TestSyncingTwiceChangesNothing:
    """Every kind here rides in the baseline as well as the log, so it WILL
    arrive twice. A record that doubled on a second sync would be worse than
    no record."""

    def test_the_same_deck_twice_is_one_deck(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        pair.a_to_b()
        assert len(_unwrap(pair.b.list_saved_decks())) == 1

    def test_the_same_games_twice_are_the_same_games(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a.record_deck_game("blue", "win")
        pair.a.record_deck_game("blue", "loss")
        pair.a_to_b()
        pair.a_to_b()
        pair.a_to_b()
        assert _record(pair.b) == "1-1", "the record doubled on a re-sync"

    def test_a_resync_does_not_pile_up_versions(self, pair):
        """An unchanged deck arriving again must not mint a version — the
        history would fill with snapshots of the sync rather than of edits."""
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        pair.a_to_b()
        assert len(_unwrap(pair.b.get_deck_history("blue"))) == 1


class TestTheNewerEditWins:
    def test_an_older_event_does_not_overwrite_a_newer_local_edit(self, pair):
        """The failure this prevents: you edit a deck on your desk, sync, and
        watch the afternoon's work revert because the phone's older copy
        arrived second."""
        from densa_deck.versioning.storage import VersionStore

        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()

        store: VersionStore = pair.b._get_vstore()
        newer = store.deck_updated_at("blue")

        # An event carrying an older timestamp for the same deck.
        result = store.upsert_from_sync(
            deck_id="blue", name="Blue Deck", format_="commander",
            decklist={"Island": 99}, updated_at="2000-01-01T00:00:00",
            zones={}, printings=[])
        assert result["applied"] is False
        assert _cards(pair.b)["Island"] == 30, "an old copy overwrote a new one"
        assert store.deck_updated_at("blue") == newer

    def test_a_newer_event_does_overwrite(self, pair):
        from densa_deck.versioning.storage import VersionStore

        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        store: VersionStore = pair.b._get_vstore()
        result = store.upsert_from_sync(
            deck_id="blue", name="Blue Deck", format_="commander",
            decklist={"Island": 99}, updated_at="2099-01-01T00:00:00",
            zones={}, printings=[])
        assert result["applied"] is True
        assert _cards(pair.b) == {"Island": 99}

    def test_an_edit_on_the_far_side_comes_back(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        pair.b.save_deck_version("blue", "Blue Deck", EDITED, "commander", "")
        pair.b_to_a()
        assert _cards(pair.a)["Brainstorm"] == 2

    def test_the_receiver_keeps_the_senders_timestamp(self, pair):
        """Stamping an applied deck with the local clock would make it look
        freshly edited here, and the receiver would start winning every
        comparison and push the sender's own deck back at it."""
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        theirs = pair.a._get_vstore().deck_updated_at("blue")
        pair.a_to_b()
        assert pair.b._get_vstore().deck_updated_at("blue") == theirs


class TestApplyingIsNotEditing:
    """A device that applies a peer's deck must not AUTHOR it as its own
    change, or the two hand one deck back and forth forever.

    The receiver's log does grow, and that is correct: it stores the peer's
    events verbatim, still stamped with the peer's device id, so a third
    device can be given them later. The thing that must never happen is a
    NEW event carrying the receiver's own id — that is what would come back
    across on the next pull and be applied again.
    """

    def _authored_by(self, api, device):
        events, _cursor = api._get_sync().log.since(0, limit=500)
        return [e for e in events if e.device == device]

    def test_applying_a_deck_authors_nothing_locally(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        mine = self._authored_by(pair.b, pair.dev_b)
        assert mine == [], "the receiver logged the deck as its own edit"

    def test_the_relayed_events_keep_the_authors_id(self, pair):
        """Which is what makes a third device possible at all."""
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a.record_deck_game("blue", "win")
        pair.a_to_b()
        relayed = self._authored_by(pair.b, pair.dev_a)
        kinds = {e.kind for e in relayed}
        assert {"deck-upsert", "deck-game"} <= kinds

    def test_applying_a_game_authors_nothing_locally(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a.record_deck_game("blue", "win")
        pair.a_to_b()
        assert self._authored_by(pair.b, pair.dev_b) == []

    def test_a_round_trip_settles_rather_than_ringing(self, pair):
        """Sync both ways twice. If applying emitted, the second pass would
        carry the same deck back again and never stop."""
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        head_a = _unwrap(pair.a.sync_status())["head"]
        head_b = _unwrap(pair.b.sync_status())["head"]
        pair.b_to_a(since=head_b)
        pair.a_to_b(since=head_a)
        assert _unwrap(pair.a.sync_status())["head"] == head_a
        assert _unwrap(pair.b.sync_status())["head"] == head_b


class TestAFreshDeviceGetsTheHistoryNotJustTheFuture:
    """The log holds only what has happened since logging existed. A deck
    built last month and never touched since is in nobody's log — it is in
    the baseline or it is nowhere."""

    def test_the_baseline_carries_decks(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        baseline = _unwrap(pair.a.sync_pull(since=0, limit=500, peer="fresh"))
        kinds = {e["kind"] for e in baseline["events"]}
        assert "deck-upsert" in kinds

    def test_the_baseline_carries_games(self, pair):
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a.record_deck_game("blue", "win")
        baseline = _unwrap(pair.a.sync_pull(since=0, limit=500, peer="fresh"))
        assert "deck-game" in {e["kind"] for e in baseline["events"]}

    def test_asking_twice_gives_the_same_game_ids(self, pair):
        """A baseline whose game ids moved would double the record on every
        retried first sync — and a first sync is exactly when a connection
        drops."""
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a.record_deck_game("blue", "win")
        first = _unwrap(pair.a.sync_pull(since=0, limit=500, peer="fresh"))
        second = _unwrap(pair.a.sync_pull(since=0, limit=500, peer="fresh"))
        ids = lambda r: [e["event_uid"] for e in r["events"]
                         if e["kind"] == "deck-game"]
        assert ids(first) == ids(second)


class TestTheIncrementalPathCarriesAsMuchAsTheBaseline:
    """Two routes exist and they are built differently.

    A first sync sends a BASELINE, assembled straight from the deck store, so
    it carries whatever the store holds. Every sync after that sends LOG
    EVENTS, whose payload is whatever `record_deck_upsert` chose to put in
    them — and a test that only ever syncs from zero exercises the first and
    proves nothing about the second.

    Which is the shape of the bug: the event could have been dropping zones
    and printings on every ordinary edit and the round-trip tests would still
    have passed.
    """

    def _sync_an_edit(self, pair):
        """Baseline first, then an edit delivered as a log event."""
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        head = _unwrap(pair.a.sync_status())["head"]
        pair.a.save_deck_version("blue", "Blue Deck", EDITED, "commander", "")
        moved = pair.a_to_b(since=head)
        assert moved, "no incremental event was sent, so nothing was tested"
        return _unwrap(pair.b.get_deck_latest("blue"))

    def test_an_edited_deck_arrives_as_an_event(self, pair):
        assert self._sync_an_edit(pair)["decklist"]["Brainstorm"] == 2

    def test_the_zones_are_in_the_event_not_only_the_baseline(self, pair):
        zones = self._sync_an_edit(pair).get("zones") or {}
        assert "Sol Ring" in zones.get("commander", []), (
            "the incremental event flattened the deck")

    def test_the_printings_are_in_the_event_too(self, pair):
        """A slot that named a printing has to still name it after crossing —
        the whole printing-level decklist otherwise survives a first sync and
        is lost on the next edit."""
        pair.a.save_deck_version("blue", "Blue Deck", DECK, "commander", "")
        pair.a_to_b()
        head = _unwrap(pair.a.sync_status())["head"]
        # An edit that names an exact printing.
        with_printing = ("Commander:\n1 Sol Ring\n\n"
                         "Mainboard:\n1 Brainstorm (ice) 51\n29 Island\n")
        pair.a.save_deck_version("blue", "Blue Deck", with_printing,
                                 "commander", "")
        pair.a_to_b(since=head)

        mine = pair.a._get_vstore().get_latest("blue")
        theirs = pair.b._get_vstore().get_latest("blue")
        assert [p.get("set_code") for p in mine.printings], (
            "the sender never recorded a printing, so nothing was tested")
        assert [p.get("set_code") for p in theirs.printings] == \
               [p.get("set_code") for p in mine.printings]
