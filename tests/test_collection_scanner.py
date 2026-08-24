"""Phase 4 — card identification from OCR text.

No camera required: the identification pipeline is pure text -> candidates,
so the interesting behaviour (and every failure mode that would corrupt an
inventory) is testable without hardware.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.collection.scanner import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXACT,
    CONFIDENCE_LIKELY,
    CONFIDENCE_UNKNOWN,
    ScanSession,
    identify_card,
    parse_card_footer,
)
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall

SKITH_SOM = "skith-som"
SKITH_MUL = "skith-mul"
BOLT_LEA = "bolt-lea"
UNIQUE = "unique-1"


def _raw(pid, name, set_code, num, *, usd="1.00", foil=None, oracle="o",
         finishes=("nonfoil",)):
    return {
        "id": pid, "oracle_id": oracle, "name": name, "set": set_code,
        "set_name": set_code.upper(), "collector_number": num, "rarity": "mythic",
        "lang": "en", "released_at": "2010-10-01", "finishes": list(finishes),
        "frame": "2015", "border_color": "black", "promo_types": [],
        "games": ["paper"], "tcgplayer_id": 1,
        "prices": {"usd": usd, "usd_foil": foil, "usd_etched": None},
    }


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        d = CardDatabase(db_path=Path(tmp) / "cards.db")
        d.upsert_printings([
            printing_row_from_scryfall(
                _raw(SKITH_SOM, "Skithiryx, the Blight Dragon", "som", "79",
                     usd="12.42", foil="55.55", finishes=("nonfoil", "foil"),
                     oracle="o-skith"), "t"),
            printing_row_from_scryfall(
                _raw(SKITH_MUL, "Skithiryx, the Blight Dragon", "mul", "147",
                     usd="12.52", oracle="o-skith"), "t"),
            printing_row_from_scryfall(
                _raw(BOLT_LEA, "Lightning Bolt", "lea", "161", usd="400.00",
                     oracle="o-bolt"), "t"),
            printing_row_from_scryfall(
                _raw(UNIQUE, "Obscure Singleton", "xyz", "5", usd=None,
                     oracle="o-uniq"), "t"),
        ])
        yield d
        d.close()


class TestFooterParsing:
    def test_modern_footer(self):
        # The real layout on a post-2015 card.
        idn = parse_card_footer("0079/0249 M\nSOM • EN   Jason Chan")
        assert idn.collector_number == "79"
        assert idn.set_code == "som"
        assert idn.language == "en"
        assert idn.has_exact_key

    def test_leading_zeros_stripped(self):
        # Cards print 0079; Scryfall stores 79.
        assert parse_card_footer("0007/0249 R\nSOM • EN").collector_number == "7"

    def test_letter_suffix_preserved(self):
        # 147a and 147b are genuinely different printings.
        idn = parse_card_footer("147a/300 M\nMUL • EN")
        assert idn.collector_number == "147a"

    def test_number_without_total(self):
        idn = parse_card_footer("123 R\nLEA • EN")
        assert idn.collector_number == "123"

    def test_set_without_language(self):
        idn = parse_card_footer("0079/0249 M\nSOM •")
        assert idn.set_code == "som"
        assert idn.language == ""

    def test_empty_text(self):
        idn = parse_card_footer("")
        assert not idn.has_exact_key
        assert idn.collector_number == ""

    def test_garbage_yields_nothing_rather_than_guessing(self):
        idn = parse_card_footer("~~~ ### ???")
        assert idn.collector_number == ""
        assert idn.set_code == ""

    def test_rules_text_does_not_produce_false_set_code(self):
        # Uppercase runs in rules text must not be mistaken for a set code;
        # the bullet separator is what anchors the match.
        idn = parse_card_footer("Whenever CREATURE deals damage, draw ONE card.")
        assert idn.set_code == ""

    def test_alternate_bullet_characters(self):
        for bullet in ("•", "·", "*"):
            idn = parse_card_footer(f"79/249 M\nSOM {bullet} EN")
            assert idn.set_code == "som", bullet


class TestExactIdentification:
    def test_set_and_number_gives_exact(self, db):
        r = identify_card("0079/0249 M\nSOM • EN", db)
        assert r.confidence == CONFIDENCE_EXACT
        assert r.best["printing_id"] == SKITH_SOM
        assert r.auto_addable

    def test_exact_beats_name_ambiguity(self, db):
        # Two printings share this name; the footer pins the right one.
        r = identify_card("Skithiryx, the Blight Dragon\n0079/0249 M\nSOM • EN", db)
        assert r.confidence == CONFIDENCE_EXACT
        assert r.best["set_code"] == "som"

    def test_name_disagreeing_with_footer_is_not_auto_added(self, db):
        """OCR that contradicts itself must never auto-file a card.

        A wrong card in inventory is worse than no card: you won't know to
        go looking for it.
        """
        r = identify_card("Lightning Bolt\n0079/0249 M\nSOM • EN", db)
        assert r.confidence == CONFIDENCE_AMBIGUOUS
        assert not r.auto_addable
        assert "Lightning Bolt" in r.candidates[0].reason

    def test_clipped_name_still_matches_footer(self, db):
        # OCR often loses the tail of a long name.
        r = identify_card("Skithiryx, the Blight Drag\n0079/0249 M\nSOM • EN", db)
        assert r.confidence == CONFIDENCE_EXACT

    def test_unknown_set_number_falls_through_to_name(self, db):
        r = identify_card("Lightning Bolt\n9999/9999 R\nZZZ • EN", db)
        assert r.confidence == CONFIDENCE_LIKELY
        assert r.best["printing_id"] == BOLT_LEA


class TestNameIdentification:
    def test_single_printing_is_likely(self, db):
        r = identify_card("Lightning Bolt\nInstant", db)
        assert r.confidence == CONFIDENCE_LIKELY
        assert r.auto_addable

    def test_multiple_printings_ask(self, db):
        r = identify_card("Skithiryx, the Blight Dragon\nLegendary Creature", db)
        assert r.confidence == CONFIDENCE_AMBIGUOUS
        assert not r.auto_addable
        assert len(r.candidates) == 2

    def test_set_code_alone_narrows_the_field(self, db):
        r = identify_card("Skithiryx, the Blight Dragon\nMUL • EN", db)
        assert r.confidence == CONFIDENCE_LIKELY
        assert r.best["set_code"] == "mul"

    def test_fuzzy_name_suggests_but_never_auto_adds(self, db):
        """A near-miss name is a guess, and guesses must be confirmed.

        Measured against the real 107k catalogue, clipped names resolve
        confidently to the wrong card — "Searing B" becomes "Searing Barb",
        a different card with a single printing, which would otherwise
        auto-add and silently corrupt the inventory.
        """
        r = identify_card("Lightnlng Bolt\nInstant", db)
        assert r.best["printing_id"] == BOLT_LEA   # right suggestion
        assert r.confidence == CONFIDENCE_AMBIGUOUS
        assert not r.auto_addable                  # but the human confirms

    def test_exact_name_still_auto_adds(self, db):
        r = identify_card("Lightning Bolt\nInstant", db)
        assert r.confidence == CONFIDENCE_LIKELY
        assert r.auto_addable

    def test_alphanumeric_collector_numbers(self, db):
        """The List, promos and Worlds decks don't use plain integers."""
        from densa_deck.collection.scanner import parse_card_footer
        for raw, expected in [
            ("WWK-90/0249 C\nPLST • EN", "wwk-90"),
            ("et208/0249 C\nPTC • EN", "et208"),
            ("js0b/0249 C\nWC99 • EN", "js0b"),
            ("A25-181/0249 M\nPLST • EN", "a25-181"),
        ]:
            assert parse_card_footer(raw).collector_number == expected, raw

    def test_name_hint_overrides_ocr(self, db):
        r = identify_card("garbled nonsense", db, name_hint="Lightning Bolt")
        assert r.confidence == CONFIDENCE_LIKELY

    def test_unreadable_card_is_unknown(self, db):
        r = identify_card("~~~", db)
        assert r.confidence == CONFIDENCE_UNKNOWN
        assert not r.auto_addable
        assert r.best is None

    def test_empty_input(self, db):
        r = identify_card("", db)
        assert r.confidence == CONFIDENCE_UNKNOWN

    def test_nonexistent_card_name(self, db):
        r = identify_card("Definitely Not A Real Card\nSorcery", db)
        assert r.confidence == CONFIDENCE_UNKNOWN


