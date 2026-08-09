"""Tests for colour-aware mana: cost parsing, payment solving, and the
colour-weighted mana curve.

The simulator used to model mana as a single integer, so a five-colour pile
played like a mono deck. These tests pin the behaviour that replaced it.
"""

import pytest

from densa_deck.goldfish import effects as fx_mod
from densa_deck.goldfish.mana import (
    ManaCost,
    can_pay,
    card_mana_cost,
    describe_dispersion,
    clear_mana_caches,
    parse_mana_cost,
    pay_cost,
    produces_mana,
    source_colors,
)
from densa_deck.goldfish.reliability import ReliabilityCollector
from densa_deck.goldfish.runner import run_goldfish_batch
from densa_deck.goldfish.state import ANY_COLOR, GameState, Permanent
from densa_deck.models import Card, CardLayout, CardTag, Deck, DeckEntry, Format, Zone

W, U, B, R, G, C = (frozenset({c}) for c in "WUBRGC")


@pytest.fixture(autouse=True)
def _clear_cache():
    # Both caches are keyed by card name. Synthetic test cards reuse names
    # across files with different text, so they must be dropped per test.
    fx_mod.clear_cache()
    clear_mana_caches()
    yield
    fx_mod.clear_cache()
    clear_mana_caches()


def _card(name, oracle="", mana_cost="", produced=None, **kw):
    kw.setdefault("cmc", 0.0)
    return Card(
        scryfall_id=f"id-{name}",
        oracle_id=f"oracle-{name}",
        name=name,
        layout=CardLayout.NORMAL,
        oracle_text=oracle,
        mana_cost=mana_cost,
        produced_mana=produced or [],
        **kw,
    )


def _entry(card, qty=1, zone=Zone.MAINBOARD):
    return DeckEntry(card_name=card.name, quantity=qty, zone=zone, card=card)


def _land(name, colors):
    return _card(name, oracle="{T}: Add mana.", is_land=True, produced=list(colors))


class TestParseManaCost:
    def test_generic_and_pips(self):
        cost = parse_mana_cost("{2}{W}{U}")
        assert cost.generic == 2
        assert cost.pips["W"] == 1 and cost.pips["U"] == 1
        assert cost.total == 4

    def test_repeated_pips(self):
        cost = parse_mana_cost("{B}{B}{B}")
        assert cost.pips["B"] == 3
        assert cost.total == 3

    def test_empty_cost(self):
        assert parse_mana_cost("").total == 0

    def test_x_contributes_nothing(self):
        # An X spell is always castable for X=0.
        cost = parse_mana_cost("{X}{R}")
        assert cost.total == 1

    def test_hybrid_accepts_either_colour(self):
        cost = parse_mana_cost("{W/U}")
        assert cost.hybrid == [frozenset({"W", "U"})]
        assert cost.total == 1

    def test_twobrid_takes_the_colour_branch(self):
        cost = parse_mana_cost("{2/W}")
        assert cost.hybrid == [frozenset({"W"})]

    def test_phyrexian_never_blocks_a_cast(self):
        cost = parse_mana_cost("{W/P}")
        assert cost.is_colorless()
        assert cost.total == 0

    def test_colorless_pip_is_distinct_from_generic(self):
        cost = parse_mana_cost("{C}")
        assert cost.pips["C"] == 1


