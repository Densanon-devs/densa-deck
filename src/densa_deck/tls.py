"""One TLS trust list for every request the app makes.

httpx already verifies against certifi's bundle. urllib does not: on Windows
`ssl.create_default_context()` also loads the Windows ROOT store and never
consults its Disallowed store, so a CA that Windows has blocked (the eMudhra
intermediates, October 2026, via their Sectigo cross-sign) is still trusted.
Passing `cafile` skips the system stores entirely, so urllib trusts exactly
what httpx trusts: the certifi bundle shipped in this build.
"""

from __future__ import annotations

import ssl


def ssl_context() -> ssl.SSLContext:
    import certifi

    return ssl.create_default_context(cafile=certifi.where())
