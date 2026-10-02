"""Deciding whether a request came from this PC, for apps behind the hub.

Behind the hub, or any local proxy (tailscale serve), every request reaches
the app from 127.0.0.1, so a peer-address check alone says "this PC" for a
phone too. Use this instead of comparing the address yourself.
"""

from __future__ import annotations

import ipaddress

# Any of these means the request passed through something. The hub strips a
# client's own copies and writes them only for requests from the network, so
# their presence is reliable; their absence is only meaningful together with
# a loopback peer.
FORWARDING_HEADERS = (
    "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
    "Forwarded", "X-Real-IP", "Tailscale-User-Login", "Tailscale-User-Name",
)


def is_local_request(headers, peer: str) -> bool:
    """True only for a request from this machine that no proxy forwarded.

    ``headers`` is anything with a case-insensitive ``get`` (the
    ``http.server`` message, a dict-like). ``peer`` is the socket's peer
    address.
    """
    if any(headers.get(h) for h in FORWARDING_HEADERS):
        return False
    try:
        return ipaddress.ip_address((peer or "").strip()).is_loopback
    except ValueError:
        return False
