"""An upstream CLIENT span ends with the outcome its transport client decided (#1277).

Over HTTP the span used to close once the request was sent, so every failure
classified afterwards -- an HTTP status, a terminated session, a body that is
not JSON, a JSON-RPC error in the body or in an SSE event -- left it UNSET, and
``notify`` checked the status after its span too. Stdio returned the answer
from inside its span without looking at it.

Each case runs through ``HttpClient.call``/``notify`` with httpx's post patched,
or ``StdioClient.call`` over a fake process, into a real SDK exporter. The
``error.type`` values are the mcp SDK server middleware's. What the caller gets
back -- the envelope returned or the exception raised -- is pinned per case and
must not depend on whether tracing is on.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import json
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from mcp_hangar.domain.exceptions import ClientError
from mcp_hangar.protocol import SESSION_TERMINATED_CODE, SESSION_TERMINATED_REASON

pytestmark = pytest.mark.otel_sdk

REQUEST_ID = "req-1"
#: Stands in for anything the upstream said; it must never reach the span.
PAYLOAD = "upstream-payload-7c1e"
#: Everything a CLIENT span may carry: its identity and the bounded outcome.
SPAN_KEYS = {"mcp.method.name", "gen_ai.operation.name", "gen_ai.tool.name", "error.type"}

TOOL_OK = {"result": {"content": [{"type": "text", "text": "ok"}]}}
RPC_ERROR = {"error": {"code": -32602, "message": PAYLOAD}}
SSE_ERROR = {"error": {"code": -32603, "message": PAYLOAD}}
TOOL_ERROR = {"result": {"isError": True, "content": [{"type": "text", "text": PAYLOAD}]}}

#: An httpx post stand-in's answer to one request: a response built from the
#: request body, or an exception to raise.
Reply = Callable[[dict[str, Any]], httpx.Response] | Exception


def _message(**body: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": REQUEST_ID, **body}


def _json(status: int = 200, **body: Any) -> Reply:
    return lambda request: httpx.Response(status, json={"jsonrpc": "2.0", "id": request.get("id"), **body})


def _raw(status: int, content: bytes = b"", content_type: str = "text/plain") -> Reply:
    return lambda _request: httpx.Response(status, content=content, headers={"Content-Type": content_type})


def _sse(**body: Any) -> Reply:
    def reply(request: dict[str, Any]) -> httpx.Response:
        event = json.dumps({"jsonrpc": "2.0", "id": request["id"], **body})
        return httpx.Response(
            200, text=f"event: message\ndata: {event}\n\n", headers={"Content-Type": "text/event-stream"}
        )

    return reply


@contextmanager
def _traced() -> Iterator[Any]:
    """Hangar's spans go to a local SDK provider and in-memory exporter, never the global one."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with (
        patch("mcp_hangar.observability.tracing.get_tracer", side_effect=provider.get_tracer),
        patch("mcp_hangar.observability.tracing._initialized", True),
    ):
        yield exporter


@contextmanager
def _untraced() -> Iterator[None]:
    # Whatever provider an earlier test registered globally, Hangar's tracing is off.
    with patch("mcp_hangar.observability.tracing._tracing_active", return_value=False):
        yield


@pytest.fixture()
def otel() -> Iterator[Any]:
    with _traced() as exporter:
        yield exporter


def _outcome(send: Callable[[], Any]) -> tuple[Any, ...]:
    """What the caller sees: the value returned, or the exception's type and text."""
    try:
        return ("returned", send())
    except Exception as exc:  # noqa: BLE001 -- the outcome under test
        return ("raised", type(exc), str(exc))


