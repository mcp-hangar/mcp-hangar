"""Tool digest value objects for SEP-1766 digest pinning.

Provides immutable domain primitives for tool integrity verification:
- ToolDigest: SHA-256 fingerprint of a tool's canonical schema
- DigestEnforcement: enforcement level on mismatch (audit/warn/block)
- DigestUnknownPolicy: handling of tools without a known digest
- DigestPolicy: combines enforcement, unknown-tool handling, and an allowlist
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from enum import StrEnum


_HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_DEPRECATED_POLICY_ALIASES: dict[str, str] = {
    "allow_degraded": "allow_unverified",
}


def normalize_unknown_policy(raw: str) -> str:
    """Resolve deprecated policy aliases with a warning.

    Accepts the old ``allow_degraded`` value and maps it to
    ``allow_unverified``, emitting a :class:`DeprecationWarning`.
    All other values pass through unchanged.
    """
    new = _DEPRECATED_POLICY_ALIASES.get(raw)
    if new is not None:
        warnings.warn(
            f"DigestUnknownPolicy '{raw}' is deprecated; use '{new}'. "
            "Will be removed in v1.4.",
            DeprecationWarning,
            stacklevel=2,
        )
        return new
    return raw


class DigestEnforcement(StrEnum):
    """Enforcement level when a tool digest mismatch is detected."""

    AUDIT = "audit"
    WARN = "warn"
    BLOCK = "block"


class DigestUnknownPolicy(StrEnum):
    """How to handle tools that have no digest in the allowlist."""

    ALLOW_UNVERIFIED = "allow_unverified"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class ToolDigest:
    """SHA-256 fingerprint of a tool's canonical schema.

    Attributes:
        tool_name: Fully qualified tool name.
        sha256: 64-character lowercase hex SHA-256 digest.
        algorithm: Hash algorithm identifier (forward-compat for spec changes).
    """

    tool_name: str
    sha256: str
    algorithm: str = "sha256"

    def __post_init__(self):
        if not self.tool_name:
            raise ValueError("tool_name cannot be empty")
        if not _HEX64_PATTERN.match(self.sha256):
            raise ValueError("sha256 must be exactly 64 lowercase hex characters")
        if not self.algorithm:
            raise ValueError("algorithm cannot be empty")


@dataclass(frozen=True)
class DigestPolicy:
    """Policy governing tool digest enforcement.

    Attributes:
        enforcement: What to do when a digest mismatch is detected.
        unknown: What to do when a tool has no known digest in the allowlist.
        allowlist: Set of approved tool digests.
    """

    enforcement: DigestEnforcement
    unknown: DigestUnknownPolicy
    allowlist: frozenset[ToolDigest]

    def __post_init__(self):
        if not isinstance(self.enforcement, DigestEnforcement):
            raise TypeError("enforcement must be a DigestEnforcement value")
        if not isinstance(self.unknown, DigestUnknownPolicy):
            raise TypeError("unknown must be a DigestUnknownPolicy value")
        if not isinstance(self.allowlist, frozenset):
            raise TypeError("allowlist must be a frozenset of ToolDigest")

    def get_expected_digest(self, tool_name: str) -> ToolDigest | None:
        """Look up the expected digest for a tool by name."""
        for digest in self.allowlist:
            if digest.tool_name == tool_name:
                return digest
        return None
