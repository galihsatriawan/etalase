"""RFC 9421 HTTP Message Signatures for UCP webhook deliveries (the business signs, the platform verifies).

UCP's Order capability says every order-event webhook MUST be signed by the business with a key it publishes in its profile
(`signing_keys[]`), so a platform can tell a real delivery from a forged one. This module is the signing half only:

- ES256 (ECDSA P-256, fixed-width raw r||s as RFC 9421 section 3.3.1 requires, never DER) or Ed25519;
- the covered-component set the UCP signatures page requires: @method, @authority, @path, @query when there is one,
  content-digest and content-type for a body, idempotency-key and ucp-agent when those headers are present, plus the
  webhook-id and webhook-timestamp of a delivery;
- RFC 9530 `Content-Digest` (sha-256) over the exact bytes sent;
- the key id is the RFC 7638 JWK thumbprint of the public key, so the same key always republishes under the same `kid`.

Etalase does not verify incoming signatures here (platforms are not yet asked to sign their requests to us).

The signing and the thumbprint follow the official UCP sample server (github.com/Universal-Commerce-Protocol/samples,
`ucp_signing.py` and `webhook_signer.py`, Apache License 2.0, Copyright 2026 UCP Authors). Only the signing path is kept, and the
structure is this repository's own. The cryptography itself comes from the `cryptography` package.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

COORDINATE_BYTES = 32      # P-256


class SigningKeyError(ValueError):
    """The configured private key is not usable for UCP signatures."""


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def content_digest(body: bytes) -> str:
    """The RFC 9530 Content-Digest value for the exact body bytes."""
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode("ascii") + ":"


def public_jwk(public_key, kid: str = "") -> dict:
    """The public key as a JWK, as published in the profile's `signing_keys[]`."""
    if isinstance(public_key, ed25519.Ed25519PublicKey):
        raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return {"kid": kid, "kty": "OKP", "crv": "Ed25519", "x": _b64u(raw), "use": "sig", "alg": "EdDSA"}
    numbers = public_key.public_numbers()
    return {"kid": kid, "kty": "EC", "crv": "P-256", "x": _b64u(numbers.x.to_bytes(COORDINATE_BYTES, "big")),
            "y": _b64u(numbers.y.to_bytes(COORDINATE_BYTES, "big")), "use": "sig", "alg": "ES256"}


def thumbprint(public_key) -> str:
    """RFC 7638: SHA-256 over the required public members in lexical order, no whitespace."""
    jwk = public_jwk(public_key)
    members = {k: jwk[k] for k in (("crv", "kty", "x") if jwk["kty"] == "OKP" else ("crv", "kty", "x", "y"))}
    return _b64u(hashlib.sha256(json.dumps(members, separators=(",", ":"), sort_keys=True).encode()).digest())


@dataclass
class Signer:
    """The business's webhook-signing identity: a private key and the `kid` it is published under."""
    key: object
    kid: str
    configured: bool           # False for the throwaway key generated when none was configured (dev and tests only)

    @classmethod
    def load(cls, pem: str = "") -> "Signer":
        """From a PEM private key (EC P-256 or Ed25519); with no PEM, a fresh in-memory key that changes on every start."""
        pem = (pem or "").replace("\\n", "\n").strip()
        if not pem:
            key = ec.generate_private_key(ec.SECP256R1())
            return cls(key, thumbprint(key.public_key()), configured=False)
        try:
            key = serialization.load_pem_private_key(pem.encode(), password=None)
        except (ValueError, TypeError) as e:
            raise SigningKeyError("UCP_SIGNING_KEY is not a PEM private key without a passphrase") from e
        if isinstance(key, ec.EllipticCurvePrivateKey):
            if not isinstance(key.curve, ec.SECP256R1):
                raise SigningKeyError(f"UCP_SIGNING_KEY must be EC P-256 or Ed25519, not EC {key.curve.name}")
        elif not isinstance(key, ed25519.Ed25519PrivateKey):
            raise SigningKeyError(f"UCP_SIGNING_KEY must be EC P-256 or Ed25519, not {type(key).__name__}")
        return cls(key, thumbprint(key.public_key()), configured=True)

    def jwk(self) -> dict:
        return public_jwk(self.key.public_key(), self.kid)

    def _raw_signature(self, base: bytes) -> bytes:
        if isinstance(self.key, ed25519.Ed25519PrivateKey):
            return self.key.sign(base)
        r, s = decode_dss_signature(self.key.sign(base, ec.ECDSA(hashes.SHA256())))
        return r.to_bytes(COORDINATE_BYTES, "big") + s.to_bytes(COORDINATE_BYTES, "big")

    def sign(self, method: str, url: str, headers: dict[str, str], body: bytes, created: int | None = None,
             extra: tuple[str, ...] = ("webhook-id", "webhook-timestamp")) -> dict[str, str]:
        """The headers to add so the request is signed: Content-Digest, Signature-Input and Signature.

        `headers` are the headers the caller will send. A signed body needs a Content-Type, so one is fixed here when missing.
        Covered components are the ones UCP requires, plus `extra` when that header is present."""
        parts = urlsplit(url)
        add: dict[str, str] = {}
        have = {k.lower(): v for k, v in headers.items()}
        if body:
            add["Content-Digest"] = have["content-digest"] = content_digest(body)
            if "content-type" not in have:
                add["Content-Type"] = have["content-type"] = "application/json"
        covered = ["@method", "@authority", "@path"]
        if parts.query:
            covered.append("@query")
        if body:
            covered += ["content-digest", "content-type"]
        covered += [h for h in ("idempotency-key", "ucp-agent", *extra) if h in have and h not in covered]
        params = "(" + " ".join(f'"{c}"' for c in covered) + f');created={int(time.time()) if created is None else created};keyid="{self.kid}"'

        def value(name: str) -> str:
            if name == "@method":
                return method.upper()
            if name == "@authority":
                return authority(parts.netloc, parts.scheme)
            if name == "@path":
                return parts.path or "/"
            if name == "@query":
                return "?" + parts.query
            return have[name]

        base = "\n".join([f'"{c}": {value(c)}' for c in covered] + [f'"@signature-params": {params}']).encode("utf-8")
        add["Signature-Input"] = f"sig1={params}"
        add["Signature"] = "sig1=:" + base64.b64encode(self._raw_signature(base)).decode("ascii") + ":"
        return add


def authority(netloc: str, scheme: str = "https") -> str:
    """RFC 9421 section 2.2.3: lower-case host, without the scheme's default port."""
    a = netloc.lower()
    default = ":443" if scheme == "https" else ":80"
    return a[: -len(default)] if a.endswith(default) else a
