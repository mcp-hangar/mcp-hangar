"""Thread-safe stdio client with proper message correlation."""

import io
import json
import os
import select
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Full, Queue
from typing import TYPE_CHECKING, Any

from . import metrics as prometheus_metrics
from .domain.exceptions import ClientError, ResponseTooLarge
from .logging_config import get_logger
from .observability.tracing import inject_trace_context, record_upstream_outcome, upstream_call_span
from .protocol import inject_protocol_meta
from .response_limit import default_max_response_bytes

if TYPE_CHECKING:
    from .lock_hierarchy import TrackedLock

logger = get_logger(__name__)

#: How long the stdout reader goes on collecting stderr once stdout reached EOF.
#: An exited process's stderr reaches EOF at once. A descendant that inherited
#: the pipe can hold it open after the process exits, and a process that closed
#: stdout and kept running never lets it reach EOF: past this deadline, what
#: arrived is all there is, and the reader goes on to fail the calls in flight.
_STDERR_DRAIN_S = 1.0
#: The most stderr collected from a process whose stdout reached EOF.
_STDERR_DRAIN_MAX_BYTES = 64 * 1024
#: Upstream notifications waiting for their router at most.
_NOTIFICATION_BACKLOG = 256
#: The most of one line read at a time, so a line past the limit is noticed within one chunk of it.
_READ_CHUNK = 64 * 1024
#: How much of each end of a line past the limit is kept, to find the request it answers.
_ID_WINDOW = 4096


@dataclass(frozen=True)
class _OversizedLine:
    """A line the reader stopped holding once it passed the limit, and discarded to its newline."""

    size: int
    limit: int
    #: Its first and last `_ID_WINDOW` characters: a JSON-RPC response puts its id near one end.
    ends: str


def _text(data: bytes | str) -> str:
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data


def _read_line(stream: Any, read_limit: Callable[[], int]) -> str | _OversizedLine:
    """One newline-framed message within the read limit, or `_OversizedLine`; "" at EOF (#1613).

    Never holds more than the limit plus one chunk of a line. Past the limit
    the rest is read and dropped a chunk at a time up to the newline, which is
    the next frame boundary: a JSON message cannot contain a raw newline, so the
    framing is back in step and the next response parses. Only the two ends are
    kept, to find the request it answers. The limit is read once the line has
    begun to arrive, so a limit set while the reader waited applies to it.
    *stream* is binary, or text, in which case the limit counts characters.
    """
    newline = b"\n" if isinstance(stream, io.BufferedIOBase) else "\n"
    chunk = stream.readline(_READ_CHUNK)
    limit = read_limit()
    chunks: list[Any] = []
    size = 0
    while chunk:
        chunks.append(chunk)
        size += len(chunk)
        if chunk.endswith(newline) and size - 1 <= limit:
            break
        if size > limit:
            head, tail = chunks[0][:_ID_WINDOW], chunk[-_ID_WINDOW:]
            chunks.clear()
            while chunk and not chunk.endswith(newline):
                chunk = stream.readline(_READ_CHUNK)
                size += len(chunk)
                tail = (tail + chunk)[-_ID_WINDOW:]
            return _OversizedLine(size=size, limit=limit, ends=f"{_text(head)} {_text(tail)}")
        chunk = stream.readline(min(_READ_CHUNK, limit + 1 - size))
    return _text(newline[:0].join(chunks))


def _drain_pipe(pipe: Any, deadline_s: float, max_bytes: int) -> str:
    """Read a pipe until EOF, ``max_bytes`` or the deadline, whichever comes first.

    ``read()`` returns only at EOF, and a pipe that a live process holds has
    none. ``select`` waits for data or EOF for no longer than the time left, so
    this never waits past the deadline, whatever the other end does.
    """
    fd = pipe.fileno()
    chunks: list[bytes] = []
    size = 0
    deadline = time.monotonic() + deadline_s
    while size < max_bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        readable, _, _ = select.select([fd], [], [], remaining)
        if not readable:
            break
        chunk = os.read(fd, max_bytes - size)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks).decode(errors="replace")


@dataclass
class PendingRequest:
    """Tracks a pending RPC request waiting for a response."""

    request_id: str
    #: The response, or the exception the reader failed the request with.
    result_queue: "Queue[dict[str, Any] | Exception]"
    started_at: float


