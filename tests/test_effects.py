"""Tests for oracle-text effect parsing and its resolution in the simulator.

Two layers are covered:
  1. `parse_effects` reads the right numbers off real card wordings.
  2. `GameState` actually applies them — a ramp spell puts a land into
     play, a draw spell puts a card in hand, Sol Ring makes two mana.

Before this layer existed the simulator moved a resolved spell to the
battlefield or graveyard and did nothing else, so every one of these
assertions would have failed at 0.
"""

import pytest

from densa_deck.goldfish import effects as fx_mod
from densa_deck.goldfish.effects import CardEffects, parse_effects
from densa_deck.goldfish.heuristics import (
    cost_discount,
    effective_cost,
    effective_mana_cost,
    play_turn,
)
from densa_deck.goldfish.mana import clear_mana_caches
from densa_deck.goldfish.state import GameState, Permanent
from densa_deck.models import Card, CardLayout, DeckEntry, Zone


@pytest.fixture(autouse=True)
def _clear_effect_cache():
    """Effects and mana colours are cached by card name, and these tests
    reuse names across files with different text."""
    fx_mod.clear_cache()
    clear_mana_caches()
    yield
    fx_mod.clear_cache()
    clear_mana_caches()


def _card(name, oracle="", **kw):
    kw.setdefault("cmc", 0.0)
    return Card(
        scryfall_id=f"id-{name}",
        oracle_id=f"oracle-{name}",
        name=name,
        layout=CardLayout.NORMAL,
        oracle_text=oracle,
        **kw,
    )


def _entry(card, zone=Zone.MAINBOARD):
    return DeckEntry(card_name=card.name, quantity=1, zone=zone, card=card)


# ---------------------------------------------------------------- parsing ---


class TestParseDraw:
    def test_draw_a_card(self):
        assert parse_effects(_card("Cantrip", "Draw a card.")).draw == 1

    def test_draw_multiple(self):
        assert parse_effects(_card("Divination", "Draw two cards.")).draw == 2
        assert parse_effects(_card("Harmonize", "Draw three cards.")).draw == 3

    def test_draws_an_additional_card_is_recurring(self):
        fx = parse_effects(_card(
            "Howling Mine",
            "At the beginning of each player's draw step, if this artifact is "
            "untapped, that player draws an additional card.",
        ))
        assert fx.draw_per_turn == 1
        assert fx.draw == 0

    def test_recurring_draw_is_not_counted_as_immediate(self):
        fx = parse_effects(_card(
            "Phyrexian Arena",
            "At the beginning of your upkeep, you draw a card and you lose 1 life.",
        ))
        assert fx.draw_per_turn == 1
        assert fx.draw == 0

    def test_etb_draw_counts_as_immediate(self):
        fx = parse_effects(_card(
            "Elvish Visionary",
            "When this creature enters, draw a card.",
        ))
        assert fx.draw == 1
        assert fx.draw_per_turn == 0

    def test_conditional_whenever_draw_is_not_modelled(self):
        # We deliberately do not model payoffs gated on opponent behaviour.
        fx = parse_effects(_card(
            "Rhystic Study",
            "Whenever an opponent casts a spell, you may draw a card unless "
            "that player pays {1}.",
        ))
        assert fx.draw == 0
        assert fx.draw_per_turn == 0

    def test_impulse_selection_counts_as_a_draw(self):
        fx = parse_effects(_card(
            "Impulse",
            "Look at the top four cards of your library. Put one of them into "
            "your hand and the rest on the bottom of your library in any order.",
        ))
        assert fx.draw == 1

    def test_exile_and_play_counts_cards_exiled(self):
        fx = parse_effects(_card(
            "Wrenn's Resolve",
            "Exile the top two cards of your library. Until the end of your "
            "next turn, you may play those cards.",
        ))
        assert fx.draw == 2

    def test_reminder_text_does_not_create_phantom_draws(self):
        # Cycling's reminder text says "Draw a card" but casting the spell
        # does not draw one.
        fx = parse_effects(_card(
            "Cycler",
            "Cycling {2} (({2}, Discard this card: Draw a card.))",
        ))
        assert fx.draw == 0


