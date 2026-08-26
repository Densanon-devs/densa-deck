"""Where to point at a card's art.

**Nothing here downloads or stores an image, and nothing ever should.** The
project's licence position is that card images are hotlinked to Scryfall and
never rehosted or cached — see `legal.py` and the WotC/Scryfall notes in
CLAUDE.md. This module exists precisely so there is one obvious place that
does the right thing, rather than a URL built by hand in three UIs.

Scryfall's image CDN has a stable, documented layout::

    https://cards.scryfall.io/<size>/front/<a>/<b>/<scryfall-id>.jpg

where `a` and `b` are the first two characters of the id, used to fan the
files out across directories. That is exactly what the `image_uris` in a card's
own JSON resolve to, so building the URL is the same as reading it — and it
means art works without ingesting the 107k-printing catalogue or storing a URI
per card.

The id is a printing id, not an oracle id. Which printing you own decides which
art you see, and a collection that showed the wrong artwork for the card in
your hand would be worse than showing none.
"""

from __future__ import annotations

# `png` is the only one that is not a .jpg, and it is the transparent-corner
# version — worth having for a card rendered against a coloured background.
_EXTENSION = {"png": "png"}

SIZES = ("small", "normal", "large", "png", "art_crop", "border_crop")

CDN = "https://cards.scryfall.io"


def _looks_like_scryfall_id(printing_id: str) -> bool:
    """A UUID, loosely.

    Only loosely on purpose: the check exists to stop a blank or obviously
    wrong value producing a URL like `.../n/o/none.jpg`, not to validate
    Scryfall's id format on their behalf.
    """
    # str() rather than a type check: this is called from JSON-bridge code on
    # both a desktop and a phone, and a number arriving where an id was
    # expected must produce no art, not an exception on a card screen.
    cleaned = str(printing_id or "").strip().lower()
    return len(cleaned) >= 8 and all(
        c in "0123456789abcdef-" for c in cleaned
    ) and cleaned[0] in "0123456789abcdef"


def card_image_url(printing_id: str, size: str = "normal",
                   face: str = "front") -> str:
    """The Scryfall URL for one printing's art, or "" if it cannot be built.

    Returns an empty string rather than raising: a missing image is a cosmetic
    problem on a screen whose real job is telling you what you own, and it
    must never be able to take that screen down.
    """
    if size not in SIZES:
        size = "normal"
    # str() rather than a type check: this is called from JSON-bridge code on
    # both a desktop and a phone, and a number arriving where an id was
    # expected must produce no art, not an exception on a card screen.
    cleaned = str(printing_id or "").strip().lower()
    if not _looks_like_scryfall_id(cleaned):
        return ""
    if face not in ("front", "back"):
        face = "front"
    return (
        f"{CDN}/{size}/{face}/{cleaned[0]}/{cleaned[1]}/"
        f"{cleaned}.{_EXTENSION.get(size, 'jpg')}"
    )


def card_image_urls(printing_id: str) -> dict[str, str]:
    """Every size, for a caller that wants a thumbnail and a full view.

    Empty when the id is unusable, so `if urls:` is a sufficient check.
    """
    if not _looks_like_scryfall_id(printing_id):
        return {}
    return {size: card_image_url(printing_id, size) for size in SIZES}


def scryfall_page_url(printing_id: str) -> str:
    """Where to send someone who wants rulings, prices and printings.

    Deep-linking to Scryfall is both the polite thing to do when using their
    images and the honest answer to "I want more than this app shows".
    """
    # str() rather than a type check: this is called from JSON-bridge code on
    # both a desktop and a phone, and a number arriving where an id was
    # expected must produce no art, not an exception on a card screen.
    cleaned = str(printing_id or "").strip().lower()
    if not _looks_like_scryfall_id(cleaned):
        return ""
    return f"https://scryfall.com/card/{cleaned}"
