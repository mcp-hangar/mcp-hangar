"""Bootstrap wiring for per-provider log buffers and LogStreamBroadcaster.

Called once during bootstrap after providers have been loaded from config.
Creates a ProviderLogBuffer per provider, wires the LogStreamBroadcaster as the
on_append callback, registers each buffer in the singleton registry, and injects
it into the Provider aggregate via Provider.set_log_buffer().
"""

from ...logging_config import get_logger

logger = get_logger(__name__)


def init_log_buffers(providers: dict) -> None:
    """Create and wire log buffers for all loaded providers.

    For each provider in *providers*:
    1. Creates a :class:`~mcp_hangar.infrastructure.persistence.log_buffer.ProviderLogBuffer`
       with the global :class:`~mcp_hangar.server.api.ws.logs.LogStreamBroadcaster` as the
       ``on_append`` callback so live lines reach connected WebSocket clients.
    2. Registers the buffer in the singleton registry via
       :func:`~mcp_hangar.infrastructure.persistence.log_buffer.set_log_buffer`.
    3. Injects the buffer into the :class:`~mcp_hangar.domain.model.provider.Provider`
       aggregate via :meth:`~mcp_hangar.domain.model.provider.Provider.set_log_buffer`.

    This function is idempotent -- calling it again replaces existing buffers.

    Args:
        providers: Dict mapping provider_id -> Provider aggregate instance.
            Typically the module-level ``PROVIDERS`` dict from ``server.state``.
    """
    # Import lazily to avoid circular imports between bootstrap sub-modules.
    from ...infrastructure.persistence.log_buffer import ProviderLogBuffer, set_log_buffer
    from ..api.ws.logs import get_log_broadcaster

    broadcaster = get_log_broadcaster()
    provider_ids = list(providers.keys())

    for provider_id in provider_ids:
        provider = providers.get(provider_id)
        if provider is None:
            continue

        buffer = ProviderLogBuffer(
            provider_id=provider_id,
            on_append=broadcaster.notify,
        )
        set_log_buffer(provider_id, buffer)
        provider.set_log_buffer(buffer)

    logger.info("log_buffers_initialized", provider_count=len(provider_ids), provider_ids=provider_ids)
