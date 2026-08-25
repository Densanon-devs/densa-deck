"""Phone-as-scanner bridge, reached over Tailscale.

    phone browser --HTTPS over tailnet--> 100.x.y.z:8791 (this server)
                                                |
                                       scoped scan surface
                                                |
                                         AppApi -> collection

The desktop stays the brain. The phone is a camera and a screen. Every piece
of identification, pricing and storage logic is the same code the desktop
Scan tab already uses - this module adds no card knowledge whatsoever.

## Where it listens

Loopback plus this machine's tailnet address, and nothing else. Not 0.0.0.0,
which would also answer on whatever cafe Wi-Fi the laptop is on. A 100.64/10
address is only routable to devices already authenticated onto the tailnet.

## Two different things called HTTPS

  * **A CA/Tailscale certificate** publishes this machine's name to the public
    Certificate Transparency log, permanently. Declined, and stays declined.
    `tailscale serve` is still detected if someone has set it up, but nothing
    here asks for it.
  * **A self-signed certificate**, generated on this machine into
    `~/.densa-deck/certs/` (see `phone_tls.py`). No CA, no CT log, nothing
    leaves the box, `remove_cert()` erases it.

The second is used, and it is not cosmetic: `getUserMedia` is *absent*
outside a secure context, so without it the phone cannot open a viewfinder at
all and "scanning" degrades to uploading one still at a time. With it the
phone scans continuously - hold a card up, feel the buzz, move on.

The cost is a one-time browser interstitial per device, because nothing
vouches for the certificate but us. If `cryptography` is unavailable the
bridge falls back to plain HTTP and the phone page says why.

## Layers, because Tailscale alone isn't authorisation

Being on the tailnet proves *which device* you are, not that you meant to
mutate someone's inventory. So:

  * a **pairing token** is minted per session and required on every request;
  * the surface is **scoped** - scanning, browsing, decks, the analyst and
    sync. Written out one route at a time rather than forwarding method
    names onto AppApi, because that would put deck deletion, licence
    handling and 74 MB downloads one typo away from a phone on a network.
    No deletion of decks or collections, no licence, no fee model, no
    catalogue downloads. The full AppApi is never reachable from the phone;
  * it is **off by default** and stops when the desktop app closes.
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Always bound, for the desktop's own use.
BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8791

# The native companion talks here, in the clear. See the note on TLS below:
# the certificate exists for a browser API, and Android refuses a self-signed
# one with no way to override it from JavaScript.
COMPANION_PORT = 8792

# Tailscale hands out addresses in 100.64.0.0/10 (CGNAT). A listener on one of
# those is reachable *only* by devices already authenticated onto the tailnet
# — which is what makes plain HTTP acceptable here and what distinguishes this
# from binding 0.0.0.0, which would also answer on café Wi-Fi.
TAILNET_CGNAT_PREFIX = "100."


def is_tailnet_address(ip: str) -> bool:
    """True for a Tailscale 100.64/10 address."""
    parts = (ip or "").split(".")
    if len(parts) != 4 or parts[0] != "100":
        return False
    try:
        return 64 <= int(parts[1]) <= 127
    except ValueError:
        return False



# Private ranges, per RFC 1918. A LAN listener binds one of these explicitly
# and nothing else — never 0.0.0.0, which would also answer on whatever
# untrusted Wi-Fi this machine joins next, and never a routable address.
def is_private_lan_address(ip: str) -> bool:
    """True for 10/8, 172.16/12 and 192.168/16, and nothing else."""
    parts = (ip or "").split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if a == 10:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    return a == 192 and b == 168


def lan_address() -> str:
    """This machine's current private LAN address, or "".

    Found by asking the OS which local address it would use to reach the
    outside world — that is the interface with the default route, which is the
    one a phone on the same Wi-Fi can reach. No packet is actually sent.
    """
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.settimeout(0.4)
        probe.connect(("8.8.8.8", 80))   # UDP connect sends nothing
        found = probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()
    return found if is_private_lan_address(found) else ""


STATIC_DIR = Path(__file__).parent / "static" / "phone"

# Where the pairing token lives between runs. Beside the collection database,
# in the per-user directory this app already keeps its licence and settings in.
TOKEN_FILE = Path.home() / ".densa-deck" / "phone-pairing.json"


def _token_path() -> Path:
    """Overridable for tests, which must never touch the real pairing."""
    import os
    override = os.environ.get("DENSA_PHONE_TOKEN_FILE")
    return Path(override) if override else TOKEN_FILE


def load_or_create_token() -> str:
    """The pairing token, created once and reused forever after.

    Persisting it is what lets a phone stay paired: the URL bookmarked on the
    phone keeps working across desktop restarts, so scanning at a shop needs
    nothing but the tailnet. A per-run token forced a trip back to the desktop
    after every restart, which is precisely when you are not at the desktop.

    The tailnet is the network perimeter here — only enrolled devices can
    reach the port at all — and this token is the authorisation floor beneath
    it, so that reaching the port is not the same as being allowed in.
    """
    path = _token_path()
    if path.exists():
        # A file that EXISTS but will not parse is not permission to mint a
        # new pairing. It could be a partial read while another process is
        # writing, or a momentarily locked file — and rotating on that
        # strands every paired device with no way back except walking to the
        # desktop, which is the precise failure this whole design avoids.
        # Minting only happens when there is genuinely nothing there.
        for attempt in range(3):
            try:
                stored = json.loads(path.read_text(encoding="utf-8")).get("token", "")
                if isinstance(stored, str) and len(stored) >= 12:
                    return stored
                break          # present, parsed, and genuinely empty
            except (OSError, ValueError):
                if attempt == 2:
                    # Genuinely unreadable, not a passing race. Keep the file
                    # as evidence and mint a new pairing so the desktop still
                    # works — but leave a marker, because every paired device
                    # has just been locked out and the user has to be told
                    # rather than discovering it when their phone fails.
                    try:
                        path.replace(path.with_suffix(".corrupt.json"))
                    except OSError:
                        pass
                    fresh = rotate_token()
                    _mark_pairing_reset(path)
                    return fresh
                import time
                time.sleep(0.1)
    return rotate_token()


# Set when an unreadable pairing file forced a new token. The desktop reports
# it so "your phone stopped working" is explained rather than mysterious.
_PAIRING_RESET: dict = {"happened": False, "previous_file": ""}


def _mark_pairing_reset(path) -> None:
    _PAIRING_RESET["happened"] = True
    _PAIRING_RESET["previous_file"] = str(path.with_suffix(".corrupt.json"))


def pairing_was_reset() -> dict:
    """Whether an unreadable pairing file forced every device to re-pair."""
    return dict(_PAIRING_RESET)


def rotate_token() -> str:
    """Mint a new pairing token, invalidating every URL already handed out."""
    token = secrets.token_urlsafe(24)
    path = _token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"token": token}), encoding="utf-8")
        _restrict_permissions(path)
    except OSError:
        # An unwritable home directory must not break scanning; the token just
        # reverts to lasting only as long as the process.
        pass
    return token


def _restrict_permissions(path: Path) -> None:
    """Keep the token readable only by this user.

    A bearer token in a world-readable file would let any other account on the
    machine drive the scanner. chmod covers POSIX; on Windows the per-user
    profile directory already carries an ACL that excludes other users.
    """
    try:
        import os
        import stat
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

# Uploaded frames are phone-camera JPEGs. A few MB is generous; anything past
# this is not a photo of a card.
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


class PhoneBridge:
    """Owns the loopback server and the pairing token."""

    def __init__(self, api, port: int = DEFAULT_PORT, use_tls: bool = True,
                 companion_port: int = COMPANION_PORT, bind_lan: bool = True):
        self._api = api
        self.port = int(port)
        self.companion_port = int(companion_port)
        self.companion_hosts: list[str] = []
        # Serving the local network as well as the tailnet. On by default
        # because "same Wi-Fi" is the common case at home and the fast one.
        self.bind_lan = bool(bind_lan)
        self.lan_host = ""
        # TLS on by default. Without it the phone has no camera API at all,
        # which reduces "scanning" to uploading one photo at a time.
        self.use_tls = bool(use_tls)
        self.ssl_context = None
        self.cert_paths = None
        self.token = ""
        # One server per bound address: loopback always, plus this machine's
        # tailnet address when there is one. Deliberately NOT 0.0.0.0 — that
        # would also answer on whatever untrusted Wi-Fi we happen to be on.
        self._servers: list[ThreadingHTTPServer] = []
        self._threads: list[threading.Thread] = []
        self.bound_hosts: list[str] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------ lifecycle

    def is_running(self) -> bool:
        return bool(self._threads) and any(t.is_alive() for t in self._threads)

    def start(self) -> dict:
        with self._lock:
            if self.is_running():
                return {"ok": True, "already_running": True, **self.status()}

            # The token OUTLIVES the process. A fresh one every start meant
            # re-pairing from the desktop after every restart, which quietly
            # destroys the point of the feature: the phone is meant to be
            # usable away from the desk — at a shop, over the tailnet — and
            # you cannot walk to the desktop to re-pair from there.
            #
            # Revoking is now an explicit act (`unpair`) rather than a side
            # effect of stopping the bridge, because "I closed the app" and "I
            # want that phone locked out" are different intentions.
            self.token = load_or_create_token()
            bridge = self

            class Handler(_PhoneHandler):
                pass

            Handler.bridge = bridge

            hosts = [BIND_HOST]
            dns_name = ""
            try:
                ts = tailscale_status()
                tailnet_ip = ts.get("ipv4", "")
                dns_name = ts.get("dns_name", "")
            except Exception:
                tailnet_ip = ""
            # Verify the address really is a tailnet one before binding it.
            # Trusting the field blindly would risk putting the scan surface
            # on a routable interface.
            if tailnet_ip and is_tailnet_address(tailnet_ip):
                hosts.append(tailnet_ip)

            # The local address, so a phone on the same Wi-Fi connects
            # directly: faster than the tunnel, and nothing leaves the house.
            # A SPECIFIC private address, never 0.0.0.0 — that distinction is
            # the entire safety argument, because 0.0.0.0 would also answer on
            # whatever untrusted network this machine joins next.
            self.lan_host = lan_address() if self.bind_lan else ""
            if self.lan_host and self.lan_host not in hosts:
                hosts.append(self.lan_host)

            # TLS from a locally-generated certificate. This is what gives the
            # phone `isSecureContext`, and therefore a live camera instead of
            # one-still-at-a-time photo upload. Nothing is published anywhere:
            # no CA, no Certificate Transparency entry — see phone_tls.py.
            self.ssl_context = None
            self.cert_paths = None
            if self.use_tls:
                from densa_deck.app import phone_tls
                names = list(hosts)
                if dns_name:
                    names.append(dns_name)
                paths = phone_tls.ensure_cert(names)
                if paths:
                    ctx = phone_tls.make_ssl_context(*paths)
                    if ctx is not None:
                        self.ssl_context = ctx
                        self.cert_paths = paths

            for host in hosts:
                try:
                    server = ThreadingHTTPServer((host, self.port), Handler)
                    if self.ssl_context is not None:
                        server.socket = self.ssl_context.wrap_socket(
                            server.socket, server_side=True)
                except OSError as exc:
                    if host == BIND_HOST:
                        self._shutdown_all()
                        return {
                            "ok": False,
                            "error": f"Could not bind {host}:{self.port} - {exc}",
                            "error_type": "PortUnavailable",
                        }
                    # The tailnet address can disappear mid-session if
                    # Tailscale restarts. That costs phone access, not the app.
                    continue
                server.daemon_threads = True
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self._servers.append(server)
                self._threads.append(thread)
                self.bound_hosts.append(host)

            # The companion's port, always in the clear. Same handler, same
            # token, same tailnet-only binding — only the transport differs,
            # because a native client cannot be made to accept a self-signed
            # certificate and does not need one.
            self.companion_hosts = []
            for host in hosts:
                try:
                    plain = ThreadingHTTPServer((host, self.companion_port),
                                                Handler)
                except OSError:
                    # Losing this costs the app, not the desktop or the web
                    # page, so it is never fatal.
                    continue
                plain.daemon_threads = True
                thread = threading.Thread(target=plain.serve_forever,
                                          daemon=True)
                thread.start()
                self._servers.append(plain)
                self._threads.append(thread)
                self.companion_hosts.append(host)

            return {"ok": True, **self.status()}

    def stop(self) -> dict:
        """Stop serving. The pairing survives — see `unpair` to revoke it.

        Closing the app and locking a phone out are different intentions, and
        conflating them meant every restart demanded a re-pair from the
        desktop. The listener is gone either way, so nothing is reachable
        while stopped.
        """
        with self._lock:
            self._shutdown_all()
            self.token = ""
            return {"ok": True, "running": False}

    def unpair(self) -> dict:
        """Revoke the pairing: every URL already on a phone stops working."""
        with self._lock:
            was_running = self.is_running()
            self._shutdown_all()
            self.token = ""
            rotate_token()
            return {"ok": True, "running": False, "was_running": was_running,
                    "unpaired": True}

    def _shutdown_all(self) -> None:
        self.ssl_context = None
        self.cert_paths = None
        for server in self._servers:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        self._servers = []
        self._threads = []
        self.bound_hosts = []
        self.companion_hosts = []
        self.lan_host = ""

    def status(self) -> dict:
        tailnet_hosts = [h for h in self.bound_hosts if h != BIND_HOST]
        return {
            "running": self.is_running(),
            "port": self.port,
            "bind_host": BIND_HOST,
            "bound_hosts": list(self.bound_hosts),
            "tailnet_host": tailnet_hosts[0] if tailnet_hosts else "",
            # Whether a phone can actually reach this right now. False means
            # the URL we hand out would just hang.
            "reachable_from_phone": bool(tailnet_hosts),
            "tls": self.ssl_context is not None,
            "companion_port": self.companion_port,
            "companion_hosts": list(self.companion_hosts),
            "lan_host": self.lan_host,
            "scheme": "https" if self.ssl_context is not None else "http",
            "token": self.token,
            "local_url": (f"http://{BIND_HOST}:{self.port}/scan?t={self.token}"
                          if self.token else ""),
        }

    # -------------------------------------------------------------- routing

    def check_token(self, supplied: str) -> bool:
        # compare_digest so a wrong token can't be narrowed by timing.
        return bool(self.token) and secrets.compare_digest(supplied or "", self.token)

    def handle_api(self, route: str, payload: dict) -> dict:
        """The entire phone-reachable surface.

        Deliberately an explicit allow-list rather than dispatch-by-name onto
        AppApi. A phone on the tailnet must not be one typo away from
        `delete_deck` or `printings_remove`.
        """
        api = self._api
        if route == "identify":
            return _unwrap(api.scan_identify(payload.get("text", ""),
                                             payload.get("name_hint", "")))
        if route == "commit":
            return _unwrap(api.scan_commit(
                payload.get("printing_id", ""),
                payload.get("card_name", ""),
                payload.get("finish", "nonfoil"),
                payload.get("condition", "NM"),
                payload.get("location", ""),
                payload.get("confidence", "phone"),
                payload.get("collection_id"),
            ))
        if route == "adjust":
            # Bounded on the API side to copies this session added, so this
            # cannot be used to delete arbitrary collection rows.
            return _unwrap(api.scan_adjust(
                payload.get("printing_id", ""),
                int(payload.get("delta", 1) or 0),
                payload.get("finish", "nonfoil"),
                payload.get("condition", "NM"),
                payload.get("location", ""),
                payload.get("card_name", ""),
                payload.get("collection_id"),
            ))
        if route == "skip":
            return _unwrap(api.scan_skip("unknown"))
        # --- browsing the collection ------------------------------------
        # Read-only. `collection_id` omitted means the master collection.
        if route == "collection/list":
            return _unwrap(api.list_collection(payload.get("query") or {}))
        if route == "collection/value":
            # capture=False: a phone browsing must not write a price snapshot,
            # which is a once-a-day fact the desktop owns.
            return _unwrap(api.get_collection_value(capture=False))
        if route == "collection/sets":
            return _unwrap(api.get_collection_sets(
                int(payload.get("limit", 100) or 100)))
        if route == "collection/status":
            return _unwrap(api.get_collection_status())
        if route == "collection/move":
            return _unwrap(api.move_to_collection(
                int(payload.get("item_id", 0) or 0),
                int(payload.get("collection_id", 0) or 0),
                payload.get("quantity")))
        if route == "collection/set-quantity":
            return _unwrap(api.set_collection_item_quantity(
                int(payload.get("item_id", 0) or 0),
                int(payload.get("quantity", 0) or 0)))

        # --- searching every card, not just the owned ones ----------------
        # The catalogue is 34k oracle cards and 107k printings, which is not
        # going on a phone. So this is one of the operations that genuinely
        # needs the desktop, and it fails honestly when the desktop is away
        # rather than pretending the collection is the world.
        #
        # `ownership` is opt-in: omitted, the search covers everything,
        # because "what could I put in this deck" is a different question
        # from "what do I have".
        if route == "cards/search":
            return _unwrap(api.search_cards(payload.get("query") or {}))

        # --- decks --------------------------------------------------------
        if route == "decks/list":
            # Named key rather than a bare array. Every response from this
            # bridge is a JSON object, so a typed client can model one
            # envelope shape instead of asking, per route, whether it is
            # about to receive a list.
            return {"decks": _unwrap(api.list_saved_decks())}
        if route == "decks/get":
            return _unwrap(api.get_deck_latest(payload.get("deck_id", "")))
        if route == "decks/history":
            return {"versions": _unwrap(api.get_deck_history(
                payload.get("deck_id", "")))}
        if route == "decks/save":
            # Argument order is (deck_id, name, decklist_text) — getting it
            # wrong saves a deck whose name is its decklist, which the test
            # for this route caught.
            return _unwrap(api.save_deck_version(
                payload.get("deck_id", ""),
                payload.get("name", ""),
                payload.get("decklist_text", ""),
                payload.get("format"),
                payload.get("notes", "")))
        if route == "decks/ownership":
            return _unwrap(api.get_deck_ownership(
                payload.get("decklist_text", "")))
        if route == "decks/value":
            return _unwrap(api.get_deck_collection_value(
                payload.get("decklist_text", "")))

        # --- analyst: the PC does the thinking ----------------------------
        # These are why the desktop is the brain. A phone cannot hold the
        # catalogue, the combo database or a model, and does not need to.
        if route == "analyst/analyze":
            return _unwrap(api.analyze_deck(
                payload.get("decklist_text", ""),
                payload.get("format"),
                payload.get("name", "Unnamed Deck")))
        if route == "analyst/combos":
            return _unwrap(api.detect_combos_for_deck(
                payload.get("decklist_text", "")))
        if route == "analyst/bracket":
            return _unwrap(api.assess_bracket_fit(
                payload.get("decklist_text", ""),
                payload.get("target_bracket")))
        if route == "analyst/rule0":
            return _unwrap(api.build_rule0_worksheet(
                payload.get("decklist_text", "")))
        if route == "analyst/explain":
            return _unwrap(api.explain_card_in_deck(
                payload.get("card_name", ""),
                payload.get("decklist_text", "")))

        # --- sync -------------------------------------------------------
        # Reachable by a paired companion only, like everything else here.
        # These carry data both ways, so they sit under the same token and
        # the same tailnet-only listener as the scanner.
        if route == "sync/hello":
            return _unwrap(api.sync_hello(payload.get("peer", "")))
        if route == "sync/pull":
            return _unwrap(api.sync_pull(
                int(payload.get("since", 0) or 0),
                int(payload.get("limit", 500) or 500),
                payload.get("peer", "")))
        if route == "sync/push":
            return _unwrap(api.sync_push(
                payload.get("events") or [],
                payload.get("peer", ""),
                payload.get("cursor")))
        if route == "sync/status":
            return _unwrap(api.sync_status())

        if route == "collections":
            return _unwrap(api.list_collections())
        if route == "new-collection":
            # Creating a grouping mid-run is safe and additive. Renaming and
            # deleting are deliberately NOT reachable from the phone: those
            # can move or destroy cards, and belong where the whole
            # collection is visible.
            return _unwrap(api.create_collection(payload.get("name", "")))
        if route == "session":
            return _unwrap(api.get_scan_session())
        if route == "appraise":
            return _unwrap(api.appraise_scan_session(None))
        if route == "capabilities":
            return _unwrap(api.get_scan_capabilities())
        if route == "capture":
            return self._handle_capture(payload)
        return {"ok": False, "error": f"unknown route '{route}'"}

    def _handle_capture(self, payload: dict) -> dict:
        """A phone-camera frame: detect the card, OCR its corner, identify.

        The phone can't OCR (no browser API does it reliably), so it ships
        pixels and the desktop does the work — which means this path needs an
        OCR engine installed on the desktop and says so plainly when there
        isn't one, rather than silently returning nothing.
        """
        import base64
        import tempfile

        data_url = payload.get("image") or ""
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        if not data_url:
            return {"ok": False, "error": "No image received."}

        try:
            raw = base64.b64decode(data_url)
        except Exception:
            return {"ok": False, "error": "Image could not be decoded."}
        if len(raw) > MAX_UPLOAD_BYTES:
            return {"ok": False, "error": "Image too large."}

        from densa_deck.collection.capture import (
            detect_card,
            opencv_available,
            read_regions,
            save_image,
        )
        from densa_deck.collection.scan_backends import best_ocr_backend

        if not opencv_available():
            return {"ok": False,
                    "error": "Photo scanning needs OpenCV on the desktop "
                             "(pip install opencv-python-headless). Typing the "
                             "card's corner text works without it.",
                    "error_type": "OpenCvMissing"}

        backend = best_ocr_backend()
        if backend.name == "manual":
            return {"ok": False,
                    "error": "Photo scanning needs an OCR engine on the desktop "
                             "(pip install winrt-Windows.Media.Ocr). Typing the "
                             "card's corner text works without it.",
                    "error_type": "OcrMissing"}

        import cv2
        import numpy as np
        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return {"ok": False, "error": "Image could not be read."}

        detected = detect_card(frame)
        if not detected.found and _debug_frames_enabled():
            _save_debug_frame(frame, detected.reason)

        # Finding the card outline gives the best crops, but failing to find
        # it must NOT be a dead end. A photo of a card still has readable text
        # whether or not we located its corners, so fall back to OCR-ing the
        # whole frame. "No card shape" as a terminal error meant the feature
        # simply stopped working the moment detection had an off day.
        text = ""
        if detected.found:
            # Both orientations: a card held upside down puts the footer
            # top-right, and nothing in the outline says which way up it was.
            text = read_regions((
                ("title", detected.title),
                ("footer", detected.footer),
                ("title_flipped", detected.title_flipped),
                ("footer_flipped", detected.footer_flipped),
            ), backend)

        result = _unwrap(self._api.scan_identify(text, "")) if text else None

        # Reading the whole frame is the fallback, and it is NOT reserved for
        # the case where no outline was found. Detection can succeed on the
        # wrong region — a colour-based pass can lock onto the art box instead
        # of the card — and then the crops land on the artist credit and read
        # nothing useful. Measured on real photos, two cards that identified
        # exactly from the whole frame regressed to unknown the moment
        # detection started "succeeding" on them. So whenever the crops fail
        # to produce something auto-addable, read the frame as well and let
        # the identifier judge the combined text.
        if not (isinstance(result, dict) and result.get("auto_addable")):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "frame.png"
                # A full frame is already large; upscaling it 3x helps nothing
                # and costs seconds, so this reads it as-is.
                whole = ((backend.read_text(path) or "").strip()
                         if save_image(frame, path) else "")
            if whole:
                text = f"{text}\n{whole}".strip() if text else whole
                result = _unwrap(self._api.scan_identify(text, ""))

        if not text:
            return {
                "ok": False,
                "error": ("Couldn't read any text on that card. Try filling more "
                          "of the frame, and avoid glare on the bottom-left "
                          "corner."),
                "error_type": "NoTextRead",
            }
        if isinstance(result, dict) and result.get("ok") is not False:
            cap = result.setdefault("capture", {})
            cap["text"] = text
            # Report whether the outline was found. A whole-frame read still
            # works but is less reliable, and the UI should be able to say so
            # rather than leaving a worse result unexplained.
            cap["card_detected"] = detected.found
            cap["detect_reason"] = "" if detected.found else detected.reason
        return result


def _debug_frames_enabled() -> bool:
    """Whether to keep frames the detector failed on.

    Off unless DENSA_SCAN_DEBUG=1. These are photographs of whatever the
    camera was pointed at, so writing them to disk is never done quietly.
    """
    import os
    return os.environ.get("DENSA_SCAN_DEBUG") == "1"


def _save_debug_frame(frame, reason: str) -> None:
    """Write a missed frame so detection can be fixed from evidence.

    Capped at 20 files - a continuous scan pointed at a desk would otherwise
    fill the disk with near-identical images.
    """
    try:
        from datetime import datetime

        from densa_deck.collection.capture import save_image
        out = Path.home() / ".densa-deck" / "scan-debug"
        out.mkdir(parents=True, exist_ok=True)
        if len(list(out.glob("miss-*.png"))) >= 20:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        safe = "".join(c if c.isalnum() else "-" for c in reason)[:40]
        save_image(frame, out / f"miss-{stamp}-{safe}.png")
    except Exception:
        pass


def _unwrap(envelope):
    """Flatten the AppApi @_safe envelope for the phone's JSON client.

    Lists are unwrapped as well as dicts. They used not to be, which meant a
    route returning a list (saved decks, for one) arrived shaped differently
    from every other route — `{"ok": true, "data": [...]}` instead of the bare
    payload — and a client had to know which was which. Errors stay
    recognisable either way: they are always a dict carrying `ok: false`.
    """
    if isinstance(envelope, dict) and "ok" in envelope:
        if not envelope["ok"]:
            return envelope
        data = envelope.get("data")
        return data if isinstance(data, (dict, list)) else envelope
    return envelope


class _PhoneHandler(BaseHTTPRequestHandler):
    bridge: PhoneBridge = None  # set per-server in PhoneBridge.start

    server_version = "DensaDeckPhone/1.0"

    def log_message(self, *args):
        # The desktop app has no console; stderr noise helps nobody.
        pass

    # ---------------------------------------------------------------- utils

    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # This page must never be embedded or referrer-leak its token.
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload), "application/json")

    def _token_from_request(self) -> str:
        header = self.headers.get("X-Densa-Token", "")
        if header:
            return header
        from urllib.parse import parse_qs, urlparse
        return (parse_qs(urlparse(self.path).query).get("t") or [""])[0]

    def _authorised(self) -> bool:
        return self.bridge is not None and self.bridge.check_token(
            self._token_from_request())

    # ----------------------------------------------------------------- GET

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        path = parsed.path

        # Reachability probe. Deliberately open — a phone has to be able to
        # ask "are you there" before it can prove anything — but the two
        # fields that describe this machine are only handed out to a caller
        # holding the pairing token.
        if path == "/health":
            body = {"ok": True}
            # The caller's source address AS THIS SERVER SAW IT. The only
            # honest answer to "which path did we take": dialling a LAN URL
            # and arriving from CGNAT means the packets took the tunnel.
            body["peer"] = self.client_address[0] if self.client_address else ""
            token = (parse_qs(parsed.query).get("token") or [""])[0]
            if self.bridge is not None and self.bridge.check_token(token):
                # This machine's CURRENT LAN address, so a phone whose stored
                # one has gone stale can find its way home without re-pairing.
                body["lan"] = self.bridge.lan_host
                body["device"] = getattr(self.bridge, "device_name", "")
            self._json(200, body)
            return

        if path in ("/", "/scan"):
            if not self._authorised():
                self._send(403, _DENIED_HTML, "text/html; charset=utf-8")
                return
            page = STATIC_DIR / "scan.html"
            if not page.exists():
                self._send(500, "scan.html missing from the bundle", "text/plain")
                return
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            return

        if path == "/healthz":
            # Unauthenticated on purpose: lets the desktop confirm the port is
            # live without handing the token to anything.
            self._json(200, {"ok": True, "service": "densa-deck-phone"})
            return

        if path.startswith("/static/"):
            name = path[len("/static/"):]
            # Static files carry no secrets, but path traversal out of the
            # bundle would.
            if "/" in name or "\\" in name or ".." in name:
                self._json(404, {"ok": False, "error": "not found"})
                return
            target = STATIC_DIR / name
            if not target.exists() or not target.is_file():
                self._json(404, {"ok": False, "error": "not found"})
                return
            ctype = {".css": "text/css", ".js": "application/javascript",
                     ".png": "image/png"}.get(target.suffix, "application/octet-stream")
            self._send(200, target.read_bytes(), ctype)
            return

        self._json(404, {"ok": False, "error": "not found"})

    # ---------------------------------------------------------------- POST

    def do_POST(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path

        if not path.startswith("/api/"):
            self._json(404, {"ok": False, "error": "not found"})
            return
        if not self._authorised():
            self._json(403, {"ok": False, "error": "Not paired. Reopen the link "
                                                   "from the desktop Settings tab."})
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_UPLOAD_BYTES:
            self._json(413, {"ok": False, "error": "Payload too large."})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json(400, {"ok": False, "error": "Malformed request."})
            return
        if not isinstance(payload, dict):
            self._json(400, {"ok": False, "error": "Malformed request."})
            return

        route = path[len("/api/"):]
        try:
            result = self.bridge.handle_api(route, payload)
        except Exception as exc:
            # The phone is across a network; a stack trace helps nobody there.
            self._json(200, {"ok": False, "error": str(exc)})
            return
        # Routes are expected to return a dict; anything else is wrapped so
        # the wire shape stays predictable rather than silently varying.
        self._json(200, result if isinstance(result, dict)
                   else {"ok": True, "data": result})


_DENIED_HTML = b"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui;background:#0f1117;color:#e4e6eb;padding:2rem;line-height:1.5}
a{color:#4a90e2}</style>
<h2>Not paired</h2>
<p>This link is missing its pairing code, or the desktop app has stopped sharing.</p>
<p>Open <b>Settings &rarr; Scan from your phone</b> on the desktop and scan the QR code again.</p>
"""


