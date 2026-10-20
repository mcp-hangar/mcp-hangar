"""An upstream's peer identity: a key lineage and the keys it may present (ADR-030).

A key-pinned server is reached only through a connection that ends at one of
its listed keys. This is the declaration, parsed and validated; the check
itself runs in the HTTP client's TLS handshake.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The one form a key pin has: SHA-256 over the DER SubjectPublicKeyInfo, lowercase hex.
_KEY_PIN = re.compile(r"sha256:[0-9a-f]{64}")

#: Keys ADR-030 names that this release does not act on yet. Refused rather than
#: accepted and ignored, so a declaration never claims more than it does.
_NOT_YET = {
    "issuance": "the declared issuance of the keys",
}


@dataclass(frozen=True)
class PeerIdentity:
    """A key lineage (`key_ref`) and the key pins it may present (`spki_sha256`)."""

    key_ref: str
    spki_sha256: frozenset[str]

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
        for key, what in _NOT_YET.items():
            if key in raw:
                raise ValueError(f"tls.peer_identity.{key} ({what}) is not supported by this release")
        unknown = set(raw) - {"key_ref", "spki_sha256"}
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
        return cls(key_ref=key_ref, spki_sha256=frozenset(pins))

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
