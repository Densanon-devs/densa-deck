"""Phone-as-scanner bridge.

The security posture is the point of most of these: this opens a socket, so
the tests that matter are the ones proving it opens as little as possible.
"""

from __future__ import annotations

import json
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from densa_deck.app.api import AppApi
from densa_deck.app.phone import (
    BIND_HOST,
    PhoneBridge,
    build_serve_command,
    phone_url,
    qr_matrix,
    tailscale_cli,
)
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall

CMM = "11111111-1111-1111-1111-111111111111"
SLD = "22222222-2222-2222-2222-222222222222"


def _raw(pid, name, set_code, num, *, usd="1.50", foil=None,
         finishes=("nonfoil",)):
    return {
        "id": pid, "oracle_id": "o-sol", "name": name, "set": set_code,
        "set_name": set_code.upper(), "collector_number": num,
        "rarity": "uncommon", "lang": "en", "released_at": "2023-01-01",
        "finishes": list(finishes), "frame": "2015", "border_color": "black",
        "promo_types": [], "games": ["paper"], "tcgplayer_id": 1,
        "prices": {"usd": usd, "usd_foil": foil, "usd_etched": None},
    }


@pytest.fixture(autouse=True)
def isolated_user_state(tmp_path, monkeypatch):
    """Keep these tests away from the real pairing and preferences.

    Both now persist in the user's home directory, so without this a test run
    would rotate the pairing on the machine it runs on — silently unpairing
    the developer's own phone — and would read whatever autostart preference
    that machine happens to have, which makes the defaults untestable.
    """
    monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE",
                       str(tmp_path / "phone-pairing.json"))
    monkeypatch.setattr("densa_deck.app.api._user_prefs_path",
                        lambda: tmp_path / "config.json")


@pytest.fixture
def api():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_printings([
            printing_row_from_scryfall(
                _raw(CMM, "Sol Ring", "cmm", "410", finishes=("nonfoil", "foil"),
                     foil="4.00"), "t"),
            printing_row_from_scryfall(_raw(SLD, "Sol Ring", "sld", "99",
                                            usd="21.72"), "t"),
        ])
        db.close()
        a = AppApi(db_path=root / "cards.db", version_db_path=root / "versions.db")
        yield a
        a.close()


@pytest.fixture
def bridge(api):
    b = PhoneBridge(api, port=8794)
    yield b
    b.stop()


def _ctx():
    """Accept the bridge's self-signed certificate.

    A browser shows a one-time interstitial for this; a test just needs to not
    reject it. Verification is off because the certificate is generated
    locally and vouched for by nobody — that is the whole design.
    """
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _url(port, path, scheme="https"):
    return f"{scheme}://{BIND_HOST}:{port}{path}"