class TestPaymentSolver:
    def test_enough_of_the_right_colour(self):
        assert can_pay(parse_mana_cost("{W}{W}"), [W, W])

    def test_wrong_colours_cannot_pay(self):
        # Three white sources do not cast a blue spell.
        assert not can_pay(parse_mana_cost("{U}"), [W, W, W])

    def test_count_without_colour_is_not_enough(self):
        assert not can_pay(parse_mana_cost("{W}{U}{B}"), [W, W, W])

    def test_greedy_trap_dual_must_be_assigned_correctly(self):
        # A naive solver assigns the dual to W, then the plain W land can't
        # cover U and it wrongly reports failure.
        dual = frozenset({"W", "U"})
        assert can_pay(parse_mana_cost("{W}{U}"), [dual, W])
        assert can_pay(parse_mana_cost("{W}{U}"), [dual, U])

    def test_generic_paid_by_any_source(self):
        assert can_pay(parse_mana_cost("{2}{W}"), [W, C, C])

    def test_generic_still_needs_enough_units(self):
        assert not can_pay(parse_mana_cost("{2}{W}"), [W, C])

    def test_colourless_source_cannot_pay_a_pip(self):
        assert not can_pay(parse_mana_cost("{2}{W}"), [C, C, C])

    def test_any_colour_source_pays_anything(self):
        assert can_pay(parse_mana_cost("{W}{U}{B}"), [ANY_COLOR] * 3)

    def test_hybrid_satisfied_by_either_half(self):
        assert can_pay(parse_mana_cost("{W/U}"), [U])
        assert can_pay(parse_mana_cost("{W/U}"), [W])
        assert not can_pay(parse_mana_cost("{W/U}"), [B])

    def test_pay_cost_returns_the_units_consumed(self):
        chosen = pay_cost(parse_mana_cost("{1}{W}"), [W, C])
        assert chosen is not None and len(chosen) == 2

    def test_pay_cost_returns_none_when_unpayable(self):
        assert pay_cost(parse_mana_cost("{W}"), [U]) is None


class TestSourceColors:
    def test_reads_produced_mana(self):
        assert source_colors(_land("Hallowed Fountain", "WU")) == frozenset({"W", "U"})

    def test_rainbow_land(self):
        tower = _land("Command Tower", "WUBRG")
        assert source_colors(tower) == frozenset({"W", "U", "B", "R", "G"})

    def test_unknown_source_is_colourless_not_rainbow(self):
        # Guessing "any colour" for an unreadable source would hide screw.
        assert source_colors(_card("Mystery")) == frozenset({"C"})

    def test_none_card_is_colourless(self):
        assert source_colors(None) == frozenset({"C"})

    def test_dispersion_counts_a_dual_for_both_colours(self):
        counts = describe_dispersion([frozenset({"W", "U"}), frozenset({"W"})])
        assert counts["W"] == 2
        assert counts["U"] == 1


class TestColorAwareGameState:
    def test_tapping_records_source_colours(self):
        state = GameState()
        forest = _land("Forest", "G")
        forest.tags = [CardTag.MANA_ROCK]
        state.battlefield.append(Permanent(entry=_entry(forest), summoning_sick=False))
        state.tap_for_mana(1)
        assert state.mana_units() == [frozenset({"G"})]

    def test_cannot_pay_wrong_colour(self):
        state = GameState()
        state.battlefield.append(
            Permanent(entry=_entry(_land("Forest", "G")), summoning_sick=False))
        state.tap_for_mana(1)
        assert state.can_pay_for(parse_mana_cost("{U}")) is False
        assert state.pay_for(parse_mana_cost("{U}")) is False
        # Nothing was spent on the failed attempt.
        assert state.mana_pool == 1

    def test_pay_for_consumes_the_right_mana(self):
        state = GameState()
        for name, colors in (("Forest", "G"), ("Island", "U")):
            state.battlefield.append(
                Permanent(entry=_entry(_land(name, colors)), summoning_sick=False))
        state.tap_for_mana(2)
        assert state.pay_for(parse_mana_cost("{G}{U}")) is True
        assert state.mana_pool == 0

    def test_treasures_pay_any_colour(self):
        state = GameState()
        state.treasures = 2
        state.tap_for_mana(2)
        assert state.pay_for(parse_mana_cost("{W}{B}")) is True

    def test_directly_set_pool_is_treated_as_flexible(self):
        # Older callers set mana_pool as a bare integer; that must keep working.
        state = GameState()
        state.mana_pool = 3
        assert state.can_pay_for(parse_mana_cost("{1}{W}{U}")) is True

    def test_floating_mana_clears_between_turns(self):
        state = GameState()
        state.add_mana(frozenset({"G"}), 2)
        state.begin_turn()
        assert state.mana_pool == 0
        assert state.floating_colors == []

    def test_ritual_adds_its_own_colour(self):
        state = GameState()
        dark_ritual = _card("Dark Ritual", "Add {B}{B}{B}.", is_instant=True)
        state.cast_spell(_entry(dark_ritual))
        assert state.mana_pool == 3
        assert state.can_pay_for(parse_mana_cost("{B}{B}{B}")) is True
        assert state.can_pay_for(parse_mana_cost("{U}{U}{U}")) is False

    def test_color_sources_in_play_counts_duals_twice(self):
        state = GameState()
        state.battlefield.append(
            Permanent(entry=_entry(_land("Hallowed Fountain", "WU")), summoning_sick=False))
        counts = state.color_sources_in_play()
        assert counts["W"] == 1 and counts["U"] == 1


