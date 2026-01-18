"""Event upcasting for schema evolution.

Upcasting happens at read time. Persisted events may have older schema versions.
This module provides:
- IEventUpcaster: a pure transformer from one schema version to the next
- UpcasterChain: resolves and applies a sequence of upcasters to reach the current schema version

Design goals:
- Pure functions (no I/O, no time dependence)
- Fail fast with context
- Backward compatible: missing version is treated as v1
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


class UpcastingError(RuntimeError):
    """Raised when upcasting cannot be completed."""

    def __init__(
        self,
        *,
        event_type: str,
        from_version: int,
        message: str,
    ) -> None:
        self.event_type = event_type
        self.from_version = from_version
        super().__init__(f"Upcasting failed for {event_type} from v{from_version}: {message}")


class IEventUpcaster(ABC):
    """Transforms event data from one schema version to the next."""

    @property
    @abstractmethod
    def event_type(self) -> str:
        """Event type this upcaster handles (e.g., 'ProviderStarted')."""

    @property
    @abstractmethod
    def from_version(self) -> int:
        """Source schema version."""

    @property
    @abstractmethod
    def to_version(self) -> int:
        """Target schema version."""

    @abstractmethod
    def upcast(self, data: dict[str, Any]) -> dict[str, Any]:
        """Transform event data from from_version to to_version.

        Args:
            data: Event payload at from_version schema.

        Returns:
            Event payload at to_version schema.
        """


class UpcasterChain:
    """Chains upcasters to transform events through multiple versions."""

    def __init__(self) -> None:
        # Structure: {event_type: {from_version: upcaster}}
        self._upcasters: dict[str, dict[int, IEventUpcaster]] = {}

    def register(self, upcaster: IEventUpcaster) -> None:
        """Register an upcaster.

        Args:
            upcaster: Upcaster instance.

        Raises:
            ValueError: If the upcaster is invalid or conflicts with an existing registration.
        """
        if upcaster.to_version <= upcaster.from_version:
            raise ValueError(
                f"Invalid upcaster {type(upcaster).__name__}: to_version must be > from_version "
                f"(got {upcaster.from_version} -> {upcaster.to_version})",
            )

        by_type = self._upcasters.setdefault(upcaster.event_type, {})
        existing = by_type.get(upcaster.from_version)
        if existing is not None:
            raise ValueError(
                f"Upcaster conflict for {upcaster.event_type} from v{upcaster.from_version}: "
                f"{type(existing).__name__} already registered",
            )

        by_type[upcaster.from_version] = upcaster
        logger.debug(
            "event_upcaster_registered",
            event_type=upcaster.event_type,
            from_version=upcaster.from_version,
            to_version=upcaster.to_version,
            upcaster=type(upcaster).__name__,
        )

    def upcast(
        self, event_type: str, version: int, data: dict[str, Any], *, current_version: int
    ) -> tuple[int, dict[str, Any]]:
        """Apply all necessary upcasters to reach current version.

        Args:
            event_type: Domain event type name.
            version: Payload schema version.
            data: Parsed payload dictionary.
            current_version: Current (target) schema version.

        Returns:
            Tuple of (final_version, transformed_data).

        Raises:
            UpcastingError: If an upcast step is missing or fails.
        """
        if version >= current_version:
            return version, data

        event_upcasters = self._upcasters.get(event_type)
        if not event_upcasters:
            # No upcasters registered for this type; passthrough.
            return version, data

        working_version = version
        working_data = data

        # Apply step-by-step: v1 -> v2 -> ...
        while working_version < current_version:
            step = event_upcasters.get(working_version)
            if step is None:
                raise UpcastingError(
                    event_type=event_type,
                    from_version=working_version,
                    message=f"Missing upcaster to reach v{current_version}",
                )

            if step.to_version != working_version + 1:
                raise UpcastingError(
                    event_type=event_type,
                    from_version=working_version,
                    message=(
                        f"Upcasters must advance one version at a time (got v{step.from_version} -> v{step.to_version})"
                    ),
                )

            try:
                working_data = step.upcast(working_data)
            except Exception as e:
                raise UpcastingError(
                    event_type=event_type,
                    from_version=working_version,
                    message=f"Upcaster {type(step).__name__} failed: {e}",
                ) from e

            working_version = step.to_version

        return working_version, working_data
