"""One contender in an approval race, run as its own process (#1794).

``python _approval_race_harness.py <db> <approval_id> <action> <go_at>``: wait
until the wall clock reaches ``go_at``, so every contender fires together, then
try ``action`` against the one pending request in the shared SQLite file and
print what came of it as one JSON line. Not collected by pytest.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime

from mcp_hangar.approvals.models import ApprovalState
from mcp_hangar.approvals.persistence.sqlite_approval_repository import SqliteApprovalRepository
from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig

ACTIONS = {
    "approve": ApprovalState.APPROVED,
    "deny": ApprovalState.DENIED,
    "expire": ApprovalState.EXPIRED,
    "cancel": ApprovalState.CANCELLED,
}


async def main(db: str, approval_id: str, action: str, go_at: float) -> None:
    repository = SqliteApprovalRepository(Database(DatabaseConfig(path=db)))
    await repository.get(approval_id)  # open the file and settle the schema before the start gun
    while time.time() < go_at:
        await asyncio.sleep(0.0005)
    won = await repository.transition(
        approval_id, (ApprovalState.PENDING,), ACTIONS[action], f"{action}-proc", datetime.now(UTC), action
    )
    print(json.dumps({"action": action, "won": won}))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])))
