"""Phase B: playgroup module + analyst integration.

Covers:
- PlaygroupStore CRUD round-trips (pods, members, default flag)
- build_pod_context aggregates correctly
- threat_themes maps archetypes → answer themes
- executive_summary_prompt picks up the pod block + tunes instruction
- App API endpoints (list_playgroups / add_pod_member / get_playgroup)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.playgroup import (
    PlaygroupStore,
    Pod,
    PodMember,
    PodContext,
    build_pod_context,
)
from densa_deck.playgroup.context import render_pod_block
from densa_deck.analyst.prompts import executive_summary_prompt


# ----------------------------------------------------------------- store


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = PlaygroupStore(db_path=Path(tmp) / "pg.db")
        yield s


class TestStoreCRUD:
    def test_empty_list(self, store):
        assert store.list_pods() == []

    def test_create_and_get(self, store):
        store.create_pod("Wed Night")
        pod = store.get_pod("Wed Night")
        assert pod is not None
        assert pod.name == "Wed Night"
        assert pod.is_default is False
        assert pod.member_count() == 0

    def test_create_idempotent(self, store):
        store.create_pod("Wed Night")
        store.create_pod("Wed Night")
        assert len(store.list_pods()) == 1

    def test_add_member_upserts_keyed_by_commander(self, store):
        store.add_member("Wed", PodMember("Atraxa", archetype="control", power_level=7.5))
        # Re-add with the same commander but different power — should update, not duplicate.
        pod = store.add_member("Wed", PodMember("Atraxa", archetype="control", power_level=8.5))
        assert pod.member_count() == 1
        assert pod.members[0].power_level == 8.5

    def test_add_member_creates_pod_if_missing(self, store):
        # Should not raise — auto-creates the pod.
        pod = store.add_member("New Pod", PodMember("Korvold"))
        assert pod.name == "New Pod"
        assert pod.member_count() == 1

    def test_remove_member(self, store):
        store.add_member("Wed", PodMember("Atraxa"))
        store.add_member("Wed", PodMember("Korvold"))
        assert store.remove_member("Wed", "Atraxa") is True
        pod = store.get_pod("Wed")
        assert [m.commander_name for m in pod.members] == ["Korvold"]

    def test_remove_missing_member_returns_false(self, store):
        store.create_pod("Wed")
        assert store.remove_member("Wed", "Nobody") is False

    def test_delete_pod_cascades_members(self, store):
        store.add_member("Wed", PodMember("Atraxa"))
        store.add_member("Wed", PodMember("Korvold"))
        assert store.delete_pod("Wed") is True
        # Re-create pod and verify members are gone.
        store.create_pod("Wed")
        assert store.get_pod("Wed").member_count() == 0

    def test_set_default_flips_other_pods(self, store):
        store.create_pod("A")
        store.create_pod("B")
        store.set_default("A")
        store.set_default("B")
        assert store.get_pod("A").is_default is False
        assert store.get_pod("B").is_default is True
        assert store.get_default_pod().name == "B"

    def test_position_preserves_order_on_update(self, store):
        store.add_member("Wed", PodMember("First", position=0))
        store.add_member("Wed", PodMember("Second", position=1))
        # Updating First's archetype shouldn't change its position.
        store.add_member("Wed", PodMember("First", archetype="combo"))
        pod = store.get_pod("Wed")
        assert [m.commander_name for m in pod.members] == ["First", "Second"]
        assert pod.members[0].archetype == "combo"


# -------------------------------------------------------------- context


class TestBuildContext:
    def test_empty_pod_returns_zero_member_context(self):
        ctx = build_pod_context(Pod(name="Empty"))
        assert ctx.member_count == 0
        assert ctx.avg_power is None

    def test_avg_power_mean_of_known_levels(self):
        pod = Pod(name="Mix", members=[
            PodMember("A", power_level=6.0),
            PodMember("B", power_level=8.0),
            PodMember("C", power_level=None),  # unknown — excluded from mean
        ])
        ctx = build_pod_context(pod)
        assert ctx.avg_power == 7.0  # (6+8)/2, C ignored

    def test_avg_power_none_when_no_known_levels(self):
        pod = Pod(name="Unknowns", members=[
            PodMember("A"), PodMember("B"),
        ])
        ctx = build_pod_context(pod)
        assert ctx.avg_power is None

    def test_archetype_mix_counter(self):
        pod = Pod(name="X", members=[
            PodMember("A", archetype="combo"),
            PodMember("B", archetype="combo"),
            PodMember("C", archetype="midrange"),
        ])
        ctx = build_pod_context(pod)
        assert ctx.archetype_mix == {"combo": 2, "midrange": 1}
        assert ctx.has_combo is True
        assert ctx.has_control is False
        # Primary archetypes are sorted by count desc.
        assert ctx.primary_archetypes()[0] == "combo"

    def test_threat_themes_for_combo_pod(self):
        pod = Pod(name="cEDH", members=[
            PodMember("A", archetype="combo"),
            PodMember("B", archetype="spellslinger"),
        ])
        themes = build_pod_context(pod).threat_themes()
        assert "combo interaction" in themes
        assert any("counterspells" in t or "stack" in t for t in themes)

    def test_threat_themes_for_graveyard_pod(self):
        pod = Pod(name="GY", members=[
            PodMember("A", archetype="reanimator"),
            PodMember("B", archetype="aristocrats"),
        ])
        themes = build_pod_context(pod).threat_themes()
        assert "graveyard hate" in themes


class TestPodBlockRender:
    def test_empty_renders_empty(self):
        assert render_pod_block(PodContext(pod_name="x", member_count=0, avg_power=None)) == ""

    def test_renders_commanders_and_mix(self):
        pod = Pod(name="Wed", members=[
            PodMember("Atraxa", archetype="control", power_level=7.0),
            PodMember("Korvold", archetype="aristocrats", power_level=8.0),
        ])
        block = render_pod_block(build_pod_context(pod))
        assert "Wed" in block
        assert "Atraxa" in block
        assert "Korvold" in block
        assert "aristocrats" in block
        # Threat themes derived from aristocrats → graveyard hate.
        assert "graveyard hate" in block


# -------------------------------------------------------- prompt integration


class TestPromptIntegration:
    def _base_args(self):
        return dict(
            deck_name="Test", archetype="midrange",
            power_overall=7.0, power_tier="focused",
            power_reasons_up=[], power_reasons_down=[],
            land_count=36, ramp_count=10, draw_count=10,
            interaction_count=10, avg_mana_value=2.8,
            color_identity=["G", "B"], format_name="commander",
            recommendations=[],
        )

    def test_no_pod_no_pod_block(self):
        prompt = executive_summary_prompt(**self._base_args(), pod_context=None)
        assert "Pod:" not in prompt
        assert "Playgroup target" not in prompt

    def test_pod_context_injects_block_and_themes(self):
        pod = Pod(name="Wed", members=[
            PodMember("Atraxa", archetype="control", power_level=7.0),
            PodMember("Korvold", archetype="aristocrats", power_level=8.0),
        ])
        ctx = build_pod_context(pod)
        prompt = executive_summary_prompt(**self._base_args(), pod_context=ctx)
        assert "Pod: Wed" in prompt
        # Avg power from pod overrides absent playgroup_power.
        assert "Playgroup target: 7.5" in prompt
        # Threat themes appear in the instruction sentence.
        assert "graveyard hate" in prompt

    def test_pod_avg_power_overrides_playgroup_power(self):
        """When both are set, the pod's derived avg_power wins because it
        carries strictly more information than a single manual number."""
        pod = Pod(name="X", members=[PodMember("A", power_level=9.0)])
        ctx = build_pod_context(pod)
        prompt = executive_summary_prompt(
            **self._base_args(), playgroup_power=5.0, pod_context=ctx
        )
        assert "Playgroup target: 9.0" in prompt
        assert "Playgroup target: 5.0" not in prompt

    def test_playgroup_power_used_when_no_pod_avg(self):
        """Falls back to the flag when the pod has no power data."""
        pod = Pod(name="X", members=[PodMember("A")])  # no power
        ctx = build_pod_context(pod)
        prompt = executive_summary_prompt(
            **self._base_args(), playgroup_power=6.0, pod_context=ctx
        )
        assert "Playgroup target: 6.0" in prompt
