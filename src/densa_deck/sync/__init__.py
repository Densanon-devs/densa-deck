"""Two-way sync between the desktop and a companion device.

The problem this solves is not "copy the database over". Both devices are
edited while apart — you add a box of cards on the phone at a shop while the
PC is off, and the PC has meanwhile had a deck rebuilt — and both sets of
edits have to survive the reunion. A last-write-wins copy of the collection
would silently destroy one side's work, which for an inventory of physical
property is the worst possible failure.

## The shape that makes this safe

Quantities are never sent as totals. They are sent as **deltas**, and deltas
commute: +2 on the phone and +3 on the desktop merge to +5 regardless of which
arrives first, with no conflict to resolve and nothing to lose. That is the
whole trick, and the collection already had the right shape for it — an
append-only event log was written from the start for cost-basis work.

Things that are genuinely documents rather than counters — a collection's
name, a deck's contents — use last-write-wins on a UTC timestamp. Losing a
rename is survivable; losing cards is not.

## Identity across devices

Nothing that crosses the wire is a local autoincrement id, because two devices
offline both mint id 2 and then disagree about what it means. Instead:

  * stacks are addressed by their **natural key** — printing, finish,
    condition, language, location, collection — which both devices derive
    independently and agree on without coordination;
  * collections and decks carry a **UUID** minted at creation;
  * every event carries a UUID, so applying the same event twice is a no-op
    and a retried or duplicated sync cannot double-count anything.

## What a sync exchange looks like

    phone                                   desktop
      |  GET  /sync/pull?since=<cursor>  ->   |
      |  <- events the desktop has and phone lacks
      |  POST /sync/push  [events]       ->   |
      |  <- accepted, new cursor              |

Each side keeps a watermark per peer. There is no central clock, no leader,
and no requirement that both sides were ever online at the same time before.
"""

from densa_deck.sync.log import (
    DEVICE_ID_FILE,
    SyncEvent,
    SyncLog,
    device_id,
)

__all__ = ["SyncEvent", "SyncLog", "device_id", "DEVICE_ID_FILE"]
