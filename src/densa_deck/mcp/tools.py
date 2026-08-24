"""MCP tool wrappers around AppApi.

Each tool is a thin function that:
  1. Unwraps the AppApi `{ok, data, error}` envelope into a clean dict
     (or raises on error so MCP surfaces it to the model).
  2. Has stable type annotations so FastMCP can auto-generate JSON-schema
     parameter descriptors for every tool — that schema is what the AI
     client sees, so good annotations + docstrings ARE the model prompt.
  3. Calls `assert_pro(...)` at the top for paywalled tools. Free tools
     don't gate; the server skips registering them entirely when
     `--read-only` is passed.

The wrappers deliberately don't expose every AppApi method. Excluded:

  * Setup / progress polling (ingest_start, analyst_pull_start, combo_refresh_start) —
    these are threaded background ops with HTTP-style polling that don't
    fit cleanly into a stdio tool call. The user runs them from the
    desktop app's Settings tab.
  * UI tour state (get_first_run_state, mark_first_run_complete) — irrelevant
    to AI clients.
  * Builder draft (save_builder_draft, load_builder_draft, clear_builder_draft) —
    UI-state only.
  * Destructive ops (delete_deck) — too easy for a confused agent to drop
    a deck on a stray prompt.
  * Configuration (set_user_preferences, activate_license, open_external) —
    privileged operations the user should drive directly.

The result is a focused ~20-tool surface that maps cleanly onto how an
AI assistant actually wants to drive a deck-analysis engine.
"""

from __future__ import annotations

from typing import Any, Optional

from densa_deck.app.api import AppApi
from densa_deck.mcp.license_gate import assert_pro


def _unwrap(response: dict) -> dict:
    """AppApi returns {ok, data, error_type?} envelopes. Unwrap to the bare
    dict so the AI sees a clean payload, or raise so MCP surfaces the
    error to the model with a clear message."""
    if not isinstance(response, dict):
        return {"result": response}
    if response.get("ok") is False:
        msg = response.get("error") or "Unknown engine error."
        kind = response.get("error_type") or "EngineError"
        raise RuntimeError(f"{kind}: {msg}")
    return response.get("data", response)


# =============================================================================
# Free tier — read-only analysis, search, combos, exports, version history
# =============================================================================


