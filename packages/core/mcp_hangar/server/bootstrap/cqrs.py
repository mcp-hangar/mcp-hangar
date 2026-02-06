"""CQRS and Saga initialization."""

from typing import TYPE_CHECKING

from ...application.commands import register_all_handlers as register_command_handlers
from ...application.queries import register_all_handlers as register_query_handlers
from ...application.sagas import GroupRebalanceSaga
from ...infrastructure.saga_manager import get_saga_manager
from ...logging_config import get_logger
from ..context import get_context
from ..state import PROVIDER_REPOSITORY, set_group_rebalance_saga

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


def init_cqrs(runtime: "Runtime", current_config_path: str | None = None) -> None:
    """Register command and query handlers.

    Args:
        runtime: Runtime instance with command and query buses.
        current_config_path: Current configuration file path for reload handler.
    """
    register_command_handlers(runtime.command_bus, PROVIDER_REPOSITORY, runtime.event_bus, current_config_path)
    register_query_handlers(runtime.query_bus, PROVIDER_REPOSITORY)
    logger.info("cqrs_handlers_registered")


def init_saga() -> None:
    """Initialize group rebalance saga."""
    ctx = get_context()
    saga = GroupRebalanceSaga(groups=ctx.groups)
    ctx.group_rebalance_saga = saga
    set_group_rebalance_saga(saga)  # For backward compatibility
    saga_manager = get_saga_manager()
    saga_manager.register_event_saga(saga)
    logger.info("group_rebalance_saga_registered")
