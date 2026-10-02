"""Densa Deck on the shared Densanon hub.

The hub is one port (8770) for every Densanon app on a PC. While the phone
bridge is sharing, Densa Deck registers `/deck` there as well as keeping its
own 8791/8792. What these pin down:

* the route comes and goes with the bridge, never outliving the window;
* a phone's request through the hub reaches the same token-gated surface;
* the hub address reaches the phone through the QR and through /health,
  without disturbing anything an older phone reads;
* the old ports keep answering;
* the analyst's model goes through the shared model service, and keeps its
  seed per call.

Every test here runs against a hub on a throwaway port and home directory
(see the autouse fixture in conftest.py). None touches the real 8770 or
`~/.densanon`, and none loads a real model.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from densa_deck._vendor.densanon_hub import netutil as hub_netutil
from densa_deck._vendor.densanon_hub.config import load_secret
from densa_deck.app.phone import HUB_PREFIX, PhoneBridge, pairing_url

# A loopback address that the hub and the bridge both accept as "the LAN".
# Windows and Linux both route all of 127/8 to this machine, so a test can
# have a real second listener without touching a real network.
FAKE_LAN = "127.0.0.2"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _hub_port() -> int:
    return int(os.environ["DENSANON_HUB_PORT"])


class _FakeApi:
    """Just enough AppApi for the routes these tests call."""

    def _note_remote_change(self, areas):
        pass


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No real pairing file, no Tailscale CLI, no real network address."""
    monkeypatch.setenv("DENSA_PHONE_TOKEN_FILE", str(tmp_path / "pairing.json"))
    monkeypatch.setattr("densa_deck.app.phone.tailscale_status", lambda: {})


@pytest.fixture
def with_fake_lan(monkeypatch):
    """Make 127.0.0.2 this machine's LAN address, for the bridge and the hub."""
    monkeypatch.setattr("densa_deck.app.phone.lan_address", lambda: FAKE_LAN)
    monkeypatch.setattr(hub_netutil, "lan_address", lambda: FAKE_LAN)
    # http.server looks up the bound address's host name in server_bind. For
    # 127.0.0.2 that reverse lookup takes about five seconds on Windows, and
    # the hub does it inside the registration request, past the member's
    # three-second timeout. Skip the lookup; nothing here uses the name.
    monkeypatch.setattr(socket, "getfqdn", lambda name="": name or "localhost")


def _bridge(**kw) -> PhoneBridge:
    return PhoneBridge(_FakeApi(), port=_free_port(), companion_port=_free_port(),
                       use_tls=kw.pop("use_tls", False), **kw)


@pytest.fixture
def bridge():
    b = _bridge(bind_lan=False)
    yield b
    b.stop()


@pytest.fixture
def lan_bridge(with_fake_lan):
    b = _bridge(bind_lan=True)
    yield b
    b.stop()


def _settled(bridge, timeout=10.0) -> dict:
    """The hub status once the first lease has landed.

    A member's role can read `blocked` for one lease interval when the hub
    is slow to answer its first registration. It settles within a lease.
    """
    deadline = time.monotonic() + timeout
    while True:
        hub = bridge.status()["hub"]
        if hub["joined"] or time.monotonic() > deadline:
            return hub
        time.sleep(0.2)


def _request(url, *, method="GET", payload=None, token="", headers=None, timeout=5):
    data = json.dumps(payload or {}).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method)
    if method == "POST":
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Densa-Token", token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # the hub's certificate is self-signed
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def _hub_routes() -> list[dict]:
    """The hub's own routing table, read with the (test) hub secret."""
    secret = load_secret()
    req = urllib.request.Request(f"http://127.0.0.1:{_hub_port()}/_hub/routes",
                                 headers={"X-Densanon-Hub-Secret": secret})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())["routes"]


