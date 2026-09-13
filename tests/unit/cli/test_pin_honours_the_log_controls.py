"""`mcp-hangar pin` honours the controls that turn its log output down (#1236).

`pin` never set up logging. Every stdio-client line reached the terminal at
every level, around the two or three lines that are the command's answer, and
none of the three documented controls changed that: the global `--quiet`,
`MCP_LOG_LEVEL`, or the config file's `logging.level`. The last one *was*
honoured by `serve` with the same file.

The server side is stubbed here so the level filter can be asserted in-process
and fast: the stub logs through the real `mcp_hangar.stdio_client` logger, the
same name the real client uses, at the same levels. The real subprocess
handshake is covered in `tests/integration/test_pin_output_is_quiet_and_split.py`.

Each absence assertion is paired with `test_without_a_control_the_client_lines_show`.
Without it, a stub that logged nothing would pass every one of them.
"""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
import structlog
from typer.testing import CliRunner
import yaml

from mcp_hangar import gc as gc_module
from mcp_hangar.logging_config import get_logger
from mcp_hangar.server.cli.commands import pin as pin_module
from mcp_hangar.server.cli.main import app
from mcp_hangar.server.cli.services.pinning import Observation
from mcp_hangar.server.lifecycle import resolve_logging_settings

PINNED = "a" * 64
SERVED = "b" * 64

#: What the stub logs, at the levels the real client logs them.
CLIENT_INFO = "stdio_client_process_exited"
CLIENT_DEBUG = "stdio_client_eof_on_stdout"
CLIENT_ERROR = "stdio_client_reader_error"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_logging(monkeypatch):
    """Each run calls `setup_logging`, which rewrites process-global state; put it back."""
    for name in ("MCP_LOG_LEVEL", "MCP_CONFIG", "MCP_JSON_LOGS"):
        monkeypatch.delenv(name, raising=False)
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    config = structlog.get_config()
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.configure(**config)


@pytest.fixture
def served(monkeypatch):
    """Stand in for the servers: log like the stdio client, then answer with `SERVED`."""

    def observe(path, mcp_server_ids=None):
        # A fresh logger, not the module's: a proxy cached by an earlier test
        # would keep that test's configuration and bypass this one's.
        client = get_logger("mcp_hangar.stdio_client")
        client.debug(CLIENT_DEBUG, expected=True)
        client.info(CLIENT_INFO, exit_code=-15, expected=True)
        client.error(CLIENT_ERROR, error="probe")
        return [Observation("demo", {"echo": SERVED})]

    monkeypatch.setattr(pin_module, "observe_digests", observe)


def write_config(tmp_path: Path, *, pin: str | None = PINNED, level: str | None = None) -> Path:
    spec: dict = {"mode": "subprocess", "command": ["true"]}
    if pin is not None:
        spec["tool_projection"] = {"pins": {"echo": pin}}
    document: dict = {"mcp_servers": {"demo": spec}}
    if level is not None:
        document["logging"] = {"level": level}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(document))
    return path


def test_without_a_control_the_client_lines_show(runner, tmp_path, served):
    # The default is INFO, as on `serve`: info and error show, debug does not.
    config = write_config(tmp_path)

    result = runner.invoke(app, ["pin", "--config", str(config), "--check"])

    assert result.exit_code == 1, result.output
    assert CLIENT_INFO in result.stderr
    assert CLIENT_ERROR in result.stderr
    assert CLIENT_DEBUG not in result.stderr
    assert "stdio_client" not in result.stdout


