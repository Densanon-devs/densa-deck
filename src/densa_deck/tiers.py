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
    "playgroup": Tier.FREE,          # Pod profile CRUD — local-only data
    "iterate": Tier.FREE,            # Rule-engine proposals + preview — no LLM
    # Your own collection is your data, not our card data — same call as
    # playgroup. Free ownership tracking is also what makes the app sticky;
    # the money layer on top (portfolio analytics, scanner, reseller P&L) is
    # where Pro earns its keep. Every gate stays a capability gate, never a
    # gate on card access or anything resembling in-game power.
    "collection": Tier.FREE,

    # --- deck records -----------------------------------------------------
    # FREE, and the deck COUNT is what limits it.
    #
    # The lever has to be how many decks you may keep, not what you may do
    # with one, or the taste is worthless: a deck you cannot version is not a
    # sample of version history, and a deck you cannot log a game against
    # never shows you why you would want a record. So the one free deck gets
    # the whole deck lab — versions, diffs, win/loss, retention — and the
    # second deck is what costs money.
    "deck_record": Tier.FREE,

    # --- the card panel, split down the middle ----------------------------
    # What a card DOES here, what it already works with, and which combo
    # lines it sits in are deterministic readings of cards and rules — the
    # same call as `combos` and `rule0`, and free for the same reason.
    "card_synergy": Tier.FREE,
    # What to ADD is the recommendation engine, which is what
    # `suggest_deckbuild_additions` already charges for. One capability, one
    # price, wherever it is reached from.
    "deckbuild_suggestions": Tier.PRO,

    # --- collection, split the same way -----------------------------------
    # Describing cards you own — colours, curve, types, rarity, which sets
    # they came from — is your data, and free like the rest of the
    # collection.
    "collection_breakdown": Tier.FREE,
    # What it is WORTH, and how far through a set you are, is the portfolio
    # analytics named above as where Pro earns its keep.
    "collection_analytics": Tier.PRO,
}

# How much of a Pro feature the free tier gets before the wall.
#
# A taste, not a locked door. Somebody who has never saved a deck cannot want
# deck history — they have to have used it once to know what they would be
# buying. So free gets the feature genuinely WORKING, at a scale small enough
# that anyone who relies on it will pass the limit quickly and know exactly
# what they are paying for.
#
# Every one of these is a COUNT, never a crippled version of the thing. A
# suggestion list that is quietly worse on free would teach people the
# feature is bad rather than that it is limited.

# Three saved decks, each with its full history and its whole win/loss
# record. The limit is on how MANY decks, not on what you may do with one —
# a deck you cannot version is not a taste of version history.
#
# Three rather than one because one deck is not a habit. A person with a
# single deck never finds out what comparing two versions is worth, which is
# the thing they would be paying for.
FREE_SAVED_DECKS = 3

# Three groupings of your own, on top of the main collection.
#
# The main one does not count: it is created for you and cannot be opted out
# of, so charging it against the allowance would quietly make this two.
#
# Groups are pure organisation over cards you already own — no analysis, no
# catalogue — so this is the most generous of the counts on purpose. Three
# is enough for a real workflow (a trade binder, a deck's pile, and the box
# you are sorting) and runs out exactly when someone is organising enough to
# be getting their money's worth.
FREE_COLLECTIONS = 3

# Two of the eight cards the panel would suggest, in the same order Pro sees.
FREE_SUGGESTIONS = 2

# The three sets you are closest to finishing, which is the actionable end of
# that list anyway.
FREE_SETS_TRACKED = 3


def free_allowance(name: str) -> int:
    """How many of `name` the current tier may have. -1 means no limit."""
    if get_user_tier() == Tier.PRO:
        return -1
    return {
        "saved_decks": FREE_SAVED_DECKS,
        "suggestions": FREE_SUGGESTIONS,
        "sets_tracked": FREE_SETS_TRACKED,
        "collections": FREE_COLLECTIONS,
    }.get(name, -1)


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
    "coverage": "info",          # simulator-fidelity report — free, it's honesty
    "rulings": "card_search",    # rulings are card data — never paywalled
    "playgroup": "playgroup",    # pod CRUD — always available
    "iterate": "iterate",        # iteration loop — propose/preview/history
    "collection": "collection",  # physical collection CRUD — local-only data
    "phone": "collection",       # phone scanning — same local-only data
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
