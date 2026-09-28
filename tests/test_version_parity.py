"""Every place the desktop version is written agrees.

The same control Table of War has (test/version_parity.test.ts). Without it
`packaging/installer.iss` sat at 0.6.0 while the app was 0.7.0 -- an
installer built from it would have been named, and would have registered
itself as, the previous version. The combo-fetch User-Agent literal drifted
the same way and is now derived from `__version__`.

The companion's own sources are checked by companion/scripts and its tests;
this file is the desktop's.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def _package() -> str:
    from densa_deck import __version__
    return __version__


def _installer() -> str:
    text = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    m = re.search(r'#define\s+AppVersion\s+"([^"]+)"', text)
    assert m, "installer.iss has no AppVersion define"
    return m.group(1)


def test_every_desktop_version_source_agrees():
    versions = {
        "pyproject.toml": _pyproject(),
        "densa_deck/__init__.py": _package(),
        "packaging/installer.iss": _installer(),
    }
    assert len(set(versions.values())) == 1, versions


def test_the_combo_user_agent_follows_the_version():
    from densa_deck.combos.data import USER_AGENT_DEFAULT
    assert f"/{_package()} " in USER_AGENT_DEFAULT


def test_no_hardcoded_version_in_user_agents():
    # A literal "DensaDeck/0.x.y" anywhere is a string that will go stale.
    hits = []
    for path in (ROOT / "src" / "densa_deck").rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'DensaDeck/\d+\.\d+\.\d+', line):
                hits.append(f"{path.relative_to(ROOT)}:{n}")
    assert not hits, hits
