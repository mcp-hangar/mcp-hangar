"""Boot Hangar with a config that declares pins, and stop (#1800).

Run as a script, in its own interpreter, by ``test_the_pin_ledger_survives_restarts.py``:
``python _pin_ledger_harness.py <db> <pins.json>``. ``pins.json`` maps a tool to its
pinned digest (or is empty for no pin). Not collected by pytest.

The real ``bootstrap()``: the config's pins are recorded in the event store it
opens, and the process exits, so the next run is a restart over the same file.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MOCK_PROVIDER = Path(__file__).resolve().parent.parent / "mock_provider.py"


def main(db: Path, pins: dict[str, str]) -> None:
    os.chdir(db.parent)
    server: dict = {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]}
    if pins:
        server["tool_projection"] = {"pins": pins}
    config = {"event_store": {"path": str(db)}, "mcp_servers": {"math": server}}

    from mcp_hangar.server.bootstrap import bootstrap

    context = bootstrap(config_dict=config)
    for mcp_server in context.runtime.repository.get_all().values():
        mcp_server.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()))
