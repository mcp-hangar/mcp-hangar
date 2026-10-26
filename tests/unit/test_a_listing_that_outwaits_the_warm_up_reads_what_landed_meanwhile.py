"""A listing whose warm-up wait hits its deadline answers with what landed meanwhile (#2133).

The front door's warm-up projects its servers one at a time. A first listing
that finds nothing projected waits for the warm-up, at most
`WARMUP_LIST_WAIT_S`. When the deadline passed before the warm-up finished,
the listing and the flat call path served the empty projection they had read
BEFORE the wait, although a server could have landed during it. Tier 0 met
this on a loaded runner: `member-a` was projected mid-wait, `member-b` was
still warming at the deadline, and `tools/list` answered `set()`.

`server/discover` always re-read after waiting, which is why #2118 did not
show this. These pin the other two surfaces to the same rule.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Iterator
from typing import Any

import anyio
import pytest

from mcp_hangar._sdk_compat import Tool as MCPTool
from mcp_hangar.fastmcp_server import catalogue_warmup
from mcp_hangar.fastmcp_server import flat_tool_projection as ftp

#: Short enough for a unit test; the first server lands well inside it.
_DEADLINE_S = 0.4
_LANDS_AFTER_S = 0.1


@pytest.fixture(autouse=True)
def _clean_warmup_state():
    catalogue_warmup.reset()
    yield
    catalogue_warmup.reset()


@pytest.fixture
def one_server_lands_mid_wait(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """A warm-up that stays marked past the deadline, and projects one server during the wait."""
    landed = threading.Event()

    def flat_map(tenant_id: str | None) -> dict[str, tuple[str, str]]:
        return {"add": ("member-a", "add")} if landed.is_set() else {}

    def tools(flat: Any, tenant_id: str | None = None) -> list[MCPTool]:
        return [MCPTool(name=name, description=name, inputSchema={"type": "object"}) for name in flat]

    def classify(tenant_id: str | None) -> str:
        return ftp.EMPTY_FILTERED if landed.is_set() else ftp.EMPTY_NOTHING_DISCOVERED

    monkeypatch.setattr(ftp, "_build_flat_map", flat_map)
    monkeypatch.setattr(ftp, "_build_mcp_tool_list", tools)
    monkeypatch.setattr(ftp, "_classify_empty_projection", classify)
    monkeypatch.setattr(ftp, "wait_for_catalogue", functools.partial(ftp.wait_for_catalogue, timeout=_DEADLINE_S))

    # Never finished: the second server is still warming when the deadline passes.
    catalogue_warmup.warmup_started()
    timer = threading.Timer(_LANDS_AFTER_S, landed.set)
    timer.start()
    yield landed
    timer.cancel()


def _identity(monkeypatch: pytest.MonkeyPatch) -> None:
    caller = type("C", (), {"tenant_id": "tenant-lb"})()
    monkeypatch.setattr(ftp, "get_identity_context", lambda: type("I", (), {"caller": caller})())


def test_the_listing_serves_the_server_that_landed_during_the_wait(
    one_server_lands_mid_wait: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identity(monkeypatch)
    monkeypatch.setattr(ftp, "_memoise_flat_map", lambda *a, **k: None)
    monkeypatch.setattr(ftp, "_envelope", lambda ctx: {"method": "tools/list"})

    async def no_management(_ctx: Any) -> list[MCPTool]:
        return []

    result = anyio.run(lambda: ftp._list_projected_tools(None, no_management))

    assert catalogue_warmup.is_warming(), "the deadline, not the end of the warm-up, ended this wait"
    assert [tool.name for tool in result.tools] == ["add"]


def test_a_flat_call_routes_to_the_server_that_landed_during_the_wait(
    one_server_lands_mid_wait: threading.Event,
) -> None:
    routes = anyio.run(lambda: ftp._settled_flat_map(None, "tenant-lb"))

    assert catalogue_warmup.is_warming(), "the deadline, not the end of the warm-up, ended this wait"
    assert dict(routes) == {"add": ("member-a", "add")}


def test_a_listing_that_needs_no_wait_is_not_read_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unchanged: no warm-up in flight, no wait and no second read."""
    reads: list[str | None] = []

    def flat_map(tenant_id: str | None) -> dict[str, tuple[str, str]]:
        reads.append(tenant_id)
        return {}

    monkeypatch.setattr(ftp, "_build_flat_map", flat_map)

    routes = anyio.run(lambda: ftp._settled_flat_map(None, "tenant-lb"))

    assert dict(routes) == {}
    assert reads == ["tenant-lb"]
