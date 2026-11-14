"""Tier 0 live verification: a front door projects only the tools ``tool_projection.flat`` names (#1372).

A front door used to project every upstream tool under its own name. Now each
server names the tools it projects, and one that names none refuses to start.

Black-box, against the ``mcp-hangar`` this checkout installs, over real
streamable HTTP with API-key auth and a tenant key. The stub backend serves
five tools and the file names one, ``add``:

* ``tools/list`` holds ``add`` and no other upstream tool;
* ``add`` is served, and a tool the backend serves but the file does not name
  is refused ``-32601``, as a name that does not exist is;
* a reload of a file with the ``flat`` key removed is refused, and the
  surface stays as it was;
* the same file without the key does not start, and says which decision is
  missing.

The in-process unit tests are ``tests/unit/test_flat_projection_is_opt_in.py``.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_front_door_flat_is_opt_in.py -m "live and t0"
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests._hangar_executable import hangar_executable
from tests.live.conftest import _MATH_SERVER, running_hangar
from tests.live.test_t0_reload_keeps_the_front_door import _CONFIG, _Gateway, _seed_keys

pytestmark = [pytest.mark.live, pytest.mark.t0]

METHOD_NOT_FOUND = -32601
FLAT = "    tool_projection:\n      flat: [add, subtract, multiply, divide, power]\n"
ONLY_ADD = "    tool_projection:\n      flat: [add]\n"


def _upstream(names: list[str]) -> list[str]:
    """The upstream tools in a listing: the management tools a caller is offered are not this test's."""
    return [name for name in names if not name.startswith("hangar_")]


def _call(gateway: _Gateway, name: str) -> object:
    payload = gateway.rpc("tenant", "tools/call", {"name": name, "arguments": {"a": 1, "b": 2}})
    return payload["error"]["code"] if "error" in payload else "served"


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Gateway]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("flat_opt_in")
    keys = _seed_keys(workdir / "auth.db")
    full = _CONFIG.format(mode="front_door", auth_db=workdir / "auth.db", python=sys.executable, server=_MATH_SERVER)
    assert FLAT in full, "the base config changed shape"
    text = full.replace(FLAT, ONLY_ADD)
    with running_hangar(workdir, text) as hangar:
        gw = _Gateway(hangar, keys, workdir / "config.yaml", text)
        deadline = time.monotonic() + 40
        while "add" not in gw.tools("tenant"):
            if time.monotonic() > deadline:
                pytest.fail(f"the tenant never saw 'add':\n{hangar.output()[-2000:]}")
            time.sleep(0.5)
        yield gw


def test_tools_list_holds_only_the_named_tool(gateway: _Gateway) -> None:
    assert _upstream(gateway.tools("tenant")) == ["add"]


def test_the_named_tool_is_served(gateway: _Gateway) -> None:
    assert _call(gateway, "add") == "served"


@pytest.mark.parametrize("name", ["subtract", "divide", "no_such_tool"])
def test_a_tool_the_file_does_not_name_is_refused_like_a_missing_one(gateway: _Gateway, name: str) -> None:
    assert _call(gateway, name) == METHOD_NOT_FOUND


def test_a_reload_that_drops_the_key_is_refused_and_changes_nothing(gateway: _Gateway) -> None:
    refused_before = gateway.reloads("configuration_reload_failed")
    gateway.config_path.write_text(gateway.config_text.replace(ONLY_ADD, ""))
    try:
        response = gateway.reload_over_rest()

        assert response.status_code != 200, response.text
        assert "tool_projection.flat" in response.text, response.text
        gateway.wait_for_reload(refused_before, "configuration_reload_failed")
        assert _upstream(gateway.tools("tenant")) == ["add"]
        assert _call(gateway, "subtract") == METHOD_NOT_FOUND
        assert _call(gateway, "add") == "served"
    finally:
        gateway.config_path.write_text(gateway.config_text)


def test_a_front_door_with_no_key_does_not_start(tmp_path: Path) -> None:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    text = _CONFIG.format(mode="front_door", auth_db=tmp_path / "auth.db", python=sys.executable, server=_MATH_SERVER)
    config = tmp_path / "config.yaml"
    config.write_text(text.replace(FLAT, ""))

    result = subprocess.run(
        [hangar_executable(), "--config", str(config), "serve", "--http", "--host", "127.0.0.1", "--port", "0"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "tool_projection.flat" in output, output
    assert "must be chosen explicitly" in output, output