class TestScanSession:
    def _result(self, db, text):
        return identify_card(text, db)

    def test_counts_and_value(self, db):
        s = ScanSession()
        s.record(self._result(db, "0079/0249 M\nSOM • EN"), added=True)
        s.record(self._result(db, "Lightning Bolt"), added=True)
        assert s.scanned == 2
        assert s.added == 2
        assert s.value_usd == pytest.approx(412.42, abs=0.01)

    def test_foil_valued_as_foil(self, db):
        s = ScanSession()
        s.record(self._result(db, "0079/0249 M\nSOM • EN"), added=True, finish="foil")
        assert s.value_usd == 55.55

    def test_unpriced_counted_not_zeroed(self, db):
        s = ScanSession()
        s.record(self._result(db, "Obscure Singleton"), added=True)
        assert s.added == 1
        assert s.unpriced == 1
        assert s.value_usd == 0.0

    def test_ambiguous_goes_to_review_not_skipped(self, db):
        s = ScanSession()
        s.record(self._result(db, "Skithiryx, the Blight Dragon"), added=False)
        assert s.needs_review == 1
        assert s.skipped == 0

    def test_unreadable_is_skipped(self, db):
        s = ScanSession()
        s.record(self._result(db, "~~~"), added=False)
        assert s.skipped == 1
        assert s.entries[0]["card_name"] == "(unread)"

    def test_entries_capped_in_payload(self, db):
        s = ScanSession()
        for _ in range(60):
            s.record(self._result(db, "Lightning Bolt"), added=True)
        assert s.scanned == 60
        assert len(s.to_dict()["entries"]) == 50

    def test_to_dict_shape(self, db):
        s = ScanSession()
        s.record(self._result(db, "Lightning Bolt"), added=True)
        d = s.to_dict()
        assert set(d) >= {"scanned", "added", "skipped", "needs_review",
                          "value_usd", "unpriced", "entries"}


