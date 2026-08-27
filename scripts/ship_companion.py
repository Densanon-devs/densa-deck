"""Put the companion APK on Drive, and refuse to if it is not what it says.

Hashing both ends catches a half-written copy. It does not catch the mistake
that actually happened: a version bump that silently did not run, a build that
stamped the OLD version, and a file copied to Drive under the NEW name. Both
ends hashed identically. Both ends were wrong.

An APK whose name disagrees with its contents is the same failure the project
already knows about from debug builds sitting beside release ones — "no way to
tell them apart is how the wrong build gets sent out". A wrong versionCode is
worse than cosmetic: Android refuses an update that does not increase it, so
the phone keeps running the old app while the file on Drive looks new.

So this checks, in order:

  * the four version sources agree with each other (a test does this too, but
    this runs at the moment it matters);
  * the built APK actually CONTAINS that version string in its JS bundle;
  * the manifest's versionCode is higher than whatever is already on Drive;
  * and only then copies, hashes both ends, and removes the superseded file.

Run:  python scripts/ship_companion.py [--drive "G:/My Drive/Densanon LLC/DensaDeck"]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANION = ROOT / "companion"
APK = (COMPANION / "android" / "app" / "build" / "outputs" / "apk" / "release"
       / "app-release.apk")
DEFAULT_DRIVE = Path("G:/My Drive/Densanon LLC/DensaDeck")


def _fail(message: str) -> None:
    print(f"REFUSING TO SHIP: {message}", file=sys.stderr)
    raise SystemExit(1)


def declared_versions() -> dict[str, str]:
    """What each of the four sources says. They have to agree."""
    app_json = json.loads((COMPANION / "app.json").read_text(encoding="utf-8"))
    package = json.loads((COMPANION / "package.json").read_text(encoding="utf-8"))
    version_ts = (COMPANION / "src" / "lib" / "version.ts").read_text(encoding="utf-8")
    gradle = (COMPANION / "android" / "app" / "build.gradle").read_text(encoding="utf-8")

    ts_match = re.search(r"VERSION\s*=\s*'([^']+)'", version_ts)
    gradle_name = re.search(r'versionName\s+"([^"]+)"', gradle)
    gradle_code = re.search(r"versionCode\s+(\d+)", gradle)
    if not (ts_match and gradle_name and gradle_code):
        _fail("could not read the version out of version.ts or build.gradle")

    return {
        "app.json": app_json["expo"]["version"],
        "app.json code": str(app_json["expo"]["android"]["versionCode"]),
        "package.json": package["version"],
        "version.ts": ts_match.group(1),
        "build.gradle": gradle_name.group(1),
        "build.gradle code": gradle_code.group(1),
    }


def check_sources_agree(found: dict[str, str]) -> tuple[str, int]:
    names = {k: v for k, v in found.items() if "code" not in k}
    codes = {k: v for k, v in found.items() if "code" in k}
    if len(set(names.values())) != 1:
        _fail(f"the version sources disagree: {names}")
    if len(set(codes.values())) != 1:
        _fail(f"the versionCode sources disagree: {codes}")
    return next(iter(names.values())), int(next(iter(codes.values())))


def version_in_bundle(version: str) -> bool:
    """Is that version string actually inside the built app?

    The check the hash could never make. Hermes keeps any string with a
    non-ASCII character in a UTF-16 table, so both encodings are searched —
    the same trap `test_apk_contents.py` documents.
    """
    if not APK.exists():
        _fail(f"no APK at {APK} — build it first with ./gradlew assembleRelease")
    with zipfile.ZipFile(APK) as archive:
        if "assets/index.android.bundle" not in archive.namelist():
            _fail("that APK carries no JS bundle — it is a DEBUG build")
        bundle = archive.read("assets/index.android.bundle")
    return (version.encode("utf-8") in bundle
            or version.encode("utf-16-le") in bundle)


def highest_shipped(drive: Path) -> tuple[str, Path | None]:
    """The newest version already on Drive, by filename."""
    best, best_path = "", None
    for path in drive.glob("DensaDeck-Companion-*.apk"):
        found = re.search(r"DensaDeck-Companion-(.+)\.apk$", path.name)
        if not found:
            continue
        if _as_tuple(found.group(1)) > _as_tuple(best):
            best, best_path = found.group(1), path
    return best, best_path


def _as_tuple(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in version.split("."))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drive", type=Path, default=DEFAULT_DRIVE)
    parser.add_argument("--keep-old", action="store_true",
                        help="Leave the superseded APK in place")
    args = parser.parse_args()

    found = declared_versions()
    version, code = check_sources_agree(found)
    print(f"Declared: v{version} (versionCode {code})")

    if not version_in_bundle(version):
        _fail(
            f"the APK does not contain 'v{version}'. The build predates the "
            f"version bump — rebuild before shipping. This is exactly how a "
            f"file named for one version ends up holding another."
        )
    print(f"Confirmed: v{version} is inside the built bundle")

    args.drive.mkdir(parents=True, exist_ok=True)
    shipped, shipped_path = highest_shipped(args.drive)
    if shipped and _as_tuple(version) <= _as_tuple(shipped):
        _fail(f"v{shipped} is already on Drive — v{version} is not newer. "
              f"Android refuses an update whose versionCode does not rise.")

    target = args.drive / f"DensaDeck-Companion-{version}.apk"
    shutil.copy2(APK, target)

    local, remote = _sha256(APK), _sha256(target)
    if local != remote:
        _fail(f"the copy does not match: {local} vs {remote}")
    print(f"Copied and hash-verified: {target}")
    print(f"  sha256 {local}")

    if shipped_path and shipped_path != target and not args.keep_old:
        shipped_path.unlink()
        print(f"Removed superseded {shipped_path.name}")


if __name__ == "__main__":
    main()