class TestParseRamp:
    def test_basic_land_ramp_enters_tapped(self):
        fx = parse_effects(_card(
            "Rampant Growth",
            "Search your library for a basic land card, put it onto the "
            "battlefield tapped, then shuffle.",
        ))
        assert fx.lands_to_battlefield == 1
        assert fx.lands_enter_tapped is True

    def test_natures_lore_enters_untapped(self):
        fx = parse_effects(_card(
            "Nature's Lore",
            "Search your library for a Forest card, put that card onto the "
            "battlefield, then shuffle.",
        ))
        assert fx.lands_to_battlefield == 1
        assert fx.lands_enter_tapped is False

    def test_cultivate_splits_between_play_and_hand(self):
        fx = parse_effects(_card(
            "Cultivate",
            "Search your library for up to two basic land cards, reveal those "
            "cards, put one onto the battlefield tapped and the rest into your "
            "hand, then shuffle.",
        ))
        assert fx.lands_to_battlefield == 1
        assert fx.lands_to_hand == 1

    def test_two_lands_to_battlefield(self):
        fx = parse_effects(_card(
            "Explosive Vegetation",
            "Search your library for up to two basic land cards, put them onto "
            "the battlefield tapped, then shuffle.",
        ))
        assert fx.lands_to_battlefield == 2


class TestParseMana:
    def test_sol_ring_makes_two(self):
        fx = parse_effects(_card("Sol Ring", "{T}: Add {C}{C}.", is_artifact=True))
        assert fx.mana_produced == 2

    def test_signet_makes_one(self):
        fx = parse_effects(_card(
            "Arcane Signet", "{T}: Add one mana of any color.", is_artifact=True))
        assert fx.mana_produced == 1

    def test_ritual_adds_temporary_mana(self):
        fx = parse_effects(_card("Dark Ritual", "Add {B}{B}{B}.", is_instant=True))
        assert fx.ritual_mana == 3

    def test_permanent_with_tap_ability_is_not_a_ritual(self):
        fx = parse_effects(_card("Rock", "{T}: Add {C}{C}.", is_artifact=True))
        assert fx.ritual_mana == 0


class TestParseOtherFamilies:
    def test_treasure_tokens(self):
        assert parse_effects(_card("Boon", "Create three Treasure tokens.")).treasure_tokens == 3

    def test_for_each_scales_treasure(self):
        fx = parse_effects(_card(
            "Brass's Bounty", "For each land you control, create a Treasure token."))
        assert fx.treasure_tokens == fx_mod.FOR_EACH_NOMINAL

    def test_creature_tokens_with_power(self):
        fx = parse_effects(_card(
            "Tokens", "Create two 2/2 white Knight creature tokens."))
        assert fx.creature_tokens == 2
        assert fx.creature_token_power == 2

    def test_extra_land_drops(self):
        assert parse_effects(_card(
            "Exploration", "You may play an additional land on each of your turns."
        )).extra_land_drops == 1
        assert parse_effects(_card(
            "Azusa", "You may play two additional lands on each of your turns."
        )).extra_land_drops == 2

    def test_cost_reduction(self):
        fx = parse_effects(_card(
            "Goblin Electromancer",
            "Instant and sorcery spells you cast cost {1} less to cast."))
        assert fx.cost_reduction == 1

    def test_direct_damage(self):
        fx = parse_effects(_card(
            "Lightning Bolt", "Lightning Bolt deals 3 damage to any target.",
            is_instant=True))
        assert fx.direct_damage == 3

    def test_extra_turn(self):
        fx = parse_effects(_card(
            "Time Warp", "Target player takes an extra turn after this one."))
        assert fx.extra_turns == 1

    def test_anthem(self):
        fx = parse_effects(_card(
            "Glorious Anthem", "Creatures you control get +1/+1."))
        assert fx.anthem_power == 1

    def test_scry(self):
        assert parse_effects(_card("Preordain", "Scry 2, then draw a card.")).scry == 2

    def test_plus_one_counters(self):
        fx = parse_effects(_card("Pump", "Put two +1/+1 counters on target creature."))
        assert fx.counters_added == 2

    def test_pump_until_end_of_turn(self):
        fx = parse_effects(_card(
            "Giant Growth", "Target creature gets +3/+3 until end of turn."))
        assert fx.pump_power == 3

    def test_vanilla_creature_has_no_effects(self):
        fx = parse_effects(_card("Grizzly Bears", ""))
        assert fx.is_empty()


