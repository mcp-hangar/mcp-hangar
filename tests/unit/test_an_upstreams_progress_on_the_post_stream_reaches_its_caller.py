"""The progress an upstream sends on a POST's own SSE body reaches its caller (#2314).

Streamable HTTP lets an upstream send the notifications of a request on the SSE
body of that request's POST response. A stateless (2026-07-28) upstream has no
other channel: it has no standing GET stream (#882). Hangar minted the token and
sent it (#883), but read the POST body for the result only, so every
``notifications/progress`` there was dropped and a long call looked frozen.

The POST body's progress now goes to the handler the GET stream hands its
messages to (`McpServer._route_upstream_message`, which hands progress to
`progress_relay`), as each event is read. Only progress that carries the token
this call sent: the body is this request's stream, so a token the call did not
send is dropped, never handed to the caller it was minted for.
"""

from __future__ import annotations

import gzip
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from mcp_hangar.domain.services import progress_relay
from mcp_hangar.http_client import HttpClient, HttpClientConfig

SSE = {"Content-Type": "text/event-stream"}
TOKEN = "hangar-progress-own"


def _event(message: dict[str, Any]) -> str:
    return f"event: message\ndata: {json.dumps(message)}\n\n"


def _progress(token: Any, progress: float, total: float | None = 3) -> str:
    params: dict[str, Any] = {"progressToken": token, "progress": progress}
    if total is not None:
        params["total"] = total
    return _event({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})


def _answer(request_id: str, text: str = "done") -> str:
    return _event({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})


def _call_params(token: Any = TOKEN) -> dict[str, Any]:
    params: dict[str, Any] = {"name": "slow", "arguments": {}}
    if token is not None:
        params["_meta"] = {"progressToken": token}
    return params


class Upstream:
    """Answers each POST with an SSE body built from the request id it carries."""

    def __init__(self, *events: str, headers: dict[str, str] | None = None) -> None:
        self.events = events
        self.headers = headers or SSE
        self.sent: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.sent.append(body)
        text = "".join(e.replace("{id}", body["id"]) for e in self.events)
        return httpx.Response(200, headers=self.headers, text=text)


def _stateless_client(
    transport: httpx.BaseTransport | None = None, endpoint: str = "http://upstream.test/mcp"
) -> HttpClient:
    client = HttpClient(endpoint, http_config=HttpClientConfig(max_retries=1))
    client.modern_envelope = True
    if transport is not None:
        defaults = client._client.headers
        client._client.close()
        client._client = httpx.Client(headers=defaults, transport=transport)
    return client