def _port_is_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class TestTheRouteFollowsTheBridge:
    def test_starting_the_bridge_joins_the_hub(self, bridge):
        bridge.start()
        hub = bridge.status()["hub"]
        # A fresh port with nobody on it: this process hosts the hub.
        assert hub["role"] == "host"
        assert hub["joined"] is True
        assert hub["port"] == _hub_port()
        routes = _hub_routes()
        assert [r["prefix"] for r in routes] == [HUB_PREFIX]
        # Forwarded to the PLAIN loopback companion listener; the hub, not
        # this server, is what speaks TLS to the phone.
        assert routes[0]["port"] == bridge.companion_port
        assert routes[0]["app"] == "densa-deck"

    def test_stopping_the_bridge_leaves_the_hub(self, bridge):
        bridge.start()
        assert not _port_is_free(_hub_port())
        bridge.stop()
        assert bridge.hub is None
        assert bridge.status()["hub"]["joined"] is False
        # We were hosting, and nothing else is running: the port is released.
        assert _port_is_free(_hub_port())

    def test_unpairing_leaves_the_hub_too(self, bridge):
        bridge.start()
        bridge.unpair()
        assert bridge.hub is None
        assert _port_is_free(_hub_port())

    def test_a_member_of_someone_elses_hub_unregisters_on_stop(self, bridge):
        """Another Densanon app hosting: we join it, and leave it cleanly."""
        from densa_deck._vendor.densanon_hub import HubMember
        other = HubMember("other-app", "/other", _free_port())
        other.start()
        try:
            bridge.start()
            assert bridge.status()["hub"]["role"] == "member"
            assert HUB_PREFIX in [r["prefix"] for r in _hub_routes()]
            bridge.stop()
            assert HUB_PREFIX not in [r["prefix"] for r in _hub_routes()]
        finally:
            other.stop()

    def test_a_port_held_by_another_program_changes_nothing(self, bridge):
        """`blocked`: the bridge works exactly as it did before the hub."""
        squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        squatter.bind(("127.0.0.1", _hub_port()))
        squatter.listen(1)
        try:
            result = bridge.start()
            assert result["ok"] is True
            hub = bridge.status()["hub"]
            assert hub["joined"] is False
            assert hub["urls"] == {}
            status, body, _ = _request(
                f"http://127.0.0.1:{bridge.companion_port}/api/tier",
                method="POST", token=bridge.token)
            assert status == 200 and "tier" in json.loads(body)
        finally:
            squatter.close()

    def test_a_slow_first_lease_while_hosting_is_not_called_blocked(self, bridge):
        """The hub's first registration can outlast the member's wait, which
        the member reports as `blocked` while holding the port itself."""
        bridge.start()
        bridge.hub.role = "blocked"     # what a timed-out first lease leaves
        assert bridge.hub.server is not None
        assert bridge.status()["hub"]["role"] == "starting"

    def test_declining_the_hub_is_possible(self):
        b = _bridge(bind_lan=False, join_hub=False)
        try:
            b.start()
            assert b.hub is None
            assert _port_is_free(_hub_port())
        finally:
            b.stop()


class TestRequestsThroughTheHub:
    def test_the_companion_api_answers_under_deck_with_the_token(self, bridge):
        bridge.start()
        base = f"http://127.0.0.1:{_hub_port()}{HUB_PREFIX}"
        status, body, _ = _request(f"{base}/api/tier", method="POST", token=bridge.token)
        assert status == 200
        assert "tier" in json.loads(body)

    def test_the_token_is_still_required_through_the_hub(self, bridge):
        bridge.start()
        base = f"http://127.0.0.1:{_hub_port()}{HUB_PREFIX}"
        status, _, _ = _request(f"{base}/api/tier", method="POST", token="wrong")
        assert status == 403
        status, _, _ = _request(f"{base}/api/tier", method="POST")
        assert status == 403

    def test_the_lan_reaches_the_route_because_lan_binding_is_on(self, lan_bridge):
        lan_bridge.start()
        assert _settled(lan_bridge)["urls"]["lan"] == \
            f"http://{FAKE_LAN}:{_hub_port()}{HUB_PREFIX}"
        url = f"http://{FAKE_LAN}:{_hub_port()}{HUB_PREFIX}/api/tier"
        # The hub opens its LAN listener on the lease after the route asks.
        deadline = time.monotonic() + 8
        while True:
            try:
                status, body, _ = _request(url, method="POST", token=lan_bridge.token,
                                           timeout=3)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        assert status == 200 and "tier" in json.loads(body)

    def test_the_lan_does_not_reach_the_route_when_lan_binding_is_off(
            self, with_fake_lan):
        b = _bridge(bind_lan=False)
        try:
            b.start()
            assert "lan" not in b.status()["hub"]["urls"]
            # Nobody asked for the LAN, so the hub does not listen there.
            with pytest.raises(OSError):
                _request(f"http://{FAKE_LAN}:{_hub_port()}{HUB_PREFIX}/health",
                         timeout=2)
        finally:
            b.stop()

    def test_health_believes_the_hubs_forwarded_peer(self, bridge):
        """Through the hub every request arrives from 127.0.0.1; the phone
        must still be told the address IT came from, which the hub writes."""
        bridge.start()
        _, body, _ = _request(
            f"http://127.0.0.1:{bridge.companion_port}/health",
            headers={"X-Densanon-Listener": "lan",
                     "X-Forwarded-For": "192.168.1.77"})
        assert json.loads(body)["peer"] == "192.168.1.77"

    def test_health_answers_through_the_hubs_lan_listener(self, lan_bridge):
        lan_bridge.start()
        _settled(lan_bridge)
        url = (f"http://{FAKE_LAN}:{_hub_port()}{HUB_PREFIX}/health"
               f"?token={lan_bridge.token}")
        deadline = time.monotonic() + 8
        while True:
            try:
                status, body, _ = _request(url, timeout=3)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        assert status == 200
        health = json.loads(body)
        assert health["hub"]["lan"].endswith(f":{_hub_port()}{HUB_PREFIX}")

    def test_a_client_cannot_forge_its_peer_on_the_legacy_port(self, bridge):
        """X-Forwarded-For is believed only from the hub, never from a phone."""
        bridge.start()
        status, body, _ = _request(
            f"http://127.0.0.1:{bridge.companion_port}/health",
            headers={"X-Forwarded-For": "100.64.0.9"})
        # No X-Densanon-Listener, so the header is ignored.
        assert json.loads(body)["peer"] == "127.0.0.1"


