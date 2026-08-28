"""One card, and everything around it.

Clicking a card raises the same three questions every time, and the engine
could answer none of them about a SINGLE card:

* **What is it doing here?** Its roles, and whether the deck is short of them.
  `run_static_analysis` knows the deck's role counts and `classify_card` knows
  the card's tags; nothing joined the two to say "this is one of your four
  pieces of removal, and you wanted eight".
* **What does it work with?** `detect_synergies` enumerates pairs across a
  whole deck, which is the deck-level report. Asked about one card it is the
  wrong shape — you get every pair in the deck and have to find yours.
* **What would work with it?** `find_add_candidates` fills ROLE GAPS. That is
  a different question from "what pairs with this specific card", and asking
  the first when you meant the second gets you a good removal spell when you
  wanted the sacrifice outlet that turns your tokens into a wincon.

Combos are folded in rather than bolted on: a card that completes a line you
already almost have is the single most useful thing to say about it, and it
would otherwise sit in a different panel entirely.

Nothing here calls a model. It is arithmetic over tags, the deck's own role
counts and the Spellbook cache, so it answers the same way twice and works
with nothing loaded — the same reason `suggest_deckbuild_additions` is
LLM-free.
"""

from __future__ import annotations

from densa_deck.analysis.advanced import _SYNERGY_RULES
from densa_deck.models import CardTag, Zone

# Which role each tag counts toward, so "you wanted eight" can be said at all.
# Mirrors the buckets `run_static_analysis` reports and `DeckTargets` names —
# a card is described in the same words the deck is.
_TAG_ROLE: dict[str, str] = {
    CardTag.RAMP.value: "ramp",
    CardTag.MANA_ROCK.value: "ramp",
    CardTag.MANA_DORK.value: "ramp",
    CardTag.CARD_DRAW.value: "draw",
    CardTag.CANTRIP.value: "draw",
    CardTag.TARGETED_REMOVAL.value: "removal",
    CardTag.BOARD_WIPE.value: "removal",
    CardTag.COUNTERSPELL.value: "removal",
    CardTag.ARTIFACT_ENCHANTMENT_REMOVAL.value: "removal",
    CardTag.THREAT.value: "threats",
    CardTag.FINISHER.value: "threats",
    CardTag.TOKEN_MAKER.value: "threats",
    CardTag.LAND.value: "lands",
    CardTag.BASIC_LAND.value: "lands",
}

# What `run_static_analysis` calls each of those counts.
_ROLE_COUNT_FIELD = {
    "ramp": "ramp_count",
    "draw": "draw_engine_count",
    "removal": "interaction_count",
    "threats": "threat_count",
    "lands": "land_count",
}


def _tags_of(card) -> set[str]:
    """This card's roles, classifying it if nobody has yet.

    Tags are NOT stored in the card database and the resolver does not fill
    them in — `analyze_deck` classifies its entries in place as a side effect,
    which covers the cards in a deck and covers nothing else. The subject card
    here comes from a separate `lookup_by_name` and arrives with an empty tag
    list, so reading the attribute and trusting it produced an empty panel for
    every card on real data while every fixture with hand-set tags passed.

    Classified on demand instead, so the answer does not depend on whether
    something else happened to run first.
    """
    stored = getattr(card, "tags", None) or []
    if stored:
        return {t.value if hasattr(t, "value") else str(t) for t in stored}
    if card is None:
        return set()
    try:
        from densa_deck.classification.tagger import classify_card
        return {t.value if hasattr(t, "value") else str(t)
                for t in classify_card(card)}
    except Exception:
        return set()


def _active_entries(deck):
    """Maindeck and commanders — the cards actually in the deck.

    A sideboard card is not something this card synergises with today, and
    listing it as one sends people looking for a card that is not there.
    """
    return [e for e in deck.entries
            if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD) and e.card]


