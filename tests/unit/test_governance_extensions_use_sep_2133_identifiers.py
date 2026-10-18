"""Hangar's governance extensions are named the way SEP-2133 names an extension (#1898).

An identifier is ``{vendor-prefix}/{extension-name}``. Hangar used ``io.mcp-hangar.<name>``,
which has no slash, and advertised it only under ``experimental``. The handshake era
has no other place; the 2026-07-28 surface has ``capabilities.extensions``.
"""

from __future__ import annotations

import pytest

from mcp_hangar._sdk_compat import lowlevel_server
from mcp_hangar.fastmcp_server.governance_extensions import LEGACY_DOTTED_IDS
from mcp_hangar.fastmcp_server.server_discover import server_discover_result
from mcp_hangar.server.bootstrap import build_serving_mcp_server

CURRENT = {"io.mcp-hangar/validator", "io.mcp-hangar/mutator", "io.mcp-hangar/digest-pinning"}


@pytest.fixture(scope="module")
def mcp():
    return build_serving_mcp_server()


def _handshake_capabilities(mcp) -> dict:
    return lowlevel_server(mcp).get_capabilities().model_dump(mode="json", by_alias=True, exclude_none=True)


def test_every_identifier_has_a_vendor_prefix_and_a_name():
    assert set(LEGACY_DOTTED_IDS.values()) == CURRENT
    for identifier in CURRENT:
        vendor, _, name = identifier.partition("/")
        assert vendor == "io.mcp-hangar" and name and "/" not in name


def test_the_handshake_era_advertises_them_under_experimental(mcp):
    experimental = _handshake_capabilities(mcp)["experimental"]

    assert set(experimental) >= CURRENT


def test_the_dotted_forms_stay_on_the_handshake_era_marked_deprecated(mcp):
    experimental = _handshake_capabilities(mcp)["experimental"]

    for old, new in LEGACY_DOTTED_IDS.items():
        assert experimental[old]["deprecated"] is True
        assert experimental[old]["replacedBy"] == new


def test_the_modern_surface_advertises_them_under_extensions_only(mcp):
    capabilities = server_discover_result(None, mcp)["capabilities"]

    assert set(capabilities["extensions"]) >= CURRENT
    assert not set(LEGACY_DOTTED_IDS) & set(capabilities["extensions"])
    assert not set(capabilities.get("experimental") or {}) & (CURRENT | set(LEGACY_DOTTED_IDS))
