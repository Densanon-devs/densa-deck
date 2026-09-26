"""Card art by name, and coach sessions that stay deleted.

`get_card_images` is what lets every desktop panel show a card rather than
only name it: most of them have a name and nothing else in hand.
"""

from __future__ import annotations

import json
import threading

import pytest

from densa_deck.app.api import AppApi
from densa_deck.data.database import CardDatabase
from densa_deck.models import Card, CardLayout, Legality

SOL = "91fdb56b-54d5-4272-8319-505ff987fe9b"
FIRE = "18303862-4726-4136-814f-157aa7006579"


@pytest.fixture
def api(tmp_path):
    db = CardDatabase(db_path=tmp_path / "cards.db")
    db.upsert_cards([
        Card(scryfall_id=SOL, oracle_id="oid-sol", name="Sol Ring",
             layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}", type_line="Artifact",
             legalities={"commander": Legality.LEGAL}),
        Card(scryfall_id=FIRE, oracle_id="oid-fire", name="Fire // Ice",
             layout=CardLayout.SPLIT, cmc=4, type_line="Instant // Instant",
             legalities={"commander": Legality.LEGAL}),
        # A catalogue row with an unusable id must yield no art, not an error.
        Card(scryfall_id="not-a-uuid", oracle_id="oid-bad", name="Broken Row",
             layout=CardLayout.NORMAL, type_line="Artifact",
             legalities={"commander": Legality.LEGAL}),
    ])
    db.close()
    a = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "versions.db")
    a._session_path = tmp_path / "coach_sessions.json"
    yield a
    a.close()


def _images(r):
    assert r["ok"], r
    return r["data"]["images"]


def test_resolves_names_to_hotlinked_scryfall_images(api):
    images = _images(api.get_card_images(["Sol Ring"]))
    sol = images["Sol Ring"]
    assert sol["small"] == f"https://cards.scryfall.io/small/front/9/1/{SOL}.jpg"
    assert sol["normal"] == f"https://cards.scryfall.io/normal/front/9/1/{SOL}.jpg"
    assert sol["art"] == f"https://cards.scryfall.io/art_crop/front/9/1/{SOL}.jpg"
    assert sol["scryfall"] == f"https://scryfall.com/card/{SOL}"


def test_keys_come_back_exactly_as_sent(api):
    images = _images(api.get_card_images(["sol ring", "Fire"]))
    # Case as sent, and a split card found by one face.
    assert set(images) == {"sol ring", "Fire"}
    assert FIRE in images["Fire"]["normal"]


def test_unknown_blank_and_unusable_names_are_simply_absent(api):
    images = _images(api.get_card_images(["Not A Card", "", None, "Broken Row", "Sol Ring"]))
    assert set(images) == {"Sol Ring"}


def test_batch_is_capped(api):
    names = [f"Card {i}" for i in range(AppApi.CARD_IMAGE_BATCH_LIMIT + 50)] + ["Sol Ring"]
    # Sol Ring sits past the cap, so it is not looked at.
    assert "Sol Ring" not in _images(api.get_card_images(names))


def test_empty_request(api):
    assert _images(api.get_card_images([])) == {}
    assert _images(api.get_card_images(None)) == {}


def test_deleting_a_coach_session_is_saved_immediately(api):
    from densa_deck.analyst.coach import CoachSession
    for token in ("keep", "drop"):
        api._coach_sessions[token] = {
            "session": CoachSession(deck_sheet="", allowed_cards=set()),
            "deck_name": token, "deck_id": None, "created_at": "",
            "turn_lock": threading.Lock(),
        }
    api._save_coach_sessions()

    r = api.coach_close("drop")
    assert r["ok"] and r["data"]["closed"] is True

    # On disk now, not only at shutdown: a crash must not bring it back.
    saved = json.loads(api._session_path.read_text(encoding="utf-8"))
    tokens = {s.get("token") for s in (saved.get("sessions", saved) if isinstance(saved, dict) else saved)}
    assert "drop" not in tokens
    assert "keep" in tokens
