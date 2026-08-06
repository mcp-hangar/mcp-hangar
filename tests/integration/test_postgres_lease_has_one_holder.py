"""Against a real PostgreSQL: sixteen replicas race, one wins.

A fake cannot show this. Whether two `INSERT ... ON CONFLICT DO UPDATE ... WHERE`
statements arriving at once can both succeed is the server's answer, not the
adapter's, and an in-memory stand-in that interprets the statements would agree
with whatever the adapter believed when it was written.

So the contention test starts sixteen threads on sixteen connections and asserts
that exactly one comes back holding a lease -- and then that the fifteen losers
wrote nothing.

Opt-in, like the other `live` tests. See
`tests/integration/test_postgres_tail_does_not_skip.py` for the podman one-liner.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import threading
import time

import pytest

from mcp_hangar.domain.contracts.management_lease import Lease
from mcp_hangar.infrastructure.persistence.backends.postgresql.management_lease import PostgresManagementLease

pytestmark = pytest.mark.live

DSN = os.environ.get("HANGAR_TEST_POSTGRES_DSN", "")

psycopg2 = pytest.importorskip("psycopg2", reason="the postgres extra is not installed")

if not DSN:
    pytest.skip("HANGAR_TEST_POSTGRES_DSN is not set", allow_module_level=True)


class _DirectFactory:
    """One connection per call: this test needs many at once, genuinely racing."""

    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(DSN)
        try:
            yield conn
        finally:
            conn.close()


@pytest.fixture
def lease_store():
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS management_lease")
        conn.commit()
    yield PostgresManagementLease(_DirectFactory())
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS management_lease")
        conn.commit()


class TestOnlyOneHolderUnderContention:
    def test_sixteen_replicas_start_at_once_and_one_wins(self, lease_store) -> None:
        # The rollout case: a Deployment scaled to sixteen, all racing for the
        # lease in the same instant on a database that has never held one.
        granted: list[Lease] = []
        guard = threading.Lock()
        start = threading.Barrier(16)

        def contend(index: int) -> None:
            start.wait(timeout=30)
            lease = lease_store.acquire(f"gateway-{index}", ttl_s=30)
            if lease is not None:
                with guard:
                    granted.append(lease)

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert len(granted) == 1, f"{len(granted)} instances believe they are the manager"

    def test_the_losers_wrote_nothing(self, lease_store) -> None:
        winner = lease_store.acquire("gateway-a", ttl_s=30)

        losers = [lease_store.acquire(f"gateway-{i}", ttl_s=30) for i in range(1, 6)]

        assert losers == [None] * 5
        held = lease_store.current()
        assert held is not None
        assert (held.holder, held.generation) == (winner.holder, winner.generation)

    def test_a_second_round_after_expiry_also_has_one_winner(self, lease_store) -> None:
        # Handover under contention, which is the case that actually happens:
        # the holder dies and every survivor tries at once.
        lease_store.acquire("gateway-dead", ttl_s=0.3)
        time.sleep(0.5)

        granted: list[Lease] = []
        guard = threading.Lock()
        start = threading.Barrier(8)

        def contend(index: int) -> None:
            start.wait(timeout=30)
            lease = lease_store.acquire(f"gateway-{index}", ttl_s=30)
            if lease is not None:
                with guard:
                    granted.append(lease)

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert len(granted) == 1


class TestTheTenure:
    def test_an_unexpired_lease_is_not_taken_from_its_holder(self, lease_store) -> None:
        # The difference from SQLite, and the reason the two adapters do not
        # share this behaviour: here an unexpired row may belong to a peer that
        # is alive and managing right now.
        lease_store.acquire("gateway-a", ttl_s=30)

        assert lease_store.acquire("gateway-b", ttl_s=30) is None

    def test_an_expired_lease_is_taken(self, lease_store) -> None:
        lease_store.acquire("gateway-a", ttl_s=0.3)
        time.sleep(0.5)

        assert lease_store.acquire("gateway-b", ttl_s=30) is not None

    def test_the_generation_advances_on_every_handover(self, lease_store) -> None:
        first = lease_store.acquire("gateway-a", ttl_s=0.3)
        time.sleep(0.5)

        second = lease_store.acquire("gateway-b", ttl_s=30)

        assert second.generation == first.generation + 1

    def test_a_renewal_keeps_the_tenure_alive_and_its_generation(self, lease_store) -> None:
        lease = lease_store.acquire("gateway-a", ttl_s=1)

        renewed = lease_store.renew(lease, ttl_s=30)

        assert renewed is not None
        assert renewed.generation == lease.generation
        assert renewed.expires_at > lease.expires_at
        assert lease_store.acquire("gateway-b", ttl_s=30) is None

    def test_a_lapsed_lease_cannot_be_renewed_even_if_nobody_took_it(self, lease_store) -> None:
        # During the lapse there was no holder, so a peer may be mid-acquisition.
        # Extending from underneath that is the race the whole class avoids.
        lease = lease_store.acquire("gateway-a", ttl_s=0.3)
        time.sleep(0.5)

        assert lease_store.renew(lease, ttl_s=30) is None

    def test_a_deposed_holder_is_told_it_lost(self, lease_store) -> None:
        # The stalled-leader sequence: A stalls, the lease expires, B takes it,
        # A's renewal loop wakes up carrying the old generation.
        stale = lease_store.acquire("gateway-a", ttl_s=0.3)
        time.sleep(0.5)
        lease_store.acquire("gateway-b", ttl_s=30)

        assert lease_store.renew(stale, ttl_s=30) is None

    def test_releasing_hands_over_in_seconds_rather_than_a_ttl(self, lease_store) -> None:
        lease = lease_store.acquire("gateway-a", ttl_s=3600)

        lease_store.release(lease)

        assert lease_store.acquire("gateway-b", ttl_s=30) is not None

    def test_a_deposed_holder_cannot_release_the_current_tenure(self, lease_store) -> None:
        stale = lease_store.acquire("gateway-a", ttl_s=0.3)
        time.sleep(0.5)
        current = lease_store.acquire("gateway-b", ttl_s=3600)

        lease_store.release(stale)

        assert lease_store.acquire("gateway-c", ttl_s=30) is None
        held = lease_store.current()
        assert held is not None and held.generation == current.generation


class TestTheClockIsTheDatabases:
    def test_expiry_does_not_depend_on_the_callers_clock(self, lease_store) -> None:
        # Two replicas whose clocks disagree by minutes is ordinary. If the
        # adapter computed `expires_at` locally, a lease written by a fast node
        # would read as long expired to a slow one, and both would manage.
        lease = lease_store.acquire("gateway-a", ttl_s=30)

        with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT EXTRACT(EPOCH FROM (expires_at - now())) FROM management_lease")
            remaining = float(cur.fetchone()[0])
            conn.commit()

        assert 25 < remaining <= 30
        assert lease.expires_at > 0


class TestUpgradingAnExistingDatabase:
    def test_creating_the_table_twice_is_harmless(self, lease_store) -> None:
        # Every replica runs this at startup, and during a rollout they run it
        # at the same moment.
        PostgresManagementLease(_DirectFactory())
        PostgresManagementLease(_DirectFactory())

        assert lease_store.acquire("gateway-a", ttl_s=30) is not None
