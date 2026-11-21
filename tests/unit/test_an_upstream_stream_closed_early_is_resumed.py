"""An upstream's SSE stream that closes before its answer is resumed (#2014).

Streamable HTTP lets an upstream close the SSE stream of a request it has not
answered yet (SEP-1699), after an event with an id and a ``retry`` interval.
The client waits that interval and opens a GET that names the last id in
``Last-Event-ID``; the answer comes on that stream. Hangar, as the client of
its upstreams, read the close as the end of the call and failed it, and the
standing GET stream re-opened at once with no ``Last-Event-ID``.

The upstream here is an ``httpx.MockTransport``, so the requests asserted are
the ones the client sent. ``time.sleep`` is recorded rather than waited, except
where the deadline itself is under test.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from mcp_hangar.domain.exceptions import ResponseTooLarge, UpstreamPeerKeyMismatchError
from mcp_hangar.http_client import DEFAULT_SSE_RECONNECT_S, HttpClient, HttpClientConfig, _SseCursor

SSE = {"Content-Type": "text/event-stream"}
NO_ANSWER = "SSE response did not contain valid JSON-RPC response"


def _priming(event_id: str, retry_ms: int | None = 500) -> str:
    retry = f"retry: {retry_ms}\n" if retry_ms is not None else ""
    return f"id: {event_id}\n{retry}data: \n\n"


def _answer_event(request_id: str, event_id: str = "e-9") -> str:
    message = {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "resumed"}]}}
    return f"event: message\nid: {event_id}\ndata: {json.dumps(message)}\n\n"


class Upstream:
    """Answers the POST with *post_body* and each GET with the next of *gets*.

    ``{id}`` in *post_body* is the id of the request being answered; each GET is given it.
    """

    def __init__(self, post_body: str, gets: list[Callable[[str], httpx.Response]]) -> None:
        self.post_body = post_body
        self.gets = gets
        self.request_id = ""
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.method == "POST":
            self.request_id = json.loads(request.content)["id"]
            return httpx.Response(200, headers=SSE, text=self.post_body.replace("{id}", self.request_id))
        if not self.gets:
            return httpx.Response(405)
        return self.gets.pop(0)(self.request_id)

    @property
    def get_requests(self) -> list[httpx.Request]:
        return [r for r in self.seen if r.method == "GET"]


def _client(upstream: Upstream, **config: Any) -> HttpClient:
    client = HttpClient("http://upstream.test/mcp", http_config=HttpClientConfig(max_retries=1, **config))
    defaults = client._client.headers
    client._client.close()
    client._client = httpx.Client(headers=defaults, transport=httpx.MockTransport(upstream))
    client._mcp_session_id = "s-1"
    return client


def _stream(*events: str) -> Callable[[str], httpx.Response]:
    """A GET answered with an SSE stream; each event may name ``{id}``, the request being answered."""
    return lambda request_id: httpx.Response(
        200, headers=SSE, text="".join(e.replace("{id}", request_id) for e in events)
    )


def _answering(event_id: str = "e-9") -> Callable[[str], httpx.Response]:
    return lambda request_id: httpx.Response(
        200, headers=SSE, text=_priming("e-3") + _answer_event(request_id, event_id)
    )


@pytest.fixture
def slept() -> Iterator[list[float]]:
    waits: list[float] = []
    with patch("mcp_hangar.http_client.time.sleep", side_effect=waits.append):
        yield waits


class TestAPostStreamClosedEarly:
    def test_is_resumed_on_a_get_naming_the_last_event_id(self, slept: list[float]) -> None:
        upstream = Upstream(_priming("e-1"), [_answering()])
        answer = _client(upstream).call("tools/call", {"name": "t", "arguments": {}})

        assert answer["result"]["content"][0]["text"] == "resumed"
        (get,) = upstream.get_requests
        assert get.headers["Last-Event-ID"] == "e-1"
        assert get.headers["Mcp-Session-Id"] == "s-1"
        assert get.headers["Accept"] == "text/event-stream"

    def test_waits_the_upstreams_retry_interval_first(self, slept: list[float]) -> None:
        upstream = Upstream(_priming("e-1", retry_ms=500), [_answering()])
        _client(upstream).call("tools/call", {"name": "t", "arguments": {}})
        assert slept == [0.5]

    def test_waits_the_default_without_a_retry_field(self, slept: list[float]) -> None:
        upstream = Upstream(_priming("e-1", retry_ms=None), [_answering()])
        _client(upstream).call("tools/call", {"name": "t", "arguments": {}})
        assert slept == [DEFAULT_SSE_RECONNECT_S]

    def test_closed_again_resumes_from_the_newer_id(self, slept: list[float]) -> None:
        upstream = Upstream(_priming("e-1"), [_stream(_priming("e-2", retry_ms=200)), _answering()])
        answer = _client(upstream).call("tools/call", {"name": "t", "arguments": {}})

        assert answer["result"]["content"][0]["text"] == "resumed"
        assert [g.headers["Last-Event-ID"] for g in upstream.get_requests] == ["e-1", "e-2"]
        assert slept == [0.5, 0.2]

    def test_a_stream_with_no_event_id_is_not_resumed(self, slept: list[float]) -> None:
        upstream = Upstream("data: \n\n", [_answering()])
        answer = _client(upstream).call("tools/call", {"name": "t", "arguments": {}})

        assert answer["error"]["message"] == NO_ANSWER
        assert upstream.get_requests == []
        assert slept == []

    def test_an_answer_on_the_post_stream_needs_no_resume(self, slept: list[float]) -> None:
        upstream = Upstream(_priming("e-1") + _answer_event("{id}"), [_answering()])
        assert _client(upstream).call("tools/call", {"name": "t", "arguments": {}})["result"]
        assert upstream.get_requests == []

    def test_an_upstream_without_resumption_keeps_the_old_error(self, slept: list[float]) -> None:
        upstream = Upstream(_priming("e-1"), [lambda _id: httpx.Response(405)])
        answer = _client(upstream).call("tools/call", {"name": "t", "arguments": {}})

        assert answer["error"]["message"] == NO_ANSWER
        assert len(upstream.get_requests) == 1

    def test_failed_resumes_give_up_after_the_limit(self, slept: list[float]) -> None:
        refused = lambda _id: httpx.Response(500)  # noqa: E731
        upstream = Upstream(_priming("e-1"), [refused, refused, _answering()])
        answer = _client(upstream).call("tools/call", {"name": "t", "arguments": {}})

        assert answer["error"]["message"] == NO_ANSWER
        assert len(upstream.get_requests) == 2

    def test_a_non_sse_answer_to_the_get_is_a_failed_resume(self, slept: list[float]) -> None:
        upstream = Upstream(_priming("e-1"), [lambda _id: httpx.Response(200, json={}), _answering()])
        answer = _client(upstream).call("tools/call", {"name": "t", "arguments": {}})
        assert answer["result"]["content"][0]["text"] == "resumed"

    def test_a_modern_connection_does_not_resume(self, slept: list[float]) -> None:
        upstream = Upstream(_priming("e-1"), [_answering()])
        client = _client(upstream)
        client.modern_envelope = True
        answer = client.call("tools/call", {"name": "t", "arguments": {}})

        assert answer["error"]["message"] == NO_ANSWER
        assert upstream.get_requests == []

    def test_a_stateless_upstream_gets_no_session_id(self, slept: list[float]) -> None:
        upstream = Upstream(_priming("e-1"), [_answering()])
        _client(upstream, stateless_upstream=True).call("tools/call", {"name": "t", "arguments": {}})
        (get,) = upstream.get_requests
        assert "Mcp-Session-Id" not in get.headers


class TestTheResumeStaysInsideTheCallsTimeout:
    def test_a_retry_past_the_timeout_fails_at_once(self, slept: list[float]) -> None:
        upstream = Upstream(_priming("e-1", retry_ms=999_999_999), [_answering()])
        with pytest.raises(TimeoutError):
            _client(upstream).call("tools/call", {"name": "t", "arguments": {}}, timeout=5)

        assert slept == []
        assert upstream.get_requests == []

    def test_a_trickling_stream_is_cut_at_the_deadline(self) -> None:
        def trickle() -> Iterator[bytes]:
            yield _priming("e-2", retry_ms=0).encode()
            while True:
                time.sleep(0.02)
                yield b":"

        upstream = Upstream(
            _priming("e-1", retry_ms=0), [lambda _id: httpx.Response(200, headers=SSE, content=trickle())]
        )
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            _client(upstream).call("tools/call", {"name": "t", "arguments": {}}, timeout=0.5)
        assert time.monotonic() - started < 3


class TestWhatIsNotRetried:
    def test_an_oversized_event_on_the_resumed_stream_is_refused(self, slept: list[float]) -> None:
        big = "data: " + "x" * 2048 + "\n\n"
        upstream = Upstream(_priming("e-1"), [_stream(big), _answering()])
        client = _client(upstream)
        client.max_response_bytes = 1024
        with pytest.raises(ResponseTooLarge):
            client.call("tools/call", {"name": "t", "arguments": {}})
        assert len(upstream.get_requests) == 1

    def test_a_peer_key_refusal_on_the_get_propagates(self, slept: list[float]) -> None:
        def refuse(_id: str) -> httpx.Response:
            raise UpstreamPeerKeyMismatchError("sha256:00")

        upstream = Upstream(_priming("e-1"), [refuse, _answering()])
        with pytest.raises(UpstreamPeerKeyMismatchError):
            _client(upstream).call("tools/call", {"name": "t", "arguments": {}})
        assert len(upstream.get_requests) == 1


class TestTheCursor:
    def test_reads_id_and_retry_as_the_sse_rules_do(self) -> None:
        cursor = _SseCursor()
        cursor.read("id:e-1\nretry: 250\ndata:")
        assert (cursor.last_event_id, cursor.retry_s) == ("e-1", 0.25)

        cursor.read("id: e-2\r\nretry: soon\ndata: x")
        assert (cursor.last_event_id, cursor.retry_s) == ("e-2", 0.25)

        cursor.read("id: bad\0id\ndata: x")
        assert cursor.last_event_id == "e-2"

        cursor.read(": a comment\ndata: x")
        assert cursor.last_event_id == "e-2"


def _stream_responses(*bodies: str) -> tuple[Callable[..., Any], list[dict[str, str]]]:
    """A stand-in for ``httpx.Client.stream``: one body per connection, recording each one's headers."""
    sent: list[dict[str, str]] = []
    queue = list(bodies)

    @contextmanager
    def stream(_method: str, _url: str, *, headers: dict[str, str], **_kwargs: Any) -> Iterator[Any]:
        sent.append(dict(headers))
        response = MagicMock()
        response.status_code = 200 if queue else 405
        response.iter_bytes.return_value = iter([queue.pop(0).encode()] if queue else [])
        yield response

    return stream, sent


