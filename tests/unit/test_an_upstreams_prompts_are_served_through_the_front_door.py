"""An upstream's prompts are served through the front door (#1024, split from #889).

The gateway advertised ``prompts`` and served none (#888 made the claim
honest by withdrawing it); this covers the proxy that makes the claim true
again in ``front_door`` mode: ``prompts/list`` aggregated per tenant across
the tenant's own upstreams, ``prompts/get`` relayed to the owning upstream,
tool-convention flat naming with collision-drop, and no cross-tenant leak.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server import prompt_proxy as pp


def _identity(tenant_id: str | None) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


_GREET = {"name": "greet", "description": "Say hello", "arguments": [{"name": "who", "required": True}]}
_SUMMARIZE = {"name": "summarize", "description": "Summarize"}


class TestPromptMap:
    def test_prompts_aggregate_across_the_tenants_upstreams(self) -> None:
        responses = {
            "server_a": {"result": {"prompts": [_GREET]}},
            "server_b": {"result": {"prompts": [_SUMMARIZE]}},
        }
        with (
            patch.object(pp, "_upstream_ids", return_value=["server_a", "server_b"]) as upstreams,
            patch.object(pp, "_relay", side_effect=lambda s, _m, _p: responses[s]),
        ):
            flat = pp._build_prompt_map("tenant:a")

        upstreams.assert_called_once_with("tenant:a")
        assert flat == {"greet": ("server_a", _GREET), "summarize": ("server_b", _SUMMARIZE)}

    def test_a_cross_server_name_collision_drops_both(self) -> None:
        """Tool convention (#232): an ambiguously-routed name serves neither."""
        with (
            patch.object(pp, "_upstream_ids", return_value=["server_a", "server_b"]),
            patch.object(pp, "_relay", return_value={"result": {"prompts": [_GREET]}}),
        ):
            flat = pp._build_prompt_map("tenant:a")

        assert flat == {}

    def test_a_dead_upstream_does_not_empty_the_list(self) -> None:
        def relay(server, _method, _params):
            if server == "server_a":
                raise RuntimeError("relay unavailable")
            return {"result": {"prompts": [_SUMMARIZE]}}

        with (
            patch.object(pp, "_upstream_ids", return_value=["server_a", "server_b"]),
            patch.object(pp, "_relay", side_effect=relay),
        ):
            flat = pp._build_prompt_map("tenant:a")

        assert flat == {"summarize": ("server_b", _SUMMARIZE)}

    def test_upstream_ids_come_from_the_tenants_flat_tool_map(self) -> None:
        """Scope = the tenant's own projected upstreams, group members collapsed (#857)."""
        from mcp_hangar.fastmcp_server import flat_tool_projection as ftp

        flat_map = {"t1": ("member_1", "t1"), "t2": ("member_2", "t2"), "t3": ("server_c", "t3")}
        with (
            patch.object(ftp, "_build_flat_map", return_value=flat_map) as build,
            patch.object(ftp, "_member_to_group", return_value={"member_1": "group_g", "member_2": "group_g"}),
        ):
            assert pp._upstream_ids("tenant:a") == ["group_g", "server_c"]
        build.assert_called_once_with("tenant:a")


class _FakeLow:
    """Captures add_request_handler registrations (the SDK v2 seam)."""

    def __init__(self):
        self.handlers = {}

    def add_request_handler(self, method, _params_type, handler):
        self.handlers[method] = handler


def _register(resolver_mode: str = "front_door") -> _FakeLow:
    low = _FakeLow()
    mcp = MagicMock()
    with (
        patch(
            "mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=resolver_mode == "front_door"
        ),
        patch("mcp_hangar.fastmcp_server.prompt_proxy.lowlevel_server", return_value=low),
    ):
        installed = pp.maybe_register_prompt_proxy(mcp)
    assert installed == (resolver_mode == "front_door")
    return low


class TestProxyHandlers:
    @pytest.mark.asyncio
    async def test_list_serves_the_tenants_prompts(self) -> None:
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with patch.object(pp, "_build_prompt_map", return_value={"greet": ("server_a", _GREET)}) as build:
                result = await low.handlers["prompts/list"](None, SimpleNamespace())
        finally:
            identity_context_var.reset(token)

        build.assert_called_once_with("tenant:a")
        assert [p.name for p in result.prompts] == ["greet"]

    @pytest.mark.asyncio
    async def test_get_relays_to_the_owning_upstream(self) -> None:
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with (
                patch.object(pp, "_build_prompt_map", return_value={"greet": ("server_a", _GREET)}),
                patch.object(
                    pp,
                    "_relay",
                    return_value={
                        "result": {"messages": [{"role": "user", "content": {"type": "text", "text": "hi bob"}}]}
                    },
                ) as relay,
            ):
                result = await low.handlers["prompts/get"](
                    None, SimpleNamespace(name="greet", arguments={"who": "bob"})
                )
        finally:
            identity_context_var.reset(token)

        relay.assert_called_once_with("server_a", "prompts/get", {"name": "greet", "arguments": {"who": "bob"}})
        assert result.messages[0].content.text == "hi bob"

    @pytest.mark.asyncio
    async def test_a_resource_link_in_a_prompt_is_projected(self) -> None:
        """Same front-door URI rewrite as a tool result (#1025), or the client
        is handed a uri the gateway cannot resolve."""
        from mcp_hangar.fastmcp_server import resource_link_read_through as rt

        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with (
                patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True),
                patch.object(pp, "_build_prompt_map", return_value={"greet": ("server_a", _GREET)}),
                patch.object(
                    pp,
                    "_relay",
                    return_value={
                        "result": {
                            "messages": [
                                {
                                    "role": "user",
                                    "content": {"type": "resource_link", "uri": "demo://doc/1", "name": "d"},
                                }
                            ]
                        }
                    },
                ),
            ):
                result = await low.handlers["prompts/get"](None, SimpleNamespace(name="greet", arguments=None))
                assert rt._lookup("tenant:a", "hangar://server_a/demo://doc/1") is not None
        finally:
            identity_context_var.reset(token)
            rt._links.clear()

        assert result.messages[0].content.uri == "hangar://server_a/demo://doc/1"

    @pytest.mark.asyncio
    async def test_an_unknown_prompt_is_a_generic_not_found(self) -> None:
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with (
                patch.object(pp, "_build_prompt_map", return_value={}),
                patch.object(pp, "_relay") as relay,
                pytest.raises(Exception) as excinfo,
            ):
                await low.handlers["prompts/get"](None, SimpleNamespace(name="greet", arguments=None))
        finally:
            identity_context_var.reset(token)

        relay.assert_not_called()
        assert "Unknown prompt" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_another_tenants_prompts_are_not_served(self) -> None:
        """The map is built for the CALLER's tenant -- existence is not leaked."""
        low = _register()
        token = identity_context_var.set(_identity("tenant:b"))
        try:
            with patch.object(
                pp, "_build_prompt_map", side_effect=lambda tenant: {} if tenant != "tenant:a" else {"greet": ("s", {})}
            ):
                result = await low.handlers["prompts/list"](None, SimpleNamespace())
                with pytest.raises(Exception) as excinfo:
                    await low.handlers["prompts/get"](None, SimpleNamespace(name="greet", arguments=None))
        finally:
            identity_context_var.reset(token)

        assert result.prompts == []
        assert "Unknown prompt" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_an_upstream_error_surfaces_as_an_mcp_error(self) -> None:
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with (
                patch.object(pp, "_build_prompt_map", return_value={"greet": ("server_a", _GREET)}),
                patch.object(pp, "_relay", return_value={"error": {"code": -32602, "message": "missing argument"}}),
                pytest.raises(Exception) as excinfo,
            ):
                await low.handlers["prompts/get"](None, SimpleNamespace(name="greet", arguments={}))
        finally:
            identity_context_var.reset(token)

        assert "missing argument" in str(excinfo.value)

    def test_egress_mode_registers_nothing(self) -> None:
        low = _register(resolver_mode="egress")
        assert low.handlers == {}