def make_free_tools(api: AppApi) -> dict[str, Any]:
    """Build the dict of free-tier tool callables. Returned shape is
    `{tool_name: callable}` so server.py can iterate and register each
    via FastMCP's @tool decorator."""

    def get_tier() -> dict:
        """Report the user's current tier. The AI should call this first to
        know whether Pro tools (goldfish, gauntlet, analyst, coach) are
        available, so it can pick a strategy that doesn't dead-end."""
        return _unwrap(api.get_tier())

    def search_cards(
        name: Optional[str] = None,
        colors: Optional[list[str]] = None,
        color_match: str = "subset",
        cmc_min: Optional[float] = None,
        cmc_max: Optional[float] = None,
        type_line: Optional[str] = None,
        format_legal: Optional[str] = None,
        rarity: Optional[list[str]] = None,
        max_price_usd: Optional[float] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        """Structured card search. `colors` are single-letter (W,U,B,R,G,C);
        `color_match` is "subset" (color-identity-subset, default) or "any"
        (any-of). `format_legal` filters by format legality
        (commander, modern, ...). `rarity` accepts one or more of
        common/uncommon/rare/mythic. Returns up to 120 cards per call.

        Key names below must match `AppApi.search_cards` exactly. They didn't:
        `type_line` and `max_price_usd` were silently dropped (the API reads
        `types` and `max_price`), so type and budget filters did nothing over
        MCP, and a list `rarity` raised AttributeError inside the SQL builder.
        Unknown keys are ignored rather than rejected, which is why the first
        two failed quietly."""
        query: dict[str, Any] = {"offset": offset, "limit": limit}
        if name: query["name"] = name
        if colors: query["colors"] = colors
        # search_structured only special-cases "any"; everything else means
        # identity. Normalise so the documented value maps to a real mode.
        query["color_match"] = "any" if color_match == "any" else "identity"
        if cmc_min is not None: query["cmc_min"] = cmc_min
        if cmc_max is not None: query["cmc_max"] = cmc_max
        # The API takes a list of primary types under `types`.
        if type_line: query["types"] = [type_line]
        if format_legal: query["format_legal"] = format_legal
        # The API takes a single rarity string; accept a list from the model
        # and use the first entry rather than crashing on .strip().
        if rarity:
            query["rarity"] = rarity[0] if isinstance(rarity, list) else rarity
        if max_price_usd is not None: query["max_price"] = max_price_usd
        return _unwrap(api.search_cards(query))

    def get_card(name: str) -> dict:
        """Fetch one card by exact name. Use after search_cards to pull
        full text + legalities + price for hover/detail views."""
        return _unwrap(api.get_card(name))

    def resolve_suggestions(names: list[str], limit: int = 3) -> dict:
        """Fuzzy typo-fix for unresolved card names — pass the names that
        failed to parse; returns up to `limit` suggestions per name."""
        return _unwrap(api.resolve_suggestions(names, limit))

    def analyze_deck(
        decklist_text: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
    ) -> dict:
        """Full static analysis of a deck: archetype, power level (1-10),
        mana curve, color sources, ramp/draw/interaction counts,
        castability, staples, recommendations. The decklist is plain
        text — one card per line with optional quantity prefix and
        optional Commander / Mainboard / Sideboard headers."""
        return _unwrap(api.analyze_deck(decklist_text, format_, name))

    def assess_bracket_fit(
        decklist_text: str,
        target_bracket: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
    ) -> dict:
        """Score the deck against a Commander bracket
        (1-precon, 2-upgraded, 3-optimized, 4-tuned, 5-cedh). Returns
        the detected bracket, the verdict against the target, and a
        punch-list of specific lines to drop or add."""
        return _unwrap(api.assess_bracket_fit(decklist_text, target_bracket, format_, name))

    def list_saved_decks() -> list[dict]:
        """All saved decks the user has tracked, with version count and
        last-saved timestamp. Use the deck_id to fetch with get_deck_latest."""
        return _unwrap(api.list_saved_decks())

    def get_deck_latest(deck_id: str) -> dict:
        """Latest version of a saved deck. Returns the snapshot plus the
        reconstructed decklist text you can pass to analyze_deck or
        run_goldfish."""
        return _unwrap(api.get_deck_latest(deck_id))

    def get_deck_history(deck_id: str) -> list[dict]:
        """All versions of a saved deck, newest-first. Returns one
        snapshot summary per version."""
        return _unwrap(api.get_deck_history(deck_id))

    def diff_deck_versions(deck_id: str, version_a: int, version_b: int) -> dict:
        """Diff between two saved versions: cards added / removed /
        score deltas / combo gains / combo losses."""
        return _unwrap(api.diff_deck_versions(deck_id, version_a, version_b))

    def import_deck_from_url(url: str) -> dict:
        """Fetch a Moxfield or Archidekt deck URL and return a pasteable
        decklist. Moxfield is currently Cloudflare-blocked — Archidekt
        works; for Moxfield, the user should paste the deck text directly."""
        return _unwrap(api.import_deck_from_url(url))

    def export_deck_format(
        decklist_text: str,
        target: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
    ) -> dict:
        """Export a deck to a specific platform's text format. `target` is
        one of: "mtga", "mtgo", "moxfield"."""
        return _unwrap(api.export_deck_format(decklist_text, target, format_, name))

    def build_rule0_worksheet(
        decklist_text: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
        include_combos: bool = True,
    ) -> dict:
        """Build a pre-game Rule 0 disclosure sheet: archetype, power tier,
        bracket, win conditions, combo lines, notable cards. Pure
        rule-engine narration — no LLM, free tier."""
        return _unwrap(api.build_rule0_worksheet(decklist_text, format_, name, include_combos))

    def get_combo_status() -> dict:
        """Status of the local Commander Spellbook combo cache:
        combo_count, last_refresh_at, source. AI should check this first
        before calling detect_combos_for_deck — empty cache = the user
        needs to refresh from the desktop app's Settings tab."""
        return _unwrap(api.get_combo_status())

    def detect_combos_for_deck(
        decklist_text: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
        limit: int = 50,
    ) -> dict:
        """Detect every combo line in the deck (full match across all
        pieces). Errors if the combo cache is empty — call get_combo_status
        first."""
        return _unwrap(api.detect_combos_for_deck(decklist_text, format_, name, limit))

    def detect_near_miss_combos_for_deck(
        decklist_text: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
        max_missing: int = 1,
        limit: int = 25,
    ) -> dict:
        """Combos the deck is `max_missing` cards away from completing —
        the high-leverage "if you add this one card, you unlock a combo"
        suggestions. Default max_missing=1."""
        return _unwrap(api.detect_near_miss_combos_for_deck(
            decklist_text, format_, name, max_missing, limit))

    def get_current_version() -> dict:
        """Return Densa Deck's running version + build date."""
        return _unwrap(api.get_current_version())

    def get_collection_status() -> dict:
        """How many physical cards the user owns, and whether the printing
        catalogue (set + price data) has been downloaded. Check this before
        answering collection questions — when `printings.ready` is false,
        set details and prices are unavailable and you should say so rather
        than implying the collection is empty."""
        return _unwrap(api.get_collection_status())

    def list_collection(
        name_like: Optional[str] = None,
        finish: Optional[str] = None,
        condition: Optional[str] = None,
        location: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """List the physical cards the user owns, newest filters first.

        One row per *stack* — identical copies of one printing in one finish,
        condition and location. The same card in two sets is two rows, because
        they are different objects with different values. `finish` is
        nonfoil/foil/etched; `condition` is NM/LP/MP/HP/DMG.

        `stack_value_usd` is null when the price is unknown — that means
        unknown, never zero, and must not be summed as free."""
        return _unwrap(api.list_collection({
            "name_like": name_like, "finish": finish, "condition": condition,
            "location": location, "limit": limit, "offset": offset,
        }))

    def get_card_printings(card_name: str) -> dict:
        """Every physical printing of a card, with per-printing prices and how
        many the user owns of each.

        Use this before adding a card to a collection: prices vary enormously
        between printings of the same card (Sol Ring ranges from about $1 to
        over $1,600), so "which printing" is the whole question. `finishes`
        lists the finishes that printing was actually made in."""
        return _unwrap(api.get_card_printings(card_name))

    def get_card_ownership(card_names: list[str]) -> dict:
        """Owned / committed / available counts for a batch of card names.

        `committed` is copies already called for by saved decks, `available`
        is what is free to use in something new. A card can be owned but
        unavailable — that means unsleeve it, not buy it."""
        return _unwrap(api.get_card_ownership(card_names))

    def get_collection_value() -> dict:
        """Estimated market value of the user's whole collection, with 24h/7d/30d
        movement where history exists.

        `unpriced_copies` is not decoration: cards with no known price are
        excluded from the total, so quote the total and the unpriced count
        together or the figure misleads. Prices come from daily bulk data and
        carry `prices_stale` / `price_age_hours` — say how old they are."""
        return _unwrap(api.get_collection_value(True))

    def get_deck_collection_value(
        decklist_text: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
        deck_id: Optional[str] = None,
    ) -> dict:
        """Three distinct numbers for a decklist, which are easy to conflate:

        `deck_value_usd` — what the user's own copies are worth (their foil
        Secret Lair counts at foil Secret Lair money).
        `build_value_usd` — what someone else would pay buying the cheapest
        legal printing of every card. This is the honest "is this deck
        expensive?" number.
        `cost_to_complete_usd` — cheapest printings of only what's missing.

        Also returns a paste-ready `shopping_list_text`."""
        return _unwrap(api.get_deck_collection_value(decklist_text, format_, name, deck_id))

    def appraise_collection(acquisition_id: Optional[int] = None) -> dict:
        """Estimate resale proceeds and target purchase bands for the user's
        cards (or one acquisition).

        Deliberately returns NO buy/sell verdict. The underlying price feed's
        own publisher states it is not fit to power a sales system, so this
        reports the arithmetic, the fee assumptions, the price age and the
        `caveats` list. Always relay the caveats and the confidence — never
        present the target prices as a recommendation.

        Note cards below the bulk threshold are valued at bulk rates, not at
        nominal market price; ignoring that overstates large piles severalfold."""
        return _unwrap(api.appraise_collection(acquisition_id))

    def get_reseller_dashboard() -> dict:
        """Capital invested, realized profit from sales, and remaining
        inventory value. `sales_missing_basis` counts sales with no cost basis
        allocated — realized profit excludes those, so mention it when non-zero."""
        return _unwrap(api.get_reseller_dashboard())

    def get_deck_ownership(
        decklist_text: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
        deck_id: Optional[str] = None,
    ) -> dict:
        """What a decklist needs that the user doesn't have available.

        Returns per-card rows plus totals. Distinguishes `missing` (not owned
        — buy it) from `blocked` (owned but committed to another deck —
        unsleeve it). Pass `deck_id` for a saved deck so it isn't counted as
        competing with itself."""
        return _unwrap(api.get_deck_ownership(decklist_text, format_, name, deck_id))

    return {
        "get_tier": get_tier,
        "search_cards": search_cards,
        "get_card": get_card,
        "resolve_suggestions": resolve_suggestions,
        "analyze_deck": analyze_deck,
        "assess_bracket_fit": assess_bracket_fit,
        "list_saved_decks": list_saved_decks,
        "get_deck_latest": get_deck_latest,
        "get_deck_history": get_deck_history,
        "diff_deck_versions": diff_deck_versions,
        "import_deck_from_url": import_deck_from_url,
        "export_deck_format": export_deck_format,
        "build_rule0_worksheet": build_rule0_worksheet,
        "get_combo_status": get_combo_status,
        "detect_combos_for_deck": detect_combos_for_deck,
        "detect_near_miss_combos_for_deck": detect_near_miss_combos_for_deck,
        "get_current_version": get_current_version,
        # Collection — read-only. Mutating the user's physical inventory over
        # MCP is deliberately not exposed, same policy as deck deletion:
        # an agent should not be able to silently rewrite what you own.
        "get_collection_status": get_collection_status,
        "list_collection": list_collection,
        "get_card_printings": get_card_printings,
        "get_card_ownership": get_card_ownership,
        "get_deck_ownership": get_deck_ownership,
        "get_collection_value": get_collection_value,
        "get_deck_collection_value": get_deck_collection_value,
        "appraise_collection": appraise_collection,
        "get_reseller_dashboard": get_reseller_dashboard,
    }


# =============================================================================
# Pro tier — simulation, analyst LLM, coach, save/diff. License-gated.
# =============================================================================


def make_pro_tools(api: AppApi) -> dict[str, Any]:
    """Pro-only tools. Each calls `assert_pro(feature_key)` at the top so a
    free-tier user gets a clear ProRequiredError that the AI client can
    explain to the user, rather than a silent failure."""

    def run_goldfish(
        decklist_text: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
        sims: int = 1000,
        seed: Optional[int] = None,
        include_combos: bool = True,
    ) -> dict:
        """Goldfish simulation — play the deck against itself N times,
        report win-rate / win-turn distribution / mulligan rate. Combo-
        aware when include_combos=True. Typical 1000-sim run is 3-15
        seconds; cap at 5000 to keep tool latency reasonable."""
        assert_pro("goldfish_simulation")
        if sims > 5000: sims = 5000
        return _unwrap(api.run_goldfish(decklist_text, format_, name, sims, seed, include_combos))

    def run_gauntlet(
        decklist_text: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
        sims: int = 200,
        seed: Optional[int] = None,
        include_combos: bool = True,
    ) -> dict:
        """Matchup gauntlet — run the deck against 11 archetype profiles,
        sims games each. 30-60 seconds at default settings; cap at 500
        to keep latency in check."""
        assert_pro("matchup_gauntlet")
        if sims > 500: sims = 500
        return _unwrap(api.run_gauntlet(decklist_text, format_, name, sims, seed, include_combos))

    def duel_decks(
        deck_a_id: str,
        deck_b_id: str,
        sims: int = 100,
        seed: Optional[int] = None,
    ) -> dict:
        """Saved deck vs. saved deck — both as the hero. Returns the
        verdict + a per-axis power delta + win rate. 100 sims is the
        sweet spot; cap at 500."""
        assert_pro("matchup_gauntlet")
        if sims > 500: sims = 500
        return _unwrap(api.duel_decks(deck_a_id, deck_b_id, sims, seed))

    def suggest_deckbuild_additions(
        decklist_text: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
        count: int = 8,
        budget_usd: Optional[float] = None,
    ) -> dict:
        """Suggest cards to add — ranked by role-gap fit and combo-completer
        bonus. Deterministic (no LLM call). `budget_usd` filters out
        cards above the per-card price ceiling."""
        assert_pro("deep_analysis")
        return _unwrap(api.suggest_deckbuild_additions(
            decklist_text, format_, name, count, budget_usd))

    def explain_card_in_deck(
        decklist_text: str,
        card_name: str,
        format_: Optional[str] = None,
        name: str = "Unnamed Deck",
    ) -> dict:
        """Explain why a single card was flagged in this deck — castability
        issues, role redundancy, combo-piece status. LLM-narrated; the
        analyst model must be installed (one-time download via desktop
        Settings)."""
        assert_pro("explain_card")
        return _unwrap(api.explain_card_in_deck(decklist_text, card_name, format_, name))

    def compare_decks_analyst(deck_a_id: str, deck_b_id: str) -> dict:
        """LLM-narrated comparison of two saved decks: power gap, role
        deltas, added/removed cards, combo gained/lost. Both decks must
        already be saved (use save_deck_version first if needed)."""
        assert_pro("compare_decks")
        return _unwrap(api.compare_decks_analyst(deck_a_id, deck_b_id))

    def save_deck_version(
        deck_id: str,
        name: str,
        decklist_text: str,
        format_: Optional[str] = None,
        notes: str = "",
    ) -> dict:
        """Save a new version of a deck. `deck_id` is the stable handle
        (use the same id to add versions to the same deck). Returns the
        saved snapshot plus any combo lines this save broke."""
        assert_pro("deck_version_history")
        return _unwrap(api.save_deck_version(deck_id, name, decklist_text, format_, notes))

    def coach_start(
        deck_id: Optional[str] = None,
        decklist_text: Optional[str] = None,
        name: str = "Coach Deck",
        format_: Optional[str] = None,
    ) -> dict:
        """Open a coach session — interactive LLM tutor against a specific
        deck. Pass either `deck_id` (saved deck) or `decklist_text`.
        Returns a session token to use with coach_ask / coach_close."""
        assert_pro("analyst")
        return _unwrap(api.coach_start(deck_id, decklist_text, name, format_))

    def coach_ask(token: str, question: str) -> dict:
        """Send a question to the coach session. Returns the LLM's
        response plus session metadata."""
        assert_pro("analyst")
        return _unwrap(api.coach_ask(token, question))

    def coach_get_history(token: str) -> list[dict]:
        """Full turn history for a coach session."""
        assert_pro("analyst")
        return _unwrap(api.coach_get_history(token))

    def coach_close(token: str) -> dict:
        """Close a coach session — frees the LLM context."""
        assert_pro("analyst")
        return _unwrap(api.coach_close(token))

    return {
        "run_goldfish": run_goldfish,
        "run_gauntlet": run_gauntlet,
        "duel_decks": duel_decks,
        "suggest_deckbuild_additions": suggest_deckbuild_additions,
        "explain_card_in_deck": explain_card_in_deck,
        "compare_decks_analyst": compare_decks_analyst,
        "save_deck_version": save_deck_version,
        "coach_start": coach_start,
        "coach_ask": coach_ask,
        "coach_get_history": coach_get_history,
        "coach_close": coach_close,
    }