class TestRealCardFromPhotos:
    """Regressions from four real photos of a foil Death Wind (DTK 095/264).

    Every one of these was missing from the synthetic set, which scored 12/12
    while the real card failed outright.
    """

    @pytest.fixture
    def db_dtk(self, db):
        db.upsert_printings([
            printing_row_from_scryfall(
                _raw("dtk-95", "Death Wind", "dtk", "95", usd="0.07",
                     foil="0.48", finishes=("nonfoil", "foil"),
                     oracle="o-death"), "t"),
        ])
        return db

    def test_star_separator_parses(self):
        """The real footer reads "DTK * EN" with a STAR, not a bullet.

        With only bullets accepted, the set code never parsed, so every foil
        of this era fell through to guessing by name.
        """
        idn = parse_card_footer("095/264 U\nDTK ★ EN")
        assert idn.set_code == "dtk"
        assert idn.collector_number == "95"
        assert idn.has_exact_key

    def test_star_is_read_as_foil(self):
        idn = parse_card_footer("095/264 U\nDTK ★ EN")
        assert idn.foil_hint is True

    def test_no_star_is_not_foil(self):
        idn = parse_card_footer("095/264 U\nDTK • EN")
        assert idn.foil_hint is False

    def test_real_card_identifies_exactly(self, db_dtk):
        r = identify_card("Death Wind\n095/264 U\nDTK ★ EN", db_dtk)
        assert r.confidence == CONFIDENCE_EXACT
        assert r.best["set_code"] == "dtk"
        assert r.best["collector_number"] == "95"

    def test_foil_finish_is_suggested(self, db_dtk):
        """Filing this foil as non-foil records $0.07 instead of $0.48."""
        r = identify_card("Death Wind\n095/264 U\nDTK ★ EN", db_dtk)
        assert r.suggested_finish == "foil"

    def test_foil_never_suggested_for_a_nonfoil_only_printing(self, db):
        db.upsert_printings([
            printing_row_from_scryfall(
                _raw("nf-1", "Plainscard", "xyz", "5", usd="1.00",
                     finishes=("nonfoil",), oracle="o-nf"), "t")])
        r = identify_card("Plainscard\n005/264 C\nXYZ ★ EN", db)
        # A misread star must not record a finish that never existed.
        assert r.suggested_finish == "nonfoil"

    def test_ocr_rendering_the_star_as_asterisk(self):
        """OCR frequently returns * for the star glyph."""
        idn = parse_card_footer("095/264 U\nDTK * EN")
        assert idn.set_code == "dtk"


