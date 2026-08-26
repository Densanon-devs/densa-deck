"""Turning sync events into state, and state changes into sync events.

This is the half of syncing where mistakes cost cards, so the rules are
stated plainly and the tests hold them:

* **A delta is applied exactly once.** The log's `accept` is the gate; if it
  says the event is already known, nothing happens here at all.
* **A delta is never re-derived from a total.** The event says "+3", not
  "there are now 5". Two devices that both added copies both get their
  copies.
* **Collections are addressed by uid**, and one that arrives unknown is
  created rather than dropped. An event for a collection this device has
  never heard of is normal — it was made on the other device.
* **Create beats delete for collections.** If a collection is deleted here
  and simultaneously had cards added there, the cards win and the collection
  comes back. Resurrecting a grouping is an annoyance; losing the cards
  inside it is not.
"""

from __future__ import annotations

from densa_deck.sync.log import (
    KIND_COLLECTION_DELETE,
    KIND_COLLECTION_UPSERT,
    KIND_DECK_DELETE,
    KIND_DECK_UPSERT,
    KIND_STACK_DELTA,
    KIND_STACK_SET,
    SyncEvent,
    SyncLog,
)


def stack_delta_event(*, printing_id: str, card_name: str, delta: int,
                      collection_uid: str, oracle_id: str = "",
                      finish: str = "nonfoil", condition: str = "NM",
                      language: str = "en", location: str = "",
                      reason: str = "manual") -> dict:
    """The payload for a quantity change, addressed by natural key.

    Nothing here is a local id: both devices derive this key independently
    from the card itself and agree without ever having spoken.
    """
    return {
        "printing_id": printing_id,
        "card_name": card_name,
        "oracle_id": oracle_id,
        "finish": finish,
        "condition": condition,
        "language": language,
        "location": location,
        "collection_uid": collection_uid,
        "delta": int(delta),
        "reason": reason,
    }


