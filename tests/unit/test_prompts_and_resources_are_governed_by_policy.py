"""Prompts and resources are governed by the tool policy surface (#1028).

The prompts proxy (#1029) and the resources catalogue (#1031) shipped ungoverned
within the tenant boundary. This covers the seam that governs them: the existing
``ToolAccessPolicy`` surface, re-keyed ``(mcp_server, kind, name)`` instead of
grown a second time as parallel prompt/resource policies.

What is pinned here:

* the key shape -- kinds are independent, and an undefined kind is unrestricted
  for that scope exactly as an undefined tool policy always was;
* **backward compatibility** -- a config written before this change parses and
  decides identically, and governs tools ONLY;
* not-shown == not-callable on all three surfaces: a denied prompt/resource is
  absent from the listing AND refused on get/read, with the refusal
  indistinguishable from one for something that does not exist (#905);
* resource policy matches the UPSTREAM uri, not the ``hangar://`` projection;
* the SEP-1865 ``ui://`` guard is a case of this surface and stays fail-closed
  even when policy allows everything.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.fastmcp_server import prompt_proxy as pp
from mcp_hangar.fastmcp_server import resource_link_read_through as rt
from mcp_hangar.fastmcp_server.flat_tool_projection import is_governed_allowed

_SERVER = "server_a"
_TENANT = "tenant:a"
_OTHER = "tenant:b"
_GROUP = "group_g"

_GREET = {"name": "greet", "description": "Say hello"}
_DRAFT = {"name": "draft_email", "description": "Draft an email"}
_DOC = {"uri": "demo://doc/1", "name": "Doc 1"}
_SECRET = {"uri": "demo://secret/1", "name": "Secret"}
_TEMPLATE = {"uriTemplate": "demo://secret/{id}", "name": "Secret by id"}


@pytest.fixture(autouse=True)
def _clean_state():
    """Every singleton this seam reads, reset around each test."""
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    rt._links.clear()
    yield
    rt._links.clear()
    reset_tool_access_resolver()
    reset_tool_projection_registry()


@pytest.fixture(autouse=True)
def _no_groups():
    """No group topology unless a test asks for one."""
    with patch("mcp_hangar.fastmcp_server.flat_tool_projection._member_to_group", return_value={}):
        yield


def _identity(tenant_id: str | None) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


def _deny(kind: str, *patterns: str, server: str = _SERVER) -> None:
    get_tool_access_resolver().set_mcp_server_policy(server, ToolAccessPolicy(deny_list=patterns), kind=kind)  # type: ignore[arg-type]


def _groups():
    """Make ``_GROUP`` a real group id for the duration of a test."""
    return patch.dict(
        "mcp_hangar.server.bootstrap.composition.GROUPS",
        {_GROUP: SimpleNamespace(id=_GROUP, members=[SimpleNamespace(id="member_1")])},
        clear=True,
    )


def _prompt_map(tenant_id: str | None, responses: dict[str, dict]) -> dict:
    with (
        patch.object(pp, "_upstream_ids", return_value=list(responses)),
        patch.object(pp, "_relay", side_effect=lambda s, _m, _p: responses[s]),
    ):
        return pp._build_prompt_map(tenant_id)


def _catalog(tenant_id: str | None, listing: tuple[str, str, str], responses: dict[str, dict]) -> list[dict]:
    with (
        patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=list(responses)),
        patch.object(rt, "_relay_list", side_effect=lambda server, _method: responses[server]),
    ):
        return rt._build_catalog(tenant_id, listing)


class TestTheKeyIsServerKindName:
    """One resolver, three kinds -- and a kind never leaks into another."""

    def test_a_prompt_deny_does_not_govern_a_tool_of_the_same_name(self) -> None:
        _deny("prompt", "greet")

        assert not is_governed_allowed(_SERVER, "greet", kind="prompt", tenant_id=_TENANT)
        assert is_governed_allowed(_SERVER, "greet", kind="tool", tenant_id=_TENANT)

    def test_a_tool_deny_does_not_govern_a_prompt_of_the_same_name(self) -> None:
        """The whole point of keying by kind: a tool rule is not a prompt rule."""
        _deny("tool", "greet")

        assert not is_governed_allowed(_SERVER, "greet", kind="tool", tenant_id=_TENANT)
        assert is_governed_allowed(_SERVER, "greet", kind="prompt", tenant_id=_TENANT)

    def test_a_kind_defined_on_one_server_leaves_another_unrestricted(self) -> None:
        """The tool rule, mirrored: an undefined scope is unrestricted, per kind.

        Defining prompt policy anywhere does not turn every other server's
        prompts into a deny -- and does not silently fail open for them either:
        each scope is enforced exactly where it is defined.
        """
        _deny("prompt", "*", server="locked_down")

        assert not is_governed_allowed("locked_down", "greet", kind="prompt", tenant_id=_TENANT)
        assert is_governed_allowed(_SERVER, "greet", kind="prompt", tenant_id=_TENANT)

    def test_a_per_tenant_prompt_deny_applies_to_that_tenant_only(self) -> None:
        get_tool_access_resolver().set_standalone_member_policy(
            _SERVER, _TENANT, ToolAccessPolicy(deny_list=("draft_*",)), kind="prompt"
        )

        assert not is_governed_allowed(_SERVER, "draft_email", kind="prompt", tenant_id=_TENANT)
        assert is_governed_allowed(_SERVER, "draft_email", kind="prompt", tenant_id=_OTHER)

    def test_a_front_door_caller_with_no_identity_is_denied_every_kind(self) -> None:
        """The fail-closed branch is inherited, not re-implemented per surface."""
        get_tool_access_resolver().set_topology_mode("front_door")

        for kind in ("tool", "prompt", "resource"):
            assert not is_governed_allowed(_SERVER, "anything", kind=kind, tenant_id=None)


class TestAnExistingConfigIsUnchanged:
    """Backward compatibility, pinned: a pre-#1028 config still means what it meant."""

    @pytest.fixture(autouse=True)
    def _stub_repository(self):
        with patch("mcp_hangar.server.config._mcp_server_repository") as repo:
            repo.return_value = Mock(add=Mock())
            yield

    def _load(self, spec: dict) -> None:
        from mcp_hangar.server.config import _load_mcp_server_config

        _load_mcp_server_config(_SERVER, spec)

    def test_a_tools_only_config_still_decides_exactly_as_before(self) -> None:
        """Server policy, per-tenant policy and withdrawal: all unchanged."""
        self._load(
            {
                "mode": "subprocess",
                "command": ["dummy"],
                "tools": {"deny_list": ["dangerous_*"]},
                "tool_access": {"member": {_TENANT: {"deny_list": ["tenant_only_*"]}}},
                "tool_projection": {"withdrawn": ["legacy_tool"]},
            }
        )

        assert not is_governed_allowed(_SERVER, "dangerous_rm", kind="tool", tenant_id=_TENANT)
        assert not is_governed_allowed(_SERVER, "tenant_only_x", kind="tool", tenant_id=_TENANT)
        assert is_governed_allowed(_SERVER, "tenant_only_x", kind="tool", tenant_id=_OTHER)
        assert not is_governed_allowed(_SERVER, "legacy_tool", kind="tool", tenant_id=_TENANT)
        assert is_governed_allowed(_SERVER, "read_item", kind="tool", tenant_id=_TENANT)

        # The withdrawal still reaches the tool projection the executor reads.
        proj = get_tool_projection_registry().resolve(_SERVER, "legacy_tool", tenant_id=_TENANT)
        assert proj is not None and proj.is_withdrawn_for(_TENANT)

    def test_a_tools_only_config_governs_tools_only(self) -> None:
        """No config wrote a prompt rule, so no prompt is denied by one."""
        self._load(
            {
                "mode": "subprocess",
                "command": ["dummy"],
                "tools": {"deny_list": ["dangerous_*"]},
                "tool_projection": {"withdrawn": ["legacy_tool"]},
            }
        )

        for kind in ("prompt", "resource"):
            assert is_governed_allowed(_SERVER, "dangerous_rm", kind=kind, tenant_id=_TENANT)
            assert is_governed_allowed(_SERVER, "legacy_tool", kind=kind, tenant_id=_TENANT)

    def test_the_registration_apis_still_default_to_tools(self) -> None:
        """Every pre-#1028 call site passes no kind and must still mean 'tool'."""
        resolver = get_tool_access_resolver()
        policy = ToolAccessPolicy(deny_list=("x",))
        resolver.set_mcp_server_policy(_SERVER, policy)
        get_tool_projection_registry().set_config_withdrawal(_SERVER, "w")

        assert resolver.resolve_effective_policy(_SERVER) is policy
        assert resolver.get_configured_policy("mcp_server", _SERVER) is policy
        assert not resolver.is_tool_allowed(_SERVER, "x", member_id=_TENANT)
        assert get_tool_projection_registry().is_withdrawn(_SERVER, "w")

    def test_the_new_access_block_registers_prompt_and_resource_policy(self) -> None:
        self._load(
            {
                "mode": "subprocess",
                "command": ["dummy"],
                "access": {
                    "prompt": {"deny_list": ["draft_*"]},
                    "resource": {"allow_list": ["demo://doc/*"]},
                },
                "tool_projection": {
                    "withdrawn_prompts": ["retired_prompt"],
                    "withdrawn_resources": ["demo://gone/1"],
                },
            }
        )

        assert not is_governed_allowed(_SERVER, "draft_email", kind="prompt", tenant_id=_TENANT)
        assert is_governed_allowed(_SERVER, "greet", kind="prompt", tenant_id=_TENANT)
        assert is_governed_allowed(_SERVER, "demo://doc/1", kind="resource", tenant_id=_TENANT)
        assert not is_governed_allowed(_SERVER, "demo://other/1", kind="resource", tenant_id=_TENANT)
        assert not is_governed_allowed(_SERVER, "retired_prompt", kind="prompt", tenant_id=_TENANT)
        assert not is_governed_allowed(_SERVER, "demo://gone/1", kind="resource", tenant_id=_TENANT)
        # ... and none of it touched tools.
        assert is_governed_allowed(_SERVER, "draft_email", kind="tool", tenant_id=_TENANT)