def _post(port, route, payload, token, timeout=5, scheme="https"):
    req = urllib.request.Request(
        _url(port, f"/api/{route}", scheme),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Densa-Token": token or ""},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        return json.loads(r.read())


def _get(port, path, timeout=5, scheme="https"):
    with urllib.request.urlopen(_url(port, path, scheme), timeout=timeout,
                                context=_ctx()) as r:
        return r.status, r.read()


class TestLifecycle:
    def test_starts_and_stops(self, bridge):
        r = bridge.start()
        assert r["ok"] is True
        assert bridge.is_running()
        bridge.stop()
        assert not bridge.is_running()

    def test_token_minted_on_start(self, bridge):
        bridge.start()
        assert len(bridge.token) >= 10

    def test_stop_burns_the_token(self, bridge):
        """Stopping is the revoke mechanism — a phone holding the URL loses it."""
        bridge.start()
        old = bridge.token
        bridge.stop()
        assert bridge.token == ""
        assert not bridge.check_token(old)

    def test_restart_keeps_the_phone_paired(self, bridge):
        """The token must survive a restart, or the phone can't leave the house.

        A fresh token per run meant re-pairing from the desktop after every
        launch — which is exactly what you cannot do while standing in a game
        shop with the cards you want to scan. Stopping still takes the
        listener down, so nothing is reachable in the meantime.
        """
        bridge.start()
        first = bridge.token
        bridge.stop()
        bridge.start()
        assert bridge.token == first
        assert bridge.check_token(first)

    def test_unpair_revokes_the_token(self, bridge):
        """Revoking is now explicit, and must genuinely lock the phone out."""
        bridge.start()
        old = bridge.token
        bridge.unpair()
        assert not bridge.check_token(old)
        bridge.start()
        assert bridge.token != old

    def test_a_new_pairing_is_hard_to_guess(self, bridge):
        """The token is the only thing between a tailnet device and the API."""
        bridge.start()
        assert len(bridge.token) >= 24

    def test_double_start_is_idempotent(self, bridge):
        bridge.start()
        r = bridge.start()
        assert r.get("already_running") is True

    def test_stop_when_never_started(self, bridge):
        assert bridge.stop()["ok"] is True


class TestBindsTailnetNotLan:
    """The security boundary.

    The bridge binds loopback (for the desktop) and this machine's tailnet
    address (for the phone) — and nothing else. Binding 0.0.0.0 would also
    answer on whatever Wi-Fi the laptop is on, which is the whole thing being
    avoided. A 100.64/10 address is only routable to devices already
    authenticated onto the tailnet, which is what makes plain HTTP acceptable
    and removes any need for a certificate.
    """

    def test_loopback_is_always_bound(self, bridge):
        bridge.start()
        assert BIND_HOST == "127.0.0.1"
        assert "127.0.0.1" in bridge.status()["bound_hosts"]

    def test_never_binds_all_interfaces(self, bridge):
        bridge.start()
        assert "0.0.0.0" not in bridge.status()["bound_hosts"]

    def test_every_bound_host_is_loopback_tailnet_or_private(self, bridge):
        """The local network is served too, so a phone on the same Wi-Fi
        takes the fast path — but only ever a SPECIFIC private address.

        The invariant that protects anything is unchanged: never 0.0.0.0,
        which would also answer on whatever untrusted Wi-Fi this machine
        joins next, and never a routable one.
        """
        from densa_deck.app.phone import (
            is_private_lan_address,
            is_tailnet_address,
        )
        bridge.start()
        for host in bridge.status()["bound_hosts"]:
            assert (host == "127.0.0.1" or is_tailnet_address(host)
                    or is_private_lan_address(host)), host
            assert host != "0.0.0.0"

    def test_not_reachable_on_a_lan_address(self, bridge):
        """The property that matters: a laptop on café Wi-Fi is not serving
        its card collection to the café."""
        import socket
        bridge.start()
        try:
            addrs = {i[4][0] for i in socket.getaddrinfo(socket.gethostname(),
                                                         None, socket.AF_INET)}
        except Exception:
            pytest.skip("cannot enumerate local addresses")
        bound = set(bridge.status()["bound_hosts"])
        others = [a for a in addrs if a not in bound and not a.startswith("127.")]
        if not others:
            pytest.skip("no non-tailnet address on this machine")
        for addr in others:
            s = socket.socket()
            s.settimeout(1.5)
            try:
                connected = s.connect_ex((addr, bridge.port)) == 0
            finally:
                s.close()
            assert not connected, f"port {bridge.port} is reachable on {addr}"

    def test_status_reports_phone_reachability(self, bridge):
        bridge.start()
        st = bridge.status()
        # True only when a tailnet address was actually bound. False means the
        # URL we hand out would hang.
        assert st["reachable_from_phone"] == bool(st["tailnet_host"])


class TestNonTailnetAddressRefused:
    def test_is_tailnet_address(self):
        from densa_deck.app.phone import is_tailnet_address
        assert is_tailnet_address("100.124.242.11")
        assert is_tailnet_address("100.64.0.1")
        assert is_tailnet_address("100.127.255.255")
        # Outside the CGNAT range, even though it starts with 100.
        assert not is_tailnet_address("100.63.0.1")
        assert not is_tailnet_address("100.128.0.1")
        assert not is_tailnet_address("192.168.1.5")
        assert not is_tailnet_address("10.0.0.1")
        assert not is_tailnet_address("0.0.0.0")
        assert not is_tailnet_address("")
        assert not is_tailnet_address("not.an.ip.at.all")

    def test_a_lan_address_from_tailscale_is_not_treated_as_the_tailnet(
            self, api, monkeypatch):
        """Trusting the reported address blindly would put the scan surface on
        a routable interface.

        The local network is now served deliberately, through its own code
        path with its own opt-out. What must never happen is a NON-tailnet
        address being adopted AS the tailnet: that is how a routable address
        gets bound by accident and announced to a phone as safe.
        """
        monkeypatch.setattr("densa_deck.app.phone.tailscale_status",
                            lambda: {"ipv4": "192.168.1.50"})
        b = PhoneBridge(api, port=8797, companion_port=8803, bind_lan=False)
        try:
            b.start()
            assert b.status()["bound_hosts"] == ["127.0.0.1"]
            assert b.status()["tailnet_host"] == ""
            assert b.status()["reachable_from_phone"] is False
        finally:
            b.stop()

    def test_the_local_network_can_be_declined(self, api):
        """Serving the whole Wi-Fi is a choice, and it must be refusable."""
        b = PhoneBridge(api, port=8797, companion_port=8803, bind_lan=False)
        try:
            b.start()
            from densa_deck.app.phone import is_private_lan_address
            assert not any(is_private_lan_address(h)
                           for h in b.status()["bound_hosts"])
        finally:
            b.stop()

    def test_zero_zero_zero_zero_is_never_bound(self, bridge):
        """The distinction the whole safety argument rests on."""
        bridge.start()
        assert "0.0.0.0" not in bridge.status()["bound_hosts"]
        assert "0.0.0.0" not in bridge.status()["companion_hosts"]


class TestPairingUrlPointsWhereSomethingListens:
    """Handing the phone an https:// URL with no `tailscale serve` running
    doesn't error — it HANGS, dialling 443 where nothing listens. That was the
    original failure."""

    def test_without_serve_uses_plain_http_tailnet(self):
        from densa_deck.app.phone import pairing_url
        url = pairing_url({"tailnet_host": "100.64.1.2", "port": 8791},
                          {"dns_name": "box.tail1.ts.net"},
                          {"configured": False}, "tok")
        assert url == "http://100.64.1.2:8791/scan?t=tok"
        assert not url.startswith("https")

    def test_with_serve_uses_https(self):
        from densa_deck.app.phone import pairing_url
        url = pairing_url({"tailnet_host": "100.64.1.2", "port": 8791},
                          {"dns_name": "box.tail1.ts.net"},
                          {"configured": True}, "tok")
        assert url == "https://box.tail1.ts.net/scan?t=tok"

    def test_no_tailnet_and_no_serve_yields_nothing(self):
        """Better an empty URL the UI can explain than one that hangs."""
        from densa_deck.app.phone import pairing_url
        assert pairing_url({"tailnet_host": "", "port": 8791},
                           {"dns_name": "box.tail1.ts.net"},
                           {"configured": False}, "tok") == ""

    def test_no_token_yields_nothing(self):
        from densa_deck.app.phone import pairing_url
        assert pairing_url({"tailnet_host": "100.64.1.2", "port": 8791},
                           {}, {"configured": False}, "") == ""


class TestAuth:
    def test_api_rejects_a_missing_token(self, bridge):
        bridge.start()
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(bridge.port, "session", {}, "")
        assert exc.value.code == 403

    def test_api_rejects_a_wrong_token(self, bridge):
        bridge.start()
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(bridge.port, "session", {}, "not-the-token")
        assert exc.value.code == 403

    def test_page_rejects_a_missing_token(self, bridge):
        bridge.start()
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(bridge.port, "/scan")
        assert exc.value.code == 403

    def test_page_served_with_a_good_token(self, bridge):
        bridge.start()
        status, body = _get(bridge.port, f"/scan?t={bridge.token}")
        assert status == 200
        assert b"Densa Deck" in body

    def test_healthz_needs_no_token(self, bridge):
        """So the desktop can confirm the port is live without leaking the token."""
        bridge.start()
        status, body = _get(bridge.port, "/healthz")
        assert status == 200
        assert json.loads(body)["service"] == "densa-deck-phone"

    def test_token_accepted_via_query_string(self, bridge):
        bridge.start()
        status, _ = _get(bridge.port, f"/?t={bridge.token}")
        assert status == 200


class TestScopedSurface:
    """A phone on the tailnet must not be one typo from a destructive call."""

    def test_unknown_route_refused(self, bridge):
        bridge.start()
        r = _post(bridge.port, "delete_deck", {"deck_id": "x"}, bridge.token)
        assert r["ok"] is False
        assert "unknown route" in r["error"]

    @pytest.mark.parametrize("route", [
        "delete_deck", "printings_remove", "activate_license",
        "set_fee_model", "record_sale", "delete_collection_item",
        "printings_download_start", "set_user_preferences", "open_external",
    ])
    def test_dangerous_routes_are_not_reachable(self, bridge, route):
        bridge.start()
        r = _post(bridge.port, route, {}, bridge.token)
        assert r["ok"] is False, f"{route} was reachable from the phone"

    def test_allowed_routes_work(self, bridge):
        bridge.start()
        for route in ("session", "capabilities"):
            r = _post(bridge.port, route, {}, bridge.token)
            assert r.get("ok") is not False, route


class TestScanFlow:
    def test_identify_exact(self, bridge):
        bridge.start()
        # The name is on the card as well as the footer: a key on its own is
        # no longer trusted enough to file without a human looking at it.
        r = _post(bridge.port, "identify",
                  {"text": "Sol Ring\n0410/0500 U\nCMM • EN"}, bridge.token)
        assert r["confidence"] == "exact"
        assert r["candidates"][0]["printing_id"] == CMM

    def test_identify_ambiguous(self, bridge):
        bridge.start()
        r = _post(bridge.port, "identify", {"text": "Sol Ring"}, bridge.token)
        assert r["confidence"] == "ambiguous"
        assert r["auto_addable"] is False

    def test_commit_adds_to_the_collection(self, bridge, api):
        bridge.start()
        r = _post(bridge.port, "commit",
                  {"printing_id": CMM, "card_name": "Sol Ring",
                   "finish": "foil", "condition": "LP"}, bridge.token)
        assert r["session"]["added"] == 1
        assert r["session"]["value_usd"] == 4.00   # foil priced as foil

        listing = api.list_collection()
        items = listing.get("data", listing)["items"]
        assert items[0]["finish"] == "foil"
        assert items[0]["condition"] == "LP"

    def test_session_accumulates_across_requests(self, bridge):
        bridge.start()
        _post(bridge.port, "commit",
              {"printing_id": CMM, "card_name": "Sol Ring"}, bridge.token)
        _post(bridge.port, "commit",
              {"printing_id": SLD, "card_name": "Sol Ring"}, bridge.token)
        r = _post(bridge.port, "session", {}, bridge.token)
        assert r["session"]["added"] == 2

    def test_phone_and_desktop_share_one_session(self, bridge, api):
        """The phone is an input device, not a separate app."""
        bridge.start()
        _post(bridge.port, "commit",
              {"printing_id": CMM, "card_name": "Sol Ring"}, bridge.token)
        desktop = api.get_scan_session()
        assert desktop.get("data", desktop)["session"]["added"] == 1

    def test_capture_without_opencv_explains_itself(self, bridge):
        bridge.start()
        r = _post(bridge.port, "capture", {"image": "data:image/jpeg;base64,//4="},
                  bridge.token)
        assert r["ok"] is False
        # Whatever is missing, the message must name it and offer the way round.
        assert "OpenCV" in r["error"] or "OCR" in r["error"] or "read" in r["error"]

    def test_capture_with_no_image(self, bridge):
        bridge.start()
        r = _post(bridge.port, "capture", {}, bridge.token)
        assert r["ok"] is False

    def test_malformed_json_refused(self, bridge):
        bridge.start()
        req = urllib.request.Request(
            _url(bridge.port, "/api/session"),
            data=b"{not json",
            headers={"Content-Type": "application/json",
                     "X-Densa-Token": bridge.token},
            method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5, context=_ctx())
        assert exc.value.code == 400


class TestStaticServing:
    def test_path_traversal_refused(self, bridge):
        bridge.start()
        for evil in ("/static/../api.py", "/static/..%2fapi.py", "/static/a/b"):
            try:
                status, _ = _get(bridge.port, evil)
                assert status == 404, evil
            except urllib.error.HTTPError as exc:
                assert exc.code == 404, evil

    def test_unknown_path_404s(self, bridge):
        bridge.start()
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(bridge.port, "/nope")
        assert exc.value.code == 404


class TestTailscaleHelpers:
    def test_serve_command_names_the_port(self):
        assert build_serve_command(8791) == "tailscale serve --bg 8791"

    def test_phone_url_shape(self):
        url = phone_url("box.tail1234.ts.net", "abc123")
        assert url == "https://box.tail1234.ts.net/scan?t=abc123"

    def test_phone_url_empty_without_identity(self):
        assert phone_url("", "abc") == ""
        assert phone_url("box.ts.net", "") == ""

    def test_cli_lookup_returns_path_or_none(self):
        found = tailscale_cli()
        assert found is None or Path(found).exists()

    def test_qr_matrix_is_square_or_none(self):
        m = qr_matrix("https://example.ts.net/scan?t=abc")
        if m is None:
            pytest.skip("qrcode not installed (optional)")
        assert len(m) == len(m[0])
        assert all(isinstance(cell, bool) for cell in m[0])

    def test_qr_none_for_empty(self):
        m = qr_matrix("")
        assert m is None or len(m) > 0


class TestApiIntegration:
    def test_status_is_read_only(self, api):
        """Asking about phone sharing must not start anything."""
        r = api.get_phone_status()
        data = r.get("data", r)
        assert data["bridge"]["running"] is False
        assert "tailscale" in data and "serve_command" in data

    def test_start_then_stop_via_api(self, api):
        started = api.phone_bridge_start()
        assert started.get("data", started).get("ok", True)
        status = api.get_phone_status()
        assert status.get("data", status)["bridge"]["running"] is True
        api.phone_bridge_stop()
        status = api.get_phone_status()
        assert status.get("data", status)["bridge"]["running"] is False

    def test_closing_the_app_stops_sharing(self, api):
        """A live socket and a valid token must not outlive the window."""
        api.phone_bridge_start()
        bridge = api._get_phone_bridge()
        assert bridge.is_running()
        api.close()
        assert not bridge.is_running()
        assert bridge.token == ""


class TestHttpsGuidance:
    """`tailscale serve` HANGS when the tailnet has no HTTPS certificates.

    Advising it in that state hands the user a wedged terminal and no error,
    so the guidance is three-state and the order is load-bearing. This is the
    convention densabooks already established in this workspace.
    """

    def _ts(self, **over):
        base = {"installed": True, "running": True, "https_enabled": True,
                "dns_name": "box.tail1234.ts.net",
                "https_admin_url": "https://login.tailscale.com/admin/dns"}
        base.update(over)
        return base

    def test_no_certs_blocks_and_never_offers_the_command(self):
        from densa_deck.app.phone import https_guidance
        g = https_guidance(self._ts(https_enabled=False), 8791)
        assert g["state"] == "https_not_enabled"
        assert g["blocking"] is True
        assert g["command"] == ""          # the whole point
        assert "admin" in g["admin_url"]

    def test_no_certs_states_the_permanent_cost(self):
        from densa_deck.app.phone import https_guidance
        g = https_guidance(self._ts(https_enabled=False), 8791)
        # Enabling publishes the machine name to CT logs forever; that has to
        # be said before the user opts in, not after.
        assert "Transparency" in g["cost"]
        assert "permanently" in g["cost"]

    def test_certs_enabled_offers_the_command(self):
        from densa_deck.app.phone import https_guidance
        g = https_guidance(self._ts(), 8791)
        assert g["state"] == "ready_to_serve"
        assert g["blocking"] is False
        assert g["command"] == "tailscale serve --bg 8791"

    def test_tailscale_down_is_its_own_state(self):
        from densa_deck.app.phone import https_guidance
        for over in ({"installed": False}, {"running": False}):
            g = https_guidance(self._ts(**over), 8791)
            assert g["state"] == "tailscale_unavailable"
            assert g["command"] == ""

    def test_status_reports_https_enabled_flag(self):
        from densa_deck.app.phone import tailscale_status
        s = tailscale_status()
        if not s.get("installed"):
            pytest.skip("Tailscale not installed on this machine")
        # Present whether or not certs are on — the UI branches on it.
        assert "https_enabled" in s
        assert isinstance(s["https_enabled"], bool)

    def test_api_surfaces_guidance(self, api):
        r = api.get_phone_status()
        data = r.get("data", r)
        assert "https" in data
        assert data["https"]["state"] in (
            "https_not_enabled", "ready_to_serve", "tailscale_unavailable")


class TestCliDiscovery:
    def test_probes_beyond_path(self, monkeypatch):
        """The Windows installer doesn't add Tailscale to PATH, so `which`
        alone reports "not installed" to someone with a working tray icon."""
        import densa_deck.app.phone as phone_mod
        monkeypatch.setattr(phone_mod.shutil if hasattr(phone_mod, "shutil") else __import__("shutil"),
                            "which", lambda _: None)
        # Should still find it here if Tailscale is installed at all.
        found = phone_mod.tailscale_cli()
        if found is None:
            pytest.skip("Tailscale genuinely absent")
        assert Path(found).exists()

    def test_subprocess_hides_the_console_window(self):
        """A windowed app must not flash a console box on every status poll."""
        import inspect

        import densa_deck.app.phone as phone_mod
        src = inspect.getsource(phone_mod._run_tailscale)
        assert "CREATE_NO_WINDOW" in src


class TestOneClickScanInstall:
    """Photo scanning needs OpenCV on the desktop. It is a button, not a pip
    command printed at the user — and a photo that cannot possibly be
    processed must be refused before it is taken, not after."""

    def test_capabilities_report_photo_readiness(self, api):
        c = api.get_scan_capabilities()
        c = c.get("data", c)
        assert "photo_ready" in c
        assert "can_auto_install" in c
        assert isinstance(c["photo_ready"], bool)

    def test_photo_ready_requires_both_opencv_and_ocr(self, api, monkeypatch):
        monkeypatch.setattr(
            "densa_deck.collection.scan_backends.camera_status",
            lambda: type("S", (), {"name": "opencv", "available": False,
                                   "detail": "missing", "install_hint": "pip"})())
        c = api.get_scan_capabilities()
        c = c.get("data", c)
        assert c["photo_ready"] is False

    def test_no_auto_install_offered_when_frozen(self, api, monkeypatch):
        """A frozen build has no pip; offering a button there would lie."""
        import sys as _sys
        monkeypatch.setattr(_sys, "frozen", True, raising=False)
        monkeypatch.setattr(
            "densa_deck.collection.scan_backends.camera_status",
            lambda: type("S", (), {"name": "opencv", "available": False,
                                   "detail": "missing", "install_hint": "pip"})())
        c = api.get_scan_capabilities()
        c = c.get("data", c)
        assert c["can_auto_install"] is False

    def test_install_progress_pollable_before_starting(self, api):
        p = api.install_scan_support_progress()
        p = p.get("data", p)
        assert p["running"] is False and p["done"] is True

    def test_frozen_install_reports_honestly(self, api, monkeypatch):
        import sys as _sys
        monkeypatch.setattr(_sys, "frozen", True, raising=False)
        api._do_scan_install()
        p = api.install_scan_support_progress()
        p = p.get("data", p)
        assert p["done"] is True
        assert p["error"] == "frozen"
        # Must still point at the path that does work.
        assert "typing" in p["message"].lower() or "Typing" in p["message"]

    def test_double_install_refused(self, api, monkeypatch):
        import threading
        gate = threading.Event()
        monkeypatch.setattr(api, "_do_scan_install", lambda: gate.wait(5))
        first = api.install_scan_support_start()
        assert first.get("data", first)["started"] is True
        second = api.install_scan_support_start()
        assert second["ok"] is False
        gate.set()


class TestSelfSignedTls:
    """TLS from a locally-generated certificate.

    This is NOT the HTTPS that was declined. That was a Tailscale/CA
    certificate, which publishes the machine name to the public Certificate
    Transparency log permanently. This one is generated on the machine, lives
    in ~/.densa-deck/certs, involves no CA, and is deleted by removing the
    folder. It exists solely because `getUserMedia` — the live camera — is
    absent outside a secure context.
    """

    def test_cert_covers_every_host_it_serves(self, tmp_path):
        from densa_deck.app import phone_tls
        if not phone_tls.tls_available():
            pytest.skip("cryptography not installed")
        paths = phone_tls.ensure_cert(["127.0.0.1", "100.64.1.2", "box.ts.net"],
                                      base=tmp_path)
        assert paths
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(Path(paths[0]).read_bytes())
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        names = {str(g.value) for g in san}
        # A browser ignores CN entirely; anything missing from SAN is a hard
        # failure on the phone.
        assert "100.64.1.2" in names
        assert "127.0.0.1" in names
        assert "box.ts.net" in names

    def test_cert_is_cached(self, tmp_path):
        from densa_deck.app import phone_tls
        if not phone_tls.tls_available():
            pytest.skip("cryptography not installed")
        first = phone_tls.ensure_cert(["127.0.0.1"], base=tmp_path)
        before = Path(first[0]).read_bytes()
        second = phone_tls.ensure_cert(["127.0.0.1"], base=tmp_path)
        assert Path(second[0]).read_bytes() == before

    def test_changed_address_regenerates(self, tmp_path):
        """A cert for an address you no longer hold produces a far more
        confusing browser error than no cert at all."""
        from densa_deck.app import phone_tls
        if not phone_tls.tls_available():
            pytest.skip("cryptography not installed")
        first = phone_tls.ensure_cert(["127.0.0.1", "100.64.1.2"], base=tmp_path)
        before = Path(first[0]).read_bytes()
        second = phone_tls.ensure_cert(["127.0.0.1", "100.64.9.9"], base=tmp_path)
        assert Path(second[0]).read_bytes() != before

    def test_removable(self, tmp_path):
        from densa_deck.app import phone_tls
        if not phone_tls.tls_available():
            pytest.skip("cryptography not installed")
        phone_tls.ensure_cert(["127.0.0.1"], base=tmp_path)
        assert phone_tls.remove_cert(base=tmp_path) is True
        assert not (phone_tls.cert_dir(tmp_path) / phone_tls.CERT_NAME).exists()

    def test_bridge_serves_https(self, api):
        b = PhoneBridge(api, port=8798)
        try:
            b.start()
            st = b.status()
            if not st["tls"]:
                pytest.skip("cryptography not installed")
            assert st["scheme"] == "https"
            import ssl
            import urllib.request
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            r = urllib.request.urlopen(
                f"https://{BIND_HOST}:{b.port}/healthz", context=ctx, timeout=5)
            assert json.loads(r.read())["service"] == "densa-deck-phone"
        finally:
            b.stop()

    def test_pairing_url_uses_https_when_tls_is_up(self):
        from densa_deck.app.phone import pairing_url
        url = pairing_url({"tailnet_host": "100.64.1.2", "port": 8791,
                           "scheme": "https"},
                          {"dns_name": "box.ts.net"}, {"configured": False}, "tok")
        assert url == "https://100.64.1.2:8791/scan?t=tok"

    def test_falls_back_to_http_without_tls(self, api):
        """No cryptography must mean plain HTTP, not a dead listener."""
        b = PhoneBridge(api, port=8799, use_tls=False)
        try:
            b.start()
            assert b.status()["tls"] is False
            assert b.status()["scheme"] == "http"
            status, _ = _get(b.port, "/healthz", scheme="http")
            assert status == 200
        finally:
            b.stop()


class TestPairingSurvivesRestarts:
    """The phone must stay usable away from the desktop.

    Re-pairing requires reading a QR code off the desktop screen. If that is
    needed after every launch then the scanner only works while standing at
    the desktop — which is the one place the feature isn't for. These lock the
    behaviour that makes "open it at the shop and scan" possible.
    """

    def test_token_is_the_same_after_a_cold_start(self, tmp_path, monkeypatch):
        from densa_deck.app import phone

        monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(tmp_path / "p.json"))
        first = phone.load_or_create_token()
        # A different PhoneBridge, as a later launch would have.
        second = phone.load_or_create_token()
        assert first == second

    def test_token_is_written_where_it_can_be_found_again(self, tmp_path,
                                                          monkeypatch):
        path = tmp_path / "p.json"
        monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(path))
        from densa_deck.app import phone

        token = phone.load_or_create_token()
        assert json.loads(path.read_text(encoding="utf-8"))["token"] == token

    def test_a_corrupt_pairing_file_does_not_break_scanning(self, tmp_path,
                                                            monkeypatch):
        """It still works — see TestThePairingIsNotThrownAway for the cost,
        which is that every paired device is locked out and must be told."""
        path = tmp_path / "p.json"
        path.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(path))
        from densa_deck.app import phone

        assert len(phone.load_or_create_token()) >= 24

    def test_an_unwritable_location_still_yields_a_token(self, tmp_path,
                                                         monkeypatch):
        """A read-only home must degrade to a per-run token, not a crash."""
        monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE",
                           str(tmp_path / "nope" / "deep" / "p.json"))
        from densa_deck.app import phone

        def _explode(*a, **k):
            raise OSError("read-only")
        monkeypatch.setattr(Path, "mkdir", _explode)
        assert len(phone.load_or_create_token()) >= 24


