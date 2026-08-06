"""SQLite adapter for `IManagementLease` -- the holder is always this process.

SQLite is the standalone backend. It is a file, one process writes it, and there
is no peer that could hold the lease: that is the same property that makes its
event positions commit-ordered, stated from the other side. So acquisition
always succeeds.

**It is a real row, not a stub returning True.** Two reasons, and neither is
symmetry for its own sake:

- The generation has to be **monotonic across restarts**, or fencing means
  nothing here. A stub handing out generation 1 every time would let a write
  from a previous process -- one that stalled, was killed, and left a sweep
  half-finished in a queue somewhere -- pass a fencing check under the new
  process's number. The row is what remembers.
- The management loops are gated on holding the lease (1.3). A backend whose
  lease is a special case in the *caller* means the caller has two code paths,
  and the standalone one is the one nobody tests under load.

**Acquisition steals unconditionally, and that is the correct answer here, not
a shortcut.** On PostgreSQL an unexpired row means a peer may still be alive and
managing, so acquisition must wait for the TTL. On SQLite an unexpired row can
only be a *dead predecessor*: the file admits one process, and this process is
it. Waiting out the TTL would mean a standalone gateway that restarts manages
nothing for fifteen seconds, for the sake of a peer that cannot exist.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
import time

from mcp_hangar.domain.contracts.management_lease import IManagementLease, Lease
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS management_lease (
    name TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    generation INTEGER NOT NULL,
    expires_at REAL NOT NULL
);
"""

#: The lease this gateway's management loops run under. Same name as the
#: PostgreSQL adapter uses, so the row means the same thing in both.
FLEET_MANAGEMENT = "fleet-management"


class SQLiteManagementLease(IManagementLease):
    """The management lease, kept in a SQLite file next to everything else."""

    def __init__(self, db_path: str | Path, *, name: str = FLEET_MANAGEMENT) -> None:
        """Open (creating if needed) the lease table.

        Args:
            db_path: Path to the SQLite file.
            name: Which lease.
        """
        self._db_path = str(db_path)
        self._name = name
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def acquire(self, holder: str, ttl_s: float) -> Lease | None:
        """Take the lease. Always granted -- see the module docstring.

        Returns:
            The lease, one generation past whatever the file remembered.
        """
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT generation FROM management_lease WHERE name = ?", (self._name,)).fetchone()
            generation = int(row["generation"]) + 1 if row else 1
            expires_at = time.time() + ttl_s
            conn.execute(
                """
                INSERT INTO management_lease (name, holder, generation, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (name) DO UPDATE
                    SET holder = excluded.holder,
                        generation = excluded.generation,
                        expires_at = excluded.expires_at
                """,
                (self._name, holder, generation, expires_at),
            )
            conn.commit()

        logger.info("management_lease_acquired", holder=holder, generation=generation, backend="sqlite")
        return Lease(holder=holder, generation=generation, expires_at=expires_at)

    def renew(self, lease: Lease, ttl_s: float) -> Lease | None:
        """Extend the tenure.

        Still conditional on it being ours. Nothing here can take the lease
        away, but a test double or a future second holder in the same process
        could, and a renewal that ignores the condition would paper over it.
        """
        expires_at = time.time() + ttl_s
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE management_lease SET expires_at = ?
                 WHERE name = ? AND holder = ? AND generation = ?
                """,
                (expires_at, self._name, lease.holder, lease.generation),
            )
            conn.commit()
            changed = cursor.rowcount

        if not changed:
            logger.warning("management_lease_lost", holder=lease.holder, generation=lease.generation)
            return None
        return Lease(holder=lease.holder, generation=lease.generation, expires_at=expires_at)

    def release(self, lease: Lease) -> None:
        """Give the lease up, keeping the generation for the next tenure."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE management_lease SET expires_at = ? WHERE name = ? AND holder = ? AND generation = ?",
                (time.time(), self._name, lease.holder, lease.generation),
            )
            conn.commit()

    def current(self) -> Lease | None:
        """Who holds it now, expired or not."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT holder, generation, expires_at FROM management_lease WHERE name = ?",
                (self._name,),
            ).fetchone()
        if row is None:
            return None
        return Lease(holder=str(row["holder"]), generation=int(row["generation"]), expires_at=float(row["expires_at"]))
