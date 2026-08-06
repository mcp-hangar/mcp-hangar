"""Two replicas cannot both be the manager, and a deposed one cannot pretend.

The management loops -- discovery, GC, health, TTL deregistration -- are
convergence loops. Three of them racing on one database is the failure nobody
can debug afterwards: a server registered by one replica and deregistered by
another, in the same second, forever.

The SQLite half of this is tested here in full, because its answer is genuinely
different rather than a simplification: it always grants, and the reason is that
its file admits one process. The PostgreSQL half's contention is MVCC behaviour
and gets a real server in
`tests/integration/test_postgres_lease_has_one_holder.py`.
"""

from __future__ import annotations

import time

import pytest

from mcp_hangar.domain.contracts.management_lease import IManagementLease, Lease
from mcp_hangar.infrastructure.persistence.sqlite_management_lease import SQLiteManagementLease


@pytest.fixture
def lease_store(tmp_path) -> SQLiteManagementLease:
    return SQLiteManagementLease(str(tmp_path / "hangar.db"))


class TestAStandaloneGatewayIsAlwaysTheManager:
    def test_it_gets_the_lease(self, lease_store) -> None:
        assert lease_store.acquire("gateway-a", ttl_s=15) is not None

    def test_a_restart_does_not_wait_out_the_previous_tenure(self, lease_store) -> None:
        # The reason acquisition steals here rather than waiting: on SQLite an
        # unexpired row can only be a dead predecessor, because the file admits
        # one writer. Waiting for the TTL would mean a standalone gateway that
        # restarts manages nothing for fifteen seconds, to protect a peer that
        # cannot exist.
        lease_store.acquire("gateway-before-restart", ttl_s=3600)

        assert lease_store.acquire("gateway-after-restart", ttl_s=15) is not None

    def test_the_generation_survives_the_restart(self, lease_store) -> None:
        # Fencing needs a number that never repeats. A stub answering "yes, you
        # hold it, generation 1" would let a write left over from a stalled
        # previous process pass the new process's fencing check.
        first = lease_store.acquire("gateway-before-restart", ttl_s=15)

        second = lease_store.acquire("gateway-after-restart", ttl_s=15)

        assert second is not None and first is not None
        assert second.generation > first.generation

    def test_it_is_a_row_and_not_a_special_case_in_the_caller(self, lease_store) -> None:
        # If the standalone backend had no lease, the loops gating on one would
        # need two code paths, and the standalone path is the one nobody
        # exercises under load.
        lease_store.acquire("gateway-a", ttl_s=15)

        held = lease_store.current()

        assert held is not None and held.holder == "gateway-a"


class TestATenureCanBeExtendedOnlyByItsHolder:
    def test_a_renewal_pushes_the_expiry_out(self, lease_store) -> None:
        lease = lease_store.acquire("gateway-a", ttl_s=1)

        renewed = lease_store.renew(lease, ttl_s=60)

        assert renewed is not None and renewed.expires_at > lease.expires_at

    def test_a_renewal_keeps_the_generation(self, lease_store) -> None:
        # A renewal is the same tenure continuing. Bumping the generation there
        # would invalidate the holder's own in-flight writes.
        lease = lease_store.acquire("gateway-a", ttl_s=15)

        renewed = lease_store.renew(lease, ttl_s=15)

        assert renewed is not None and renewed.generation == lease.generation

    def test_a_previous_tenure_cannot_be_renewed(self, lease_store) -> None:
        # The stalled-leader sequence: A holds the lease, stalls, loses it, and
        # its renewal loop wakes up still carrying the old generation. It has to
        # be told it lost, not silently adopted into the current tenure.
        stale = lease_store.acquire("gateway-a", ttl_s=15)
        lease_store.acquire("gateway-b", ttl_s=15)

        assert lease_store.renew(stale, ttl_s=15) is None

    def test_losing_it_is_reported_rather_than_raised(self, lease_store) -> None:
        # Losing the lease is an ordinary outcome of running in a cluster, not
        # an error to retry through. A caller that saw an exception would be
        # tempted to treat it as transient and keep managing.
        stale = lease_store.acquire("gateway-a", ttl_s=15)
        lease_store.acquire("gateway-b", ttl_s=15)

        assert lease_store.renew(stale, ttl_s=15) is None