class TestMultiRenderingNoise:
    """The capture path now feeds `identify_card` a much noisier blob.

    Each frame is read as four crops in three renderings, so the text arrives
    with the real footer buried among a dozen lines of mirrored garbage from
    the wrong-orientation crops. Measured on rendered photographs, that noise
    was beating the real line and knocking correct reads down to "likely" or
    "ambiguous". These lock the arbitration that fixed it.
    """

    @pytest.fixture
    def db_dtk(self, db):
        db.upsert_printings([
            printing_row_from_scryfall(
                _raw("dtk-95", "Death Wind", "dtk", "95", usd="0.07",
                     foil="0.48", finishes=("nonfoil", "foil"),
                     oracle="o-death"), "t"),
        ])
        return db

    # Verbatim OCR output from a rendered sideways photo of the card.
    NOISY = ("n t9?/S60\n"
             "n we/S60\n"
             "Kdd!q-) n ne/S60\n"
             "Death Wind\n"
             "095/264 U DTK * EN")

    def test_garbage_lines_do_not_beat_the_real_footer(self, db_dtk):
        result = identify_card(self.NOISY, db_dtk)
        assert result.confidence == CONFIDENCE_EXACT
        assert result.best["collector_number"] == "95"

    def test_garbage_name_lines_do_not_veto_a_good_key(self, db_dtk):
        """Mirrored crops produce name-shaped lines that match nothing.

        The name cross-check exists to catch a misread key, so it must
        consider every plausible name line — if one junk line vetoing the
        read were enough, the check would reject correct scans.
        """
        result = identify_card(self.NOISY, db_dtk)
        assert result.confidence == CONFIDENCE_EXACT

    def test_a_genuinely_wrong_name_still_vetoes(self, db_dtk):
        """The veto must still fire when nothing plausible agrees."""
        result = identify_card("Lightning Bolt\n095/264 U DTK * EN", db_dtk)
        assert result.confidence == CONFIDENCE_AMBIGUOUS

    def test_separator_dropped_by_ocr_still_yields_a_set_code(self):
        """Windows OCR drops the star glyph outright on a clean render.

        Measured: "DTK ★ EN" comes back as "DTK EN". Requiring a separator
        meant those reads carried no set code at all.
        """
        idn = parse_card_footer("095/264 U DTK EN")
        assert idn.set_code == "dtk"
        assert idn.collector_number == "95"

    def test_a_bullet_vetoes_the_foil_reading(self):
        """A bullet is the non-foil separator and survives OCR reliably.

        When both glyphs appear across renderings of one card, the bullet is
        the trustworthy one — a false foil silently overvalues a collection.
        """
        idn = parse_card_footer("095/264 U DTK * EN\n095/264 U DTK • EN")
        assert idn.foil_hint is False

    def test_set_code_needs_a_real_language(self):
        """Dropping the separator requirement must not match prose."""
        idn = parse_card_footer("Target creature gets -X/-X")
        assert idn.set_code == ""


