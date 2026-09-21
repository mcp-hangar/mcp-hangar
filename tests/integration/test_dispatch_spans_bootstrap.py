"""A bootstrapped gateway traces its dispatches, on both non-executor entry points (#1297).

The unit tests build a bus and a `RateLimitMiddleware` by hand and prove the
span shape. What they cannot prove is that the bus a real gateway runs is the
bus that was instrumented, or that the REST router and the MCP management tools
-- neither of which goes through the batch executor -- reach it.

So this drives `bootstrap()` in a subprocess and reads the spans back off the
provider that bootstrap registered. See `_dispatch_span_harness.py` for why it
is a subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.otel_sdk

HARNESS = Path(__file__).with_name("_dispatch_span_harness.py")
DISPATCH = "dispatch.ListMcpServersQuery"
OPERATION = "hangar.dispatch.operation"
OUTCOME = "hangar.dispatch.outcome"


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("dispatch") / "run.json"
    env = {**os.environ, "MCP_TRACING_ENABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": ""}
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(out)], capture_output=True, text=True, timeout=90, env=env
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    return json.loads(out.read_text())


def _dispatches(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [span for span in run["spans"] if span["name"] == DISPATCH]


def test_both_entry_points_answered(run: dict[str, Any]) -> None:
    """If either call failed, every assertion below would be about nothing."""
    assert run["rest_status"] == 200, run["rest_status"]
    assert run["tool_status"] == 200, run["tool_status"]


def test_the_bootstrapped_command_bus_is_the_instrumented_one(run: dict[str, Any]) -> None:
    """The rate-limit middleware is registered by `bootstrap()` and by nothing else.

    A unit test constructs it, so a unit test cannot notice if bootstrap stopped
    registering it -- and a dispatch span that never covers middleware would be
    a span covering half of what it claims.
    """
    assert "RateLimitMiddleware" in run["command_bus_middleware"], run["command_bus_middleware"]


def test_a_rest_route_and_a_management_tool_each_leave_a_dispatch_span(run: dict[str, Any]) -> None:
    """Two entry points that never touch the batch executor, both now traced."""
    dispatches = _dispatches(run)
    assert len(dispatches) == 2, [span["name"] for span in run["spans"]]
    assert all(span["attributes"][OPERATION] == "ListMcpServersQuery" for span in dispatches)
    assert all(span["attributes"][OUTCOME] == "success" for span in dispatches)


def test_the_management_tool_dispatch_nests_under_the_request_span(run: dict[str, Any]) -> None:
    """On `/mcp` the SDK's SERVER span is already open, so the dispatch joins it."""
    server_spans = {span["span_id"] for span in run["spans"] if span["name"].startswith("tools/call ")}
    assert server_spans, "the SDK opened no SERVER span; the harness drove the wrong surface"

    nested = [span for span in _dispatches(run) if span["parent"] in server_spans]
    assert len(nested) == 1, _dispatches(run)


def test_the_rest_dispatch_is_a_root_because_rest_has_no_request_span(run: dict[str, Any]) -> None:
    """Asserted as observed, and it is a finding rather than a success.

    The MCP surface enters through the SDK's SERVER span, so a dispatch there
    nests. The REST router opens nothing, so its dispatch span is the trace
    root -- one REST call is a trace with a single span and no record of the
    request that caused it.

    That is not this change's doing: REST had no request span before it and no
    dispatch span either, so the root is new only because the span is. It is
    pinned here so that instrumenting the REST surface flips this test rather
    than passing quietly under it.
    """
    roots = [span for span in _dispatches(run) if span["parent"] is None]
    assert len(roots) == 1, _dispatches(run)
