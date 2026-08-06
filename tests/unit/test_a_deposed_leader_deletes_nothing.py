"""The stalled leader's sweep lands on a fleet that is no longer its own.

The sequence, from the HA design (#789):

1. A holds the management lease and decides server X has expired.
2. A stalls -- a stop-the-world pause, a wedged disk, a partition.
3. The lease expires. B acquires it and re-registers X, which is alive.
4. A resumes and issues its delete.

A's own lease keeper cannot save it: it was frozen too, and the delete goes out
before its next tick. The gate in 1.3 is checked per cycle, and this is *inside*
a cycle that had already started. So the check has to be in the write, which is
what fencing means -- and what "affects zero rows" is a test for.

A's *operator* deleting a server is a different thing and is not fenced: they
are not a stale loop finishing, and refusing them on two pods out of three would
make the API answer differently depending on which one they reached.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar.domain.contracts.fleet import NotTheManagerError
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.sqlite_management_lease import SQLiteManagementLease


@pytest.fixture
async def storage(tmp_path):
    """One SQLite file holding both the fleet and the lease.

    They share a file by construction -- the backend gives them the same path --
    because the fence is a condition inside one statement over both tables. A
    deployment that split them would have a fence that could never pass.
    """
    path = str(tmp_path / "mcp_hangar.db")
    database = Database(DatabaseConfig(path=path))
    await database.initialize()
    return SQLiteMcpServerConfigRepository(database), SQLiteManagementLease(path)


def _snapshot(mcp_server_id: str = "math") -> McpServerConfigSnapshot:
    return McpServerConfigSnapshot(mcp_server_id=mcp_server_id, mode="subprocess", command=["python"])


async def _recovered(configs) -> list[str]:
    """What the next process would restore.

    Asserted on rather than `get()`, because deletion here is a soft one: the
    row survives with `enabled = 0` and `get()` still returns it. What decides
    whether a server comes back is `get_all()`, which is what recovery reads.
    """
    return [config.mcp_server_id for config in await configs.get_all()]


@pytest.mark.asyncio
class TestTheDeposedLeaderSequence:
    async def test_its_deregistration_affects_zero_rows(self, storage) -> None:
        configs, lease_store = storage
        stale = lease_store.acquire("gateway-a", ttl_s=3600)
        await configs.save(_snapshot("math"))

        # B takes over while A is stalled.
        lease_store.acquire("gateway-b", ttl_s=3600)

        # A resumes, still holding its old tenure, and sweeps.
        writer = RepositoryFleetWriter(configs, lease_provider=lambda: stale)
        writer.delete("math", fenced=True)

        assert await _recovered(configs) == ["math"], "a deposed leader deregistered a server the current one had kept"

    async def test_the_current_holder_can_still_deregister(self, storage) -> None:
        # The fence has to refuse the stale write without refusing the real one,
        # or discovery simply stops working under a lease.
        configs, lease_store = storage
        current = lease_store.acquire("gateway-b", ttl_s=3600)
        await configs.save(_snapshot("math"))

        RepositoryFleetWriter(configs, lease_provider=lambda: current).delete("math", fenced=True)

        assert await _recovered(configs) == []

    async def test_a_lapsed_tenure_deregisters_nothing_even_before_a_peer_takes_it(self, storage) -> None:
        # There is a window where the lease has expired and nobody has claimed
        # it. A write in that window is still from a tenure that is over, and a
        # peer may be acquiring right now.
        configs, lease_store = storage
        lapsed = lease_store.acquire("gateway-a", ttl_s=3600)
        await configs.save(_snapshot("math"))
        lease_store.release(lapsed)

        # Released, so the row's tenure is over. Another acquisition bumps the
        # generation past the one this writer carries.
        lease_store.acquire("gateway-b", ttl_s=3600)
        RepositoryFleetWriter(configs, lease_provider=lambda: lapsed).delete("math", fenced=True)

        assert await _recovered(configs) == ["math"]


@pytest.mark.asyncio
class TestWhoIsFenced:
    async def test_an_operators_deletion_is_not(self, storage) -> None:
        # They are not a stale loop finishing. Fencing them would make
        # `DELETE /api/mcp_servers/x` succeed or fail depending on which pod the
        # load balancer picked.
        configs, lease_store = storage
        stale = lease_store.acquire("gateway-a", ttl_s=3600)
        lease_store.acquire("gateway-b", ttl_s=3600)
        await configs.save(_snapshot("math"))

        RepositoryFleetWriter(configs, lease_provider=lambda: stale).delete("math")

        assert await _recovered(configs) == []

    async def test_a_standalone_gateway_is_not_fenced_either(self, storage) -> None:
        # No keeper at all: nothing is coordinating, so there is no tenure to
        # prove and no peer to protect the fleet from.
        configs, _lease_store = storage
        await configs.save(_snapshot("math"))

        RepositoryFleetWriter(configs).delete("math", fenced=True)

        assert await _recovered(configs) == []

    async def test_holding_nothing_refuses_the_write_rather_than_letting_it_through(self, storage) -> None:
        # The difference between "no keeper" and "a keeper holding nothing".
        # Conflating them leaves open exactly the window between losing the
        # lease and an in-flight cycle reaching its write.
        configs, _lease_store = storage
        await configs.save(_snapshot("math"))

        writer = RepositoryFleetWriter(configs, lease_provider=lambda: None)

        with pytest.raises(NotTheManagerError):
            writer.delete("math", fenced=True)
        assert await _recovered(configs) == ["math"]

    async def test_a_repository_that_cannot_fence_refuses_to_delete_unfenced(self) -> None:
        # Silently dropping the fence is worse than refusing: the deletion looks
        # like it worked, and the whole guarantee is gone with no sign of it.
        from mcp_hangar.domain.contracts.management_lease import Lease

        class _Unfenceable:
            async def delete(self, mcp_server_id: str) -> bool:
                raise AssertionError("an unfenced delete was performed for a fenced request")

        writer = RepositoryFleetWriter(
            _Unfenceable(), lease_provider=lambda: Lease(holder="a", generation=1, expires_at=0)
        )

        with pytest.raises(NotImplementedError):
            writer.delete("math", fenced=True)


@pytest.mark.asyncio
class TestTheCallPath:
    async def test_discovery_deregistration_is_marked_as_a_convergence_decision(self) -> None:
        # `provenance` is set by the construction path and never by a request,
        # exactly as on registration. A policy keyed on the free-text `source`
        # would be settable by anyone who can reach a route that forwards it.
        import inspect

        from mcp_hangar.server.bootstrap import discovery

        source = inspect.getsource(discovery._on_mcp_server_deregister)

        assert "DeleteMcpServerCommand" in source
        assert "Provenance.DISCOVERY" in source

    async def test_the_handler_fences_a_discovery_deletion_and_not_an_operators(self) -> None:
        from mcp_hangar.application.commands.crud_commands import DeleteMcpServerCommand
        from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler, DeleteMcpServerHandler
        from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand
        from mcp_hangar.domain.repository import InMemoryMcpServerRepository
        from mcp_hangar.domain.value_objects.provenance import Provenance

        asked: list[bool] = []

        class _Writer:
            def save(self, snapshot: Any) -> None:
                pass

            def delete(self, mcp_server_id: str, *, fenced: bool = False) -> None:
                asked.append(fenced)

        class _Bus:
            def publish(self, event: Any) -> None:
                pass

            def publish_aggregate_events(self, *args: Any, **kwargs: Any) -> int:
                return 0

        fleet = InMemoryMcpServerRepository()
        writer = _Writer()
        for server_id in ("a", "b"):
            CreateMcpServerHandler(repository=fleet, event_bus=_Bus(), fleet_writer=writer).handle(
                CreateMcpServerCommand(mcp_server_id=server_id, mode="subprocess", command=["python"])
            )

        handler = DeleteMcpServerHandler(repository=fleet, event_bus=_Bus(), fleet_writer=writer)
        handler.handle(DeleteMcpServerCommand(mcp_server_id="a", provenance=Provenance.DISCOVERY))
        handler.handle(DeleteMcpServerCommand(mcp_server_id="b", provenance=Provenance.HUMAN))

        assert asked == [True, False]

    async def test_the_default_provenance_is_the_untrusted_one(self) -> None:
        from mcp_hangar.application.commands.crud_commands import DeleteMcpServerCommand
        from mcp_hangar.domain.value_objects.provenance import Provenance

        assert DeleteMcpServerCommand(mcp_server_id="a").provenance is Provenance.HUMAN

    async def test_the_lease_and_the_fleet_share_one_sqlite_file(self, tmp_path) -> None:
        # The fence is one statement over both tables. Separate files would make
        # it a condition that can never pass -- and it would fail *silently*,
        # as a deregistration that quietly stopped happening.
        from mcp_hangar.infrastructure.persistence.registry import create_backend

        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            lease = backend.management_lease()
            database = backend.config_repository()._db

            assert lease._db_path == database.config.path
        finally:
            backend.close()