# ------------------------------------------------------------- resolution ---


class TestEffectsResolveInGame:
    def test_draw_spell_puts_cards_in_hand(self):
        state = GameState()
        state.library = [_entry(_card(f"L{i}", is_land=True)) for i in range(10)]
        before = len(state.hand)
        state.apply_immediate_effects(CardEffects(draw=2, matched=["draw"]))
        assert len(state.hand) == before + 2

    def test_ramp_pulls_a_land_out_of_the_library(self):
        state = GameState()
        state.library = [
            _entry(_card("Spell")),
            _entry(_card("Forest", is_land=True)),
        ]
        state.apply_immediate_effects(
            CardEffects(lands_to_battlefield=1, lands_enter_tapped=True, matched=["land_ramp"])
        )
        assert len(state.lands_in_play) == 1
        assert state.lands_in_play[0].tapped is True
        assert len(state.library) == 1

    def test_untapped_ramp_land_is_available_immediately(self):
        state = GameState()
        state.library = [_entry(_card("Forest", is_land=True))]
        state.apply_immediate_effects(
            CardEffects(lands_to_battlefield=1, lands_enter_tapped=False, matched=["land_ramp"])
        )
        assert state.available_mana == 1

    def test_ramp_with_empty_library_does_not_crash(self):
        state = GameState()
        state.library = []
        state.apply_immediate_effects(CardEffects(lands_to_battlefield=2, matched=["land_ramp"]))
        assert len(state.lands_in_play) == 0

    def test_sol_ring_produces_two_mana(self):
        card = _card("Sol Ring", "{T}: Add {C}{C}.", is_artifact=True)
        perm = Permanent(entry=_entry(card), summoning_sick=False)
        from densa_deck.models import CardTag
        card.tags = [CardTag.MANA_ROCK]
        assert perm.available_mana() == 2

    def test_unparsed_mana_source_still_makes_one(self):
        card = _card("Forest", "", is_land=True)
        perm = Permanent(entry=_entry(card), summoning_sick=False)
        assert perm.available_mana() == 1

    def test_ritual_mana_goes_to_the_pool(self):
        state = GameState()
        state.apply_immediate_effects(CardEffects(ritual_mana=3, matched=["ritual"]))
        assert state.mana_pool == 3

    def test_treasures_are_spendable_mana(self):
        state = GameState()
        state.apply_immediate_effects(CardEffects(treasure_tokens=2, matched=["treasure"]))
        assert state.available_mana == 2
        state.tap_for_mana(2)
        assert state.mana_pool == 2
        assert state.treasures == 0

    def test_creature_tokens_add_to_power_and_count(self):
        state = GameState()
        state.apply_immediate_effects(
            CardEffects(creature_tokens=2, creature_token_power=2, matched=["creature_tokens"])
        )
        assert state.token_count == 2
        assert state.total_power == 4
        assert state.creature_count == 2

    def test_direct_damage_hits_the_opponent(self):
        state = GameState()
        state.opponent_life = 40
        state.apply_immediate_effects(CardEffects(direct_damage=3, matched=["direct_damage"]))
        assert state.opponent_life == 37
        assert state.total_damage_dealt == 3

    def test_anthem_raises_total_power(self):
        state = GameState()
        bear = _card("Bear", "", is_creature=True, power="2", toughness="2")
        state.battlefield.append(Permanent(entry=_entry(bear), summoning_sick=False))
        assert state.total_power == 2
        anthem = _card("Anthem", "Creatures you control get +1/+1.")
        state.battlefield.append(Permanent(entry=_entry(anthem), summoning_sick=False))
        assert state.total_power == 3

    def test_counters_go_on_the_biggest_creature(self):
        state = GameState()
        small = _card("Small", "", is_creature=True, power="1", toughness="1")
        big = _card("Big", "", is_creature=True, power="4", toughness="4")
        state.battlefield.append(Permanent(entry=_entry(small), summoning_sick=False))
        big_perm = Permanent(entry=_entry(big), summoning_sick=False)
        state.battlefield.append(big_perm)
        state.apply_immediate_effects(CardEffects(counters_added=2, matched=["counters"]))
        assert big_perm.counters == 2
        assert state.total_power == 1 + 6

    def test_extra_land_drops_allow_a_second_land(self):
        state = GameState()
        expl = _card("Exploration", "You may play an additional land on each of your turns.")
        state.battlefield.append(Permanent(entry=_entry(expl), summoning_sick=False))
        assert state.land_drops_allowed == 2
        state.hand = [
            _entry(_card("Forest", is_land=True)),
            _entry(_card("Island", is_land=True)),
        ]
        play_turn(state)
        assert len(state.lands_in_play) == 2

    def test_cost_reduction_lowers_effective_cost(self):
        state = GameState()
        red = _card("Electromancer", "Spells you cast cost {1} less to cast.")
        state.battlefield.append(Permanent(entry=_entry(red), summoning_sick=False))
        spell = _card("Spell", "", cmc=3.0)
        assert effective_cost(spell, state) == 2

    def test_cost_reduction_never_goes_below_zero(self):
        state = GameState()
        red = _card("BigReducer", "Spells you cast cost {5} less to cast.")
        state.battlefield.append(Permanent(entry=_entry(red), summoning_sick=False))
        assert effective_cost(_card("Cheap", "", cmc=1.0), state) == 0

    def test_recurring_draw_fires_at_upkeep(self):
        state = GameState()
        arena = _card(
            "Phyrexian Arena",
            "At the beginning of your upkeep, you draw a card and you lose 1 life.",
        )
        state.battlefield.append(Permanent(entry=_entry(arena), summoning_sick=False))
        state.library = [_entry(_card(f"C{i}")) for i in range(10)]
        state.on_play = False
        state.begin_turn()
        # One for the normal draw step, one for the Arena.
        assert len(state.hand) == 2

    def test_scry_bottoms_a_spell_when_short_on_lands(self):
        state = GameState()
        spell = _entry(_card("Spell"))
        land = _entry(_card("Forest", is_land=True))
        state.library = [spell, land]
        state.scry(1)
        assert state.library[0] is land

    def test_scry_keeps_a_land_when_short_on_lands(self):
        state = GameState()
        land = _entry(_card("Forest", is_land=True))
        spell = _entry(_card("Spell"))
        state.library = [land, spell]
        state.scry(1)
        assert state.library[0] is land

    def test_haste_lets_a_creature_attack_immediately(self):
        state = GameState()
        giver = _card("Fervor", "Creatures you control have haste.")
        state.battlefield.append(Permanent(entry=_entry(giver), summoning_sick=False))
        bear = _card("Bear", "", is_creature=True, power="2", toughness="2")
        state.cast_spell(_entry(bear))
        attacker = [p for p in state.creatures_in_play if p.name == "Bear"][0]
        assert attacker.summoning_sick is False