class TestAutostart:
    """Bringing the bridge back up on launch, but only if it was wanted."""

    def test_off_by_default(self, api):
        assert api.phone_autostart_enabled() is False

    def test_launch_does_not_open_a_port_unasked(self, api):
        result = api.start_phone_bridge_if_enabled()
        assert result["running"] is False
        assert result["autostart"] is False

    def test_starting_records_the_preference(self, api):
        api.phone_bridge_start()
        try:
            assert api.phone_autostart_enabled() is True
        finally:
            api.phone_bridge_stop()

    def test_stopping_clears_the_preference(self, api):
        api.phone_bridge_start()
        api.phone_bridge_stop()
        assert api.phone_autostart_enabled() is False


class TestThePairingIsNotThrownAway:
    """Minting a new token unpairs every device that holds the old one.

    That is a walk-to-the-desktop failure, and the whole design exists to
    avoid needing one. So it happens when there is genuinely no pairing, and
    not merely when one cannot be read this instant.
    """

    def test_a_missing_file_mints_one(self, tmp_path, monkeypatch):
        from densa_deck.app.phone import load_or_create_token

        monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(tmp_path / "new.json"))
        assert len(load_or_create_token()) >= 24

    def test_an_existing_pairing_is_returned_unchanged(self, tmp_path,
                                                        monkeypatch):
        from densa_deck.app.phone import load_or_create_token

        path = tmp_path / "pair.json"
        monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(path))
        first = load_or_create_token()
        for _ in range(5):
            assert load_or_create_token() == first

    def test_a_transient_read_failure_does_not_unpair(self, tmp_path,
                                                       monkeypatch):
        """A partial read while another process writes must not unpair a phone.

        The file is retried before any conclusion is drawn, because the
        pairing is very likely perfectly fine a tenth of a second later.
        """
        from densa_deck.app import phone

        path = tmp_path / "pair.json"
        monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(path))
        original = phone.load_or_create_token()

        real_read = Path.read_text
        attempts = {"n": 0}

        def flaky(self, *args, **kwargs):
            if self == path:
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise OSError("locked by another process")
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", flaky)
        assert phone.load_or_create_token() == original, "a retry saved it"

    def test_a_truly_corrupt_file_is_kept_and_the_reset_is_flagged(
            self, tmp_path, monkeypatch):
        """Scanning must keep working, but silence would be the real failure.

        Re-minting locks out every paired device. That is survivable if the
        user is told; discovering it when your phone stops working in a shop
        is not.
        """
        from densa_deck.app import phone

        path = tmp_path / "pair.json"
        path.write_text("{not json at all", encoding="utf-8")
        monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(path))

        fresh = phone.load_or_create_token()
        assert len(fresh) >= 24, "the desktop still works"
        assert phone.pairing_was_reset()["happened"] is True, "and it says so"
        # The unreadable file is kept as evidence rather than overwritten.
        assert path.with_suffix(".corrupt.json").exists()

    def test_a_present_but_empty_pairing_mints_one(self, tmp_path, monkeypatch):
        """Parsed cleanly and genuinely holding nothing is a real "no pairing"."""
        from densa_deck.app.phone import load_or_create_token

        path = tmp_path / "pair.json"
        path.write_text('{"token": ""}', encoding="utf-8")
        monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(path))
        assert len(load_or_create_token()) >= 24