def partner_tags_for(tags: set[str]) -> dict[str, list[tuple[str, float]]]:
    """Tags that pair with these, and why.

    The synergy rules are directionless in practice — a sacrifice outlet
    wants aristocrat payoffs and an aristocrat payoff wants sacrifice outlets
    — so both directions are read. Reading one would answer the question for
    half the cards in the deck and silently return nothing for the other half.
    """
    wanted: dict[str, list[tuple[str, float]]] = {}
    for tag_a, tag_b, reason, strength in _SYNERGY_RULES:
        if tag_a in tags:
            wanted.setdefault(tag_b, []).append((reason, strength))
        if tag_b in tags:
            wanted.setdefault(tag_a, []).append((reason, strength))
    return wanted


def synergies_in_deck(card, deck, *, limit: int = 12) -> list[dict]:
    """What is already in the deck that works with this card."""
    tags = _tags_of(card)
    if not tags:
        return []
    wanted = partner_tags_for(tags)
    if not wanted:
        return []

    found: dict[str, dict] = {}
    for entry in _active_entries(deck):
        other = entry.card
        if other is None or other.name == card.name:
            continue
        overlap = _tags_of(other) & set(wanted)
        if not overlap:
            continue
        # The strongest reason this pair exists, not every reason — a list of
        # five restatements of one relationship reads as noise.
        best_reason, best_strength = "", 0.0
        for tag in overlap:
            for reason, strength in wanted[tag]:
                if strength > best_strength:
                    best_reason, best_strength = reason, strength
        existing = found.get(other.name)
        if existing is None or best_strength > existing["strength"]:
            found[other.name] = {
                "card_name": other.name,
                "reason": best_reason,
                "strength": round(best_strength, 2),
                "printing_id": getattr(other, "scryfall_id", "") or "",
            }
    return sorted(found.values(),
                  key=lambda s: (-s["strength"], s["card_name"]))[:limit]


def role_fit(card, deck, analysis=None) -> dict:
    """What this card is doing in the deck, in the deck's own terms.

    The useful sentence is not "this is a removal spell" — you can read the
    card. It is "this is one of your four removal spells and you wanted
    eight", which needs the deck's counts and the format's targets, and is
    the reason this lives beside the analysis rather than beside the card.
    """
    tags = _tags_of(card)
    roles = sorted({_TAG_ROLE[t] for t in tags if t in _TAG_ROLE})
    out = {
        "tags": sorted(tags),
        "roles": roles,
        "is_land": bool(getattr(card, "is_land", False)),
        "cmc": float(getattr(card, "cmc", 0) or 0),
        "counts": [],
    }
    if analysis is None:
        return out

    try:
        from densa_deck.formats.profiles import get_format_profile
        targets = getattr(get_format_profile(deck.format), "targets", None)
    except Exception:
        targets = None

    for role in roles:
        field = _ROLE_COUNT_FIELD.get(role)
        have = int(getattr(analysis, field, 0) or 0) if field else 0
        want = None
        if targets is not None:
            band = getattr(targets, role, None)
            if isinstance(band, tuple) and band:
                want = int(band[0])
        out["counts"].append({
            "role": role,
            "have": have,
            "want": want,
            # Only ever a shortfall. "You have twelve ramp and wanted ten" is
            # not a problem, and phrasing it as one trains people to ignore
            # the panel.
            "short": max(0, (want or 0) - have) if want else 0,
        })
    return out


def combo_lines_for(card_name: str, matched: list) -> list[dict]:
    """The combos this card is actually part of, out of the deck's matches.

    `matched` holds MatchedCombo wrappers; the names and the payoff live on
    the `combo` inside, not on the wrapper.
    """
    target = (card_name or "").strip().lower()
    lines = []
    for match in matched or []:
        combo = getattr(match, "combo", match)
        names = [str(n) for n in (getattr(combo, "cards", None) or [])]
        if not any(n.strip().lower() == target for n in names):
            continue
        lines.append({
            "combo_id": getattr(combo, "combo_id", ""),
            "cards": names,
            # The others, which is what a reader wants — they know this card.
            "with": [n for n in names if n.strip().lower() != target],
            "produces": list(getattr(combo, "produces", None) or []),
            "spellbook_url": getattr(combo, "spellbook_url", ""),
        })
    return lines