def _mono_deck(color="G", pips=1):
    """A deck whose lands all produce `color` and whose spells need `pips` of it."""
    entries = []
    for i in range(40):
        entries.append(_entry(_land(f"Land{i}", color)))
    cost = ("{" + color + "}") * pips
    for i in range(20):
        spell = _card(f"Spell{i}", mana_cost=cost, cmc=float(pips))
        entries.append(_entry(spell))
    return Deck(name="mono", entries=entries, format=Format.COMMANDER)


def _off_color_deck():
    """Lands make green; spells demand blue. Should be unable to cast anything."""
    entries = [_entry(_land(f"Land{i}", "G")) for i in range(40)]
    for i in range(20):
        entries.append(_entry(_card(f"Blue{i}", mana_cost="{U}", cmc=1.0)))
    return Deck(name="offcolor", entries=entries, format=Format.COMMANDER)


class TestManaReliabilityReport:
    def test_report_is_attached_to_the_goldfish_batch(self):
        report = run_goldfish_batch(_mono_deck(), simulations=20, seed=1)
        assert report.mana_reliability is not None
        assert report.mana_reliability.games_analyzed == 20

    def test_reliability_can_be_disabled(self):
        report = run_goldfish_batch(_mono_deck(), simulations=10, seed=1, reliability=False)
        assert report.mana_reliability is None

    def test_mono_colour_deck_is_solid(self):
        report = run_goldfish_batch(_mono_deck(), simulations=60, seed=2)
        green = [c for c in report.mana_reliability.colors if c.color == "G"][0]
        assert green.verdict == "solid"
        assert green.on_curve_hit_rate > 0.9

    def test_off_colour_deck_is_short(self):
        report = run_goldfish_batch(_off_color_deck(), simulations=60, seed=3)
        blue = [c for c in report.mana_reliability.colors if c.color == "U"][0]
        assert blue.verdict == "short"
        assert blue.on_curve_hit_rate == 0.0

    def test_off_colour_deck_reports_colour_screw_not_mana_screw(self):
        # There is plenty of mana; it's simply the wrong colour.
        report = run_goldfish_batch(_off_color_deck(), simulations=60, seed=4)
        assert report.mana_reliability.color_screw_rate > 0.5

    def test_curve_records_the_colour_requirement_per_turn(self):
        report = run_goldfish_batch(_mono_deck(pips=2), simulations=30, seed=5)
        curve = report.mana_reliability.curve
        assert curve
        point = curve[0]
        assert point.requirement.get("G") == 2

    def test_sources_in_deck_are_counted(self):
        report = run_goldfish_batch(_mono_deck(), simulations=20, seed=6)
        green = [c for c in report.mana_reliability.colors if c.color == "G"][0]
        assert green.sources_in_deck == 40

    def test_over_extended_flag_needs_three_short_colours(self):
        report = run_goldfish_batch(_off_color_deck(), simulations=20, seed=7)
        # Only one colour is demanded, so this is short but not over-extended.
        assert report.mana_reliability.over_extended is False

    def test_summary_line_is_human_readable(self):
        report = run_goldfish_batch(_off_color_deck(), simulations=20, seed=8)
        assert "Blue" in report.mana_reliability.summary_line()

    def test_reliability_sampling_caps_games_analyzed(self):
        report = run_goldfish_batch(
            _mono_deck(), simulations=50, seed=9, reliability_games=10)
        assert report.mana_reliability.games_analyzed == 10
        assert report.simulations == 50

    def test_collector_ignores_sideboard(self):
        deck = _mono_deck()
        deck.entries.append(
            _entry(_card("SB", mana_cost="{U}", cmc=1.0), zone=Zone.SIDEBOARD))
        collector = ReliabilityCollector(deck, max_turns=10)
        assert "SB" not in collector.costs


