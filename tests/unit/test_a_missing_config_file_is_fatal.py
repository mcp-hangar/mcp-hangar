"""A configuration file that is not there stops the boot (#1650).

`mcp-hangar --config /typo.yaml serve` used to log one INFO line and boot a
built-in demo configuration -- a `math_subprocess` backend and every `hangar_*`
tool, stop, load and reload_config included -- with none of the operator's pins,
policies or auth. A typo in a deployment's config path served an ungoverned
gateway.

Now no file means no boot, whichever rule chose the path: a flag, `$MCP_CONFIG`,
a `config_path` handed to `bootstrap()` or the facade, or the implicit default
(`./config.yaml`, then `~/.config/mcp-hangar/config.yaml`). The message names the
path and where it came from; for the implicit default it says to run `init`.

The CLI cases run the real entry points in a subprocess: the refusal has to
happen before anything is served, and only a process exit shows that.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_hangar.server.cli.config_path import MissingConfigFileError

#: The typer CLI, as the `mcp-hangar` console script runs it.
CLI = [sys.executable, "-c", "from mcp_hangar.server.cli import cli_main; cli_main()"]
#: The legacy argparse entry point.
MODULE = [sys.executable, "-m", "mcp_hangar.server"]


def _env(home: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("MCP_CONFIG", "MCP_MODE")}
    # HOME here, or a developer's own ~/.config/mcp-hangar/config.yaml would be read.
    # COLUMNS, so rich does not wrap a long path across lines.
    env.update(HOME=str(home), COLUMNS="1000", **extra)
    return env


def _run(argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as e:
        # A process still running after a minute is a process that is serving.
        pytest.fail(f"{argv} was still running after 60s, so it booted: {e.stderr!r}")


@pytest.mark.security
@pytest.mark.parametrize(
    ("argv", "use_env", "named"),
    [
        pytest.param([*CLI, "--config", "{missing}", "serve"], False, "named on the command line", id="global-flag"),
        pytest.param([*CLI, "serve", "--config", "{missing}"], False, "named on the command line", id="serve-flag"),
        pytest.param([*CLI, "--config", "{missing}"], False, "named on the command line", id="bare-cli"),
        pytest.param([*CLI, "serve"], True, "named by $MCP_CONFIG", id="env"),
        pytest.param([*MODULE, "--config", "{missing}"], False, "named on the command line", id="python-m"),
        pytest.param([*MODULE], True, "named by $MCP_CONFIG", id="python-m-env"),
    ],
)
def test_an_explicit_path_that_does_not_exist_is_fatal(tmp_path, argv, use_env, named):
    missing = tmp_path / "typo.yaml"
    argv = [a.replace("{missing}", str(missing)) for a in argv]
    env = _env(tmp_path, **({"MCP_CONFIG": str(missing)} if use_env else {}))

    result = _run(argv, tmp_path, env)

    assert result.returncode == 1, result.stderr
    assert f"{missing} ({named}) does not exist" in result.stderr
    assert "mcp_registry_ready" not in result.stdout + result.stderr
    assert "config_not_found_using_default" not in result.stdout + result.stderr


@pytest.mark.security
@pytest.mark.parametrize("argv", [[*CLI, "--config", "{dir}", "serve"], [*MODULE, "--config", "{dir}"]])
def test_a_directory_is_not_a_configuration_file(tmp_path, argv):
    argv = [a.replace("{dir}", str(tmp_path)) for a in argv]

    result = _run(argv, tmp_path, _env(tmp_path))

    assert result.returncode == 1, result.stderr
    assert f"{tmp_path} (named on the command line) is not a regular file" in result.stderr


@pytest.mark.security
@pytest.mark.parametrize("argv", [[*CLI], [*CLI, "serve"], [*MODULE]], ids=["bare-cli", "serve", "python-m"])
def test_no_configuration_anywhere_is_fatal_and_points_at_init(tmp_path, argv):
    """The implicit default: no flag, no env, no ./config.yaml, no file from `init`."""
    result = _run(argv, tmp_path, _env(tmp_path))

    assert result.returncode == 1, result.stderr
    assert "No configuration file" in result.stderr
    assert "mcp-hangar init" in result.stderr
    assert "mcp_registry_ready" not in result.stdout + result.stderr


class TestInProcess:
    """`bootstrap()` and the facade refuse the same way the CLI does."""

    @pytest.mark.security
    def test_bootstrap_refuses_a_missing_config_path(self, tmp_path):
        from mcp_hangar.server.bootstrap import bootstrap

        missing = tmp_path / "typo.yaml"
        with pytest.raises(MissingConfigFileError, match="passed as config_path") as raised:
            bootstrap(config_path=str(missing))

        assert raised.value.details == {"path": str(missing), "source": "argument"}

    @pytest.mark.security
    async def test_the_facade_refuses_a_missing_config_path(self, tmp_path):
        from mcp_hangar.facade import Hangar

        hangar = Hangar.from_config(tmp_path / "typo.yaml")
        try:
            with pytest.raises(MissingConfigFileError, match="does not exist"):
                await hangar.start()
        finally:
            await hangar.stop()

    def test_with_no_path_the_file_init_writes_is_read(self, tmp_path, monkeypatch):
        """The implicit default still works when there is a file to find."""
        from mcp_hangar.server.config import load_configuration

        monkeypatch.delenv("MCP_CONFIG", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        written = tmp_path / ".config" / "mcp-hangar" / "config.yaml"
        written.parent.mkdir(parents=True)
        written.write_text("mcp_servers: {}\n", encoding="utf-8")

        assert load_configuration(None, load_servers=False)["mcp_servers"] == {}

    def test_with_no_path_and_no_file_it_refuses(self, tmp_path, monkeypatch):
        from mcp_hangar.server.config import load_configuration

        monkeypatch.delenv("MCP_CONFIG", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.chdir(tmp_path)

        with pytest.raises(MissingConfigFileError, match="mcp-hangar init"):
            load_configuration(None, load_servers=False)
