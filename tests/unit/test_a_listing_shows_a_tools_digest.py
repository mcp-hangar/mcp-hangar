"""What a listing shows next to a tool: its computed digest and its pin (#1528).

`ToolProjectionRegistry.digest_fields` is what `hangar_tools` and the REST tool
routes call for each tool they list. Its digest is `compute_tool_digest`'s -- the
function `mcp-hangar pin` digests a server with -- and its pin is the one
`resolve_pin` finds for the caller's tenant, on the named id and then on the
server that served the listing. The served surfaces are pinned end to end in
`tests/integration/test_a_tools_digest_reads_on_every_listing.py`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcp_hangar.application.read_models import tool_projection
from mcp_hangar.application.read_models.tool_projection import ToolProjectionRegistry
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.value_objects.tool_digest import ToolDigest

PIN_ALL, PIN_TENANT, PIN_MEMBER = "a" * 64, "b" * 64, "c" * 64


def _tool(name: str = "read_item", description: str = "reads") -> ToolSchema:
    return ToolSchema(name=name, description=description, input_schema={"type": "object"})


@pytest.fixture
def registry() -> ToolProjectionRegistry:
    registry = ToolProjectionRegistry()
    registry.build_from_tools("store", [_tool()])
    return registry


def test_the_digest_is_the_one_pin_computes(registry: ToolProjectionRegistry) -> None:
    listed = _tool().to_dict()

    assert registry.digest_fields(listed, named="store") == {"digest": compute_tool_digest(listed).sha256}


def test_the_projections_digest_is_reused_when_its_schema_is_the_listed_one(registry: ToolProjectionRegistry) -> None:
    with patch.object(tool_projection, "compute_tool_digest") as compute:
        fields = registry.digest_fields(_tool().to_dict(), named="store")

    compute.assert_not_called()
    assert fields["digest"] == compute_tool_digest(_tool().to_dict()).sha256


def test_a_schema_the_projection_does_not_hold_is_digested_as_listed(registry: ToolProjectionRegistry) -> None:
    changed = _tool(description="changed since discovery").to_dict()

    assert registry.digest_fields(changed, named="store")["digest"] == compute_tool_digest(changed).sha256
    assert registry.digest_fields(_tool("unprojected").to_dict(), named="store")["digest"] == (
        compute_tool_digest(_tool("unprojected").to_dict()).sha256
    )


def test_the_callers_tenant_pin_wins_over_the_all_tenants_one(registry: ToolProjectionRegistry) -> None:
    registry.set_config_pin("store", "read_item", None, ToolDigest(tool_name="read_item", sha256=PIN_ALL))
    registry.set_config_pin("store", "read_item", "tenant-a", ToolDigest(tool_name="read_item", sha256=PIN_TENANT))
    listed = _tool().to_dict()

    assert registry.digest_fields(listed, named="store", tenant_id="tenant-a")["pinned_digest"] == PIN_TENANT
    assert registry.digest_fields(listed, named="store", tenant_id="tenant-b")["pinned_digest"] == PIN_ALL
    assert registry.digest_fields(listed, named="store")["pinned_digest"] == PIN_ALL


def test_no_pin_means_no_pinned_digest(registry: ToolProjectionRegistry) -> None:
    registry.set_config_pin("store", "read_item", "tenant-a", ToolDigest(tool_name="read_item", sha256=PIN_TENANT))

    assert "pinned_digest" not in registry.digest_fields(_tool().to_dict(), named="store", tenant_id="tenant-b")


def test_a_group_listing_reads_the_members_projection_and_pin(registry: ToolProjectionRegistry) -> None:
    registry.set_config_pin("store", "read_item", None, ToolDigest(tool_name="read_item", sha256=PIN_MEMBER))
    listed = _tool().to_dict()

    with patch.object(tool_projection, "compute_tool_digest") as compute:
        fields = registry.digest_fields(listed, named="pool", served_by="store")

    compute.assert_not_called()
    assert fields == {"digest": compute_tool_digest(listed).sha256, "pinned_digest": PIN_MEMBER}


def test_a_pin_on_the_group_wins_over_one_on_its_member(registry: ToolProjectionRegistry) -> None:
    registry.set_config_pin("store", "read_item", None, ToolDigest(tool_name="read_item", sha256=PIN_MEMBER))
    registry.set_config_pin("pool", "read_item", None, ToolDigest(tool_name="read_item", sha256=PIN_ALL))

    fields = registry.digest_fields(_tool().to_dict(), named="pool", served_by="store")

    assert fields["pinned_digest"] == PIN_ALL
