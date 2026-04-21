"""Tests for the hash-based license key system."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from densa_deck.licensing import (
    LICENSE_PATH,
    MASTER_KEY,
    License,
    _hash_key,
    generate_license_key,
    validate_key,
    verify_license_key,
)


class TestHashFunction:
    def test_deterministic(self):
        """Same input always produces same hash."""
        a = _hash_key("cs_test_abc123")
        b = _hash_key("cs_test_abc123")
        assert a == b

    def test_different_inputs(self):
        """Different inputs produce different hashes."""
        a = _hash_key("cs_test_abc123")
        b = _hash_key("cs_test_xyz789")
        assert a != b

    def test_case_insensitive(self):
        """Hash normalizes to lowercase."""
        a = _hash_key("ABC123")
        b = _hash_key("abc123")
        assert a == b

    def test_empty_string(self):
        """Empty string still produces a valid hash."""
        h = _hash_key("")
        assert isinstance(h, str)
        assert len(h) > 0


class TestKeyGeneration:
    def test_format(self):
        """Generated keys match the DD-XXXX-XXXX-XXXX format."""
        key = generate_license_key("cs_test_session_abc123")
        import re
        assert re.match(r"^DD-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$", key)

    def test_deterministic(self):
        """Same seed always produces same key."""
        a = generate_license_key("cs_test_abc")
        b = generate_license_key("cs_test_abc")
        assert a == b

    def test_different_seeds_different_keys(self):
        a = generate_license_key("cs_test_abc")
        b = generate_license_key("cs_test_xyz")
        assert a != b

    def test_generated_key_validates(self):
        key = generate_license_key("cs_test_real_session")
        assert validate_key(key) is True


class TestKeyValidation:
    def test_master_key_validates(self):
        assert validate_key(MASTER_KEY) is True

    def test_generated_key_validates(self):
        key = generate_license_key("test-session-id")
        assert validate_key(key) is True

    def test_invalid_format_rejected(self):
        assert validate_key("not-a-valid-key") is False
        assert validate_key("") is False
        assert validate_key("DD-ABCD-EFGH") is False  # Missing third segment
        assert validate_key("DBR-ABCD-EFGH-IJKL") is False  # Wrong prefix

    def test_tampered_key_rejected(self):
        key = generate_license_key("test-session")
        # Change the last character of the checksum segment
        tampered = key[:-1] + ("X" if key[-1] != "X" else "Y")
        assert validate_key(tampered) is False

    def test_random_uppercase_rejected(self):
        assert validate_key("DD-ABCD-1234-WXYZ") is False

    def test_case_insensitive_format(self):
        """Lowercase keys still validate (gets normalized)."""
        key = generate_license_key("test")
        assert validate_key(key.lower()) is True

    def test_whitespace_stripped(self):
        key = generate_license_key("test")
        assert validate_key(f"  {key}  ") is True


class TestLicenseObject:
    def test_valid_license(self):
        key = generate_license_key("test")
        result = verify_license_key(key)
        assert result.valid is True
        assert result.grants_pro() is True
        assert result.is_master is False

    def test_master_license(self):
        result = verify_license_key(MASTER_KEY)
        assert result.valid is True
        assert result.is_master is True
        assert result.grants_pro() is True

    def test_invalid_license(self):
        result = verify_license_key("DD-1234-5678-9999")
        assert result.valid is False
        assert result.grants_pro() is False
        assert result.error != ""

    def test_empty_license(self):
        result = verify_license_key("")
        assert result.valid is False
        assert "empty" in result.error.lower()


class TestLicenseFileIO:
    def test_save_and_load_license(self):
        key = generate_license_key("test_session")
        tmp_path = Path(tempfile.mkdtemp()) / "license.key"
        with patch("densa_deck.licensing.LICENSE_PATH", tmp_path):
            from densa_deck.licensing import load_saved_license, save_license
            result = save_license(key)
            assert result.valid is True
            assert tmp_path.exists()

            loaded = load_saved_license()
            assert loaded is not None
            assert loaded.valid is True
            assert loaded.key == key
            assert loaded.activated_at != ""

    def test_load_no_license(self):
        tmp_path = Path(tempfile.mkdtemp()) / "no-license.key"
        with patch("densa_deck.licensing.LICENSE_PATH", tmp_path):
            from densa_deck.licensing import load_saved_license
            assert load_saved_license() is None

    def test_save_invalid_key_does_not_persist(self):
        tmp_path = Path(tempfile.mkdtemp()) / "license.key"
        with patch("densa_deck.licensing.LICENSE_PATH", tmp_path):
            from densa_deck.licensing import save_license
            result = save_license("not-a-valid-key")
            assert result.valid is False
            assert not tmp_path.exists()

    def test_remove_license(self):
        key = generate_license_key("test")
        tmp_path = Path(tempfile.mkdtemp()) / "license.key"
        with patch("densa_deck.licensing.LICENSE_PATH", tmp_path):
            from densa_deck.licensing import remove_license, save_license
            save_license(key)
            assert tmp_path.exists()
            assert remove_license() is True
            assert not tmp_path.exists()
            assert remove_license() is False  # Already removed

    def test_master_key_is_savable(self):
        tmp_path = Path(tempfile.mkdtemp()) / "license.key"
        with patch("densa_deck.licensing.LICENSE_PATH", tmp_path):
            from densa_deck.licensing import load_saved_license, save_license
            result = save_license(MASTER_KEY)
            assert result.valid is True
            loaded = load_saved_license()
            assert loaded is not None
            assert loaded.is_master is True


class TestJavaScriptCompatibility:
    """Critical: keys generated in the browser must validate in Python.

    These are KNOWN-GOOD values verified against the JavaScript implementation
    in densanon-site/densa-deck-success.html. If you change LICENSE_SALT or the
    hash function in either place, both will need to be updated and these
    tests will catch the drift.
    """

    def test_js_compat_seed_1(self):
        """seed 'cs_test_abc123' must produce 'DD-QZQJ-6T00-L1PE' in both impls."""
        assert generate_license_key("cs_test_abc123") == "DD-QZQJ-6T00-L1PE"

    def test_js_compat_seed_2(self):
        """seed 'cs_test_KNOWN' must produce 'DD-UHYP-YG00-6FOO' in both impls."""
        assert generate_license_key("cs_test_KNOWN") == "DD-UHYP-YG00-6FOO"

    def test_js_compat_hash_1(self):
        """Lower-level hash check for JS compat."""
        assert _hash_key("cs_test_abc123") == "qzqj6t"

    def test_js_compat_hash_2(self):
        assert _hash_key("cs_test_KNOWN") == "uhypyg"

    def test_known_session_id_produces_known_key(self):
        seed = "cs_test_known_session_id_for_test"
        key = generate_license_key(seed)
        import re
        assert re.match(r"^DD-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$", key)
        assert generate_license_key(seed) == key
        assert validate_key(key) is True
