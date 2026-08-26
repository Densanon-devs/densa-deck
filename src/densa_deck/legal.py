"""Legal disclaimers, attribution, and compliance constants."""

DISCLAIMER = (
    "This tool is not affiliated with or endorsed by Wizards of the Coast. "
    "Magic: The Gathering and its logos are trademarks of Wizards of the Coast LLC. "
    "Card data provided by Scryfall (https://scryfall.com)."
)

ATTRIBUTION = "Card data provided by Scryfall — https://scryfall.com"

SCRYFALL_IMAGE_BASE = "https://cards.scryfall.io"


def scryfall_image_url(scryfall_id: str, face: str = "front", size: str = "normal") -> str:
    """Build a Scryfall hotlink URL for a card image. Never host images locally.

    Delegates to `data.images`, which is the single implementation. This used
    to build the path itself and had two faults worth naming, since both were
    invisible until you hit them:

      * it appended `.jpg` for every size, including `png` — the one size that
        is not a jpg, and the one you want for a card on a coloured background;
      * it indexed `scryfall_id[0]` with no check, so an empty id raised
        IndexError rather than producing no image. Every call site had grown a
        try/except to survive that.

    Args:
        scryfall_id: The Scryfall UUID for the card.
        face: 'front' or 'back' for DFCs.
        size: 'small', 'normal', 'large', 'png', 'art_crop', 'border_crop'.

    Returns:
        The URL, or "" when the id is unusable.
    """
    from densa_deck.data.images import card_image_url

    return card_image_url(scryfall_id, size=size, face=face)
