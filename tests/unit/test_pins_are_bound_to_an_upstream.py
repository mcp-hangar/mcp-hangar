"""Pins carry the identity of the upstream they were taken from (#1802).

The validator, the identity a server reports, and the way the configuration
binds a block of pins to it.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.read_models.tool_projection import ToolProjectionRegistry
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.services.digest_validator import DigestValidator
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.domain.value_objects.tool_digest import DigestEnforcement, DigestPolicy, DigestUnknownPolicy
from mcp_hangar.server.config import ConfigurationError, _register_tool_projection_block

TOOL = {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}}


class TestTheIdentityAServerReports:
    def test_a_remote_server_is_its_origin_without_credentials_path_or_query(self):
        server = McpServer("a", mode="remote", endpoint="https://user:secret@Host.Example:8443/mcp/v1?token=x")

        assert server.upstream_identity() == "remote:https://host.example:8443"

    def test_two_paths_on_one_origin_are_one_upstream(self):
        one = McpServer("a", mode="remote", endpoint="https://h.example/one").upstream_identity()
        two = McpServer("b", mode="remote", endpoint="https://h.example/two").upstream_identity()

        assert one == two

    def test_a_subprocess_is_its_executable_and_a_hash_of_the_command_line(self):
        server = McpServer("a", mode="subprocess", command=["/usr/bin/python3", "server.py", "--token", "hunter2"])

        identity = server.upstream_identity()

        assert identity.startswith("stdio:python3:")
        assert "hunter2" not in identity and "server.py" not in identity

    def test_a_different_command_line_is_a_different_upstream(self):
        one = McpServer("a", mode="subprocess", command=["node", "one.js"]).upstream_identity()
        two = McpServer("a", mode="subprocess", command=["node", "two.js"]).upstream_identity()

        assert one != two

    def test_the_server_id_is_not_part_of_it(self):
        """Renaming a server does not change what it points at."""
        one = McpServer("old-name", mode="remote", endpoint="https://h.example/mcp").upstream_identity()
        two = McpServer("new-name", mode="remote", endpoint="https://h.example/mcp").upstream_identity()

        assert one == two

    def test_a_container_is_its_image(self):
        assert McpServer("a", mode="docker", image="registry.example/tools:1.2").upstream_identity() == (
            "image:registry.example/tools:1.2"
        )


def _validate(pin: ToolDigest, upstream: str | None, enforcement: DigestEnforcement = DigestEnforcement.BLOCK):
    policy = DigestPolicy(enforcement=enforcement, unknown=DigestUnknownPolicy.BLOCK, allowlist=frozenset({pin}))
    return DigestValidator(policy).validate_tool(TOOL, "srv", "corr", upstream=upstream)


class TestTheValidator:
    sha = compute_tool_digest(TOOL).sha256

    def test_a_pin_bound_to_the_upstream_that_serves_the_tool_holds(self):
        result = _validate(ToolDigest("read_file", self.sha, upstream="stdio:node:aa"), "stdio:node:aa")

        assert result.valid and not result.blocked and result.event is None

    def test_an_identical_definition_from_another_upstream_is_a_mismatch(self):
        result = _validate(ToolDigest("read_file", self.sha, upstream="stdio:node:aa"), "stdio:node:bb")

        assert not result.valid and result.blocked
        assert (result.event.expected_upstream, result.event.observed_upstream) == ("stdio:node:aa", "stdio:node:bb")
        assert result.event.expected_digest == result.event.observed_digest == self.sha

    def test_the_mode_decides_what_a_moved_upstream_costs(self):
        pin = ToolDigest("read_file", self.sha, upstream="stdio:node:aa")

        warned = _validate(pin, "stdio:node:bb", DigestEnforcement.WARN)

        assert not warned.valid and not warned.blocked and warned.event is not None

    def test_a_pin_that_named_no_upstream_checks_the_definition_only(self):
        result = _validate(ToolDigest("read_file", self.sha), "anything:at:all")

        assert result.valid and result.event is None

    def test_a_server_that_cannot_say_what_it_is_does_not_match_a_bound_pin(self):
        result = _validate(ToolDigest("read_file", self.sha, upstream="stdio:node:aa"), None)

        assert not result.valid and result.blocked

    def test_an_ordinary_mismatch_names_no_upstream(self):
        result = _validate(ToolDigest("read_file", "0" * 64), "stdio:node:aa")

        assert result.event.expected_upstream is None and result.event.observed_upstream is None


class TestTheConfigBindsAPinBlock:
    def _register(self, block: dict) -> ToolProjectionRegistry:
        registry = ToolProjectionRegistry()
        from mcp_hangar.server import config as config_module

        original = config_module._staged_config
        try:
            config_module._staged_config = lambda: type("S", (), {"projections": registry})()  # type: ignore[assignment]
            _register_tool_projection_block("srv", block)
        finally:
            config_module._staged_config = original  # type: ignore[assignment]
        return registry

    def test_the_blocks_pins_carry_its_upstream(self):
        registry = self._register({"upstream": "stdio:node:aa", "pins": {"read_file": "a" * 64}})

        assert registry.resolve_pin("srv", "read_file", None).upstream == "stdio:node:aa"

    def test_a_tenants_pins_carry_it_too(self):
        registry = self._register(
            {"upstream": "stdio:node:aa", "tenant_overrides": {"t1": {"pins": {"read_file": "a" * 64}}}}
        )

        assert registry.resolve_pin("srv", "read_file", "t1").upstream == "stdio:node:aa"

    def test_pins_without_an_upstream_are_unbound(self):
        registry = self._register({"pins": {"read_file": "a" * 64}})

        assert registry.resolve_pin("srv", "read_file", None).upstream is None

    @pytest.mark.parametrize("bad", [5, ["x"], True, ""])
    def test_an_upstream_that_is_not_an_identity_string_is_refused(self, bad):
        with pytest.raises(ConfigurationError, match="tool_projection.upstream"):
            self._register({"upstream": bad, "pins": {"read_file": "a" * 64}})
