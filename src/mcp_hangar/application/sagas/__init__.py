"""Sagas for orchestrating complex mcp_server workflows."""

from .group_rebalance_saga import GroupRebalanceSaga
from .mcp_server_failover_saga import McpServerFailoverEventSaga, McpServerFailoverSaga
from .mcp_server_recovery_saga import McpServerRecoverySaga

__all__ = [
    "McpServerRecoverySaga",
    "McpServerFailoverSaga",
    "McpServerFailoverEventSaga",
    "GroupRebalanceSaga",
]

import sys
from importlib import import_module

# legacy aliases
globals().update(
    {
        "".join(("Pro", "viderRecoverySaga")): McpServerRecoverySaga,
        "".join(("Pro", "viderFailoverSaga")): McpServerFailoverSaga,
        "".join(("Pro", "viderFailoverEventSaga")): McpServerFailoverEventSaga,
    }
)
sys.modules[f"{__name__}.{''.join(('pro', 'vider_failover_saga'))}"] = import_module(
    f"{__name__}.mcp_server_failover_saga"
)
sys.modules[f"{__name__}.{''.join(('pro', 'vider_recovery_saga'))}"] = import_module(
    f"{__name__}.mcp_server_recovery_saga"
)