class TestPromptsAreFilteredAtBothSurfaces:
    def test_a_denied_prompt_is_absent_from_the_list(self) -> None:
        _deny("prompt", "draft_*")

        flat = _prompt_map(_TENANT, {_SERVER: {"result": {"prompts": [_GREET, _DRAFT]}}})

        assert list(flat) == ["greet"]

    def test_a_denied_prompt_is_not_fetchable_and_looks_nonexistent(self) -> None:
        """Same map behind list and get, so what was hidden cannot be fetched."""
        _deny("prompt", "draft_*")
        low = _register_prompts()

        with (
            patch.object(pp, "_upstream_ids", return_value=[_SERVER]),
            patch.object(pp, "_relay", return_value={"result": {"prompts": [_DRAFT]}}) as relay,
            pytest.raises(Exception) as denied,
        ):
            await_sync(low.handlers["prompts/get"](None, SimpleNamespace(name="draft_email", arguments=None)))

        # The upstream was never asked, and the answer is the not-found a
        # nonexistent prompt gets -- no oracle for what exists elsewhere (#905).
        assert relay.call_count == 1, "only the list relay, never prompts/get"
        assert "Unknown prompt: draft_email" in str(denied.value)

        with (
            patch.object(pp, "_upstream_ids", return_value=[_SERVER]),
            patch.object(pp, "_relay", return_value={"result": {"prompts": []}}),
            pytest.raises(Exception) as absent,
        ):
            await_sync(low.handlers["prompts/get"](None, SimpleNamespace(name="draft_email", arguments=None)))

        assert str(denied.value) == str(absent.value)

    def test_a_withdrawn_prompt_is_absent_for_every_tenant(self) -> None:
        get_tool_projection_registry().set_config_withdrawal(_SERVER, "greet", kind="prompt")

        assert _prompt_map(_TENANT, {_SERVER: {"result": {"prompts": [_GREET]}}}) == {}
        assert _prompt_map(_OTHER, {_SERVER: {"result": {"prompts": [_GREET]}}}) == {}

    def test_a_runtime_withdrawal_hides_a_prompt_for_one_tenant_only(self) -> None:
        """Mirrors tool withdrawal, including the reload-safe runtime overlay."""
        registry = get_tool_projection_registry()
        registry.withdraw(_SERVER, "greet", _TENANT, kind="prompt")

        assert _prompt_map(_TENANT, {_SERVER: {"result": {"prompts": [_GREET]}}}) == {}
        assert list(_prompt_map(_OTHER, {_SERVER: {"result": {"prompts": [_GREET]}}})) == ["greet"]

        registry.clear_config_withdrawals()
        assert _prompt_map(_TENANT, {_SERVER: {"result": {"prompts": [_GREET]}}}) == {}, "runtime survives reload"

        registry.restore(_SERVER, "greet", _TENANT, kind="prompt")
        assert list(_prompt_map(_TENANT, {_SERVER: {"result": {"prompts": [_GREET]}}})) == ["greet"]

    def test_a_group_member_is_checked_against_its_group(self) -> None:
        """The #857 rule, unchanged for prompts: members are one logical server."""
        get_tool_access_resolver().set_group_policy("group_g", ToolAccessPolicy(deny_list=("draft_*",)), kind="prompt")

        with patch(
            "mcp_hangar.fastmcp_server.flat_tool_projection._member_to_group",
            return_value={"member_1": "group_g"},
        ):
            assert not is_governed_allowed("member_1", "draft_email", kind="prompt", tenant_id=_TENANT)
            assert is_governed_allowed("member_1", "greet", kind="prompt", tenant_id=_TENANT)


