"""Feature tier system — free vs pro gating.

Monetization is feature-gated, never data-gated. Raw card data and basic
analysis are always free. Premium features include deep simulation, extended
deck storage, coaching insights, and advanced matchup testing.

Tier is determined by:
1. MTG_ENGINE_TIER environment variable (overrides config)
2. ~/.densa-deck/config.json {"tier": "pro"}
3. Default: "free"
"""

from __future__ import annotations

import enum
import json
import os
from pathlib import Path


class Tier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"


# Maps feature keys to the minimum tier required
FEATURE_TIERS: dict[str, Tier] = {
    # Always free
    "ingest": Tier.FREE,
    "card_search": Tier.FREE,
    "deck_import": Tier.FREE,
    "static_analysis": Tier.FREE,
    "basic_mana_curve": Tier.FREE,
    "basic_recommendations": Tier.FREE,
    "info": Tier.FREE,
    "calc": Tier.FREE,
    "license": Tier.FREE,
    # Pro features
    "deep_analysis": Tier.PRO,
    "probability": Tier.PRO,
    "goldfish_simulation": Tier.PRO,
    "matchup_gauntlet": Tier.PRO,
    "deck_version_history": Tier.PRO,
    "export_reports": Tier.PRO,
    "deck_diff": Tier.PRO,
    "mulligan_practice": Tier.PRO,
    "advanced_scoring": Tier.PRO,
    "custom_benchmark_suites": Tier.PRO,
    "analyst": Tier.PRO,  # LLM-backed analyst: executive summary + cut suggestions
    # Phase 6 + combos
    "combos": Tier.FREE,            # Combo detection — free-tier feature, gives free users a real reason to ingest
    "rule0": Tier.FREE,              # Pre-game worksheet — pure rule-engine narration, no LLM
    "explain_card": Tier.PRO,        # Per-card analyst narration
    "compare_decks": Tier.PRO,       # Two-deck analyst narration
}

# Map CLI command names to feature keys
COMMAND_FEATURES: dict[str, str] = {
    "ingest": "ingest",
    "analyze": "static_analysis",
    "search": "card_search",
    "info": "info",
    "calc": "calc",
    "license": "license",  # Always free — managing your own license
    "probability": "probability",
    "goldfish": "goldfish_simulation",
    "gauntlet": "matchup_gauntlet",
    "save": "deck_version_history",
    "compare": "deck_version_history",
    "history": "deck_version_history",
    "diff": "deck_diff",
    "practice": "mulligan_practice",
    "analyst": "analyst",  # model-management subcommand — Pro-only
    "coach": "analyst",    # interactive REPL — uses analyst backend, Pro-gated
    "app": "info",         # GUI launcher — free tier can launch; Pro features gated inside
    "register-protocol": "info",  # Registry helper; always available
    "combos": "combos",
    "rule0": "rule0",
    "explain": "explain_card",
    "compare-decks": "compare_decks",
    "bracket": "rule0",          # bracket fit is a free deterministic feature
    "export": "card_search",     # export is free (commodity feature)
}

_CONFIG_PATH = Path.home() / ".densa-deck" / "config.json"

_PRO_UPGRADE_MSG = (
    "[bold yellow]This feature requires Densa Deck Pro.[/bold yellow]\n"
    "Free tier includes: card search, deck import, static analysis, mana curve, "
    "basic recommendations, and the hypergeometric calculator (calc).\n"
    "[dim]To unlock: set MTG_ENGINE_TIER=pro or update ~/.densa-deck/config.json[/dim]"
)


def get_user_tier() -> Tier:
    """Detect the user's tier from environment, license, or config."""
    # 1. Environment variable override
    env_tier = os.environ.get("MTG_ENGINE_TIER", "").lower().strip()
    if env_tier == "pro":
        return Tier.PRO
    if env_tier == "free":
        return Tier.FREE
    if env_tier:
        import sys
        print(f"Warning: unrecognized MTG_ENGINE_TIER='{env_tier}' (expected 'free' or 'pro')", file=sys.stderr)

    # 2. Saved license file (Pro purchase)
    try:
        from densa_deck.licensing import load_saved_license
        license = load_saved_license()
        if license and license.grants_pro():
            return Tier.PRO
    except ImportError:
        pass

    # 3. Config file
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            tier_str = data.get("tier", "free").lower().strip()
            if tier_str == "pro":
                return Tier.PRO
        except (json.JSONDecodeError, OSError):
            pass

    # 4. Default
    return Tier.FREE


def check_access(feature: str, user_tier: Tier | None = None) -> bool:
    """Check whether the user's tier grants access to a feature."""
    if user_tier is None:
        user_tier = get_user_tier()
    required = FEATURE_TIERS.get(feature)
    if required is None:
        return True  # Unknown features default to open
    if user_tier == Tier.PRO:
        return True  # Pro gets everything
    return required == Tier.FREE


def require_pro(feature: str) -> bool:
    """Returns True if the feature is blocked (user is free, feature is pro).

    Use this at the top of pro commands to gate access.
    """
    return not check_access(feature)


def set_tier(tier: str):
    """Save tier to config file. Atomic write so a crash mid-save can't
    leave the next launch with a half-truncated config.json — and so a
    concurrent set_user_preferences (which also writes this file)
    can't lose the tier field on a torn-write race."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = {}
    if _CONFIG_PATH.exists():
        try:
            config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    config["tier"] = tier
    import os as _os
    tmp = _CONFIG_PATH.with_suffix(_CONFIG_PATH.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    _os.replace(tmp, _CONFIG_PATH)
