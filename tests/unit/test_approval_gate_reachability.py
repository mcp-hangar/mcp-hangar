"""The approval gate must be reachable on the path the process actually takes (#678).

Every assertion here is against a seam the shipped `mcp-hangar serve` / `serve
--http` goes through -- `load_config`, `load_components`, `bootstrap`,
`create_api_router` -- never against `MCPServerFactory`, which has no production
call site. Asserting the factory is how five previous instances of this class
shipped green (#592, #594, #595, #596, operator#91).

Before this, all three legs were broken independently:

1. no YAML/REST/CLI key could put a tool on an `approval_list`,
2. `bootstrap_approvals()` had no call site in `src/`, so
   `ctx.approval_gate` stayed `None` and a gated call executed immediately with
   a `approval_gate_not_configured` debug line, and
3. `/api/approvals` read `app.state.approval_gate_service`, which nothing set --
   answering 500 with an `AttributeError`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.model.mcp_server_config import (
    McpServerConfig,
    parse_tools_access_config,
    ToolsConfig,
)
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.server.bootstrap.reachability import (
    check_subsystem_reachability,
    enforce_subsystem_reachability,
)
from mcp_hangar.server.context import get_context


@pytest.fixture(autouse=True)
def _clean_policies():
    """Config loading writes to process-global registries. Put them back."""
    from mcp_hangar.server.state import get_runtime, GROUPS

    repository = get_runtime().repository
    original_servers = repository.get_all()
    original_groups = dict(GROUPS)

    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()

    repository.clear()
    for mcp_server_id, mcp_server in original_servers.items():
        repository.add(mcp_server_id, mcp_server)
    GROUPS.clear()
    GROUPS.update(original_groups)


@pytest.fixture
def context_without_gate():
    """The application context with no approval gate wired."""
    ctx = get_context()
    previous = getattr(ctx, "approval_gate", None)
    ctx.approval_gate = None
    yield ctx
    ctx.approval_gate = previous


@pytest.fixture
def context_with_gate():
    """The application context carrying a stand-in approval gate service."""
    ctx = get_context()
    previous = getattr(ctx, "approval_gate", None)
    ctx.approval_gate = MagicMock(name="approval-gate")
    yield ctx
    ctx.approval_gate = previous


@pytest.fixture
def bootstrap_harness():
    """Run the real `bootstrap()` with only its heavy edges stubbed.

    Everything under test -- component loading, context wiring and the startup
    reachability check -- runs for real; the runtime, event store, CQRS
    registration and FastMCP construction do not.
    """
    ctx = get_context()
    previous_gate = getattr(ctx, "approval_gate", None)
    ctx.approval_gate = None

    mock_runtime = MagicMock()
    mock_runtime.rate_limit_config.requests_per_second = 10
    mock_runtime.rate_limit_config.burst_size = 100
    mock_runtime.repository.get_all.return_value = {}
    mock_runtime.repository.get_all_ids.return_value = []

    patches = [
        patch("mcp_hangar.server.bootstrap._ensure_data_dir", MagicMock()),
        patch("mcp_hangar.server.bootstrap.get_runtime", MagicMock(return_value=mock_runtime)),
        patch("mcp_hangar.server.bootstrap.init_context", MagicMock()),
        patch("mcp_hangar.server.bootstrap.init_event_handlers", MagicMock()),
        patch("mcp_hangar.server.bootstrap.init_cqrs", MagicMock()),
        patch("mcp_hangar.server.bootstrap.init_saga", MagicMock()),
        patch(
            "mcp_hangar.server.bootstrap.load_configuration",
            MagicMock(return_value={"discovery": {"enabled": False}, "relay_tasks_enabled": False}),
        ),
        patch("mcp_hangar.server.bootstrap.init_retry_config", MagicMock()),
        patch("mcp_hangar.server.bootstrap.init_event_store", MagicMock()),
        patch("mcp_hangar.server.bootstrap.init_hot_loading", MagicMock(return_value=(None, None))),
        patch("mcp_hangar.server.bootstrap.new_mcp_server", MagicMock()),
        patch("mcp_hangar.server.bootstrap.register_all_tools", MagicMock()),
        patch("mcp_hangar.server.bootstrap.register_modern_surface", MagicMock()),
        patch("mcp_hangar.server.bootstrap.create_background_workers", MagicMock(return_value=[])),
        patch("mcp_hangar.server.bootstrap.init_log_buffers", MagicMock()),
        patch("mcp_hangar.server.bootstrap.GROUPS", {}),
    ]
    for patcher in patches:
        patcher.start()
    try:
        yield
    finally:
        for patcher in patches:
            patcher.stop()
        ctx.approval_gate = previous_gate


# ---------------------------------------------------------------------------
# 1. The config key reaches ToolAccessPolicy
# ---------------------------------------------------------------------------


class TestApprovalListReachesThePolicy:
    """`approval_list` existed only on the policy object and in its own tests."""

    def test_tools_config_carries_approval_settings_onto_the_policy(self):
        policy = ToolsConfig(
            approval_list=["deploy_*"],
            approval_timeout_seconds=600,
            approval_channel="event_stream",
        ).to_policy()

        assert policy.approval_list == ("deploy_*",)
        assert policy.approval_timeout_seconds == 600
        assert policy.approval_channel == "event_stream"
        assert policy.requires_approval("deploy_prod") is True

    def test_a_tools_block_with_only_an_approval_list_is_a_policy(self):
        """It used to parse to nothing: the `if allow_list or deny_list` guard."""
        cfg = parse_tools_access_config({"approval_list": ["multiply"]})

        assert cfg is not None
        assert cfg.to_policy().requires_approval("multiply") is True

    def test_a_tools_block_with_no_access_keys_is_still_not_a_policy(self):
        assert parse_tools_access_config({"something_else": 1}) is None

    def test_an_empty_approval_pattern_is_rejected(self):
        with pytest.raises(ValueError, match="approval_list"):
            parse_tools_access_config({"approval_list": [""]})

    def test_a_non_positive_timeout_is_rejected(self):
        with pytest.raises(ValueError, match="approval_timeout_seconds"):
            parse_tools_access_config({"approval_list": ["x"], "approval_timeout_seconds": 0})

    def test_mcp_server_level_yaml_registers_an_approval_policy(self):
        """The `tools:` block on an mcp_server, through the real config loader."""
        from mcp_hangar.server.config import load_config

        load_config(
            {
                "math": {
                    "mode": "subprocess",
                    "command": ["python", "-m", "math_server"],
                    "tools": {"approval_list": ["multiply"], "approval_timeout_seconds": 42},
                }
            }
        )

        policy = get_tool_access_resolver().resolve_effective_policy("math")
        assert policy.requires_approval("multiply") is True
        assert policy.approval_timeout_seconds == 42
        # Approval-gated is still *allowed* -- visible, held, not denied.
        assert policy.is_tool_allowed("multiply") is True

    def test_group_level_yaml_registers_an_approval_policy(self):
        from mcp_hangar.server.config import load_config

        load_config(
            {
                "pool": {
                    "mode": "group",
                    "tools": {"approval_list": ["delete_*"]},
                    "members": [
                        {"id": "member-a", "mode": "subprocess", "command": ["python", "-m", "a"]},
                    ],
                }
            }
        )

        policy = get_tool_access_resolver().resolve_effective_policy("member-a", group_id="pool", member_id="member-a")
        assert policy.requires_approval("delete_everything") is True

    def test_group_member_level_yaml_registers_an_approval_policy(self):
        from mcp_hangar.server.config import load_config

        load_config(
            {
                "pool": {
                    "mode": "group",
                    "members": [
                        {
                            "id": "member-a",
                            "mode": "subprocess",
                            "command": ["python", "-m", "a"],
                            "tools": {"approval_list": ["risky_*"]},
                        },
                    ],
                }
            }
        )

        policy = get_tool_access_resolver().resolve_effective_policy("member-a", group_id="pool", member_id="member-a")
        assert policy.requires_approval("risky_thing") is True

    def test_per_tenant_tool_access_yaml_registers_an_approval_policy(self):
        from mcp_hangar.server.config import load_config

        load_config(
            {
                "notion": {
                    "mode": "subprocess",
                    "command": ["python", "-m", "notion"],
                    "tool_access": {"member": {"tenant:a": {"approval_list": ["update_page"]}}},
                }
            }
        )

        policy = get_tool_access_resolver().resolve_effective_policy("notion", member_id="tenant:a")
        assert policy.requires_approval("update_page") is True

    def test_mcp_server_config_from_dict_carries_the_approval_list(self):
        config = McpServerConfig.from_dict(
            "math",
            {"mode": "subprocess", "command": ["x"], "tools": {"approval_list": ["multiply"]}},
        )

        assert config.tools_access is not None
        assert config.tools_access.to_policy().requires_approval("multiply") is True

    def test_deny_still_beats_approval_through_the_config_surface(self):
        """Precedence is a property of the policy; the new key must not soften it."""
        policy = ToolsConfig(deny_list=["multiply"], approval_list=["multiply"]).to_policy()

        assert policy.requires_approval("multiply") is False
        assert policy.is_tool_allowed("multiply") is False


# ---------------------------------------------------------------------------
# 2. The gate service is constructed and lands on the context
# ---------------------------------------------------------------------------


class TestTheGateServiceIsConstructed:
    def test_load_components_builds_the_gate_without_auth(self):
        """`bootstrap_approvals()` had no call site anywhere in src/."""
        from mcp_hangar.server.bootstrap.components import load_components

        components = load_components({})

        assert components.approval_service is not None
        assert hasattr(components.approval_service, "check")

    def test_load_components_honours_the_off_switch(self):
        from mcp_hangar.server.bootstrap.components import load_components

        assert load_components({"approvals": {"enabled": False}}).approval_service is None

    def test_a_missing_approvals_module_does_not_take_the_gateway_down(self):
        from mcp_hangar.server.bootstrap import components as components_module

        with patch.object(components_module, "_import_attribute", side_effect=ImportError("gone")):
            assert components_module.build_approval_service({}) is None

    def test_a_failing_bootstrap_does_not_take_the_gateway_down(self):
        """It surfaces through the startup reachability check instead."""
        from mcp_hangar.approvals import bootstrap as approvals_bootstrap
        from mcp_hangar.server.bootstrap import components as components_module

        with patch.object(approvals_bootstrap, "bootstrap_approvals", side_effect=RuntimeError("boom")):
            assert components_module.build_approval_service({}) is None


class TestTheGateIsOnTheContextAfterBootstrap:
    """The shipped `serve` / `serve --http` bootstrap, not the factory."""

    def test_bootstrap_publishes_the_gate_onto_the_context(self, bootstrap_harness):
        """`ctx.approval_gate = components.approval_service` never fired.

        The assignment was already there; `components.approval_service` was
        always None, so the condition guarding it was never true.
        """
        from mcp_hangar.server.bootstrap import bootstrap

        bootstrap()

        assert get_context().approval_gate is not None

    def test_the_context_the_executor_reads_is_the_one_bootstrap_wired(self, bootstrap_harness):
        """The gate and the enforcement path must be the same object."""
        from mcp_hangar.server.bootstrap import bootstrap
        from mcp_hangar.server.tools.batch import executor as executor_module

        context = bootstrap()

        gate = get_context().approval_gate
        assert gate is not None
        assert context.approval_service is gate
        # This is the exact lookup `_check_approval_gate` performs.
        assert getattr(executor_module.get_context(), "approval_gate", None) is gate


class TestAYamlGatedToolActuallyReachesTheGate:
    """The whole loop: YAML -> policy -> resolver -> executor -> gate service.

    Each leg was tested in isolation and the chain was broken at both joints.
    Observed on the stock image: a gated call returned `"result": 42.0`
    immediately with a `approval_gate_not_configured` debug line.
    """

    def _gated_call(self):
        from mcp_hangar.server.config import load_config
        from mcp_hangar.server.tools.batch.models import CallSpec

        load_config(
            {
                "math": {
                    "mode": "subprocess",
                    "command": ["python", "-m", "math_server"],
                    "tools": {"approval_list": ["multiply"]},
                }
            }
        )
        return CallSpec(index=0, call_id="c-1", mcp_server="math", tool="multiply", arguments={"a": 2})

    def test_the_gate_denies_a_yaml_gated_call(self, context_with_gate):
        from mcp_hangar.approvals.models import ApprovalResult
        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        call = self._gated_call()
        seen: dict[str, object] = {}

        async def _check(**kwargs):
            seen.update(kwargs)
            return ApprovalResult.denied("a-1", reason="nope")

        context_with_gate.approval_gate.check = _check

        result = BatchExecutor()._check_approval_gate(call, get_tool_access_resolver(), context_with_gate)

        assert result is not None, "the gated call executed instead of being held"
        assert result.error_type == "approval_denied"
        assert seen["tool_name"] == "multiply"
        # The policy handed to the gate is the one the YAML declared.
        assert seen["policy"].requires_approval("multiply") is True

    def test_an_ungated_tool_is_untouched(self, context_with_gate):
        from mcp_hangar.server.tools.batch.executor import BatchExecutor
        from mcp_hangar.server.tools.batch.models import CallSpec

        self._gated_call()
        call = CallSpec(index=0, call_id="c-2", mcp_server="math", tool="add", arguments={})

        def _explode(**_kwargs):
            raise AssertionError("the gate must not be consulted for an ungated tool")

        context_with_gate.approval_gate.check = _explode

        assert BatchExecutor()._check_approval_gate(call, get_tool_access_resolver(), context_with_gate) is None


# ---------------------------------------------------------------------------
# 3. The REST route finds it -- on every surface that builds the router
# ---------------------------------------------------------------------------


class TestTheRestRouteFindsTheGate:
    def test_the_router_publishes_the_gate_onto_app_state(self, context_with_gate):
        """Wired inside `create_api_router`, so every REST surface gets it.

        `server/lifecycle.py` was the only place that set this, and it set it
        from a field that was never populated.
        """
        from mcp_hangar.server.api import create_api_router

        app = create_api_router()

        assert app.state.approval_gate_service is context_with_gate.approval_gate

    def test_listing_approvals_answers_200_not_500(self, context_with_gate):
        from mcp_hangar.server.api import create_api_router

        service = MagicMock()

        async def _list_by_state(_state, _provider_id=None):
            return []

        service._repository.list_by_state = _list_by_state
        context_with_gate.approval_gate = service

        with TestClient(create_api_router()) as client:
            response = client.get("/approvals")

        assert response.status_code == 200
        assert response.json() == []

    def test_an_absent_gate_is_503_not_an_attribute_error(self, context_without_gate):
        """The observed failure was a 500 `AttributeError` out of the route."""
        from mcp_hangar.server.api import create_api_router

        with TestClient(create_api_router()) as client:
            response = client.get("/approvals")

        assert response.status_code == 503
        assert "approval gate" in response.json()["error"]

    def test_lifecycle_reads_a_field_the_application_context_actually_has(self):
        """The overlay in `run_http` must name a real field.

        It reads `approval_service` off its ApplicationContext; a rename on
        either side is exactly the drift that made this unreachable, and nothing
        would have caught it because the read used `getattr(..., None)`.
        """
        import inspect
        from dataclasses import fields

        from mcp_hangar.server.bootstrap import ApplicationContext
        from mcp_hangar.server.lifecycle import ServerLifecycle

        source = inspect.getsource(ServerLifecycle.run_http)
        assert "create_api_router" in source

        read_names = {name for name in ("approval_service", "approval_gate") if f'"{name}"' in source}
        assert read_names, "run_http no longer reads any approval field"
        assert read_names <= {f.name for f in fields(ApplicationContext)}

    def test_the_factory_surface_shares_the_router(self):
        """`MCPServerFactory` has no production call site.

        It must therefore not have its own approval wiring -- it goes through
        `create_api_router` like the shipped path, so the two cannot diverge.
        Wiring it there *only* is how #592/#594/#595/#596 happened.
        """
        import inspect

        from mcp_hangar.fastmcp_server.factory import MCPServerFactory

        source = inspect.getsource(MCPServerFactory)
        assert "create_api_router" in source
        assert "approval_gate_service" not in source


# ---------------------------------------------------------------------------
# 4. The startup guard: configured-but-unreachable is never silent
# ---------------------------------------------------------------------------


class TestStartupReachabilityGuard:
    def _gate_a_tool(self):
        from mcp_hangar.server.config import load_config

        load_config(
            {
                "math": {
                    "mode": "subprocess",
                    "command": ["python", "-m", "math_server"],
                    "tools": {"approval_list": ["multiply"]},
                }
            }
        )

    def test_a_gated_config_with_no_gate_service_refuses_to_start(self, context_without_gate):
        self._gate_a_tool()

        with pytest.raises(ConfigurationError, match="approval_gate"):
            enforce_subsystem_reachability({"relay_tasks_enabled": False}, context_without_gate)

    def test_the_refusal_names_the_subsystem_and_what_asked_for_it(self, context_without_gate):
        self._gate_a_tool()

        unreachable = check_subsystem_reachability({"relay_tasks_enabled": False}, context_without_gate)

        assert [r.subsystem for r in unreachable] == ["approval_gate"]
        assert "approval_list" in unreachable[0].required_by
        assert "mcp_server:math" in unreachable[0].required_by

    def test_a_gated_config_with_the_gate_wired_starts(self, context_with_gate):
        self._gate_a_tool()

        assert enforce_subsystem_reachability({"relay_tasks_enabled": False}, context_with_gate) == []

    def test_an_ungated_config_does_not_require_the_gate(self, context_without_gate):
        from mcp_hangar.server.config import load_config

        load_config(
            {
                "math": {
                    "mode": "subprocess",
                    "command": ["python", "-m", "math_server"],
                    "tools": {"deny_list": ["multiply"]},
                }
            }
        )

        assert enforce_subsystem_reachability({"relay_tasks_enabled": False}, context_without_gate) == []

    def test_enforcement_can_be_downgraded_but_never_silenced(self, context_without_gate, caplog):
        self._gate_a_tool()

        unreachable = enforce_subsystem_reachability(
            {"relay_tasks_enabled": False, "startup_checks": {"enforce": False}},
            context_without_gate,
        )

        assert [r.subsystem for r in unreachable] == ["approval_gate"]

    def test_the_task_relay_is_covered_by_the_same_guard(self, context_with_gate):
        """#592 restated as a check: enabled by config, wired by nothing."""
        from mcp_hangar._sdk_compat import HAS_NATIVE_TASKS

        if not HAS_NATIVE_TASKS:
            pytest.skip("SDK without native tasks: the relay is off by construction")

        context_with_gate.governed_task_store = None

        unreachable = check_subsystem_reachability({"relay_tasks_enabled": True}, context_with_gate)

        assert [r.subsystem for r in unreachable] == ["task_relay"]
        # Not fail-closed: the relay degrading is not an enforcement bypass.
        assert unreachable[0].fail_closed is False