class TestPromptArgumentCompletions:
    """`completion/complete` for a `ref/prompt` (#1026, part 3 of the #889 split)."""

    def _params(self, ref_type: str = "ref/prompt", name: str = "greet"):
        from mcp_types import CompleteRequestParams

        return CompleteRequestParams.model_validate(
            {"ref": {"type": ref_type, "name": name}, "argument": {"name": "who", "value": "b"}}
        )

    def test_the_owning_upstream_is_resolved_from_the_prompt_map(self) -> None:
        with patch.object(pp, "_build_prompt_map", return_value={"greet": ("server_a", _GREET)}) as build:
            assert pp._completion_target("tenant:a", self._params().ref) == "server_a"

        build.assert_called_once_with("tenant:a")

    def test_a_prompt_this_tenant_cannot_see_has_no_target(self) -> None:
        with patch.object(pp, "_build_prompt_map", return_value={}):
            assert pp._completion_target("tenant:b", self._params().ref) is None

    def test_a_resource_reference_is_not_served_here(self) -> None:
        """A projected `hangar://` URI is a gateway name no upstream would know."""
        from mcp_types import ResourceTemplateReference

        with patch.object(pp, "_build_prompt_map", return_value={"greet": ("server_a", _GREET)}):
            ref = ResourceTemplateReference(uri="hangar://server_a/demo://blob/{id}")
            assert pp._completion_target("tenant:a", ref) is None

    def test_the_callers_meta_is_not_forwarded_upstream(self) -> None:
        from mcp_types import CompleteRequestParams

        params = CompleteRequestParams.model_validate(
            {
                "ref": {"type": "ref/prompt", "name": "greet"},
                "argument": {"name": "who", "value": "b"},
                "_meta": {"progressToken": "caller-token"},
            }
        )

        assert pp._completion_params(params) == {
            "ref": {"type": "ref/prompt", "name": "greet"},
            "argument": {"name": "who", "value": "b"},
        }

    @pytest.mark.asyncio
    async def test_completions_relay_to_the_owning_upstream(self) -> None:
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with (
                patch.object(pp, "_build_prompt_map", return_value={"greet": ("server_a", _GREET)}),
                patch.object(
                    pp, "_relay", return_value={"result": {"completion": {"values": ["bob"], "total": 1}}}
                ) as relay,
            ):
                result = await low.handlers["completion/complete"](None, self._params())
        finally:
            identity_context_var.reset(token)

        assert relay.call_args[0][0] == "server_a"
        assert relay.call_args[0][1] == "completion/complete"
        assert result.completion.values == ["bob"]

    @pytest.mark.asyncio
    async def test_an_unknown_prompt_answers_the_same_as_one_that_is_not_yours(self) -> None:
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            from mcp_types import CompleteRequestParams

            resource_ref = CompleteRequestParams.model_validate(
                {
                    "ref": {"type": "ref/resource", "uri": "hangar://server_a/demo://blob/{id}"},
                    "argument": {"name": "id", "value": "1"},
                }
            )
            with patch.object(pp, "_build_prompt_map", return_value={"greet": ("server_a", _GREET)}):
                with pytest.raises(Exception) as unknown:
                    await low.handlers["completion/complete"](None, self._params(name="nope"))
                with pytest.raises(Exception) as not_a_prompt:
                    await low.handlers["completion/complete"](None, resource_ref)
        finally:
            identity_context_var.reset(token)

        assert str(unknown.value) == str(not_a_prompt.value)

    @pytest.mark.asyncio
    async def test_an_upstream_without_completions_answers_an_empty_completion(self) -> None:
        """The prompt exists; only its completions do not."""
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with (
                patch.object(pp, "_build_prompt_map", return_value={"greet": ("server_a", _GREET)}),
                patch.object(pp, "_relay", return_value={"error": {"code": -32601, "message": "Method not found"}}),
            ):
                result = await low.handlers["completion/complete"](None, self._params())
        finally:
            identity_context_var.reset(token)

        assert result.completion.values == []

    def test_nothing_is_registered_outside_front_door(self) -> None:
        low = _register(resolver_mode="registry")

        assert low.handlers == {}
