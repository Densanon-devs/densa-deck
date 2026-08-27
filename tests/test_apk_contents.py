"""What actually ships in the APK.

Every other test in this repo checks a decision. This one checks *assembly*,
and it exists because the two are genuinely different: the deck builder, the
card search, the scanner and the wishlist were all fully implemented and fully
tested while being **absent from the built app**. Metro bundles outward from
the entry point, so a screen nothing navigates to is silently left out, and
nothing that tests logic can notice.

Two traps this had to learn the hard way:

  * A **debug** APK contains no JavaScript at all — it fetches it from a dev
    server at launch. Installing one on a phone away from the PC gives a red
    error screen.
  * The bundle is **Hermes bytecode**, and Hermes keeps any string containing
    a non-ASCII character in a UTF-16 table. Searching UTF-8 alone reports a
    string as missing when it is present, which is how a false alarm looks.

Skipped when no APK has been built, so the suite still runs on a clean tree.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

APK = (Path(__file__).parent.parent / "companion" / "android" / "app" /
       "build" / "outputs" / "apk" / "release" / "app-release.apk")

# One string from each screen, chosen to be something a user would see. If a
# screen stops being reachable from the entry point, its strings leave the
# bundle and the matching test fails.
SCREENS = {
    "pairing": "Scan from your phone",
    "collection browsing": "Search your collection",
    "deck list": "New deck name",
    "deck editing": "Still needed",
    "sideboard panel": "Sideboard (",
    "card browser": "Browse cards",
    "browser filters": "Only mine",
    "rules text search": "Rules text",
    "analyst": "Analyse on my PC",
    "wishlist": "part of your collection",
    "scanner": "Point at a card",
    "camera permission": "Camera access needed",
    "crash reporting": "Something broke",
    "build stamp": "Densa Deck companion",
    "pairing progress": "Read a code",
    "continuous scanning": "Auto scan",
    "camera controls": "Torch off",
    "connection diagnostics": "Nothing answered",
    "making a collection": "New collection",
    "scan target picker": "Scanning into",
    "connection strip": "Connected over",
    "undo a bad scan": "Wrong? Undo",
    "taking out of a list": "Take out of list",
    "confirmed delete": "Yes, delete",
    "card detail": "Rulings and printings",
    "overlaps tab": "In more than one list",
    "catalogue on wishlist": "Search every card in Magic",
    "list membership": "In these lists",
    "deleting a collection": "Delete this collection",
    "scan diagnosis": "but matched nothing",
    "offline art": "Download all card art",
    "art diagnostics": "Card art",
}

# Behaviour that has to survive into the shipped app, not merely exist in src.
BEHAVIOUR = {
    "LAN/tunnel failover": "isTunnelAddr",
    "stale LAN self-heal": "healedLanHost",
    "catalogue search route": "cards/search",
    # Scryfall answer HTTP 400 to the okhttp User-Agent React Native sends
    # by default, so without this EVERY card image fails while the same
    # URL works in a browser. Reproduced live: okhttp -> 400, DensaDeck -> 200.
    "art User-Agent": "(companion; Android)",
    "sync push route": "sync/push",
    # The camera crashed the app because nothing ever asked for it, and the
    # crash reported nothing because nothing was catching. Both fixes are
    # facts about the shipped binary, not about the source tree.
    "camera permission request": "useCameraPermissions",
    # A label only this app uses; "setGlobalHandler" would pass on
    # React Native's own copy whether or not the trap shipped.
    "global error trap": "opening the collection",
}


def _bundle() -> bytes:
    if not APK.exists():
        pytest.skip("no release APK built (companion/android ./gradlew assembleRelease)")
    with zipfile.ZipFile(APK) as archive:
        names = archive.namelist()
        if "assets/index.android.bundle" not in names:
            pytest.fail(
                "The APK carries no JavaScript bundle. That is what a DEBUG "
                "build looks like: it loads its code from a dev server at "
                "launch, so on a phone away from the PC it shows a red error "
                "screen. Build with assembleRelease."
            )
        return archive.read("assets/index.android.bundle")


def _present(bundle: bytes, text: str) -> bool:
    """Search both encodings.

    Hermes stores strings containing non-ASCII in a UTF-16 table, so a UTF-8
    search alone reports present strings as missing.
    """
    return text.encode("utf-8") in bundle or text.encode("utf-16-le") in bundle


@pytest.mark.parametrize("screen,needle", sorted(SCREENS.items()))
def test_the_screen_is_actually_in_the_app(screen, needle):
    """A screen nothing navigates to is not shipped, however well it is tested."""
    assert _present(_bundle(), needle), (
        f"The {screen} screen is missing from the built app. It is most likely "
        f"not reachable from App.tsx — Metro only bundles what the entry point "
        f"can reach."
    )


@pytest.mark.parametrize("what,needle", sorted(BEHAVIOUR.items()))
def test_the_behaviour_is_actually_in_the_app(what, needle):
    assert _present(_bundle(), needle), f"{what} did not make it into the bundle"


def test_the_native_libraries_are_there():
    """No SQLite means no local collection, and the app is offline-first."""
    if not APK.exists():
        pytest.skip("no release APK built")
    with zipfile.ZipFile(APK) as archive:
        names = archive.namelist()
    assert any("libexpo-sqlite" in n for n in names), "expo-sqlite is missing"


def test_the_bundle_is_not_suspiciously_small():
    """A near-empty bundle still passes a substring check for nothing."""
    assert len(_bundle()) > 200_000


def test_the_app_does_not_reach_for_a_global_that_is_not_there():
    """`crypto.randomUUID` is in Node, in jsdom and in every browser.

    It is not in React Native — not in Hermes, not in React Native's own
    polyfills, not in Expo's WinterCG runtime. Calling it threw the instant a
    QR code was read, inside a promise nobody awaited, in a build that reports
    unhandled rejections nowhere. The pairing screen simply never changed.

    An absence is the honest assertion here: the fix is that the call is gone.
    """
    assert not _present(_bundle(), "randomUUID"), (
        "crypto.randomUUID is back in the bundle; it does not exist on a phone"
    )


def test_the_release_build_is_allowed_to_use_plain_http():
    """The bug that made the app say Offline while sitting next to the PC.

    Android 9 made `cleartextTrafficPermitted` default to false. Expo's
    template puts `usesCleartextTraffic` in the **debug** manifest only, so a
    release build blocks every plain-HTTP request before it leaves the handset.

    Nothing about that looked broken. Pairing worked, because pairing only
    parses a URL and writes it down. The collection opened, because it reads
    the local mirror. Every sync failed the instant it started.

    This app talks plain HTTP on purpose — a certificate would mean publishing
    the machine's name to the public Certificate Transparency log — so the
    permission has to be in the SHIPPED manifest, which is what this reads.
    Android compiles the manifest to binary XML and keeps attribute names in a
    UTF-16 string pool, so the needle is encoded rather than written out.
    """
    if not APK.exists():
        pytest.skip("no release APK built")
    with zipfile.ZipFile(APK) as archive:
        manifest = archive.read("AndroidManifest.xml")
    needle = "usesCleartextTraffic".encode("utf-16-le")
    assert needle in manifest, (
        "the shipped manifest does not permit cleartext HTTP, so the app "
        "cannot reach the desktop at all"
    )
