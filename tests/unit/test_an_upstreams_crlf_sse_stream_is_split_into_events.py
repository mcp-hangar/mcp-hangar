"""An upstream's CRLF-, LF- or CR-framed SSE stream is split into events (#2327).

The WHATWG HTML Living Standard ("Parsing an event stream") terminates a line
with CRLF, a lone LF or a lone CR, and an event with an empty line. The Python
MCP SDK server (sse-starlette) frames every SSE event with CRLF. Before #2327,
``_iter_stream_events`` and ``_parse_sse_body`` split only on ``\\n\\n``, so
events on the standing GET stream and on a resumed GET stream were never
dispatched, and an oversize event's dropping branch carried only 1 byte and
could lose a split CRLF boundary.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from mcp_hangar.domain.exceptions import ResponseTooLarge
from mcp_hangar.http_client import HttpClient, HttpClientConfig

SSE = {"Content-Type": "text/event-stream"}


def _chunked_response(chunks: list[bytes]) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.iter_bytes.return_value = iter(chunks)
    return response


def _frame(events: list[list[str]], eol: str) -> bytes:
    """Encode *events* (each a list of SSE field lines) using *eol* line endings."""
    return "".join(eol.join(lines) + eol + eol for lines in events).encode("utf-8")


class TestIterStreamEventsLineEndings:
    @pytest.mark.parametrize("eol", ["\r\n", "\n", "\r"], ids=["crlf", "lf", "cr"])
    def test_splits_crlf_lf_and_cr_into_the_same_events(self, eol: str) -> None:
        client = HttpClient("http://u.test/mcp")
        body = _frame(
            [
                ["event: message", 'data: {"a":1}'],
                ["event: message", 'data: {"b":2}'],
            ],
            eol,
        )
        response = httpx.Response(200, content=body)

        events = list(client._iter_stream_events(response, "u", stop=lambda: False, refuse_oversize=False))

        assert events == [
            'event: message\ndata: {"a":1}',
            'event: message\ndata: {"b":2}',
        ]

    @pytest.mark.parametrize(
        "chunks",
        [
            # Split inside the 4-byte CRLF blank-line boundary at every offset.
            [b'event: message\r\ndata: {"a":1}\r', b'\n\r\nevent: message\r\ndata: {"b":2}\r\n\r\n'],
            [b'event: message\r\ndata: {"a":1}\r\n', b'\r\nevent: message\r\ndata: {"b":2}\r\n\r\n'],
            [b'event: message\r\ndata: {"a":1}\r\n\r', b'\nevent: message\r\ndata: {"b":2}\r\n\r\n'],
            # Split inside a single CRLF between lines (must not become a blank line).
            [b"event: message\r", b'\ndata: {"a":1}\r\n\r\nevent: message\r\ndata: {"b":2}\r\n\r\n'],
            # 1-byte chunks throughout.
            [bytes([b]) for b in b'event: message\r\ndata: {"a":1}\r\n\r\nevent: message\r\ndata: {"b":2}\r\n\r\n'],
        ],
        ids=["split-after-first-cr", "split-between-crlfs", "split-before-last-lf", "split-mid-event-crlf", "one-byte"],
    )
    def test_crlf_boundary_split_across_chunks_is_not_lost(self, chunks: list[bytes]) -> None:
        client = HttpClient("http://u.test/mcp")
        response = _chunked_response(chunks)

        events = list(client._iter_stream_events(response, "u", stop=lambda: False, refuse_oversize=False))

        assert events == [
            'event: message\ndata: {"a":1}',
            'event: message\ndata: {"b":2}',
        ]

    def test_cr_only_boundary_split_across_chunks_and_at_eof(self) -> None:
        client = HttpClient("http://u.test/mcp")
        chunks = [b'event: message\rdata: {"a":1}\r', b'\revent: message\rdata: {"b":2}\r\r']
        response = _chunked_response(chunks)

        events = list(client._iter_stream_events(response, "u", stop=lambda: False, refuse_oversize=False))

        assert events == [
            'event: message\ndata: {"a":1}',
            'event: message\ndata: {"b":2}',
        ]


class TestIterStreamEventsSizeLimit:
    @pytest.mark.parametrize("eol", ["\r\n", "\n", "\r"], ids=["crlf", "lf", "cr"])
    def test_drops_oversize_event_and_keeps_next_in_single_chunk(self, eol: str) -> None:
        client = HttpClient("http://u.test/mcp")
        client.max_response_bytes = 200
        big = ["event: message", "data: " + "x" * 500]
        small = ["event: message", 'data: {"ok":true}']
        response = httpx.Response(200, content=_frame([big, small], eol))

        events = list(client._iter_stream_events(response, "u", stop=lambda: False, refuse_oversize=False))

        assert events == ['event: message\ndata: {"ok":true}']

    @pytest.mark.parametrize(
        "boundary_chunks",
        [
            [b"\r\n\r\n"],
            [b"\r", b"\n\r\n"],
            [b"\r\n", b"\r\n"],
            [b"\r\n\r", b"\n"],
            [b"\r", b"\n", b"\r", b"\n"],
        ],
        ids=["unsplit", "cr-then-lfcrlf", "crlf-then-crlf", "crlfcr-then-lf", "four-single-bytes"],
    )
    def test_dropping_mode_handles_every_crlf_boundary_split(self, boundary_chunks: list[bytes]) -> None:
        client = HttpClient("http://u.test/mcp")
        client.max_response_bytes = 200
        big = b'data: {"x": "' + b"y" * 1000 + b'"}'
        small = b'data: {"ok": true}\r\n\r\n'
        # Feed the oversized payload in pieces so dropping mode is active before the boundary.
        chunks = [big[:400], big[400:], *boundary_chunks, small]
        response = _chunked_response(chunks)

        events = list(client._iter_stream_events(response, "u", stop=lambda: False, refuse_oversize=False))

        assert events == ['data: {"ok": true}']

    def test_dropping_mode_handles_crlf_starting_on_the_oversize_chunk_itself(self) -> None:
        client = HttpClient("http://u.test/mcp")
        client.max_response_bytes = 200
        big = b'data: {"x": "' + b"y" * 1000 + b'"}'
        small = b'data: {"ok": true}\r\n\r\n'
        # Trigger dropping with the first \r already attached to the oversize chunk (1-byte carry case).
        for tail_on_big, rest_of_boundary in [
            (b"\r", b"\n\r\n"),
            (b"\r\n", b"\r\n"),
            (b"\r\n\r", b"\n"),
        ]:
            response = _chunked_response([big + tail_on_big, rest_of_boundary + small])
            events = list(client._iter_stream_events(response, "u", stop=lambda: False, refuse_oversize=False))
            assert events == ['data: {"ok": true}'], (tail_on_big, rest_of_boundary)

    def test_dropping_mode_handles_cr_only_boundary_split(self) -> None:
        client = HttpClient("http://u.test/mcp")
        client.max_response_bytes = 200
        big = b'data: {"x": "' + b"y" * 1000 + b'"}'
        small = b'data: {"ok": true}\r\r'
        response = _chunked_response([big + b"\r", b"\r" + small])

        events = list(client._iter_stream_events(response, "u", stop=lambda: False, refuse_oversize=False))

        assert events == ['data: {"ok": true}']

    @pytest.mark.parametrize("eol", ["\r\n", "\n", "\r"], ids=["crlf", "lf", "cr"])
    def test_refuse_oversize_raises_for_every_line_ending(self, eol: str) -> None:
        client = HttpClient("http://u.test/mcp")
        client.max_response_bytes = 200
        big = ["event: message", "data: " + "x" * 500]
        response = httpx.Response(200, content=_frame([big], eol))

        with pytest.raises(ResponseTooLarge):
            list(client._iter_stream_events(response, "u", stop=lambda: False, refuse_oversize=True))


class TestStandingGetStreamAndResumeWithCrlf:
    def test_crlf_events_on_the_standing_get_stream_reach_on_message(self) -> None:
        client = HttpClient(endpoint="http://upstream.test:9000/mcp", http_config=HttpClientConfig())
        progress = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {"progressToken": "t1", "progress": 1, "total": 2},
            }
        )
        body = _frame(
            [
                ["event: message", f"data: {progress}"],
                ["event: message", 'data: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}'],
            ],
            "\r\n",
        )
        response = _chunked_response([body[:40], body[40:]])

        @contextmanager
        def stream(*_args: Any, **_kwargs: Any) -> Iterator[MagicMock]:
            yield response

        seen: list[dict[str, Any]] = []
        with patch.object(client._client, "stream", new=stream):
            client.start_notification_stream(seen.append)
            deadline = time.monotonic() + 2
            while len(seen) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            client._sse_running = False

        assert [m["method"] for m in seen] == ["notifications/progress", "notifications/tools/list_changed"]

    @pytest.mark.parametrize("eol", ["\r\n", "\r"], ids=["crlf", "cr"])
    def test_early_closed_post_resumes_and_reads_answer_on_crlf_or_cr_get(self, eol: str) -> None:
        request_id = ""
        get_headers: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_id
            if request.method == "POST":
                request_id = json.loads(request.content)["id"]
                priming = _frame([["id: e-1", "retry: 50", "data: "]], eol)
                return httpx.Response(200, headers=SSE, content=priming)
            get_headers.append(request.headers)
            answer = {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "resumed"}]}}
            body = _frame(
                [
                    ["id: e-2", "data: "],
                    ["event: message", "id: e-3", f"data: {json.dumps(answer)}"],
                ],
                eol,
            )
            return httpx.Response(200, headers=SSE, content=body)

        client = HttpClient("http://upstream.test/mcp", http_config=HttpClientConfig(max_retries=1))
        defaults = client._client.headers
        client._client.close()
        client._client = httpx.Client(headers=defaults, transport=httpx.MockTransport(handler))
        client._mcp_session_id = "s-1"

        with patch("mcp_hangar.http_client.time.sleep"):
            answer = client.call("tools/call", {"name": "t", "arguments": {}}, timeout=0.5)

        assert answer["result"]["content"][0]["text"] == "resumed"
        assert len(get_headers) == 1
        assert get_headers[0]["Last-Event-ID"] == "e-1"


class TestParseSseBodyWithAllLineEndings:
    @pytest.mark.parametrize("eol", ["\r\n", "\n", "\r"], ids=["crlf", "lf", "cr"])
    def test_splits_post_body_events_and_matches_request_id(self, eol: str) -> None:
        client = HttpClient("http://u.test/mcp")
        # Include a wrong-id response BEFORE the right-id response so fallback cannot mask a broken split.
        wrong = json.dumps({"jsonrpc": "2.0", "id": "other", "result": {"wrong": True}})
        right = json.dumps({"jsonrpc": "2.0", "id": "req-1", "result": {"ok": True}})
        body = _frame(
            [
                ["event: message", "id: e-1", f"data: {wrong}"],
                ["event: message", "id: e-2", f"data: {right}"],
            ],
            eol,
        ).decode("utf-8")

        answer = client._parse_sse_body(body, "req-1")

        assert answer == {"jsonrpc": "2.0", "id": "req-1", "result": {"ok": True}}