class TestLandSourceQuality:
    """Land data is where the colour model is easiest to get wrong.

    Scryfall records `produced_mana: []` for fetchlands because they
    sacrifice rather than tap, which naively reads as "colourless" — the
    exact opposite of what a fetch does for a mana base.
    """

    def _fetch(self, name, text):
        return _card(name, oracle=text, is_land=True)

    def test_fetchland_reads_colours_from_searched_types(self):
        heath = self._fetch(
            "Windswept Heath",
            "{T}, Pay 1 life, Sacrifice this land: Search your library for a "
            "Forest or Plains card, put it onto the battlefield, then shuffle.",
        )
        assert source_colors(heath) == frozenset({"G", "W"})

    def test_island_in_the_text_does_not_truncate_the_scope(self):
        # "Island" ends in "land" — a careless terminator loses every type.
        delta = self._fetch(
            "Polluted Delta",
            "{T}, Pay 1 life, Sacrifice this land: Search your library for an "
            "Island or Swamp card, put it onto the battlefield, then shuffle.",
        )
        assert source_colors(delta) == frozenset({"U", "B"})

    def test_fetch_naming_two_types_across_two_clauses(self):
        verge = _card(
            "Krosan Verge", is_land=True, produced=["C"],
            oracle=(
                "This land enters tapped.\n{T}: Add {C}.\n{2}, {T}, Sacrifice "
                "this land: Search your library for a Forest card and a Plains "
                "card, put them onto the battlefield tapped, then shuffle."
            ),
        )
        # Taps for {C} and gives access to G and W; the solver still only
        # ever spends the unit once.
        assert source_colors(verge) == frozenset({"C", "G", "W"})

    def test_generic_basic_fetch_is_all_colours(self):
        wilds = self._fetch(
            "Evolving Wilds",
            "{T}, Sacrifice this land: Search your library for a basic land "
            "card, put it onto the battlefield tapped, then shuffle.",
        )
        assert source_colors(wilds) == frozenset({"W", "U", "B", "R", "G"})

    def test_land_that_makes_no_mana_is_not_a_source(self):
        maze = self._fetch("Maze of Ith", "{T}: Untap target attacking creature.")
        assert produces_mana(maze) is False

    def test_land_with_no_text_is_still_assumed_to_tap(self):
        # Unresolved or synthetic cards must not silently produce zero mana.
        assert produces_mana(_card("Land", is_land=True)) is True

    def test_normal_land_is_a_source(self):
        assert produces_mana(_land("Forest", "G")) is True