def _http(replies: list[Reply], op: str, session: str | None = None, **config: Any) -> tuple[tuple[Any, ...], int]:
    """Send through ``HttpClient.<op>`` while httpx's post answers ``replies`` in turn, the last one repeating.

    Returns the outcome and how many requests were posted.
    """
    from mcp_hangar.http_client import AuthConfig, HttpClient, HttpClientConfig

    client = HttpClient(
        endpoint="http://upstream:8080",
        auth_config=AuthConfig(),
        http_config=HttpClientConfig(retry_backoff_factor=0, **config),
    )
    client._mcp_session_id = session
    posted: list[dict[str, Any]] = []

    def post(url: str, *, json: dict[str, Any], headers: Any = None, timeout: Any = None) -> httpx.Response:
        posted.append(json)
        reply = replies[min(len(posted), len(replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply(json)

    if op == "call":
        send = lambda: client.call("tools/call", {"name": "t", "arguments": {}})  # noqa: E731
    else:
        send = lambda: client.notify("notifications/progress", {"progressToken": "p", "progress": 1})  # noqa: E731
    with (
        patch.object(client._client, "post", side_effect=post),
        patch("mcp_hangar.http_client.uuid", **{"uuid4.return_value": REQUEST_ID}),
    ):
        return _outcome(send), len(posted)


def _stdio(reply: dict[str, Any] | Exception | None, timeout: float = 1.0) -> tuple[Any, ...]:
    """``StdioClient.call`` against a fake process: it answers ``reply``, fails the write, or stays silent (None)."""
    from mcp_hangar.stdio_client import StdioClient

    process = MagicMock(spec=subprocess.Popen)
    process.pid = 4242
    process.stdin = MagicMock()
    process.stdout = MagicMock()
    process.poll.return_value = None
    with patch("mcp_hangar.stdio_client.threading.Thread"):
        client = StdioClient(process)

    def write(line: str) -> None:
        # What the reader thread does with the process's answer.
        if isinstance(reply, Exception):
            raise reply
        if reply is not None:
            request = json.loads(line)
            with client.pending_lock:
                pending = client.pending.pop(request["id"])
            pending.result_queue.put({"jsonrpc": "2.0", "id": request["id"], **reply})

    process.stdin.write.side_effect = write
    with patch("mcp_hangar.stdio_client.uuid", **{"uuid4.return_value": REQUEST_ID}):
        return _outcome(lambda: client.call("tools/call", {"name": "t", "arguments": {}}, timeout=timeout))


def _client_span(exporter: Any) -> Any:
    from opentelemetry.trace import SpanKind

    [span] = [s for s in exporter.get_finished_spans() if s.kind is SpanKind.CLIENT]
    return span


def _assert_span_outcome(span: Any, error_type: str | None) -> None:
    from opentelemetry.trace import StatusCode

    assert span.status.status_code is (StatusCode.ERROR if error_type else StatusCode.UNSET)
    assert span.attributes.get("error.type") == error_type, dict(span.attributes)
    assert set(span.attributes) <= SPAN_KEYS, dict(span.attributes)
    # Nothing the upstream sent -- message, content, body -- is copied onto the span.
    recorded = [*span.attributes.values(), span.status.description, *(e.attributes for e in span.events)]
    assert not any(PAYLOAD in str(value) for value in recorded), recorded


# name -> (replies, session id held, outcome, posts, CLIENT span error.type or None for UNSET).
CALLS: dict[str, tuple[list[Reply], str | None, tuple[Any, ...], int, str | None]] = {
    "success": ([_json(**TOOL_OK)], None, ("returned", _message(**TOOL_OK)), 1, None),
    "sse-success": ([_sse(**TOOL_OK)], None, ("returned", _message(**TOOL_OK)), 1, None),
    "http-status": (
        [_raw(400, PAYLOAD.encode())],
        None,
        ("returned", {"error": {"code": -32000, "message": "HTTP error: 400", "data": PAYLOAD}}),
        1,
        "http_400",
    ),
    "session-terminated": (
        [_raw(404)],
        "dead-session",
        (
            "returned",
            {
                "error": {
                    "code": SESSION_TERMINATED_CODE,
                    "message": "Session terminated",
                    "data": {"reason": SESSION_TERMINATED_REASON},
                }
            },
        ),
        1,
        "http_404",
    ),
    "jsonrpc-error": ([_json(**RPC_ERROR)], None, ("returned", _message(**RPC_ERROR)), 1, "-32602"),
    "tool-error": ([_json(**TOOL_ERROR)], None, ("returned", _message(**TOOL_ERROR)), 1, "tool_error"),
    "invalid-json": (
        [_raw(200, f"<html>{PAYLOAD}</html>".encode(), "application/json")],
        None,
        (
            "returned",
            {"error": {"code": -32700, "message": "Invalid JSON response: Expecting value: line 1 column 1 (char 0)"}},
        ),
        1,
        "JSONDecodeError",
    ),
    "sse-error": ([_sse(**SSE_ERROR)], None, ("returned", _message(**SSE_ERROR)), 1, "-32603"),
    "timeout": (
        [httpx.ReadTimeout("read timed out")],
        None,
        ("raised", TimeoutError, "timeout: tools/call after 30.0s"),
        1,
        "ReadTimeout",
    ),
    # Connect failures are retried, all three attempts inside the one span.
    "connection-error": (
        [httpx.ConnectError("connection refused")],
        None,
        ("raised", ClientError, "connection_failed: connection refused"),
        3,
        "ConnectError",
    ),
    "retried-then-answered": (
        [_raw(503), _raw(503), _json(**TOOL_OK)],
        None,
        ("returned", _message(**TOOL_OK)),
        3,
        None,
    ),
    "retried-out": (
        [_raw(503)],
        None,
        ("returned", {"error": {"code": -32000, "message": "HTTP error: 503", "data": ""}}),
        3,
        "http_503",
    ),
}

# name -> (replies, outcome, CLIENT span error.type or None for UNSET). A notification is never retried.
NOTIFIES: dict[str, tuple[list[Reply], tuple[Any, ...], str | None]] = {
    "accepted": ([_raw(202)], ("returned", None), None),
    "rejected": ([_raw(500, PAYLOAD.encode())], ("raised", ClientError, "notify_rejected: HTTP 500"), "http_500"),
    "redirected": ([_raw(307)], ("raised", ClientError, "notify_rejected: HTTP 307"), "http_307"),
    "unreachable": (
        [httpx.ConnectError("connection refused")],
        ("raised", ClientError, "notify_failed: connection refused"),
        "ConnectError",
    ),
}

# name -> (process reply, call timeout, outcome, CLIENT span error.type or None for UNSET).
STDIO: dict[str, tuple[dict[str, Any] | Exception | None, float, tuple[Any, ...], str | None]] = {
    "success": (TOOL_OK, 1.0, ("returned", _message(**TOOL_OK)), None),
    "jsonrpc-error": (RPC_ERROR, 1.0, ("returned", _message(**RPC_ERROR)), "-32602"),
    "tool-error": (TOOL_ERROR, 1.0, ("returned", _message(**TOOL_ERROR)), "tool_error"),
    # A code that is not an integer is not copied: error.type stays bounded.
    "non-integer-code": (
        {"error": {"code": PAYLOAD, "message": "x"}},
        1.0,
        ("returned", _message(error={"code": PAYLOAD, "message": "x"})),
        "_OTHER",
    ),
    "timeout": (None, 0.05, ("raised", TimeoutError, "timeout: tools/call after 0.05s"), "TimeoutError"),
    "write-failed": (
        BrokenPipeError("pipe closed"),
        1.0,
        ("raised", ClientError, "write_failed: pipe closed"),
        "ClientError",
    ),
}


class TestHttpCall:
    @pytest.mark.parametrize("case", CALLS)
    def test_the_client_span_ends_with_the_outcome(self, otel, case: str) -> None:
        replies, session, outcome, posts, error_type = CALLS[case]

        assert _http(replies, "call", session) == (outcome, posts)
        _assert_span_outcome(_client_span(otel), error_type)

    @pytest.mark.parametrize("case", CALLS)
    def test_the_caller_gets_the_same_outcome_and_retries_traced_or_not(self, case: str) -> None:
        replies, session, outcome, posts, _error_type = CALLS[case]

        with _traced():
            traced = _http(replies, "call", session)
        with _untraced():
            untraced = _http(replies, "call", session)
        assert traced == untraced == (outcome, posts)


class TestHttpNotify:
    @pytest.mark.parametrize("case", NOTIFIES)
    def test_the_client_span_ends_with_the_outcome(self, otel, case: str) -> None:
        replies, outcome, error_type = NOTIFIES[case]

        assert _http(replies, "notify") == (outcome, 1)
        _assert_span_outcome(_client_span(otel), error_type)

    @pytest.mark.parametrize("case", NOTIFIES)
    def test_the_caller_gets_the_same_outcome_traced_or_not(self, case: str) -> None:
        replies, outcome, _error_type = NOTIFIES[case]

        with _traced():
            traced = _http(replies, "notify")
        with _untraced():
            untraced = _http(replies, "notify")
        assert traced == untraced == (outcome, 1)


class TestStdioCall:
    @pytest.mark.parametrize("case", STDIO)
    def test_the_client_span_ends_with_the_outcome(self, otel, case: str) -> None:
        reply, timeout, outcome, error_type = STDIO[case]

        assert _stdio(reply, timeout) == outcome
        _assert_span_outcome(_client_span(otel), error_type)

    @pytest.mark.parametrize("case", STDIO)
    def test_the_caller_gets_the_same_outcome_traced_or_not(self, case: str) -> None:
        reply, timeout, outcome, _error_type = STDIO[case]

        with _traced():
            traced = _stdio(reply, timeout)
        with _untraced():
            untraced = _stdio(reply, timeout)
        assert traced == untraced == outcome


@pytest.mark.parametrize("answer", [TOOL_OK, RPC_ERROR, TOOL_ERROR], ids=["success", "jsonrpc-error", "tool-error"])
def test_stdio_and_http_record_the_same_answer_alike(answer: dict[str, Any]) -> None:
    recorded = []
    for send in (lambda: _http([_json(**answer)], "call"), lambda: _stdio(answer)):
        with _traced() as exporter:
            send()
        span = _client_span(exporter)
        recorded.append((span.status.status_code, span.attributes.get("error.type")))

    assert recorded[0] == recorded[1]