class Handler:
    """Records what `McpServer._route_upstream_message` would be handed, in order."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def __call__(self, msg: dict[str, Any]) -> None:
        self.messages.append(msg)

    @property
    def progress(self) -> list[float]:
        return [m["params"]["progress"] for m in self.messages]


@pytest.fixture
def handler() -> Handler:
    return Handler()


class TestAStatelessUpstreamsPostStream:
    def test_its_progress_reaches_the_handler_in_order_and_the_result_is_unchanged(self, handler: Handler) -> None:
        upstream = Upstream(_progress(TOKEN, 1), _progress(TOKEN, 2), _answer("{id}"))
        client = _stateless_client(httpx.MockTransport(upstream))
        client.start_notification_stream(handler)

        answer = client.call("tools/call", _call_params())

        assert handler.progress == [1, 2]
        assert all(m["params"]["progressToken"] == TOKEN for m in handler.messages)
        assert answer == {
            "jsonrpc": "2.0",
            "id": upstream.sent[0]["id"],
            "result": {"content": [{"type": "text", "text": "done"}]},
        }
        # Still no standing GET stream for a stateless upstream.
        assert client._sse_thread is None

    def test_a_token_the_call_did_not_send_is_dropped(self, handler: Handler) -> None:
        upstream = Upstream(_progress("hangar-progress-other", 1), _progress(TOKEN, 2), _answer("{id}"))
        client = _stateless_client(httpx.MockTransport(upstream))
        client.start_notification_stream(handler)

        client.call("tools/call", _call_params())

        assert handler.progress == [2]

    def test_a_call_that_asked_for_no_progress_hands_on_none(self, handler: Handler) -> None:
        upstream = Upstream(_progress(TOKEN, 1), _answer("{id}"))
        client = _stateless_client(httpx.MockTransport(upstream))
        client.start_notification_stream(handler)

        answer = client.call("tools/call", _call_params(token=None))

        assert handler.messages == []
        assert answer["result"]["content"][0]["text"] == "done"

    def test_an_integer_token_matches_only_itself(self, handler: Handler) -> None:
        upstream = Upstream(_progress("7", 1), _progress(True, 2), _progress(7, 3), _answer("{id}"))
        client = _stateless_client(httpx.MockTransport(upstream))
        client.start_notification_stream(handler)

        client.call("tools/call", _call_params(token=7))

        assert handler.progress == [3]

    def test_other_notifications_on_it_are_not_handed_on(self, handler: Handler) -> None:
        other = [
            _event({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "x"}}),
            _event({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}),
            _event(
                {
                    "jsonrpc": "2.0",
                    "id": "srv-1",
                    "method": "notifications/progress",
                    "params": {"progressToken": TOKEN, "progress": 9},
                }
            ),
        ]
        upstream = Upstream(*other, _progress(TOKEN, 1), _answer("{id}"))
        client = _stateless_client(httpx.MockTransport(upstream))
        client.start_notification_stream(handler)

        client.call("tools/call", _call_params())

        assert handler.progress == [1]

    def test_a_handler_that_raises_does_not_fail_the_call(self) -> None:
        def broken(msg: dict[str, Any]) -> None:
            raise RuntimeError("bad forwarder")

        upstream = Upstream(_progress(TOKEN, 1), _answer("{id}"))
        client = _stateless_client(httpx.MockTransport(upstream))
        client.start_notification_stream(broken)

        answer = client.call("tools/call", _call_params())

        assert answer["result"]["content"][0]["text"] == "done"

    def test_without_a_handler_the_call_is_answered_as_before(self) -> None:
        upstream = Upstream(_progress(TOKEN, 1), _answer("{id}"))
        client = _stateless_client(httpx.MockTransport(upstream))

        answer = client.call("tools/call", _call_params())

        assert answer["result"]["content"][0]["text"] == "done"


class TestLineEndings:
    """SSE ends a line in CRLF, LF or CR; the Python SDK's server sends CRLF."""

    def test_a_crlf_body_hands_on_its_progress(self, handler: Handler) -> None:
        events = [e.replace("\n", "\r\n") for e in (_progress(TOKEN, 1), _progress(TOKEN, 2), _answer("{id}"))]
        client = _stateless_client(httpx.MockTransport(Upstream(*events)))
        client.start_notification_stream(handler)

        answer = client.call("tools/call", _call_params())

        assert handler.progress == [1, 2]
        assert answer["result"]["content"][0]["text"] == "done"

    @pytest.mark.parametrize("ending", ["\r\n", "\r", "\n"])
    def test_every_split_of_the_body_hands_on_each_event_once(self, ending: str) -> None:
        from mcp_hangar.http_client import _PostStreamRelay  # here: main has no such class (fail-before runs)

        # The answer follows, as on a real body: a CR that ends the input may yet be half a CRLF.
        body = (_progress(TOKEN, 1) + _progress(TOKEN, 2) + _answer("r-1")).replace("\n", ending).encode()
        for cut in range(1, len(body)):
            seen: list[dict[str, Any]] = []
            relay = _PostStreamRelay(TOKEN, lambda msg, size, seen=seen: seen.append(msg))
            relay.feed(body[:cut])
            relay.feed(body[cut:])
            assert [m["params"]["progress"] for m in seen] == [1, 2], cut


