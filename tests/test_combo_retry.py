"""Combo refresh must survive a rate limit.

The walk is ~60 back-to-back requests, so a 429 is an ordinary event. Before
this, one 429 anywhere in the sequence raised through `raise_for_status()`,
discarded every page already fetched, and showed the user an opaque error
screen — observed against the live API on 2026-08-17.
"""

from __future__ import annotations

import httpx
import pytest

from densa_deck.combos.data import MAX_PAGE_ATTEMPTS, _get_page


class _Resp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}
        self.request = httpx.Request("GET", "https://example.test/variants/")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}",
                                        request=self.request, response=self)


class _Client:
    """Replays a scripted sequence of responses."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def get(self, url):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """Backoff is real seconds; tests shouldn't pay them."""
    async def _instant(_):
        return None
    monkeypatch.setattr("densa_deck.combos.data.asyncio.sleep", _instant)


class TestRateLimitRecovery:
    @pytest.mark.asyncio
    async def test_recovers_from_a_single_429(self):
        client = _Client([_Resp(429), _Resp(200, {"results": [], "next": None})])
        data = await _get_page(client, "https://example.test/variants/")
        assert data == {"results": [], "next": None}
        assert client.calls == 2

    @pytest.mark.asyncio
    async def test_recovers_from_several_429s(self):
        client = _Client([_Resp(429), _Resp(429), _Resp(429),
                          _Resp(200, {"results": [1]})])
        data = await _get_page(client, "https://example.test/variants/")
        assert data == {"results": [1]}

    @pytest.mark.asyncio
    async def test_gives_up_with_an_actionable_message(self):
        client = _Client([_Resp(429)] * MAX_PAGE_ATTEMPTS)
        with pytest.raises(RuntimeError) as exc:
            await _get_page(client, "https://example.test/variants/")
        msg = str(exc.value)
        # Must say what happened, that nothing was lost, and what to do.
        assert "rate limiting" in msg
        assert "unchanged" in msg
        assert "few minutes" in msg

    @pytest.mark.asyncio
    async def test_retries_transient_server_errors(self):
        client = _Client([_Resp(503), _Resp(200, {"results": []})])
        assert await _get_page(client, "https://example.test/variants/") == {"results": []}

    @pytest.mark.asyncio
    async def test_retries_connection_errors(self):
        client = _Client([httpx.ConnectError("reset"),
                          _Resp(200, {"results": []})])
        assert await _get_page(client, "https://example.test/variants/") == {"results": []}

    @pytest.mark.asyncio
    async def test_hard_4xx_fails_fast(self):
        """Retrying a genuinely bad request is just slower failure."""
        client = _Client([_Resp(404)])
        with pytest.raises(httpx.HTTPStatusError):
            await _get_page(client, "https://example.test/variants/")
        assert client.calls == 1

    @pytest.mark.asyncio
    async def test_success_does_not_retry(self):
        client = _Client([_Resp(200, {"results": []})])
        await _get_page(client, "https://example.test/variants/")
        assert client.calls == 1