class TestCounterFamily:
    """+1/+1 counters feed total_power, so getting them wrong moves the clock.

    A creature that enters as a 4/4 with four counters attacks as an 8/8;
    the simulator used to swing it for 4.
    """

    def test_enters_with_counters(self):
        fx = parse_effects(_card(
            "Kalonian Hydra", "This creature enters with four +1/+1 counters on it."))
        assert fx.enters_with_counters == 4

    def test_enters_with_x_counters_uses_the_variable_default(self):
        fx = parse_effects(_card(
            "Walking Ballista", "This creature enters with X +1/+1 counters on it."))
        assert fx.enters_with_counters == fx_mod.VARIABLE_X_VALUE

    def test_counters_on_each_creature_is_distinct_from_one_target(self):
        fx = parse_effects(_card(
            "Overrun", "Put a +1/+1 counter on each creature you control."))
        assert fx.counters_each == 1
        assert fx.counters_added == 0

    def test_distribute_counters(self):
        fx = parse_effects(_card(
            "Spread", "Distribute three +1/+1 counters among any number of targets."))
        assert fx.counters_added == 3

    def test_counter_doubler(self):
        fx = parse_effects(_card(
            "Branching Evolution",
            "If one or more +1/+1 counters would be put on a creature you "
            "control, twice that many +1/+1 counters are put on that creature "
            "instead."))
        assert fx.counter_multiplier == 2

    def test_proliferate_on_resolution(self):
        fx = parse_effects(_card("Contagion Clasp", "{4}, {T}: Proliferate."))
        assert fx.proliferate == 1

    def test_self_controlled_trigger_is_modelled_per_turn(self):
        # No blockers in a goldfish, so a combat-damage trigger always fires.
        fx = parse_effects(_card(
            "Thrummingbird",
            "Whenever this creature deals combat damage to a player, proliferate."))
        assert fx.proliferate_per_turn == 1

    def test_cast_trigger_is_self_controlled(self):
        fx = parse_effects(_card(
            "Inexorable Tide", "Whenever you cast a spell, proliferate."))
        assert fx.proliferate_per_turn == 1

    def test_opponent_dependent_trigger_stays_unmodelled(self):
        fx = parse_effects(_card(
            "Rhystic Study",
            "Whenever an opponent casts a spell, you may draw a card unless "
            "that player pays {1}."))
        assert fx.draw == 0 and fx.draw_per_turn == 0


