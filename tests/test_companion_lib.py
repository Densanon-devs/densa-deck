"""The companion app's sync engine, run as part of the Python suite.

The engine lives in TypeScript because it ships inside an Expo app, but it is
the half of the system that can lose someone's cards, so it must not be
possible to make the Python suite green while it is broken. Node runs the
TypeScript directly — no build step — which is why the app's data layer is
kept free of React Native imports.

Skipped where Node is unavailable, so the Python suite still runs on a machine
without it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

COMPANION = Path(__file__).parent.parent / "companion"


def _node() -> str | None:
    return shutil.which("node")


@pytest.mark.skipif(not COMPANION.exists(), reason="companion app not present")
def test_companion_sync_engine():
    node = _node()
    if node is None:
        pytest.skip("Node is not installed")

    result = subprocess.run(
        [node, "--test", "tests/**/*.test.mjs"],
        cwd=COMPANION, capture_output=True, text=True, timeout=300,
        env={**os.environ, "NO_COLOR": "1"},
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined[-4000:]
    # A run that failed to find any test would exit 0 with nothing done, which
    # must not read as success.
    assert "# pass" in combined or "pass " in combined, combined[-2000:]


@pytest.mark.skipif(not COMPANION.exists(), reason="companion app not present")
def test_companion_typechecks():
    """A type error in the sync engine is a bug waiting to happen offline."""
    node = _node()
    if node is None:
        pytest.skip("Node is not installed")
    if not (COMPANION / "node_modules" / "typescript").exists():
        pytest.skip("companion dependencies not installed (npm install)")

    result = subprocess.run(
        ["npx", "tsc", "--noEmit"],
        cwd=COMPANION, capture_output=True, text=True, timeout=300,
        shell=os.name == "nt",
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]


@pytest.mark.skipif(not COMPANION.exists(), reason="companion app not present")
def test_the_default_collection_uid_matches_the_desktop():
    """Both sides have to agree on what "unfiled" means.

    This constant is the one piece of the protocol that is written down twice,
    and a mismatch would give each device its own unfiled pile — removals made
    on one landing in a collection the other does not have.
    """
    from densa_deck.collection.storage import DEFAULT_COLLECTION_UID

    store_ts = (COMPANION / "src" / "lib" / "store.ts").read_text(encoding="utf-8")
    assert DEFAULT_COLLECTION_UID in store_ts, (
        "companion store.ts does not use the desktop's default collection uid"
    )