class TestBackoffPolicy:
    @pytest.mark.asyncio
    async def test_honours_retry_after(self, monkeypatch):
        slept = []

        async def _record(seconds):
            slept.append(seconds)
        monkeypatch.setattr("densa_deck.combos.data.asyncio.sleep", _record)

        client = _Client([_Resp(429, headers={"Retry-After": "7"}),
                          _Resp(200, {"results": []})])
        await _get_page(client, "https://example.test/variants/")
        assert slept == [7.0]

    @pytest.mark.asyncio
    async def test_caps_an_absurd_retry_after(self, monkeypatch):
        """A 10-minute Retry-After must not freeze the progress bar."""
        slept = []

        async def _record(seconds):
            slept.append(seconds)
        monkeypatch.setattr("densa_deck.combos.data.asyncio.sleep", _record)

        client = _Client([_Resp(429, headers={"Retry-After": "600"}),
                          _Resp(200, {"results": []})])
        await _get_page(client, "https://example.test/variants/")
        from densa_deck.combos.data import MAX_BACKOFF_SECONDS
        assert slept[0] <= MAX_BACKOFF_SECONDS

    @pytest.mark.asyncio
    async def test_backoff_grows(self, monkeypatch):
        slept = []

        async def _record(seconds):
            slept.append(seconds)
        monkeypatch.setattr("densa_deck.combos.data.asyncio.sleep", _record)

        client = _Client([_Resp(429), _Resp(429), _Resp(200, {"results": []})])
        await _get_page(client, "https://example.test/variants/")
        assert slept[1] > slept[0]

    @pytest.mark.asyncio
    async def test_non_numeric_retry_after_falls_back(self, monkeypatch):
        slept = []

        async def _record(seconds):
            slept.append(seconds)
        monkeypatch.setattr("densa_deck.combos.data.asyncio.sleep", _record)

        client = _Client([_Resp(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                          _Resp(200, {"results": []})])
        await _get_page(client, "https://example.test/variants/")
        assert slept == [1.0]


class TestProgressReporting:
    @pytest.mark.asyncio
    async def test_tells_the_user_why_it_stalled(self):
        """A silent stall during backoff reads as a hang."""
        seen = []
        client = _Client([_Resp(429), _Resp(200, {"results": []})])
        await _get_page(client, "https://example.test/variants/",
                        progress_cb=lambda p, c, msg=None: seen.append(msg),
                        pages_done=3, combos_seen=1500)
        assert any(m and "Rate limited" in m for m in seen)

    @pytest.mark.asyncio
    async def test_tolerates_two_arg_callbacks(self):
        """Older progress callbacks take (pages, combos) only."""
        seen = []
        client = _Client([_Resp(429), _Resp(200, {"results": []})])
        await _get_page(client, "https://example.test/variants/",
                        progress_cb=lambda p, c: seen.append((p, c)))
        assert seen == [(0, 0)]


class TestPartialWalkIsKept:
    """A refresh that dies at page 117 must not throw away 58,000 combos.

    Combos are independent facts — most of them is worth vastly more than
    none, and the next refresh simply tops the store up.
    """

    def _fake_combo(self, i):
        from densa_deck.combos.models import Combo
        return Combo(combo_id=f"c{i}", cards=["Sol Ring"], color_identity="U")

    def test_partial_walk_carries_its_combos(self):
        from densa_deck.combos.data import PartialComboWalk
        combos = [self._fake_combo(i) for i in range(3)]
        exc = PartialComboWalk(combos, 117, "rate limited")
        assert exc.combos == combos
        assert exc.pages == 117

    def test_refresh_saves_what_it_got_then_signals(self, tmp_path, monkeypatch):
        from densa_deck.combos import ComboStore
        from densa_deck.combos.data import (
            PartialComboRefresh,
            PartialComboWalk,
            refresh_combo_snapshot,
        )

        kept = [self._fake_combo(i) for i in range(58)]

        async def _boom(**kwargs):
            raise PartialComboWalk(kept, 117, "Rate limited after 6 attempts.")
        monkeypatch.setattr("densa_deck.combos.data._walk_variants", _boom)

        store = ComboStore(db_path=tmp_path / "combos.db")
        with pytest.raises(PartialComboRefresh) as exc:
            refresh_combo_snapshot(store)

        # The data is really there.
        assert exc.value.combos_written == 58
        assert store.combo_count() == 58
        # And it's flagged as incomplete rather than passing for a full sync.
        assert store.get_metadata("last_refresh_partial") == "1"
        assert "didn't finish" in str(exc.value)

    def test_total_failure_still_raises_plainly(self, tmp_path, monkeypatch):
        from densa_deck.combos import ComboStore
        from densa_deck.combos.data import PartialComboWalk, refresh_combo_snapshot

        async def _boom(**kwargs):
            raise PartialComboWalk([], 0, "Rate limited immediately.")
        monkeypatch.setattr("densa_deck.combos.data._walk_variants", _boom)

        store = ComboStore(db_path=tmp_path / "combos.db")
        with pytest.raises(RuntimeError) as exc:
            refresh_combo_snapshot(store)
        # Not a PartialComboRefresh — nothing was saved.
        assert "Rate limited immediately" in str(exc.value)

    def test_full_walk_clears_the_partial_flag(self, tmp_path, monkeypatch):
        from densa_deck.combos import ComboStore
        from densa_deck.combos.data import refresh_combo_snapshot

        async def _ok(**kwargs):
            return [self._fake_combo(i) for i in range(5)]
        monkeypatch.setattr("densa_deck.combos.data._walk_variants", _ok)

        store = ComboStore(db_path=tmp_path / "combos.db")
        store.set_metadata("last_refresh_partial", "1")
        assert refresh_combo_snapshot(store) == 5
        assert store.get_metadata("last_refresh_partial") == ""


class TestPacing:
    def test_spacing_is_slower_than_the_original(self):
        """250ms sustained more requests than the server now tolerates."""
        from densa_deck.combos.data import PAGE_SPACING_SECONDS
        assert PAGE_SPACING_SECONDS >= 0.5


class TestResume:
    """A re-run must continue, not restart.

    Without a stored resume point the walk begins at page 1 every time,
    re-fetches the same pages, and trips the same rate limit — the store
    would plateau at whatever page the limiter first bites and never advance,
    while the error message cheerfully says "run it again".
    """

    def _fake_combo(self, i):
        from densa_deck.combos.models import Combo
        return Combo(combo_id=f"c{i}", cards=["Sol Ring"], color_identity="U")

    def test_partial_records_where_to_resume(self, tmp_path, monkeypatch):
        from densa_deck.combos import ComboStore
        from densa_deck.combos.data import (
            PartialComboRefresh,
            PartialComboWalk,
            refresh_combo_snapshot,
        )

        async def _boom(**kwargs):
            raise PartialComboWalk([self._fake_combo(0)], 112, "limited",
                                   next_url="https://api.test/variants/?offset=56000")
        monkeypatch.setattr("densa_deck.combos.data._walk_variants", _boom)

        store = ComboStore(db_path=tmp_path / "combos.db")
        with pytest.raises(PartialComboRefresh):
            refresh_combo_snapshot(store)
        assert store.get_metadata("last_refresh_next_url") == \
            "https://api.test/variants/?offset=56000"

    def test_next_run_starts_from_there(self, tmp_path, monkeypatch):
        from densa_deck.combos import ComboStore
        from densa_deck.combos.data import refresh_combo_snapshot

        seen = {}

        async def _capture(**kwargs):
            seen["start_url"] = kwargs.get("start_url")
            return [self._fake_combo(1)]
        monkeypatch.setattr("densa_deck.combos.data._walk_variants", _capture)

        store = ComboStore(db_path=tmp_path / "combos.db")
        store.set_metadata("last_refresh_partial", "1")
        store.set_metadata("last_refresh_next_url", "https://api.test/variants/?offset=56000")

        refresh_combo_snapshot(store)
        assert seen["start_url"] == "https://api.test/variants/?offset=56000"

    def test_completing_clears_the_resume_point(self, tmp_path, monkeypatch):
        from densa_deck.combos import ComboStore
        from densa_deck.combos.data import refresh_combo_snapshot

        async def _ok(**kwargs):
            return [self._fake_combo(1)]
        monkeypatch.setattr("densa_deck.combos.data._walk_variants", _ok)

        store = ComboStore(db_path=tmp_path / "combos.db")
        store.set_metadata("last_refresh_partial", "1")
        store.set_metadata("last_refresh_next_url", "https://api.test/x")
        refresh_combo_snapshot(store)
        assert store.get_metadata("last_refresh_next_url") == ""
        assert store.get_metadata("last_refresh_partial") == ""

    def test_restart_flag_ignores_the_resume_point(self, tmp_path, monkeypatch):
        """An explicit full rebuild must not silently resume."""
        from densa_deck.combos import ComboStore
        from densa_deck.combos.data import refresh_combo_snapshot

        seen = {}

        async def _capture(**kwargs):
            seen["start_url"] = kwargs.get("start_url")
            return [self._fake_combo(1)]
        monkeypatch.setattr("densa_deck.combos.data._walk_variants", _capture)

        store = ComboStore(db_path=tmp_path / "combos.db")
        store.set_metadata("last_refresh_partial", "1")
        store.set_metadata("last_refresh_next_url", "https://api.test/x")
        refresh_combo_snapshot(store, restart=True)
        assert seen["start_url"] is None

    def test_clean_store_starts_from_the_beginning(self, tmp_path, monkeypatch):
        from densa_deck.combos import ComboStore
        from densa_deck.combos.data import refresh_combo_snapshot

        seen = {}

        async def _capture(**kwargs):
            seen["start_url"] = kwargs.get("start_url")
            return [self._fake_combo(1)]
        monkeypatch.setattr("densa_deck.combos.data._walk_variants", _capture)

        store = ComboStore(db_path=tmp_path / "combos.db")
        refresh_combo_snapshot(store)
        assert seen["start_url"] is None
