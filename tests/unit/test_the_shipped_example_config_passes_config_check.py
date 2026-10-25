"""The `config.yaml.example` users are told to copy passes `mcp-hangar config check` (#2077).

It set `execution.default_provider_concurrency`, a pre-rename key nothing reads,
so the shipped example failed the product's own check and was refused under
`HANGAR_CONFIG_STRICT=1`.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from mcp_hangar.server.cli.main import app

EXAMPLE = Path(__file__).resolve().parents[2] / "config.yaml.example"


def test_config_check_accepts_the_example() -> None:
    result = CliRunner().invoke(app, ["config", "check", str(EXAMPLE)])

    assert result.exit_code == 0, result.output
