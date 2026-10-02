"""Binding the hub port exclusively, and finding this machine's addresses."""

from __future__ import annotations

import ipaddress
import socket
import socketserver
import ssl
import sys
from http.server import ThreadingHTTPServer

LOOPBACK = "loopback"
LAN = "lan"
TAILNET = "tailnet"

_TAILNET = ipaddress.ip_network("100.64.0.0/10")


class ExclusiveServer(ThreadingHTTPServer):
    """A listener that refuses to share its port.

    The whole election rests on "whoever binds the port is the host". Python's
    HTTPServer sets SO_REUSEADDR, and on Windows that lets a second socket
    bind a port that is already listening, so two apps would both believe they
    were the host and split the traffic between them. SO_EXCLUSIVEADDRUSE makes
    the second bind fail, as it does everywhere else.
    """

    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"
    # When set, connections that open with a TLS handshake are served over
    # TLS and the rest as plain HTTP, on the same port.
    ssl_context: ssl.SSLContext | None = None

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Bind without HTTPServer.server_bind, which looks the address up with
        # socket.getfqdn(). On a LAN address that reverse lookup took five
        # seconds on a real machine, long enough for the app that had just won
        # the port to time out registering with itself.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    def finish_request(self, request, client_address):
        ctx = self.ssl_context
        wrapped = None
        if ctx is not None:
            # Runs on the connection's own thread, so waiting for the first
            # byte holds up nobody else.
            try:
                request.settimeout(15)
                first = request.recv(1, socket.MSG_PEEK)
                if first == b"\x16":  # TLS handshake record
                    wrapped = request = ctx.wrap_socket(request, server_side=True)
                request.settimeout(None)
            except (OSError, ssl.SSLError):
                return
        try:
            super().finish_request(request, client_address)
        finally:
            if wrapped is not None:
                try:
                    wrapped.close()
                except OSError:
                    pass


def classify(ip: str) -> str | None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_loopback:
        return LOOPBACK
    if addr in _TAILNET:
        return TAILNET
    if addr.is_private:
        return LAN
    return None


def lan_address() -> str | None:
    """The address this machine uses on its local network.

    Connecting a UDP socket sends nothing; it only asks the OS which interface
    would carry the traffic.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return ip if classify(ip) == LAN else None


def tailnet_address() -> str | None:
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return None
    for info in infos:
        ip = info[4][0]
        if classify(ip) == TAILNET:
            return ip
    return None


def address_for(kind: str) -> str | None:
    if kind == LOOPBACK:
        return "127.0.0.1"
    if kind == LAN:
        return lan_address()
    if kind == TAILNET:
        return tailnet_address()
    return None