class TestTheStandingGetStream:
    def test_reopens_after_the_retry_with_the_last_event_id(self) -> None:
        client = HttpClient("http://upstream.test/mcp", http_config=HttpClientConfig())
        stream, sent = _stream_responses(_priming("g-1", retry_ms=300), _priming("g-2"))
        waits: list[float] = []

        def wait(delay: float) -> bool:
            waits.append(delay)
            return False

        with patch.object(client._client, "stream", new=stream), patch.object(client._sse_stop, "wait", new=wait):
            client._sse_running = True
            client._notification_stream_loop(lambda _msg: None)

        assert [h.get("Last-Event-ID") for h in sent] == [None, "g-1", "g-2"]
        assert waits == [0.3, 0.5]

    def test_a_long_retry_ends_when_the_client_closes(self) -> None:
        """The wait is the upstream's to choose, so close() must cut it short, or the thread outlives the client."""
        client = HttpClient("http://upstream.test/mcp", http_config=HttpClientConfig())
        stream, _sent = _stream_responses(_priming("g-1", retry_ms=999_999_999))

        with patch.object(client._client, "stream", new=stream):
            client.start_notification_stream(lambda _msg: None)
            thread = client._sse_thread
            assert thread is not None
            time.sleep(0.1)
            client.close()
            thread.join(timeout=2)

        assert not thread.is_alive()
