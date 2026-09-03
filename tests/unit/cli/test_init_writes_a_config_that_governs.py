"""`init` writes a configuration that produces a verdict (#1192).

The generated config used to describe a fleet and enforce nothing: no
`tool_access`, no digest pins, no identity. So a first run served the `hangar_*`
meta-API to an anonymous caller and ended every tool call in `allow`, while the
project's own claim is that every call ends in a verdict.

Four decisions are pinned here, because each one is invisible in the output it
produces and easy to undo by accident:

1. the generated file carries the policy, the identity and the pins;
2. every key in it is a key Hangar reads -- checked against the real schema,
   which is how the `health_check:` block that nothing had ever read was found;
3. pins are only written for what was actually observed, so `--skip-test` writes
   none rather than inventing them;
4. the summary panel reports what ran, instead of defaulting to "All passed".
"""

from pathlib import Path

import yaml

from mcp_hangar.server.cli.services.config_file import ConfigFileManager
from mcp_hangar.server.cli.services.dependency_detector import DependencyStatus, RuntimeInfo
from mcp_hangar.server.cli.services.mcp_server_registry import get_mcp_server
from mcp_hangar.server.config_schema import validate_config

NPX_ONLY = DependencyStatus(
    npx=RuntimeInfo("npx", "/usr/bin/npx", True),
    uvx=RuntimeInfo("uvx", None, False),
    docker=RuntimeInfo("docker", None, False),
    podman=RuntimeInfo("podman", None, False),
)

PINS = {"filesystem": {"read_file": "a" * 64, "write_file": "b" * 64}}


def generate(tmp_path: Path, pins=None) -> dict:
    manager = ConfigFileManager(tmp_path / "config.yaml")
    definitions = [get_mcp_server("filesystem")]
    manager.write_initial_config(definitions, {"filesystem": {"path": "/tmp"}}, NPX_ONLY, pins=pins)
    return yaml.safe_load(manager.config_path.read_text())


class TestTheGeneratedConfigGoverns:
    def test_it_serves_the_upstreams_own_tools(self, tmp_path: Path):
        assert generate(tmp_path)["tool_access"] == {"mode": "front_door"}

    def test_it_names_the_caller_a_stdio_session_has(self, tmp_path: Path):
        # Without this, front_door is fail-closed on an identity nobody set and
        # serves zero tools (ADR-026).
        principal = generate(tmp_path)["auth"]["stdio"]["principal"]

        assert principal == {"id": "local-user", "tenant_id": "local", "roles": ["viewer"]}

    def test_it_pins_what_the_smoke_test_saw(self, tmp_path: Path):
        projection = generate(tmp_path, pins=PINS)["mcp_servers"]["filesystem"]["tool_projection"]

        assert projection["digest_enforcement"] == "block"
        assert projection["pins"] == PINS["filesystem"]

    def test_no_observation_means_no_pins(self, tmp_path: Path):
        # An unverified pin is worse than none: it would refuse every call to a
        # tool nobody digested.
        assert "tool_projection" not in generate(tmp_path)["mcp_servers"]["filesystem"]

    def test_every_key_is_one_hangar_reads(self, tmp_path: Path):
        # Asked against the real schema rather than a list repeated here. The
        # `health_check:` block the generator used to write failed exactly this,
        # on every config `init` had ever produced.
        assert validate_config(generate(tmp_path, pins=PINS)) == []

    def test_the_documentation_link_points_at_a_domain_that_resolves(self, tmp_path: Path):
        # The generated header pointed at `docs.mcp-hangar.io`, which does not
        # exist, so the one link a new user is handed went nowhere.
        generate(tmp_path)
        text = (tmp_path / "config.yaml").read_text()

        assert "https://mcp-hangar.io/docs/reference/configuration" in text
        assert "docs.mcp-hangar.io" not in text


class TestTheSummaryReportsWhatRan:
    def test_a_skipped_test_is_not_a_pass(self, capsys):
        from mcp_hangar.server.cli.commands.init import _show_completion_summary

        _show_completion_summary(
            mcp_servers=["filesystem"],
            hangar_config_path=Path("/tmp/config.yaml"),
            client_paths=[],
            backup_path=None,
            smoke_test_status="skipped",
            pinned_tools=0,
        )

        out = capsys.readouterr().out
        assert "All passed" not in out
        assert "Not run" in out

    def test_a_pass_is_reported_with_its_pins(self, capsys):
        from mcp_hangar.server.cli.commands.init import _show_completion_summary

        _show_completion_summary(
            mcp_servers=["filesystem"],
            hangar_config_path=Path("/tmp/config.yaml"),
            client_paths=[],
            backup_path=None,
            smoke_test_status="passed",
            pinned_tools=7,
        )

        out = capsys.readouterr().out
        assert "All passed" in out
        assert "7 tool(s) pinned" in out
