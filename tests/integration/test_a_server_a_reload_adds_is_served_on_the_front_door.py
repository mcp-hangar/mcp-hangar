"""A server a reload adds, or rebuilds, is started on a front door, as boot starts every configured one.

In ``front_door`` the flat projection is ``tools/list``, and a server's tools reach
it only when it starts. Boot starts every configured server; a reload committed
the ones it added cold and started nothing. So an added server's tools were
never listed, no ``notifications/tools/list_changed`` was sent, and a call to one
answered ``-32601`` until a restart, and a rebuilt server kept the catalogue of
the process the reload stopped.

``_reload_front_door_catalogue_harness.py`` runs in a fresh interpreter: the real
``bootstrap()`` from a file, real stdio subprocess servers, the reload over REST
with an admin key, and the tenant's listing and calls over streamable HTTP with
its own key, while a ``GET /mcp`` stream is held open on a real socket. In
``egress`` a reload still starts nothing.

A server that leaves the fleet, by reload or by ``DELETE /api/mcp_servers``,
leaves the listing too, the client is told, and one added back under the same
name is listed again, started or not (#2088).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(180)

HARNESS = Path(__file__).with_name("_reload_front_door_catalogue_harness.py")
#: Only ``late`` and ``api`` list it.
LATE_TOOL = "links"
#: What a flat call of a name the caller is not shown answers.
METHOD_NOT_FOUND = -32601


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
    return _run("front_door", tmp_path_factory.mktemp("reload-front-door"))


@pytest.fixture(scope="module")
def egress(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run("egress", tmp_path_factory.mktemp("reload-egress"))


def test_the_booted_server_is_listed_and_the_added_one_is_not_yet(front_door: dict[str, Any]) -> None:
    # The baseline the rest compares against.
    boot = front_door["boot"]

    assert boot["add"] == "before"
    assert "links" not in boot["tools"]


def test_a_server_a_reload_adds_is_listed_and_callable(front_door: dict[str, Any]) -> None:
    added = front_door["added"]

    assert added["status"] == 200
    assert added["added"] == ["late"]
    assert {"echo", "links"} <= set(added["tools"])
    assert added["late_call"] == "served"
    assert added["states"]["late"] == "ready"


def test_the_listing_client_is_told_to_re_list(front_door: dict[str, Any]) -> None:
    assert front_door["added"]["told"] is True


def test_a_server_a_reload_rebuilds_is_listed_with_its_new_catalogue(front_door: dict[str, Any]) -> None:
    added = front_door["added"]

    assert added["add"] == "after"
    assert added["states"]["edit"] == "ready"


def test_a_server_a_reload_removes_leaves_the_listing_and_its_tool_is_not_served(front_door: dict[str, Any]) -> None:
    # Its projection used to stay: listed, and refused when called (#2088).
    removed = front_door["removed"]

    assert removed["status"] == 200
    assert removed["in_repository"] is False
    assert LATE_TOOL not in removed["tools"]
    assert "add" in removed["tools"], "the server that stays lost its tools"
    assert removed["told"] is True
    assert removed["late_call"] == METHOD_NOT_FOUND


def test_a_server_a_reload_adds_back_is_listed_again(front_door: dict[str, Any]) -> None:
    readded = front_door["readded"]

    assert readded["status"] == 200
    assert LATE_TOOL in readded["tools"]
    assert readded["late_call"] == "served"


def test_a_server_deleted_over_rest_leaves_the_listing_and_comes_back_when_registered_again_unstarted(
    front_door: dict[str, Any],
) -> None:
    rest = front_door["rest"]

    assert (rest["created"], rest["started"], rest["listed"], rest["told_added"]) == (201, 200, True, True), rest
    assert rest["deleted"] == 200
    assert rest["told"] is True
    assert LATE_TOOL not in rest["tools"]
    assert "add" in rest["tools"]
    assert rest["call"] == METHOD_NOT_FOUND
    # Registered again with no start: listed with its last tools, and the first
    # call starts the new copy, as a re-created server was served before #2088.
    assert (rest["recreated"], rest["state_recreated"], rest["listed_again"]) == (201, "cold", True), rest
    assert rest["call_again"] == "served"
    assert rest["state_after_call"] == "ready"


def test_egress_starts_nothing_on_a_reload(egress: dict[str, Any]) -> None:
    # Lazy start on first use is what egress is designed around.
    assert egress["boot"]["states"] == {"edit": "cold"}
    assert egress["added"]["status"] == 200
    assert egress["added"]["added"] == ["late"]
    assert egress["added"]["states"] == {"edit": "cold", "late": "cold"}
    assert egress["added"]["tools"] == egress["boot"]["tools"]
