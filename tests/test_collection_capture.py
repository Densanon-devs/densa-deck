"""Camera capture pipeline.

OpenCV is an optional dependency we deliberately do not bundle, so these
tests split in two:

  * Degradation tests always run and assert that every entry point returns a
    clear "unavailable" rather than raising. That is the behaviour customers
    without OpenCV actually get, so it is the behaviour that must be locked.
  * Geometry tests are skipped without OpenCV and verify the detection maths
    against synthetic card images.
"""

from __future__ import annotations

import pytest

from densa_deck.collection.capture import (
    CARD_ASPECT,
    CARD_H,
    CARD_W,
    CameraCapture,
    DetectedCard,
    _aspect_ok,
    crop_region,
    detect_card,
    enhance_for_ocr,
    opencv_available,
    save_image,
)

cv2 = pytest.importorskip("cv2", reason="OpenCV is optional and not bundled") \
    if opencv_available() else None

needs_cv = pytest.mark.skipif(not opencv_available(),
                              reason="OpenCV not installed (optional dependency)")


class TestDegradesWithoutOpenCV:
    """What a customer without OpenCV experiences."""

    def test_detect_returns_a_reason_not_an_exception(self, monkeypatch):
        monkeypatch.setattr("densa_deck.collection.capture.opencv_available",
                            lambda: False)
        result = detect_card(object())
        assert isinstance(result, DetectedCard)
        assert result.found is False
        assert "OpenCV" in result.reason

    def test_enhance_passes_through(self, monkeypatch):
        monkeypatch.setattr("densa_deck.collection.capture.opencv_available",
                            lambda: False)
        sentinel = object()
        assert enhance_for_ocr(sentinel) is sentinel

    def test_save_reports_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr("densa_deck.collection.capture.opencv_available",
                            lambda: False)
        assert save_image(object(), tmp_path / "x.png") is False

    def test_camera_open_reports_false(self, monkeypatch):
        monkeypatch.setattr("densa_deck.collection.capture.opencv_available",
                            lambda: False)
        cam = CameraCapture()
        assert cam.open() is False
        assert cam.read() is None
        cam.close()   # must not raise

    def test_camera_context_manager_is_safe(self, monkeypatch):
        monkeypatch.setattr("densa_deck.collection.capture.opencv_available",
                            lambda: False)
        with CameraCapture() as cam:
            assert cam.read() is None

    def test_crop_handles_none(self):
        assert crop_region(None, (0, 0, 1, 1)) is None


class TestAspectGate:
    """Card proportions are the only thing separating a card from the table
    edge, the monitor, or a playmat — no training data needed."""

    def test_card_proportions_accepted(self):
        assert _aspect_ok(630, 880)

    def test_sideways_card_accepted(self):
        # A card lying on its side is still a card.
        assert _aspect_ok(880, 630)

    def test_square_rejected(self):
        assert not _aspect_ok(500, 500)

    def test_long_rectangle_rejected(self):
        # e.g. a table edge or a monitor bezel
        assert not _aspect_ok(1920, 200)

    def test_degenerate_rejected(self):
        assert not _aspect_ok(0, 100)
        assert not _aspect_ok(100, 0)

    def test_constant_matches_real_card(self):
        # 63mm x 88mm
        assert CARD_ASPECT == pytest.approx(0.7159, abs=0.001)


