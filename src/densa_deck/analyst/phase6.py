"""Phase 6 analyst features: compare two decks, explain a single card,
and the Rule 0 worksheet export shape.

These are intentionally separate from `runner.AnalystRunner`. The runner
is a long-form pipeline (summary + cuts + adds + swaps); the Phase 6
features are short, single-shot prompts where keeping them out of the
pipeline reduces cross-coupling and makes them easy to test in isolation.

All three are pure narration of structured data — the LLM never gets to
introduce new card names, so hallucination surface is the same as the
existing summary prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from densa_deck.analyst.backends import LLMBackend
from densa_deck.analyst.pipeline import GenerateResult, generate_with_verify
from densa_deck.analyst.prompts import (
    compare_decks_prompt,
    explain_card_prompt,
)
from densa_deck.analyst.verifiers import verify_prose_output


# =============================================================================
# Compare deck A vs B
# =============================================================================


@dataclass
class CompareResult:
    summary: str = ""
    confidence: float = 0.0
    verified: bool = False
    raw: GenerateResult | None = None
    # Echo the structured deltas so callers / exporters can render a
    # numeric table alongside the LLM prose.
    added_in_b: list[str] = field(default_factory=list)
    removed_in_b: list[str] = field(default_factory=list)
    score_deltas: dict[str, float] = field(default_factory=dict)
    role_deltas: dict[str, int] = field(default_factory=dict)
    power_gap: float = 0.0


def compare_decks(
    *,
    backend: LLMBackend,
    deck_a_name: str,
    deck_b_name: str,
    deck_a_archetype: str,
    deck_b_archetype: str,
    deck_a_power: float,
    deck_b_power: float,
    added_cards: list[str],
    removed_cards: list[str],
    score_deltas: dict[str, float],
    role_deltas: dict[str, int] | None = None,
    max_retries: int = 2,
) -> CompareResult:
    """Narrate the difference between two decks.

    `added_cards` / `removed_cards` come from the existing
    `versioning.storage.diff_versions` flow, generalized to also accept
    "compare against a netdeck text I just pasted." Score deltas are
    keyed the same way `static.AnalysisResult.scores` is, so the caller
    can pass `b.scores - a.scores` directly.

    The prompt itself caps card-name lists at 8 to keep small-model
    inputs short; full lists are retained on the result for export.
    """
    prompt = compare_decks_prompt(
        deck_a_name=deck_a_name,
        deck_b_name=deck_b_name,
        deck_a_archetype=deck_a_archetype,
        deck_b_archetype=deck_b_archetype,
        deck_a_power=deck_a_power,
        deck_b_power=deck_b_power,
        added_cards=added_cards,
        removed_cards=removed_cards,
        score_deltas=score_deltas,
        role_deltas=role_deltas,
    )
    gen = generate_with_verify(
        backend, prompt, verify=verify_prose_output,
        max_retries=max_retries, max_tokens=512,
    )
    return CompareResult(
        summary=gen.output.strip(),
        confidence=gen.confidence,
        verified=gen.verified,
        raw=gen,
        added_in_b=list(added_cards),
        removed_in_b=list(removed_cards),
        score_deltas=dict(score_deltas),
        role_deltas=dict(role_deltas or {}),
        power_gap=deck_b_power - deck_a_power,
    )


# =============================================================================
# Explain a card
# =============================================================================


@dataclass
class ExplainResult:
    card_name: str = ""
    summary: str = ""
    confidence: float = 0.0
    verified: bool = False
    raw: GenerateResult | None = None
    flags: list[str] = field(default_factory=list)
    # Echoed so the CLI/UI can show the numeric basis next to the prose.
    on_curve_prob: float | None = None
    bottleneck_color: str | None = None


def explain_card(
    *,
    backend: LLMBackend,
    card_name: str,
    mana_cost: str,
    cmc: float,
    deck_name: str,
    deck_colors: list[str],
    color_sources: dict[str, int],
    on_curve_prob: float | None,
    bottleneck_color: str | None,
    flags: list[str],
    role_tags: list[str],
    max_retries: int = 2,
) -> ExplainResult:
    """Narrate why a single card was flagged.

    `flags` is a free-text list of rule-engine signals. The LLM only
    explains the named card's situation — same hallucination-surface
    profile as the summary prompt.
    """
    prompt = explain_card_prompt(
        card_name=card_name,
        mana_cost=mana_cost,
        cmc=cmc,
        deck_name=deck_name,
        deck_colors=deck_colors,
        color_sources=color_sources,
        on_curve_prob=on_curve_prob,
        bottleneck_color=bottleneck_color,
        flags=flags,
        role_tags=role_tags,
    )
    gen = generate_with_verify(
        backend, prompt, verify=verify_prose_output,
        max_retries=max_retries, max_tokens=384,
    )
    return ExplainResult(
        card_name=card_name,
        summary=gen.output.strip(),
        confidence=gen.confidence,
        verified=gen.verified,
        raw=gen,
        flags=list(flags),
        on_curve_prob=on_curve_prob,
        bottleneck_color=bottleneck_color,
    )


# =============================================================================
# Rule 0 worksheet
# =============================================================================


@dataclass
class Rule0Worksheet:
    """Pre-game discussion sheet — the shape a Commander player wants
    to glance at before sitting down. Different from the Analyze export
    in two ways: (1) it's tuned for a 30-second read, (2) it foregrounds
    the questions playgroups actually ask: bracket, kill turn, combos,
    interaction density, "anything weird?".
    """

    deck_name: str
    archetype: str
    color_identity: str
    power_overall: float
    power_tier: str           # "casual" / "focused" / "optimized" / "competitive" / "cEDH"
    bracket: str              # "1-precon" / "2-upgraded" / "3-optimized" / "4-cedh"
    avg_kill_turn: float      # from goldfish report, if available; else 0
    fastest_kill_turn: int    # 99th-percentile fastest kill in goldfish (0 if not run)
    interaction_count: int
    interaction_density: str  # "low" / "moderate" / "heavy"
    combo_lines: list[str] = field(default_factory=list)   # text snippets, e.g. "Thoracle + Demonic Consultation"
    notable_cards: list[str] = field(default_factory=list)  # legendary or signature cards
    pre_game_notes: list[str] = field(default_factory=list)  # bullet points the player should disclose
    land_count: int = 0
    ramp_count: int = 0
    draw_count: int = 0


_BRACKET_THRESHOLDS = [
    # (max_power_inclusive, label)
    (3.0, "1-precon"),
    (5.5, "2-upgraded"),
    (7.5, "3-optimized"),
    (9.0, "4-high-power"),
    (10.1, "5-cedh"),
]


def _bracket_for_power(overall: float) -> str:
    """Map overall power 1-10 to a Commander bracket label.

    Buckets follow the WotC bracket framework that emerged in 2024-2025:
      1 — Precon out of the box
      2 — Upgraded precon, no infinite combos
      3 — Optimized casual, may have combos
      4 — High-power, fast mana + tutors
      5 — cEDH
    """
    for ceiling, label in _BRACKET_THRESHOLDS:
        if overall <= ceiling:
            return label
    return "5-cedh"


def _interaction_density(count: int, format_total: int = 99) -> str:
    """Coarse label for interaction density.

    Calibrated against the Commander analyst targets (8-12 = healthy).
    """
    if count <= 4:
        return "low"
    if count <= 9:
        return "moderate"
    return "heavy"


def build_rule0_worksheet(
    *,
    deck_name: str,
    archetype: str,
    color_identity: list[str],
    power,
    analysis,
    goldfish_report=None,
    combo_lines: list[str] | None = None,
    notable_cards: list[str] | None = None,
    extra_notes: list[str] | None = None,
) -> Rule0Worksheet:
    """Assemble a Rule 0 worksheet from existing analysis outputs.

    `power` and `analysis` are the same objects produced by
    `analysis.power_level.estimate_power_level` and
    `analysis.static.analyze_deck`. `goldfish_report` is optional —
    when supplied we surface the avg kill turn and fastest 1%; without
    it those fields are 0.

    Combo lines + notable cards are caller-supplied because they may
    come from the Commander Spellbook integration (Phase 7) or from
    the deck's commander-card metadata. We don't make those up here.
    """
    overall = float(getattr(power, "overall", 0.0) or 0.0)
    tier = str(getattr(power, "tier", "")) or ""
    bracket = _bracket_for_power(overall)

    avg_kill = 0.0
    fastest = 0
    if goldfish_report is not None:
        avg_kill = float(getattr(goldfish_report, "average_kill_turn", 0.0) or 0.0)
        # Fastest kill: the smallest turn with non-zero rate in the
        # kill_turn_distribution. Goldfish reports keep this as a {turn: rate} dict.
        dist = getattr(goldfish_report, "kill_turn_distribution", {}) or {}
        if dist:
            non_zero = [t for t, rate in dist.items() if rate and rate > 0]
            if non_zero:
                fastest = int(min(non_zero))

    interaction_count = int(getattr(analysis, "interaction_count", 0) or 0)
    notes: list[str] = list(extra_notes or [])
    # Auto-add a "no interaction" warning to the bullets so the player
    # doesn't accidentally under-disclose this.
    if interaction_count <= 4:
        notes.insert(0, f"Light on interaction ({interaction_count} pieces) — disclose if asked.")
    if combo_lines:
        notes.insert(0, f"Has {len(combo_lines)} combo line{'s' if len(combo_lines) != 1 else ''} — disclose before pre-game agreement.")

    return Rule0Worksheet(
        deck_name=deck_name,
        archetype=archetype,
        color_identity="".join(color_identity) or "colorless",
        power_overall=round(overall, 1),
        power_tier=tier,
        bracket=bracket,
        avg_kill_turn=round(avg_kill, 1),
        fastest_kill_turn=fastest,
        interaction_count=interaction_count,
        interaction_density=_interaction_density(interaction_count),
        combo_lines=list(combo_lines or []),
        notable_cards=list(notable_cards or []),
        pre_game_notes=notes,
        land_count=int(getattr(analysis, "land_count", 0) or 0),
        ramp_count=int(getattr(analysis, "ramp_count", 0) or 0),
        draw_count=int(getattr(analysis, "draw_engine_count", 0) or 0),
    )


def render_rule0_text(ws: Rule0Worksheet) -> str:
    """Plain-text rendering for paste-into-Discord / printable use.

    Format is deliberately scannable (one fact per line, leading colon-aligned
    labels) so a player can read it in 10 seconds before a game.
    """
    lines = [
        f"Deck:            {ws.deck_name}",
        f"Colors:          {ws.color_identity}",
        f"Archetype:       {ws.archetype}",
        f"Power:           {ws.power_overall}/10 ({ws.power_tier})",
        f"Bracket:         {ws.bracket}",
        f"Lands / Ramp:    {ws.land_count} / {ws.ramp_count}",
        f"Card draw:       {ws.draw_count}",
        f"Interaction:     {ws.interaction_count} ({ws.interaction_density})",
    ]
    if ws.avg_kill_turn or ws.fastest_kill_turn:
        kill_line = f"Kill turn:       avg ~{ws.avg_kill_turn}"
        if ws.fastest_kill_turn:
            kill_line += f", fastest ~{ws.fastest_kill_turn}"
        lines.append(kill_line)
    if ws.combo_lines:
        lines.append("Combo lines:")
        for c in ws.combo_lines:
            lines.append(f"  - {c}")
    if ws.notable_cards:
        lines.append("Notable cards:")
        for n in ws.notable_cards[:6]:
            lines.append(f"  - {n}")
    if ws.pre_game_notes:
        lines.append("Pre-game notes:")
        for note in ws.pre_game_notes:
            lines.append(f"  - {note}")
    return "\n".join(lines) + "\n"
