"""The HTTP graceful-shutdown bound is a config key; unset it is uvicorn's own default (#1447).

`run_http` handed uvicorn no `timeout_graceful_shutdown`, so the bound was
whatever uvicorn defaults to and nothing an operator could set. These drive the
real `run_http` into the real `uvicorn.Config`: only the serve is stubbed out,
so the value asserted is the one the server would have run with.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs
import uvicorn
import yaml

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.server.bootstrap import ApplicationContext
from mcp_hangar.server.config import (
    apply_configuration,
    check_process_config,
    http_graceful_shutdown_timeout,
    load_config_from_file,
)
from mcp_hangar.server.config_schema import ConfigSchemaError, validate_config
from mcp_hangar.server.lifecycle import ServerLifecycle

KEY = "graceful_shutdown_timeout_s"

#: What the installed uvicorn does when it is not told: `None`, a stop that
#: waits for in-flight requests without a bound.
UVICORN_DEFAULT = inspect.signature(uvicorn.Config).parameters["timeout_graceful_shutdown"].default

BAD_VALUES = {
    "zero": 0,
    "negative": -5,
    "a bool": True,
    "a fraction": 1.5,
    "a string": "90",
    "a list": [90],
}


def _serve(config: dict[str, Any]) -> tuple[uvicorn.Config, list[dict[str, Any]]]:
    """Run the real `run_http` on `config`; return the `uvicorn.Config` it built, and its log."""
    runtime = MagicMock()
    runtime.repository.get_all_ids.return_value = []
    context = ApplicationContext(runtime=runtime, mcp_server=MagicMock(), config=config)

    real_config = uvicorn.Config
    built: list[uvicorn.Config] = []

    def build(*args: Any, **kwargs: Any) -> uvicorn.Config:
        built.append(real_config(*args, **kwargs))
        return built[-1]

    def never_serve(coro: Any, *args: Any, **kwargs: Any) -> None:
        coro.close()

    with (
        patch("uvicorn.Config", side_effect=build),
        patch("asyncio.run", side_effect=never_serve),
        capture_logs() as logs,
    ):
        ServerLifecycle(context).run_http("127.0.0.1", 9000)

    assert len(built) == 1
    return built[0], logs


def _started(logs: list[dict[str, Any]]) -> dict[str, Any]:
    [line] = [line for line in logs if line["event"] == "starting_http_server"]
    return line


def _write(tmp_path: Path, config: dict[str, Any]) -> str:
    path = tmp_path / "hangar.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return str(path)


class TestUvicornGetsTheBound:
    def test_the_configured_bound_reaches_uvicorn(self) -> None:
        config, logs = _serve({"mcp_servers": {}, "http": {KEY: 90}})

        assert config.timeout_graceful_shutdown == 90
        assert _started(logs)[KEY] == 90

    def test_the_bound_from_a_file_reaches_uvicorn(self, tmp_path, monkeypatch) -> None:
        """The file is read and checked the way `serve` reads it, strict schema included."""
        monkeypatch.setenv("HANGAR_CONFIG_STRICT", "1")
        full_config = load_config_from_file(_write(tmp_path, {"mcp_servers": {}, "http": {KEY: 45}}))

        config, _ = _serve(full_config)

        assert config.timeout_graceful_shutdown == 45

    @pytest.mark.parametrize(
        "config",
        [{"mcp_servers": {}}, {"mcp_servers": {}, "http": {}}, {"mcp_servers": {}, "http": {KEY: None}}],
        ids=["no http section", "an empty http section", "an explicit null"],
    )
    def test_unset_uvicorn_gets_its_own_default(self, config) -> None:
        built, logs = _serve(config)

        assert UVICORN_DEFAULT is None, "uvicorn changed its default; UPGRADE.md and the chart describe None"
        assert built.timeout_graceful_shutdown == UVICORN_DEFAULT
        assert _started(logs)[KEY] is None


class TestABadBoundIsRefused:
    @pytest.mark.parametrize("value", BAD_VALUES.values(), ids=list(BAD_VALUES))
    @pytest.mark.parametrize("strict", ["", "1"], ids=["default", "strict"])
    def test_a_bad_value_refuses_the_configuration(self, value, strict, monkeypatch) -> None:
        """Refused before any section is applied, with or without strict mode."""
        monkeypatch.setenv("HANGAR_CONFIG_STRICT", strict)

        with pytest.raises(ConfigurationError, match="http.graceful_shutdown_timeout_s"):
            apply_configuration({"mcp_servers": {}, "http": {KEY: value}}, source="config_dict", load_servers=False)

    @pytest.mark.parametrize("value", BAD_VALUES.values(), ids=list(BAD_VALUES))
    def test_a_bad_value_refuses_a_reload(self, value) -> None:
        """A reload checks every process-wide section before it stops anything."""
        with pytest.raises(ConfigurationError, match="http.graceful_shutdown_timeout_s"):
            check_process_config({"mcp_servers": {}, "http": {KEY: value}})

    def test_an_http_section_that_is_not_a_mapping_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="Invalid http section"):
            http_graceful_shutdown_timeout({"http": 90})


class TestTheSchemaKnowsTheKey:
    def test_the_key_is_accepted(self) -> None:
        assert validate_config({"mcp_servers": {}, "http": {KEY: 90}}) == []

    def test_a_misspelled_key_is_reported(self) -> None:
        [problem] = validate_config({"mcp_servers": {}, "http": {"graceful_shutdown_timeout": 90}})

        assert "http has unknown key(s) ['graceful_shutdown_timeout']" in problem
        assert KEY in problem

    def test_strict_mode_refuses_a_misspelled_key_from_a_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("HANGAR_CONFIG_STRICT", "1")
        path = _write(tmp_path, {"mcp_servers": {}, "http": {"graceful_shutdown_timeout": 90}})

        with pytest.raises(ConfigSchemaError, match="graceful_shutdown_timeout"):
            load_config_from_file(path)
