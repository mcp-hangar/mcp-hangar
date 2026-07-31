"""Unit tests verifying approval gate behavior for UI-originated tool calls.

This test suite documents and locks the behavior: the approval gate fires on
a call for an approval-listed tool regardless of origin. Specifically:

- UI-originated calls via tools/call (MCP Apps) are plain CallSpec objects with
  tool name and mcp_server, but no origin field.
- The approval gate (`policy.requires_approval(tool_name)`) inspects ONLY the
  tool name — it has NO origin branch and makes no distinction between caller
  types.
- Therefore, a UI call for an approval-listed tool (e.g., update_page) trips
  the same gate as an agent-originated call for that tool.
- There is NO origin-based bypass.

This is the desired behavior: approval is a blanket policy on a tool, not
scoped by caller origin.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy


class FakeRepository:
    """In-memory repository for testing."""

    def __init__(self):
        self._store: dict[str, ApprovalRequest] = {}

    async def save(self, request: ApprovalRequest) -> None:
        self._store[request.approval_id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._store.get(approval_id)

    async def list_pending(self, mcp_server_id=None):
        return [
            r
            for r in self._store.values()
            if r.state == ApprovalState.PENDING and (mcp_server_id is None or r.mcp_server_id == mcp_server_id)
        ]

    async def list_by_state(self, state, mcp_server_id=None):
        return [
            r
            for r in self._store.values()
            if r.state == state and (mcp_server_id is None or r.mcp_server_id == mcp_server_id)
        ]

    async def update_state(self, approval_id, state, decided_by, decided_at, reason):
        req = self._store.get(approval_id)
        if req:
            req.state = state
            req.decided_by = decided_by
            req.decided_at = decided_at
            req.reason = reason


class FakeDelivery:
    """Delivery that records send calls."""

    def __init__(self):
        self.sent: list[ApprovalRequest] = []

    async def send(self, request: ApprovalRequest) -> None:
        self.sent.append(request)


@pytest.fixture
def repo():
    return FakeRepository()


@pytest.fixture
def hold_registry():
    return ApprovalHoldRegistry()


@pytest.fixture
def event_bus():
    bus = MagicMock()
    bus.published = []

    def capture(event):
        bus.published.append(event)

    bus.publish = capture
    return bus


@pytest.fixture
def delivery():
    return FakeDelivery()


@pytest.fixture
def service(repo, hold_registry, event_bus, delivery):
    return ApprovalGateService(
        repository=repo,
        hold_registry=hold_registry,
        event_bus=event_bus,
        delivery=delivery,
    )


class TestUIOriginApprovalGate:
    """Tests verifying approval gate is NOT origin-gated.

    The approval gate fires on approval-listed tools regardless of caller
    origin (UI, agent, etc.). The gate has no origin parameter and makes
    no origin-based distinctions.
    """

    @pytest.mark.asyncio
    async def test_approval_gate_fires_on_ui_originated_tool_call(self, service, hold_registry):
        """Approval gate fires for UI-originated tools/call of approval-listed tool.

        A UI client (e.g., MCP Apps) makes a tools/call to invoke a tool,
        which becomes a CallSpec(tool=update_page, mcp_server=notion, ...).
        The approval gate has no knowledge of caller origin — it only inspects
        the tool name. Therefore, the gate fires for this tool.

        This test simulates the UI -> tools/call path and verifies approval
        is required.
        """
        policy = ToolAccessPolicy(
            approval_list=("update_page",),
            approval_timeout_seconds=5,
        )

        # Simulate the gate capture, as in the real approval gate service
        captured_id = None

        original_register = hold_registry.register

        async def capturing_register(approval_id):
            nonlocal captured_id
            captured_id = approval_id
            await original_register(approval_id)

        hold_registry.register = capturing_register

        async def approve_after_register():
            while captured_id is None:
                await asyncio.sleep(0.01)
            await service.resolve(captured_id, True, "admin@test.com")

        asyncio.create_task(approve_after_register())

        # Invoke the gate: this mirrors the tools/call path for a UI origin.
        # The call has only tool name and mcp_server — no origin field.
        result = await service.check(
            mcp_server_id="notion",
            tool_name="update_page",
            arguments={"page_id": "abc123"},
            policy=policy,
            correlation_id="corr-ui-1",
        )

        # Verify the gate fired: approval was required and then granted.
        assert result.approved is True
        assert result.approval_id is not None, "Approval was required for this tool"

    @pytest.mark.asyncio
    async def test_approval_gate_fires_for_agent_originated_call_same_tool(self, service, hold_registry):
        """Approval gate fires for agent-originated call of same approval-listed tool.

        An agent invokes a tool via tools/call (same endpoint as UI), which
        becomes a CallSpec(tool=update_page, mcp_server=notion, ...).
        The gate sees the same tool name and fires the same approval.

        This test demonstrates the gate is NOT origin-scoped — both caller
        types (UI, agent) trip the same approval for the same tool.
        """
        policy = ToolAccessPolicy(
            approval_list=("update_page",),
            approval_timeout_seconds=5,
        )

        captured_id = None

        original_register = hold_registry.register

        async def capturing_register(approval_id):
            nonlocal captured_id
            captured_id = approval_id
            await original_register(approval_id)

        hold_registry.register = capturing_register

        async def approve_after_register():
            while captured_id is None:
                await asyncio.sleep(0.01)
            await service.resolve(captured_id, True, "admin@test.com")

        asyncio.create_task(approve_after_register())

        # Invoke the gate for an agent caller.
        # Structurally, this is identical to the UI case:
        # CallSpec(tool, mcp_server) with no origin.
        result = await service.check(
            mcp_server_id="notion",
            tool_name="update_page",
            arguments={"page_id": "def456"},
            policy=policy,
            correlation_id="corr-agent-1",
        )

        # Verify the gate fired identically: same tool => same approval required.
        assert result.approved is True
        assert result.approval_id is not None, "Approval was required for this tool"

    @pytest.mark.asyncio
    async def test_approval_gate_no_origin_bypass_for_approval_listed_tool(self, service, hold_registry):
        """No origin-based bypass exists for approval-listed tools.

        The gate checks `policy.requires_approval(tool_name)`, period.
        There is no condition like:
          if origin == "ui":
              return None  # bypass
        if origin == "agent":
            check_approval()

        The gate either requires approval for a tool (via approval_list) or
        it doesn't. Origin does not factor in.

        This test verifies that two calls for the same approval-listed tool
        both trigger the approval gate, regardless of how we label or frame
        their origin.
        """
        policy = ToolAccessPolicy(
            approval_list=("delete_database",),
            approval_timeout_seconds=0,  # immediate timeout, so gate fires quick
        )

        # First call: "UI origin"
        result_ui = await service.check(
            mcp_server_id="postgres",
            tool_name="delete_database",
            arguments={},
            policy=policy,
            correlation_id="corr-ui-no-bypass",
        )
        assert result_ui.approved is False
        assert result_ui.error_code == "approval_timeout", "Gate fired for tool (no origin bypass for UI)"

        # Second call: "Agent origin"
        result_agent = await service.check(
            mcp_server_id="postgres",
            tool_name="delete_database",
            arguments={},
            policy=policy,
            correlation_id="corr-agent-no-bypass",
        )
        assert result_agent.approved is False
        assert result_agent.error_code == "approval_timeout", "Gate fired for tool (no origin bypass for agent)"

        # Both calls hit the same gate: the tool is on approval_list.
        # There is no origin parameter in the gate decision.

    @pytest.mark.asyncio
    async def test_approval_gate_tool_name_only_no_caller_distinction(self, service):
        """Approval gate decision is purely on tool name; no caller distinction.

        The gate calls policy.requires_approval(tool_name), which is purely
        a string match against the approval_list. The gate has no knowledge
        of the caller (UI, agent, CLI, webhook) and makes no distinction.

        This test verifies that for a tool on the approval_list, the gate
        fires regardless of context.
        """
        policy = ToolAccessPolicy(
            approval_list=("update_page", "delete_page"),
            approval_timeout_seconds=0,
        )

        # Check various tools: some on approval_list, some not.
        result_approved = await service.check(
            mcp_server_id="notion",
            tool_name="update_page",
            arguments={},
            policy=policy,
            correlation_id="corr-tool-approved",
        )
        # update_page is on approval_list => gate requires approval
        assert result_approved.approved is False
        assert result_approved.error_code == "approval_timeout"

        result_not_approved = await service.check(
            mcp_server_id="notion",
            tool_name="search",
            arguments={},
            policy=policy,
            correlation_id="corr-tool-not-approved",
        )
        # search is NOT on approval_list => gate passes (no approval needed)
        assert result_not_approved.approved is True
        assert result_not_approved.approval_id is None

        # The gate decision is purely tool name; no origin field involved.
