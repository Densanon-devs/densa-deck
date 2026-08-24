"""Finding a card in a camera frame and flattening it.

The pipeline, in order:

    frame -> grayscale -> blur -> edges -> contours
          -> largest 4-sided contour with card-like proportions
          -> perspective warp to a flat 488x680 card
          -> crop the bottom-left corner, where the set code and collector
             number live

That last crop is the point. OCR over a whole card returns rules text, flavor
text, artist credits and a legal line — a haystack. The footer region is
roughly 3% of the card and contains exactly the two fields that identify a
printing uniquely, so OCR-ing it alone is both faster and dramatically more
accurate.

OpenCV is imported lazily and is optional. It is ~50 MB against a 107 MB
installer for a feature most users never touch, and this project has already
shipped four releases broken by PyInstaller bundling problems. Without it,
every function here degrades to a clear "unavailable" rather than an
ImportError at startup.

Card geometry is fixed and known: Magic cards are 63 x 88 mm, an aspect ratio
of ~0.716. That constant is what lets us reject the monitor, the table edge
and the playmat without any training data.
"""

from __future__ import annotations

from dataclasses import dataclass

# Magic card proportions (63mm x 88mm). Used to reject rectangles that are
# the wrong shape to be a card.
CARD_ASPECT = 63.0 / 88.0
ASPECT_TOLERANCE = 0.12

# Warp target. Roughly Scryfall's "normal" image size, so crops line up with
# what people are used to seeing.
CARD_W = 488
CARD_H = 680

# The bottom-left block holding collector number, rarity, set code and
# language. Fractions of the card so it scales with the warp size.
# Widened after a real read returned only the collector-number line and lost
# "DTK * EN" entirely - the set code is half the exact key, so losing it drops
# identification to guessing by name. Perspective warp is never pixel-exact,
# so the crop needs slack on all sides rather than hugging the nominal box.
FOOTER_BOX = (0.01, 0.855, 0.62, 0.995)   # x0, y0, x1, y1
# Title bar, for the name fallback on pre-2015 cards.
TITLE_BOX = (0.03, 0.02, 0.97, 0.135)


@dataclass
class DetectedCard:
    """A located card, ready to OCR."""

    found: bool = False
    reason: str = ""
    corners: list | None = None
    area_fraction: float = 0.0
    warped: object | None = None      # numpy array when OpenCV is present
    footer: object | None = None
    title: object | None = None
    # The same crops taken from the card rotated 180 degrees. Making the card
    # portrait still leaves it possibly upside down, which puts the footer
    # top-right instead of bottom-left. Reading both and letting the parser
    # pick beats guessing which way up someone held it.
    footer_flipped: object | None = None
    title_flipped: object | None = None


def opencv_available() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except Exception:
        return False


def _order_corners(pts):
    """Order 4 points as top-left, top-right, bottom-right, bottom-left.

    Contour points arrive in arbitrary rotation. Without a canonical order the
    perspective transform can flip or rotate the card, which would put the
    footer crop somewhere else entirely.
    """
    import numpy as np

    pts = np.array(pts, dtype="float32").reshape(4, 2)
    ordered = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    ordered[0] = pts[np.argmin(s)]      # top-left has the smallest x+y
    ordered[2] = pts[np.argmax(s)]      # bottom-right the largest
    diff = np.diff(pts, axis=1)
    ordered[1] = pts[np.argmin(diff)]   # top-right has the smallest y-x
    ordered[3] = pts[np.argmax(diff)]
    return ordered


def _upright_corners(ordered):
    """Re-order corners so the card's long edge maps to the warp's height.

    `_order_corners` gives a consistent top-left/top-right/bottom-right/
    bottom-left by screen position, which is not the same as the card's own
    orientation. For a card lying sideways the "top" edge is its long edge, and
    warping that into a portrait target squashes the image and moves the
    footer out from under the footer crop.

    Rotating the sequence by one turns a landscape quad into a portrait one
    without touching a card that is already upright.
    """
    import numpy as np

    top = float(np.linalg.norm(ordered[1] - ordered[0]))
    side = float(np.linalg.norm(ordered[3] - ordered[0]))
    if top > side:
        # Landscape: shift so the current left edge becomes the new top.
        return np.array([ordered[3], ordered[0], ordered[1], ordered[2]],
                        dtype="float32")
    return ordered


def _aspect_ok(w: float, h: float) -> bool:
    if w <= 0 or h <= 0:
        return False
    # Accept either orientation; a sideways card is still a card.
    ratio = min(w, h) / max(w, h)
    return abs(ratio - CARD_ASPECT) <= ASPECT_TOLERANCE


