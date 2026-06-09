"""Playgroup-aware tuning — pod profiles fed into analyst + bracket logic.

A *pod* is a named group of commanders the user regularly plays against.
The analyst uses the pod's aggregate (average power, archetype mix, color
spread, commander list) to tune cuts/adds and the executive summary so
recommendations land for *that* table — not a generic optimum.

Local-only by design: pod data lives in `~/.densa-deck/playgroup.db` and
never leaves the machine. This is a textbook local-AI use case — the
playgroup is *exactly* the kind of context cloud LLMs can't have.
"""

from densa_deck.playgroup.models import PodMember, Pod, PodContext
from densa_deck.playgroup.storage import PlaygroupStore
from densa_deck.playgroup.context import build_pod_context

__all__ = [
    "PodMember",
    "Pod",
    "PodContext",
    "PlaygroupStore",
    "build_pod_context",
]
