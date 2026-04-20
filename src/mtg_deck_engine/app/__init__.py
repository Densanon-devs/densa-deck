"""Desktop app wrapper for the MTG Deck Engine.

Stage A: pywebview shell + HTML/JS frontend that wraps the existing CLI
engine for non-developer users. The engine itself is unchanged — this
package only adds a GUI surface and a deck-lab flow (save / edit / version
/ diff) on top of the existing `versioning` module.

Two entry points:
  - `from mtg_deck_engine.app.api import AppApi` — the Python API exposed
    to JS via pywebview's bridge. Testable in isolation (no pywebview).
  - `mtg_deck_engine.app.main:run()` — creates the webview window and
    starts the GUI event loop. Requires `pywebview` (optional dep).

Everything a Pro customer does in the GUI maps back to the same engine
functions the CLI uses. There is no parallel code path — the GUI is a
view layer, not a reimplementation.
"""

from mtg_deck_engine.app.api import AppApi

__all__ = ["AppApi"]
