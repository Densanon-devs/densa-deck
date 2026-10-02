"""One local server for every Densanon app.

The first app to start hosts the hub on one well-known port; every app after
it registers a route there instead of opening a port of its own. See
README.md for the design.
"""

__version__ = "0.2.1"

from .member import BLOCKED, CONFLICT, HOST, MEMBER, HubMember  # noqa: E402

__all__ = ["HubMember", "HOST", "MEMBER", "BLOCKED", "CONFLICT", "__version__"]
