"""An upstream response is bounded while it is read, not after it is held (#1613).

The per-call size cap used to run in the executor, after the result had been
read whole, parsed, held and serialized again to be measured. It bounded no
gateway memory. Both transports now stop reading a response once it passes
the limit and fail the call with `ResponseTooLarge`:

* stdio stops holding a line past the limit, drops the rest of it up to its
  newline, and fails the request whose id is at either end of the line. The
  next call on the same process works.
* HTTP counts a POST body as httpx reads it, decoded, so a JSON body, an SSE
  body and a compressed body are held to the same limit. The standing GET
  stream drops one event past the limit and keeps going.

Memory is measured with `tracemalloc`: a 16 MiB response against a 64 KiB
limit peaks far under the size of the response. Precedence of the limit is
pinned too: a server's own beats the process-wide one, and the environment
beats the file.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
import threading
import time
import tracemalloc
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.domain.exceptions import ConfigurationError, ResponseTooLarge
from mcp_hangar.http_client import HttpClient, HttpClientConfig
from mcp_hangar.response_limit import (
    DEFAULT_MAX_RESPONSE_BYTES,
    default_max_response_bytes,
    resolve_max_response_bytes,
    set_default_max_response_bytes,
)
from mcp_hangar.stdio_client import StdioClient

LIMIT = 64 * 1024
BIG = 16 * 1024 * 1024
#: Far under the 16 MiB response: the limit, a read chunk, and what a call allocates besides.
MEMORY_CEILING = 4 * 1024 * 1024


@pytest.fixture(autouse=True)
def _process_default() -> Iterator[None]:
    yield
    set_default_max_response_bytes(DEFAULT_MAX_RESPONSE_BYTES)


def _peak_while(action: Callable[[], Any]) -> tuple[Any, int]:
    """What *action* returned or raised, and the peak memory Python allocated while it ran."""
    tracemalloc.start()
    try:
        try:
            outcome: Any = action()
        except Exception as exc:  # noqa: BLE001 -- the test inspects it
            outcome = exc
        return outcome, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _text(response: dict[str, Any]) -> str:
    return str(response["result"]["content"][0]["text"])


# --- stdio -------------------------------------------------------------------

_STDIO_UPSTREAM = r"""
import json, sys
size, where = int(sys.argv[1]), sys.argv[2]
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    rid = json.dumps(request["id"])
    if request.get("params", {}).get("name") == "big":
        text = "x" * size
        if where == "head":
            out = '{"jsonrpc":"2.0","id":%s,"result":{"content":[{"type":"text","text":"%s"}]}}' % (rid, text)
        else:
            out = '{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"%s"}]},"id":%s}' % (text, rid)
    else:
        ok = {"content": [{"type": "text", "text": "ok"}]}
        out = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": ok})
    sys.stdout.write(out + "\n")
    sys.stdout.flush()
"""


@contextmanager
def _stdio_upstream(size: int = BIG, where: str = "head") -> Iterator[StdioClient]:
    process = subprocess.Popen(
        [sys.executable, "-c", _STDIO_UPSTREAM, str(size), where],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    client = StdioClient(process, mcp_server_id="stub")
    try:
        yield client
    finally:
        client.close()


def _stdio_call(client: StdioClient, name: str) -> dict[str, Any]:
    return client.call("tools/call", {"name": name, "arguments": {}}, timeout=30)


@pytest.mark.parametrize("where", ["head", "tail"])
def test_a_stdio_line_over_the_limit_fails_its_call_without_being_held(where: str) -> None:
    with _stdio_upstream(where=where) as client:
        client.max_response_bytes = LIMIT

        outcome, peak = _peak_while(lambda: _stdio_call(client, "big"))

        assert isinstance(outcome, ResponseTooLarge), outcome
        assert outcome.limit_bytes == LIMIT
        assert peak < MEMORY_CEILING, f"the reader held {peak} bytes of a {BIG}-byte line"


def test_the_next_stdio_call_on_the_same_process_works() -> None:
    """The rest of the line was dropped up to its newline: the framing is back in step."""
    with _stdio_upstream() as client:
        client.max_response_bytes = LIMIT
        with pytest.raises(ResponseTooLarge):
            _stdio_call(client, "big")

        assert _text(_stdio_call(client, "small")) == "ok"
        assert client.is_alive()


def test_a_stdio_line_under_the_limit_is_read_as_before() -> None:
    with _stdio_upstream(size=LIMIT // 2) as client:
        client.max_response_bytes = LIMIT

        assert _text(_stdio_call(client, "big")) == "x" * (LIMIT // 2)


def test_a_stdio_client_without_its_own_limit_reads_with_the_process_default() -> None:
    set_default_max_response_bytes(LIMIT)
    with _stdio_upstream(size=LIMIT * 2) as client:
        with pytest.raises(ResponseTooLarge):
            _stdio_call(client, "big")


def test_a_stdio_clients_own_limit_beats_the_process_default() -> None:
    set_default_max_response_bytes(LIMIT)
    with _stdio_upstream(size=LIMIT * 2) as client:
        client.max_response_bytes = LIMIT * 4

        assert len(_text(_stdio_call(client, "big"))) == LIMIT * 2


# --- HTTP --------------------------------------------------------------------


def _raw_deflate(data: bytes) -> bytes:
    packer = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return packer.compress(data) + packer.flush()


def _http_answers(size: int) -> dict[str, tuple[bytes, dict[str, str]]]:
    """Each tool's answer, built before anything is measured: the upstream runs in this process."""

    def body(text: str) -> bytes:
        payload = {"jsonrpc": "2.0", "id": "fixed", "result": {"content": [{"type": "text", "text": text}]}}
        return json.dumps(payload).encode()

    big = body("x" * size)
    return {
        "small": (body("ok"), {"Content-Type": "application/json"}),
        "big_json": (big, {"Content-Type": "application/json"}),
        "big_sse": (b"event: message\ndata: " + big + b"\n\n", {"Content-Type": "text/event-stream"}),
        "big_gzip": (gzip.compress(big), {"Content-Type": "application/json", "Content-Encoding": "gzip"}),
        "big_deflate": (zlib.compress(big), {"Content-Type": "application/json", "Content-Encoding": "deflate"}),
        "big_raw_deflate": (_raw_deflate(big), {"Content-Type": "application/json", "Content-Encoding": "deflate"}),
    }


