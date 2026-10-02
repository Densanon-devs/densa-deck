"""Where the hub lives: its port, its state directory, and the shared secret."""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

# One port for every Densanon app. Chosen clear of the ports the products
# already use (Densa Deck 8791/8792, DensAssistant 8777, DensaBooks 18234) and
# of the 8000 that half the dev servers default to.
DEFAULT_PORT = 8770

HUB_PROTOCOL = 1

# A member renews its lease this often, and the host forgets a route that has
# gone this long without one. The interval is also how fast a surviving app
# notices the host has gone, so it bounds the gap during a handoff.
LEASE_INTERVAL = 2.0
LEASE_TTL = 6.0


def hub_port() -> int:
    raw = os.environ.get("DENSANON_HUB_PORT", "")
    return int(raw) if raw.isdigit() else DEFAULT_PORT


def hub_home() -> Path:
    raw = os.environ.get("DENSANON_HUB_HOME")
    return Path(raw) if raw else Path.home() / ".densanon"


def load_secret(home: Path | None = None) -> str:
    """The secret every member uses to register, created by whoever needs it first.

    It guards the control routes, so only processes that can read this user's
    files can claim a route. That is the same trust boundary each product
    already relies on for its own pairing file.
    """
    home = home or hub_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / "hub-secret"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Empty can mean another app created it a moment ago and is still
        # writing. Wait for that rather than racing it with a second value.
        for _ in range(20):
            value = path.read_text(encoding="ascii").strip()
            if value:
                return value
            time.sleep(0.05)
        # Still empty: the creator crashed mid-write. Replace it.
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    value = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(value)
    return value
