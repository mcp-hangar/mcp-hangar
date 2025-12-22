"""Thread-safe stdio client with proper message correlation."""

from dataclasses import dataclass
import json
import logging
from queue import Empty, Queue
import subprocess
import threading
import time
from typing import Any, Dict
import uuid

from .domain.exceptions import ClientError

logger = logging.getLogger(__name__)


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
        self.pending: Dict[str, PendingRequest] = {}
        self.pending_lock = threading.Lock()
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.closed = False
        self.reader_thread.start()

    def _reader_loop(self):
        """
        Read stdout and dispatch responses to waiting callers.
        Runs in a dedicated daemon thread.
        """
        while not self.closed:
            try:
                line = self.process.stdout.readline()
                if not line:
                    # EOF reached, process died
                    logger.warning("stdio_client: EOF on stdout, process died")
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.error(f"stdio_client: malformed JSON: {line[:100]}, error={e}")
                    continue

                msg_id = msg.get("id")

                if msg_id:
                    # This is a response to a request
                    with self.pending_lock:
                        pending = self.pending.pop(msg_id, None)

                    if pending:
                        pending.result_queue.put(msg)
                    else:
                        logger.warning(
                            f"stdio_client: received response for unknown request: {msg_id}"
                        )
                else:
                    # Unsolicited notification - log and ignore
                    logger.debug(f"stdio_client: unsolicited notification: {msg}")

            except Exception as e:
                logger.error(f"stdio_client: reader loop error: {e}")
                break

        # Clean up on exit
        self._cleanup_pending("reader_died")

    def _cleanup_pending(self, error_msg: str):
        """Clean up all pending requests on shutdown or error."""
        with self.pending_lock:
            for pending in self.pending.values():
                pending.result_queue.put({"error": {"code": -1, "message": error_msg}})
            self.pending.clear()

    def call(self, method: str, params: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
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

        pending = PendingRequest(
            request_id=request_id, result_queue=result_queue, started_at=time.time()
        )

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
            self.process.stdin.write(request_str)
            self.process.stdin.flush()
        except Exception as e:
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
            logger.debug(f"stdio_client: shutdown RPC failed (expected): {e}")

        # Terminate process
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    logger.warning("stdio_client: process didn't terminate, killing")
                    self.process.kill()
                    self.process.wait()
        except Exception as e:
            logger.error(f"stdio_client: error during process cleanup: {e}")

        # Clean up any remaining pending requests
        self._cleanup_pending("client_closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
