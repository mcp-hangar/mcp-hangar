"""Every call on the front door's projected surface leaves one log line (#1362).

On a five-replica front door, five successful calls to one projected tool left no
line naming it, and a client's deterministic failure -- reported with its own
request id -- had nothing server-side to be matched against. A deny left no
trace at all, and that is the case this exists for.

What is driven is the app ``serve --http`` builds, ``mcp_app_for_serving`` over
``build_serving_mcp_server``, over HTTP, with ``tool_access.mode: front_door``
and a caller that carries a tenant. What is read is what ``setup_logging``'s
JSON formatter renders, so the claim is about the line an operator's shipper
sees. Two things are stood in for: the auth middleware, which puts the verified
principal on ``request.state.auth``, and the upstream, reached through a fake
``BatchExecutor``. The projection, the policy that filters it, the identity
bridge, the session guard and the handler are the shipped ones.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
import inspect
import io
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from starlette.testclient import TestClient

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.contracts.session_suspension import VERIFIED_SESSION_ID_KEY
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.fastmcp_server import flat_call_log, flat_tool_projection
from mcp_hangar.fastmcp_server.flat_call_log import CALL_LOG_EVENT, DENIAL_CODES
from mcp_hangar.logging_config import setup_logging
from mcp_hangar.server.api.sessions import get_session_suspension_registry
from mcp_hangar.server.session_guard import SESSION_SUSPENDED_REASON, SUSPENSION_UNCHECKED_REASON
from mcp_hangar.server.tools.batch.models import BatchResult, CallResult
from mcp_hangar.tasks_wire import HEADER_MISMATCH

_BASE_URL = "http://127.0.0.1:8000"  # a Host the SDK's DNS-rebinding guard accepts
_MODERN_VERSION = "2026-07-28"
_ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "call-log-probe", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

_SERVER = "graph"
_TENANT = "tenant-a"
_CALLER = "svc:agent"
_SESSION = "s-call-log"

#: In every call's arguments. It must never reach the log.
CANARY = "CANARY-1362-do-not-log"

_SERVED = "read_graph"
_REFUSED_AT_DISPATCH = "search_nodes"  # the policy moved between the listing and the call
_TIMED_OUT = "open_nodes"
_ANSWERED_WITH_ERROR = "create_entities"
_EXPLODES = "add_observations"
_DENIED_BY_POLICY = "delete_entities"  # never projected to this tenant
_UNKNOWN = "no_such_tool"


def _answer(tool: str, batch_id: str) -> CallResult:
    """What the fake fleet answers for *tool*."""
    if tool == _EXPLODES:
        raise RuntimeError("the executor itself failed")
    answers = {
        _SERVED: CallResult(
            index=0, call_id=batch_id, success=True, result={"content": [{"type": "text", "text": "{}"}]}
        ),
        _REFUSED_AT_DISPATCH: CallResult(
            index=0,
            call_id=batch_id,
            success=False,
            error="Tool not available for this mcp_server",
            error_type="ToolAccessDeniedError",
        ),
        _TIMED_OUT: CallResult(index=0, call_id=batch_id, success=False, error="timed out", error_type="TimeoutError"),
        _ANSWERED_WITH_ERROR: CallResult(
            index=0,
            call_id=batch_id,
            success=True,
            result={"content": [{"type": "text", "text": "no"}], "isError": True},
        ),
    }
    return answers[tool]


class _Upstream:
    """The fake ``BatchExecutor``, answering per tool."""

    def execute(self, *, batch_id: str, calls: list[Any], **_kw: Any) -> BatchResult:
        (call,) = calls
        result = _answer(call.tool, batch_id)
        return BatchResult(
            batch_id=batch_id,
            success=result.success,
            total=1,
            succeeded=int(result.success),
            failed=int(not result.success),
            elapsed_ms=0.0,
            results=[result],
        )


def _principal() -> Principal:
    return Principal(
        id=PrincipalId(_CALLER),
        type=PrincipalType.SERVICE_ACCOUNT,
        tenant_id=_TENANT,
        metadata={VERIFIED_SESSION_ID_KEY: _SESSION},
    )


def _authenticated(app: Any, principal: Principal) -> Any:
    """What the auth middleware does for a verified caller: the principal on ``request.state.auth``."""

    async def with_principal(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["auth"] = SimpleNamespace(principal=principal)
        await app(scope, receive, send)

    return with_principal


@pytest.fixture
def front_door(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from mcp_hangar.server.bootstrap import build_serving_mcp_server
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    reset_tool_projection_registry()
    reset_tool_access_resolver()
    resolver = get_tool_access_resolver()
    resolver.set_topology_mode("front_door")
    projected = [_SERVED, _REFUSED_AT_DISPATCH, _TIMED_OUT, _ANSWERED_WITH_ERROR, _EXPLODES, _DENIED_BY_POLICY]
    get_tool_projection_registry().build_from_tools(
        _SERVER, [ToolSchema(name=name, description=name, input_schema={"type": "object"}) for name in projected]
    )
    resolver.set_standalone_member_policy(_SERVER, _TENANT, ToolAccessPolicy(deny_list=(_DENIED_BY_POLICY,)))

    monkeypatch.setattr("mcp_hangar.server.tools.batch.BatchExecutor", _Upstream)
    monkeypatch.setattr("mcp_hangar.server.tools.tool_permissions.management_tools_for", lambda _ctx: frozenset())
    get_session_suspension_registry().clear()

    app = _authenticated(mcp_app_for_serving(build_serving_mcp_server()), _principal())
    with TestClient(app, base_url=_BASE_URL) as client:
        yield client

    get_session_suspension_registry().clear()
    reset_tool_projection_registry()
    reset_tool_access_resolver()


CallLog = Callable[[], tuple[list[dict[str, Any]], str]]


@pytest.fixture
def call_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[CallLog]:
    """Every ``mcp_hangar`` line at INFO, rendered by the production JSON formatter.

    Returns the ``front_door_tool_call`` lines written since the last read, and
    all the output. Only the formatter is taken from ``setup_logging``: the root
    logger goes straight back to what pytest had, so nothing of this test's
    outlives it or writes to a stream pytest has since closed.
    """
    saved_config = structlog.get_config()
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    setup_logging(level="INFO", json_format=True)
    (production,) = root.handlers
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    # Loggers cached on first use stay bound to whichever pipeline they first
    # met, here or in a later test; a fresh module logger meets this one.
    structlog.configure(cache_logger_on_first_use=False)
    monkeypatch.setattr(flat_call_log, "logger", structlog.get_logger(flat_call_log.__name__))

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(production.formatter)
    package = logging.getLogger("mcp_hangar")
    saved_package_level = package.level
    package.setLevel(logging.INFO)
    package.addHandler(handler)

    def read() -> tuple[list[dict[str, Any]], str]:
        output = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
        lines = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        return [line for line in lines if line.get("event") == CALL_LOG_EVENT], output

    yield read
    package.removeHandler(handler)
    package.setLevel(saved_package_level)
    structlog.configure(**saved_config)


def _call(client: TestClient, tool: str, request_id: int | str = 1) -> tuple[int, dict[str, Any]]:
    """One stateless 2026-07-28 ``tools/call`` POST: the HTTP status and the JSON-RPC response.

    The status is not always 200: the modern transport maps a JSON-RPC error to
    the status the spec requires (404 for ``-32601``, 400 for a header refusal).
    """
    headers = {
        "MCP-Protocol-Version": _MODERN_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": tool,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    params = {"name": tool, "arguments": {"query": CANARY}, "_meta": _ENVELOPE}
    body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params})
    response = client.post("/mcp", headers=headers, content=body)
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    return response.status_code, json.loads(text)


class TestEveryServedCallLeavesOneLine:
    def test_n_calls_leave_n_lines_naming_the_tool(self, front_door: TestClient, call_log: CallLog) -> None:
        for request_id in range(1, 6):
            assert "result" in _call(front_door, _SERVED, request_id)[1]

        lines, _ = call_log()

        assert [line["tool"] for line in lines] == [_SERVED] * 5
        assert [line["request_id"] for line in lines] == [1, 2, 3, 4, 5]
        for line in lines:
            assert line["level"] == "info"
            assert line["outcome"] == "ok"
            assert line["reason"] is None
            assert line["principal_id"] == _CALLER
            assert line["tenant_id"] == _TENANT
            assert line["duration_ms"] >= 0

    @pytest.mark.parametrize(
        ("tool", "outcome", "reason"),
        [
            (_REFUSED_AT_DISPATCH, "denied", "ToolAccessDeniedError"),
            (_DENIED_BY_POLICY, "not_found", "not_projected"),
            (_UNKNOWN, "not_found", "unknown"),
            (_TIMED_OUT, "tool_error", "TimeoutError"),
            (_ANSWERED_WITH_ERROR, "tool_error", None),
            (_EXPLODES, "error", "RuntimeError"),
        ],
    )
    def test_every_outcome_is_one_line_with_its_verdict(
        self, front_door: TestClient, call_log: CallLog, tool: str, outcome: str, reason: str | None
    ) -> None:
        _call(front_door, tool, "req-7")

        lines, _ = call_log()

        assert len(lines) == 1, lines
        (line,) = lines
        assert (line["tool"], line["outcome"], line["reason"]) == (tool, outcome, reason)
        assert (line["principal_id"], line["tenant_id"], line["request_id"]) == (_CALLER, _TENANT, "req-7")

    def test_a_suspended_sessions_refusal_is_a_denied_line(self, front_door: TestClient, call_log: CallLog) -> None:
        # Refused by the decorator before the handler body runs -- no flat map,
        # no executor, no BatchCallCompleted -- so nothing else would log it.
        get_session_suspension_registry().suspend(_SESSION)

        _, answer = _call(front_door, _SERVED, "caller-chosen-id")

        assert answer["result"]["isError"] is True
        (line,) = call_log()[0]
        assert (line["outcome"], line["reason"]) == ("denied", SESSION_SUSPENDED_REASON)
        assert (line["principal_id"], line["tenant_id"]) == (_CALLER, _TENANT)
        # Nothing a suspended caller chose is echoed, as in the guard's own line.
        assert (line["tool"], line["request_id"]) == (None, None)

    def test_a_refused_header_is_a_rejected_line(
        self, front_door: TestClient, call_log: CallLog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The ADR-025 opt-in: a call whose Mcp-Param-* headers nobody could check.
        monkeypatch.setattr(flat_tool_projection, "_param_validation_required", True)
        monkeypatch.setattr(flat_tool_projection, "_param_validation_skipped", lambda _ctx: True)

        _, answer = _call(front_door, _SERVED)

        assert answer["error"]["code"] == HEADER_MISMATCH
        (line,) = call_log()[0]
        assert (line["outcome"], line["reason"]) == ("rejected", "header_mismatch")

    def test_a_mixed_run_is_one_line_per_call_and_no_arguments(self, front_door: TestClient, call_log: CallLog) -> None:
        tools = [_SERVED, _DENIED_BY_POLICY, _SERVED, _UNKNOWN, _REFUSED_AT_DISPATCH, _TIMED_OUT, _SERVED]
        for request_id, tool in enumerate(tools, start=1):
            _call(front_door, tool, request_id)

        lines, output = call_log()

        assert [(line["request_id"], line["tool"]) for line in lines] == list(enumerate(tools, start=1))
        # Nothing Hangar logged at INFO for these calls carries an argument.
        assert CANARY not in output
        assert all("arguments" not in line and "query" not in line for line in lines)


class TestTheCallerSeesNoDifference:
    def test_denied_and_missing_are_the_same_answer(self, front_door: TestClient, call_log: CallLog) -> None:
        # The log tells an operator which it was; the caller must not be able
        # to (#905). Only the echoed name differs.
        denied_status, denied = _call(front_door, _DENIED_BY_POLICY, 9)
        missing_status, missing = _call(front_door, _UNKNOWN, 9)

        assert denied_status == missing_status
        assert denied["error"]["code"] == missing["error"]["code"] == -32601
        assert denied["error"]["message"].replace(_DENIED_BY_POLICY, "?") == missing["error"]["message"].replace(
            _UNKNOWN, "?"
        )
        assert denied["error"].get("data") == missing["error"].get("data")
        assert [line["reason"] for line in call_log()[0]] == ["not_projected", "unknown"]


class TestTheVerdictVocabulary:
    #: What the executor can return that is not a refusal: the call failed, or
    #: could not be made, and no gate decided anything.
    _NOT_REFUSALS = frozenset(
        {
            "CancellationError",
            "CircuitBreakerOpen",
            "McpServerNotFoundError",
            "McpServerStartError",
            "NoAvailableMemberError",
            "TaskRelayNotSupported",
            "TimeoutError",
        }
    )

    def test_every_code_the_executor_writes_is_sorted_on_purpose(self) -> None:
        # A new refusal the executor learns must be added to DENIAL_CODES or
        # here. Unsorted, it would log as a tool_error: the line survives, the
        # verdict is mislabelled.
        from mcp_hangar.server.tools.batch import executor

        written: set[str] = set()
        for node in ast.walk(ast.parse(inspect.getsource(executor))):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if callee in ("refuse", "_refuse") and node.args and isinstance(node.args[-1], ast.Constant):
                written.add(node.args[-1].value)
            written |= {
                kw.value.value
                for kw in node.keywords
                if kw.arg == "error_type" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
            }

        assert written - DENIAL_CODES == self._NOT_REFUSALS

    def test_the_session_guards_reasons_are_denials(self) -> None:
        assert {SESSION_SUSPENDED_REASON, SUSPENSION_UNCHECKED_REASON} <= DENIAL_CODES
