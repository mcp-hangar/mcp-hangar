"""A front door says it does not serve a hot-loaded server (#2100).

The front door lists and routes only the servers the configuration declares. A
server loaded at runtime with `hangar_load` lives in the runtime store, which
the tool projection is not built from, so its tools were never listed and a flat
call of one answered `-32601` -- while `hangar_load` answered `loaded` and said
nothing. Serving it is not a fix this side of a decision: a hot-loaded server
cannot be pinned, withheld or given a per-tenant policy (those are declared on
the server's entry in the file), and it lives on one replica only. So the load
says so, and the limitation is pinned here: a change that serves hot-loaded
servers on the front door has to flip these assertions deliberately.

`_front_door_hot_load_harness.py` runs in a fresh interpreter: the real
`bootstrap()` from a file, the app `serve --http` serves behind its
authentication layer, `hangar_load` and `hangar_unload` over streamable HTTP
with an admin key, the listing and the calls with a tenant's. Only the registry
and the installer are stood in for. `egress` is the baseline, and nothing about
a load there changes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# One gateway per mode, with two real upstreams.
pytestmark = pytest.mark.timeout(180)

HARNESS = Path(__file__).with_name("_front_door_hot_load_harness.py")
METHOD_NOT_FOUND = -32601


def _says_front_door(answer: dict[str, Any]) -> bool:
    """Whether a `hangar_load` answer warns that the front door does not serve the server."""
    return any("front_door" in warning and "-32601" in warning for warning in answer.get("warnings") or [])


def _run(mode: str, workdir: Path) -> dict[str, Any]:
    out = workdir / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(workdir), str(out)],
        capture_output=True,
        text=True,
        timeout=170,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


@pytest.fixture(scope="module")
def front_door(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run("front_door", tmp_path_factory.mktemp("front-door-hot-load"))


@pytest.fixture(scope="module")
def egress(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run("egress", tmp_path_factory.mktemp("egress-hot-load"))


def test_a_load_on_a_front_door_says_the_server_is_not_served_there(front_door: dict[str, Any]) -> None:
    loaded = front_door["loaded"]

    assert loaded["status"] == "loaded", "the load itself is unchanged"
    assert _says_front_door(loaded), "a front door answered `loaded` and said nothing"


def test_loading_it_again_says_so_too(front_door: dict[str, Any]) -> None:
    again = front_door["loaded_again"]

    assert again["status"] == "already_loaded"
    assert _says_front_door(again)


def test_the_front_door_does_not_list_or_route_the_hot_loaded_server(front_door: dict[str, Any]) -> None:
    # What the warning states, as the tenant sees it.
    assert "add" not in front_door["listing"]
    assert front_door["listing"] == front_door["boot_listing"]
    assert front_door["flat_call"] == METHOD_NOT_FOUND
    assert front_door["hangar_call"] == METHOD_NOT_FOUND


def test_the_configured_server_is_still_served_beside_it(front_door: dict[str, Any]) -> None:
    assert "links" in front_door["listing"]
    assert front_door["kept_call"] == "served"
    assert front_door["unloaded"]["status"] == "unloaded"


def test_a_load_on_an_egress_gateway_is_unchanged(egress: dict[str, Any]) -> None:
    assert egress["loaded"]["status"] == "loaded"
    assert "warnings" not in egress["loaded"]
    assert "warnings" not in egress["loaded_again"]
    assert egress["hangar_call"] == "served", "hangar_call reaches a hot-loaded server on egress"
    assert egress["unloaded"]["status"] == "unloaded"
