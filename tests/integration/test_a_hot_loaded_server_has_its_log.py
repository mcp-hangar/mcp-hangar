"""A hot-loaded server's output reaches the logs API, and leaving releases it (#1506).

`hangar_load` built a server at runtime and attached no log buffer. No stderr
reader was ever started for it, so `GET /api/mcp_servers/{id}/logs` served an
empty list for a running server. `hangar_unload` and the delete endpoint
released none, so the registry entry outlived the server under an id that was
free again.

`_hot_load_logs_harness.py` runs in a fresh interpreter: the real `bootstrap()`
from a file, the real `hangar_load` and `hangar_unload` tools over streamable
HTTP, a real stdio subprocess writing a line of its own to stderr, the delete
over REST, and every log read back through the API. Only the registry and the
installer are stood in for -- they are the network. The handler-level versions
are in `tests/unit/test_a_hot_loaded_server_gets_a_log_buffer.py`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# One gateway, booted once, with three real upstreams.
pytestmark = pytest.mark.timeout(180)

HARNESS = Path(__file__).with_name("_hot_load_logs_harness.py")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    workdir = tmp_path_factory.mktemp("hot-load-logs")
    out = workdir / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(workdir), str(out)],
        capture_output=True,
        text=True,
        timeout=170,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


def test_a_configured_servers_output_reaches_its_log(run: dict[str, Any]) -> None:
    # The baseline every assertion below compares a hot-loaded server against.
    assert run["kept"]["logs"] == ["kept is up"]
    assert run["kept"]["wired"]


def test_the_hot_loaded_servers_output_reaches_the_same_log(run: dict[str, Any]) -> None:
    loaded = run["loaded"]

    assert loaded["status"] == "loaded"
    assert loaded["mcp_server_id"] == "hot-one"
    assert loaded["logs"] == ["hot-one is up"], "a hot-loaded server had no buffer to write to"
    assert loaded["wired"], "the server must fill the very buffer the logs API reads"
    assert not loaded["in_repository"], "a hot-loaded server lives in the runtime store, not the repository"


def test_a_server_that_already_holds_one_keeps_it(run: dict[str, Any]) -> None:
    """A running server's reader fills the buffer it was started with, so
    replacing the registered one would leave it writing where nothing reads."""
    again = run["loaded_again"]

    assert again["status"] == "already_loaded"
    assert again["same_buffer"]
    assert again["logs"] == ["hot-one is up"], "the lines already in it survived"


def test_unloading_releases_the_log(run: dict[str, Any]) -> None:
    unloaded = run["unloaded"]

    assert unloaded["status"] == "unloaded"
    assert not unloaded["in_store"]
    assert not unloaded["registered"], "its output stayed registered under an id now free"
    # The server is gone, so the API has nothing to serve a log for.
    assert unloaded["logs"] == 404


def test_deleting_releases_the_log(run: dict[str, Any]) -> None:
    deleted = run["deleted"]

    assert deleted["status"] in (200, 204)
    assert not deleted["in_repository"]
    assert not deleted["registered"], "its output stayed registered under an id now free"
    assert deleted["logs"] == 404


def test_the_delete_releases_only_its_own(run: dict[str, Any]) -> None:
    """The release is by id, so a server loaded beside the deleted one keeps its log."""
    deleted = run["deleted"]

    assert deleted["hot_two_registered"]
    assert deleted["hot_two_logs"] == ["hot-two is up"]
