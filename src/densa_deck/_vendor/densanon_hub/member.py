"""The app side: join the hub that is running, or become it.

An app runs its own backend on a loopback port as it always has, then hands
that port to a ``HubMember``. The member keeps one lease alive:

- if a hub is answering on the hub port, it registers there;
- if nothing is, it binds the port and hosts the hub itself;
- if the host goes away, the next renewal finds the port free and one of the
  surviving apps takes it over;
- if the host runs an older copy of this library, the member asks it to step
  down and takes the port.

If something that is not a Densanon hub holds the port, the member reports
``blocked`` and the app should keep its own listener, as it did before the
hub existed.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import threading
import time
from pathlib import Path

from . import __version__, netutil
from .config import LEASE_INTERVAL, hub_port, load_secret
from .hub import SECRET_HEADER, HubServer, parse_version

log = logging.getLogger("densanon_hub")

HOST = "host"
MEMBER = "member"
BLOCKED = "blocked"
CONFLICT = "conflict"
STARTING = "starting"
STOPPED = "stopped"


class HubMember:
    def __init__(
        self,
        app: str,
        prefix: str,
        port: int,
        *,
        exposure: tuple[str, ...] | list[str] = (),
        app_version: str = "",
        hub_port_: int | None = None,
        home: Path | None = None,
        on_change=None,
        tls: bool = True,
    ):
        self.app = app
        self.prefix = prefix
        self.port = port
        self.exposure = list(exposure)
        self.app_version = app_version
        self.hub_port = hub_port_ or hub_port()
        self.home = home
        self.on_change = on_change
        self._tls_wanted = tls
        self.role = STARTING
        self.detail = ""
        self.server: HubServer | None = None
        self._secret = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._defer_bind_until = 0.0
        self._tick_lock = threading.Lock()
        # From the last renewal: how long since a request reached this app
        # through the hub, and whether the hub can serve HTTPS.
        self.idle_seconds = 0.0
        self.tls = False

    # -- public ------------------------------------------------------------

    def start(self) -> str:
        """Join or host, and keep doing so in the background. Returns the first role."""
        self._secret = load_secret(self.home)
        self._tick()
        self._thread = threading.Thread(target=self._run, name=f"densanon-hub-{self.app}", daemon=True)
        self._thread.start()
        return self.role

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self.role in (HOST, MEMBER):
            self._post("unregister", {"app": self.app})
        if self.server:
            self.server.stop()
            self.server = None
        self._set(STOPPED)

    def set_exposure(self, exposure) -> None:
        """Change which networks this app answers on, effective on the next renewal."""
        self.exposure = list(exposure)
        self._tick()

    def status(self) -> dict:
        return {
            "role": self.role, "detail": self.detail, "hub_port": self.hub_port,
            "prefix": self.prefix, "listening": self.server.listening() if self.server else None,
            "idle_seconds": self.idle_seconds, "tls": self.tls, "urls": self.urls(),
        }

    def urls(self) -> dict[str, str]:
        """Where a phone can reach this app through the hub, by network.

        Only networks this app asked for, and only while it is part of a
        hub. Put these in the pairing QR and the token-gated /health so a
        phone learns the hub address, and keeps learning it when the PC's
        address changes.
        """
        if self.role not in (HOST, MEMBER):
            return {}
        out = {}
        for kind in self.exposure:
            address = netutil.address_for(kind)
            if address:
                out[kind] = f"http://{address}:{self.hub_port}{self.prefix}"
                if self.tls:
                    out[f"{kind}_https"] = f"https://{address}:{self.hub_port}{self.prefix}"
        return out

    # -- the lease loop ----------------------------------------------------

    def _run(self) -> None:
        while not self._stop.wait(LEASE_INTERVAL):
            try:
                self._tick()
            except Exception:  # never let the lease thread die
                log.exception("hub lease renewal failed")

    def _tick(self) -> None:
        # start(), set_exposure() and the lease thread can all get here.
        with self._tick_lock:
            self._tick_locked()

    def _tick_locked(self) -> None:
        if self.server and self.server.stopped.is_set():
            # We hosted and stepped down for a newer hub. Give it the port.
            self.server = None
            self._defer_bind_until = time.monotonic() + 3.0

        # Try the port before knocking on it. A bind succeeds or fails at once,
        # while a refused loopback connect on Windows takes about two seconds,
        # and that delay would be added to every handoff.
        if self.server is None and time.monotonic() >= self._defer_bind_until and self._host():
            status, reply = self._post("register", self._lease())
            if status == 200:
                self._note(reply)
                return self._set(HOST)
            # We hold the port, so whatever went wrong is ours and passing:
            # stay "starting" and register on the next tick. Reporting
            # "blocked" here sent apps back to their own ports for nothing.
            return

        status, reply = self._post("register", self._lease())
        if status == 200 and reply.get("service") != "densanon-hub":
            # Something answered 200 to anything, which is not a hub.
            return self._set(BLOCKED, self._who_holds_port())
        if status == 200:
            self._note(reply)
            if self.server is None:
                # The newer hub we stepped down for is up, so the reason to
                # hold back from the port is gone. Leaving the hold in place
                # made a host killed soon after a takeover take five seconds
                # to replace instead of one.
                self._defer_bind_until = 0.0
            if self.server is None and self._newer_than(reply):
                return self._take_over()
            return self._set(HOST if self.server else MEMBER)
        if status == 409:
            return self._set(CONFLICT, f"{self.prefix} is held by {reply.get('app', 'another app')}")
        if status is None:
            # The host left between our bind attempt and now. Try the port
            # again at once rather than a lease interval later.
            if self.server is None and time.monotonic() >= self._defer_bind_until and self._host():
                status, reply = self._post("register", self._lease())
                if status == 200:
                    self._note(reply)
                    self._set(HOST)
            return
        # Something answered, but not as a hub we can register with.
        self._set(BLOCKED, self._who_holds_port())

    def _note(self, reply: dict) -> None:
        self.idle_seconds = float(reply.get("idle_seconds") or 0.0)
        self.tls = bool(reply.get("tls"))

    def _lease(self) -> dict:
        return {
            "app": self.app, "prefix": self.prefix, "port": self.port,
            "exposure": self.exposure, "version": self.app_version, "pid": os.getpid(),
        }

    def _host(self) -> bool:
        server = HubServer(self._secret, self.hub_port, home=self.home, tls=self._tls_wanted)
        try:
            server.start()
        except OSError:
            return False
        self.server = server
        return True

    def _newer_than(self, reply: dict) -> bool:
        return parse_version(__version__) > parse_version(reply.get("version", ""))

    def _take_over(self) -> None:
        status, _ = self._post("yield", {"version": __version__})
        if status != 200:
            return self._set(MEMBER)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self._host():
                self._post("register", self._lease())
                return self._set(HOST)
            time.sleep(0.1)
        self._set(MEMBER)  # someone else got there; we will join them

    def _who_holds_port(self) -> str:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", self.hub_port, timeout=1.0)
            conn.request("GET", "/_hub/ping")
            body = conn.getresponse().read(4096)
            conn.close()
            ident = json.loads(body)
            if isinstance(ident, dict) and ident.get("service") == "densanon-hub":
                return "a Densanon hub with a different secret holds the port"
        except (OSError, ValueError):
            pass
        return f"port {self.hub_port} is used by another program"

    def _set(self, role: str, detail: str = "") -> None:
        changed = role != self.role
        self.role, self.detail = role, detail
        if changed:
            log.info("%s: hub role %s %s", self.app, role, detail)
            if self.on_change:
                try:
                    self.on_change(role)
                except Exception:
                    log.exception("hub on_change callback failed")

    # -- transport ---------------------------------------------------------

    def _post(self, action: str, payload: dict) -> tuple[int | None, dict]:
        """POST to the hub. Status None means nothing is listening."""
        data = json.dumps(payload).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.hub_port, timeout=3.0)
        try:
            conn.connect()
        except OSError:
            # Refused, or on Windows a timeout: a refused loopback connect
            # there is retried for a couple of seconds before it fails.
            conn.close()
            return None, {}
        try:
            conn.request("POST", f"/_hub/{action}", body=data, headers={
                "Content-Type": "application/json", SECRET_HEADER: self._secret,
            })
            resp = conn.getresponse()
            raw = resp.read(65536)
        except OSError as exc:
            return 0, {"error": str(exc)}
        finally:
            conn.close()
        try:
            reply = json.loads(raw)
        except ValueError:
            reply = {}
        return resp.status, reply if isinstance(reply, dict) else {}
