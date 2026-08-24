"""The phone scan page, exercised in a real DOM.

`scan.html` is ~900 lines of browser JavaScript that the Python suite cannot
reach, and it has been the source of bugs the Python tests were structurally
incapable of catching: Enter swallowed in the card textarea, a printing list
truncated at 25 entries so the copy in the user's hand was unreachable, a
repeat guard that cleared on any missed frame and filed six copies of one
card. All of those are visible from a DOM with no camera attached.

Skipped unless Node and jsdom are available, so the Python suite still runs
on a machine without them:

    npm install jsdom
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "js" / "phone_page.test.js"


def _node_env() -> dict | None:
    """Environment able to `require("jsdom")`, or None if it isn't installed."""
    roots = [
        Path(__file__).resolve().parents[1] / "node_modules",
        Path(os.environ.get("DENSA_JSDOM_PATH", "")),
    ]
    for root in roots:
        if root and (root / "jsdom").is_dir():
            return {**os.environ, "NODE_PATH": str(root)}
    # Also accept a globally resolvable jsdom.
    try:
        probe = subprocess.run(["node", "-e", "require('jsdom')"],
                               capture_output=True, timeout=30)
        if probe.returncode == 0:
            return dict(os.environ)
    except (OSError, subprocess.SubprocessError):
        return None
    return None


@pytest.mark.skipif(not SCRIPT.exists(), reason="phone page test script missing")
def test_phone_page_behaviour():
    env = _node_env()
    if env is None:
        pytest.skip("Node with jsdom not available (npm install jsdom)")

    result = subprocess.run(["node", str(SCRIPT)], capture_output=True,
                            text=True, timeout=180, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    # The script counts its own assertions; a silent pass would mean it stopped
    # early without failing, which must not read as success.
    assert "checks passed" in result.stdout, result.stdout