# ------------------------------------------------------------- tailscale glue


def tailscale_cli() -> str | None:
    """Locate the Tailscale CLI, or None.

    PATH is checked first but cannot be relied on: the Windows installer does
    not add Tailscale to PATH, so `which` misses it even while the tray app is
    connected and happily routing traffic. Probing the install locations is
    what stops us reporting "not installed" to someone staring at a working
    Tailscale icon. Same probe list the other projects in this workspace use.
    """
    import os
    import shutil
    found = shutil.which("tailscale")
    if found:
        return found
    candidates = [
        r"C:\Program Files\Tailscale\tailscale.exe",
        r"C:\Program Files (x86)\Tailscale\tailscale.exe",
        "/usr/bin/tailscale", "/usr/local/bin/tailscale",
        "/opt/homebrew/bin/tailscale",
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    ]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        # Per-user install — no admin rights needed, so it's common.
        candidates.insert(0, str(Path(local_appdata) / "Tailscale" / "tailscale.exe"))
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def _run_tailscale(cli: str, args: list[str], timeout: int = 10):
    """Shell the CLI without flashing a console window.

    Densa Deck is a windowed pywebview app. Without CREATE_NO_WINDOW every
    status poll pops a black console box on screen for a fraction of a
    second, which looks like the app is misbehaving.
    """
    import subprocess
    return subprocess.run(
        [cli, *args], capture_output=True, timeout=timeout, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def tailscale_status() -> dict:
    """This machine's tailnet identity, and whether the CLI is usable.

    Everything here is read-only; nothing is configured as a side effect of
    asking.
    """
    cli = tailscale_cli()
    if not cli:
        return {"installed": False,
                "detail": "Tailscale not found. Install it to reach the desktop "
                          "from your phone.",
                "url": "https://tailscale.com/download"}

    try:
        proc = _run_tailscale(cli, ["status", "--json"])
        data = json.loads(proc.stdout or "{}")
    except Exception as exc:
        return {"installed": True, "running": False, "detail": str(exc)}

    self_node = data.get("Self") or {}
    dns_name = (self_node.get("DNSName") or "").rstrip(".")
    # This machine's address ON the tailnet. Binding here (rather than
    # 0.0.0.0) is what lets a phone reach the bridge over plain HTTP without
    # any certificate: a 100.64/10 address is only routable to devices
    # already authenticated onto the tailnet.
    ipv4 = ""
    for addr in self_node.get("TailscaleIPs") or []:
        if ":" not in str(addr):
            ipv4 = str(addr)
            break
    peers = []
    for peer in (data.get("Peer") or {}).values():
        peers.append({
            "name": peer.get("HostName", ""),
            "os": peer.get("OS", ""),
            "online": bool(peer.get("Online")),
        })
    phones = [p for p in peers if p["os"] in ("android", "iOS") and p["online"]]

    # CertDomains is populated only once HTTPS certificates are enabled for
    # the tailnet. This is load-bearing, not cosmetic: `tailscale serve`
    # HANGS when it's empty, so telling someone to run it in that state hands
    # them a wedged terminal and no explanation. Check before advising.
    cert_domains = data.get("CertDomains") or []

    return {
        "installed": True,
        "running": bool(data.get("BackendState") == "Running"),
        "backend_state": data.get("BackendState", ""),
        "dns_name": dns_name,
        "ipv4": ipv4,
        "magicdns": bool((data.get("CurrentTailnet") or {}).get("MagicDNSEnabled")),
        "https_enabled": bool(cert_domains),
        "cert_domains": list(cert_domains),
        "https_admin_url": "https://login.tailscale.com/admin/dns",
        "peers": peers,
        "phones_online": phones,
    }


def serve_status() -> dict:
    """Whether `tailscale serve` is currently proxying anything."""
    cli = tailscale_cli()
    if not cli:
        return {"available": False}
    try:
        proc = _run_tailscale(cli, ["serve", "status", "--json"])
        config = json.loads(proc.stdout or "{}")
    except Exception:
        return {"available": True, "configured": False}
    return {"available": True, "configured": bool(config), "config": config}


def https_guidance(ts: dict, port: int) -> dict:
    """What to tell the user about getting a live camera on the phone.

    Three states, and conflating them is how people end up with a hung
    terminal:

      * HTTPS certs not enabled for the tailnet -> `tailscale serve` HANGS.
        The fix is a one-time toggle in the admin console, and that must be
        said BEFORE the command, not after they've wedged a shell.
      * certs enabled, serve not configured -> run the command.
      * serve configured -> nothing to do.

    The cost is stated rather than buried. Enabling HTTPS publishes this
    machine's name to the public Certificate Transparency log, permanently.
    That is a real and irreversible disclosure, and it buys exactly one
    thing: a live viewfinder. Typing and the OS camera app work regardless,
    so declining is a legitimate choice, not a broken setup.
    """
    if not ts.get("installed") or not ts.get("running"):
        return {"state": "tailscale_unavailable", "command": "", "blocking": True}

    if not ts.get("https_enabled"):
        return {
            "state": "https_not_enabled",
            "blocking": True,
            "command": "",
            "admin_url": ts.get("https_admin_url", ""),
            "headline": "Turn on HTTPS certificates for your tailnet first",
            "detail": (
                "Without it, `tailscale serve` will hang rather than fail. "
                "Enable HTTPS in the Tailscale admin console (DNS page), then "
                "come back here."
            ),
            "cost": (
                "Enabling it publishes this machine's name to the public "
                "Certificate Transparency log, permanently. Traffic over the "
                "tailnet is already encrypted by WireGuard — the certificate "
                "only lets the phone's browser verify that, which is what "
                "unlocks the live camera."
            ),
        }

    return {
        "state": "ready_to_serve",
        "blocking": False,
        "command": build_serve_command(port),
        "headline": "Publish this bridge over HTTPS",
        "detail": "Run this once on this machine, then reload the page on your phone.",
        "cost": "",
    }


def build_serve_command(port: int) -> str:
    """The exact command that publishes this bridge over HTTPS.

    Surfaced for the user to run rather than executed for them: `tailscale
    serve` changes machine-level network configuration and can require
    elevation, and provisioning a public certificate for their machine name
    is not a side effect an app should trigger on its own.
    """
    return f"tailscale serve --bg {port}"


def phone_url(dns_name: str, token: str) -> str:
    """HTTPS URL, valid ONLY when `tailscale serve` is actually running.

    Kept for that case, but never the default: `tailscale serve` is opt-in
    here, and handing a phone an https:// address with nothing listening on
    443 doesn't fail — it hangs. Prefer `pairing_url()`.
    """
    if not dns_name or not token:
        return ""
    return f"https://{dns_name}/scan?t={token}"


def pairing_url(bridge_status: dict, ts: dict, serve: dict, token: str) -> str:
    """The address the phone should actually open.

    Picks by what is genuinely listening, not by what would be nicest:

      * `tailscale serve` configured -> the HTTPS MagicDNS name it fronts.
      * otherwise -> plain HTTP straight to this machine's tailnet address,
        which the bridge binds directly.

    Getting this wrong is not a cosmetic bug. Pointing a phone at
    `https://<name>.ts.net` with no Serve running means dialling port 443
    where nothing listens: the browser sits there spinning rather than
    reporting an error, which is exactly the "we are hanging" symptom.

    Plain HTTP is not a downgrade in confidentiality — a 100.64/10 address is
    only routable inside the tailnet and WireGuard already encrypts the hop.
    The certificate would only buy `isSecureContext`, i.e. a live camera
    viewfinder, at the cost of publishing this machine's name to the public
    CT log forever.
    """
    if not token:
        return ""
    if serve.get("configured") and ts.get("dns_name"):
        return phone_url(ts["dns_name"], token)
    host = bridge_status.get("tailnet_host") or ""
    if host:
        scheme = bridge_status.get("scheme", "http")
        port = bridge_status.get("port", DEFAULT_PORT)
        url = f"{scheme}://{host}:{port}/scan?t={token}"
        # The native app cannot use the TLS port: Android refuses a
        # self-signed certificate and offers no way to override it from
        # JavaScript. Carrying the plain endpoint in the SAME link means one
        # QR code serves both the web page and the app — the browser ignores
        # the extra parameter, and the app does not have to guess at port
        # arithmetic to find its way home.
        companion_port = bridge_status.get("companion_port")
        if companion_port and bridge_status.get("companion_hosts"):
            url += f"&api=http://{host}:{companion_port}"
            # The local address as well, so the phone can take the fast path
            # when it is on the same Wi-Fi. It is a starting point rather than
            # a fact: a DHCP lease moves, and the phone re-learns the current
            # one from /health on any successful contact.
            lan = bridge_status.get("lan_host") or ""
            if lan:
                url += f"&lan=http://{lan}:{companion_port}"
        return url
    return ""


def qr_matrix(data: str) -> list[list[bool]] | None:
    """QR code as a boolean matrix, or None when unavailable.

    Returned as a matrix rather than an image so the caller can render it as
    HTML/SVG — no Pillow, no temp files, and it survives being sent over the
    pywebview bridge as plain JSON. `qrcode` is optional; without it the UI
    falls back to showing the URL.
    """
    try:
        import qrcode
    except Exception:
        return None
    try:
        qr = qrcode.QRCode(border=1, box_size=1)
        qr.add_data(data)
        qr.make(fit=True)
        return [[bool(cell) for cell in row] for row in qr.get_matrix()]
    except Exception:
        return None
