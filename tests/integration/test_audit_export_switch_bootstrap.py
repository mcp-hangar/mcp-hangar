"""With audit export switched off, an explicit OTLP endpoint builds no audit log pipeline (#1327).

The switch is ``observability.audit.enabled`` in the file or ``MCP_AUDIT_EXPORT_ENABLED``
in the env. Each case runs ``_audit_log_harness.py`` in a fresh interpreter, as
``test_audit_log_pipeline_bootstrap.py`` does: the real ``bootstrap()`` and a real
``hangar_call``, with an OTLP endpoint set and unreachable by design. The same
run checks what must stay on: trace export, the in-process audit trail, and the
compliance feed ``MCP_COMPLIANCE_FORMAT`` selects, which is a separate handler.

Before the switch, both runs built the logger provider and selected
``OTLPAuditExporter``: the file key was ignored and the env var did not exist.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any

import pytest

pytestmark = pytest.mark.otel_sdk

HARNESS = Path(__file__).with_name("_audit_log_harness.py")
MODES = ("audit_off_file", "audit_off_env")
_STRIPPED = ("OTEL_", "MCP_TRACING_", "MCP_AUDIT_", "MCP_COMPLIANCE_")


def _unreachable_endpoint() -> str:
    """A loopback port nothing listens on: exports fail fast and stay local."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{probe.getsockname()[1]}"


def _run(mode: str, tmp: Path) -> dict[str, Any]:
    endpoint = _unreachable_endpoint()
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    feed = out.with_name("compliance.jsonl")
    env = {k: v for k, v in os.environ.items() if not k.startswith(_STRIPPED)}
    env |= {"MCP_COMPLIANCE_FORMAT": "jsonlines", "MCP_COMPLIANCE_OUTPUT": str(feed)}
    if mode == "audit_off_env":
        env |= {"OTEL_EXPORTER_OTLP_ENDPOINT": endpoint, "MCP_AUDIT_EXPORT_ENABLED": "false"}
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out), endpoint],
        capture_output=True,
        text=True,
        timeout=50,
        env=env,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{mode}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    lines = feed.read_text().splitlines() if feed.exists() else []
    return {**json.loads(out.read_text()), "stderr": result.stderr, "compliance": [json.loads(x) for x in lines]}


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("audit_off")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


@pytest.mark.parametrize("mode", MODES)
def test_switched_off_no_logger_provider_is_built(runs, mode):
    run = runs[mode]

    assert run["audit_configured"] is False
    assert run["logger_provider"] == "ProxyLoggerProvider" and run["owned"] is False
    assert run["endpoints"] == [] and run["batched"] == [] and run["records"] == []
    assert "audit_log_export_initialized" not in run["stderr"]
    assert "audit_log_export_disabled_by_config" in run["stderr"]


@pytest.mark.parametrize("mode", MODES)
def test_switched_off_the_otlp_audit_exporter_is_the_null_one(runs, mode):
    # Two audit handlers: the OTLP one, now Null, and the compliance feed's own.
    assert runs[mode]["audit_exporters"] == ["JSONLinesExporter", "NullAuditExporter"]


@pytest.mark.parametrize("mode", MODES)
def test_switched_off_traces_the_audit_trail_and_the_compliance_feed_are_unaffected(runs, mode):
    run = runs[mode]
    call = run["calls"]["sampled"]
    assert call["batch"]["success"] is True, run["calls"]

    assert run["tracing_enabled"] is True and run["tracer_provider"] == "TracerProvider"
    assert "tracing_initialized" in run["stderr"]
    assert any(span["trace_id"] == call["trace_id"] for span in run["spans"])
    assert run["in_process_audit"] >= 1
    assert [line["tool_name"] for line in run["compliance"] if "tool_name" in line] == ["add"]
