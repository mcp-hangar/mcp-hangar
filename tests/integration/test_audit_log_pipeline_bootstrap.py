"""After a real bootstrap, a tool call's audit record reaches the owned log pipeline (#1289).

Each configuration runs ``_audit_log_harness.py`` in a fresh interpreter: the real
``bootstrap()``, then ``hangar_call`` through the app ``serve --http`` serves, with
in-memory exporters attached to the providers the bootstrap registered. This
proves the pipeline Hangar builds and feeds, not that a collector receives what
it sends: the OTLP endpoint here is unreachable by design (#1291, #1293).

Before this, nothing built a logger provider, so with the SDK installed every
audit record was dropped; and only the env var turned audit export on.

The record's caller (#1342): the handler read keys ``IdentityContext.to_dict()``
never produces, so no record carried a caller id, a user, a session or a tenant;
a failed call reported a duration of 0.0; and the audit resource was built apart
from the trace resource, so records could not be joined to traces by instance.
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
MODES = ("yaml", "env", "none", "tracing_off", "auth", "bound")

# As `_audit_log_harness.py` mints and declares them.
AUTH_PRINCIPAL, AUTH_TENANT = "user:audit-harness", "tenant-audit"
BOUND_AGENT, BOUND_SESSION, BOUND_TENANT = "agent-audit", "session-audit", "tenant-bound"

# The `auth` run's resource environment: OTEL_RESOURCE_ATTRIBUTES must beat
# MCP_ENVIRONMENT on both resources, since both are built by one function.
RESOURCE_ENV = {
    "MCP_ENVIRONMENT": "from-mcp-env",
    "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=from-otel-env,service.version=9.9.9-harness",
}
JOIN_KEYS = ("service.instance.id", "service.version", "deployment.environment")
CALLER_KEYS = ("mcp.caller.id", "mcp.caller.type", "mcp.caller.roles", "mcp.caller.tenant_id")


def _unreachable_endpoint() -> str:
    """A loopback port nothing listens on: exports fail fast and stay local."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{probe.getsockname()[1]}"


def _run(mode: str, tmp: Path, endpoint: str) -> dict[str, Any]:
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING_", "MCP_ENVIRONMENT"))}
    if mode == "env":
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint
    if mode == "auth":
        env.update(RESOURCE_ENV)
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
    return {**json.loads(out.read_text()), "endpoint": endpoint, "stderr": result.stderr}


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    # Concurrently, under the 60s pytest-timeout the integration job applies.
    tmp = tmp_path_factory.mktemp("audit")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp, _unreachable_endpoint()) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


def _tool_records(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in run["records"] if r["body"] == "tool_invocation"]


def _record_for(run: dict[str, Any], call: str) -> dict[str, Any]:
    trace_id = run["calls"][call]["trace_id"]
    found = [r for r in _tool_records(run) if r["trace_id"] == trace_id]
    assert len(found) == 1, (call, _tool_records(run))
    return found[0]


@pytest.mark.parametrize("mode", ["yaml", "env"])
def test_an_endpoint_in_the_file_or_the_env_builds_the_owned_pipeline(runs, mode):
    run = runs[mode]

    assert run["audit_exporters"] == ["OTLPAuditExporter"]
    assert run["logger_provider"] == "LoggerProvider" and run["owned"] is True
    # One OTLP exporter, to the configured endpoint, metered, behind a batch processor.
    # The gRPC exporter keeps the target it resolved, host:port.
    assert run["endpoints"] == [run["endpoint"].removeprefix("http://")]
    assert run["batched"] == ["_MeteredLogExporter"]
    assert "audit_log_export_initialized" in run["stderr"]


def test_without_an_endpoint_the_null_exporter_is_kept_and_nothing_is_built(runs):
    run = runs["none"]

    assert run["audit_exporters"] == ["NullAuditExporter"]
    assert run["logger_provider"] == "ProxyLoggerProvider" and run["owned"] is False
    assert run["endpoints"] == [] and run["records"] == []
    assert run["calls"]["sampled"]["batch"]["success"] is True


@pytest.mark.parametrize("mode", ["yaml", "env"])
def test_a_tool_call_yields_an_audit_record_on_the_owned_provider(runs, mode):
    run = runs[mode]
    assert run["calls"]["sampled"]["batch"]["success"] is True, run["calls"]

    record = _record_for(run, "sampled")

    assert record["attributes"]["mcp.event.name"] == "tool_invocation"
    assert record["attributes"]["mcp.server.id"] == "math"
    assert record["attributes"]["gen_ai.tool.name"] == "add"
    assert record["attributes"]["mcp.tool.status"] == "success"


