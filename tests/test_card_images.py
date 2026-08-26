"""Pointing at card art without ever holding a copy of it.

The licence position this project works under is that card images are
hotlinked to Scryfall and never rehosted, cached or served from here. So this
module builds URLs and does nothing else, and the tests are mostly about the
ways a URL can be wrong in a way nobody notices — a blank id producing
`.../n/o/none.jpg`, or a screen showing the art of a printing you do not own.
"""

from __future__ import annotations

from densa_deck.data.images import (
    SIZES,
    card_image_url,
    card_image_urls,
    scryfall_page_url,
)

# A real Scryfall printing id: Death Wind, DTK #95.
DEATH_WIND = "87ed0a14-1a98-4190-b195-f84fa42d4364"


class TestTheUrlItBuilds:
    def test_it_matches_scryfall_s_documented_layout(self):
        """Verified against the live CDN: 200, image/jpeg, 116 KB.

        The two directory levels are the first two characters of the id.
        Getting that wrong gives a 404 on every card at once, which at least
        fails loudly — getting it subtly wrong would not.
        """
        assert card_image_url(DEATH_WIND) == (
            "https://cards.scryfall.io/normal/front/8/7/"
            "87ed0a14-1a98-4190-b195-f84fa42d4364.jpg"
        )

    def test_png_is_the_one_that_is_not_a_jpg(self):
        assert card_image_url(DEATH_WIND, "png").endswith(".png")
        for size in SIZES:
            if size != "png":
                assert card_image_url(DEATH_WIND, size).endswith(".jpg"), size

    def test_every_size_is_offered(self):
        urls = card_image_urls(DEATH_WIND)
        assert set(urls) == set(SIZES)
        assert all(url.startswith("https://cards.scryfall.io/") for url in urls.values())

    def test_an_unknown_size_falls_back_rather_than_404ing(self):
        # A typo in a caller should show the card, not a broken image.
        assert "/normal/" in card_image_url(DEATH_WIND, "enormous")

    def test_an_uppercase_id_still_resolves(self):
        # The CDN paths are lowercase; an id that arrived capitalised would
        # otherwise 404 for that one card and nobody would know why.
        assert card_image_url(DEATH_WIND.upper()) == card_image_url(DEATH_WIND)


class TestRefusingToGuess:
    def test_a_missing_id_gives_nothing_rather_than_a_broken_url(self):
        # `.../n/o/none.jpg` renders as a broken image with no explanation.
        # An empty string lets the UI say "no art for this printing".
        assert card_image_url("") == ""
        assert card_image_url("   ") == ""
        assert card_image_urls("") == {}

    def test_something_that_is_not_an_id_gives_nothing(self):
        assert card_image_url("none") == ""
        assert card_image_url("Death Wind") == ""
        assert card_image_url("../../etc/passwd") == ""

    def test_it_never_raises(self):
        """A missing image must not be able to take down a screen whose real
        job is telling you what you own."""
        for bad in ["", None, 12345, "x", "-", "zzzz-zzzz"]:
            assert card_image_url(bad) == "" or isinstance(card_image_url(bad), str)


class TestSendingPeopleToScryfall:
    def test_it_deep_links_to_the_printing(self):
        # Both the polite thing to do when using their images and the honest
        # answer to "I want rulings and every printing".
        assert scryfall_page_url(DEATH_WIND) == f"https://scryfall.com/card/{DEATH_WIND}"

    def test_a_bad_id_links_nowhere_rather_than_to_a_404(self):
        assert scryfall_page_url("") == ""
        assert scryfall_page_url("not-an-id-at-all!") == ""


class TestNothingIsStored:
    def test_the_module_does_not_fetch_or_write_anything(self):
        """The licence position, held by reading the source.

        Any import of httpx, requests, urllib or pathlib here would mean
        something had started downloading or caching art, which is exactly
        what must not happen.
        """
        from pathlib import Path

        source = (Path(__file__).parent.parent / "src" / "densa_deck" /
                  "data" / "images.py").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines()
            if not line.strip().startswith(("#", '"', "*"))
        )
        for forbidden in ["httpx", "requests", "urllib", "open(", "Path("]:
            assert forbidden not in code, f"images.py should not use {forbidden}"