def _edge_variants(gray):
    """Several ways of turning a frame into edges.

    One fixed Canny threshold pair works on a crisp synthetic rectangle and
    falls over on real frames, where the card may be pale-on-pale, blown out
    by glare, or dim and noisy. Each strategy fails differently, so trying a
    few and taking the first plausible card is far more robust than tuning
    one of them harder.
    """
    import cv2
    import numpy as np

    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    kernel = np.ones((3, 3), np.uint8)
    variants = []

    # 1. Fixed thresholds - good on high contrast.
    variants.append(cv2.dilate(cv2.Canny(blurred, 40, 140), kernel, iterations=1))

    # 2. Median-derived thresholds - adapts to overall exposure, which is what
    #    rescues dim and washed-out frames.
    median = float(np.median(blurred))
    lo = int(max(0, 0.66 * median))
    hi = int(min(255, 1.33 * median))
    variants.append(cv2.dilate(cv2.Canny(blurred, lo, hi), kernel, iterations=1))

    # 3. Otsu-thresholded silhouette - finds a card against a similar-toned
    #    desk, where edge detection has almost nothing to grip.
    _, otsu = cv2.threshold(blurred, 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, kernel, iterations=2))
    variants.append(cv2.bitwise_not(variants[-1]))

    # 4. Adaptive threshold - uneven lighting across the frame.
    adaptive = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                     cv2.THRESH_BINARY, 51, 8)
    variants.append(cv2.morphologyEx(cv2.bitwise_not(adaptive), cv2.MORPH_CLOSE,
                                     kernel, iterations=2))
    return variants


def _colour_variants(frame):
    """Edge maps built from colour, for cards that vanish in greyscale.

    Every strategy above works on brightness, and brightness is exactly what
    a dark-bordered card lying on a dark wooden table does not have. Measured
    on fifteen real phone frames of a borderless card on a brown desk, all
    four greyscale strategies found the card in ZERO of them.

    Card art is vividly coloured and desks are not, so saturation separates
    them cleanly where brightness cannot: the same fifteen frames went to
    thirteen. This runs last, so high-contrast scenes still take the cheaper
    greyscale path and behave exactly as before.
    """
    import cv2
    import numpy as np

    if frame is None or frame.ndim != 3:
        return []

    saturation = cv2.GaussianBlur(
        cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 1], (7, 7), 0)
    kernel = np.ones((5, 5), np.uint8)
    variants = []
    for channel in (saturation,
                    cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
                    .apply(saturation)):
        _, mask = cv2.threshold(channel, 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel,
                                         iterations=3))
    return variants


# A contour this close to the whole frame is the frame, not a card. When a
# threshold pass comes back all-white — routine when a dark-bordered card sits
# on a dark desk — findContours returns the image border, and a 4:3 phone photo
# has an aspect of 0.75, which sails through the card-aspect test at 0.716.
# The result is a "detected card" that is the entire photograph, warped, with
# the footer crop landing on desk. Measured: without this, a realistic frame
# detected at area_fraction 0.999 and every OCR region came back empty.
MAX_AREA_FRACTION = 0.97

# How completely the shape must fill its bounding rectangle. Measured against
# its CONVEX HULL rather than the raw contour, because a real mask of a real
# card is notched: the card's own text box is unsaturated and its border is
# dark, so both colour and brightness masks come back with bites taken out of
# them. Four of fifteen real frames held a perfectly good card that was thrown
# away for filling only 0.58-0.68 of its rectangle. A card is convex, so its
# hull fills the rectangle almost exactly and this can be strict; an L-shaped
# blob's hull does not, which is the case the check exists to reject.
HULL_FILL_RATIO = 0.90

# Rectangularity scores within this of each other count as equally card-like,
# and the larger outline wins. Without a tie band the comparison turns into
# noise-chasing: masks from different strategies trace the same card to within
# a pixel or two and score fractionally apart for no meaningful reason.
SCORE_TIE = 0.02


def _quad_from_contour(contour, frame_area, min_area_fraction):
    """A card-shaped quadrilateral from a contour, or None.

    Works on the contour's convex hull throughout. Real cards have rounded
    corners and soft edges, and real masks of them are ragged, so `approxPolyDP`
    on the raw contour frequently returns five to eight points for a perfectly
    good card. The hull is the shape we actually mean.
    """
    quad, _ = _scored_quad(contour, frame_area, min_area_fraction)
    return quad


