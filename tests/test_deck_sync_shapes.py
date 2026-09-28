"""Decks cross the wire in two shapes at once, and neither loses zones.

Audit 2026-09-28: the phone's map left the sideboard out and it sent no
`printings`, so the PC lost the sideboard and a phone edit wiped the PC
deck's chosen printings; the PC sent no per-zone arrays, so the phone put
sideboard / companion / maybeboard in the main deck and dropped printings.
See sync/apply.py `deck_payload`, `desktop_shape`, `phone_arrays`.
"""

from __future__ import annotations

from densa_deck.sync.apply import deck_payload, desktop_shape, phone_arrays

PHONE_EVENT = {
    "deck_id": "ur", "name": "Izzet", "format": "modern", "notes": "",
    # What a phone from before this fix sent: the map WITHOUT the sideboard.
    "decklist": {"Lightning Bolt": 4, "Island": 10},
    "entries": [
        {"name": "Lightning Bolt", "qty": 4, "set_code": "lea", "collector_number": "161"},
        {"name": "Island", "qty": 10},
    ],
    "sideboard": [{"name": "Mystical Dispute", "qty": 3}],
    "commander": [],
    "zones": {"mainboard": ["Lightning Bolt", "Island"], "sideboard": ["Mystical Dispute"]},
    "updated_at": "2026-01-02T00:00:00Z",
}


def test_the_phones_arrays_win_and_recover_the_sideboard():
    decklist, zones, printings = desktop_shape(PHONE_EVENT)
    assert decklist["Mystical Dispute"] == 3
    assert zones["sideboard"] == ["Mystical Dispute"]
    assert printings == [{
        "card_name": "Lightning Bolt", "quantity": 4, "zone": "mainboard",
        "set_code": "lea", "collector_number": "161",
    }]


def test_an_old_desktop_event_is_read_as_before():
    old = {"decklist": {"Sol Ring": 1}, "zones": {"commander": ["Sol Ring"]},
           "printings": []}
    assert desktop_shape(old) == ({"Sol Ring": 1}, {"commander": ["Sol Ring"]}, [])


def test_the_desktop_sends_arrays_the_phone_can_place():
    deck = {
        "deck_id": "ur", "name": "Izzet", "format": "modern", "notes": "",
        "decklist": {"Lightning Bolt": 4, "Island": 10, "Mystical Dispute": 3,
                     "Lurrus of the Dream-Den": 1, "Ponder": 1},
        "zones": {"mainboard": ["Lightning Bolt", "Island"],
                  "sideboard": ["Mystical Dispute"],
                  "companion": ["Lurrus of the Dream-Den"],
                  "maybeboard": ["Ponder"]},
        "printings": [{"card_name": "Lightning Bolt", "set_code": "lea",
                       "collector_number": "161", "quantity": 1, "zone": "mainboard"}],
        "updated_at": "2026-01-01",
    }
    p = deck_payload(deck)
    # Still readable by an older phone...
    assert p["decklist"] == deck["decklist"] and p["zones"] == deck["zones"]
    # ...and placeable by a new one.
    main = {(e["name"], e.get("set_code", "")): e["qty"] for e in p["entries"]}
    assert main == {("Lightning Bolt", "lea"): 1, ("Lightning Bolt", ""): 3, ("Island", ""): 10}
    side = {e["name"]: e["qty"] for e in p["sideboard"]}
    assert side == {"Mystical Dispute": 3, "Lurrus of the Dream-Den": 1, "Ponder": 1}


def test_a_round_trip_through_the_phone_keeps_every_card_and_printing():
    deck = {"decklist": {"Sol Ring": 1, "Island": 30, "Negate": 2},
            "zones": {"commander": ["Sol Ring"], "mainboard": ["Island"],
                      "sideboard": ["Negate"]},
            "printings": [{"card_name": "Sol Ring", "set_code": "cmm",
                           "collector_number": "410", "quantity": 1, "zone": "commander"}]}
    arrays = phone_arrays(deck["decklist"], deck["zones"], deck["printings"])
    decklist, zones, printings = desktop_shape(arrays)
    assert decklist == deck["decklist"]
    assert {z: sorted(n) for z, n in zones.items()} == \
        {z: sorted(n) for z, n in deck["zones"].items()}
    assert [(r["card_name"], r["set_code"], r["zone"]) for r in printings] == \
        [("Sol Ring", "cmm", "commander")]


def test_through_the_real_applier_the_pc_keeps_the_phones_sideboard(tmp_path, monkeypatch):
    from densa_deck.app.api import AppApi
    from densa_deck.data.database import CardDatabase
    from densa_deck.sync.log import KIND_DECK_UPSERT

    CardDatabase(db_path=tmp_path / "cards.db").close()
    monkeypatch.setenv("DENSA_DEVICE_FILE", str(tmp_path / "device.json"))
    api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "versions.db")
    try:
        api.sync_push(events=[{"event_uid": "e1", "device": "phone-1", "seq": 1,
                               "kind": KIND_DECK_UPSERT, "payload": PHONE_EVENT}],
                      peer="phone-1", cursor=1)
        latest = api._get_vstore().get_latest("ur")
        assert latest.decklist["Mystical Dispute"] == 3
        assert "Mystical Dispute" in latest.zones.get("sideboard", [])
        assert any(r.get("set_code") == "lea" for r in latest.printings)
    finally:
        api.close()