@pytest.mark.parametrize(
    ("argv", "env", "file_level"),
    [
        pytest.param(["--quiet", "pin"], {}, None, id="global --quiet"),
        pytest.param(["pin", "--quiet"], {}, None, id="pin --quiet"),
        pytest.param(["pin"], {"MCP_LOG_LEVEL": "ERROR"}, None, id="MCP_LOG_LEVEL"),
        pytest.param(["pin"], {}, "ERROR", id="logging.level"),
    ],
)
def test_each_control_silences_the_client_info_and_debug_lines(runner, tmp_path, served, argv, env, file_level):
    config = write_config(tmp_path, level=file_level)

    result = runner.invoke(app, [*argv, "--config", str(config), "--check"], env=env)

    assert result.exit_code == 1, result.output
    assert CLIENT_INFO not in result.stderr
    assert CLIENT_DEBUG not in result.stderr
    # An error is not non-essential: it is still reported, on stderr.
    assert CLIENT_ERROR in result.stderr
    # And the answer is untouched by any of them.
    assert "drift demo.echo" in result.stdout


def test_quiet_is_a_floor_not_a_level(runner, tmp_path, served):
    # `--quiet` wins over a level that would let more through, from any source.
    config = write_config(tmp_path, level="DEBUG")

    result = runner.invoke(app, ["--quiet", "pin", "--config", str(config), "--log-level", "DEBUG", "--check"])

    assert result.exit_code == 1, result.output
    assert CLIENT_DEBUG not in result.stderr
    assert CLIENT_INFO not in result.stderr


@pytest.mark.parametrize(
    ("argv", "env", "file_level", "debug_shows"),
    [
        pytest.param(["--log-level", "DEBUG"], {}, "ERROR", True, id="flag beats file"),
        pytest.param([], {"MCP_LOG_LEVEL": "DEBUG"}, "ERROR", True, id="env beats file"),
        pytest.param(["--log-level", "ERROR"], {"MCP_LOG_LEVEL": "DEBUG"}, None, False, id="flag beats env"),
        pytest.param([], {}, "DEBUG", True, id="file beats default"),
    ],
)
def test_the_levels_resolve_in_the_same_order_as_serve(runner, tmp_path, served, argv, env, file_level, debug_shows):
    config = write_config(tmp_path, level=file_level)

    result = runner.invoke(app, ["pin", "--config", str(config), "--check", *argv], env=env)

    assert result.exit_code == 1, result.output
    assert (CLIENT_DEBUG in result.stderr) is debug_shows, result.stderr


@pytest.mark.parametrize(
    ("log_level", "file_level", "expected"),
    [
        ("INFO", None, "INFO"),
        ("INFO", "error", "ERROR"),  # INFO cannot be told apart from the default
        ("WARNING", "ERROR", "WARNING"),
        ("debug", "ERROR", "DEBUG"),
    ],
)
def test_the_resolution_serve_and_pin_share(tmp_path, log_level, file_level, expected):
    config = write_config(tmp_path, level=file_level)

    assert resolve_logging_settings(str(config), log_level=log_level).level == expected


def test_the_drift_report_is_on_stdout_and_not_on_stderr(runner, tmp_path, served):
    config = write_config(tmp_path)

    result = runner.invoke(app, ["pin", "--config", str(config), "--check"])

    assert result.exit_code == 1, result.output
    assert "drift demo.echo" in result.stdout
    assert PINNED in result.stdout and SERVED in result.stdout
    assert "drift" not in result.stderr