def completions_for(card_name: str, near_misses: list) -> list[dict]:
    """Combos this card would COMPLETE — the near-misses it is missing from.

    The most valuable thing that can be said about a card you are looking at
    while deckbuilding, and it lives in a different panel from the card,
    which is why nobody ever sees it.
    """
    target = (card_name or "").strip().lower()
    out = []
    for match in near_misses or []:
        # `missing_cards` is on the wrapper — it is a fact about this DECK's
        # relationship to the combo, not about the combo.
        missing = [str(n) for n in (getattr(match, "missing_cards", None) or [])]
        if not any(n.strip().lower() == target for n in missing):
            continue
        combo = getattr(match, "combo", match)
        out.append({
            "combo_id": getattr(combo, "combo_id", ""),
            "cards": [str(n) for n in (getattr(combo, "cards", None) or [])],
            "still_missing": [n for n in missing
                              if n.strip().lower() != target],
            "produces": list(getattr(combo, "produces", None) or []),
            "spellbook_url": getattr(combo, "spellbook_url", ""),
        })
    return out


def suggestions_for_card(card, deck, db, *, limit: int = 8,
                         combo_completers: set[str] | None = None) -> list[dict]:
    """Cards NOT in the deck that would work with this one.

    Sourced from the partner TAGS rather than from the deck's role gaps,
    which is the distinction that makes this worth having. `find_add_candidates`
    answers "what is this deck short of" — a good question, asked elsewhere,
    and the wrong one here: it will hand back an excellent removal spell when
    what you wanted was the sacrifice outlet that turns the tokens you are
    looking at into a win condition.

    Combo completers float to the top when supplied. A card that finishes a
    line you already almost have beats a card that is merely thematic, and it
    is the one piece of advice here that changes what someone does next.
    """
    from densa_deck.analyst.add_candidates import find_add_candidates
    from densa_deck.models import CardTag as _CardTag

    wanted = partner_tags_for(_tags_of(card))
    if not wanted:
        return []

    in_deck = {e.card.name for e in _active_entries(deck) if e.card}
    in_deck.add(card.name)

    identity = set()
    for entry in _active_entries(deck):
        for colour in (getattr(entry.card, "color_identity", None) or []):
            identity.add(colour.value if hasattr(colour, "value") else str(colour))
    # A deck with no colours read from it is a deck this cannot judge; asking
    # for everything would offer cards that are not castable.
    if not identity:
        identity = {c.value if hasattr(c, "value") else str(c)
                    for c in (getattr(card, "color_identity", None) or [])}

    completers = {n.strip().lower() for n in (combo_completers or set())}
    out: list[dict] = []
    seen: set[str] = set()

    # Strongest partnership first, so a thin catalogue still spends its slots
    # on the relationship that matters most.
    ranked_tags = sorted(
        wanted.items(),
        key=lambda kv: -max(strength for _reason, strength in kv[1]))

    for tag_value, reasons in ranked_tags:
        try:
            role = _CardTag(tag_value)
        except ValueError:
            continue
        reason, strength = max(reasons, key=lambda r: r[1])
        try:
            found = find_add_candidates(
                db, role, identity, deck.format, in_deck | seen,
                limit=limit, combo_completers=combo_completers)
        except Exception:
            # A suggestion panel that cannot suggest is a smaller panel, not
            # a broken card view.
            continue
        for candidate in found:
            name = candidate.card.name
            if name in seen or name in in_deck:
                continue
            seen.add(name)
            out.append({
                "card_name": name,
                "reason": reason,
                "strength": round(strength, 2),
                "role": role.value,
                "printing_id": getattr(candidate.card, "scryfall_id", "") or "",
                "cmc": float(getattr(candidate.card, "cmc", 0) or 0),
                "completes_combo": name.strip().lower() in completers,
            })

    out.sort(key=lambda s: (not s["completes_combo"], -s["strength"],
                            s["cmc"], s["card_name"]))
    return out[:limit]
