"""TLS of the satellite OTA channel (spec 2026-11-10 §16: the satellite pulls from the VM agent over HTTPS).

A self-signed certificate made once on the VM (`openssl`, P-256, 10 years) in /srv/friday/secrets (key 0600). The
satellites pin its SHA-256 fingerprint, delivered with their token over Home Assistant's authenticated WebSocket
(`ota_issue_token`): no CA, and nobody on the LAN can stand in for the VM.
"""
from __future__ import annotations

import hashlib
import os
import ssl
import subprocess
from typing import Callable

CERT = "/srv/friday/secrets/ota_tls.crt"
KEY = "/srv/friday/secrets/ota_tls.key"
PORT = int(os.environ.get("FRIDAY_OTA_TLS_PORT", "8802"))


def ensure(cert: str = CERT, key: str = KEY, run: Callable = subprocess.run) -> None:
    """Makes the pair if either half is missing (never overwrites a good pair: the satellites pinned it)."""
    if os.path.isfile(cert) and os.path.isfile(key):
        return
    os.makedirs(os.path.dirname(cert), exist_ok=True)
    tmp_c, tmp_k = cert + ".tmp", key + ".tmp"
    r = run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
             "-days", "3650", "-subj", "/CN=friday-ota", "-keyout", tmp_k, "-out", tmp_c],
            capture_output=True, timeout=60)
    if r.returncode != 0:
        raise OSError("openssl failed")
    os.chmod(tmp_k, 0o600)
    os.replace(tmp_k, key)
    os.replace(tmp_c, cert)


def fingerprint(cert: str = CERT) -> str:
    with open(cert, encoding="ascii") as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    return hashlib.sha256(der).hexdigest()


def context(cert: str = CERT, key: str = KEY) -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx
