"""Thread-safe stdio client with proper message correlation."""

from dataclasses import dataclass
import json
from queue import Empty, Queue
import subprocess
import threading
import time
from typing import Any, TYPE_CHECKING
import uuid

from . import metrics as prometheus_metrics
from .domain.exceptions import ClientError
from .logging_config import get_logger
from .observability.tracing import inject_trace_context, upstream_call_span

if TYPE_CHECKING:
    from .infrastructure.lock_hierarchy import TrackedLock

logger = get_logger(__name__)


@dataclass
class PendingRequest:
    """Tracks a pending RPC request waiting for a response."""

    request_id: str
    result_queue: "Queue[dict[str, Any]]"
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
        self.pending: dict[str, PendingRequest] = {}
        # Lock hierarchy level: STDIO_CLIENT (50)
        # Safe to acquire after: PROVIDER, EVENT_BUS, EVENT_STORE
        # Safe to acquire before: (none - this is lowest level)
        # This lock protects the pending requests map only, not I/O
        self.pending_lock = self._create_lock(popen.pid)
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.closed = False
        self._last_stderr: str | None = None
        self.reader_thread.start()

    @staticmethod
    def _create_lock(pid: int) -> "TrackedLock | threading.Lock":
        """Create lock with hierarchy tracking."""
        try:
            from .infrastructure.lock_hierarchy import LockLevel, TrackedLock

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
        while not self.closed:
            try:
                line = self.process.stdout.readline()
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
                    # Unsolicited notification - log and ignore
                    logger.debug("stdio_client_notification", message=msg)

            except Exception as e:  # noqa: BLE001 -- fault-barrier: reader loop must not crash silently
                logger.error("stdio_client_reader_error", error=str(e))
                break

        # Clean up on exit
        self._cleanup_pending("reader_died")

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
                    # Read available stderr (non-blocking would be ideal, but read() works post-exit)
                    err_bytes = stderr.read()
                    if err_bytes:
                        err_text = (
                            err_bytes if isinstance(err_bytes, str) else err_bytes.decode(errors="replace")
                        ).strip()
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
        result_queue: Queue[dict[str, Any]] = Queue(maxsize=1)

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
        # upstream's span to this one.
        with upstream_call_span(method, params):
            # Propagate the active trace context to the upstream server over
            # stdio. W3C traceparent/tracestate go in the MCP `_meta` field
            # (mirrors the HTTP transport, which injects into request headers).
            # Servers that don't understand _meta ignore it. Copy params so the
            # caller's dict is never mutated.
            carrier: dict[str, str] = {}
            inject_trace_context(carrier)
            if carrier:
                meta = dict(params.get("_meta") or {})
                meta.update(carrier)
                params = {**params, "_meta": meta}

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
                return response
            except Empty:
                with self.pending_lock:
                    self.pending.pop(request_id, None)
                raise TimeoutError(f"timeout: {method} after {timeout}s") from None

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

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