class TestResourcePolicyMatchesTheUpstreamUri:
    def test_a_pattern_matches_the_upstream_uri_not_the_projection(self) -> None:
        """The decision documented in ``is_governed_allowed``, pinned.

        An operator writes the upstream's own uri -- the identity that is stable
        across gateways -- and the owning server is already the policy scope.
        A pattern against the ``hangar://`` form therefore matches nothing.
        """
        _deny("resource", "demo://secret/*")

        entries = _catalog(_TENANT, rt.RESOURCES, {_SERVER: {"result": {"resources": [_DOC, _SECRET]}}})
        assert [e["uri"] for e in entries] == [f"hangar://{_SERVER}/demo://doc/1"]

        reset_tool_access_resolver()
        _deny("resource", "hangar://*")
        entries = _catalog(_TENANT, rt.RESOURCES, {_SERVER: {"result": {"resources": [_DOC, _SECRET]}}})
        assert len(entries) == 2, "the projected form is not the policy identity"

    def test_a_denied_template_is_absent_from_the_templates_list(self) -> None:
        _deny("resource", "demo://secret/*")

        entries = _catalog(_TENANT, rt.TEMPLATES, {_SERVER: {"result": {"resourceTemplates": [_TEMPLATE]}}})

        assert entries == []

    def test_a_denied_resource_is_not_readable_and_looks_nonexistent(self) -> None:
        _deny("resource", "demo://secret/*")
        low = _register_resources()

        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=[_SERVER]),
            patch.object(rt, "_relay_read") as relay,
            pytest.raises(Exception) as denied,
        ):
            await_sync(low.handlers["resources/read"](None, SimpleNamespace(uri=f"hangar://{_SERVER}/demo://secret/1")))

        relay.assert_not_called()
        assert "Unknown resource" in str(denied.value)

    def test_a_handed_out_link_stops_resolving_once_denied(self) -> None:
        """The TOCTOU re-check: a capability handed out earlier is not a bypass."""
        with patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True):
            rt.project_result_uris(_TENANT, _SERVER, {"content": [{"type": "resource_link", "uri": "demo://secret/1"}]})
        assert rt._links_for(_TENANT), "remembered before the deny lands"

        _deny("resource", "demo://secret/*")

        assert rt._links_for(_TENANT) == [], "and gone from the listing too"
        assert rt._resolve_target(_TENANT, f"hangar://{_SERVER}/demo://secret/1") is None


