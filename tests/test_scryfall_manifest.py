"""Contract tests for the Scryfall bulk-data manifest.

Card ingest broke in production while the suite stayed green, because
`test_app_api.py` mocked `fetch_bulk_data_manifest` with field names we had
chosen rather than field names Scryfall serves. The mock agreed with our
belief, so nothing noticed when the belief went stale.

These tests exist to make that failure mode harder to repeat:

  * The **shape** tests pin both manifest shapes Scryfall has published, so
    a future rename shows up as a named expectation rather than a KeyError
    in someone's terminal.
  * The **payload** tests pin the sniffing, since the bulk file changed
    encoding at the same time the fields were renamed.
  * The **live contract** test actually asks Scryfall what it serves today.
    It is marked `network` and skipped in CI — an offline suite that can
    never talk to the API can never notice the API changing, so this one is
    deliberately opt-in rather than absent.
"""

import gzip
import json

import pytest

from densa_deck.data.scryfall import (
    bulk_download_url,
    bulk_size_bytes,
    iter_raw_cards,
)

# The shape Scryfall serves as of 2026-08: renamed fields, gzipped JSON Lines.
CURRENT_MANIFEST = {
    "object": "bulk_data",
    "type": "oracle_cards",
    "updated_at": "2026-08-08T21:03:07.251+00:00",
    "uri": "https://api.scryfall.com/bulk-data/27bf3214",
    "name": "Oracle Cards",
    "jsonl_download_uri": "https://data.scryfall.io/oracle-cards/oracle-cards.jsonl.gz",
    "compressed_size": 24496961,
}

# The shape Scryfall served previously. The helpers still accept it, both so
# a rollback wouldn't break us and so old cached manifests keep working.
LEGACY_MANIFEST = {
    "object": "bulk_data",
    "type": "oracle_cards",
    "updated_at": "2026-04-01T00:00:00+00:00",
    "download_uri": "https://archive.scryfall.com/json/oracle_cards.json",
    "size": 350 * 1024 * 1024,
}


class TestManifestShape:
    def test_current_shape_resolves_url_and_size(self):
        assert bulk_download_url(CURRENT_MANIFEST).endswith(".jsonl.gz")
        assert bulk_size_bytes(CURRENT_MANIFEST) == 24496961

    def test_legacy_shape_still_resolves(self):
        assert bulk_download_url(LEGACY_MANIFEST).endswith(".json")
        assert bulk_size_bytes(LEGACY_MANIFEST) == 350 * 1024 * 1024

    def test_missing_url_raises_with_the_keys_actually_present(self):
        # The original failure was a bare KeyError: 'download_uri', which told
        # nobody what Scryfall had actually sent.
        with pytest.raises(KeyError) as excinfo:
            bulk_download_url({"object": "bulk_data", "type": "oracle_cards"})
        message = str(excinfo.value)
        assert "jsonl_download_uri" in message
        assert "type" in message  # the keys we did receive

    def test_missing_size_is_zero_not_an_error(self):
        # A missing size only affects a cosmetic "~N MB" label; it must not
        # take down the update check.
        assert bulk_size_bytes({"updated_at": "2026-01-01"}) == 0

    def test_unparseable_size_is_zero(self):
        assert bulk_size_bytes({"size": "not a number"}) == 0


class TestPayloadSniffing:
    """The payload encoding changed at the same time as the field names."""

    def _cards(self):
        return [
            {"id": "1", "name": "Alpha", "layout": "normal"},
            {"id": "2", "name": "Beta", "layout": "normal"},
        ]

    def test_reads_gzipped_json_lines(self, tmp_path):
        path = tmp_path / "cards.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            for card in self._cards():
                f.write(json.dumps(card) + "\n")
        assert [c["name"] for c in iter_raw_cards(path)] == ["Alpha", "Beta"]

    def test_reads_plain_json_lines(self, tmp_path):
        path = tmp_path / "cards.jsonl"
        path.write_text(
            "\n".join(json.dumps(c) for c in self._cards()), encoding="utf-8")
        assert [c["name"] for c in iter_raw_cards(path)] == ["Alpha", "Beta"]

    def test_reads_a_plain_json_array(self, tmp_path):
        path = tmp_path / "cards.json"
        path.write_text(json.dumps(self._cards()), encoding="utf-8")
        assert [c["name"] for c in iter_raw_cards(path)] == ["Alpha", "Beta"]

    def test_reads_a_gzipped_json_array(self, tmp_path):
        path = tmp_path / "cards.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(json.dumps(self._cards()))
        assert [c["name"] for c in iter_raw_cards(path)] == ["Alpha", "Beta"]

    def test_format_is_sniffed_not_taken_from_the_extension(self, tmp_path):
        # A misleading filename must not change how the file is read.
        path = tmp_path / "definitely_not_gzipped.json"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(json.dumps(self._cards()))
        assert [c["name"] for c in iter_raw_cards(path)] == ["Alpha", "Beta"]

    def test_blank_lines_in_json_lines_are_skipped(self, tmp_path):
        path = tmp_path / "cards.jsonl"
        path.write_text(
            '{"id":"1","name":"Alpha"}\n\n{"id":"2","name":"Beta"}\n\n',
            encoding="utf-8",
        )
        assert [c["name"] for c in iter_raw_cards(path)] == ["Alpha", "Beta"]

    def test_trailing_commas_in_json_lines_are_tolerated(self, tmp_path):
        # Some JSONL exports leave the array's commas behind on each line.
        path = tmp_path / "cards.jsonl"
        path.write_text(
            '{"id":"1","name":"Alpha"},\n{"id":"2","name":"Beta"},\n',
            encoding="utf-8",
        )
        assert [c["name"] for c in iter_raw_cards(path)] == ["Alpha", "Beta"]


@pytest.mark.network
class TestLiveContract:
    """Asks Scryfall what it serves today.

    Skipped in CI. Run with `pytest -m network` when touching ingest, or on a
    schedule — this is the test that would have caught the original break.
    """

    @pytest.fixture(scope="class")
    def manifest(self):
        """Live manifest, or skip.

        A 503 from Scryfall is their uptime, not our contract — this suite
        should only go red when the *shape* changes, so transport failures
        skip rather than fail. One fetch shared across the class also keeps
        us from hammering their API.
        """
        import asyncio

        import httpx

        from densa_deck.data.scryfall import fetch_bulk_data_manifest
        try:
            return asyncio.run(fetch_bulk_data_manifest())
        except (httpx.HTTPError, OSError) as exc:
            pytest.skip(f"Scryfall unreachable ({exc.__class__.__name__}): {exc}")
        except RuntimeError as exc:
            # Raised when the oracle_cards entry is missing entirely, which
            # IS a contract change and should be loud.
            pytest.fail(f"Scryfall no longer lists oracle_cards: {exc}")

    def test_live_manifest_yields_a_usable_url(self, manifest):
        assert bulk_download_url(manifest).startswith("https://")

    def test_live_manifest_reports_a_size(self, manifest):
        assert bulk_size_bytes(manifest) > 0

    def test_live_manifest_has_an_updated_at(self, manifest):
        assert manifest.get("updated_at")
