"""Digest pins have to hold a caller who carries no tenant (#902).

Two halves of one defect. The enforcement half: `resolve_pin` was keyed only by
tenant id, so with auth off -- where every caller is anonymous and `tenant_id`
is `None` -- no pin was ever found and the gate waved every call through while
`initialize` advertised the capability. The configuration half: there was no way
to declare a pin that did not name a tenant, so the fix could not be "configure
it correctly".

The executor tests here run the real BatchExecutor pin gate, mirroring
test_digest_pinning_executor.py. The bootstrap tests run the real refusal.
"""

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.bootstrap.pinning import (
    PinnedToolsNeedAnIdentityError,
    refuse_pins_that_no_caller_can_match,
)
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec

_SERVER = "server_a"
_TOOL = "read_item"
_TENANT_A = "tenant:A"
_STALE = "a" * 64  # never matches the real schema digest
_OTHER_STALE = "b" * 64


def _make_tool(name: str = _TOOL) -> ToolSchema:
    return ToolSchema(name=name, description="A tool", input_schema={"type": "object", "properties": {}})


def _identity(tenant_id: str | None) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


@pytest.fixture(autouse=True)
def reset_singletons():
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture()
def mock_context():
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.command_bus.send.return_value = {"ok": True}
    ctx.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"), has_tools=False, health=Mock(should_degrade=Mock(return_value=False))
    )
    ctx.mcp_server_exists.return_value = True
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as val_groups,
    ):
        exec_groups.get.return_value = None
        val_groups.get.return_value = None
        yield ctx


def _execute(tenant_id: str | None, tool: str = _TOOL):
    token = identity_context_var.set(_identity(tenant_id))
    try:
        return BatchExecutor().execute(
            batch_id="b",
            calls=[CallSpec(index=0, call_id="test-call", mcp_server=_SERVER, tool=tool, arguments={})],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )
    finally:
        identity_context_var.reset(token)


class TestAllTenantsPinHoldsAnAnonymousCaller:
    def test_drifted_tool_is_blocked_with_no_tenant_identity(self, mock_context):
        """The regression. Auth off means tenant_id is None on every call."""
        registry = get_tool_projection_registry()
        registry.build_from_tools(_SERVER, [_make_tool()])
        registry.set_config_pin(_SERVER, _TOOL, None, ToolDigest(tool_name=_TOOL, sha256=_STALE))

        result = _execute(None)

        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolDigestMismatchError"
        mock_context.command_bus.send.assert_not_called()

    def test_matching_schema_still_passes_with_no_tenant_identity(self, mock_context):
        """Pinning to the live digest must not turn into a blanket refusal."""
        registry = get_tool_projection_registry()
        registry.build_from_tools(_SERVER, [_make_tool()])
        live = registry.resolve(_SERVER, _TOOL).digest
        registry.set_config_pin(_SERVER, _TOOL, None, ToolDigest(tool_name=_TOOL, sha256=live.sha256))

        result = _execute(None)

        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()

    def test_it_holds_an_identified_tenant_too(self, mock_context):
        registry = get_tool_projection_registry()
        registry.build_from_tools(_SERVER, [_make_tool()])
        registry.set_config_pin(_SERVER, _TOOL, None, ToolDigest(tool_name=_TOOL, sha256=_STALE))

        result = _execute(_TENANT_A)

        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolDigestMismatchError"

    def test_a_tenant_pin_wins_over_the_all_tenants_one(self, mock_context):
        """Narrowest first, the order the tool-access policies already use."""
        registry = get_tool_projection_registry()
        registry.build_from_tools(_SERVER, [_make_tool()])
        live = registry.resolve(_SERVER, _TOOL).digest
        registry.set_config_pin(_SERVER, _TOOL, None, ToolDigest(tool_name=_TOOL, sha256=_STALE))
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=live.sha256))

        # tenant:A is pinned to what the backend actually serves, so it passes
        # the all-tenants pin it overrides.
        assert _execute(_TENANT_A).results[0].success is True
        # everyone else is still held to the stale all-tenants pin
        assert _execute("tenant:B").results[0].success is False

    def test_a_tenant_pin_alone_still_misses_an_anonymous_caller(self, mock_context):
        """Unchanged and correct: that pin names a tenant this caller is not."""
        registry = get_tool_projection_registry()
        registry.build_from_tools(_SERVER, [_make_tool()])
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=_STALE))

        assert _execute(None).results[0].success is True

    def test_reload_drops_an_all_tenants_pin_removed_from_the_file(self, mock_context):
        registry = get_tool_projection_registry()
        registry.build_from_tools(_SERVER, [_make_tool()])
        registry.set_config_pin(_SERVER, _TOOL, None, ToolDigest(tool_name=_TOOL, sha256=_STALE))
        assert _execute(None).results[0].success is False

        registry.clear_config_pins()

        assert _execute(None).results[0].success is True