class TestTheGuardRunsOnTheShippedBootstrapPath:
    """Placement matters as much as the check: it sits in `bootstrap()`.

    That is the funnel `serve`, `serve --http` and the facade all pass through,
    so no entry point can skip it.
    """

    def test_bootstrap_refuses_a_gated_config_with_approvals_turned_off(self, bootstrap_harness):
        from mcp_hangar.server.bootstrap import bootstrap

        with pytest.raises(ConfigurationError, match="approval_gate"):
            bootstrap(
                config_dict={
                    "approvals": {"enabled": False},
                    "relay_tasks_enabled": False,
                    "mcp_servers": {
                        "math": {
                            "mode": "subprocess",
                            "command": ["python", "-m", "math_server"],
                            "tools": {"approval_list": ["multiply"]},
                        }
                    },
                }
            )

    def test_bootstrap_starts_when_the_same_config_can_reach_the_gate(self, bootstrap_harness):
        from mcp_hangar.server.bootstrap import bootstrap

        bootstrap(
            config_dict={
                "relay_tasks_enabled": False,
                "mcp_servers": {
                    "math": {
                        "mode": "subprocess",
                        "command": ["python", "-m", "math_server"],
                        "tools": {"approval_list": ["multiply"]},
                    }
                },
            }
        )

        assert get_context().approval_gate is not None
