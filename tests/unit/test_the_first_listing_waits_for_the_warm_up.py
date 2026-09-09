"""A listing that arrives mid-warm-up waits instead of caching an empty one (#1231).

The front door warms its upstreams on a thread, deliberately, because gating the
serving path on a backend handshake deadlocks the deployment (#599). The cost was
the first answer: a client connecting during the warm-up was served a catalogue
with no upstream tools, told `tools.listChanged: false`, and never notified --
so it cached that empty catalogue until it reconnected.

These pin the narrow wait that fixes it, and -- just as important -- the three
cases that must NOT wait.
"""

from __future__ import annotations

import threading
import time

import anyio
import pytest

from mcp_hangar._sdk_compat import Tool as MCPTool
from mcp_hangar.fastmcp_server import catalogue_warmup


def _as_tools(flat):
    """Real Tool objects: ListToolsResult validates what it is handed."""
    return [MCPTool(name=n, description=n, inputSchema={"type": "object"}) for n in flat]


@pytest.fixture(autouse=True)
def _clean_warmup_state():
    catalogue_warmup.reset()
    yield
    catalogue_warmup.reset()


class TestTheWarmupGate:
    def test_nothing_is_warming_before_a_warm_up_starts(self) -> None:
        assert catalogue_warmup.is_warming() is False

    def test_a_started_warm_up_is_in_flight(self) -> None:
        catalogue_warmup.warmup_started()

        assert catalogue_warmup.is_warming() is True

    def test_a_finished_warm_up_is_not(self) -> None:
        catalogue_warmup.warmup_started()
        catalogue_warmup.warmup_finished()

        assert catalogue_warmup.is_warming() is False

    def test_finishing_without_starting_is_harmless(self) -> None:
        """The `finally` in the warm-up runs even on an `egress` gateway."""
        catalogue_warmup.warmup_finished()

        assert catalogue_warmup.is_warming() is False


class TestWaiting:
    def test_it_returns_as_soon_as_the_warm_up_finishes(self) -> None:
        catalogue_warmup.warmup_started()

        async def scenario() -> bool:
            async def finish_soon() -> None:
                await anyio.sleep(0.1)
                catalogue_warmup.warmup_finished()

            async with anyio.create_task_group() as tg:
                tg.start_soon(finish_soon)
                return await catalogue_warmup.wait_for_catalogue(timeout=5.0)

        started = time.monotonic()
        assert anyio.run(scenario) is True
        # Returned on the event, not on the deadline.
        assert time.monotonic() - started < 1.0

    def test_it_gives_up_at_the_deadline_rather_than_hanging(self) -> None:
        """A backend that never arrives must not hold the listing open."""
        catalogue_warmup.warmup_started()

        started = time.monotonic()
        assert anyio.run(lambda: catalogue_warmup.wait_for_catalogue(timeout=0.2)) is False
        elapsed = time.monotonic() - started

        assert 0.15 < elapsed < 2.0

    def test_it_does_not_wait_when_no_warm_up_was_ever_started(self) -> None:
        """An `egress` gateway never warms, so a listing there waits for nothing."""
        started = time.monotonic()
        assert anyio.run(lambda: catalogue_warmup.wait_for_catalogue(timeout=5.0)) is False

        assert time.monotonic() - started < 0.5

    def test_a_warm_up_that_already_finished_is_not_waited_on(self) -> None:
        catalogue_warmup.warmup_started()
        catalogue_warmup.warmup_finished()

        started = time.monotonic()
        assert anyio.run(lambda: catalogue_warmup.wait_for_catalogue(timeout=5.0)) is True

        assert time.monotonic() - started < 0.5


