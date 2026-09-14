"""Bootstrap Hangar over a saga state store holding an old circuit-breaker row, then shut it down (#1388).

Run as a script, in its own interpreter, by
``test_a_leftover_circuit_breaker_row_is_ignored.py``:
``python _leftover_circuit_row_harness.py <out.json>``. Not collected by pytest.

A separate process because ``bootstrap()`` fills process-global state -- the
runtime, the saga manager, ``GROUPS`` -- that a second bootstrap in the same
interpreter would inherit.

Before #1388 a gateway saved each group's circuit breaker into the saga state
store on shutdown, as a ``circuit_breaker`` row, and bootstrap tried to read it
back. Nothing writes or reads those rows now, but a deployment that ran an older
version still has one. The harness writes it first, OPEN, under the id of a
configured group, next to a recovery saga checkpoint. Then it runs what
production runs: ``bootstrap()`` on the SQLite persistence backend, and
``ServerLifecycle.shutdown()``, which ``serve`` calls on SIGTERM.
"""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"

#: A group with an old row under its id, and a group without one.
LEFT_OVER, WITHOUT_ROW = "math-pool", "spare-pool"
#: The server the recovery checkpoint names. Not configured, so restoring the
#: checkpoint starts nothing.
RETIRED = "retired-server"
RETRY_STATE = {RETIRED: {"retries": 3, "last_attempt": 100.0, "next_retry": 110.0}}


def _group(member: str) -> dict[str, Any]:
    return {
        "mode": "group",
        "strategy": "priority",
        "min_healthy": 1,
        "auto_start": False,
        "circuit_breaker": {"failure_threshold": 2, "reset_timeout_s": 3600},
        "members": [{"id": member}],
    }


def _config(data_dir: Path) -> dict[str, Any]:
    server = {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]}
    return {
        "mcp_servers": {
            "math-a": dict(server),
            "math-b": dict(server),
            LEFT_OVER: _group("math-a"),
            WITHOUT_ROW: _group("math-b"),
        },
        "persistence": {"backend": "sqlite", "sqlite": {"data_dir": str(data_dir)}},
    }


def _leave_rows(data_dir: Path) -> None:
    """What an older gateway left behind: its last breaker save, and a saga checkpoint."""
    from mcp_hangar.infrastructure.persistence.database_common import SQLiteConfig, SQLiteConnectionFactory
    from mcp_hangar.infrastructure.persistence.saga_state_store import SagaStateStore

    factory = SQLiteConnectionFactory(SQLiteConfig(path=str(data_dir / "saga_state.db")))
    try:
        store = SagaStateStore(factory)
        # `CircuitBreaker.to_dict()` of a breaker that had just opened, as the
        # old shutdown hook wrote it, with an hour of reset timeout still to run.
        store.checkpoint(
            saga_type="circuit_breaker",
            saga_id=LEFT_OVER,
            state_data={
                "state": "open",
                "is_open": True,
                "failure_count": 2,
                "failure_threshold": 2,
                "reset_timeout_s": 3600.0,
                "probe_count": 1,
                "opened_at": time.time(),
            },
            last_event_position=0,
        )
        store.checkpoint(
            saga_type="mcp_server_recovery",
            saga_id="recovery",
            state_data={"retry_state": RETRY_STATE},
            last_event_position=42,
        )
    finally:
        factory.close()


def _rows(data_dir: Path) -> list[list[Any]]:
    """Every saga state row, read straight from the file."""
    with closing(sqlite3.connect(data_dir / "saga_state.db")) as conn:
        cursor = conn.execute("SELECT saga_type, saga_id, state_data, updated_at FROM saga_state ORDER BY 1, 2")
        return [list(row) for row in cursor.fetchall()]


def main(out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps anything else under ./data
    data_dir = out.parent / "data"
    data_dir.mkdir()
    _leave_rows(data_dir)
    report: dict[str, Any] = {"rows_left": _rows(data_dir)}

    from mcp_hangar.infrastructure.saga_manager import get_saga_manager
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle
    from mcp_hangar.server.state import GROUPS

    context = bootstrap(config_dict=_config(data_dir))

    report["groups"] = {
        group_id: {
            "circuit_open": group.circuit_open,
            "breaker": group._circuit_breaker.state.value,
            "failure_count": group._circuit_breaker.failure_count,
        }
        for group_id, group in GROUPS.items()
    }
    report["recovery_retry_state"] = get_saga_manager()._event_sagas["mcp_server_recovery"]._retry_state

    ServerLifecycle(context).shutdown()
    report["rows_after_shutdown"] = _rows(data_dir)

    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    # Daemon threads bootstrap started may still be mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
