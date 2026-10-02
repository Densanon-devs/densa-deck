"""A self-signed certificate for the hub port, so one port can speak HTTPS too.

Some pages need a secure context: a phone browser only exposes the live
camera (`getUserMedia`) to an https:// origin. Densa Deck's browser scanner
is the case today. The hub serves HTTP and HTTPS on the same port, telling
them apart by the first byte a client sends.

This is a local, self-signed certificate: no CA and no Certificate
Transparency log, so nothing about this machine is published. The cost is a
one-time "proceed anyway" per phone browser. Native apps use plain HTTP and
never see it.

Generating needs `cryptography`. A host without it still serves a
certificate another app already made, as long as that certificate covers the
current addresses.
"""

from __future__ import annotations

import datetime
import ipaddress
import logging
import ssl
from pathlib import Path

log = logging.getLogger("densanon_hub")

CERT_NAME = "hub-cert.pem"
KEY_NAME = "hub-key.pem"
MARKER_NAME = "hub-san.txt"
VALID_DAYS = 3650


def can_generate() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except Exception:
        return False


def ensure_cert(home: Path, hosts: list[str]) -> tuple[Path, Path] | None:
    """(cert, key) covering exactly `hosts`, generating if needed and possible."""
    hosts = [h for h in dict.fromkeys(hosts) if h]
    if not hosts:
        return None
    crt, key, marker = home / CERT_NAME, home / KEY_NAME, home / MARKER_NAME
    want = ",".join(sorted(hosts))
    try:
        if crt.exists() and key.exists() and marker.read_text(encoding="utf-8").strip() == want:
            return crt, key
    except OSError:
        pass
    if not can_generate():
        return None
    home.mkdir(parents=True, exist_ok=True)
    _generate(hosts, crt, key)
    marker.write_text(want, encoding="utf-8")
    return crt, key


def context_for(crt: Path, key: Path) -> ssl.SSLContext | None:
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(crt), str(key))
        return ctx
    except (OSError, ssl.SSLError) as exc:
        log.warning("hub certificate unusable: %s", exc)
        return None


def _generate(hosts: list[str], crt: Path, key: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    alt_names: list = []
    for host in hosts:
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            alt_names.append(x509.DNSName(host))
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Densanon (this PC)"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Densanon"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    key.write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    try:
        key.chmod(0o600)
    except OSError:
        pass
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