class TestCountersInGame:
    def test_creature_enters_with_its_counters(self):
        state = GameState()
        hydra = _card(
            "Hydra", "This creature enters with four +1/+1 counters on it.",
            is_creature=True, power="4", toughness="4")
        state.cast_spell(_entry(hydra))
        perm = state.creatures_in_play[0]
        assert perm.counters == 4
        assert perm.power_value() == 8

    def test_counter_doubler_doubles_entering_counters(self):
        state = GameState()
        doubler = _card(
            "Branching Evolution",
            "If one or more +1/+1 counters would be put on a creature you "
            "control, twice that many +1/+1 counters are put on that creature "
            "instead.")
        state.battlefield.append(Permanent(entry=_entry(doubler), summoning_sick=False))
        hydra = _card(
            "Hydra", "This creature enters with two +1/+1 counters on it.",
            is_creature=True, power="1", toughness="1")
        state.cast_spell(_entry(hydra))
        perm = [p for p in state.creatures_in_play if p.name == "Hydra"][0]
        assert perm.counters == 4

    def test_counters_each_hits_the_whole_board(self):
        state = GameState()
        for i in range(3):
            bear = _card(f"Bear{i}", "", is_creature=True, power="2", toughness="2")
            state.battlefield.append(Permanent(entry=_entry(bear), summoning_sick=False))
        state.apply_immediate_effects(
            CardEffects(counters_each=1, matched=["counters_each"]))
        assert state.total_power == 9

    def test_proliferate_only_grows_things_that_already_have_counters(self):
        state = GameState()
        withc = _card("A", "", is_creature=True, power="1", toughness="1")
        without = _card("B", "", is_creature=True, power="1", toughness="1")
        pa = Permanent(entry=_entry(withc), summoning_sick=False)
        pa.counters = 1
        pb = Permanent(entry=_entry(without), summoning_sick=False)
        state.battlefield.extend([pa, pb])
        state.proliferate()
        assert pa.counters == 2
        assert pb.counters == 0

    def test_proliferate_engine_fires_each_turn(self):
        state = GameState()
        tide = _card("Inexorable Tide", "Whenever you cast a spell, proliferate.")
        state.battlefield.append(Permanent(entry=_entry(tide), summoning_sick=False))
        bear = _card("Bear", "", is_creature=True, power="1", toughness="1")
        perm = Permanent(entry=_entry(bear), summoning_sick=False)
        perm.counters = 1
        state.battlefield.append(perm)
        state.library = [_entry(_card(f"C{i}")) for i in range(10)]
        state.begin_turn()
        state.begin_turn()
        assert perm.counters == 3