class _HttpUpstream(BaseHTTPRequestHandler):
    answers: ClassVar[dict[str, tuple[bytes, dict[str, str]]]] = {}

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        body, headers = self.answers[request.get("params", {}).get("name")]
        try:
            self.send_response(200)
            for key, value in {**headers, "Content-Length": str(len(body))}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client stopped reading, which is the point


@contextmanager
def _http_upstream(size: int = BIG) -> Iterator[HttpClient]:
    handler = type("_Upstream", (_HttpUpstream,), {"answers": _http_answers(size)})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = HttpClient(
        endpoint=f"http://127.0.0.1:{server.server_address[1]}/mcp",
        mcp_server_id="stub",
        http_config=HttpClientConfig(max_retries=1),
    )
    try:
        yield client
    finally:
        client.close()
        server.shutdown()
        server.server_close()


def _http_call(client: HttpClient, name: str) -> dict[str, Any]:
    return client.call("tools/call", {"name": name, "arguments": {}}, timeout=30)


@pytest.mark.parametrize("name", ["big_json", "big_sse", "big_gzip", "big_deflate", "big_raw_deflate"])
def test_an_http_body_over_the_limit_fails_its_call_without_being_held(name: str) -> None:
    """A JSON body, one SSE event, and a gzip body whose decoded size is over the limit."""
    with _http_upstream() as client:
        client.max_response_bytes = LIMIT

        outcome, peak = _peak_while(lambda: _http_call(client, name))

        assert isinstance(outcome, ResponseTooLarge), outcome
        assert peak < MEMORY_CEILING, f"the client held {peak} bytes of a {BIG}-byte body"


def test_the_next_http_call_works() -> None:
    with _http_upstream() as client:
        client.max_response_bytes = LIMIT
        with pytest.raises(ResponseTooLarge):
            _http_call(client, "big_json")

        assert _text(_http_call(client, "small")) == "ok"