class TestConditionalTappedLands:
    def _state_with_lands(self, *lands):
        state = GameState()
        for land in lands:
            state.battlefield.append(
                Permanent(entry=_entry(land), summoning_sick=False))
        return state

    def _play(self, state, card):
        state.play_land(_entry(card))
        return state.battlefield[-1].tapped

    def test_check_land_enters_tapped_with_no_matching_type(self):
        fortress = _card(
            "Glacial Fortress", is_land=True, produced=["W", "U"],
            oracle="This land enters tapped unless you control a Plains or an Island.",
        )
        state = self._state_with_lands(_land("Forest", "G"))
        assert self._play(state, fortress) is True

    def test_check_land_enters_untapped_with_a_matching_type(self):
        fortress = _card(
            "Glacial Fortress", is_land=True, produced=["W", "U"],
            oracle="This land enters tapped unless you control a Plains or an Island.",
        )
        state = self._state_with_lands(_land("Island", "U"))
        assert self._play(state, fortress) is False

    def test_fast_land_untapped_early_tapped_late(self):
        coast = _card(
            "Seachrome Coast", is_land=True, produced=["W", "U"],
            oracle="This land enters tapped unless you control two or fewer other lands.",
        )
        early = self._state_with_lands(_land("A", "W"))
        assert self._play(early, coast) is False
        late = self._state_with_lands(*[_land(f"L{i}", "W") for i in range(4)])
        assert self._play(late, coast) is True

    def test_unreadable_condition_stays_conservative(self):
        weird = _card(
            "Abandoned Campground", is_land=True, produced=["W"],
            oracle="This land enters tapped unless a player has 13 or less life.",
        )
        state = self._state_with_lands()
        assert self._play(state, weird) is True

    def test_unconditional_tapped_land_still_enters_tapped(self):
        triome = _card(
            "Raugrin Triome", is_land=True, produced=["R", "U", "W"],
            oracle="This land enters tapped.",
        )
        assert self._play(self._state_with_lands(), triome) is True

    def test_shockland_is_assumed_paid_for(self):
        shock = _card(
            "Hallowed Fountain", is_land=True, produced=["W", "U"],
            oracle="As this land enters, you may pay 2 life. If you don't, it enters tapped.",
        )
        assert self._play(self._state_with_lands(), shock) is False


class TestReportReachesConsumers:
    """The report is only worth building if the surfaces actually show it."""

    def test_desktop_api_serialises_the_report(self):
        from densa_deck.app.api import _mana_reliability_to_dict
        report = run_goldfish_batch(_mono_deck(), simulations=20, seed=1)
        payload = _mana_reliability_to_dict(report.mana_reliability)
        assert payload["games_analyzed"] == 20
        assert payload["colors"] and payload["curve"]
        assert "summary" in payload
        import json
        json.dumps(payload)  # must survive the JSON bridge to the frontend

    def test_serialiser_handles_disabled_reliability(self):
        from densa_deck.app.api import _mana_reliability_to_dict
        assert _mana_reliability_to_dict(None) is None

    def test_analyst_deck_sheet_gets_a_mana_block(self):
        from densa_deck.analyst.coach import build_deck_sheet
        report = run_goldfish_batch(_off_color_deck(), simulations=30, seed=2)
        sheet = build_deck_sheet(
            deck_name="D", archetype="midrange", color_identity=["U"],
            power_overall=5.0, power_tier="mid", land_count=40, ramp_count=0,
            draw_count=0, interaction_count=0, avg_mana_value=1.0,
            deck_cards=["Blue0"], mana_reliability=report.mana_reliability,
        )
        assert "[MANA]" in sheet
        assert "Blue" in sheet
        assert "castable on curve" in sheet

    def test_deck_sheet_without_mana_data_is_unchanged(self):
        from densa_deck.analyst.coach import build_deck_sheet
        sheet = build_deck_sheet(
            deck_name="D", archetype="midrange", color_identity=["U"],
            power_overall=5.0, power_tier="mid", land_count=40, ramp_count=0,
            draw_count=0, interaction_count=0, avg_mana_value=1.0,
            deck_cards=["Blue0"],
        )
        assert "[MANA]" not in sheet