class TestWhatQuietKeeps:
    def test_drift_still_prints(self, runner, tmp_path, served):
        config = write_config(tmp_path)

        result = runner.invoke(app, ["--quiet", "pin", "--config", str(config), "--check"])

        assert result.exit_code == 1, result.output
        assert "drift demo.echo" in result.stdout

    def test_a_clean_check_prints_nothing(self, runner, tmp_path, served):
        # Exit 0 is the answer; the confirmation line only restates it.
        config = write_config(tmp_path, pin=SERVED)

        loud = runner.invoke(app, ["pin", "--config", str(config), "--check"])
        quiet = runner.invoke(app, ["--quiet", "pin", "--config", str(config), "--check"])

        assert loud.exit_code == quiet.exit_code == 0
        assert "still matches" in loud.stdout
        assert quiet.stdout == ""

    def test_digests_still_print(self, runner, tmp_path, served):
        config = write_config(tmp_path)

        result = runner.invoke(app, ["--quiet", "pin", "--config", str(config)])

        assert result.exit_code == 0, result.output
        assert yaml.safe_load(result.stdout) == {"demo": {"echo": SERVED}}

    def test_a_write_says_nothing_but_still_writes(self, runner, tmp_path, served):
        config = write_config(tmp_path)

        result = runner.invoke(app, ["--quiet", "pin", "--config", str(config), "--write"])

        assert result.exit_code == 0, result.output
        assert result.stdout == ""
        assert yaml.safe_load(config.read_text())["mcp_servers"]["demo"]["tool_projection"]["pins"] == {"echo": SERVED}

    def test_json_is_never_trimmed(self, runner, tmp_path, served):
        config = write_config(tmp_path, pin=SERVED)

        result = runner.invoke(app, ["--quiet", "pin", "--config", str(config), "--check", "--json"])

        assert result.exit_code == 0, result.output
        assert '"drift": []' in result.stdout


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--quiet", "pin"], id="global --quiet"),
        pytest.param(["-q", "pin"], id="global -q"),
        pytest.param(["pin", "-q"], id="pin -q"),
    ],
)
def test_the_issue_repro_is_silent(runner, tmp_path, served, argv):
    # `mcp-hangar --quiet pin --config demo.yaml --write`, the repro from #1236.
    # The global flag, placed before the subcommand, must reach `pin` exactly as
    # `pin -q` does.
    config = write_config(tmp_path)

    result = runner.invoke(app, [*argv, "--config", str(config), "--write"])

    assert result.exit_code == 0, result.output
    assert CLIENT_INFO not in result.stderr
    assert CLIENT_DEBUG not in result.stderr
    assert result.stdout == ""


def test_a_usage_error_is_on_stderr(runner, tmp_path):
    result = runner.invoke(app, ["pin", "--config", str(tmp_path / "missing.yaml"), "--check"])

    assert result.exit_code == 2
    assert "No such configuration file" in result.stderr
    assert result.stdout == ""


def test_importing_the_cli_logs_nothing():
    # Anything logged at import runs before a command can set up logging, so no
    # control reaches it. gc.py's watchdog notice was one such line, on every
    # invocation including `--help` and `pin`; it now fires where hot reload
    # starts (below). Checked in a subprocess: the claim is about a fresh
    # import, which an in-process check cannot give after other tests.
    result = subprocess.run(
        [sys.executable, "-c", "import mcp_hangar.server.cli.main"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-500:]
    assert result.stderr == ""
    assert result.stdout == ""


WATCHDOG_NOTICE = "watchdog package not installed, config file watching will use polling"


class TestTheWatchdogNotice:
    """The notice moved from gc.py's import to where hot reload starts; it did not go away.

    It tells a `serve` operator why config reload polls. It never had anything
    to say to `pin` or `--help`, which only import the module.
    """

    @pytest.fixture
    def worker_log(self, monkeypatch) -> Mock:
        log = Mock()
        monkeypatch.setattr(gc_module, "logger", log)
        monkeypatch.setattr(gc_module, "WATCHDOG_AVAILABLE", False)
        return log

    @staticmethod
    def start_hot_reload(tmp_path: Path, *, use_watchdog: bool) -> None:
        worker = gc_module.ConfigReloadWorker(
            str(write_config(tmp_path)), command_bus=Mock(), interval_s=1, use_watchdog=use_watchdog
        )
        worker.start()
        worker.stop()

    @staticmethod
    def said_it(log: Mock) -> bool:
        return any(call.args[:1] == (WATCHDOG_NOTICE,) for call in log.debug.call_args_list)

    def test_hot_reload_that_asks_for_watchdog_says_it_will_poll(self, tmp_path, worker_log):
        self.start_hot_reload(tmp_path, use_watchdog=True)

        assert self.said_it(worker_log)

    def test_hot_reload_that_did_not_ask_says_nothing(self, tmp_path, worker_log):
        self.start_hot_reload(tmp_path, use_watchdog=False)

        assert not self.said_it(worker_log)