def test_the_record_carries_the_trace_and_span_of_the_call(runs):
    run = runs["yaml"]
    record = _record_for(run, "sampled")
    call = run["calls"]["sampled"]
    hangar_spans = {s["span_id"]: s["name"] for s in run["spans"] if s["trace_id"] == call["trace_id"]}

    assert record["sampled"] is True
    # A span Hangar opened for this call, not the remote caller's.
    assert record["span_id"] in hangar_spans, (record, hangar_spans)
    assert record["span_id"] != call["remote_span_id"]


def test_an_unsampled_call_still_emits_its_record(runs):
    run = runs["yaml"]
    assert run["calls"]["unsampled"]["batch"]["success"] is True

    record = _record_for(run, "unsampled")

    assert record["sampled"] is False
    assert record["span_id"] != "0" * 16


def test_with_tracing_off_the_record_is_emitted_without_trace_context(runs):
    """Audit export is its own signal: `tracing.enabled: false` does not turn it off."""
    run = runs["tracing_off"]

    assert run["audit_exporters"] == ["OTLPAuditExporter"] and run["owned"] is True
    assert run["calls"]["no_trace_context"]["batch"]["success"] is True
    [record] = _tool_records(run)
    assert (record["trace_id"], record["span_id"]) == ("0" * 32, "0" * 16)


def test_an_authenticated_call_records_its_caller_and_tenant(runs):
    run = runs["auth"]
    assert run["calls"]["sampled"]["batch"]["success"] is True, run["calls"]

    attributes = _record_for(run, "sampled")["attributes"]

    assert attributes["mcp.caller.id"] == AUTH_PRINCIPAL
    assert attributes["mcp.user.id"] == AUTH_PRINCIPAL
    # An API key authenticates a service-account principal.
    assert attributes["mcp.caller.type"] == "service"
    assert attributes["mcp.caller.tenant_id"] == AUTH_TENANT
    # Neither is invented: the served HTTP path's identity carries no session,
    # and no identity carries roles.
    assert "mcp.session.id" not in attributes
    assert "mcp.caller.roles" not in attributes


def test_a_declared_session_and_agent_reach_the_record(runs):
    run = runs["bound"]
    assert run["calls"]["sampled"]["batch"]["success"] is True, run["calls"]

    attributes = _record_for(run, "sampled")["attributes"]

    assert attributes["mcp.session.id"] == BOUND_SESSION
    assert attributes["mcp.caller.id"] == BOUND_AGENT  # no user: the agent is the caller
    assert attributes["mcp.caller.type"] == "anonymous"
    assert attributes["mcp.caller.tenant_id"] == BOUND_TENANT
    assert "mcp.user.id" not in attributes


def test_a_failed_call_records_its_duration(runs):
    run = runs["auth"]
    assert run["calls"]["failed"]["batch"]["success"] is False, run["calls"]
    trace_id = run["calls"]["failed"]["trace_id"]

    failures = [r["attributes"] for r in _tool_records(run) if r["trace_id"] == trace_id]

    assert failures and all(a["mcp.tool.status"] == "error" for a in failures), failures
    assert all(a["mcp.tool.duration_ms"] > 0.0 for a in failures), failures
    assert all(a["mcp.caller.id"] == AUTH_PRINCIPAL for a in failures), failures


@pytest.mark.parametrize("mode", ["auth", "bound"], ids=["resource-env", "defaults"])
def test_the_audit_resource_joins_the_trace_resource(runs, mode):
    audit, trace = runs[mode]["resources"]["audit"], runs[mode]["resources"]["trace"]

    assert {key: audit.get(key) for key in JOIN_KEYS} == {key: trace.get(key) for key in JOIN_KEYS}
    assert all(trace.get(key) for key in JOIN_KEYS), trace
    if mode == "auth":
        assert audit["deployment.environment"] == "from-otel-env"
        assert audit["service.version"] == "9.9.9-harness"


def test_without_an_identity_the_record_has_no_caller_field(runs):
    attributes = _record_for(runs["yaml"], "sampled")["attributes"]

    assert not {key for key in attributes if key.startswith("mcp.caller.")}, attributes
    assert "mcp.user.id" not in attributes and "mcp.session.id" not in attributes
    assert all(key not in attributes for key in CALLER_KEYS)