def _config(*, auth_enabled: bool | None, projection: dict) -> dict:
    cfg: dict = {"mcp_servers": {_SERVER: {"mode": "subprocess", "tool_projection": projection}}}
    if auth_enabled is not None:
        cfg["auth"] = {"enabled": auth_enabled}
    return cfg


class TestBootRefusal:
    def test_per_tenant_pins_with_auth_off_do_not_start(self):
        with pytest.raises(PinnedToolsNeedAnIdentityError) as excinfo:
            refuse_pins_that_no_caller_can_match(
                _config(auth_enabled=False, projection={"tenant_overrides": {_TENANT_A: {"pins": {_TOOL: _STALE}}}})
            )

        message = str(excinfo.value)
        # The acceptance criterion: the message names both halves of the
        # contradiction, not just the one the operator was looking at.
        assert "auth.enabled" in message
        assert _TENANT_A in message and _TOOL in message

    def test_an_absent_auth_block_is_auth_off(self):
        """`parse_auth_config` defaults `enabled` to False, so absence is not neutral."""
        with pytest.raises(PinnedToolsNeedAnIdentityError):
            refuse_pins_that_no_caller_can_match(
                _config(auth_enabled=None, projection={"tenant_overrides": {_TENANT_A: {"pins": {_TOOL: _STALE}}}})
            )

    def test_per_tenant_pins_start_when_auth_is_on(self):
        refuse_pins_that_no_caller_can_match(
            _config(auth_enabled=True, projection={"tenant_overrides": {_TENANT_A: {"pins": {_TOOL: _STALE}}}})
        )

    def test_all_tenants_pins_start_with_auth_off(self):
        """The way out of the refusal must not itself be refused."""
        refuse_pins_that_no_caller_can_match(_config(auth_enabled=False, projection={"pins": {_TOOL: _STALE}}))

    def test_a_tenant_override_without_pins_is_not_an_offender(self):
        refuse_pins_that_no_caller_can_match(
            _config(auth_enabled=False, projection={"tenant_overrides": {_TENANT_A: {"withdrawn": [_TOOL]}}})
        )

    def test_no_pins_at_all_start(self):
        refuse_pins_that_no_caller_can_match({"mcp_servers": {_SERVER: {"mode": "subprocess"}}})
        refuse_pins_that_no_caller_can_match({})
        refuse_pins_that_no_caller_can_match(None)

    def test_every_offending_server_is_named_not_just_the_first(self):
        cfg = {
            "auth": {"enabled": False},
            "mcp_servers": {
                "alpha": {"tool_projection": {"tenant_overrides": {"tenant:A": {"pins": {"one": _STALE}}}}},
                "beta": {"tool_projection": {"tenant_overrides": {"tenant:B": {"pins": {"two": _OTHER_STALE}}}}},
            },
        }
        with pytest.raises(PinnedToolsNeedAnIdentityError) as excinfo:
            refuse_pins_that_no_caller_can_match(cfg)

        assert {o[0] for o in excinfo.value.offenders} == {"alpha", "beta"}