class SyncApplier:
    """Applies events from a peer to this device's collection."""

    def __init__(self, store, log: SyncLog, deck_store=None):
        self.store = store
        self.log = log
        self.deck_store = deck_store

    # --------------------------------------------------------------- inbound

    def apply(self, event: SyncEvent) -> dict:
        """Apply one event. Returns what happened, for the caller's report."""
        if not self.log.accept(event):
            # Already known. This is the common case on a re-sync and is not
            # an error: it is the mechanism that makes retries safe.
            return {"applied": False, "reason": "duplicate"}

        handler = {
            KIND_STACK_DELTA: self._apply_stack_delta,
            KIND_STACK_SET: self._apply_stack_set,
            KIND_COLLECTION_UPSERT: self._apply_collection_upsert,
            KIND_COLLECTION_DELETE: self._apply_collection_delete,
            KIND_DECK_UPSERT: self._apply_deck_upsert,
            KIND_DECK_DELETE: self._apply_deck_delete,
        }.get(event.kind)

        if handler is None:
            # An unknown kind from a newer peer is STORED and forwarded but
            # not acted on. Dropping it would silently break a mixed-version
            # pair; guessing at it would be worse.
            return {"applied": False, "reason": "unknown kind", "kind": event.kind}

        try:
            return handler(event)
        except Exception as exc:                       # pragma: no cover
            # One malformed event must not stall the whole exchange. It stays
            # in the log (so it is not re-requested forever) and is reported.
            return {"applied": False, "reason": f"error: {exc}"}

    def apply_many(self, events: list[SyncEvent]) -> dict:
        applied = duplicates = failed = 0
        problems: list[dict] = []
        for event in events:
            result = self.apply(event)
            if result.get("applied"):
                applied += 1
            elif result.get("reason") == "duplicate":
                duplicates += 1
            else:
                failed += 1
                problems.append({"event_uid": event.event_uid,
                                 "reason": result.get("reason", "")})
        return {"applied": applied, "duplicates": duplicates,
                "failed": failed, "problems": problems[:20]}

    def _apply_stack_delta(self, event: SyncEvent) -> dict:
        p = event.payload
        delta = int(p.get("delta") or 0)
        if not delta:
            return {"applied": False, "reason": "zero delta"}

        collection_id = self._collection_for(p.get("collection_uid", ""))
        self.store.add_copies(
            p.get("printing_id", ""), p.get("card_name", "") or "Unknown card",
            quantity=delta,
            oracle_id=p.get("oracle_id", ""),
            finish=p.get("finish", "nonfoil"),
            condition=p.get("condition", "NM"),
            language=p.get("language", "en"),
            location=p.get("location", ""),
            collection_id=collection_id,
            reason=p.get("reason", "sync"),
        )
        return {"applied": True, "kind": event.kind, "delta": delta}

    def _apply_stack_set(self, event: SyncEvent) -> dict:
        """Set a stack to an absolute quantity, rather than nudging it.

        Only ever used for the first-sync baseline, and it has to be absolute:
        a phone that has just been paired must end up agreeing with the
        desktop exactly, and a pile of deltas whose starting point nobody
        recorded cannot promise that.

        Everything else in this system is a delta on purpose — two devices
        editing the same stack offline both keep their change. An absolute set
        is the one operation that CANNOT commute, which is why it is confined
        to the moment a device has no state at all to conflict with.
        """
        p = event.payload
        target = int(p.get("quantity") or 0)
        collection_id = self._collection_for(p.get("collection_uid", ""))
        current = self.store.stack_quantity(
            p.get("printing_id", ""),
            finish=p.get("finish", "nonfoil"),
            condition=p.get("condition", "NM"),
            language=p.get("language", "en"),
            collection_id=collection_id,
        )
        delta = target - current
        if not delta:
            return {"applied": True, "kind": event.kind, "delta": 0}
        self.store.add_copies(
            p.get("printing_id", ""), p.get("card_name", "") or "Unknown card",
            quantity=delta,
            oracle_id=p.get("oracle_id", ""),
            finish=p.get("finish", "nonfoil"),
            condition=p.get("condition", "NM"),
            language=p.get("language", "en"),
            location=p.get("location", ""),
            collection_id=collection_id,
            reason="baseline",
        )
        return {"applied": True, "kind": event.kind, "delta": delta}

    def _apply_collection_upsert(self, event: SyncEvent) -> dict:
        p = event.payload
        uid = p.get("collection_uid", "")
        if not uid:
            return {"applied": False, "reason": "no collection uid"}

        existing = self.store.collection_by_uid(uid)
        if not existing:
            self.store.ensure_collection_uid(
                uid, name=p.get("name", "Collection"),
                kind=p.get("kind", "collection"), notes=p.get("notes", ""))
            return {"applied": True, "kind": event.kind, "created": True}

        # Last write wins on the name. A name has no sane merge, and losing a
        # rename costs nothing that cannot be redone in a second.
        name = (p.get("name") or "").strip()
        if name and name != existing["name"]:
            try:
                self.store.rename_collection(existing["collection_id"], name)
            except ValueError:
                # The name is taken locally by a different collection. Keep
                # ours rather than inventing a suffix behind the user's back.
                return {"applied": True, "kind": event.kind,
                        "renamed": False, "reason": "name taken locally"}
        return {"applied": True, "kind": event.kind, "renamed": bool(name)}

    def _apply_collection_delete(self, event: SyncEvent) -> dict:
        p = event.payload
        existing = self.store.collection_by_uid(p.get("collection_uid", ""))
        if not existing:
            return {"applied": True, "kind": event.kind, "already_gone": True}

        # `discard_cards` is only honoured when the peer said so explicitly.
        # A delete arriving without it removes the grouping and keeps every
        # card, which is the safe reading of an ambiguous instruction.
        result = self.store.delete_collection(
            existing["collection_id"],
            discard_cards=bool(p.get("discard_cards")))
        return {"applied": True, "kind": event.kind, **result}

    def _apply_deck_upsert(self, event: SyncEvent) -> dict:
        if self.deck_store is None:
            return {"applied": False, "reason": "no deck store"}
        p = event.payload
        deck_id = p.get("deck_id", "")
        if not deck_id:
            return {"applied": False, "reason": "no deck id"}
        # A deck is a document: last write wins, because a half-merged
        # decklist is worse than a lost edit.
        self.deck_store.upsert_from_sync(
            deck_id=deck_id, name=p.get("name", "Untitled"),
            format_=p.get("format", ""), decklist=p.get("decklist", {}),
            updated_at=event.created_at, notes=p.get("notes", ""))
        return {"applied": True, "kind": event.kind}

    def _apply_deck_delete(self, event: SyncEvent) -> dict:
        if self.deck_store is None:
            return {"applied": False, "reason": "no deck store"}
        deck_id = event.payload.get("deck_id", "")
        if not deck_id:
            return {"applied": False, "reason": "no deck id"}
        self.deck_store.delete_deck(deck_id)
        return {"applied": True, "kind": event.kind}

    def _collection_for(self, uid: str) -> int | None:
        """Local id for a collection uid, creating it if this is news.

        An event for a collection we have never heard of is completely
        normal — it was created on the other device — so it is made here
        rather than dropped, which would strand the cards.
        """
        if not uid:
            return None
        existing = self.store.collection_by_uid(uid)
        if existing:
            return existing["collection_id"]
        return self.store.ensure_collection_uid(uid, name="Synced collection")

    # -------------------------------------------------------------- outbound

    def record_stack_delta(self, **kwargs) -> SyncEvent:
        """Log a local quantity change so peers will learn about it."""
        return self.log.record(KIND_STACK_DELTA, stack_delta_event(**kwargs))

    def record_collection_upsert(self, collection: dict) -> SyncEvent:
        return self.log.record(KIND_COLLECTION_UPSERT, {
            "collection_uid": collection.get("collection_uid", ""),
            "name": collection.get("name", ""),
            "kind": collection.get("kind", "collection"),
            "notes": collection.get("notes", ""),
        })

    def record_collection_delete(self, collection_uid: str, *,
                                 discard_cards: bool = False) -> SyncEvent:
        return self.log.record(KIND_COLLECTION_DELETE, {
            "collection_uid": collection_uid,
            "discard_cards": bool(discard_cards),
        })

    def record_deck_upsert(self, deck: dict) -> SyncEvent:
        return self.log.record(KIND_DECK_UPSERT, {
            "deck_id": deck.get("deck_id", ""),
            "name": deck.get("name", ""),
            "format": deck.get("format", ""),
            "notes": deck.get("notes", ""),
            "decklist": deck.get("decklist", {}),
        })

    def record_deck_delete(self, deck_id: str) -> SyncEvent:
        return self.log.record(KIND_DECK_DELETE, {"deck_id": deck_id})