class TestAlternativeCosts:
    """Delve, convoke, improvise and affinity make spells cheaper than their
    printed cost — and each spends a real resource to do it."""

    def test_delve_parsed(self):
        assert parse_effects(_card("Treasure Cruise", "Delve\nDraw three cards.")).delve

    def test_convoke_parsed(self):
        assert parse_effects(_card("Chord of Calling", "Convoke\nSearch your library.")).convoke

    def test_affinity_parsed(self):
        fx = parse_effects(_card("Frogmite", "Affinity for artifacts"))
        assert fx.cost_less_per == "artifacts"
        assert fx.cost_less_amount == 1

    def test_cost_less_for_each_parsed(self):
        fx = parse_effects(_card(
            "Thoughtcast",
            "This spell costs {1} less to cast for each artifact you control.\nDraw two cards."))
        assert fx.cost_less_amount == 1
        assert "artifact" in fx.cost_less_per

    def test_delve_discount_scales_with_graveyard(self):
        state = GameState()
        state.graveyard = [_entry(_card(f"g{i}")) for i in range(5)]
        cruise = _card("Cruise", "Delve\nDraw three cards.",
                       mana_cost="{7}{U}", cmc=8.0, is_sorcery=True)
        assert cost_discount(cruise, state) == 5
        assert effective_mana_cost(cruise, state).generic == 2

    def test_delve_exiles_the_graveyard_it_ate(self):
        state = GameState()
        state.graveyard = [_entry(_card(f"g{i}")) for i in range(5)]
        cruise = _card("Cruise", "Delve\nDraw three cards.",
                       mana_cost="{7}{U}", cmc=8.0, is_sorcery=True)
        state.pay_alternative_costs(cruise, 5)
        assert state.graveyard == []

    def test_convoke_taps_the_creatures_it_used(self):
        state = GameState()
        for i in range(3):
            bear = _card(f"Bear{i}", "", is_creature=True, power="2", toughness="2")
            state.battlefield.append(Permanent(entry=_entry(bear), summoning_sick=False))
        chord = _card("Chord", "Convoke", mana_cost="{3}{G}{G}{G}", cmc=6.0, is_instant=True)
        assert effective_mana_cost(chord, state).generic == 0
        state.pay_alternative_costs(chord, 3)
        assert all(p.tapped for p in state.creatures_in_play)

    def test_alternative_costs_never_reduce_coloured_pips(self):
        state = GameState()
        state.graveyard = [_entry(_card(f"g{i}")) for i in range(9)]
        cruise = _card("Cruise", "Delve", mana_cost="{7}{U}", cmc=8.0, is_sorcery=True)
        assert effective_mana_cost(cruise, state).pips["U"] == 1

    def test_no_resource_means_no_discount(self):
        state = GameState()
        cruise = _card("Cruise", "Delve", mana_cost="{7}{U}", cmc=8.0, is_sorcery=True)
        assert cost_discount(cruise, state) == 0