class TestTheBrowserScannerUnderDeck:
    def test_the_scan_page_is_served_under_deck(self, bridge):
        bridge.start()
        base = f"http://127.0.0.1:{_hub_port()}{HUB_PREFIX}"
        status, body, headers = _request(f"{base}/scan?t={bridge.token}")
        assert status == 200
        assert b"<html" in body.lower() or b"<!doctype" in body.lower()
        # The hub marks the page so prefix-less asset requests come home.
        assert "densanon_app=/deck" in (headers.get("Set-Cookie") or "")

    def test_the_page_calls_its_api_under_its_own_prefix(self):
        """`fetch("/api/...")` from /deck/scan would leave Densa Deck's route.

        The bridge sends `Referrer-Policy: no-referrer`, so the hub cannot
        route by Referer; the page derives its prefix from its own address
        instead of leaning on the hub's shared cookie.
        """
        page = (Path(__file__).resolve().parent.parent / "src" / "densa_deck" / "app"
                / "static" / "phone" / "scan.html").read_text(encoding="utf-8")
        assert 'fetch(BASE + "/api/" + route' in page
        assert 'location.pathname.replace(/\\/(scan\\/?)?$/, "")' in page

    def test_prefixless_requests_are_routed_by_the_cookie(self, bridge):
        bridge.start()
        root = f"http://127.0.0.1:{_hub_port()}"
        cookie = {"Cookie": "densanon_app=/deck"}
        status, body, _ = _request(f"{root}/static/scan.html", headers=cookie)
        assert status == 200 and b"densa-deck-token" in body
        status, body, _ = _request(f"{root}/api/tier", method="POST",
                                   token=bridge.token, headers=cookie)
        assert status == 200 and "tier" in json.loads(body)

    def test_the_scan_page_over_the_hubs_https(self, bridge):
        pytest.importorskip("cryptography")
        bridge.start()
        # The hub makes its certificate on a background thread, and the
        # member learns it is ready on the next lease renewal.
        deadline = time.monotonic() + 20
        while not bridge.hub.tls:
            if time.monotonic() > deadline:
                pytest.fail("the hub never reported a certificate")
            time.sleep(0.3)
        base = f"https://127.0.0.1:{_hub_port()}{HUB_PREFIX}"
        status, body, _ = _request(f"{base}/scan?t={bridge.token}")
        assert status == 200 and b"densa-deck-token" in body
        status, body, _ = _request(f"{base}/api/tier", method="POST", token=bridge.token)
        assert status == 200 and "tier" in json.loads(body)