class TestCastabilityReconciliation:
    """There are two answers to "can I cast this" and they must never be
    presented as if they were the same number."""

    def _deck_card_names(self):
        return _mono_deck()

    def test_estimate_is_labelled_as_estimated(self):
        from densa_deck.analysis.castability import analyze_castability
        deck = _mono_deck(pips=2)
        report = analyze_castability(deck, {"G": 40})
        assert report.source == "estimated"
        assert all(c.source == "estimated" for c in report.cards)

    def test_measured_rates_supersede_the_estimate(self):
        from densa_deck.analysis.castability import analyze_castability
        deck = _mono_deck(pips=2)
        name = [e.card.name for e in deck.entries
                if e.card and not e.card.is_land][0]
        report = analyze_castability(deck, {"G": 40}, measured_rates={name: 0.123})
        card = [c for c in report.cards if c.name == name][0]
        assert card.on_curve_probability == 0.123
        assert card.source == "measured"

    def test_partial_measurement_is_reported_as_mixed(self):
        from densa_deck.analysis.castability import analyze_castability
        deck = _mono_deck(pips=2)
        names = [e.card.name for e in deck.entries
                 if e.card and not e.card.is_land]
        report = analyze_castability(deck, {"G": 40}, measured_rates={names[0]: 0.5})
        if len(report.cards) > 1:
            assert report.source == "mixed"

    def test_measured_rates_flow_from_a_real_goldfish_batch(self):
        from densa_deck.analysis.castability import analyze_castability
        deck = _off_color_deck()
        m = run_goldfish_batch(deck, simulations=30, seed=5).mana_reliability
        rates = {n: r for n, _c, r in m.card_on_curve}
        report = analyze_castability(deck, {"G": 40}, measured_rates=rates)
        # The blue spells genuinely can't be cast off green sources.
        assert report.source in ("measured", "mixed")


class TestExportsCarryColour:
    def test_markdown_export_includes_the_colour_curve(self):
        from densa_deck.analysis.static import analyze_deck as _analyze
        from densa_deck.export.exporter import export_markdown
        deck = _off_color_deck()
        m = run_goldfish_batch(deck, simulations=30, seed=6).mana_reliability
        md = export_markdown(_analyze(deck), mana_reliability=m)
        assert "## Colour-Weighted Mana Curve" in md
        assert "On-curve castability" in md
        assert "Blue" in md

    def test_markdown_export_without_colour_data_is_unchanged(self):
        from densa_deck.analysis.static import analyze_deck as _analyze
        from densa_deck.export.exporter import export_markdown
        md = export_markdown(_analyze(_mono_deck()))
        assert "Colour-Weighted Mana Curve" not in md

    def test_json_export_includes_the_report_and_source_label(self):
        import json
        from densa_deck.analysis.castability import analyze_castability
        from densa_deck.analysis.static import analyze_deck as _analyze
        from densa_deck.export.exporter import export_json
        deck = _off_color_deck()
        m = run_goldfish_batch(deck, simulations=30, seed=7).mana_reliability
        rates = {n: r for n, _c, r in m.card_on_curve}
        payload = json.loads(export_json(
            _analyze(deck),
            castability=analyze_castability(deck, {"G": 40}, measured_rates=rates),
            mana_reliability=m,
        ))
        assert payload["mana_reliability"]["colors"]
        assert payload["castability"]["source"] in ("measured", "mixed")

    def test_html_export_threads_the_report_through(self):
        from densa_deck.analysis.static import analyze_deck as _analyze
        from densa_deck.export.exporter import export_html
        deck = _off_color_deck()
        m = run_goldfish_batch(deck, simulations=30, seed=8).mana_reliability
        html = export_html(_analyze(deck), mana_reliability=m)
        assert "Colour-Weighted Mana Curve" in html


