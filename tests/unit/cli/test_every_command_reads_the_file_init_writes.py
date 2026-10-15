"""Every command reads the same configuration file, by one rule (#1657).

`init`, `add` and `remove` used `~/.config/mcp-hangar/config.yaml`; `status`
searched that and then `./config.yaml`; `pin`, `config check` and `serve` read
`$MCP_CONFIG` or `./config.yaml`. So a new user's `pin --check` failed straight
after `init`, and `pin` and `config check` ignored the global `--config`.

The rule now, highest first: a path on the command line (the command's own,
then the global `--config`), `$MCP_CONFIG`, `./config.yaml` if it exists, the
file `init` writes. Each test runs with `HOME` and the working directory in
`tmp_path`, so the developer's own files are never read.

The servers are not started: `pin`'s observation and `serve`'s `run_server`
are replaced by stubs that record the path they were handed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog
import yaml
from typer.testing import CliRunner

from mcp_hangar.server.cli.commands import init as init_module
from mcp_hangar.server.cli.commands import pin as pin_module
from mcp_hangar.server.cli.config_path import ConfigPathSource, resolve_config_path, user_config_path
from mcp_hangar.server.cli.main import app
from mcp_hangar.server.cli.services.dependency_detector import DependencyStatus, RuntimeInfo
from mcp_hangar.server.cli.services.mcp_clients import HANGAR_ENTRY_NAME

NPX_ONLY = DependencyStatus(
    npx=RuntimeInfo("npx", "/usr/bin/npx", True),
    uvx=RuntimeInfo("uvx", None, False),
    docker=RuntimeInfo("docker", None, False),
    podman=RuntimeInfo("podman", None, False),
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_logging():
    """`pin` points logging at the runner's stream, which closes; put the process-global state back."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    config = structlog.get_config()
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.configure(**config)


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory and a HOME of the test's own, with no `MCP_CONFIG`."""
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MCP_CONFIG", raising=False)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(init_module, "detect_dependencies", lambda: NPX_ONLY)
    return cwd


@pytest.fixture
def pin_reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """The file each `pin` run asked the servers about."""
    seen: list[Path] = []

    def observe(path, mcp_server_ids=None):
        seen.append(Path(path))
        return []  # "configures no MCP servers": exit 2, after the file was found

    monkeypatch.setattr(pin_module, "observe_digests", observe)
    return seen


def _write(path: Path, document: dict | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document or {"mcp_servers": {}}))
    return path


def _served_config(runner: CliRunner, args: list[str]) -> str:
    with patch("mcp_hangar.server.lifecycle.run_server") as run_server:
        result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return str(run_server.call_args.args[0].config_path)


def _json(output: str) -> dict:
    # Rich wraps long lines at the terminal width, inside strings too; a path has no spaces to lose.
    return dict(json.loads(output.replace("\n", "")))


def _init(runner: CliRunner, *extra: str) -> None:
    result = runner.invoke(app, ["init", "-y", "--skip-test", "--bundle", "starter", *extra])
    assert result.exit_code == 0, result.output


class TestTheOrder:
    def test_a_command_flag_outranks_the_global_flag(self, work):
        assert resolve_config_path("local.yaml", "global.yaml").path == Path("local.yaml")

    def test_a_flag_outranks_the_environment(self, work, monkeypatch):
        monkeypatch.setenv("MCP_CONFIG", "env.yaml")

        resolved = resolve_config_path(None, "global.yaml")

        assert resolved.path == Path("global.yaml")
        assert resolved.source is ConfigPathSource.FLAG

    def test_the_environment_outranks_a_file_in_the_working_directory(self, work, monkeypatch):
        _write(work / "config.yaml")
        monkeypatch.setenv("MCP_CONFIG", "env.yaml")

        assert resolve_config_path().source is ConfigPathSource.ENV

    def test_a_file_in_the_working_directory_outranks_the_init_path(self, work):
        _write(user_config_path())
        _write(work / "config.yaml")

        resolved = resolve_config_path()

        assert resolved.path == work / "config.yaml"
        assert resolved.path.is_absolute()
        assert not resolved.explicit

    def test_with_nothing_else_it_is_the_file_init_writes(self, work):
        resolved = resolve_config_path()

        assert resolved.path == Path.home() / ".config" / "mcp-hangar" / "config.yaml"
        assert resolved.source is ConfigPathSource.USER


