"""Over stdio Hangar ends the approval wait itself, and with it the approval's window (ADR-029 s12.4, #1918)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_hangar.approvals.bootstrap import DEFAULT_STDIO_WAIT_SECONDS, bootstrap_approvals, stdio_wait_seconds
from mcp_hangar.approvals.models import ApprovalState
from mcp_hangar.approvals.persistence.sqlite_approval_repository import SqliteApprovalRepository
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (None, DEFAULT_STDIO_WAIT_SECONDS),
        ({}, DEFAULT_STDIO_WAIT_SECONDS),
        ({"approvals": {"stdio_wait_seconds": 20}}, 20),
        ({"approvals": {"stdio_wait_seconds": 0}}, DEFAULT_STDIO_WAIT_SECONDS),
        ({"approvals": {"stdio_wait_seconds": -1}}, DEFAULT_STDIO_WAIT_SECONDS),
        ({"approvals": {"stdio_wait_seconds": True}}, DEFAULT_STDIO_WAIT_SECONDS),
        ({"approvals": {"stdio_wait_seconds": "20"}}, DEFAULT_STDIO_WAIT_SECONDS),
    ],
)
def test_the_limit_is_a_positive_number_of_seconds(config: Any, expected: int) -> None:
    assert stdio_wait_seconds(config) == expected


def test_the_default_is_below_the_shortest_client_timeout_measured() -> None:
    assert DEFAULT_STDIO_WAIT_SECONDS < 60


@pytest.mark.parametrize(
    ("stdio", "allowed", "window"),
    [(True, 300, 20), (True, 5, 5), (False, 300, 300)],
    ids=["stdio-limit-ends-it", "stdio-shorter-policy-stands", "http-is-not-limited"],
)
def test_the_window_is_the_shorter_of_the_policy_and_the_limit_on_stdio(stdio, allowed, window) -> None:
    service = bootstrap_approvals(
        database=MagicMock(),
        event_bus=MagicMock(),
        config={"approvals": {"stdio_wait_seconds": 20}},
        repository=AsyncMock(),
        stdio=stdio,
    )
    policy = ToolAccessPolicy(approval_list=("t",), approval_timeout_seconds=allowed)

    assert service._window_seconds(policy) == window


async def test_the_record_expires_with_the_wait_and_a_late_approve_is_refused(tmp_path) -> None:
    repository = SqliteApprovalRepository(Database(DatabaseConfig(path=str(tmp_path / "a.db"))))
    service = bootstrap_approvals(
        database=None,
        event_bus=MagicMock(),
        config={"approvals": {"stdio_wait_seconds": 1}},
        repository=repository,
        stdio=True,
    )
    policy = ToolAccessPolicy(approval_list=("t",), approval_timeout_seconds=300)

    result = await service.check(tool_name="t", arguments={}, policy=policy, correlation_id="c", mcp_server_id="s")

    record = await repository.get(result.approval_id)
    assert record.state is ApprovalState.EXPIRED
    assert (record.expires_at - record.requested_at).total_seconds() == 1
    assert await service.resolve(result.approval_id, True, "late") is False
