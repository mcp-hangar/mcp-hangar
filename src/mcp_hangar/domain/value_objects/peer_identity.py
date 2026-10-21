"""An upstream's peer identity: a key lineage and the keys it may present (ADR-030).

A key-pinned server is reached only through a connection that ends at one of
its listed keys. This is the declaration, parsed and validated; the check
itself runs in the HTTP client's TLS handshake.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The one form a key pin has: SHA-256 over the DER SubjectPublicKeyInfo, lowercase hex.
_KEY_PIN = re.compile(r"sha256:[0-9a-f]{64}")

#: The declared sources of the keys (ADR-030 §2), a closed set. A declaration,
#: never a verification: Hangar records it and checks none of it.
KEY_ISSUANCES = frozenset({"kbs-attested"})


@dataclass(frozen=True)
class PeerIdentity:
    """A key lineage (`key_ref`), the key pins it may present (`spki_sha256`), and their declared `issuance`."""

    key_ref: str
    spki_sha256: frozenset[str]
    #: Where the operator says the keys come from, or None. Recorded on the
    #: invocation events as `declared_key_issuance`; it changes neither the
    #: identity digest pins bind to nor what the handshake accepts.
    issuance: str | None = None

    @classmethod
    def from_mapping(cls, raw: object) -> "PeerIdentity":
        """Parse a `tls.peer_identity` block.

        Raises:
            ValueError: naming what is wrong. An empty key list, a pin not in
                canonical form, or a missing `key_ref` would build a client that
                verifies nothing, so each is refused.
        """
        if not isinstance(raw, Mapping):
            raise ValueError("tls.peer_identity must be a mapping with key_ref and spki_sha256")
        unknown = set(raw) - {"key_ref", "spki_sha256", "issuance"}
        if unknown:
            raise ValueError(f"tls.peer_identity has unknown keys: {', '.join(sorted(map(str, unknown)))}")

        key_ref = raw.get("key_ref")
        if not isinstance(key_ref, str) or not key_ref.strip():
            raise ValueError("tls.peer_identity.key_ref must name the key lineage (a non-empty string)")

        pins = raw.get("spki_sha256")
        if not isinstance(pins, list | tuple) or not pins:
            raise ValueError("tls.peer_identity.spki_sha256 must list at least one key pin")
        for pin in pins:
            if not isinstance(pin, str) or not _KEY_PIN.fullmatch(pin):
                raise ValueError(
                    f"tls.peer_identity.spki_sha256 entry {pin!r} is not a key pin: expected "
                    "'sha256:' and 64 lowercase hex characters, the SHA-256 of the DER SubjectPublicKeyInfo"
                )
        issuance = raw.get("issuance")
        if issuance is not None and (not isinstance(issuance, str) or issuance not in KEY_ISSUANCES):
            raise ValueError(
                f"tls.peer_identity.issuance {issuance!r} is not a declared key issuance; "
                f"expected one of: {', '.join(sorted(KEY_ISSUANCES))}"
            )
        return cls(key_ref=key_ref, spki_sha256=frozenset(pins), issuance=issuance)

    @classmethod
    def from_tls(cls, tls: Mapping[str, Any] | None) -> "PeerIdentity | None":
        """The peer identity a server's `tls` block declares, or None when it declares none."""
        if not tls or "peer_identity" not in tls:
            return None
        return cls.from_mapping(tls["peer_identity"])

    @property
    def upstream_identity(self) -> str:
        """The identity digest pins are bound to: the key lineage, not the address (ADR-030 §7)."""
        return f"peer-key:{self.key_ref}"


def connection_declaration(endpoint: str | None, tls: Mapping[str, Any] | None) -> tuple[str | None, str]:
    """What a remote server's client is built from, in a form two copies compare equal on.

    The row's `tls` has been through JSON and the local one may not have, so
    both are compared in their JSON form, keys sorted (ADR-030 §8).
    """
    return endpoint, json.dumps(dict(tls) if tls else None, sort_keys=True, default=str)


#: The connection age a key-pinned server gets when its configuration names none (ADR-030 §8).
DEFAULT_MAX_CONNECTION_AGE_S = 3600.0

_AGE = re.compile(r"(\d+(?:\.\d+)?)\s*([smh]?)")
_UNIT_S = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}


def max_connection_age(tls: Mapping[str, Any] | None) -> float | None:
    """Seconds one connection pool of a key-pinned server serves, or None for any other server.

    Accepts a number of seconds or a string like ``90s``, ``30m`` or ``1h``. A
    key-pinned server that names none gets ``DEFAULT_MAX_CONNECTION_AGE_S``.

    Raises:
        ValueError: for a value that is not a positive duration, or one set on a
            server without a peer identity, where nothing would act on it.
    """
    raw = (tls or {}).get("max_connection_age")
    if not tls or "peer_identity" not in tls:
        if raw is not None:
            raise ValueError("tls.max_connection_age applies only to a server with tls.peer_identity")
        return None
    if raw is None:
        return DEFAULT_MAX_CONNECTION_AGE_S
    if isinstance(raw, bool):
        raise ValueError(f"tls.max_connection_age must be a duration such as 90s, 30m or 1h, got {raw!r}")
    if isinstance(raw, int | float):
        seconds = float(raw)
    else:
        match = _AGE.fullmatch(str(raw).strip().lower())
        if match is None:
            raise ValueError(f"tls.max_connection_age must be a duration such as 90s, 30m or 1h, got {raw!r}")
        seconds = float(match.group(1)) * _UNIT_S[match.group(2)]
    if seconds <= 0:
        raise ValueError(f"tls.max_connection_age must be positive, got {raw!r}")
    return seconds
