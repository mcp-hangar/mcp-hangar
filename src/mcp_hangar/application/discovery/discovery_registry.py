"""DiscoveryRegistry — UUID-keyed registry for discovery source specs.

Wraps DiscoveryOrchestrator for actual source lifecycle while maintaining
spec metadata with UUID-based identity. All mutations are thread-safe.
"""

import threading
from dataclasses import replace
from typing import Any

from ...domain.value_objects.discovery import DiscoverySourceSpec
from ...logging_config import get_logger
from .discovery_orchestrator import DiscoveryOrchestrator

logger = get_logger(__name__)


class DiscoveryRegistry:
    """UUID-keyed, thread-safe registry for DiscoverySourceSpec objects.

    Maintains the authoritative in-memory map of source_id -> DiscoverySourceSpec.
    Delegates source lifecycle (start/stop) to the wrapped DiscoveryOrchestrator
    but owns spec identity and metadata independently.

    Thread safety: All mutations acquire _lock. get_all_sources() returns a
    snapshot list to avoid holding the lock during iteration.

    Args:
        orchestrator: DiscoveryOrchestrator to delegate lifecycle operations to.
    """

    def __init__(self, orchestrator: DiscoveryOrchestrator) -> None:
        """Initialize the registry.

        Args:
            orchestrator: Existing DiscoveryOrchestrator for lifecycle delegation.
        """
        self._orchestrator = orchestrator
        self._sources: dict[str, DiscoverySourceSpec] = {}
        self._lock = threading.RLock()

    def register_source(self, spec: DiscoverySourceSpec) -> None:
        """Register or replace a discovery source spec.

        Idempotent: if source_id already exists, the spec is replaced.
        Does NOT start the underlying source — callers must trigger discovery
        via TriggerSourceScanCommand if immediate activation is needed.

        Args:
            spec: DiscoverySourceSpec to register.
        """
        with self._lock:
            self._sources[spec.source_id] = spec
        logger.info(
            "discovery_source_registered",
            source_id=spec.source_id,
            source_type=spec.source_type,
            mode=spec.mode.value,
        )

    def unregister_source(self, source_id: str) -> None:
        """Remove a discovery source spec.

        Args:
            source_id: UUID of the spec to remove.

        Raises:
            KeyError: If source_id is not registered.
        """
        with self._lock:
            if source_id not in self._sources:
                raise KeyError(f"Discovery source not found: {source_id}")
            del self._sources[source_id]
        logger.info("discovery_source_unregistered", source_id=source_id)

    def update_source(self, source_id: str, **kwargs: Any) -> DiscoverySourceSpec:
        """Apply field updates to an existing spec (immutable — produces new spec).

        Uses dataclasses.replace() so the original spec is never mutated.
        Only fields present in DiscoverySourceSpec can be updated.

        Args:
            source_id: UUID of the spec to update.
            **kwargs: Fields to update (e.g. enabled=False, mode=DiscoveryMode.AUTHORITATIVE).

        Returns:
            The new DiscoverySourceSpec with updated fields.

        Raises:
            KeyError: If source_id is not registered.
        """
        with self._lock:
            if source_id not in self._sources:
                raise KeyError(f"Discovery source not found: {source_id}")
            updated = replace(self._sources[source_id], **kwargs)
            self._sources[source_id] = updated

        # Propagate the change to the live running source, not just the stored
        # spec. The discovery cycle gates on the live source's `is_enabled`, and
        # get_sources_status() reports it, so a spec-only update left a disabled
        # source still being scanned and a subsequent GET still reading enabled:
        # the 200 and the listing contradicted each other. Done outside the lock
        # -- it touches the orchestrator's own state, not the registry's map.
        self._apply_to_live_source(updated, kwargs)
        return updated

    def _apply_to_live_source(self, spec: DiscoverySourceSpec, changed: dict[str, Any]) -> None:
        """Reflect a spec change onto the running source the orchestrator holds.

        Only the fields that actually changed are pushed. The source may not be
        running yet -- an API-registered spec has no live source until a scan
        builds one -- in which case there is nothing to update and the spec alone
        carries the state forward.
        """
        live = self._orchestrator.get_source(spec.source_type)
        if live is None:
            return
        if "enabled" in changed:
            if spec.enabled:
                live.enable()
            else:
                live.disable()
        if "config" in changed:
            live.apply_config(spec.config)

    def get_source(self, source_id: str) -> DiscoverySourceSpec | None:
        """Retrieve a spec by source_id.

        Args:
            source_id: UUID of the spec to retrieve.

        Returns:
            DiscoverySourceSpec if found, None otherwise.
        """
        with self._lock:
            return self._sources.get(source_id)

    def get_all_sources(self) -> list[DiscoverySourceSpec]:
        """Return a snapshot list of all registered specs.

        Returns:
            List of all DiscoverySourceSpec instances (snapshot — safe to iterate
            without holding the lock).
        """
        with self._lock:
            return list(self._sources.values())

    def enable_source(self, source_id: str) -> DiscoverySourceSpec:
        """Enable a discovery source.

        Args:
            source_id: UUID of the source to enable.

        Returns:
            Updated DiscoverySourceSpec with enabled=True.

        Raises:
            KeyError: If source_id is not registered.
        """
        return self.update_source(source_id, enabled=True)

    def disable_source(self, source_id: str) -> DiscoverySourceSpec:
        """Disable a discovery source.

        Args:
            source_id: UUID of the source to disable.

        Returns:
            Updated DiscoverySourceSpec with enabled=False.

        Raises:
            KeyError: If source_id is not registered.
        """
        return self.update_source(source_id, enabled=False)

    @property
    def orchestrator(self) -> DiscoveryOrchestrator:
        """Access the wrapped DiscoveryOrchestrator.

        Returns:
            The DiscoveryOrchestrator instance.
        """
        return self._orchestrator