@needs_cv
class TestDetectionGeometry:
    """Synthetic frames: a bright card-shaped rectangle on a dark background."""

    def _frame(self, w=1280, h=720, card_w=300, card_h=419, angle=0, offset=(0, 0)):
        import cv2 as _cv
        import numpy as np

        frame = np.full((h, w, 3), 30, dtype=np.uint8)
        card = np.full((card_h, card_w, 3), 235, dtype=np.uint8)
        # Some interior structure so edge detection has something real.
        _cv.rectangle(card, (10, 10), (card_w - 10, 60), (40, 40, 40), -1)
        _cv.rectangle(card, (10, card_h - 60), (card_w - 10, card_h - 10),
                      (40, 40, 40), -1)

        cx, cy = w // 2 + offset[0], h // 2 + offset[1]
        x0, y0 = cx - card_w // 2, cy - card_h // 2
        frame[y0:y0 + card_h, x0:x0 + card_w] = card

        if angle:
            m = _cv.getRotationMatrix2D((cx, cy), angle, 1.0)
            frame = _cv.warpAffine(frame, m, (w, h), borderValue=(30, 30, 30))
        return frame

    def test_finds_a_straight_card(self):
        result = detect_card(self._frame())
        assert result.found, result.reason
        assert result.warped is not None
        assert result.warped.shape[:2] == (CARD_H, CARD_W)

    def test_finds_a_rotated_card(self):
        result = detect_card(self._frame(angle=12))
        assert result.found, result.reason

    def test_warp_normalises_size_regardless_of_distance(self):
        near = detect_card(self._frame(card_w=420, card_h=587))
        far = detect_card(self._frame(card_w=220, card_h=307))
        assert near.found and far.found
        assert near.warped.shape == far.warped.shape

    def test_offset_card_still_found(self):
        result = detect_card(self._frame(offset=(-200, 80)))
        assert result.found, result.reason

    def test_rejects_an_empty_scene(self):
        import numpy as np
        result = detect_card(np.full((720, 1280, 3), 30, dtype=np.uint8))
        assert not result.found

    def test_rejects_a_wrong_shaped_rectangle(self):
        # A square is not a card, however large and obvious.
        result = detect_card(self._frame(card_w=400, card_h=400))
        assert not result.found
        assert "quadrilateral" in result.reason

    def test_rejects_a_distant_speck(self):
        result = detect_card(self._frame(card_w=60, card_h=84))
        assert not result.found

    def test_empty_frame_guarded(self):
        import numpy as np
        assert detect_card(np.array([])).found is False

    def test_none_frame_guarded(self):
        assert detect_card(None).found is False

    def test_footer_crop_is_the_bottom_left_corner(self):
        result = detect_card(self._frame())
        assert result.footer is not None
        fh, fw = result.footer.shape[:2]
        # ~3% of the card: small enough that OCR isn't wading through rules text.
        assert (fh * fw) / (CARD_H * CARD_W) < 0.12
        assert fh > 0 and fw > 0

    def _marked_frame(self, angle=0):
        """A card with a uniquely-coloured block exactly where the footer is.

        Size alone doesn't prove the crop is in the right place — a warp that
        rotated or mirrored the card would still yield a correctly-sized crop
        of entirely the wrong region.
        """
        import cv2 as _cv
        import numpy as np

        w, h, card_w, card_h = 1280, 720, 400, 559
        frame = np.full((h, w, 3), 30, dtype=np.uint8)
        card = np.full((card_h, card_w, 3), 235, dtype=np.uint8)
        _cv.rectangle(card, (12, 12), (card_w - 12, 70), (40, 40, 40), -1)
        # Pure blue footer block at the same fractional coordinates the
        # cropper uses (bottom-left, 3%-52% wide, 88.5%-98.5% down).
        fx0, fy0 = int(0.03 * card_w), int(0.885 * card_h)
        fx1, fy1 = int(0.52 * card_w), int(0.985 * card_h)
        _cv.rectangle(card, (fx0, fy0), (fx1, fy1), (255, 0, 0), -1)  # BGR blue

        cx, cy = w // 2, h // 2
        x0, y0 = cx - card_w // 2, cy - card_h // 2
        frame[y0:y0 + card_h, x0:x0 + card_w] = card
        if angle:
            m = _cv.getRotationMatrix2D((cx, cy), angle, 1.0)
            frame = _cv.warpAffine(frame, m, (w, h), borderValue=(30, 30, 30))
        return frame

    def test_footer_crop_lands_on_the_footer(self):
        result = detect_card(self._marked_frame())
        assert result.found
        footer = result.footer
        # Dominated by blue => the crop is over the marked footer region.
        # Not *pure* blue, and deliberately so: FOOTER_BOX carries slack on
        # every side because a perspective warp is never pixel-exact, so the
        # crop always includes some plain card around the text. What matters
        # is that blue leads by a wide margin — a crop that wandered onto
        # another corner would be white, where the channels are equal.
        blue = footer[:, :, 0].mean()
        red = footer[:, :, 2].mean()
        assert blue > 200, f"blue={blue:.0f}"
        assert blue - red > 100, f"blue={blue:.0f} red={red:.0f}"

    def test_footer_crop_survives_rotation(self):
        """A rotated card must still crop the footer, not some other corner."""
        result = detect_card(self._marked_frame(angle=10))
        assert result.found
        blue = result.footer[:, :, 0].mean()
        red = result.footer[:, :, 2].mean()
        assert blue > 180, f"blue={blue:.0f}"
        assert blue - red > 90, f"blue={blue:.0f} red={red:.0f}"

    def test_footer_box_covers_the_printed_footer(self):
        """The crop must contain the whole band the text actually occupies.

        The blue-block tests above prove the crop is in the right *place*;
        this pins that it is not too small. A box that hugged the nominal
        text band exactly would clip the set code away under any warp error,
        which is how a legible card ends up identified only by name.
        """
        from densa_deck.collection.capture import FOOTER_BOX

        x0, y0, x1, y1 = FOOTER_BOX
        # The printed collector/set block, as a fraction of card height.
        assert x0 <= 0.03 and x1 >= 0.52
        assert y0 <= 0.885 and y1 >= 0.985

    def test_title_crop_does_not_overlap_the_footer(self):
        result = detect_card(self._marked_frame())
        title_blue = result.title[:, :, 0].mean()
        # The title band must NOT be sitting on the blue footer block.
        assert title_blue < 200

    def test_title_crop_is_the_top_band(self):
        result = detect_card(self._frame())
        assert result.title is not None
        assert result.title.shape[0] > 0

    def test_enhance_upscales_and_binarises(self):
        result = detect_card(self._frame())
        enhanced = enhance_for_ocr(result.footer)
        assert enhanced.shape[0] > result.footer.shape[0]   # upscaled
        assert set(enhanced.flatten().tolist()) <= {0, 255}  # binary

    def test_enhance_outputs_dark_text_on_light(self):
        # OCR engines expect dark-on-light; card footers are the opposite.
        result = detect_card(self._frame())
        enhanced = enhance_for_ocr(result.footer)
        assert enhanced.mean() >= 127

    def test_save_image_writes(self, tmp_path):
        result = detect_card(self._frame())
        path = tmp_path / "footer.png"
        assert save_image(result.footer, path) is True
        assert path.exists() and path.stat().st_size > 0