class StdioClient:
    """
    Thread-safe JSON-RPC client over stdio.
    Handles message correlation, timeouts, and process lifecycle.
    """

    def __init__(self, popen: subprocess.Popen, mcp_server_id: str | None = None):
        """
        Initialize client with a running subprocess.

        Args:
            popen: subprocess.Popen instance with stdin/stdout pipes
            mcp_server_id: Upstream server ID, used to label transport metrics.
                Falls back to "unknown" when unset.
        """
        self.process = popen
        self.mcp_server_id = mcp_server_id
        #: Whether this connection accepts the 2026-07-28 `_meta` envelope.
        #: Starts False: the era key on the `initialize` call itself is what
        #: makes a spec-current upstream apply its full era gate (missing
        #: `Mcp-Protocol-Version`/`Mcp-Method` headers, then "initialize" not
        #: existing in that era) before Hangar ever completes that envelope
        #: (#1211) -- so the handshake itself goes out legacy-shaped.
        #: `_perform_mcp_handshake` sets the real value once it knows it:
        #: True on a stateless (SEP-2575) upstream's method-not-found, True or
        #: False on a legacy upstream's negotiated `protocolVersion`, because
        #: from mcp 2.0.0 a connection that negotiated a legacy version rejects
        #: the modern envelope on every later request (-32600).
        self.modern_envelope = False
        self.pending: dict[str, PendingRequest] = {}
        # Lock hierarchy level: STDIO_CLIENT (50)
        # Safe to acquire after: PROVIDER, EVENT_BUS, EVENT_STORE
        # Safe to acquire before: (none - this is lowest level)
        # This lock protects the pending requests map only, not I/O
        self.pending_lock = self._create_lock(popen.pid)
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.closed = False
        #: This server's own read limit, set by `McpServer`; None reads with the process default.
        self.max_response_bytes: int | None = None
        self._last_stderr: str | None = None
        #: Upstream notifications, handed from the reader to their router (#1366).
        self._notifications: Queue[dict[str, Any] | None] | None = None
        self.reader_thread.start()

    @staticmethod
    def _create_lock(pid: int) -> "TrackedLock | threading.Lock":
        """Create lock with hierarchy tracking."""
        try:
            from .lock_hierarchy import LockLevel, TrackedLock

            return TrackedLock(LockLevel.STDIO_CLIENT, f"StdioClient:{pid}", reentrant=False)
        except ImportError:
            return threading.Lock()

    def _reader_loop(self):
        """
        Read stdout and dispatch responses to waiting callers.
        Runs in a dedicated daemon thread.
        """
        logger.info("stdio_client_reader_started", pid=self.process.pid)
        assert self.process.stdout is not None
        # Read the bytes under the text wrapper, so the limit counts bytes. Only
        # this thread reads stdout, so nothing is buffered in the wrapper.
        stdout = self.process.stdout
        stream = stdout.buffer if isinstance(stdout, io.TextIOWrapper) else stdout
        while not self.closed:
            try:
                line = _read_line(stream, self._read_limit)
                if isinstance(line, _OversizedLine):
                    self._fail_oversized(line)
                    continue
                if not line:
                    # EOF: process exited. Expected when we closed it (idle
                    # shutdown), otherwise the process died on its own.
                    if self.closed:
                        logger.debug("stdio_client_eof_on_stdout", expected=True)
                    else:
                        logger.warning("stdio_client_eof_on_stdout")
                    stderr_msg = self._capture_process_stderr()
                    self._last_stderr = stderr_msg
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.error("stdio_client_malformed_json", preview=line[:100], error=str(e))
                    continue

                prometheus_metrics.record_message_received(
                    self.mcp_server_id or "unknown",
                    prometheus_metrics.classify_jsonrpc_message(msg),
                    len(line),
                )

                msg_id = msg.get("id")

                if msg_id:
                    # This is a response to a request
                    with self.pending_lock:
                        pending = self.pending.pop(msg_id, None)

                    if pending:
                        pending.result_queue.put(msg)
                    else:
                        logger.warning("stdio_client_unknown_request", request_id=msg_id)
                else:
                    self._dispatch_notification(msg)

            except Exception as e:  # noqa: BLE001 -- fault-barrier: reader loop must not crash silently
                logger.error("stdio_client_reader_error", error=str(e))
                break

        # Clean up on exit
        self._cleanup_pending("reader_died")

    def _read_limit(self) -> int:
        """The most bytes of one response this client reads (#1613)."""
        return self.max_response_bytes or default_max_response_bytes()

    def _fail_oversized(self, line: _OversizedLine) -> None:
        """Fail the request a line past the limit answered, found by its id at either end of the line.

        The ids are ones this client minted, so one appearing in the kept ends
        is that request's. A response with its id in the middle of the line is
        not found, and its call times out as it would have.
        """
        with self.pending_lock:
            request_id = next((rid for rid in self.pending if f'"{rid}"' in line.ends), None)
            pending = self.pending.pop(request_id, None) if request_id is not None else None
        logger.warning(
            "stdio_client_response_too_large",
            mcp_server=self.mcp_server_id or "unknown",
            size_bytes=line.size,
            limit_bytes=line.limit,
            request_found=pending is not None,
        )
        if pending is not None:
            pending.result_queue.put(ResponseTooLarge(line.limit, self.mcp_server_id or ""))

    def start_notification_stream(self, on_message: Callable[[dict[str, Any]], None]) -> None:
        """Route the upstream's notifications to *on_message*, as the HTTP client's GET stream does (#1366).

        On stdio they arrive on the same pipe as responses, and used to be
        dropped, ``tools/list_changed`` included. They are handed to a thread of
        their own: the router answers ``list_changed`` by re-listing, and a
        ``tools/list`` issued on the reader thread would wait for a response only
        that thread can read. The queue is bounded; a flood beyond it is dropped.
        """
        if self._notifications is not None or self.closed:
            return
        queue: Queue[dict[str, Any] | None] = Queue(maxsize=_NOTIFICATION_BACKLOG)

        def _route() -> None:
            while (msg := queue.get()) is not None:
                try:
                    on_message(msg)
                except Exception as e:  # noqa: BLE001 -- fault-barrier: a bad handler must not kill the channel
                    logger.warning("stdio_client_notification_handler_failed", error_type=type(e).__name__)

        self._notifications = queue
        threading.Thread(target=_route, name=f"mcp-stdio-notify-{self.process.pid}", daemon=True).start()
        if self.closed:  # closed while this was starting: `close` may have missed the queue
            queue.put(None)

    def _dispatch_notification(self, msg: dict[str, Any]) -> None:
        """Queue one notification for its router, or drop it when there is none (reader thread)."""
        queue = self._notifications
        if queue is None:
            logger.debug("stdio_client_notification", method=msg.get("method"))
            return
        try:
            queue.put_nowait(msg)
        except Full:
            logger.warning("stdio_client_notification_dropped", method=msg.get("method"))

    def _capture_process_stderr(self) -> str | None:
        """Capture and log stderr from the process for debugging. Returns stderr text."""
        stderr_text = None
        # An exit we initiated (close() / idle shutdown sets self.closed first)
        # is expected: log it at info so it doesn't inflate the error stream and
        # trip log-based error alerting. Only an unsolicited exit is an error.
        expected = self.closed
        log = logger.info if expected else logger.error
        try:
            # Log exit code
            rc = self.process.poll()
            if rc is not None:
                log("stdio_client_process_exited", exit_code=rc, expected=expected)

            # Try to read stderr if available
            stderr = getattr(self.process, "stderr", None)
            if stderr:
                try:
                    # Never read() to EOF here. Stdout at EOF does not mean
                    # stderr will get there: a process can close stdout and
                    # keep running, and a descendant can hold stderr open.
                    # This thread would stay blocked, and the calls in flight
                    # would never be failed.
                    err_text = _drain_pipe(stderr, _STDERR_DRAIN_S, _STDERR_DRAIN_MAX_BYTES).strip()
                    if err_text:
                        # Log first 2000 chars to avoid log spam
                        if len(err_text) > 2000:
                            err_text = err_text[:2000] + "... (truncated)"
                        log("stdio_client_process_stderr", stderr=err_text, expected=expected)
                        stderr_text = err_text
                except Exception as read_err:  # noqa: BLE001 -- fault-barrier: stderr capture must not crash diagnostics
                    logger.debug("stdio_client_stderr_read_failed", error=str(read_err))
        except Exception as e:  # noqa: BLE001 -- fault-barrier: process diagnostics must not propagate
            logger.debug("stdio_client_capture_error", error=str(e))
        return stderr_text

    def _cleanup_pending(self, error_msg: str):
        """Clean up all pending requests on shutdown or error."""
        # Include stderr in error message if available
        full_error = error_msg
        if self._last_stderr:
            # Extract first meaningful line from stderr for error message
            first_line = self._last_stderr.split("\n")[0].strip()
            if first_line:
                full_error = f"{error_msg}: {first_line}"

        with self.pending_lock:
            for pending in self.pending.values():
                pending.result_queue.put({"error": {"code": -1, "message": full_error}})
            self.pending.clear()

    def call(self, method: str, params: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
        """
        Synchronous RPC call with explicit timeout.

        Args:
            method: JSON-RPC method name
            params: Method parameters
            timeout: Timeout in seconds

        Returns:
            Response dictionary with either 'result' or 'error' key

        Raises:
            ClientError: If the client is closed or write fails
            TimeoutError: If the request times out
        """
        if self.closed:
            raise ClientError("client_closed")

        request_id = str(uuid.uuid4())
        result_queue: Queue[dict[str, Any] | Exception] = Queue(maxsize=1)

        pending = PendingRequest(request_id=request_id, result_queue=result_queue, started_at=time.time())

        # SAFETY: Register pending request BEFORE writing to stdin.
        # The reader thread (_reader_loop) checks self.pending under pending_lock.
        # If we wrote first and the response arrived before registration,
        # the response would be dropped (no matching pending request).
        # This ordering guarantees every response finds its handler.
        with self.pending_lock:
            self.pending[request_id] = pending

        # CLIENT span at the upstream boundary (OTel GenAI/MCP semconv). Opened
        # before injection so the trace context written into `_meta` parents the
        # upstream's span to this one. The answer is classified inside it too.
        with upstream_call_span(method, params) as span:
            # Inject Hangar protocol metadata, then propagate the active trace
            # context to the upstream over stdio: W3C traceparent/tracestate go
            # in the MCP `_meta` field (mirrors the HTTP transport, which injects
            # into request headers). Servers that don't understand `_meta` ignore
            # it. inject_protocol_meta returns a fresh dict, so the caller's is
            # never mutated. inject_trace_context is the outbound chokepoint
            # both transports share: it also removes any baggage.
            params = inject_protocol_meta(params, modern_envelope=self.modern_envelope)
            inject_trace_context(params["_meta"])

            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }

            try:
                request_str = json.dumps(request) + "\n"
                prometheus_metrics.record_message_sent(self.mcp_server_id or "unknown", method, len(request_str))
                logger.info(
                    "stdio_client_sending_request",
                    method=method,
                    pid=self.process.pid,
                    alive=self.process.poll() is None,
                )
                assert self.process.stdin is not None
                self.process.stdin.write(request_str)
                self.process.stdin.flush()
                logger.debug("stdio_client_request_sent")
            except Exception as e:  # noqa: BLE001 -- infra-boundary: write failure wrapped as ClientError
                logger.error("stdio_client_write_failed", error=str(e))
                with self.pending_lock:
                    self.pending.pop(request_id, None)
                raise ClientError(f"write_failed: {e}") from e

            try:
                response = result_queue.get(timeout=timeout)
            except Empty:
                with self.pending_lock:
                    self.pending.pop(request_id, None)
                raise TimeoutError(f"timeout: {method} after {timeout}s") from None
            if isinstance(response, Exception):
                raise response  # ends the CLIENT span in ERROR, typed
            record_upstream_outcome(span, response)
            return response

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification: no id, no response, nothing to wait for.

        The counterpart to :meth:`call`, and until #881 there was no such thing
        on either transport -- both mint a request id unconditionally and then
        block on the matching response, so a notification could not be expressed
        at all. That is why the MCP lifecycle was never finished (see
        ``McpServer._perform_mcp_handshake``).

        Carries the same protocol ``_meta`` and trace context as a request, so
        the era gate applies here too: a legacy connection must not be sent the
        2026-07-28 envelope, and ``modern_envelope`` is what says so.

        Args:
            method: JSON-RPC method name, e.g. ``notifications/initialized``.
            params: Method parameters. Empty when omitted.

        Raises:
            ClientError: If the client is closed or the write fails.
        """
        if self.closed:
            raise ClientError("client_closed")

        with upstream_call_span(method, params or {}):
            sent_params = inject_protocol_meta(params or {}, modern_envelope=self.modern_envelope)
            inject_trace_context(sent_params["_meta"])

            message = {"jsonrpc": "2.0", "method": method, "params": sent_params}
            try:
                message_str = json.dumps(message) + "\n"
                prometheus_metrics.record_message_sent(self.mcp_server_id or "unknown", method, len(message_str))
                assert self.process.stdin is not None
                self.process.stdin.write(message_str)
                self.process.stdin.flush()
                logger.debug("stdio_client_notification_sent", method=method)
            except Exception as e:  # noqa: BLE001 -- infra-boundary: write failure wrapped as ClientError
                logger.error("stdio_client_notify_failed", method=method, error=str(e))
                raise ClientError(f"write_failed: {e}") from e

    def is_alive(self) -> bool:
        """Check if the underlying process is still running."""
        return self.process.poll() is None

    def close(self):
        """
        Graceful shutdown: attempt RPC shutdown, then terminate process.
        Safe to call multiple times.
        """
        if self.closed:
            return

        self.closed = True

        # Try graceful shutdown via RPC
        try:
            self.call("shutdown", {}, timeout=3.0)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: shutdown RPC failure is expected
            logger.debug("stdio_client_shutdown_rpc_failed", error=str(e))

        # Terminate process
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    logger.warning("stdio_client_process_terminate_timeout")
                    self.process.kill()
                    self.process.wait()
        except Exception as e:  # noqa: BLE001 -- fault-barrier: cleanup must not crash close
            logger.error("stdio_client_cleanup_error", error=str(e))

        # Clean up any remaining pending requests
        self._cleanup_pending("client_closed")
        if self._notifications is not None:
            try:
                self._notifications.put_nowait(None)
            except Full:
                pass  # a daemon thread; it goes with the process

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
