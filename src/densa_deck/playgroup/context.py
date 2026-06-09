"""Build a `PodContext` from a `Pod` — the form the analyst layer consumes."""

from __future__ import annotations

from collections import Counter

from densa_deck.playgroup.models import Pod, PodContext


_COMBO_ARCHETYPES = {"combo", "aristocrats", "reanimator"}
_CONTROL_ARCHETYPES = {"control", "stax"}
_AGGRO_ARCHETYPES = {"aggro", "voltron", "tribal"}


def build_pod_context(pod: Pod) -> PodContext:
    """Roll up pod aggregates the analyst consumes."""
    if pod is None or not pod.members:
        return PodContext(
            pod_name=getattr(pod, "name", "") or "",
            member_count=0,
            avg_power=None,
        )

    powers = [m.power_level for m in pod.members if m.power_level is not None]
    avg = round(sum(powers) / len(powers), 2) if powers else None

    archetype_mix = Counter()
    for m in pod.members:
        archetype_mix[(m.archetype or "unknown").lower()] += 1

    has_combo = any(a in _COMBO_ARCHETYPES for a in archetype_mix.keys())
    has_control = any(a in _CONTROL_ARCHETYPES for a in archetype_mix.keys())
    has_aggro = any(a in _AGGRO_ARCHETYPES for a in archetype_mix.keys())

    commanders = [m.commander_name for m in pod.members]
    notes = [m.notes for m in pod.members if m.notes]

    return PodContext(
        pod_name=pod.name,
        member_count=len(pod.members),
        avg_power=avg,
        archetype_mix=dict(archetype_mix),
        commanders=commanders,
        has_combo=has_combo,
        has_control=has_control,
        has_aggro=has_aggro,
        notes=notes,
    )


def render_pod_block(ctx: PodContext) -> str:
    """One-paragraph render of a pod context for inclusion in LLM prompts.

    Kept compact so small models aren't overwhelmed. The narrator reads:
      "Pod: <name> — <N> players, avg power <X>. Commanders: A, B, C.
       Archetype mix: combo (1), midrange (2). Threat themes: graveyard
       hate, combo interaction."
    """
    if ctx.member_count == 0:
        return ""
    parts = [f"Pod: {ctx.pod_name} — {ctx.member_count} player(s)"]
    if ctx.avg_power is not None:
        parts.append(f"avg power {ctx.avg_power:.1f}/10")
    line1 = ", ".join(parts)
    line2 = f"Commanders: {', '.join(ctx.commanders[:6])}" if ctx.commanders else ""
    mix_bits = ", ".join(f"{k} ({v})" for k, v in ctx.archetype_mix.items() if k != "unknown")
    line3 = f"Archetype mix: {mix_bits}" if mix_bits else ""
    themes = ctx.threat_themes()
    line4 = f"Threat themes: {', '.join(themes)}" if themes else ""
    return "\n".join([line for line in (line1, line2, line3, line4) if line])