class TestAGroupScopePolicyGovernsEverySurface:
    """#1036: these surfaces are handed the GROUP id, not a member id.

    ``prompt_proxy._upstream_ids`` collapses a group member to its group id
    BEFORE any check runs, so the projection only ever asks about the group.
    A group-scope ``access:`` policy that is only consulted for a member id is
    registered and inert -- a declared deny that enforces nothing. These drive
    the projection entry points, which is the shape production actually takes.
    """

    def test_a_group_scope_prompt_deny_hides_the_prompt(self) -> None:
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(deny_list=("draft_*",)), kind="prompt")

        with _groups():
            prompts = _prompt_map(_TENANT, {_GROUP: {"result": {"prompts": [_GREET, _DRAFT]}}})

        assert list(prompts) == ["greet"]

    def test_a_group_scope_resource_deny_hides_the_uri_and_the_template(self) -> None:
        get_tool_access_resolver().set_group_policy(
            _GROUP, ToolAccessPolicy(deny_list=("demo://secret/*",)), kind="resource"
        )

        with _groups():
            listed = _catalog(_TENANT, rt.RESOURCES, {_GROUP: {"result": {"resources": [_DOC, _SECRET]}}})
            templates = _catalog(_TENANT, rt.TEMPLATES, {_GROUP: {"result": {"resourceTemplates": [_TEMPLATE]}}})

        assert [e["uri"] for e in listed] == [f"hangar://{_GROUP}/demo://doc/1"]
        assert templates == []

    def test_a_group_scope_resource_deny_makes_the_read_unresolvable(self) -> None:
        """Not-shown == not-readable: the read path takes the same decision."""
        get_tool_access_resolver().set_group_policy(
            _GROUP, ToolAccessPolicy(deny_list=("demo://secret/*",)), kind="resource"
        )

        with _groups(), patch.object(pp, "_upstream_ids", return_value=[_GROUP]):
            assert rt._resolve_target(_TENANT, f"hangar://{_GROUP}/demo://doc/1") is not None, "sanity: reachable"
            assert rt._resolve_target(_TENANT, f"hangar://{_GROUP}/demo://secret/1") is None

    def test_a_handed_out_link_stops_being_listed(self) -> None:
        """The links union is the second way into ``resources/list``."""
        block = {"type": "resource_link", "uri": f"hangar://{_GROUP}/demo://secret/1"}
        rt._remember(_TENANT, _GROUP, block)

        with _groups():
            assert rt._links_for(_TENANT) == [block]
            get_tool_access_resolver().set_group_policy(
                _GROUP, ToolAccessPolicy(deny_list=("demo://secret/*",)), kind="resource"
            )
            assert rt._links_for(_TENANT) == []

    def test_a_withdrawal_declared_on_a_member_hides_it_for_the_group(self) -> None:
        """#1037: members are interchangeable, so any member's withdrawal wins.

        The declaration is invisible to these surfaces otherwise -- they ask
        under the GROUP id, and the overlay is keyed by the id it was declared
        under. Fail-closed: an item withdrawn on one of two identical backends
        is not a state an operator can have meant.
        """
        get_tool_projection_registry().withdraw("member_1", "greet", None, kind="prompt")

        with (
            _groups(),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection._member_to_group",
                return_value={"member_1": _GROUP},
            ),
            patch.object(pp, "_relay", side_effect=lambda _s, _m, _p: {"result": {"prompts": [_GREET, _DRAFT]}}),
            patch.object(pp, "_upstream_ids", return_value=[_GROUP]),
        ):
            prompts = pp._build_prompt_map(_TENANT)

        assert list(prompts) == ["draft_email"]

    def test_a_withdrawal_declared_on_the_group_hides_it_too(self) -> None:
        get_tool_projection_registry().withdraw(_GROUP, "greet", None, kind="prompt")

        with _groups():
            prompts = _prompt_map(_TENANT, {_GROUP: {"result": {"prompts": [_GREET, _DRAFT]}}})

        assert list(prompts) == ["draft_email"]

    def test_a_standalone_server_is_unaffected_by_the_union(self) -> None:
        """The scope list is the id itself for anything that is not a group."""
        get_tool_projection_registry().withdraw("member_1", "greet", None, kind="prompt")

        assert list(_prompt_map(_TENANT, {_SERVER: {"result": {"prompts": [_GREET]}}})) == ["greet"]

    def test_a_member_id_still_resolves_to_its_group(self) -> None:
        """No regression on the tool path, which keys by MEMBER id."""
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(deny_list=("draft_*",)), kind="prompt")

        with (
            _groups(),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection._member_to_group",
                return_value={"member_1": _GROUP},
            ),
        ):
            assert not is_governed_allowed("member_1", "draft_email", kind="prompt", tenant_id=_TENANT)
            assert is_governed_allowed("member_1", "greet", kind="prompt", tenant_id=_TENANT)


