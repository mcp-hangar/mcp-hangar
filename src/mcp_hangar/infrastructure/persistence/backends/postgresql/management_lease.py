"""PostgreSQL adapter for `IManagementLease` -- the one with a real adversary.

Every decision here is taken *inside* a statement rather than around it. Reading
the row, deciding it looks expired, and then writing is the shape that hands the
lease to two replicas at once: both read the same expired row, both decide, both
write, and the second write wins without either learning that it raced.

So each operation is one conditional statement whose `WHERE` carries the whole
decision, and the answer is whether it changed a row.

Time comes from `now()`, the database's clock, never the caller's. Three
replicas do not agree about the time; two of them can be minutes apart without
anyone noticing, and a lease compared against a local clock expires early on one
node and late on another. The database is the only clock all of them share, and
it is the same one that stores `expires_at`.
"""

from __future__ import annotations

from typing import Any

from mcp_hangar.domain.contracts.management_lease import IManagementLease, Lease
from mcp_hangar.infrastructure.persistence.database_common import IConnectionFactory, postgres_ddl
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

#: One row, keyed by a name, so a second kind of lease can be added later
#: without a second table. `expires_at` is a timestamp rather than a duration:
#: a duration would need a "since when", which is the field that gets written
#: from the wrong clock.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS management_lease (
    name TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    generation BIGINT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
"""

#: The lease this gateway's management loops run under.
FLEET_MANAGEMENT = "fleet-management"


class PostgresManagementLease(IManagementLease):
    """The single-holder lease, in a shared PostgreSQL."""

    def __init__(self, connection_factory: IConnectionFactory, *, name: str = FLEET_MANAGEMENT) -> None:
        """Initialize and create the table if it is missing.

        Args:
            connection_factory: An `IConnectionFactory` -- the shared port from
                `infrastructure.persistence.database_common`. This adapter knows
                SQL; it deliberately does not know psycopg2 or pooling.
            name: Which lease. One gateway has one, but naming it means a second
                kind of coordination does not need a second table.
        """
        self._connections = connection_factory
        self._name = name
        with self._connections.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(postgres_ddl(_SCHEMA))
            conn.commit()

    @staticmethod
    def _lease(row: Any, holder: str) -> Lease:
        generation, expires_at = row[0], row[1]
        return Lease(holder=holder, generation=int(generation), expires_at=expires_at.timestamp())

    def acquire(self, holder: str, ttl_s: float) -> Lease | None:
        """Take the lease if it is free or expired.

        One statement. The `INSERT` covers "nobody has ever held it"; the
        `DO UPDATE ... WHERE` covers "the last holder's tenure has run out". A
        replica that loses the race matches no row and gets None, having written
        nothing.

        The generation advances on every acquisition, including a re-acquisition
        by the same instance after its own lease lapsed -- during that gap
        another replica may have held and used the lease, so the tenure that
        follows is genuinely a new one and must not be able to pass for the old.
        """
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO management_lease (name, holder, generation, expires_at)
                VALUES (%s, %s, 1, now() + make_interval(secs => %s))
                ON CONFLICT (name) DO UPDATE
                    SET holder = excluded.holder,
                        generation = management_lease.generation + 1,
                        expires_at = excluded.expires_at
                    WHERE management_lease.expires_at <= now()
                RETURNING generation, expires_at
                """,
                (self._name, holder, ttl_s),
            )
            row = cur.fetchone()
            conn.commit()

        if row is None:
            return None
        lease = self._lease(row, holder)
        logger.info("management_lease_acquired", holder=holder, generation=lease.generation)
        return lease

    def renew(self, lease: Lease, ttl_s: float) -> Lease | None:
        """Extend a tenure that is still ours.

        `generation` is in the `WHERE` as well as `holder`: an instance that
        lost the lease and got it back is on a new tenure, and the renewal loop
        still carrying the old generation must be told it lost rather than
        silently adopted into the new one.

        `expires_at > now()` is there too. A lease that has already lapsed is not
        renewable even if nobody has taken it yet -- during the lapse there was
        no holder, so a peer may have been mid-acquisition, and extending from
        underneath that is the race this whole class avoids.
        """
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE management_lease
                   SET expires_at = now() + make_interval(secs => %s)
                 WHERE name = %s AND holder = %s AND generation = %s AND expires_at > now()
                RETURNING generation, expires_at
                """,
                (ttl_s, self._name, lease.holder, lease.generation),
            )
            row = cur.fetchone()
            conn.commit()

        if row is None:
            logger.warning(
                "management_lease_lost",
                holder=lease.holder,
                generation=lease.generation,
                detail="renewal matched no row; this instance is no longer the manager",
            )
            return None
        return self._lease(row, lease.holder)

    def release(self, lease: Lease) -> None:
        """Give the lease up now, so a peer takes over in seconds not a TTL.

        Expires the row rather than deleting it, so the generation survives.
        Deleting would let the next acquisition start from 1 again, and a
        fencing token that repeats is a fencing token that fences nothing.
        """
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE management_lease SET expires_at = now()
                 WHERE name = %s AND holder = %s AND generation = %s
                """,
                (self._name, lease.holder, lease.generation),
            )
            conn.commit()
        logger.info("management_lease_released", holder=lease.holder, generation=lease.generation)

    def current(self) -> Lease | None:
        """Who holds it now, expired or not."""
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT holder, generation, expires_at FROM management_lease WHERE name = %s",
                (self._name,),
            )
            row = cur.fetchone()
            conn.commit()

        if row is None:
            return None
        return Lease(holder=str(row[0]), generation=int(row[1]), expires_at=row[2].timestamp())