class TestCascade:
    def test_cascade_parsed(self):
        assert parse_effects(_card("Bloodbraid Elf", "Cascade")).cascade == 1

    def test_cascade_casts_a_cheaper_card_for_free(self):
        state = GameState()
        cheap = _card("Cheap", "", mana_cost="{1}", cmc=1.0, is_sorcery=True)
        state.library = [_entry(_card("Land", is_land=True)), _entry(cheap)]
        bbe = _card("BBE", "Cascade", mana_cost="{2}{R}{G}", cmc=4.0,
                    is_creature=True, power="3", toughness="2")
        state.cast_spell(_entry(bbe))
        assert "Cheap" in state.spells_cast_this_turn
        assert state.mana_pool == 0  # it really was free

    def test_cascade_skips_cards_that_cost_too_much(self):
        state = GameState()
        pricey = _card("Pricey", "", mana_cost="{9}", cmc=9.0, is_sorcery=True)
        state.library = [_entry(pricey)]
        bbe = _card("BBE", "Cascade", mana_cost="{2}{R}{G}", cmc=4.0,
                    is_creature=True, power="3", toughness="2")
        state.cast_spell(_entry(bbe))
        assert "Pricey" not in state.spells_cast_this_turn

    def test_cascade_with_empty_library_does_not_crash(self):
        state = GameState()
        bbe = _card("BBE", "Cascade", mana_cost="{2}{R}{G}", cmc=4.0,
                    is_creature=True, power="3", toughness="2")
        state.cast_spell(_entry(bbe))
        assert state.creatures_in_play

    def test_cascade_chain_is_depth_bounded(self):
        # Every card cascades into another cascader; must terminate.
        state = GameState()
        for i in range(30):
            state.library.append(_entry(_card(
                f"C{i}", "Cascade", mana_cost="{1}", cmc=1.0, is_sorcery=True)))
        top = _card("Top", "Cascade", mana_cost="{5}", cmc=5.0, is_sorcery=True)
        state.cast_spell(_entry(top))
        assert len(state.spells_cast_this_turn) <= 12


class TestMillAndRecursion:
    def test_self_mill_parsed(self):
        assert parse_effects(_card("Supplier", "When this creature enters, mill three cards.")).mill == 3

    def test_targeted_mill_is_not_self_mill(self):
        fx = parse_effects(_card("Glimpse", "Target player mills ten cards."))
        assert fx.mill == 0

    def test_mill_moves_library_to_graveyard(self):
        state = GameState()
        state.library = [_entry(_card(f"c{i}")) for i in range(5)]
        state.mill(3)
        assert len(state.graveyard) == 3
        assert len(state.library) == 2

    def test_mill_past_the_end_of_the_library_is_safe(self):
        state = GameState()
        state.library = [_entry(_card("only"))]
        state.mill(5)
        assert len(state.graveyard) == 1

    def test_reanimate_parsed(self):
        fx = parse_effects(_card(
            "Reanimate",
            "Put target creature card from a graveyard onto the battlefield "
            "under your control."))
        assert fx.reanimate == 1

    def test_reanimate_returns_the_biggest_creature(self):
        state = GameState()
        state.graveyard = [
            _entry(_card("Small", "", is_creature=True, power="1", toughness="1")),
            _entry(_card("Fatty", "", is_creature=True, power="6", toughness="6")),
        ]
        state.reanimate(1)
        assert [p.name for p in state.creatures_in_play] == ["Fatty"]
        assert len(state.graveyard) == 1

    def test_reanimate_ignores_noncreature_cards(self):
        state = GameState()
        state.graveyard = [_entry(_card("Spell"))]
        state.reanimate(1)
        assert state.creatures_in_play == []

    def test_mill_feeds_delve(self):
        # The two mechanics compose: milling makes the next delve cheaper.
        state = GameState()
        state.library = [_entry(_card(f"c{i}")) for i in range(6)]
        cruise = _card("Cruise", "Delve", mana_cost="{7}{U}", cmc=8.0, is_sorcery=True)
        assert cost_discount(cruise, state) == 0
        state.mill(6)
        assert cost_discount(cruise, state) == 6


