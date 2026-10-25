"""The failover saga is deprecated, warns where only outside code reaches it, and is silent at boot (#2092).

No configuration key reaches `configure_failover()`, and the step saga's
`failback` stops the backup right after `start_backup` started it. The classes
stay importable and behave as before; only the two entry points a caller has
to choose to use warn.
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.ports.saga import SagaContext
from mcp_hangar.application.sagas import McpServerFailoverEventSaga, McpServerFailoverSaga
from mcp_hangar.domain.events import McpServerDegraded


def test_configuring_a_failover_pair_warns_and_still_configures_it() -> None:
    saga = McpServerFailoverEventSaga(saga_manager=MagicMock())

    with pytest.warns(DeprecationWarning, match="group"):
        saga.configure_failover("primary", "backup")

    assert saga.get_failover_config("primary").backup_id == "backup"


def test_configuring_the_step_saga_warns_and_keeps_its_three_steps() -> None:
    saga = McpServerFailoverSaga()

    with pytest.warns(DeprecationWarning, match="await_primary does not wait"):
        saga.configure(SagaContext(data={"primary_id": "p", "backup_id": "b"}))

    assert [step.name for step in saga._steps] == ["start_backup", "await_primary", "failback"]


def test_what_the_gateway_does_with_it_at_boot_does_not_warn() -> None:
    """Bootstrap builds the event saga, restores its state and feeds it events; none of that warns."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        saga = McpServerFailoverEventSaga(saga_manager=MagicMock())
        saga.from_dict(saga.to_dict())
        commands = saga.handle(
            McpServerDegraded(mcp_server_id="anything", consecutive_failures=3, total_failures=3, reason="x")
        )

    assert commands == []
