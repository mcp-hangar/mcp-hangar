"""Thread-safe stdio client with proper message correlation."""

from dataclasses import dataclass
import json
from queue import Empty, Queue
import subprocess
import threading
import time
from typing import Any
import uuid

from .domain.exceptions import ClientError
from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class PendingRequest:
    """Tracks a pending RPC request waiting for a response."""

    request_id: str
    result_queue: Queue
    started_at: float


class StdioClient:
    """
    Thread-safe JSON-RPC client over stdio.
    Handles message correlation, timeouts, and process lifecycle.
    """

    def __init__(self, popen: subprocess.Popen):
        """
        Initialize client with a running subprocess.

        Args:
            popen: subprocess.Popen instance with stdin/stdout pipes
        """
        self.process = popen
        self.pending: dict[str, PendingRequest] = {}
        self.pending_lock = threading.Lock()
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.closed = False
        self._last_stderr: str | None = None
        self.reader_thread.start()

    def _reader_loop(self):
        """
        Read stdout and dispatch responses to waiting callers.
        Runs in a dedicated daemon thread.
        """
        logger.info("stdio_client_reader_started", pid=self.process.pid)
        while not self.closed:
            try:
                line = self.process.stdout.readline()
                if not line:
                    # EOF reached, process died
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

            except Exception as e:
                logger.error("stdio_client_reader_error", error=str(e))
                break

        # Clean up on exit
        self._cleanup_pending("reader_died")

    def _capture_process_stderr(self) -> str | None:
        """Capture and log stderr from the process for debugging. Returns stderr text."""
        stderr_text = None
        try:
            # Log exit code
            rc = self.process.poll()
            if rc is not None:
                logger.error("stdio_client_process_exited", exit_code=rc)

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
                            logger.error("stdio_client_process_stderr", stderr=err_text)
                            stderr_text = err_text
                except Exception as read_err:
                    logger.debug("stdio_client_stderr_read_failed", error=str(read_err))
        except Exception as e:
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
        result_queue = Queue(maxsize=1)

        pending = PendingRequest(request_id=request_id, result_queue=result_queue, started_at=time.time())

        with self.pending_lock:
            self.pending[request_id] = pending

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        try:
            request_str = json.dumps(request) + "\n"
            logger.info(
                "stdio_client_sending_request",
                method=method,
                pid=self.process.pid,
                alive=self.process.poll() is None,
            )
            self.process.stdin.write(request_str)
            self.process.stdin.flush()
            logger.debug("stdio_client_request_sent")
        except Exception as e:
            logger.error("stdio_client_write_failed", error=str(e))
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise ClientError(f"write_failed: {e}")

        try:
            response = result_queue.get(timeout=timeout)
            return response
        except Empty:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise TimeoutError(f"timeout: {method} after {timeout}s")

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
        except Exception as e:
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
        except Exception as e:
            logger.error("stdio_client_cleanup_error", error=str(e))

        # Clean up any remaining pending requests
        self._cleanup_pending("client_closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