class TestTheUiGuardStaysFailClosed:
    """SEP-1865 is a case of this surface, and policy cannot weaken it."""

    _UI = {"uri": "ui://widget/1", "name": "Widget"}

    def test_a_ui_resource_is_absent_from_the_catalog_by_default(self) -> None:
        entries = _catalog(_TENANT, rt.RESOURCES, {_SERVER: {"result": {"resources": [_DOC, self._UI]}}})

        assert [e["uri"] for e in entries] == [f"hangar://{_SERVER}/demo://doc/1"]

    def test_a_policy_that_allows_everything_does_not_open_the_guard(self) -> None:
        get_tool_access_resolver().set_mcp_server_policy(_SERVER, ToolAccessPolicy(allow_list=("*",)), kind="resource")

        entries = _catalog(_TENANT, rt.RESOURCES, {_SERVER: {"result": {"resources": [self._UI]}}})

        assert entries == [], "no wired ui policy means denied, whatever the resource policy says"
        assert not rt._deliverable(_TENANT, _SERVER, "ui://widget/1")


# ---------------------------------------------------------------------------
# Handler plumbing (the two proxies register on the SDK v2 lowlevel surface)
# ---------------------------------------------------------------------------


class _FakeLow:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def add_request_handler(self, method, _params_type, handler) -> None:
        self.handlers[method] = handler


def _register_prompts() -> _FakeLow:
    low = _FakeLow()
    with (
        patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True),
        patch("mcp_hangar.fastmcp_server.prompt_proxy.lowlevel_server", return_value=low),
    ):
        assert pp.maybe_register_prompt_proxy(MagicMock())
    return low


def _register_resources() -> _FakeLow:
    low = _FakeLow()
    with (
        patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True),
        patch("mcp_hangar.fastmcp_server.resource_link_read_through.lowlevel_server", return_value=low),
    ):
        assert rt.maybe_register_resource_read_through(MagicMock())
    return low


def await_sync(coro):
    """Drive one handler coroutine with the calling tenant bound.

    The handlers bind identity from the SDK request context; these tests bind
    it directly, which is the same seam ``bind_caller_identity`` falls back to.
    """
    import asyncio

    token = identity_context_var.set(_identity(_TENANT))
    try:
        return asyncio.run(coro)
    finally:
        identity_context_var.reset(token)
