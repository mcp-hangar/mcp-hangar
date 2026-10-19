"""Which pins make a listing withhold a drifted tool (#1776).

`ToolProjectionRegistry.drifted_pin` is the one decision every listing asks:
the front door, `hangar_tools`, `hangar_details` and REST (through
`digest_fields`). A tool is withheld only where its call is refused -- a pin it
no longer matches, declared on an id in `block` mode -- so a listing never hides
a tool that can be called, nor shows one that cannot.

Naming: neutral placeholders only (store, shelf, read_item, tenant:a, tenant:b).
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.read_models.tool_projection import WITHHELD_DIGEST_DRIFT, ToolProjectionRegistry
from mcp_hangar.domain.value_objects.tool_digest import DigestEnforcement, ToolDigest

TOOL = "read_item"
SERVED = "a" * 64
PINNED = ToolDigest(tool_name=TOOL, sha256="b" * 64)
MATCHING = ToolDigest(tool_name=TOOL, sha256=SERVED)


def test_a_pin_the_served_digest_no_longer_matches_withholds_it() -> None:
    registry = ToolProjectionRegistry()
    registry.set_config_pin("store", TOOL, None, PINNED)

    assert registry.drifted_pin(TOOL, SERVED, None, ["store"]) == ("store", PINNED)
    assert registry.drifted_pin(TOOL, PINNED.sha256, None, ["store"]) is None


@pytest.mark.parametrize("mode", [DigestEnforcement.WARN, DigestEnforcement.AUDIT])
def test_under_a_mode_that_lets_the_call_through_it_stays_listed(mode: DigestEnforcement) -> None:
    registry = ToolProjectionRegistry()
    registry.set_config_pin("store", TOOL, None, PINNED)
    registry.set_digest_enforcement("store", mode)

    assert registry.drifted_pin(TOOL, SERVED, None, ["store"]) is None


def test_a_tenant_pin_withholds_it_for_that_tenant_only() -> None:
    registry = ToolProjectionRegistry()
    registry.set_config_pin("store", TOOL, "tenant:a", PINNED)

    assert registry.drifted_pin(TOOL, SERVED, "tenant:a", ["store"]) is not None
    assert registry.drifted_pin(TOOL, SERVED, "tenant:b", ["store"]) is None
    assert registry.drifted_pin(TOOL, SERVED, None, ["store"]) is None


def test_a_group_pin_holds_against_its_members_definition() -> None:
    registry = ToolProjectionRegistry()
    registry.set_config_pin("shelf", TOOL, None, PINNED)
    registry.set_config_pin("store", TOOL, None, MATCHING)

    # The member's own pin matches; the group's, in block mode, does not.
    assert registry.drifted_pin(TOOL, SERVED, None, ["store", "shelf"]) == ("shelf", PINNED)
    registry.set_digest_enforcement("shelf", DigestEnforcement.AUDIT)
    assert registry.drifted_pin(TOOL, SERVED, None, ["store", "shelf"]) is None


def test_the_operator_listing_names_the_reason() -> None:
    registry = ToolProjectionRegistry()
    registry.set_config_pin("store", TOOL, None, PINNED)
    tool = {"name": TOOL, "description": "drifted", "inputSchema": {"type": "object"}}

    fields = registry.digest_fields(tool, named="store")

    assert fields["withheld"] == WITHHELD_DIGEST_DRIFT
    assert fields["pinned_digest"] == PINNED.sha256
    assert "withheld" not in registry.digest_fields(tool, named="elsewhere")
