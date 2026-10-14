"""Concurrent approve, deny, expire and cancel leave one terminal state, whoever races (#1794).

Both approval repositories updated a request with ``UPDATE ... WHERE
approval_id = ?``: no check of the state it was in, none of the rows it changed.
A resolve on one replica, an expiry on another and a cancel from a batch
deadline could each overwrite the others, so the record said one thing and the
held call did another.

``_approval_race_harness.py`` runs each contender as its own process against one
SQLite file, released together by a shared start time. Every round has several
contenders; exactly one may report that it moved the request, and the record
must end in that contender's state.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.approvals.persistence.sqlite_approval_repository import SqliteApprovalRepository
from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig

HARNESS = Path(__file__).with_name("_approval_race_harness.py")
EXPECTED = {
    "approve": ApprovalState.APPROVED,
    "deny": ApprovalState.DENIED,
    "expire": ApprovalState.EXPIRED,
    "cancel": ApprovalState.CANCELLED,
}


def _request() -> ApprovalRequest:
    now = datetime.now(UTC)
    return ApprovalRequest(
        approval_id=str(uuid.uuid4()),
        mcp_server_id="payments",
        tool_name="refund_payment",
        arguments={},
        arguments_hash="0" * 64,
        requested_at=now,
        expires_at=now + timedelta(minutes=5),
        state=ApprovalState.PENDING,
        channel="event_stream",
    )


async def _seed(db: Path, request: ApprovalRequest) -> None:
    await SqliteApprovalRepository(Database(DatabaseConfig(path=str(db)))).save(request)


async def _read(db: Path, approval_id: str) -> ApprovalRequest | None:
    return await SqliteApprovalRepository(Database(DatabaseConfig(path=str(db)))).get(approval_id)


def _race(db: Path, approval_id: str, actions: list[str]) -> dict[str, bool]:
    go_at = time.time() + 1.5
    procs = [
        subprocess.Popen(
            [sys.executable, str(HARNESS), str(db), approval_id, action, str(go_at)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for action in actions
    ]
    outcomes: dict[str, bool] = {}
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err[-2000:]
        line = json.loads(out.strip().splitlines()[-1])
        outcomes[line["action"]] = line["won"]
    return outcomes


@pytest.mark.parametrize("round_", range(5))
def test_four_processes_racing_for_one_request_produce_one_winner(tmp_path: Path, round_: int) -> None:
    db = tmp_path / "approvals.db"
    request = _request()
    asyncio.run(_seed(db, request))

    outcomes = _race(db, request.approval_id, ["approve", "deny", "expire", "cancel"])

    winners = [action for action, won in outcomes.items() if won]
    assert len(winners) == 1, f"{outcomes}: exactly one contender may move a pending request"
    final = asyncio.run(_read(db, request.approval_id))
    assert final is not None
    assert final.state is EXPECTED[winners[0]], f"the record says {final.state}, the winner was {winners[0]}"
    assert final.decided_by == f"{winners[0]}-proc", "the winner's decision, not a later writer's, is on the record"


def test_a_decided_request_cannot_be_moved_again(tmp_path: Path) -> None:
    db = tmp_path / "approvals.db"
    request = _request()
    asyncio.run(_seed(db, request))

    first = _race(db, request.approval_id, ["approve"])
    later = _race(db, request.approval_id, ["expire", "deny"])

    assert first == {"approve": True}
    assert later == {"expire": False, "deny": False}
    final = asyncio.run(_read(db, request.approval_id))
    assert final is not None and final.state is ApprovalState.APPROVED


def test_a_missing_request_reports_no_change(tmp_path: Path) -> None:
    db = tmp_path / "approvals.db"
    asyncio.run(_seed(db, _request()))

    assert _race(db, str(uuid.uuid4()), ["approve"]) == {"approve": False}
