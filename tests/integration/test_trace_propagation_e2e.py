"""A caller's trace reaches the upstream through the real invocation path (#1284).

Each case enters the app ``serve --http`` serves with a stateless
``tools/call hangar_call`` whose ``_meta.traceparent`` names a remote caller
span, and completes (or is refused) against a controlled upstream: the stdio
``tests/mock_provider.py`` or an in-process HTTP upstream. The spans come from
Hangar's own ``init_tracing()`` provider with nothing patched; see
``_trace_harness.py`` for why that runs in a subprocess.

The previous version of this file called the private ``_execute_call`` with a
mock context and a patched tracer, and passed ``batch_start_time=0.0``, so the
global-timeout gate refused the call before any invocation: it asserted a
parent on a span that never reached an upstream. Those direct-executor checks
now live in ``tests/unit/test_execute_call_trace_parent.py``.

Invariants, not span counts. Known defects are strict xfails naming the issue
that removes them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

pytestmark = pytest.mark.otel_sdk

HARNESS = Path(__file__).with_name("_trace_harness.py")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("trace") / "run.json"
    env = {**os.environ, "MCP_TRACING_ENABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": ""}
    # Under the 60s pytest-timeout the integration job applies.
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(out)], capture_output=True, text=True, timeout=45, env=env
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    return json.loads(out.read_text())


def _ids(traceparent: str | None) -> tuple[str, str] | None:
    """(trace id, span id) named by a W3C traceparent, or None."""
    if not traceparent:
        return None
    _version, trace_id, span_id, _flags = traceparent.split("-")
    return trace_id, span_id


class Tree:
    """One scenario's trace: its spans, its outcome, and the carriers upstreams saw."""

    def __init__(self, run: dict[str, Any], scenario: str) -> None:
        info = run["scenarios"][scenario]
        self.trace_id: str = info["trace_id"]
        self.remote_span_id: str = info["remote_span_id"]
        self.is_error: bool = info["is_error"]
        self.batch: dict[str, Any] = info["batch"]
        self.spans = [s for s in run["spans"] if s["trace_id"] == self.trace_id]
        self._by_id = {s["span_id"]: s for s in self.spans}
        self.stdio_calls = [
            c for c in run["stdio_seen"] if c["method"] == "tools/call" and self._ours(c["traceparent"])
        ]
        self.http_calls = [c for c in run["http_seen"] if c["method"] == "tools/call" and self._ours(c["header"])]

    def _ours(self, traceparent: str | None) -> bool:
        return (_ids(traceparent) or ("", ""))[0] == self.trace_id

    def one(self, name: str) -> dict[str, Any]:
        found = [s for s in self.spans if s["name"] == name]
        assert len(found) == 1, f"expected one {name!r} in trace {self.trace_id}: {[s['name'] for s in self.spans]}"
        return found[0]

    def descends_from(self, span: dict[str, Any], ancestor: dict[str, Any]) -> bool:
        parent = self._by_id.get(span["parent_id"])
        while parent is not None:
            if parent["span_id"] == ancestor["span_id"]:
                return True
            parent = self._by_id.get(parent["parent_id"])
        return False

    def assert_served_under_the_caller(self) -> None:
        """Remote caller -> SDK SERVER span -> ``hangar_call`` -> ``batch.execute``."""
        server = self.one("tools/call hangar_call")
        assert server["kind"] == "SERVER"
        assert (server["parent_id"], server["parent_is_remote"]) == (self.remote_span_id, True)
        root = self.one("hangar_call")
        assert root["parent_id"] == server["span_id"]
        assert self.one("batch.execute")["parent_id"] == root["span_id"]


