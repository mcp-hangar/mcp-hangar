"""A subprocess server's `args` follow its `command` (#1658).

`args` fed only the docker `container_args`, so the paths `init` and `add` write
there never reached the server and nothing warned.
"""

from __future__ import annotations

import pytest

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.server.config import _load_mcp_server_config


def _command(spec: dict) -> list[str] | None:
    return _load_mcp_server_config("s", spec)._command


def test_args_are_appended_to_the_command():
    assert _command({"mode": "subprocess", "command": ["npx", "-y", "pkg"], "args": ["/data"]}) == [
        "npx",
        "-y",
        "pkg",
        "/data",
    ]


def test_mode_defaults_to_subprocess():
    assert _command({"command": ["srv"], "args": ["a", "b"]}) == ["srv", "a", "b"]


def test_no_args_leaves_the_command_alone():
    assert _command({"command": ["srv"]}) == ["srv"]


def test_args_that_are_not_a_list_of_strings_refuse_the_config():
    with pytest.raises(ConfigurationError, match="args must be a list of strings"):
        _command({"command": ["srv"], "args": "/data"})


def test_docker_args_still_feed_the_container_not_the_command():
    server = _load_mcp_server_config("d", {"mode": "docker", "image": "img:1", "args": ["--x"]})
    assert server._container_args == ["--x"]
    assert server._command is None