class TestAResumedPostStream:
    def test_its_progress_reaches_the_handler_too(self, handler: Handler) -> None:
        """A legacy upstream that closed the POST stream early resumes it on a GET (#2014)."""
        request_id = ""

        def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal request_id
            if request.method == "POST":
                request_id = json.loads(request.content)["id"]
                return httpx.Response(200, headers=SSE, text=_progress(TOKEN, 1) + "id: e-1\nretry: 0\ndata: \n\n")
            return httpx.Response(200, headers=SSE, text=_progress(TOKEN, 2) + _answer(request_id))

        client = HttpClient("http://upstream.test/mcp", http_config=HttpClientConfig(max_retries=1))
        defaults = client._client.headers
        client._client.close()
        client._client = httpx.Client(headers=defaults, transport=httpx.MockTransport(upstream))
        client._on_message = handler  # the GET stream itself is not under test here

        answer = client.call("tools/call", _call_params())

        assert handler.progress == [1, 2]
        assert answer["result"]["content"][0]["text"] == "done"


class TestThroughTheProgressRelay:
    """End to end through `progress_relay`, as `_route_upstream_message` hands it on."""

    def test_progress_reaches_only_the_caller_whose_token_it_carries(self) -> None:
        mine: list[float] = []
        theirs: list[float] = []
        own, other = progress_relay.mint_token(), progress_relay.mint_token()
        progress_relay.register(own, lambda p, t, m: mine.append(p))
        progress_relay.register(other, lambda p, t, m: theirs.append(p))
        try:
            # The upstream puts another caller's live token on this call's stream.
            upstream = Upstream(_progress(other, 1), _progress(own, 2), _answer("{id}"))
            client = _stateless_client(httpx.MockTransport(upstream))
            client.start_notification_stream(lambda msg: progress_relay.forward(msg.get("params") or {}))

            client.call("tools/call", _call_params(token=own))
        finally:
            progress_relay.unregister(own)
            progress_relay.unregister(other)

        assert mine == [2]
        assert theirs == []


class _StreamingUpstream(BaseHTTPRequestHandler):
    """Sends one progress event, then holds the result until the test saw that progress."""

    delivered: threading.Event
    gzip_body = False

    def do_POST(self) -> None:  # noqa: N802 -- http.server's name
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        if self.gzip_body:
            payload = gzip.compress((_progress(TOKEN, 1) + _answer(body["id"], "buffered")).encode())
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.end_headers()
        self.wfile.write(_progress(TOKEN, 1).encode())
        self.wfile.flush()
        # The result goes out only once the progress was handed on, or after a
        # wait long enough to say it was not: then the answer says "late".
        on_time = self.delivered.wait(5.0)
        self.wfile.write(_answer(body["id"], "live" if on_time else "late").encode())
        self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 -- http.server's signature
        pass


@pytest.fixture
def streaming_upstream(request: pytest.FixtureRequest) -> Iterator[tuple[str, threading.Event]]:
    delivered = threading.Event()
    handler_class = type(
        "Upstream", (_StreamingUpstream,), {"delivered": delivered, "gzip_body": getattr(request, "param", False)}
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/mcp", delivered
    finally:
        server.shutdown()
        server.server_close()


class TestOverARealConnection:
    def test_progress_is_handed_on_as_it_is_read_not_after_the_result(
        self, streaming_upstream: tuple[str, threading.Event]
    ) -> None:
        endpoint, delivered = streaming_upstream
        seen: list[dict[str, Any]] = []

        def on_message(msg: dict[str, Any]) -> None:
            seen.append(msg)
            delivered.set()

        client = _stateless_client(endpoint=endpoint)
        client.start_notification_stream(on_message)
        try:
            answer = client.call("tools/call", _call_params(), timeout=10.0)
        finally:
            client.close()

        assert [m["params"]["progress"] for m in seen] == [1]
        assert answer["result"]["content"][0]["text"] == "live"

    @pytest.mark.parametrize("streaming_upstream", [True], indirect=True)
    def test_a_compressed_body_still_hands_on_its_progress_once_read(
        self, streaming_upstream: tuple[str, threading.Event]
    ) -> None:
        endpoint, _ = streaming_upstream
        seen: list[dict[str, Any]] = []
        client = _stateless_client(endpoint=endpoint)
        client.start_notification_stream(seen.append)
        try:
            answer = client.call("tools/call", _call_params(), timeout=10.0)
        finally:
            client.close()

        assert [m["params"]["progress"] for m in seen] == [1]
        assert answer["result"]["content"][0]["text"] == "buffered"