class TestTheLegacyPortsStay:
    def test_8792_still_answers(self, bridge):
        bridge.start()
        status, body, _ = _request(
            f"http://127.0.0.1:{bridge.companion_port}/api/tier",
            method="POST", token=bridge.token)
        assert status == 200 and "tier" in json.loads(body)

    def test_8791_still_answers(self):
        b = _bridge(bind_lan=False, use_tls=True)
        try:
            b.start()
            scheme = "https" if b.status()["tls"] else "http"
            status, body, _ = _request(f"{scheme}://127.0.0.1:{b.port}/healthz")
            assert status == 200
            assert json.loads(body)["service"] == "densa-deck-phone"
        finally:
            b.stop()

    def test_a_taken_companion_port_still_gets_a_hub_route(self, monkeypatch):
        """Losing 8792 must not also cost the hub route."""
        b = _bridge(bind_lan=False)
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", b.companion_port))
        blocker.listen(1)
        try:
            b.start()
            assert "127.0.0.1" not in b.status()["companion_hosts"]
            assert b.hub_backend_port and b.hub_backend_port != b.companion_port
            status, body, _ = _request(
                f"http://127.0.0.1:{_hub_port()}{HUB_PREFIX}/api/tier",
                method="POST", token=b.token)
            assert status == 200 and "tier" in json.loads(body)
        finally:
            b.stop()
            blocker.close()


class TestThePhoneLearnsTheHub:
    def test_health_carries_the_hub_urls_with_the_token(self, lan_bridge):
        lan_bridge.start()
        _settled(lan_bridge)
        url = f"http://127.0.0.1:{lan_bridge.companion_port}/health"
        _, body, _ = _request(f"{url}?token={lan_bridge.token}")
        health = json.loads(body)
        assert health["hub"]["lan"] == f"http://{FAKE_LAN}:{_hub_port()}{HUB_PREFIX}"

    def test_health_without_the_token_says_nothing_about_the_hub(self, lan_bridge):
        lan_bridge.start()
        _, body, _ = _request(f"http://127.0.0.1:{lan_bridge.companion_port}/health")
        assert "hub" not in json.loads(body)

    def test_the_qr_carries_the_hub_and_keeps_every_old_field(self, lan_bridge):
        lan_bridge.start()
        _settled(lan_bridge)
        status = lan_bridge.status()
        url = pairing_url(status, {}, {}, status["token"])
        q = parse_qs(urlsplit(url).query)
        # Exactly what a phone that predates the hub reads, unchanged.
        assert q["t"] == [status["token"]]
        assert q["api"] == [f"http://{FAKE_LAN}:{lan_bridge.companion_port}"]
        assert q["lan"] == [f"http://{FAKE_LAN}:{lan_bridge.companion_port}"]
        # And the new one.
        assert q["hub"] == [f"http://{FAKE_LAN}:{_hub_port()}{HUB_PREFIX}"]
        assert "hubts" not in q   # no tailnet in this test

    def test_the_browser_gets_the_hubs_https_page_once_it_has_a_certificate(self):
        status = {
            "lan_host": "192.168.1.40", "tailnet_host": "", "port": 8791,
            "scheme": "https", "companion_port": 8792,
            "companion_hosts": ["127.0.0.1", "192.168.1.40"],
            "hub": {"tls": True, "urls": {
                "lan": "http://192.168.1.40:8770/deck",
                "lan_https": "https://192.168.1.40:8770/deck",
            }},
        }
        url = pairing_url(status, {}, {}, "tok")
        assert url.startswith("https://192.168.1.40:8770/deck/scan?t=tok&")
        q = parse_qs(urlsplit(url).query)
        assert q["api"] == ["http://192.168.1.40:8792"]
        assert q["hub"] == ["http://192.168.1.40:8770/deck"]

    def test_no_certificate_keeps_the_old_page(self):
        status = {
            "lan_host": "192.168.1.40", "tailnet_host": "100.64.1.2", "port": 8791,
            "scheme": "https", "companion_port": 8792,
            "companion_hosts": ["127.0.0.1", "192.168.1.40", "100.64.1.2"],
            "hub": {"tls": False, "urls": {
                "lan": "http://192.168.1.40:8770/deck",
                "tailnet": "http://100.64.1.2:8770/deck",
            }},
        }
        url = pairing_url(status, {}, {}, "tok")
        assert url.startswith("https://100.64.1.2:8791/scan?t=tok&")
        q = parse_qs(urlsplit(url).query)
        assert q["hubts"] == ["http://100.64.1.2:8770/deck"]

    def test_without_the_api_field_the_page_stays_on_the_old_origin(self):
        """An old phone with no `api` falls back to the link's own origin,
        and the hub's origin without /deck is not Densa Deck."""
        status = {
            "lan_host": "192.168.1.40", "tailnet_host": "", "port": 8791,
            "scheme": "https", "companion_port": 8792, "companion_hosts": [],
            "hub": {"tls": True, "urls": {"lan_https": "https://192.168.1.40:8770/deck"}},
        }
        assert pairing_url(status, {}, {}, "tok").startswith(
            "https://192.168.1.40:8791/scan?t=tok")

    def test_an_old_status_with_no_hub_gives_the_old_link(self):
        status = {"lan_host": "192.168.1.40", "tailnet_host": "", "port": 8791,
                  "scheme": "http", "companion_port": 8792,
                  "companion_hosts": ["192.168.1.40"]}
        assert pairing_url(status, {}, {}, "tok") == (
            "http://192.168.1.40:8791/scan?t=tok"
            "&api=http://192.168.1.40:8792&lan=http://192.168.1.40:8792")