class TestGauntletCarriesColour:
    def test_gauntlet_attaches_the_report(self):
        from densa_deck.matchup.gauntlet import run_gauntlet
        from densa_deck.matchup.archetypes import get_default_gauntlet
        report = run_gauntlet(
            _off_color_deck(), archetypes=get_default_gauntlet()[:1],
            simulations=10, seed=1)
        assert report.mana_reliability is not None
        assert report.mana_reliability.colors

    def test_gauntlet_reliability_can_be_disabled(self):
        from densa_deck.matchup.gauntlet import run_gauntlet
        from densa_deck.matchup.archetypes import get_default_gauntlet
        report = run_gauntlet(
            _off_color_deck(), archetypes=get_default_gauntlet()[:1],
            simulations=10, seed=1, reliability=False)
        assert report.mana_reliability is None


class TestFormatCoverage:
    """EDH and Standard both simulate at the right life total, and the
    banned list is enforced from Scryfall's per-format legality data."""

    def test_commander_starts_at_forty(self):
        from densa_deck.formats.profiles import starting_life_for
        from densa_deck.models import Format
        assert starting_life_for(Format.COMMANDER) == 40

    def test_standard_and_the_60_card_formats_start_at_twenty(self):
        from densa_deck.formats.profiles import starting_life_for
        from densa_deck.models import Format
        for fmt in (Format.STANDARD, Format.MODERN, Format.PIONEER,
                    Format.LEGACY, Format.VINTAGE, Format.PAUPER):
            assert starting_life_for(fmt) == 20, fmt

    def test_brawl_oathbreaker_and_duel_are_not_forty(self):
        # These used to inherit Commander's 40 from an inline check, which
        # overstated how long those decks survive.
        from densa_deck.formats.profiles import starting_life_for
        from densa_deck.models import Format
        assert starting_life_for(Format.BRAWL) == 25
        assert starting_life_for(Format.OATHBREAKER) == 20
        assert starting_life_for(Format.DUEL) == 20

    def test_unknown_format_falls_back_to_twenty(self):
        from densa_deck.formats.profiles import starting_life_for
        assert starting_life_for(None) == 20

    def test_simulation_uses_the_format_life_total(self):
        from densa_deck.models import Format
        deck = _mono_deck()
        deck.format = Format.COMMANDER
        cmdr = run_goldfish_batch(deck, simulations=5, seed=1, reliability=False)
        deck.format = Format.STANDARD
        std = run_goldfish_batch(deck, simulations=5, seed=1, reliability=False)
        # Same deck, lower life total to chew through: Standard kills sooner.
        assert std.kill_rate >= cmdr.kill_rate

    def test_every_format_has_a_starting_life(self):
        from densa_deck.formats.profiles import starting_life_for
        from densa_deck.models import Format
        for fmt in Format:
            assert starting_life_for(fmt) > 0, fmt


class TestBannedListEnforcement:
    def test_banned_card_is_flagged_for_the_format(self):
        from densa_deck.deck.validator import validate_deck
        from densa_deck.models import Format, Legality
        card = _card("Black Lotus", is_artifact=True)
        card.legalities = {"commander": Legality.BANNED}
        deck = Deck(name="d", entries=[_entry(card)], format=Format.COMMANDER)
        messages = [i.message for i in validate_deck(deck)]
        assert any("banned" in m.lower() for m in messages)

    def test_restricted_card_over_one_copy_is_flagged(self):
        from densa_deck.deck.validator import validate_deck
        from densa_deck.models import Format, Legality
        card = _card("Ancestral Recall", is_instant=True)
        card.legalities = {"vintage": Legality.RESTRICTED}
        deck = Deck(name="d", entries=[_entry(card, qty=2)], format=Format.VINTAGE)
        messages = [i.message for i in validate_deck(deck)]
        assert any("restricted" in m.lower() for m in messages)

    def test_legal_card_is_not_flagged(self):
        from densa_deck.deck.validator import validate_deck
        from densa_deck.models import Format, Legality
        card = _card("Sol Ring", is_artifact=True)
        card.legalities = {"commander": Legality.LEGAL}
        deck = Deck(name="d", entries=[_entry(card)], format=Format.COMMANDER)
        messages = [i.message for i in validate_deck(deck)]
        assert not any("banned" in m.lower() for m in messages)
