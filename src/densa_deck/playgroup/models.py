"""Pod data shapes.

A `Pod` is a named playgroup. Each `PodMember` is one player slot — usually
identified by the commander they bring most often plus a coarse archetype
tag (control / aggro / combo / midrange / stax / aristocrats / etc.) and
an optional 1-10 power read.

`PodContext` is the *derived* form the analyst consumes: aggregates rolled
up from the members. Built once per analysis call by `build_pod_context`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Coarse archetype tags used to bucket pod members. Free-form text is
# accepted on the CLI for forward-compat; these are the canonical labels
# the bracket logic and prompts speak.
KNOWN_ARCHETYPES = (
    "aggro",
    "midrange",
    "control",
    "combo",
    "aristocrats",
    "stax",
    "tokens",
    "voltron",
    "ramp",
    "spellslinger",
    "tribal",
    "reanimator",
    "lands",
    "group_hug",
    "chaos",
    "unknown",
)


@dataclass
class PodMember:
    """One player slot in a pod."""

    commander_name: str
    archetype: str = "unknown"
    power_level: float | None = None  # 1.0-10.0, None when unknown
    notes: str = ""
    position: int = 0  # display order, 0-indexed


@dataclass
class Pod:
    """A named playgroup with its member list."""

    name: str
    members: list[PodMember] = field(default_factory=list)
    is_default: bool = False
    created_at: str = ""

    def member_count(self) -> int:
        return len(self.members)

    def with_member(self, member: PodMember) -> "Pod":
        """Return a copy with `member` upserted by commander_name."""
        out = [m for m in self.members if m.commander_name.lower() != member.commander_name.lower()]
        out.append(member)
        out.sort(key=lambda m: m.position)
        return Pod(name=self.name, members=out, is_default=self.is_default, created_at=self.created_at)


@dataclass
class PodContext:
    """Derived aggregates the analyst layer actually consumes.

    Kept separate from `Pod` so the storage shape can evolve without
    breaking prompt/bracket signatures, and so test fixtures can construct
    a PodContext without touching SQLite.
    """

    pod_name: str
    member_count: int
    avg_power: float | None  # mean of known power levels, None when no member has one
    archetype_mix: dict[str, int] = field(default_factory=dict)
    commanders: list[str] = field(default_factory=list)
    has_combo: bool = False  # any pod member tagged combo / aristocrats
    has_control: bool = False
    has_aggro: bool = False
    notes: list[str] = field(default_factory=list)  # non-empty member notes

    def primary_archetypes(self, top_n: int = 3) -> list[str]:
        """Return the most common archetypes in the pod, descending."""
        return [k for k, _ in sorted(self.archetype_mix.items(), key=lambda kv: -kv[1])[:top_n]]

    def threat_themes(self) -> list[str]:
        """Coarse themes the analyst should answer with hate cards.

        e.g. if any pod member plays aristocrats/reanimator, "graveyard
        hate" appears here so the add-candidates layer can bias toward
        graveyard answers without a second LLM call.
        """
        themes: list[str] = []
        if self.has_combo:
            themes.append("combo interaction")
        if "reanimator" in self.archetype_mix or "aristocrats" in self.archetype_mix:
            themes.append("graveyard hate")
        if "tokens" in self.archetype_mix:
            themes.append("board wipes")
        if "stax" in self.archetype_mix:
            themes.append("artifact / enchantment removal")
        if "spellslinger" in self.archetype_mix:
            themes.append("counterspells or stack interaction")
        if "voltron" in self.archetype_mix:
            themes.append("targeted removal for commanders")
        return themes