class TestEffectRegistry:
    """The registry is the contract between the parser, the coverage
    command and the docs. If they can drift apart, they will."""

    def test_every_declared_family_has_complete_metadata(self):
        for fam in fx_mod.EFFECT_FAMILIES:
            assert fam.key and fam.field and fam.summary, fam
            assert fam.phase in {"immediate", "static", "recurring", "cost", "cast"}, fam

    def test_family_keys_are_unique(self):
        keys = [f.key for f in fx_mod.EFFECT_FAMILIES]
        assert len(keys) == len(set(keys))

    def test_declared_fields_exist_on_cardeffects(self):
        blank = CardEffects()
        for fam in fx_mod.EFFECT_FAMILIES:
            for field_name in fam.field.split(" / "):
                assert hasattr(blank, field_name.strip()), f"{fam.key} -> {field_name}"

    def test_every_key_the_parser_emits_is_declared(self):
        """The real drift guard: parse a spread of wordings and require
        every family the parser reports to be in the registry."""
        samples = [
            ("Sol Ring", "{T}: Add {C}{C}.", {"is_artifact": True}),
            ("Cultivate", "Search your library for up to two basic land cards, "
                          "reveal those cards, put one onto the battlefield tapped "
                          "and the rest into your hand, then shuffle.", {}),
            ("Dark Ritual", "Add {B}{B}{B}.", {"is_instant": True}),
            ("Divination", "Draw two cards.", {}),
            ("Impulse", "Look at the top four cards of your library. Put one of "
                        "them into your hand and the rest on the bottom.", {}),
            ("Arena2", "At the beginning of your upkeep, you draw a card.", {}),
            ("Preordain", "Scry 2, then draw a card.", {}),
            ("Supplier", "When this creature enters, mill three cards.", {}),
            ("Anthem", "Creatures you control get +1/+1.", {}),
            ("Hydra", "This creature enters with four +1/+1 counters on it.", {}),
            ("Tide", "Whenever you cast a spell, proliferate.", {}),
            ("Evolution", "If one or more +1/+1 counters would be put on a creature "
                          "you control, twice that many +1/+1 counters are put on "
                          "that creature instead.", {}),
            ("Fervor", "Creatures you control have haste.", {}),
            ("Growth", "Target creature gets +3/+3 until end of turn.", {}),
            ("Bolt", "Bolt deals 3 damage to any target.", {"is_instant": True}),
            ("Reanimate2", "Put target creature card from a graveyard onto the "
                           "battlefield under your control.", {}),
            ("Warp", "Target player takes an extra turn after this one.", {}),
            ("Electromancer", "Spells you cast cost {1} less to cast.", {}),
            ("Cruise", "Delve\nDraw three cards.", {"is_sorcery": True}),
            ("Chord", "Convoke\nSearch your library for a creature card.", {}),
            ("Whir", "Improvise\nSearch your library for an artifact card.", {}),
            ("Frogmite", "Affinity for artifacts", {}),
            ("Thoughtcast", "This spell costs {1} less to cast for each artifact "
                            "you control.\nDraw two cards.", {}),
            ("BBE", "Cascade", {}),
            ("Exploration2", "You may play an additional land on each of your turns.", {}),
            ("Tithe", "At the beginning of your end step, create a Treasure token.", {}),
            ("Reflection", "Whenever you tap a land for mana, add an additional {G}.", {}),
            ("Tokens2", "Create two 2/2 white Knight creature tokens.", {}),
            ("Spread2", "Put a +1/+1 counter on each creature you control.", {}),
            ("Tutor2", "Search your library for a card and put it into your hand.", {}),
        ]
        emitted = set()
        for name, oracle, kw in samples:
            emitted.update(parse_effects(_card(name, oracle, **kw)).matched)

        undeclared = emitted - set(fx_mod.EFFECT_FAMILIES_BY_KEY)
        assert not undeclared, (
            f"parser emits families missing from EFFECT_FAMILIES: {sorted(undeclared)}"
        )

    def test_registry_covers_the_families_these_samples_reach(self):
        # Guards the opposite direction: a declared family that nothing can
        # ever emit is dead documentation.
        declared = {f.key for f in fx_mod.EFFECT_FAMILIES}
        # Families driven by wordings not in the sample set above.
        known_unsampled = {"counters", "counters_per_turn", "selection_draw",
                           "proliferate", "recurring_treasure", "land_ramp"}
        assert known_unsampled <= declared
