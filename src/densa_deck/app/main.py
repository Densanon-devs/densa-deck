"""Pywebview entry point — creates the window and starts the event loop.

Run via `densa-deck app`, which calls `run()`. pywebview is an optional
dependency (declared in pyproject.toml's [desktop] extra); if it's missing
we print a clear install message instead of crashing.

The frontend ships as static files inside this package (`static/index.html`
+ siblings). At runtime we resolve the path relative to this module so the
PyInstaller bundle still finds the assets when frozen.
"""

from __future__ import annotations

import sys
from pathlib import Path

from densa_deck.app.api import AppApi


STATIC_DIR = Path(__file__).parent / "static"


def run(debug: bool = False):
    """Create the window and start the pywebview main loop.

    `debug=True` enables the browser devtools overlay — useful when iterating
    on the frontend but noisy for end users, so it's off by default.
    """
    try:
        import webview  # pywebview
    except ImportError:
        _print_install_hint()
        sys.exit(1)

    api = AppApi()
    entry = STATIC_DIR / "index.html"
    if not entry.exists():
        print(f"ERROR: Frontend assets not found at {entry}", file=sys.stderr)
        sys.exit(1)

    # Pull the version at import time so the window title is informative —
    # the installer shows "Densa Deck" but the running window adds
    # version so users who installed via different channels know what's up.
    try:
        from densa_deck import __version__ as version
        title = f"Densa Deck — v{version}"
    except ImportError:
        title = "Densa Deck"

    window = webview.create_window(
        title=title,
        url=str(entry),
        js_api=api,
        width=1200, height=800,
        min_size=(800, 600),
        # Keep the window chrome simple so cross-platform parity is easier —
        # no custom titlebar, no frameless mode. Ship something that Works
        # first, polish chrome second.
    )

    def _on_closing():
        api.close()

    window.events.closing += _on_closing
    webview.start(debug=debug)


def _print_install_hint():
    print("pywebview is not installed. The desktop app requires it.", file=sys.stderr)
    print("", file=sys.stderr)
    print("Install with:", file=sys.stderr)
    print("    pip install 'densa-deck[desktop]'", file=sys.stderr)
    print("", file=sys.stderr)
    print("or directly:", file=sys.stderr)
    print("    pip install pywebview", file=sys.stderr)


if __name__ == "__main__":
    run(debug="--debug" in sys.argv)
