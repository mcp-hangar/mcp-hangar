"""Tests for SEP-2133 governance extension advertisement.

Verify that Hangar advertises ONLY governance it actually enforces
(interceptor validator/mutator + tool-digest pinning) under reverse-DNS
``io.mcp-hangar.*`` keys in ``capabilities.experimental``, that each entry
is marked opt-in / off by default, and that dormant task governance is NOT
advertised (guard against dishonest advertising).
"""

from unittest.mock import Mock

import pytest

from mcp_hangar.fastmcp_server import HangarFunctions, MCPServerFactory
from mcp_hangar.fastmcp_server.governance_extensions import (
    DIGEST_PINNING_ID,
    governance_experimental_capabilities,
)
from mcp_hangar.fastmcp_server.interceptors_list import MUTATOR_ID, VALIDATOR_ID

EXPECTED_KEYS = {VALIDATOR_ID, MUTATOR_ID, DIGEST_PINNING_ID}


@pytest.fixture
def mock_registry():
    """Minimal control-plane functions for booting a factory."""
    return HangarFunctions(
        list=Mock(return_value={"mcp_servers": []}),
        start=Mock(return_value={"status": "started"}),
        stop=Mock(return_value={"status": "stopped"}),
        invoke=Mock(return_value={"result": 42}),
        tools=Mock(return_value={"tools": []}),
        details=Mock(return_value={"mcp_server": "test"}),
        health=Mock(return_value={"status": "healthy"}),
    )


class TestGovernanceExperimentalMap:
    """Unit tests for the descriptor helper (no server boot required)."""

    def test_advertises_reverse_dns_governance_keys(self):
        caps = governance_experimental_capabilities()
        assert set(caps) == EXPECTED_KEYS
        for key in EXPECTED_KEYS:
            assert key.startswith("io.mcp-hangar."), key

    def test_every_entry_is_opt_in_and_off_by_default(self):
        caps = governance_experimental_capabilities()
        for key, descriptor in caps.items():
            assert descriptor["optIn"] is True, key
            assert descriptor["enabledByDefault"] is False, key

    def test_every_entry_declares_the_sep_and_a_description(self):
        caps = governance_experimental_capabilities()
        for key, descriptor in caps.items():
            assert descriptor["spec"] == "SEP-2133", key
            assert descriptor["description"], key

    def test_task_governance_is_not_advertised(self):
        """Dormant task governance (ADR-008) must never be advertised."""
        caps = governance_experimental_capabilities()
        for key in caps:
            assert "task" not in key.lower(), key
        for descriptor in caps.values():
            assert "task" not in descriptor.get("type", "").lower()
        # No stray reverse-DNS key mentioning tasks slipped in.
        assert not any(k for k in caps if k.endswith(".task") or k.endswith(".tasks"))


class TestAdvertisedServerCapabilities:
    """End-to-end: the built server advertises the governance extensions."""

    def _experimental(self, mock_registry):
        factory = MCPServerFactory(mock_registry)
        server = factory.create_server()
        init_options = server._mcp_server.create_initialization_options()
        return init_options.capabilities.experimental or {}

    def test_capabilities_experimental_contains_governance_keys(self, mock_registry):
        experimental = self._experimental(mock_registry)
        for key in EXPECTED_KEYS:
            assert key in experimental, key

    def test_advertised_entries_are_opt_in_off_by_default(self, mock_registry):
        experimental = self._experimental(mock_registry)
        for key in EXPECTED_KEYS:
            descriptor = experimental[key]
            assert descriptor["optIn"] is True, key
            assert descriptor["enabledByDefault"] is False, key

    def test_advertised_capabilities_do_not_include_task_governance(self, mock_registry):
        experimental = self._experimental(mock_registry)
        assert not any("task" in key.lower() for key in experimental), experimental.keys()
