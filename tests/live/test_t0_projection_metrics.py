"""Tier 0 live verification: the projected surface is measured and scraped from a real gateway (#1369).

BLACK-BOX against a REAL ``mcp-hangar serve --http`` with
``tool_access.mode: front_door``. The caller's tenant rides on a seeded
``X-API-Key``. The projection is changed under the connected client through the
REST withdrawal endpoint, the way an operator changes it. Every number is read
from the process's own ``GET /metrics``, and the PromQL the guide documents is
run against two of those scrapes.

Per-request behaviour on this path has had fail-opens that a unit test with a
mock context did not see, which is why this is driven over the real transport.
Skip-safe like the rest of the tier. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_projection_metrics.py -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest

from tests._promql import PROJECTION_QUERIES, Scrape, evaluate, parse_scrape
from tests.live import _group_support as gs
from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

_TENANT = "tenant-surface"
_WITHDRAWN = "power"

_CONFIG = """\
logging:
  level: WARNING
tool_access:
  mode: front_door
auth:
  enabled: true
  allow_anonymous: true
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {auth_db}
  role_assignments:
    - principal: "svc:{tenant}"
      role: developer
      scope: global
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
"""


@dataclass
class _FrontDoor:
    base_url: str
    api_key: str

    def scrape(self) -> Scrape:
        response = httpx.get(f"{self.base_url}/metrics", timeout=5.0)
        assert response.status_code == 200, response.text[:200]
        return parse_scrape(response.text, at=time.time())

    def withdraw(self, tool: str) -> None:
        response = httpx.post(
            f"{self.base_url}/api/admin/tools/math/{tool}/withdraw",
            headers={"X-API-Key": self.api_key},
            json={"tenant_id": _TENANT},
            timeout=5.0,
        )
        assert response.status_code == 200, response.text[:300]

    def list_times(self, times: int) -> list[list[str]]:
        """List tools *times* in one MCP session; return the names each listing carried."""
        from mcp import ClientSession

        from tests.live._mcp_client import open_mcp_streams

        async def _run() -> list[list[str]]:
            seen = []
            async with open_mcp_streams(f"{self.base_url}/mcp", {"X-API-Key": self.api_key}) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for _ in range(times):
                        seen.append(sorted(tool.name for tool in (await session.list_tools()).tools))
            return seen

        return asyncio.run(_run())


@pytest.fixture(scope="module")
def front_door(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_FrontDoor]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("front_door_surface")
    auth_db = workdir / "auth.db"
    try:
        keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")

    config = _CONFIG.format(auth_db=str(auth_db), tenant=_TENANT, python=sys.executable, server=str(_MATH_SERVER))
    with running_hangar(workdir, config) as hangar:
        yield _FrontDoor(base_url=hangar.base_url, api_key=keys[_TENANT])


def _value(scrape: Scrape, sample: str, **labels: str) -> float:
    return sum(
        value
        for series, value in scrape.samples.get(sample, {}).items()
        if all(dict(series).get(name) == wanted for name, wanted in labels.items())
    )


def test_a_change_under_a_connected_client_is_counted_and_the_documented_queries_answer(
    front_door: _FrontDoor,
) -> None:
    # A listing before the first scrape: a rate needs a sample on each side.
    [first] = front_door.list_times(1)
    assert _WITHDRAWN in first, first
    before = front_door.scrape()

    unchanged, _ = front_door.list_times(2)
    front_door.withdraw(_WITHDRAWN)
    [changed] = front_door.list_times(1)
    after = front_door.scrape()

    assert unchanged == first
    assert changed == sorted(set(first) - {_WITHDRAWN})
    assert (
        _value(after, "mcp_hangar_projection_changes_total") - _value(before, "mcp_hangar_projection_changes_total")
        == 1
    )
    listings = _value(after, "mcp_hangar_projected_surface_bytes_count", kind="governed") - _value(
        before, "mcp_hangar_projected_surface_bytes_count", kind="governed"
    )
    assert listings == 3
    assert _value(after, "mcp_hangar_projected_upstream_bytes_count", mcp_server="math") > 0

    for sample, series in after.samples.items():
        if sample.startswith(("mcp_hangar_projection_changes", "mcp_hangar_projected_")):
            for labels in series:
                assert {name for name, _ in labels} <= {"kind", "mcp_server", "le"}, (sample, labels)
                assert _TENANT not in dict(labels).values(), (sample, labels)

    # This caller holds the developer role, so its listings also carry the
    # `hangar_*` tools it may call (#904): measured under kind="management".
    managed = [name for name in first if name.startswith("hangar_")]
    upstream_tools = [len([name for name in names if not name.startswith("hangar_")]) for names in (first, changed)]
    results = {name: evaluate(query, before, after) for name, query in PROJECTION_QUERIES.items()}
    assert results["changed"] == {frozenset(): 1.0}
    governed, management = frozenset({("kind", "governed")}), frozenset({("kind", "management")})
    assert results["tools_per_listing"] == pytest.approx(
        {governed: (2 * upstream_tools[0] + upstream_tools[1]) / 3, management: float(len(managed))}
    )
    assert results["bytes_per_listing"][governed] > 0  # type: ignore[index]
    assert (results["bytes_per_listing"][management] > 0) == bool(managed)  # type: ignore[index]
    assert results["bytes_per_upstream"][frozenset({("mcp_server", "math")})] == pytest.approx(  # type: ignore[index]
        results["bytes_per_listing"][governed]  # type: ignore[index]
    )
    assert results["governed_bytes_p95"][frozenset()] > 0  # type: ignore[index]
