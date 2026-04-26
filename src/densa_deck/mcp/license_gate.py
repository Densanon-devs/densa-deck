"""License gate for MCP-exposed tools.

The same tier system the CLI and desktop UI use — `tiers.get_user_tier()`
reads `MTG_ENGINE_TIER` env, then `~/.densa-deck/config.json`, then the
saved license file. Customers who activate Pro through the desktop app
automatically unlock the Pro tools when their AI client reconnects to the
MCP server.

Two integration points:

- `current_tier()` returns the tier at server start (used to log a clear
  banner so the user can see "Free tier — Pro tools will refuse" or "Pro
  tier — full surface enabled").
- `assert_pro(feature)` is called at the top of every Pro tool. Raises
  ProRequiredError on a free user; the FastMCP framework catches this and
  surfaces it as a tool error to the AI client, which can then explain
  the situation to the user instead of silently retrying.

Defense in depth: a `--read-only` flag on `densa-deck mcp serve` skips
registering all Pro tools entirely, so an AI agent can't even *see* them.
That's the right default for someone exposing the server to a less-trusted
agent — the model can't be tempted to call goldfish in a tight loop if
the tool isn't in its registry.
"""

from __future__ import annotations

from densa_deck.tiers import Tier, check_access, get_user_tier


class ProRequiredError(Exception):
    """Raised when a free-tier session calls a Pro-only MCP tool.

    FastMCP catches exceptions and surfaces them to the AI client as
    structured tool errors, so the model gets a clear "this requires Pro"
    message instead of an opaque traceback.
    """

    def __init__(self, feature: str):
        self.feature = feature
        super().__init__(
            f"'{feature}' requires Densa Deck Pro. "
            "Activate a license in the desktop app's Settings tab "
            "(or set MTG_ENGINE_TIER=pro for testing)."
        )


def current_tier() -> Tier:
    """Return the user's current tier. Used for the startup banner."""
    return get_user_tier()


def is_pro() -> bool:
    return current_tier() == Tier.PRO


def assert_pro(feature: str) -> None:
    """Raise ProRequiredError if the current tier doesn't satisfy `feature`.

    `feature` is a key from `tiers.FEATURE_TIERS` — same names the CLI
    uses, e.g. "goldfish_simulation", "compare_decks", "analyst".
    """
    if not check_access(feature):
        raise ProRequiredError(feature)