class TestASuccessfulCallOverStdio:
    def test_the_tool_runs_on_the_upstream(self, run):
        tree = Tree(run, "stdio_success")

        assert tree.is_error is False
        assert tree.batch["success"] is True, tree.batch
        assert tree.batch["results"][0]["result"] == {"result": 3}

    def test_the_server_span_hangar_call_and_batch_execute_nest_under_the_caller(self, run):
        Tree(run, "stdio_success").assert_served_under_the_caller()

    def test_the_upstream_client_span_descends_from_the_call_span(self, run):
        tree = Tree(run, "stdio_success")
        client = tree.one("execute_tool add")

        assert client["kind"] == "CLIENT"
        assert tree.descends_from(client, tree.one("batch.call.add"))

    def test_the_stdio_meta_names_the_upstream_client_span(self, run):
        tree = Tree(run, "stdio_success")

        sent = [_ids(c["traceparent"]) for c in tree.stdio_calls]
        assert sent == [(tree.trace_id, tree.one("execute_tool add")["span_id"])]

    def test_no_span_on_the_path_ends_in_error(self, run):
        tree = Tree(run, "stdio_success")

        for name in ("tools/call hangar_call", "hangar_call", "batch.execute", "batch.call.add", "execute_tool add"):
            assert tree.one(name)["status"] != "ERROR", name

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="#1270 batch.call is parented on the remote caller, not batch.execute",
    )
    def test_the_call_span_is_a_child_of_batch_execute(self, run):
        tree = Tree(run, "stdio_success")

        assert tree.one("batch.call.add")["parent_id"] == tree.one("batch.execute")["span_id"]


class TestASuccessfulCallOverHttp:
    def test_the_tool_runs_on_the_upstream(self, run):
        tree = Tree(run, "http_success")

        assert tree.batch["success"] is True, tree.batch
        tree.assert_served_under_the_caller()

    def test_the_traceparent_header_names_the_upstream_client_span(self, run):
        tree = Tree(run, "http_success")
        client = tree.one("execute_tool add")

        assert tree.descends_from(client, tree.one("batch.call.add"))
        assert [_ids(c["header"]) for c in tree.http_calls] == [(tree.trace_id, client["span_id"])]

    @pytest.mark.xfail(
        strict=True, raises=AssertionError, reason="#1271 HTTP _meta is injected before the CLIENT span opens"
    )
    def test_the_meta_traceparent_names_the_upstream_client_span(self, run):
        tree = Tree(run, "http_success")

        assert [_ids(c["meta"]) for c in tree.http_calls] == [(tree.trace_id, tree.one("execute_tool add")["span_id"])]


class TestAGovernanceDenial:
    def test_the_call_is_refused(self, run):
        tree = Tree(run, "denied")

        assert tree.batch["success"] is False
        assert tree.batch["results"][0]["error_type"] == "ToolAccessDeniedError", tree.batch

    def test_the_call_span_ends_in_error_and_nothing_reaches_the_upstream(self, run):
        tree = Tree(run, "denied")

        tree.assert_served_under_the_caller()
        assert tree.one("batch.call.multiply")["status"] == "ERROR"
        assert [s["name"] for s in tree.spans if s["kind"] == "CLIENT"] == []
        assert tree.stdio_calls == []


class TestAnUpstreamFailure:
    def test_the_call_fails_with_the_upstreams_error(self, run):
        tree = Tree(run, "upstream_failure")

        assert tree.batch["success"] is False
        assert "division by zero" in tree.batch["results"][0]["error"], tree.batch

    def test_the_call_span_ends_in_error_after_reaching_the_upstream(self, run):
        tree = Tree(run, "upstream_failure")
        call = tree.one("batch.call.divide")
        client = tree.one("execute_tool divide")

        assert call["status"] == "ERROR"
        assert tree.descends_from(client, call)
        assert [_ids(c["traceparent"]) for c in tree.stdio_calls] == [(tree.trace_id, client["span_id"])]

    @pytest.mark.xfail(
        strict=True, raises=AssertionError, reason="#1277 the CLIENT span ends before the response is classified"
    )
    def test_the_client_span_records_the_upstream_failure(self, run):
        assert Tree(run, "upstream_failure").one("execute_tool divide")["status"] == "ERROR"


def test_two_concurrent_requests_keep_separate_trees(run):
    trees = [Tree(run, "concurrent_1"), Tree(run, "concurrent_2")]
    held = [c.get("overlapped") for c in run["http_seen"] if "overlapped" in c]

    # The upstream held each call until the other arrived, so both were in flight.
    assert held == [True, True], held
    for tree in trees:
        assert tree.batch["success"] is True, tree.batch
        tree.assert_served_under_the_caller()
        client = tree.one("execute_tool add")
        assert tree.descends_from(client, tree.one("batch.call.add"))
        assert [_ids(c["header"]) for c in tree.http_calls] == [(tree.trace_id, client["span_id"])]
