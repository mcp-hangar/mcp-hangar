"""Against a real PostgreSQL: a deposed leader's deregistration changes nothing.

The SQLite fence is tested in `tests/unit/test_a_deposed_leader_deletes_nothing.py`
and it proves the shape. This proves the SQL -- a condition over two tables, on
the server that actually decides it, including the part SQLite cannot express:
a tenure that has *lapsed on the database's clock* without anyone taking it is
already over, and a write from it must not land.

Opt-in, like the other `live` tests. See
`tests/integration/test_postgres_tail_does_not_skip.py` for the podman one-liner.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import time

import pytest

from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.infrastructure.persistence.backends.postgresql.config_repository import (
    PostgresMcpServerConfigRepository,
)
from mcp_hangar.infrastructure.persistence.backends.postgresql.management_lease import PostgresManagementLease

pytestmark = pytest.mark.live

DSN = os.environ.get("HANGAR_TEST_POSTGRES_DSN", "")

psycopg2 = pytest.importorskip("psycopg2", reason="the postgres extra is not installed")

if not DSN:
    pytest.skip("HANGAR_TEST_POSTGRES_DSN is not set", allow_module_level=True)


class _DirectFactory:
    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(DSN)
        try:
            yield conn
        finally:
            conn.close()


@pytest.fixture
def storage():
    factory = _DirectFactory()
    with factory.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS management_lease, mcp_server_configs")
        conn.commit()
    configs = PostgresMcpServerConfigRepository(factory)
    lease = PostgresManagementLease(factory)
    yield configs, lease
    with factory.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS management_lease, mcp_server_configs")
        conn.commit()


def _snapshot(mcp_server_id: str = "math") -> McpServerConfigSnapshot:
    return McpServerConfigSnapshot(mcp_server_id=mcp_server_id, mode="subprocess", command=["python"])


async def _recovered(configs) -> list[str]:
    """What the next process would restore -- deletion here is a soft one."""
    return [config.mcp_server_id for config in await configs.get_all()]


@pytest.mark.asyncio
class TestTheDeposedLeaderSequence:
    async def test_its_deregistration_affects_zero_rows(self, storage) -> None:
        configs, lease_store = storage
        stale = lease_store.acquire("gateway-a", ttl_s=0.3)
        await configs.save(_snapshot("math"))
        time.sleep(0.5)
        lease_store.acquire("gateway-b", ttl_s=30)

        deleted = await configs.delete_while_leased("math", stale.holder, stale.generation)

        assert deleted is False
        assert await _recovered(configs) == ["math"]

    async def test_the_current_holder_can_still_deregister(self, storage) -> None:
        configs, lease_store = storage
        current = lease_store.acquire("gateway-b", ttl_s=30)
        await configs.save(_snapshot("math"))

        deleted = await configs.delete_while_leased("math", current.holder, current.generation)

        assert deleted is True
        assert await _recovered(configs) == []

    async def test_a_lapsed_tenure_deregisters_nothing_even_before_a_peer_takes_it(self, storage) -> None:
        # The part only a real server can rule on: the row still names this
        # holder and this generation, and the write is still refused, because
        # `expires_at` has passed on the database's clock. A peer may be
        # acquiring at this very moment.
        configs, lease_store = storage
        lapsed = lease_store.acquire("gateway-a", ttl_s=0.3)
        await configs.save(_snapshot("math"))
        time.sleep(0.5)

        deleted = await configs.delete_while_leased("math", lapsed.holder, lapsed.generation)

        assert deleted is False
        assert await _recovered(configs) == ["math"]

    async def test_a_released_tenure_deregisters_nothing(self, storage) -> None:
        configs, lease_store = storage
        released = lease_store.acquire("gateway-a", ttl_s=3600)
        await configs.save(_snapshot("math"))
        lease_store.release(released)

        deleted = await configs.delete_while_leased("math", released.holder, released.generation)

        assert deleted is False

    async def test_another_instance_cannot_borrow_the_generation(self, storage) -> None:
        # Holder and generation are both in the condition. A generation on its
        # own would let any instance that happened to know the number write as
        # though it were the holder.
        configs, lease_store = storage
        current = lease_store.acquire("gateway-b", ttl_s=30)
        await configs.save(_snapshot("math"))

        deleted = await configs.delete_while_leased("math", "gateway-a", current.generation)

        assert deleted is False
        assert await _recovered(configs) == ["math"]

    async def test_an_absent_row_is_not_an_error(self, storage) -> None:
        # A convergence loop deciding twice about the same departure is normal,
        # and the second decision must not look like a fencing failure.
        configs, lease_store = storage
        current = lease_store.acquire("gateway-b", ttl_s=30)

        assert await configs.delete_while_leased("nothing-here", current.holder, current.generation) is False
