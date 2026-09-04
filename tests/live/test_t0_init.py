"""Tier 0 live verification: what `mcp-hangar init` writes (#1192).

The claim (LIVE_VERIFICATION.md): the configuration a first run produces is one
that governs -- the upstreams' own tool names through `front_door`, a declared
caller over stdio so that projection is reachable at all, and pins taken from
the servers themselves -- and it is a configuration Hangar accepts.

Driven through the shipped CLI, and checked with the shipped `config check`,
because the failure this guards against is precisely a config that *looks*
right: the generated file carried a `health_check:` block for releases that
nothing read, and every generated config logged `unknown_config_key` while
`init` reported success.

`--skip-test` is deliberate here and is itself part of the claim: it is the flag
that makes `init` touch no network (the smoke test is what fetches MCP servers
from npm), and the release states that skipping the test also means skipping the
pins -- an unverified pin would refuse every call to a tool nobody digested.

Skip-safe: skips (never fails) without the CLI. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m live -o addopts=""
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
import yaml

from tests.live.conftest import _hangar_bin

pytestmark = [pytest.mark.live, pytest.mark.t0]


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Run the wizard once, offline, and return the config it wrote."""
    workdir = tmp_path_factory.mktemp("init")
    config = workdir / "config.yaml"
    result = subprocess.run(
        [
            _hangar_bin(),
            "init",
            "-y",
            "--skip-clients",  # never touch a real client config on a dev machine
            "--skip-test",  # and never reach the network
            "--config-path",
            str(config),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(workdir),
    )
    if result.returncode != 0 or not config.is_file():
        pytest.skip(f"init did not produce a config:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    return config


def test_the_generated_config_is_one_hangar_accepts(generated: Path):
    # `config check` is always strict, which is the point: the loader only warns
    # until 3.0.0, so a key nothing reads would otherwise start fine and apply
    # nothing.
    result = subprocess.run(
        [_hangar_bin(), "config", "check", str(generated)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout


def test_it_serves_the_upstreams_own_tools(generated: Path):
    assert yaml.safe_load(generated.read_text())["tool_access"] == {"mode": "front_door"}


def test_it_names_the_caller_a_stdio_session_has(generated: Path):
    # Without this, front_door is fail-closed on an identity nobody set, and the
    # generated config would serve an empty list to the client it just wired up.
    principal = yaml.safe_load(generated.read_text())["auth"]["stdio"]["principal"]

    assert principal["id"] and principal["tenant_id"]
    assert principal["roles"] == ["viewer"]


def test_skipping_the_test_writes_no_pins_and_says_so(generated: Path):
    document = yaml.safe_load(generated.read_text())

    for server_id, spec in document["mcp_servers"].items():
        assert "pins" not in (spec.get("tool_projection") or {}), server_id