class TestReleasingIsForHandover:
    def test_a_released_lease_is_expired_immediately(self, lease_store) -> None:
        lease = lease_store.acquire("gateway-a", ttl_s=3600)

        lease_store.release(lease)

        held = lease_store.current()
        assert held is not None and held.expires_at <= time.time()

    def test_a_deposed_holder_cannot_release_the_current_tenure(self, lease_store) -> None:
        # Otherwise a stalled instance shutting down politely would hand its
        # successor's lease away.
        stale = lease_store.acquire("gateway-a", ttl_s=15)
        current = lease_store.acquire("gateway-b", ttl_s=3600)

        lease_store.release(stale)

        held = lease_store.current()
        assert held is not None and held.generation == current.generation
        assert held.expires_at > time.time()

    def test_the_generation_is_not_reset_by_a_release(self, lease_store) -> None:
        # Deleting the row would be the obvious way to release it, and it would
        # let the next acquisition start from 1. A fencing token that repeats
        # fences nothing.
        first = lease_store.acquire("gateway-a", ttl_s=15)
        lease_store.release(first)

        second = lease_store.acquire("gateway-b", ttl_s=15)

        assert second is not None and second.generation > first.generation


class TestNothingHoldsItAtTheStart:
    def test_an_unheld_lease_reads_as_nobody(self, lease_store) -> None:
        assert lease_store.current() is None

    def test_expiry_is_observable(self, lease_store) -> None:
        # The loops need to be able to say "this expired" in a log line an
        # operator can read, which needs the expiry to be a value rather than
        # an internal comparison.
        lease = lease_store.acquire("gateway-a", ttl_s=0.05)
        time.sleep(0.1)

        held = lease_store.current()
        assert held is not None and held.expires_at < time.time()
        assert lease.expires_at < time.time()


class TestTheContract:
    def test_both_adapters_implement_the_same_port(self) -> None:
        from mcp_hangar.infrastructure.persistence.backends.postgresql.management_lease import (
            PostgresManagementLease,
        )

        assert issubclass(SQLiteManagementLease, IManagementLease)
        assert issubclass(PostgresManagementLease, IManagementLease)

    def test_the_two_adapters_agree_on_the_lease_name(self) -> None:
        # The row has to mean the same thing in both, or a deployment that
        # migrates from one backend to the other silently starts a second,
        # parallel lease.
        from mcp_hangar.infrastructure.persistence.backends.postgresql import management_lease as pg
        from mcp_hangar.infrastructure.persistence import sqlite_management_lease as sqlite

        assert pg.FLEET_MANAGEMENT == sqlite.FLEET_MANAGEMENT

    def test_a_lease_cannot_be_edited_after_it_is_granted(self) -> None:
        # It is a fencing token. A caller that could adjust the generation could
        # fence itself back into a tenure it lost.
        lease = Lease(holder="gateway-a", generation=7, expires_at=0.0)

        with pytest.raises(Exception):
            lease.generation = 8  # type: ignore[misc]

    def test_neither_backend_carries_the_others_sql(self) -> None:
        # ADR-019: two implementations, not one with a dialect branch.
        import inspect

        from mcp_hangar.infrastructure.persistence.backends.postgresql import management_lease as pg

        assert "sqlite3" not in inspect.getsource(pg)
        assert "now()" not in inspect.getsource(SQLiteManagementLease)


class TestTheBackendsProvideIt:
    def test_sqlite_hands_out_a_lease(self, tmp_path) -> None:
        from mcp_hangar.infrastructure.persistence.registry import create_backend

        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            assert backend.management_lease().acquire("gateway-a", ttl_s=15) is not None
        finally:
            backend.close()

    def test_a_backend_without_one_is_refused(self) -> None:
        # The completeness rule from ADR-019, applied to the eleventh concern:
        # a backend that cannot hold the lease cannot run more than one gateway,
        # and finding that out at the first handover is finding out too late.
        from mcp_hangar.infrastructure.persistence.registry import (
            IncompletePersistenceBackendError,
            REQUIRED_CONCERNS,
            create_backend,
            register_backend_factory,
        )

        class _AlmostComplete:
            def close(self) -> None:
                return None

        for concern in REQUIRED_CONCERNS:
            if concern != "management_lease":
                setattr(_AlmostComplete, concern, lambda self: object())

        register_backend_factory("almost", lambda config: _AlmostComplete(), replace=True)

        with pytest.raises(IncompletePersistenceBackendError) as excinfo:
            create_backend("almost", {})

        assert excinfo.value.missing == ["management_lease"]
