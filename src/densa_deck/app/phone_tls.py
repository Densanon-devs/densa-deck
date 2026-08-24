"""Self-signed TLS for the phone bridge.

## Why this is not the HTTPS that was turned down

Two different things wear the word "HTTPS":

  * **Tailscale HTTPS certificates** — a real CA issues a certificate for your
    machine's MagicDNS name. That name lands in the public Certificate
    Transparency log, permanently and irreversibly. That is the one that was
    declined, and it stays declined.
  * **A self-signed certificate** — generated here, on this machine, living in
    `~/.densa-deck/certs/`. No CA, no CT log, no admin console, nothing leaves
    the box. Delete the folder and it is gone.

Only the first has a disclosure cost. This module does the second.

## Why bother at all

`navigator.mediaDevices.getUserMedia` — the live camera — is gated behind
`isSecureContext`, which requires an `https://` origin. Over plain HTTP the
API is not merely blocked, it is *absent*, so the phone can only hand off to
the OS camera app one still at a time. That is photo roulette, not scanning.

With TLS the phone gets a real viewfinder and can capture continuously: hold
a card up, hear the tick, move to the next one.

The cost is a one-time interstitial per device ("Advanced" -> "Proceed"),
because nothing vouches for this certificate but us. `ascii-oracle` in this
workspace makes the same trade for microphone access.

The certificate covers the tailnet IP and MagicDNS name, and is regenerated
whenever those change — a cert for an address you no longer have produces a
much more confusing browser error than no cert at all.
"""

from __future__ import annotations

import datetime
import ipaddress
import ssl
from pathlib import Path

CERT_DIR_NAME = "certs"
CERT_NAME = "phone-cert.pem"
KEY_NAME = "phone-key.pem"
# Records what the cached cert covers, so a changed tailnet address forces a
# regenerate instead of silently serving a cert for the wrong host.
MARKER_NAME = "phone-san.txt"

# Long-lived on purpose: this is a personal, local certificate and an expiry
# prompt a year in would just be a confusing re-accept for no security gain.
VALID_DAYS = 3650


def tls_available() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except Exception:
        return False


def cert_dir(base: Path | None = None) -> Path:
    base = base or (Path.home() / ".densa-deck")
    return Path(base) / CERT_DIR_NAME


def ensure_cert(hosts: list[str], base: Path | None = None) -> tuple[str, str] | None:
    """Return (cert_path, key_path) covering `hosts`, generating if needed.

    Cached on the exact host list. Returns None when `cryptography` isn't
    available, so callers fall back to plain HTTP rather than failing.
    """
    if not tls_available():
        return None

    hosts = [h for h in dict.fromkeys(hosts) if h]
    if not hosts:
        return None

    directory = cert_dir(base)
    directory.mkdir(parents=True, exist_ok=True)
    crt = directory / CERT_NAME
    key = directory / KEY_NAME
    marker = directory / MARKER_NAME
    want = ",".join(sorted(hosts))

    if crt.exists() and key.exists() and marker.exists():
        try:
            if marker.read_text(encoding="utf-8").strip() == want:
                return str(crt), str(key)
        except OSError:
            pass

    _generate(hosts, crt, key)
    marker.write_text(want, encoding="utf-8")
    return str(crt), str(key)


def _generate(hosts: list[str], crt: Path, key: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Subject alternative names: every address the phone might dial. A browser
    # ignores CN entirely, so anything missing here is a hard failure.
    alt_names: list[x509.GeneralName] = []
    for host in hosts:
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            alt_names.append(x509.DNSName(host))

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Densa Deck (local)"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Densa Deck"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                       critical=True)
        .sign(private_key, hashes.SHA256())
    )

    key.write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        # The key is a credential; don't leave it group/world readable.
        key.chmod(0o600)
    except OSError:
        pass


def make_ssl_context(cert_path: str, key_path: str) -> ssl.SSLContext | None:
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        return ctx
    except Exception:
        return None


def remove_cert(base: Path | None = None) -> bool:
    """Delete the local certificate. Nothing else knows it existed."""
    directory = cert_dir(base)
    removed = False
    for name in (CERT_NAME, KEY_NAME, MARKER_NAME):
        target = directory / name
        if target.exists():
            try:
                target.unlink()
                removed = True
            except OSError:
                pass
    return removed