class TestFullFrameIsNotACard:
    """A 4:3 photo must never be detected as the card itself.

    When a threshold pass comes back all-white — routine when a dark-bordered
    card sits on a dark desk — findContours returns the image border. A 4:3
    phone photo has an aspect of 0.75, which passes the card test at 0.716,
    so the whole photograph was being "detected" as a card, warped, and its
    footer crop landed on desk. Measured on a rendered photo: detected at
    area_fraction 0.999 with every OCR region coming back empty.
    """

    def test_uniform_four_three_frame_is_rejected(self):
        import numpy as np

        frame = np.full((1500, 2000, 3), 90, dtype=np.uint8)
        assert detect_card(frame).found is False

    def test_a_card_filling_most_of_the_frame_still_detects(self):
        """The guard must not reject someone holding a card up close."""
        import cv2 as _cv
        import numpy as np

        h, w = 1500, 2000
        frame = np.full((h, w, 3), 30, dtype=np.uint8)
        card_h = int(h * 0.92)
        card_w = int(card_h * (63 / 88))
        y0, x0 = (h - card_h) // 2, (w - card_w) // 2
        _cv.rectangle(frame, (x0, y0), (x0 + card_w, y0 + card_h),
                      (238, 238, 238), -1)
        assert detect_card(frame).found is True


@needs_cv
class TestLowContrastCards:
    """A dark card on a dark table — the case brightness cannot see.

    Measured on fifteen real phone frames of a borderless card lying on a
    brown desk, all four greyscale strategies detected the card in ZERO of
    them. Card art is vividly coloured and furniture is not, so saturation
    finds what brightness cannot.
    """

    def _dark_card_on_dark_desk(self):
        import cv2 as _cv
        import numpy as np

        h, w = 1500, 2000
        frame = np.full((h, w, 3), 62, np.uint8)          # dark brown desk
        frame[:, :, 0] = 48
        card_h, card_w = 900, int(900 * (63 / 88))
        y0, x0 = (h - card_h) // 2, (w - card_w) // 2
        # Card face: dark but saturated, the way real art is.
        card = np.zeros((card_h, card_w, 3), np.uint8)
        card[:, :] = (120, 40, 25)
        _cv.rectangle(card, (20, 90), (card_w - 20, 480), (150, 70, 30), -1)
        frame[y0:y0 + card_h, x0:x0 + card_w] = card
        return frame

    def test_dark_card_on_dark_desk_is_found(self):
        result = detect_card(self._dark_card_on_dark_desk())
        assert result.found, result.reason

    def test_plain_desk_is_not_a_card(self):
        import numpy as np

        frame = np.full((1500, 2000, 3), 62, np.uint8)
        frame[:, :, 0] = 48
        assert detect_card(frame).found is False


@needs_cv
class TestOutlineScoring:
    """The most card-like outline wins, not the first one found.

    Strategies disagree in kind: a colour pass can lock onto the art box while
    a brightness pass has the whole card. Stopping at the first hit let a worse
    outline mask a better one, which on real photos turned two exact reads into
    a wrong printing offered for confirmation.
    """

    def test_the_larger_of_two_card_shaped_regions_wins(self):
        import cv2 as _cv
        import numpy as np

        h, w = 1500, 2000
        frame = np.full((h, w, 3), 25, np.uint8)
        card_h, card_w = 1000, int(1000 * (63 / 88))
        y0, x0 = (h - card_h) // 2, (w - card_w) // 2
        _cv.rectangle(frame, (x0, y0), (x0 + card_w, y0 + card_h),
                      (232, 232, 232), -1)
        # An inner panel that is also card-shaped, like an art box.
        iw, ih = int(card_w * 0.6), int(card_h * 0.6)
        _cv.rectangle(frame, (x0 + 30, y0 + 30), (x0 + 30 + iw, y0 + 30 + ih),
                      (40, 40, 40), -1)
        result = detect_card(frame)
        assert result.found
        # The card, not the panel: the panel is 36% of the card's area.
        assert result.area_fraction > (card_w * card_h) / (w * h) * 0.8
