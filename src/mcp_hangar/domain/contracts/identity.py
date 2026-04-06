"""Contracts for identity propagation and extraction."""

from typing import Protocol
from mcp_hangar.domain.value_objects.identity import IdentityContext

class IIdentityExtractor(Protocol):
    """Extracts identity from raw request metadata/headers."""

    def extract(self, metadata: list[tuple[str, str]] | dict[str, str] | None) -> IdentityContext | None:
        """Extract identity context. Returns None if not present or unauthenticated."""
        ...

class IIdentityPropagator(Protocol):
    """Injects identity into downstream requests."""

    def inject(self, context: IdentityContext | None, metadata: list[tuple[str, str]] | dict[str, str]) -> None:
        """Inject identity context into metadata for downstream call."""
        ...

class NullIdentityPropagator(IIdentityPropagator):
    """A no-op propagator used when identity features are disabled."""

    def inject(self, context: IdentityContext | None, metadata: list[tuple[str, str]] | dict[str, str]) -> None:
        pass

