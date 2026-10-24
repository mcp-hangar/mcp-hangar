"""A digest pin the loader cannot read does not leave its tool unpinned (#2036).

Dropping it with a warning served the tool unpinned while the gateway booted, so
a typo switched the rug-pull control off. Two spellings of a correct digest --
uppercase, and the `sha256:` prefix key pins use -- now pin the tool. Anything
else that is not a digest pins it to a value no definition hashes to, so the
tool is withheld under `block` until the pin is fixed. A block that is not a
mapping names no tool to hold back and is refused, like its neighbouring keys.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mcp_hangar.application.read_models.tool_projection import ToolProjectionRegistry
from mcp_hangar.domain.value_objects.tool_digest import DigestEnforcement
from mcp_hangar.server import config as config_module
from mcp_hangar.server.config import ConfigurationError, _register_tool_projection_block

GOOD = "ab" * 32


def _register(block: dict) -> ToolProjectionRegistry:
    registry = ToolProjectionRegistry()
    original = config_module._staged_config
    try:
        config_module._staged_config = lambda: type("S", (), {"projections": registry})()  # type: ignore[assignment]
        _register_tool_projection_block("srv", block)
    finally:
        config_module._staged_config = original  # type: ignore[assignment]
    return registry


class TestASpellingOfTheRightDigestPins:
    @pytest.mark.parametrize("spelled", [GOOD.upper(), f"sha256:{GOOD}", f"SHA256:{GOOD.upper()}", f" {GOOD} "])
    def test_it_pins_the_digest_it_names(self, spelled) -> None:
        registry = _register({"pins": {"read_file": spelled}})

        assert registry.resolve_pin("srv", "read_file", None).sha256 == GOOD

    def test_a_tenants_pin_too(self) -> None:
        registry = _register({"tenant_overrides": {"t1": {"pins": {"read_file": f"sha256:{GOOD}"}}}})

        assert registry.resolve_pin("srv", "read_file", "t1").sha256 == GOOD


class TestAPinThatIsNotADigestWithholdsItsTool:
    @pytest.mark.parametrize("bad", ["ab" * 31, "zz" * 32, 12345, True, ["x"], ""])
    def test_the_tool_reads_as_drifted_under_block(self, bad) -> None:
        registry = _register({"digest_enforcement": "block", "pins": {"read_file": bad, "list_dir": GOOD}})

        assert registry.resolve_pin("srv", "read_file", None) is not None
        assert registry.drifted_pin("read_file", GOOD, None, ("srv",)) is not None
        assert registry.drifted_pin("list_dir", GOOD, None, ("srv",)) is None

    def test_it_is_logged_as_an_error_naming_the_tool(self) -> None:
        from structlog.testing import capture_logs

        with capture_logs() as logs:
            _register({"pins": {"read_file": "nothex"}})

        assert any(
            entry["event"] == "invalid_config_digest_pin"
            and entry["log_level"] == "error"
            and entry["tool"] == "read_file"
            for entry in logs
        ), logs

    def test_enforcement_still_decides_what_drift_costs(self) -> None:
        registry = _register({"digest_enforcement": "audit", "pins": {"read_file": "nothex"}})

        assert registry.digest_enforcement("srv") is DigestEnforcement.AUDIT


class TestABlockThatIsNotAMappingIsRefused:
    @pytest.mark.parametrize(
        "block",
        [
            {"pins": ["read_file"]},
            {"pins": "abc"},
            {"tenant_overrides": ["t1"]},
            {"tenant_overrides": {"t1": ["x"]}},
            {"tenant_overrides": {"t1": {"pins": ["x"]}}},
        ],
    )
    def test_at_load(self, block) -> None:
        with pytest.raises(ConfigurationError, match="tool_projection"):
            _register(block)

    @pytest.mark.parametrize("block", [{"pins": None}, {"tenant_overrides": None}, {"tenant_overrides": {"t1": None}}])
    def test_an_empty_yaml_block_is_still_nothing(self, block) -> None:
        _register(block)

    def test_by_config_check(self, tmp_path: Path) -> None:
        from mcp_hangar.server.cli.main import app

        path = tmp_path / "config.yaml"
        path.write_text(
            "mcp_servers:\n  srv:\n    mode: subprocess\n    command: [python]\n"
            "    tool_projection:\n      pins: [read_file]\n"
        )

        result = CliRunner().invoke(app, ["config", "check", str(path)])

        assert result.exit_code == 1, result.output
        assert "tool_projection.pins" in result.output
