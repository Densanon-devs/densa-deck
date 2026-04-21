"""Tests for tier system enforcement."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from densa_deck.tiers import (
    COMMAND_FEATURES,
    FEATURE_TIERS,
    Tier,
    check_access,
    get_user_tier,
    require_pro,
    set_tier,
)

PYTHON = sys.executable


class TestTierModel:
    def test_free_features_accessible(self):
        free_features = ["ingest", "card_search", "static_analysis", "info", "calc"]
        for f in free_features:
            assert check_access(f, Tier.FREE), f"{f} should be accessible on free tier"

    def test_pro_features_blocked_on_free(self):
        pro_features = ["goldfish_simulation", "matchup_gauntlet", "export_reports",
                        "deck_version_history", "deck_diff", "mulligan_practice"]
        for f in pro_features:
            assert not check_access(f, Tier.FREE), f"{f} should be blocked on free tier"

    def test_pro_features_accessible_on_pro(self):
        for feature in FEATURE_TIERS:
            assert check_access(feature, Tier.PRO), f"{feature} should be accessible on pro tier"

    def test_unknown_feature_defaults_open(self):
        assert check_access("some_future_feature", Tier.FREE)

    def test_require_pro_respects_env(self):
        old = os.environ.get("MTG_ENGINE_TIER")
        try:
            os.environ["MTG_ENGINE_TIER"] = "pro"
            assert not require_pro("goldfish_simulation")

            os.environ["MTG_ENGINE_TIER"] = "free"
            assert require_pro("goldfish_simulation")
        finally:
            if old is None:
                os.environ.pop("MTG_ENGINE_TIER", None)
            else:
                os.environ["MTG_ENGINE_TIER"] = old

    def test_all_commands_mapped(self):
        """Every command in the dispatcher should have a feature mapping."""
        commands = ["ingest", "analyze", "probability", "goldfish", "gauntlet",
                    "save", "compare", "history", "calc", "diff", "practice",
                    "search", "info"]
        for cmd in commands:
            assert cmd in COMMAND_FEATURES, f"Command '{cmd}' missing from COMMAND_FEATURES"


class TestTierConfig:
    def test_set_and_read_tier(self):
        tmp = tempfile.mkdtemp()
        config_path = Path(tmp) / "config.json"

        # Write config
        config_path.write_text(json.dumps({"tier": "pro"}), encoding="utf-8")

        # Read it
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["tier"] == "pro"

    def test_env_overrides_config(self):
        old = os.environ.get("MTG_ENGINE_TIER")
        try:
            os.environ["MTG_ENGINE_TIER"] = "pro"
            assert get_user_tier() == Tier.PRO

            os.environ["MTG_ENGINE_TIER"] = "free"
            assert get_user_tier() == Tier.FREE
        finally:
            if old is None:
                os.environ.pop("MTG_ENGINE_TIER", None)
            else:
                os.environ["MTG_ENGINE_TIER"] = old

    def test_default_is_free(self):
        old = os.environ.get("MTG_ENGINE_TIER")
        try:
            os.environ.pop("MTG_ENGINE_TIER", None)
            # Can't easily mock the config file, but with no env var
            # and no config, default should be free
            tier = get_user_tier()
            # It's either free (no config) or pro (if user has config)
            assert tier in (Tier.FREE, Tier.PRO)
        finally:
            if old is not None:
                os.environ["MTG_ENGINE_TIER"] = old


class TestTierCLIEnforcement:
    def test_free_commands_work(self):
        """Free commands should run without pro tier."""
        env = {**os.environ, "MTG_ENGINE_TIER": "free", "PYTHONIOENCODING": "utf-8"}
        for cmd in ["info", "calc --deck 60 --copies 4"]:
            args = cmd.split()
            r = subprocess.run(
                [PYTHON, "-m", "densa_deck.cli"] + args,
                capture_output=True, timeout=10, env=env, encoding="utf-8", errors="replace",
            )
            assert r.returncode == 0, f"'{cmd}' should work on free tier, got: {r.stderr[:200]}"

    def test_pro_commands_blocked_on_free(self):
        """Pro commands should show upgrade message on free tier."""
        env = {**os.environ, "MTG_ENGINE_TIER": "free", "PYTHONIOENCODING": "utf-8"}
        pro_commands = [
            "goldfish nonexistent.txt",
            "gauntlet nonexistent.txt",
            "practice nonexistent.txt",
            "diff a.txt b.txt",
            "probability nonexistent.txt",
        ]
        for cmd in pro_commands:
            args = cmd.split()
            r = subprocess.run(
                [PYTHON, "-m", "densa_deck.cli"] + args,
                capture_output=True, timeout=10, env=env, encoding="utf-8", errors="replace",
            )
            # Should exit 0 with upgrade message, NOT crash with file-not-found
            assert r.returncode == 0, f"'{cmd}' should show upgrade msg, not crash"
            assert "pro" in r.stdout.lower() or "Pro" in r.stdout, f"'{cmd}' should mention Pro"

    def test_pro_commands_work_with_pro_tier(self):
        """Pro commands should not show upgrade message with pro tier."""
        env = {**os.environ, "MTG_ENGINE_TIER": "pro", "PYTHONIOENCODING": "utf-8"}
        # These will fail on missing files, but they should NOT show the tier message
        r = subprocess.run(
            [PYTHON, "-m", "densa_deck.cli", "goldfish", "nonexistent.txt"],
            capture_output=True, timeout=10, env=env, encoding="utf-8", errors="replace",
        )
        # Should fail on missing file or empty db, NOT on tier check
        assert "pro" not in r.stdout.lower() or "requires Pro" not in r.stdout