def _scored_quad(contour, frame_area, min_area_fraction):
    """A card-shaped quad and how card-like it is, or (None, 0).

    The score is how completely the hull fills its bounding rectangle. A real
    card is a rectangle, so a correct outline scores near 1.0, while a partial
    region — the art box, a card half-behind a thumb — scores lower. That
    difference is what lets the caller compare outlines found by different
    strategies instead of trusting whichever ran first.
    """
    import cv2

    hull = cv2.convexHull(contour)
    area = cv2.contourArea(hull)
    fraction = area / frame_area
    if fraction < min_area_fraction or fraction > MAX_AREA_FRACTION:
        return None, 0.0

    rect = cv2.minAreaRect(hull)
    (rw, rh) = rect[1]
    if not _aspect_ok(rw, rh):
        return None, 0.0

    rect_area = rw * rh
    if rect_area <= 0:
        return None, 0.0
    fill = area / rect_area
    if fill < HULL_FILL_RATIO:
        return None, 0.0

    peri = cv2.arcLength(hull, True)
    for eps in (0.02, 0.03, 0.05):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            return approx.reshape(4, 2).astype("float32"), fill
    return cv2.boxPoints(rect).astype("float32"), fill


def detect_card(frame, *, min_area_fraction: float = 0.03) -> DetectedCard:
    """Locate the largest card-shaped quadrilateral in a frame.

    `min_area_fraction` rejects distant clutter - a card held up to a camera
    fills a decent share of the frame, so anything tiny is background.
    """
    if not opencv_available():
        return DetectedCard(found=False, reason="OpenCV not installed")

    import cv2
    import numpy as np

    if frame is None or getattr(frame, "size", 0) == 0:
        return DetectedCard(found=False, reason="empty frame")

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    frame_area = float(gray.shape[0] * gray.shape[1])

    # Every strategy is tried and the most card-like outline wins, rather than
    # the first strategy that finds anything. Strategies disagree in kind, not
    # just in luck: a colour pass can lock onto the art box while a brightness
    # pass has the whole card, and stopping at the first hit meant a worse
    # outline could mask a better one. Measured on real photos, first-wins
    # turned two exact identifications into a wrong candidate offered for
    # confirmation.
    best = None
    best_score = 0.0
    best_area = 0.0
    for edges in [*_edge_variants(gray), *_colour_variants(frame)]:
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
            quad, score = _scored_quad(contour, frame_area, min_area_fraction)
            if quad is None:
                continue
            area = cv2.contourArea(cv2.convexHull(contour))
            # Rectangularity first, area only to break near-ties: a card and
            # its own art box can both look rectangular, and the card is the
            # larger of the two.
            clearly_better = score > best_score + SCORE_TIE
            near_tie = abs(score - best_score) <= SCORE_TIE
            if best is None or clearly_better or (near_tie and area > best_area):
                best, best_score, best_area = quad, score, area

    if best is None:
        return DetectedCard(found=False, reason="no card-shaped quadrilateral")

    corners = _order_corners(best)
    # A card photographed on its side is extremely common - people lay a card
    # down and hold the phone in whatever orientation is comfortable. Warping
    # a landscape quad into a portrait target would both squash the image and,
    # worse, put the footer crop somewhere in the artwork. Rotate the corner
    # order so the card's long edge always becomes the height.
    corners = _upright_corners(corners)
    dst = np.array([[0, 0], [CARD_W - 1, 0],
                    [CARD_W - 1, CARD_H - 1], [0, CARD_H - 1]], dtype="float32")
    matrix = cv2.getPerspectiveTransform(corners, dst)
    warped = cv2.warpPerspective(frame, matrix, (CARD_W, CARD_H))

    flipped = cv2.rotate(warped, cv2.ROTATE_180)
    return DetectedCard(
        found=True,
        corners=corners.tolist(),
        area_fraction=best_area / frame_area,
        warped=warped,
        footer=crop_region(warped, FOOTER_BOX),
        title=crop_region(warped, TITLE_BOX),
        footer_flipped=crop_region(flipped, FOOTER_BOX),
        title_flipped=crop_region(flipped, TITLE_BOX),
    )


def crop_region(warped, box) -> object | None:
    """Crop a fractional box out of a flattened card."""
    if warped is None:
        return None
    h, w = warped.shape[:2]
    x0, y0, x1, y1 = box
    return warped[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


def _upscaled_gray(image):
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)


