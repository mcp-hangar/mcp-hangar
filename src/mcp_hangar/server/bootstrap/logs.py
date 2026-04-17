"""Bootstrap wiring for per-provider log buffers.

Called once during bootstrap after providers have been loaded from config.
Creates a ProviderLogBuffer per provider, registers each buffer in the
singleton registry, and injects it into the Provider aggregate via
Provider.set_log_buffer().
"""

from __future__ import annotations

from collections.abc import Mapping

from ...domain.model import Provider
from ...logging_config import get_logger

logger = get_logger(__name__)


def init_log_buffers(providers: Mapping[str, Provider]) -> None:
    """Create and wire log buffers for all loaded providers.

    For each provider in *providers*:
    1. Creates a :class:`~mcp_hangar.infrastructure.persistence.log_buffer.ProviderLogBuffer`.
    2. Registers the buffer in the singleton registry via
       :func:`~mcp_hangar.infrastructure.persistence.log_buffer.set_log_buffer`.
    3. Injects the buffer into the :class:`~mcp_hangar.domain.model.provider.Provider`
       aggregate via :meth:`~mcp_hangar.domain.model.provider.Provider.set_log_buffer`.

    This function is idempotent -- calling it again replaces existing buffers.

    Args:
        providers: Dict-like mapping of provider_id -> Provider aggregate instance.
            Typically the shared runtime repository from
            ``server.bootstrap.composition.get_runtime()``.
    """
    # Import lazily to avoid circular imports between bootstrap sub-modules.
    from ...infrastructure.persistence.log_buffer import ProviderLogBuffer, set_log_buffer

    provider_ids = list(providers.keys())

    for provider_id in provider_ids:
        provider = providers.get(provider_id)
        if provider is None:
            continue

        buffer = ProviderLogBuffer(
            provider_id=provider_id,
        )
        set_log_buffer(provider_id, buffer)
        provider.set_log_buffer(buffer)

    logger.info("log_buffers_initialized", provider_count=len(provider_ids), provider_ids=provider_ids)