class TestTheListingRebuildsAfterWaiting:
    """Waiting is only useful if the listing then re-reads the catalogue."""

    @staticmethod
    def _drive(monkeypatch, *, reason: str, builds: list[list[str]]):
        """Run `_list_projected_tools` with a projection that fills in later."""
        from mcp_hangar.fastmcp_server import flat_tool_projection as ftp

        seen = iter(builds)
        rebuilt: list[int] = []

        def fake_build_flat_map(tenant_id):
            rebuilt.append(1)
            return {name: (name, name) for name in next(seen, builds[-1])}

        monkeypatch.setattr(ftp, "_build_flat_map", fake_build_flat_map)
        monkeypatch.setattr(ftp, "_build_mcp_tool_list", _as_tools)
        monkeypatch.setattr(ftp, "_classify_empty_projection", lambda tenant_id: reason)
        monkeypatch.setattr(ftp, "_memoise_flat_map", lambda *a, **k: None)
        monkeypatch.setattr(ftp, "_envelope", lambda ctx: {"method": "tools/list"})
        monkeypatch.setattr(ftp, "build_projected_list_cache_meta", lambda tenant: None)
        monkeypatch.setattr(
            ftp,
            "get_identity_context",
            lambda: type("I", (), {"caller": type("C", (), {"tenant_id": "local"})()})(),
        )

        async def no_management(_ctx):
            return []

        result = anyio.run(lambda: ftp._list_projected_tools(None, no_management))
        return [t.name for t in result.tools], len(rebuilt)

    def test_an_empty_first_build_is_retried_once_the_warm_up_lands(self, monkeypatch) -> None:
        """The warm-up finishes while the listing is waiting on it."""
        from mcp_hangar.fastmcp_server import flat_tool_projection as ftp

        catalogue_warmup.warmup_started()

        # The upstream lands while the listing is already waiting -- from
        # another thread, exactly as the real warm-up does.
        threading.Timer(0.15, catalogue_warmup.warmup_finished).start()

        builds = {"n": 0}

        def empty_then_populated(tenant_id):
            builds["n"] += 1
            return {} if builds["n"] == 1 else {"echo": ("demo", "echo")}

        monkeypatch.setattr(ftp, "_build_flat_map", empty_then_populated)
        monkeypatch.setattr(ftp, "_build_mcp_tool_list", _as_tools)
        monkeypatch.setattr(ftp, "_classify_empty_projection", lambda tenant_id: "nothing_discovered")
        monkeypatch.setattr(ftp, "_memoise_flat_map", lambda *a, **k: None)
        monkeypatch.setattr(ftp, "_envelope", lambda ctx: {"method": "tools/list"})
        monkeypatch.setattr(ftp, "build_projected_list_cache_meta", lambda tenant: None)
        monkeypatch.setattr(
            ftp,
            "get_identity_context",
            lambda: type("I", (), {"caller": type("C", (), {"tenant_id": "local"})()})(),
        )

        async def no_management(_ctx):
            return []

        result = anyio.run(lambda: ftp._list_projected_tools(None, no_management))

        assert [t.name for t in result.tools] == ["echo"], "the listing waited but never re-read the catalogue"

    def test_a_caller_without_identity_is_refused_immediately(self, monkeypatch) -> None:
        """Fail-closed on missing identity must stay instant, never wait."""
        catalogue_warmup.warmup_started()  # in flight, but must not matter

        started = time.monotonic()
        tools, builds = self._drive(monkeypatch, reason="no_identity", builds=[[], ["echo"]])

        assert tools == []
        assert builds == 1, "a fail-closed deny must not be delayed by the warm-up"
        assert time.monotonic() - started < 1.0

    def test_a_policy_filtered_empty_list_is_the_truth_and_is_not_retried(self, monkeypatch) -> None:
        catalogue_warmup.warmup_started()

        tools, builds = self._drive(monkeypatch, reason="filtered", builds=[[], ["echo"]])

        assert tools == []
        assert builds == 1, "an empty list that policy produced is already correct"


class TestTheWarmUpAlwaysReleasesItsWaiters:
    def test_a_warm_up_that_raises_still_finishes(self, monkeypatch) -> None:
        """The release is in a `finally`: a crash must not strand every listing.

        Otherwise a warm-up that dies on its first backend leaves every later
        listing paying the full deadline for something that will never come.
        """
        from mcp_hangar.server import lifecycle

        class _Boom:
            def get_all_ids(self):
                raise RuntimeError("the fleet is on fire")

        runtime = type("R", (), {"repository": _Boom(), "command_bus": None})()
        monkeypatch.setattr(
            "mcp_hangar.domain.services.tool_access_resolver.is_front_door",
            lambda: True,
        )

        with pytest.raises(RuntimeError):
            lifecycle.warm_the_front_door_catalogue(runtime)

        assert catalogue_warmup.is_warming() is False
