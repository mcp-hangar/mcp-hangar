"""A `tool_projection:` block on a group is read, not silently dropped (#1038).

Only `_load_mcp_server_config` parsed the block, so a group could declare neither
a withdrawal nor a digest pin nor an enforcement mode -- the key loaded without a
warning and did nothing. That is the missing half of two fail-open defects: the
prompts and resources surfaces resolve a group under its GROUP id (#1037), and a
group-routed tool call does the same (#1040), so before this there was no id
under which either control could be both declared and read for a group.

Driven through `load_config`, which is the seam both the file and the reload path
go through -- asserting on the registry after calling it directly would prove the
registry works, which was never in doubt.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects.tool_digest import DigestEnforcement
from mcp_hangar.server.config import load_config

_GROUP = "group_g"
_MEMBER = "member_1"
_DIGEST = "b" * 64


@pytest.fixture(autouse=True)
def _clean_state():
    from mcp_hangar.server.state import GROUPS

    original_groups = dict(GROUPS)
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    GROUPS.clear()
    GROUPS.update(original_groups)


def _load(tool_projection: dict) -> None:
    load_config(
        {
            _MEMBER: {"mode": "subprocess", "command": ["/bin/true"]},
            _GROUP: {
                "mode": "group",
                "members": [{"id": _MEMBER}],
                "tool_projection": tool_projection,
            },
        }
    )


class TestAGroupCanWithdraw:
    def test_a_tool(self) -> None:
        _load({"withdrawn": ["legacy_tool"]})

        assert get_tool_projection_registry().is_withdrawn(_GROUP, "legacy_tool", kind="tool", tenant_id=None)

    @pytest.mark.parametrize(
        ("key", "kind", "name"),
        [("withdrawn_prompts", "prompt", "draft_email"), ("withdrawn_resources", "resource", "secret://x")],
    )
    def test_a_prompt_or_a_resource(self, key: str, kind: str, name: str) -> None:
        """The kinds #1028 added, on the scope that serves them."""
        _load({key: [name]})

        assert get_tool_projection_registry().is_withdrawn(_GROUP, name, kind=kind, tenant_id=None)  # type: ignore[arg-type]

    def test_for_one_tenant_only(self) -> None:
        _load({"tenant_overrides": {"tenant:a": {"withdrawn_prompts": ["draft_email"]}}})

        registry = get_tool_projection_registry()
        assert registry.is_withdrawn(_GROUP, "draft_email", kind="prompt", tenant_id="tenant:a")
        assert not registry.is_withdrawn(_GROUP, "draft_email", kind="prompt", tenant_id="tenant:b")


class TestAGroupCanPin:
    def test_an_all_tenants_pin(self) -> None:
        _load({"pins": {"transfer": _DIGEST}})

        pin = get_tool_projection_registry().resolve_pin(_GROUP, "transfer", "tenant:a")
        assert pin is not None and pin.sha256 == _DIGEST

    def test_a_per_tenant_pin(self) -> None:
        _load({"tenant_overrides": {"tenant:a": {"pins": {"transfer": _DIGEST}}}})

        registry = get_tool_projection_registry()
        assert registry.resolve_pin(_GROUP, "transfer", "tenant:a") is not None
        assert registry.resolve_pin(_GROUP, "transfer", "tenant:b") is None

    def test_the_enforcement_mode(self) -> None:
        _load({"digest_enforcement": "audit"})

        assert get_tool_projection_registry().digest_enforcement(_GROUP) == DigestEnforcement.AUDIT


class TestTheServerBranchIsUnchanged:
    """One helper now serves both scopes; the server's behaviour must not move."""

    def test_a_server_still_withdraws_and_pins(self) -> None:
        load_config(
            {
                "srv": {
                    "mode": "subprocess",
                    "command": ["/bin/true"],
                    "tool_projection": {
                        "withdrawn": ["legacy_tool"],
                        "withdrawn_resources": ["secret://x"],
                        "pins": {"transfer": _DIGEST},
                        "digest_enforcement": "warn",
                        "tenant_overrides": {"tenant:a": {"withdrawn": ["beta_tool"]}},
                    },
                }
            }
        )

        registry = get_tool_projection_registry()
        assert registry.is_withdrawn("srv", "legacy_tool", kind="tool", tenant_id=None)
        assert registry.is_withdrawn("srv", "secret://x", kind="resource", tenant_id=None)
        assert registry.is_withdrawn("srv", "beta_tool", kind="tool", tenant_id="tenant:a")
        assert not registry.is_withdrawn("srv", "beta_tool", kind="tool", tenant_id="tenant:b")
        assert registry.resolve_pin("srv", "transfer", None) is not None
        assert registry.digest_enforcement("srv") == DigestEnforcement.WARN

    def test_a_group_without_the_block_registers_nothing(self) -> None:
        load_config(
            {
                _MEMBER: {"mode": "subprocess", "command": ["/bin/true"]},
                _GROUP: {"mode": "group", "members": [{"id": _MEMBER}]},
            }
        )

        assert not get_tool_projection_registry().is_withdrawn(_GROUP, "anything", kind="tool", tenant_id=None)
