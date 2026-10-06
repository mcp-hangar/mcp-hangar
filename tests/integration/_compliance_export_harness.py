"""Bootstrap Hangar with a compliance (SIEM) feed, and report what it did (#1701).

Run as a script, in its own interpreter, by ``test_compliance_export_fails_closed.py``:
``python _compliance_export_harness.py <mode> <out.json>``. Not collected by pytest.
The parent sets ``MCP_COMPLIANCE_FORMAT`` and ``MCP_COMPLIANCE_OUTPUT``.

``boot`` runs the real ``bootstrap()`` and nothing else: a feed the gateway
cannot export to must stop it here, so a refusal is this process exiting
non-zero with the ``ConfigurationError`` on stderr.

``runtime`` boots with a writable feed, then serves real ``hangar_call``s
through ``mcp_app_for_serving`` -- the app ``serve --http`` serves -- to
``tests/mock_provider.py``: one call, the feed's directory removed, two calls,
the directory back, one call. After each phase it records the dropped-record
counter, the ``/health/ready`` body and ``hangar_health()``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from _audit_log_harness import BASE_URL, HEADERS, MOCK_PROVIDER, _hangar_call


def _counter() -> dict[str, float]:
    from mcp_hangar.metrics import COMPLIANCE_EXPORT_FAILURES_TOTAL

    return {f"{s.labels['format']}/{s.labels['reason']}": s.value for s in COMPLIANCE_EXPORT_FAILURES_TOTAL.collect()}


def _phase(client: Any, calls: int, repository: Any) -> dict[str, Any]:
    from mcp_hangar.server.lifecycle import build_readiness_report
    from mcp_hangar.server.tools.health import hangar_health

    results = [_hangar_call(client, "sampled", None, HEADERS)["batch"]["success"] for _ in range(calls)]
    ready, code = build_readiness_report(repository)
    return {"calls": results, "counter": _counter(), "ready": ready, "ready_code": code, "health": hangar_health()}


def main(mode: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data
    config = {"mcp_servers": {"math": {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]}}}

    from mcp_hangar.server.bootstrap import bootstrap

    context = bootstrap(config_dict=config)
    if mode == "boot":
        out.write_text(json.dumps({"booted": True}))
        sys.stdout.flush()
        os._exit(0)

    from starlette.testclient import TestClient

    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    feed_dir = Path(os.environ["MCP_COMPLIANCE_OUTPUT"]).parent
    repository = context.runtime.repository
    phases: dict[str, Any] = {}
    with TestClient(mcp_app_for_serving(context.mcp_server), base_url=BASE_URL) as client:
        phases["writable"] = _phase(client, 1, repository)
        shutil.rmtree(feed_dir)
        phases["removed"] = _phase(client, 2, repository)
        feed_dir.mkdir()
        phases["restored"] = _phase(client, 1, repository)

    for server in repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps(phases))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
