"""Turn the Densa Deck logo into app icons that survive being masked.

The logo is a wide swoosh on an opaque white square: 753x520 of artwork in a
1024 canvas, with dead space above and below and the interesting part running
edge to edge. That is a fine logo and a bad icon, for three reasons:

* **Android masks it.** From Android 8 every launcher crops the icon to its
  own shape — circle, squircle, rounded square, teardrop. Only the middle
  66/108 of the canvas is guaranteed to survive. A logo that fills the width
  loses both ends of the swoosh.
* **There was no adaptive icon at all.** No `mipmap-anydpi-v26`, so the
  launcher took the legacy square, shrank it, and dropped it on a grey plate.
  That is the generic-looking badge, and no amount of redrawing the PNG fixes
  it — the fix is shipping the two-layer icon Android asks for.
* **The white is opaque, not transparent.** So it cannot sit on anything.

What this does about it: floods the background white to transparent from the
EDGES (never a global colour key — the card faces are white too, and keying
would punch holes through the middle of the art), trims to the ink, and lays
the result on a square canvas scaled to fit inside the guaranteed-safe circle
rather than the canvas. Then it writes every size Android, Windows and Expo
each want.

Run:  python scripts/make_icons.py [--preview]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "densa-deck-logo-square.png"
RES = ROOT / "companion" / "android" / "app" / "src" / "main" / "res"
BUILT = ROOT / "assets" / "icons"

# The app's own dark, and the red the logo already uses.
INK_BACKDROP = (247, 247, 249, 255)

# Android's adaptive icon is a 108dp canvas of which the middle 72dp is drawn
# and only the middle 66dp is guaranteed by every mask. Foreground art sized
# to the inscribed rectangle of that circle cannot be clipped by any launcher.
ADAPTIVE_DP = 108
SAFE_DP = 66

DENSITIES = {           # folder suffix -> scale factor against mdpi
    "mdpi": 1,
    "hdpi": 1.5,
    "xhdpi": 2,
    "xxhdpi": 3,
    "xxxhdpi": 4,
}


def load_logo() -> Image.Image:
    """The artwork, background knocked out and cropped to the ink."""
    im = Image.open(SOURCE).convert("RGBA")
    im = _flood_background_to_alpha(im)
    return _trim(im)


def _flood_background_to_alpha(im: Image.Image, tolerance: int = 26) -> Image.Image:
    """Make the OUTSIDE white transparent, and nothing else.

    A global "white becomes transparent" would eat the card faces, which are
    also white, and leave the art full of holes. So this floods inward from
    the border: only white connected to the edge goes, which is exactly what
    "the background" means.
    """
    flat = im.convert("RGB")
    width, height = flat.size
    # ImageDraw.floodfill works on the image it is given; fill the background
    # with a colour that cannot occur in the art, then key on that.
    marker = (1, 254, 1)
    for seed in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        ImageDraw.floodfill(flat, seed, marker, thresh=tolerance)

    pixels = flat.load()
    alpha = im.getchannel("A").load()
    out = im.copy()
    target = out.load()
    for y in range(height):
        for x in range(width):
            if pixels[x, y] == marker:
                target[x, y] = (0, 0, 0, 0)
            else:
                r, g, b, _ = out.getpixel((x, y))
                target[x, y] = (r, g, b, alpha[x, y])
    return out


def _trim(im: Image.Image, keep: float = 0.995) -> Image.Image:
    """Crop to the artwork, ignoring the very faintest fringe.

    The swoosh tapers to a wisp at both ends. Cropping to the absolute bounding
    box makes those wisps define the size of everything, so the substantial
    part of the logo ends up smaller than it needs to be. Trimming to where
    the ink actually is gives a denser subject at the same safe size.
    """
    alpha = im.getchannel("A")
    box = alpha.getbbox()
    if box is None:
        return im
    if keep >= 1.0:
        return im.crop(box)

    # Column and row ink mass, measured on the ALPHA and applied to the IMAGE.
    # Keeping those two straight matters: cropping the alpha and returning it
    # hands back a silhouette, and every icon comes out a white ghost of the
    # artwork with the red and black gone.
    art = im.crop(box)
    mask = alpha.crop(box)
    width, height = mask.size
    cols = [sum(mask.crop((x, 0, x + 1, height)).getdata()) for x in range(width)]
    rows = [sum(mask.crop((0, y, width, y + 1)).getdata()) for y in range(height)]

    def _span(mass: list[int]) -> tuple[int, int]:
        total = sum(mass) or 1
        edge = (1.0 - keep) / 2.0 * total
        run, low = 0, 0
        for i, value in enumerate(mass):
            run += value
            if run >= edge:
                low = i
                break
        run, high = 0, len(mass) - 1
        for i in range(len(mass) - 1, -1, -1):
            run += mass[i]
            if run >= edge:
                high = i
                break
        return low, max(high, low + 1)

    x0, x1 = _span(cols)
    y0, y1 = _span(rows)
    return art.crop((x0, y0, x1 + 1, y1 + 1))


def widest_safe_width(logo: Image.Image, circle_frac: float,
                      canvas: int = 512, tolerance: float = 0.002) -> float:
    """How wide the art can be before a circular mask starts eating it.

    Inscribing the bounding box in the safe circle is the textbook answer and
    it is far too timid here, because this logo does not FILL its bounding
    box — it is a wide blob with empty corners, and the rule sizes everything
    off corners that hold no ink. The result is a correct icon that looks
    lost in a sea of white.

    So this measures instead of assuming: scale up, mask with the safe circle,
    and count the ink that did not survive. Take the largest size that loses
    essentially none. Slower than a formula and right about this particular
    artwork rather than about rectangles in general.
    """
    ratio = logo.width / logo.height
    mask = Image.new("L", (canvas, canvas), 0)
    inset = canvas * (1 - circle_frac) / 2
    ImageDraw.Draw(mask).ellipse(
        (inset, inset, canvas - inset - 1, canvas - inset - 1), fill=255)

    best = circle_frac * ratio / ((ratio ** 2 + 1) ** 0.5)      # the timid one
    step = 0.01
    trial = best
    while trial + step <= 0.98:
        candidate = trial + step
        width = round(canvas * candidate)
        height = max(1, round(width / ratio))
        art = logo.resize((width, height), Image.LANCZOS)
        layer = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        layer.paste(art, ((canvas - width) // 2, (canvas - height) // 2), art)

        ink = layer.getchannel("A")
        before = sum(ink.getdata())
        kept = Image.new("L", (canvas, canvas), 0)
        kept.paste(ink, (0, 0), mask)
        after = sum(kept.getdata())
        if before and (before - after) / before > tolerance:
            break
        trial = candidate
    return trial


def _fit(logo: Image.Image, canvas: int, width_frac: float) -> Image.Image:
    """Place the logo on a square canvas at a given fraction of its width."""
    ratio = logo.width / logo.height
    width = max(1, round(canvas * width_frac))
    height = max(1, round(width / ratio))
    scaled = logo.resize((width, height), Image.LANCZOS)

    out = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    out.paste(scaled, ((canvas - scaled.width) // 2,
                       (canvas - scaled.height) // 2), scaled)
    return out


def _on_backdrop(fg: Image.Image, colour=INK_BACKDROP) -> Image.Image:
    plate = Image.new("RGBA", fg.size, colour)
    plate.alpha_composite(fg)
    return plate


def _rounded(im: Image.Image, radius_frac: float = 0.22) -> Image.Image:
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, im.size[0] - 1, im.size[1] - 1),
        radius=int(im.size[0] * radius_frac), fill=255)
    out = im.copy()
    out.putalpha(mask)
    return out


def _circle(im: Image.Image) -> Image.Image:
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, im.size[0] - 1, im.size[1] - 1), fill=255)
    out = im.copy()
    out.putalpha(mask)
    return out


def build(preview: bool = False) -> None:
    logo = load_logo()
    print(f"artwork after trim: {logo.width}x{logo.height}")

    # Measured once, against the guaranteed-safe circle, and reused for every
    # density so all five are the same icon at different resolutions.
    adaptive_width = widest_safe_width(logo, SAFE_DP / ADAPTIVE_DP)
    print(f"foreground width: {adaptive_width:.0%} of the 108dp canvas"
          f" (safe circle is {SAFE_DP}/{ADAPTIVE_DP})")

    BUILT.mkdir(parents=True, exist_ok=True)

    # ---- Android adaptive: two layers, 108dp, art inside the safe circle ----
    safe = SAFE_DP / ADAPTIVE_DP
    for suffix, scale in DENSITIES.items():
        canvas = round(ADAPTIVE_DP * scale)
        folder = RES / f"mipmap-{suffix}"
        folder.mkdir(parents=True, exist_ok=True)

        fg = _fit(logo, canvas, adaptive_width)
        fg.save(folder / "ic_launcher_foreground.png")

        # Legacy icons, for anything older than Android 8. 48dp at mdpi.
        legacy = round(48 * scale)
        plate = _on_backdrop(_fit(logo, legacy, 0.86))
        _rounded(plate).save(folder / "ic_launcher.png")
        _circle(plate).save(folder / "ic_launcher_round.png")

        # The .webp the Expo template shipped would collide with these names.
        for stale in (folder / "ic_launcher.webp",
                      folder / "ic_launcher_round.webp"):
            if stale.exists():
                stale.unlink()

    _write_adaptive_xml()
    print(f"android: foreground + legacy icons in {len(DENSITIES)} densities")

    # ---- Expo's own assets, for a future prebuild ------------------------
    expo = ROOT / "companion" / "assets"
    expo.mkdir(parents=True, exist_ok=True)
    _on_backdrop(_fit(logo, 1024, 0.86)).convert("RGB").save(expo / "icon.png")
    _fit(logo, 1024, adaptive_width).save(expo / "adaptive-icon.png")
    print("expo: icon.png + adaptive-icon.png")

    # ---- Windows .ico for the desktop app --------------------------------
    master = _on_backdrop(_fit(logo, 256, 0.86))
    master.save(ROOT / "assets" / "densa-deck.ico",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                       (64, 64), (128, 128), (256, 256)])
    print("windows: densa-deck.ico")

    if preview:
        _write_preview(logo, adaptive_width)


def _write_adaptive_xml() -> None:
    """The two-layer icon Android has wanted since 8.0.

    Without this the launcher takes the legacy square, shrinks it and drops it
    on a plate of its own choosing — which is the generic badge look, and is
    not fixable by editing the PNG.
    """
    anydpi = RES / "mipmap-anydpi-v26"
    anydpi.mkdir(parents=True, exist_ok=True)
    body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/iconBackground"/>\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>\n'
        '    <monochrome android:drawable="@mipmap/ic_launcher_foreground"/>\n'
        '</adaptive-icon>\n'
    )
    (anydpi / "ic_launcher.xml").write_text(body, encoding="utf-8")
    (anydpi / "ic_launcher_round.xml").write_text(body, encoding="utf-8")

    # A colour, not a bitmap: the background layer is scaled and parallaxed by
    # the launcher, and a flat colour survives that perfectly at any size.
    colours = RES / "values" / "colors.xml"
    existing = colours.read_text(encoding="utf-8") if colours.exists() else (
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n</resources>\n')
    if "iconBackground" not in existing:
        existing = existing.replace(
            "</resources>",
            '  <color name="iconBackground">#F7F7F9</color>\n</resources>')
        colours.write_text(existing, encoding="utf-8")


def _write_preview(logo: Image.Image, width_frac: float) -> None:
    """What it looks like under each mask, side by side, at launcher size."""
    canvas, pad = 216, 24
    shots = []
    plate = _on_backdrop(_fit(logo, canvas, width_frac))
    shots.append(("circle", _circle(plate)))
    shots.append(("squircle", _rounded(plate, 0.30)))
    shots.append(("rounded", _rounded(plate, 0.18)))
    shots.append(("legacy", _rounded(
        _on_backdrop(_fit(logo, canvas, 0.86)), 0.22)))

    strip = Image.new("RGBA",
                      (len(shots) * (canvas + pad) + pad, canvas + 2 * pad),
                      (24, 26, 34, 255))
    for i, (_name, shot) in enumerate(shots):
        strip.paste(shot, (pad + i * (canvas + pad), pad), shot)
    out = BUILT / "icon-preview.png"
    strip.save(out)
    print(f"preview: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true",
                        help="Also write a strip showing every launcher mask")
    build(**vars(parser.parse_args()))
