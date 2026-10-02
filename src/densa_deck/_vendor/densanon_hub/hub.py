"""The host side: one port, a table of leased routes, and a forwarding proxy.

Whichever app binds the hub port first runs this. Every app, the host
included, then registers a route such as ``/deck`` that points at its own
backend on a loopback port, and renews it every couple of seconds. The table
is soft state, so it needs no handing over: when the host quits, the next app
to bind the port starts with an empty table, and the leases arriving over the
next couple of seconds fill it again.

The one thing that persists is the self-signed certificate, in the hub's home
directory.
"""

from __future__ import annotations

import hmac
import http.client
import json
import logging
import os
import re
import ssl
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__, netutil
from . import tls as tlsmod
from .config import HUB_PROTOCOL, LEASE_TTL, hub_home

log = logging.getLogger("densanon_hub")

SECRET_HEADER = "X-Densanon-Hub-Secret"
PREFIX_HEADER = "X-Densanon-Prefix"
LISTENER_HEADER = "X-Densanon-Listener"
COOKIE = "densanon_app"

_PREFIX_RE = re.compile(r"^/[a-z0-9][a-z0-9-]{0,31}$")
_REMOTE_KINDS = {netutil.LAN, netutil.TAILNET}
MAX_BODY = 64 * 1024 * 1024

# Headers that describe one connection, not the message, and must not cross
# the proxy (RFC 9110 section 7.6.1).
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
    # The hub has already answered 100-continue to the client itself.
    "expect",
}

# Headers a client could send to pass itself off as something it is not.
# Apps behind the hub decide who is local from exactly these, so a client's
# own copies are always dropped and the hub writes the real values.
_SPOOFABLE = ("x-forwarded-", "forwarded", "x-real-ip", "tailscale-user-", "x-densanon-")


def parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for piece in str(text).split("."):
        digits = re.match(r"\d+", piece)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts)


@dataclass
class Route:
    app: str
    prefix: str
    port: int
    exposure: frozenset = field(default_factory=frozenset)
    version: str = ""
    pid: int = 0
    expires: float = 0.0
    last_used: float = 0.0

    def public(self) -> dict:
        return {
            "app": self.app, "prefix": self.prefix, "port": self.port,
            "exposure": sorted(self.exposure), "version": self.version, "pid": self.pid,
        }


