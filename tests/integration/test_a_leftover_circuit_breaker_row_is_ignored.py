"""A circuit-breaker row an older gateway left in the saga state store is ignored (#1388).

Before #1388 a gateway saved every group's breaker into the saga state store on
shutdown, and bootstrap tried to read it back. The read never restored anything:
it ran over the context's groups while those were still an empty dict. Both
halves are gone, so a deployment upgrading from an older version keeps a row
that nothing reads and nothing rewrites.

``_leftover_circuit_row_harness.py`` runs the real ``bootstrap()`` over such a
store, in a fresh interpreter, and then the shutdown ``serve`` runs on SIGTERM.
A gateway on the SQLite backend is standalone, so it is its own lease holder:
before #1388 that shutdown rewrote the row for one group and added one for the
other.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_leftover_circuit_row_harness.py")

# As `_leftover_circuit_row_harness.py` names them.
LEFT_OVER, WITHOUT_ROW, RETIRED = "math-pool", "spare-pool", "retired-server"
CLOSED = {"circuit_open": False, "breaker": "closed", "failure_count": 0}


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("leftover-circuit-row") / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(out)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    report: dict[str, Any] = json.loads(out.read_text())
    report["stderr"] = result.stderr
    return report


def _circuit_rows(rows: list[list[Any]]) -> list[list[Any]]:
    return [row for row in rows if row[0] == "circuit_breaker"]


class TestBootstrapIgnoresTheRow:
    def test_the_store_held_an_open_row_under_a_configured_group(self, run: dict[str, Any]) -> None:
        [row] = _circuit_rows(run["rows_left"])

        assert row[1] == LEFT_OVER
        assert json.loads(row[2])["state"] == "open"

    def test_every_group_starts_closed(self, run: dict[str, Any]) -> None:
        assert run["groups"] == {LEFT_OVER: CLOSED, WITHOUT_ROW: CLOSED}

    def test_the_other_saga_rows_still_load(self, run: dict[str, Any]) -> None:
        assert run["recovery_retry_state"] == {
            RETIRED: {"retries": 3, "last_attempt": 100.0, "next_retry": 110.0},
        }

    def test_nothing_raises(self, run: dict[str, Any]) -> None:
        assert "Traceback" not in run["stderr"]


class TestShutdownWritesNoRow:
    def test_the_old_row_is_untouched_and_no_group_gets_one(self, run: dict[str, Any]) -> None:
        assert _circuit_rows(run["rows_after_shutdown"]) == _circuit_rows(run["rows_left"])