class TestInitThenEveryCommand:
    def test_pin_check_reads_the_file_init_wrote(self, runner, work, pin_reads):
        _init(runner, "--skip-clients")

        result = runner.invoke(app, ["pin", "--check"])

        assert pin_reads == [user_config_path()], result.output
        assert "No such configuration file" not in result.output

    @pytest.mark.parametrize("args", [["serve"], []], ids=["serve", "bare mcp-hangar"])
    def test_serve_reads_the_file_init_wrote(self, runner, work, args):
        _init(runner, "--skip-clients")

        assert _served_config(runner, args) == str(user_config_path())

    def test_status_and_config_check_read_the_file_init_wrote(self, runner, work):
        _init(runner, "--skip-clients")

        status = runner.invoke(app, ["--json", "status"])
        check = runner.invoke(app, ["config", "check"])

        assert _json(status.output)["config_path"] == str(user_config_path())
        assert str(user_config_path()) in check.output.replace("\n", "")

    def test_a_config_yaml_in_the_working_directory_is_what_init_and_serve_use(self, runner, work, pin_reads):
        # The compatibility rule: a setup that ran `serve` beside its own
        # `config.yaml` keeps running it, and `init` there edits that file
        # instead of writing one `serve` would not read.
        local = _write(work / "config.yaml", {"mcp_servers": {"old": {"mode": "subprocess", "command": ["x"]}}})

        _init(runner, "--client", "cursor-project")
        runner.invoke(app, ["pin", "--check"])

        assert not user_config_path().exists()
        assert "old" not in yaml.safe_load(local.read_text())["mcp_servers"]
        assert pin_reads == [local]
        assert _served_config(runner, ["serve"]) == str(local)
        # Written into the client's config absolute: the client starts Hangar
        # from a working directory of its own.
        entry = json.loads((work / ".cursor" / "mcp.json").read_text())["mcpServers"][HANGAR_ENTRY_NAME]
        assert entry["args"] == ["--config", str(local), "serve"]


class TestTheGlobalConfigFlag:
    def test_pin_honours_it(self, runner, work, pin_reads, tmp_path):
        config = _write(tmp_path / "elsewhere.yaml")

        runner.invoke(app, ["--config", str(config), "pin", "--check"])

        assert pin_reads == [config]

    def test_config_check_honours_it(self, runner, work, tmp_path):
        # #1682: only the positional path used to work.
        config = _write(tmp_path / "elsewhere.yaml", {"mcp_servers": {}, "not_a_key": 1})

        result = runner.invoke(app, ["--config", str(config), "config", "check"])

        assert result.exit_code == 1, result.output
        assert "not_a_key" in result.output

    def test_it_outranks_mcp_config_for_serve(self, runner, work, monkeypatch):
        # `serve --config` used to read MCP_CONFIG itself, so the environment
        # beat a global flag the user had typed.
        # Both files exist: `serve` refuses a path that does not (#1650).
        from_env, from_flag = work / "env.yaml", work / "flag.yaml"
        for named in (from_env, from_flag):
            named.write_text("mcp_servers: {}\n", encoding="utf-8")
        monkeypatch.setenv("MCP_CONFIG", str(from_env))

        assert _served_config(runner, ["--config", str(from_flag), "serve"]) == str(from_flag)
        assert _served_config(runner, ["serve"]) == str(from_env)

    def test_status_does_not_fall_back_past_a_named_file(self, runner, work, tmp_path):
        _write(user_config_path(), {"mcp_servers": {"other": {"mode": "subprocess"}}})
        missing = tmp_path / "missing.yaml"

        status = _json(runner.invoke(app, ["--json", "--config", str(missing), "status"]).output)

        assert status["mcp_servers"] == []
        assert str(missing) in status["error"]