def enhance_for_ocr(image):
    """Upscale and threshold a crop to make small print readable.

    The footer text is a few pixels tall in a webcam frame. Upscaling before
    thresholding gives OCR meaningfully more to work with; Otsu picks the
    threshold so this works on both black-bordered and white-bordered cards
    without a hand-tuned constant.

    This is the single best-guess rendering. `enhance_variants` is what the
    capture path actually uses, because no single rendering wins everywhere.
    """
    if not opencv_available() or image is None:
        return image
    import cv2

    scaled = _upscaled_gray(image)
    _, binary = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Card footers are light-on-dark; OCR engines expect dark-on-light.
    if binary.mean() < 127:
        binary = cv2.bitwise_not(binary)
    return binary


def enhance_variants(image) -> list:
    """Several renderings of one crop, for OCR to try in turn.

    Global Otsu — the obvious choice, and what this used to do alone — has a
    specific failure that is not rare: when a crop contains both a
    high-contrast band (the bottom edge of the art box, which the crop needs
    slack to include) and low-contrast text, Otsu spends its single threshold
    separating the band from everything else and flattens the text into the
    background. Measured on a warped card, Otsu and adaptive thresholding both
    returned nothing while CLAHE and a min-max stretch both read the collector
    number cleanly.

    So render the crop several ways and let OCR vote. Each pass costs ~15ms
    against Windows OCR, which is affordable next to a 1.3s scan interval.
    Ordered best-first so a caller that stops early still gets the strongest
    rendering.
    """
    if not opencv_available() or image is None:
        return []
    import cv2

    scaled = _upscaled_gray(image)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(scaled)

    def _positive(binary):
        """OCR engines expect dark text on light ground."""
        return cv2.bitwise_not(binary) if binary.mean() < 127 else binary

    _, otsu = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    stretched = cv2.normalize(scaled, None, 0, 255, cv2.NORM_MINMAX)
    return [
        clahe,                          # local contrast: the general winner
        _positive(otsu),                # crisp, high-contrast footers
        cv2.bitwise_not(stretched),     # light-on-dark that CLAHE misses
    ]


def save_image(image, path) -> bool:
    if not opencv_available() or image is None:
        return False
    import cv2
    return bool(cv2.imwrite(str(path), image))


class CameraCapture:
    """A webcam, opened lazily and closed deterministically.

    Kept as a context manager because leaving a camera handle open is the
    kind of resource leak that ends with the device unusable until reboot.
    """

    def __init__(self, index: int = 0):
        self.index = index
        self._cap = None

    def open(self) -> bool:
        if not opencv_available():
            return False
        import cv2
        self._cap = cv2.VideoCapture(self.index)
        if not self._cap.isOpened():
            self._cap = None
            return False
        # Ask for a high resolution: footer text is tiny, and a 640x480 frame
        # rarely carries enough pixels in the corner to read a set code.
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        return True

    def read(self):
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        return frame if ok else None

    def close(self):
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def read_regions(regions, ocr_backend) -> str:
    """OCR a set of named crops and return every distinct line found.

    `regions` is an iterable of (label, image). Each crop is rendered several
    ways (see `enhance_variants`) and every rendering is read, because the
    rendering that works varies with the card's border, the lighting and how
    much of the art box the warp swept into the crop.

    Duplicate lines are dropped but nothing else is: the parser downstream is
    pattern-driven and copes fine with extra noise, whereas a line thrown away
    here is gone for good. Order is preserved so the title still leads.
    """
    import tempfile
    from pathlib import Path

    lines: list[str] = []
    seen: set[str] = set()
    with tempfile.TemporaryDirectory() as tmp:
        for label, region in regions:
            if region is None:
                continue
            for index, rendering in enumerate(enhance_variants(region)):
                path = Path(tmp) / f"{label}_{index}.png"
                if not save_image(rendering, path):
                    continue
                for line in (ocr_backend.read_text(path) or "").splitlines():
                    line = line.strip()
                    key = line.casefold()
                    if line and key not in seen:
                        seen.add(key)
                        lines.append(line)
    return "\n".join(lines)


def capture_and_read(camera: CameraCapture, ocr_backend) -> dict:
    """One frame -> detected card -> OCR text, ready for `identify_card`.

    Reads the footer and the title separately and concatenates them, because
    that is the shape `parse_card_footer` and the name search expect: name
    first, then the corner block.
    """
    frame = camera.read()
    if frame is None:
        return {"ok": False, "reason": "no frame from camera"}

    detected = detect_card(frame)
    if not detected.found:
        return {"ok": False, "reason": detected.reason}

    text = read_regions(
        (("title", detected.title), ("footer", detected.footer),
         ("title_flipped", detected.title_flipped),
         ("footer_flipped", detected.footer_flipped)),
        ocr_backend,
    )
    return {
        "ok": True,
        "text": text,
        "area_fraction": detected.area_fraction,
        "corners": detected.corners,
    }