class TestWholeFrameReads:
    """When the card outline isn't found, the whole photo is OCR'd.

    Every case here comes from a real photograph of a foil Death Wind that
    the pipeline got wrong. The text arrives as one long run — name, rules
    text, flavour text and footer together — which breaks assumptions that
    hold fine for a tidy corner crop.
    """

    @pytest.fixture
    def db_dtk(self, db):
        db.upsert_printings([
            printing_row_from_scryfall(
                _raw("dtk-95", "Death Wind", "dtk", "95", usd="0.07",
                     foil="0.48", finishes=("nonfoil", "foil"),
                     oracle="o-death"), "t"),
        ])
        return db

    BLOB = ('Death Wind Instant Target creature gets -X/-X until end of turn. '
            '"1 am a dragonslayerfor Lord Silumgar. There is no dragon save '
            'him whom I fear." Xathi the Infallible 095/264 u DTK*EN HAMM 2015')

    def test_flavour_text_does_not_supply_the_set_code(self, db_dtk):
        """"Silumgar" parses as the set "SILUMG" in the language "AR".

        That false match sits earlier in the text than the real "DTK*EN", so
        taking the first set code found threw the card away entirely.
        """
        result = identify_card(self.BLOB, db_dtk)
        assert result.confidence == CONFIDENCE_EXACT
        assert result.best["set_code"] == "dtk"

    def test_name_is_recovered_from_the_front_of_the_blob(self, db_dtk):
        """A photo that read the name perfectly was reported unidentifiable.

        The whole run counts as one line, and that line holds the collector
        number, so the name filter discarded it wholesale.
        """
        text = ("Death Wind Instant Target creature gets -X/-X until end of "
                "turn. Xathi the Infallible 095/264 u DTK HAMM 2015")
        result = identify_card(text, db_dtk)
        assert result.auto_addable
        assert result.best["set_code"] == "dtk"

    def test_collector_number_narrows_a_named_card(self, db):
        """Name plus number identifies a printing with no set code at all."""
        db.upsert_printings([
            printing_row_from_scryfall(
                _raw("a-1", "Shared Name", "aaa", "5", oracle="o-sh"), "t"),
            printing_row_from_scryfall(
                _raw("b-2", "Shared Name", "bbb", "77", oracle="o-sh"), "t"),
        ])
        result = identify_card("Shared Name\n0077/0249 R", db)
        assert result.best["collector_number"] == "77"
        assert result.auto_addable


class TestSetCodeCapitalisation:
    """Case-insensitive matching is what reads OCR's "DtKtEN".

    It also walks straight into ordinary English, and the catalogue cannot
    arbitrate that: 411 of its 986 set codes are three letters, so a trimmed
    word hits a real set routinely. Capitalisation is the guard.
    """

    def test_a_card_name_is_not_a_set_code(self):
        # "Invade" -> INVA + language DE -> trimmed to INV, a real set.
        idn = parse_card_footer("Invade the City\n0201/0249 U")
        assert idn.set_code == ""

    def test_possessives_are_not_set_codes(self):
        # "Raven's" -> RAV + EN, and RAV is Ravnica.
        idn = parse_card_footer("Raven's Crime\n0041/0249 C")
        assert idn.set_code == ""

    def test_ocr_mangled_case_is_still_a_set_code(self):
        """A real photo returned "DtKtEN" for "DTK ★ EN"."""
        idn = parse_card_footer("095/264 u DtKtEN")
        assert idn.set_code == "dtkt"

    def test_mangled_code_resolves_by_trimming(self, db):
        db.upsert_printings([
            printing_row_from_scryfall(
                _raw("dtk-95", "Death Wind", "dtk", "95", oracle="o-d"), "t")])
        result = identify_card("095/264 u DtKtEN", db)
        assert result.confidence == CONFIDENCE_EXACT
        assert result.best["set_code"] == "dtk"

    def test_english_words_never_auto_add(self, db):
        """The whole point: prose must not file cardboard."""
        db.upsert_printings([
            printing_row_from_scryfall(
                _raw("inv-201", "Pincer Spider", "inv", "201", oracle="o-p"), "t")])
        result = identify_card("Invade the City\n0201/0249 U", db)
        assert not (result.auto_addable and result.best
                    and result.best["name"] == "Pincer Spider")