class TestTheAnalystUsesTheSharedModelService:
    class _FakeModel:
        def __init__(self, calls):
            self.calls = calls

        def create_completion(self, prompt="", **kw):
            self.calls.append({"prompt": prompt, **kw})
            return {"choices": [{"text": f"  echo:{prompt}:{kw.get('seed')}  "}]}

    def _fake_loader(self, loads, calls):
        def load(entry):
            loads.append(entry)
            return self._FakeModel(calls)
        return load

    def test_generate_goes_through_shared_llama_with_the_seed(self, tmp_path):
        from densa_deck._vendor.densanon_hub.models import SharedLlama, read_catalog
        from densa_deck.analyst.backends.llama_cpp import LlamaCppBackend

        model = tmp_path / "qwen-analyst.gguf"
        model.write_bytes(b"GGUF not really")
        loads, calls = [], []
        backend = LlamaCppBackend(model_path=model, seed=7,
                                  loader=self._fake_loader(loads, calls))
        assert backend.generate("hello", max_tokens=5) == "echo:hello:7"
        assert isinstance(backend._llama, SharedLlama)
        assert backend._llama.model_id == "qwen-analyst"
        # The seed travels with the call, so it survives a model another app
        # loaded first.
        assert calls[0]["seed"] == 7
        assert calls[0]["max_tokens"] == 5
        assert len(loads) == 1
        # Catalogued in the TEST hub home, by id, never the real ~/.densanon.
        catalog = read_catalog()
        assert catalog["qwen-analyst"]["path"] == str(model.resolve())

    def test_the_same_seed_gives_the_same_call(self, tmp_path):
        from densa_deck.analyst.backends.llama_cpp import LlamaCppBackend

        model = tmp_path / "m.gguf"
        model.write_bytes(b"x")
        loads, calls = [], []
        backend = LlamaCppBackend(model_path=model, seed=11,
                                  loader=self._fake_loader(loads, calls))
        backend.generate("p")
        backend.generate("p")
        assert calls[0] == calls[1]
        assert len(loads) == 1   # loaded once, reused

    def test_constructing_or_probing_loads_nothing(self, tmp_path):
        from densa_deck._vendor.densanon_hub.models import read_catalog
        from densa_deck.analyst.backends.llama_cpp import LlamaCppBackend

        model = tmp_path / "m.gguf"
        model.write_bytes(b"x")
        loads = []
        backend = LlamaCppBackend(model_path=model, loader=self._fake_loader(loads, []))
        backend.is_available()
        assert backend._llama is None
        assert loads == []
        assert read_catalog() == {}

    def test_is_available_keeps_its_meaning(self, tmp_path):
        from densa_deck.analyst.backends.llama_cpp import LlamaCppBackend

        assert LlamaCppBackend(model_path=tmp_path / "missing.gguf").is_available() is False
        present = tmp_path / "here.gguf"
        present.write_bytes(b"x")
        try:
            import llama_cpp  # noqa: F401
            importable = True
        except ImportError:
            importable = False
        assert LlamaCppBackend(model_path=present).is_available() is importable