@pytest.mark.parametrize("name", ["big_json", "big_sse", "big_gzip", "big_deflate", "big_raw_deflate"])
def test_an_http_body_under_the_limit_is_read_as_before(name: str) -> None:
    with _http_upstream(size=LIMIT // 2) as client:
        client.max_response_bytes = LIMIT

        assert _text(_http_call(client, name)) == "x" * (LIMIT // 2)


def test_both_transports_fail_with_the_same_message() -> None:
    with _stdio_upstream() as stdio, _http_upstream() as http:
        stdio.max_response_bytes = http.max_response_bytes = LIMIT
        with pytest.raises(ResponseTooLarge) as over_stdio:
            _stdio_call(stdio, "big")
        with pytest.raises(ResponseTooLarge) as over_http:
            _http_call(http, "big_json")

    assert str(over_stdio.value) == str(over_http.value)
    assert str(over_http.value) == f"The upstream response exceeded the limit of {LIMIT} bytes and was not read."


def test_a_get_stream_event_over_the_limit_is_dropped_and_the_stream_goes_on() -> None:
    client = HttpClient(endpoint="http://upstream.test:9000/mcp", http_config=HttpClientConfig())
    client.max_response_bytes = 2000
    big = b'data: {"jsonrpc": "2.0", "method": "notifications/message", "params": {"x": "' + b"y" * 10_000 + b'"}}'
    small = b'data: {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}\n\n'
    # The big event arrives in pieces, its closing blank line split across two of them.
    pieces = [big[i : i + 1000] for i in range(0, len(big), 1000)] + [b"\n", b"\n" + small]
    response = MagicMock()
    response.status_code = 200
    response.iter_bytes.return_value = iter(pieces)

    @contextmanager
    def stream(*_args: Any, **_kwargs: Any) -> Iterator[MagicMock]:
        yield response

    seen: list[dict[str, Any]] = []
    with patch.object(client._client, "stream", new=stream):
        client.start_notification_stream(seen.append)
        deadline = time.monotonic() + 2
        while not seen and time.monotonic() < deadline:
            time.sleep(0.01)
        client._sse_running = False

    assert [m["method"] for m in seen] == ["notifications/tools/list_changed"]


# --- configuration -----------------------------------------------------------


def test_the_default_is_32_mib() -> None:
    assert DEFAULT_MAX_RESPONSE_BYTES == 32 * 1024 * 1024
    assert resolve_max_response_bytes({}, env={}) == DEFAULT_MAX_RESPONSE_BYTES


def test_the_file_sets_the_process_limit() -> None:
    assert resolve_max_response_bytes({"execution": {"max_response_bytes": 5000}}, env={}) == 5000


def test_the_environment_beats_the_file() -> None:
    config = {"execution": {"max_response_bytes": 5000}}

    assert resolve_max_response_bytes(config, env={"MCP_MAX_RESPONSE_BYTES": "7000"}) == 7000


@pytest.mark.parametrize("bad", [0, -1, True, "lots", 1.5])
def test_an_invalid_limit_is_refused_not_defaulted(bad: object) -> None:
    with pytest.raises(ValueError, match="positive whole number of bytes"):
        resolve_max_response_bytes({"execution": {"max_response_bytes": bad}}, env={})


def test_startup_applies_the_limit_once_reading_the_environment_then(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_hangar.server.config import apply_process_config

    monkeypatch.setenv("MCP_MAX_RESPONSE_BYTES", "9000")
    apply_process_config({"execution": {"max_response_bytes": 5000}})
    monkeypatch.setenv("MCP_MAX_RESPONSE_BYTES", "1")  # not read per call

    assert default_max_response_bytes() == 9000


def test_an_invalid_process_limit_refuses_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_hangar.server.config import check_process_config

    monkeypatch.delenv("MCP_MAX_RESPONSE_BYTES", raising=False)
    with pytest.raises(ConfigurationError, match="execution.max_response_bytes"):
        check_process_config({"execution": {"max_response_bytes": 0}})


def test_both_keys_are_known_to_the_schema() -> None:
    from mcp_hangar.server.config_schema import validate_config

    config = {
        "execution": {"max_response_bytes": 5000},
        "mcp_servers": {"store": {"mode": "subprocess", "command": ["x"], "max_response_bytes": 100}},
    }

    assert validate_config(config) == []


def test_a_servers_own_limit_reaches_its_client() -> None:
    from mcp_hangar.domain.model import McpServer

    server = McpServer(mcp_server_id="store", mode="subprocess", command=["x"], max_response_bytes=100)
    client = SimpleNamespace(mcp_server_id=None, max_response_bytes=None)
    launcher = SimpleNamespace(launch=lambda **_: client)
    with patch("mcp_hangar.infrastructure.launchers.get_launcher", return_value=launcher):
        assert server._create_client() is client

    assert client.max_response_bytes == 100


def test_a_server_without_its_own_limit_leaves_its_client_on_the_default() -> None:
    from mcp_hangar.domain.model import McpServer

    server = McpServer(mcp_server_id="store", mode="subprocess", command=["x"])
    client = SimpleNamespace(mcp_server_id=None, max_response_bytes=None)
    launcher = SimpleNamespace(launch=lambda **_: client)
    with patch("mcp_hangar.infrastructure.launchers.get_launcher", return_value=launcher):
        server._create_client()

    assert client.max_response_bytes is None


def test_an_invalid_server_limit_refuses_the_config() -> None:
    from mcp_hangar.server.config import _load_mcp_server_config

    with pytest.raises(ConfigurationError, match=r"mcp_servers\.store\.max_response_bytes"):
        _load_mcp_server_config("store", {"mode": "subprocess", "command": ["x"], "max_response_bytes": "big"})