class TestDigitRepairLimits:
    def test_a_set_prefixed_number_survives_repair(self):
        """"MID-2I1" must not become "M10-211".

        D was mapped to 0 as an OCR lookalike; it turned a Midnight Hunt
        collector number into a real PLST one and auto-added the wrong card.
        """
        idn = parse_card_footer("Arlinn\nMID-2I1/O249 M\nPLST • EN")
        assert idn.collector_number == "mid-211"


class TestMultiplesAndUndo:
    """Adding several copies, and taking back one added by mistake.

    A box of cards holds playsets, and rescanning one card four times is both
    slower and more error-prone than saying "four of these". The undo matters
    just as much: a misfiled card noticed on the spot must be removable there
    and then, not hunted down on the desktop later.
    """

    def _session(self, db):
        from densa_deck.collection.scanner import ScanSession
        session = ScanSession()
        printing = db.printings_for_card("Skithiryx, the Blight Dragon")[0]
        return session, printing

    def test_extra_copies_count_and_total(self, db):
        session, printing = self._session(db)
        session.record_extra_copy(printing, "nonfoil")
        session.record_extra_copy(printing, "nonfoil")
        assert session.added == 2
        assert session.value_usd > 0

    def test_counts_collapse_to_one_row_per_printing(self, db):
        session, printing = self._session(db)
        for _ in range(4):
            session.record_extra_copy(printing, "nonfoil")
        counts = session.copy_counts()
        assert len(counts) == 1
        assert counts[0]["quantity"] == 4

    def test_finishes_are_separate_rows(self, db):
        session, printing = self._session(db)
        session.record_extra_copy(printing, "nonfoil")
        session.record_extra_copy(printing, "foil")
        assert len(session.copy_counts()) == 2

    def test_undo_reverses_the_totals(self, db):
        session, printing = self._session(db)
        session.record_extra_copy(printing, "nonfoil")
        before_value, before_added = session.value_usd, session.added
        session.record_extra_copy(printing, "nonfoil")
        assert session.undo_copy(printing["printing_id"], "nonfoil") is not None
        assert session.added == before_added
        assert session.value_usd == before_value

    def test_undo_only_reaches_this_session(self, db):
        """The phone may take back its own work, not raid the collection."""
        session, printing = self._session(db)
        assert session.undo_copy(printing["printing_id"], "nonfoil") is None

    def test_undo_does_not_take_the_wrong_finish(self, db):
        session, printing = self._session(db)
        session.record_extra_copy(printing, "nonfoil")
        assert session.undo_copy(printing["printing_id"], "foil") is None
        assert session.added == 1

    def test_unpriced_copies_undo_cleanly(self, db):
        """A card with no price must not corrupt the total when taken back."""
        from densa_deck.collection.scanner import ScanSession
        session = ScanSession()
        printing = db.printings_for_card("Obscure Singleton")[0]
        session.record_extra_copy(printing, "nonfoil")
        assert session.unpriced == 1
        session.undo_copy(printing["printing_id"], "nonfoil")
        assert session.unpriced == 0
        assert session.value_usd == 0.0