class HubServer:
    def __init__(self, secret: str, port: int, *, address_for=netutil.address_for,
                 home: Path | None = None, tls: bool = True):
        self.secret = secret
        self.port = port
        self.home = home or hub_home()
        self._tls = tls
        self._ssl_ctx: ssl.SSLContext | None = None
        self._tls_hosts: list[str] | None = None
        self._tls_lock = threading.Lock()
        self._address_for = address_for
        self._routes: dict[str, Route] = {}
        self._lock = threading.Lock()
        self._listeners: dict[str, netutil.ExclusiveServer] = {}
        self._stop = threading.Event()
        self.stopped = threading.Event()
        self.yielded = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Bind the loopback listener, or raise OSError if the port is taken."""
        self._listen(netutil.LOOPBACK, "127.0.0.1")
        threading.Thread(target=self._maintain, name="densanon-hub-reaper", daemon=True).start()
        # Making a certificate can take a second; nobody should wait for it.
        threading.Thread(target=self._refresh_tls, name="densanon-hub-tls", daemon=True).start()
        log.info("densanon hub hosting on port %d (pid %d)", self.port, os.getpid())

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        for server in list(self._listeners.values()):
            server.shutdown()
            server.server_close()
        self._listeners.clear()
        self.stopped.set()

    def _listen(self, kind: str, address: str) -> None:
        handler = type("Handler", (_Handler,), {"hub": self, "kind": kind})
        server = netutil.ExclusiveServer((address, self.port), handler)
        server.kind = kind
        server.ssl_context = self._ssl_ctx
        self._listeners[kind] = server
        threading.Thread(
            target=server.serve_forever, name=f"densanon-hub-{kind}", daemon=True,
        ).start()

    def _maintain(self) -> None:
        ticks = 0
        while not self._stop.wait(1.0):
            ticks += 1
            now = time.monotonic()
            with self._lock:
                for prefix in [p for p, r in self._routes.items() if r.expires < now]:
                    log.info("route %s expired", prefix)
                    del self._routes[prefix]
            self._sync_listeners()
            if ticks % 15 == 0:
                self._refresh_tls()  # addresses change: DHCP, Tailscale coming up

    def _refresh_tls(self) -> None:
        """Keep a certificate covering every address the hub listens on."""
        if not self._tls:
            return
        with self._tls_lock:
            hosts = ["127.0.0.1", "localhost"] + sorted(
                a for k, a in self.listening().items() if k != netutil.LOOPBACK)
            if hosts == self._tls_hosts:
                return
            try:
                pair = tlsmod.ensure_cert(self.home, hosts)
            except Exception as exc:  # a broken cryptography install must not kill the hub
                log.warning("could not make the hub certificate: %s", exc)
                pair = None
            ctx = tlsmod.context_for(*pair) if pair else None
            # Remember the attempt either way, so a host that cannot make a
            # certificate does not retry every few seconds.
            self._tls_hosts = hosts
            self._ssl_ctx = ctx
            for server in list(self._listeners.values()):
                server.ssl_context = ctx

    @property
    def tls_ready(self) -> bool:
        return self._ssl_ctx is not None

    def _sync_listeners(self) -> None:
        """Listen on the LAN or tailnet only while some app asks for it.

        Exposure is opt-in per app: DensaBooks, for one, deliberately never
        answers on local Wi-Fi. A network listener nobody needs is closed
        again, and a network that only appears later, like Tailscale coming
        up after login, is picked up on the next pass.
        """
        with self._lock:
            wanted = set().union(*(r.exposure for r in self._routes.values()))
        changed = False
        for kind in _REMOTE_KINDS:
            have = kind in self._listeners
            if kind in wanted and not have:
                address = self._address_for(kind)
                if address:
                    try:
                        self._listen(kind, address)
                        changed = True
                    except OSError as exc:
                        log.warning("could not listen on %s %s: %s", kind, address, exc)
            elif have and kind not in wanted:
                server = self._listeners.pop(kind)
                server.shutdown()
                server.server_close()
        if changed and not self._stop.is_set():
            # A new address needs to be in the certificate.
            threading.Thread(target=self._refresh_tls, daemon=True).start()

    def listening(self) -> dict[str, str]:
        return {kind: s.server_address[0] for kind, s in self._listeners.items()}

    # -- routing table -----------------------------------------------------

    def register(self, payload: dict) -> tuple[int, dict]:
        app = str(payload.get("app") or "")
        prefix = str(payload.get("prefix") or "")
        port = payload.get("port")
        exposure = payload.get("exposure") or []
        if not app or not _PREFIX_RE.match(prefix) or prefix == "/_hub":
            return 400, {"error": "bad_route", "detail": "app and a prefix like /deck are required"}
        if not isinstance(port, int) or not 0 < port < 65536:
            return 400, {"error": "bad_port"}
        if not isinstance(exposure, list) or not set(exposure) <= _REMOTE_KINDS:
            return 400, {"error": "bad_exposure", "detail": "exposure may list lan and tailnet"}
        now = time.monotonic()
        with self._lock:
            held = self._routes.get(prefix)
            if held and held.app != app and held.expires >= now:
                return 409, {"error": "prefix_taken", "app": held.app}
            same = held is not None and held.app == app
            route = Route(
                app=app, prefix=prefix, port=port, exposure=frozenset(exposure),
                version=str(payload.get("version") or ""),
                pid=int(payload.get("pid") or 0),
                expires=now + LEASE_TTL,
                last_used=held.last_used if same else now,
            )
            self._routes[prefix] = route
        if not held or held.exposure != frozenset(exposure):
            self._sync_listeners()
        # idle_seconds lets an app with nothing on screen decide to exit.
        return 200, {**self.identity(), "idle_seconds": round(now - route.last_used, 1),
                     "tls": self.tls_ready}

    def unregister(self, payload: dict) -> tuple[int, dict]:
        app = str(payload.get("app") or "")
        with self._lock:
            for prefix in [p for p, r in self._routes.items() if r.app == app]:
                del self._routes[prefix]
        self._sync_listeners()
        return 200, {"ok": True}

    def routes(self) -> list[dict]:
        with self._lock:
            return [r.public() for r in self._routes.values()]

    def identity(self) -> dict:
        return {
            "service": "densanon-hub", "protocol": HUB_PROTOCOL,
            "version": __version__, "pid": os.getpid(),
        }

    def resolve(self, path: str, referer: str, cookie: str) -> tuple[Route | None, str]:
        """Find the app a request belongs to, and the path to send it.

        A prefixed path is the normal case and loses its prefix on the way
        through, so an app sees the same paths it always has. Web pages are
        the exception: a page served under ``/books`` still asks for
        ``/static/app.js``. Those requests are matched by the page that asked
        (the Referer), then by the cookie the hub set on the page, and keep
        their path unchanged.
        """
        split = urlsplit(path)
        query = f"?{split.query}" if split.query else ""
        with self._lock:
            routes = dict(self._routes)
        for prefix in sorted(routes, key=len, reverse=True):
            if split.path == prefix or split.path.startswith(prefix + "/"):
                return routes[prefix], (split.path[len(prefix):] or "/") + query
        ref_path = urlsplit(referer).path if referer else ""
        for prefix in sorted(routes, key=len, reverse=True):
            if ref_path == prefix or ref_path.startswith(prefix + "/"):
                return routes[prefix], path
        app_prefix = _cookie_value(cookie, COOKIE)
        if app_prefix in routes:
            return routes[app_prefix], path
        return None, path

    def check_secret(self, supplied: str) -> bool:
        return bool(supplied) and hmac.compare_digest(supplied, self.secret)

    def request_yield(self, payload: dict) -> tuple[int, dict]:
        """Hand the port to a newer hub.

        Apps update one at a time, so the host may be running an older copy
        of this library than an app that starts later. The newer one asks,
        the host steps down, and the newer one binds the port.
        """
        theirs = parse_version(payload.get("version", ""))
        if theirs <= parse_version(__version__):
            return 409, {"error": "not_newer", **self.identity()}
        self.yielded = True
        # Stop after this reply has gone out, not before.
        threading.Timer(0.2, self.stop).start()
        return 200, {"yielding": True}


def _cookie_value(header: str, name: str) -> str:
    for part in (header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


class _Handler(BaseHTTPRequestHandler):
    hub: HubServer
    kind: str
    protocol_version = "HTTP/1.1"
    server_version = "DensanonHub"
    timeout = 120  # an idle keep-alive connection must not hold a thread forever

    def log_message(self, fmt, *args):  # route through logging, not stderr
        log.debug("%s %s", self.address_string(), fmt % args)

    def do_GET(self): self._handle()
    def do_POST(self): self._handle()
    def do_PUT(self): self._handle()
    def do_PATCH(self): self._handle()
    def do_DELETE(self): self._handle()
    def do_HEAD(self): self._handle()
    def do_OPTIONS(self): self._handle()

    # -- replies -----------------------------------------------------------

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _read_body(self) -> bytes | None:
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            return self._read_chunked()
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return None
        return self.rfile.read(length) if length else b""

    def _read_chunked(self) -> bytes | None:
        out = bytearray()
        while True:
            size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
            if size == 0:
                while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass  # trailers
                return bytes(out)
            out += self.rfile.read(size)
            self.rfile.readline()
            if len(out) > MAX_BODY:
                return None

    # -- dispatch ----------------------------------------------------------

    def _handle(self) -> None:
        if self.path.startswith("/_hub/"):
            return self._control()
        route, upstream_path = self.hub.resolve(
            self.path, self.headers.get("Referer", ""), self.headers.get("Cookie", ""),
        )
        # An app that has not asked for this network does not exist on it.
        # 404 rather than 403, so the hub does not confirm the app is here.
        if route is None or (self.kind != netutil.LOOPBACK and self.kind not in route.exposure):
            return self._json(404, {"error": "not_found"})
        body = self._read_body()
        if body is None:
            return self._json(413, {"error": "too_large"})
        stripped = upstream_path != self.path
        self._forward(route, upstream_path, body, stripped)

    def _control(self) -> None:
        name = urlsplit(self.path).path[len("/_hub/"):]
        if name == "ping" and self.command in ("GET", "HEAD"):
            return self._json(200, self.hub.identity())
        local = self.kind == netutil.LOOPBACK and netutil.classify(self.client_address[0]) == netutil.LOOPBACK
        if not local or not self.hub.check_secret(self.headers.get(SECRET_HEADER, "")):
            return self._json(404, {"error": "not_found"})
        if name == "routes" and self.command == "GET":
            return self._json(200, {"routes": self.hub.routes(), "listening": self.hub.listening()})
        if self.command != "POST":
            return self._json(405, {"error": "method"})
        try:
            payload = json.loads(self._read_body() or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad_json"})
        if not isinstance(payload, dict):
            return self._json(400, {"error": "bad_json"})
        action = {
            "register": self.hub.register,
            "unregister": self.hub.unregister,
            "yield": self.hub.request_yield,
        }.get(name)
        if action is None:
            return self._json(404, {"error": "not_found"})
        self._json(*action(payload))

    def _forward(self, route: Route, path: str, body: bytes, stripped: bool) -> None:
        headers = {}
        for key, value in self.headers.items():
            low = key.lower()
            if low in _HOP_BY_HOP or low == "content-length" or low.startswith(_SPOOFABLE):
                continue
            headers[key] = value
        route.last_used = time.monotonic()
        if self.kind != netutil.LOOPBACK:
            # The app sees every request arrive from 127.0.0.1. Without these
            # a phone would look like the desktop itself, which some apps
            # (DensaBooks) treat as signed in.
            headers["X-Forwarded-For"] = self.client_address[0]
            headers["X-Forwarded-Proto"] = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
            headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        if stripped:
            headers[PREFIX_HEADER] = route.prefix
        headers[LISTENER_HEADER] = self.kind
        if body or self.command in ("POST", "PUT", "PATCH"):
            headers["Content-Length"] = str(len(body))

        conn = http.client.HTTPConnection("127.0.0.1", route.port, timeout=300)
        try:
            try:
                conn.request(self.command, path, body=body or None, headers=headers)
                resp = conn.getresponse()
            except OSError:
                return self._json(502, {"error": "app_unavailable", "app": route.app})

            self.send_response(resp.status, resp.reason)
            length = resp.getheader("Content-Length")
            for key, value in resp.getheaders():
                low = key.lower()
                if low in _HOP_BY_HOP or low in ("content-length", "server", "date"):
                    continue
                self.send_header(key, value)
            if stripped:
                self.send_header("Set-Cookie", f"{COOKIE}={route.prefix}; Path=/; SameSite=Lax")
            no_body = self.command == "HEAD" or resp.status in (204, 304) or resp.status < 200
            chunked = not no_body and length is None
            if length is not None:
                self.send_header("Content-Length", length)
            elif chunked:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if no_body:
                return
            # read1 returns whatever has arrived, so a streamed reply (model
            # tokens, server-sent events) reaches the phone as it is written.
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                if chunked:
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                else:
                    self.wfile.write(chunk)
                self.wfile.flush()
            if chunked:
                self.wfile.write(b"0\r\n\r\n")
        except OSError:
            # The client or the app hung up mid-reply; nothing to tell anyone.
            self.close_connection = True
        finally:
            conn.close()
