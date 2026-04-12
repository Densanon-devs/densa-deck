"""Pro license management — hash-based, no server needed.

Matches the same pattern as D-Brief: keys are deterministic hashes of the
Stripe session_id + a salt. The app validates by re-hashing the segments
and checking the checksum (no need to know the original session_id).

The hash function is intentionally identical to the JavaScript version
on densanon.com/mtg-engine-success.html so keys generated in the browser
validate correctly in the desktop app.

Format: MTG-XXXX-XXXX-XXXX
  - p1: first 4 chars of hashKey(session_id)
  - p2: next 4 chars of hashKey(session_id)
  - p3: first 4 chars of hashKey(p1-p2) — checksum

Master key bypasses all checks (developer access).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Master key — always unlocks Pro. Only the developer knows this.
MASTER_KEY = "densanon-mtg-engine-2026"

# Salt for hashing license keys (must match the JS in mtg-engine-success.html)
LICENSE_SALT = "MTG-Deck-Engine-pro-v1"

LICENSE_PATH = Path.home() / ".mtg-deck-engine" / "license.key"

_KEY_PATTERN = re.compile(r"^MTG-([A-Z0-9]{4})-([A-Z0-9]{4})-([A-Z0-9]{4})$")


@dataclass
class License:
    """A parsed license key."""

    key: str
    valid: bool = False
    is_master: bool = False
    activated_at: str = ""
    error: str = ""

    def grants_pro(self) -> bool:
        return self.valid


def _hash_key(input_str: str) -> str:
    """Simple deterministic hash, matching the JavaScript version exactly.

    Mirrors:
        let hash = 0;
        for (let i = 0; i < input.length; i++) {
            const char = input.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash;  // 32-bit
        }
        return Math.abs(hash).toString(36);

    Python's int doesn't auto-truncate, so we mask to 32 bits and handle
    sign exactly the way JavaScript does.
    """
    full_input = f"{LICENSE_SALT}:{input_str.strip().lower()}"
    h = 0
    for ch in full_input:
        c = ord(ch)
        h = ((h << 5) - h) + c
        # Convert to 32-bit signed int (matching JavaScript's `hash & hash`)
        h = h & 0xFFFFFFFF
        if h & 0x80000000:
            h = h - 0x100000000
    # Math.abs + base 36
    return _to_base36(abs(h))


def _to_base36(n: int) -> str:
    """Convert non-negative int to base 36 string (matching JS toString(36))."""
    if n == 0:
        return "0"
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while n > 0:
        result = chars[n % 36] + result
        n //= 36
    return result


def generate_license_key(seed: str) -> str:
    """Generate a deterministic license key from a seed (e.g. Stripe session_id).

    This is used by the success page in the browser. Replicated here for
    testing and admin use. Customers receive the generated key directly.
    """
    h = _hash_key(seed)
    padded = h.ljust(8, "0")
    p1 = padded[:4].upper()
    p2 = padded[4:8].upper()
    check_input = f"{p1}-{p2}"
    check_hash = _hash_key(check_input)
    p3 = check_hash.ljust(4, "0")[:4].upper()
    return f"MTG-{p1}-{p2}-{p3}"


def validate_key(key: str) -> bool:
    """Validate a license key by checksum verification.

    Accepts:
      - Master key (developer access)
      - Properly formatted MTG-XXXX-XXXX-XXXX with valid checksum
    """
    cleaned = key.strip()

    # Master key bypass
    if cleaned == MASTER_KEY:
        return True

    # Format check
    match = _KEY_PATTERN.match(cleaned.upper())
    if not match:
        return False

    p1, p2, p3 = match.group(1), match.group(2), match.group(3)

    # Recompute checksum
    check_input = f"{p1}-{p2}"
    check_hash = _hash_key(check_input)
    expected_p3 = check_hash.ljust(4, "0")[:4].upper()

    return p3 == expected_p3


def verify_license_key(key: str) -> License:
    """Parse and verify a license key. Returns License object."""
    license = License(key=key.strip())

    if not key or not key.strip():
        license.error = "Empty license key"
        return license

    if validate_key(key):
        license.valid = True
        license.is_master = (key.strip() == MASTER_KEY)
    else:
        license.error = "Invalid license key — please check for typos"

    return license


def save_license(key: str) -> License:
    """Validate and save a license key to the user's config directory."""
    license = verify_license_key(key)
    if not license.valid:
        return license

    LICENSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "key": key.strip(),
        "activated_at": datetime.now().isoformat(),
    }
    LICENSE_PATH.write_text(json.dumps(data), encoding="utf-8")
    license.activated_at = data["activated_at"]
    return license


def load_saved_license() -> License | None:
    """Load and verify the saved license, if any."""
    if not LICENSE_PATH.exists():
        return None
    try:
        content = LICENSE_PATH.read_text(encoding="utf-8").strip()
        if not content:
            return None
        # Try JSON first (new format), fall back to raw key (old format)
        try:
            data = json.loads(content)
            key = data.get("key", "")
            license = verify_license_key(key)
            license.activated_at = data.get("activated_at", "")
            return license
        except json.JSONDecodeError:
            return verify_license_key(content)
    except OSError:
        return None


def remove_license() -> bool:
    """Remove the saved license. Returns True if a license was removed."""
    if LICENSE_PATH.exists():
        LICENSE_PATH.unlink()
        return True
    return False
