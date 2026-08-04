"""Which gate refuses first, pinned pairwise.

`BatchExecutor._execute_call_inner` is a 454-line chain of guards -- cancellation,
global timeout, target resolution, tool-access policy, withdrawal, digest pin,
circuit breaker, validators, approval, cold start, deferred pin, cancellation
again -- and each either returns a `CallResult` or falls through to the next.
The order is load-bearing and, in places, deliberate: the comments record that
withdrawal must precede the digest pin so a tool that is both withdrawn and
pinned emits no mismatch event, and that validators must precede the approval
gate so a denial short-circuits without blocking on a human.

Nothing checked that beyond the one withdrawal/pin case. Which matters because
the refusal a caller receives decides what it does next: `CircuitBreakerOpen`
invites a retry elsewhere, `ToolAccessDeniedError` does not, and
`ToolDigestMismatchError` is a supply-chain signal someone is meant to page on.
Reordering two gates silently swaps those answers.

So this arranges **two** gates to fail at once and asserts which one answers,
for every adjacent pair that can be co-triggered. Written before splitting the
function, so the split has something to be wrong against.
"""

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec

_SERVER = "server_a"
_TOOL = "read_item"
_TENANT = "tenant:A"
_STALE_DIGEST = "a" * 64  # never matches the real schema digest


def _identity(tenant_id: str | None) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


@pytest.fixture(autouse=True)
def _reset_singletons():
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture()
def ctx():
    """A healthy, ready, existing server -- every gate open unless a test shuts it."""
    context = Mock()
    context.event_bus = Mock()
    context.command_bus = Mock()
    context.command_bus.send.return_value = {"ok": True}
    context.governed_task_store = None
    context.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=False)),
    )
    context.mcp_server_exists.return_value = True
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=context),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=context),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as val_groups,
    ):
        exec_groups.get.return_value = None
        val_groups.get.return_value = None
        yield context


def _run(*, global_timeout: float = 30.0) -> object:
    token = identity_context_var.set(_identity(_TENANT))
    try:
        batch = BatchExecutor().execute(
            batch_id="b",
            calls=[CallSpec(index=0, call_id="c-1", mcp_server=_SERVER, tool=_TOOL, arguments={})],
            max_concurrency=1,
            global_timeout=global_timeout,
            fail_fast=False,
        )
    finally:
        identity_context_var.reset(token)
    return batch.results[0]


# --- arrangements, one per gate -------------------------------------------


def _arrange_catalogue() -> None:
    get_tool_projection_registry().build_from_tools(_SERVER, [ToolSchema(name=_TOOL, description="t", input_schema={})])


def _arrange_server_missing(ctx) -> None:
    ctx.get_mcp_server.return_value = None
    ctx.mcp_server_exists.return_value = False


def _arrange_tool_access_denied() -> None:
    get_tool_access_resolver().set_mcp_server_policy(_SERVER, ToolAccessPolicy(allow_list=("something-else",)))


def _arrange_withdrawn() -> None:
    get_tool_projection_registry().build_from_tools(
        _SERVER,
        [ToolSchema(name=_TOOL, description="t", input_schema={})],
        tenant_overrides={_TOOL: {_TENANT: "withdrawn"}},
    )


def _arrange_stale_pin() -> None:
    get_tool_projection_registry().set_config_pin(
        _SERVER, _TOOL, _TENANT, ToolDigest(tool_name=_TOOL, sha256=_STALE_DIGEST)
    )


def _arrange_circuit_open(ctx) -> None:
    ctx.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=True)),
    )


class TestEachGateRefusesOnItsOwn:
    """The baseline: each arrangement, alone, produces its own refusal.

    Without this, a precedence test could pass because the losing gate never
    fired at all.
    """

    def test_global_timeout(self, ctx):
        _arrange_catalogue()
        assert _run(global_timeout=-1.0).error_type == "TimeoutError"

    def test_server_not_found(self, ctx):
        _arrange_server_missing(ctx)
        assert _run().error_type == "McpServerNotFoundError"

    def test_tool_access_denied(self, ctx):
        _arrange_catalogue()
        _arrange_tool_access_denied()
        assert _run().error_type == "ToolAccessDeniedError"

    def test_withdrawn(self, ctx):
        _arrange_withdrawn()
        assert _run().error_type == "ToolWithdrawnError"

    def test_stale_digest_pin(self, ctx):
        _arrange_catalogue()
        _arrange_stale_pin()
        assert _run().error_type == "ToolDigestMismatchError"

    def test_circuit_breaker_open(self, ctx):
        _arrange_catalogue()
        _arrange_circuit_open(ctx)
        assert _run().error_type == "CircuitBreakerOpen"

    def test_all_gates_open_succeeds(self, ctx):
        """If this fails, every precedence assertion below is meaningless."""
        _arrange_catalogue()
        result = _run()
        assert result.success is True, result.error


class TestPrecedenceBetweenGates:
    """Two gates shut at once. Exactly one answer is correct."""

    def test_global_timeout_beats_everything_after_it(self, ctx):
        """The timeout check runs before the target is even resolved."""
        _arrange_server_missing(ctx)
        _arrange_tool_access_denied()
        assert _run(global_timeout=-1.0).error_type == "TimeoutError"

    def test_missing_server_beats_tool_access(self, ctx):
        """A policy answer about a server that does not exist would be misleading."""
        _arrange_server_missing(ctx)
        _arrange_tool_access_denied()
        assert _run().error_type == "McpServerNotFoundError"

    def test_tool_access_beats_withdrawal(self, ctx):
        """Not being allowed the tool outranks the tool being withdrawn."""
        _arrange_withdrawn()
        _arrange_tool_access_denied()
        assert _run().error_type == "ToolAccessDeniedError"

    def test_tool_access_beats_digest_pin(self, ctx):
        _arrange_catalogue()
        _arrange_stale_pin()
        _arrange_tool_access_denied()
        assert _run().error_type == "ToolAccessDeniedError"

    def test_withdrawal_beats_digest_pin(self, ctx):
        """Recorded in the source: a withdrawn+pinned tool emits no mismatch event."""
        _arrange_withdrawn()
        _arrange_stale_pin()
        result = _run()
        assert result.error_type == "ToolWithdrawnError"
        published = [call.args[0].__class__.__name__ for call in ctx.event_bus.publish.call_args_list]
        assert "DigestMismatchEvent" not in published

    def test_digest_pin_beats_the_circuit_breaker(self, ctx):
        """A supply-chain mismatch is not a retry-elsewhere condition."""
        _arrange_catalogue()
        _arrange_stale_pin()
        _arrange_circuit_open(ctx)
        assert _run().error_type == "ToolDigestMismatchError"

    def test_withdrawal_beats_the_circuit_breaker(self, ctx):
        _arrange_withdrawn()
        _arrange_circuit_open(ctx)
        assert _run().error_type == "ToolWithdrawnError"


class TestARefusalNeverReachesTheBackend:
    """Every gate above must refuse *before* dispatch, not after."""

    @pytest.mark.parametrize(
        "arrange",
        ["tool_access", "withdrawn", "digest_pin", "circuit_breaker"],
    )
    def test_the_command_bus_is_never_touched(self, ctx, arrange):
        _arrange_catalogue()
        if arrange == "tool_access":
            _arrange_tool_access_denied()
        elif arrange == "withdrawn":
            _arrange_withdrawn()
        elif arrange == "digest_pin":
            _arrange_stale_pin()
        else:
            _arrange_circuit_open(ctx)

        assert _run().success is False
        ctx.command_bus.send.assert_not_called()
