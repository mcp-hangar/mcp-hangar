"""Regression tests for StdioClient request-response ordering.

Verifies the critical invariant: pending request must be registered BEFORE
writing to stdin. Violation would cause responses to be dropped when the
reader thread processes them before registration completes.
"""

import subprocess
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.domain.exceptions import ClientError
from mcp_hangar.stdio_client import StdioClient


def _create_mock_popen():
    """Create a mock Popen with stdin/stdout/stderr pipes."""
    mock_popen = MagicMock(spec=subprocess.Popen)
    mock_popen.pid = 12345
    mock_popen.stdin = MagicMock()
    mock_popen.stdout = MagicMock()
    mock_popen.stderr = MagicMock()
    mock_popen.poll.return_value = None  # Process is alive
    # stdout.readline blocks forever by default (reader thread)
    mock_popen.stdout.readline.return_value = ""
    return mock_popen


class TestPendingRequestRegisteredBeforeStdinWrite:
    """Verify register-before-write ordering invariant."""

    def test_pending_request_registered_before_stdin_write(self):
        """At the moment stdin.write is called, request_id must already be in self.pending.

        This is a regression test for the ordering invariant documented in stdio_client.py.
        If the write happened before registration, a fast response from the subprocess
        could be dropped by the reader thread (no matching pending request).
        """
        mock_popen = _create_mock_popen()
        request_ids_in_pending_at_write_time: list[list[str]] = []

        # Patch threading.Thread to prevent reader_loop from starting
        with patch("mcp_hangar.stdio_client.threading.Thread") as mock_thread_cls:
            mock_thread_instance = MagicMock()
            mock_thread_cls.return_value = mock_thread_instance

            client = StdioClient(mock_popen)

        # Now instrument stdin.write to capture pending state at write time
        def capture_pending_on_write(data):
            # Snapshot the pending dict keys at the moment of write
            request_ids_in_pending_at_write_time.append(list(client.pending.keys()))

        mock_popen.stdin.write.side_effect = capture_pending_on_write

        # We need to provide a response on the queue so call() doesn't block
        # Use a side effect on the queue.get to return a response
        with patch("mcp_hangar.stdio_client.uuid.uuid4") as mock_uuid:
            mock_uuid.return_value = MagicMock()
            mock_uuid.return_value.__str__ = MagicMock(return_value="test-uuid-123")

            # Patch Queue.get to return a mock response immediately
            with patch.object(Queue, "get", return_value={"jsonrpc": "2.0", "id": "test-uuid-123", "result": {}}):
                client.call("test_method", {})

        # Verify: at the moment write was called, our request_id was already in pending
        assert len(request_ids_in_pending_at_write_time) == 1
        assert "test-uuid-123" in request_ids_in_pending_at_write_time[0], (
            "Request ID must be registered in self.pending BEFORE stdin.write is called. "
            f"Found pending keys at write time: {request_ids_in_pending_at_write_time[0]}"
        )

        client.closed = True  # Prevent close from doing work


class TestPendingRequestCleanedUpOnWriteFailure:
    """Verify that pending request is removed when stdin.write raises."""

    def test_pending_request_cleaned_up_on_write_failure(self):
        """If stdin.write raises, the pending request must be removed from self.pending.

        Without cleanup, the pending request would leak and never get a response,
        causing the caller to hang until timeout.
        """
        mock_popen = _create_mock_popen()
        mock_popen.stdin.write.side_effect = BrokenPipeError("pipe broken")

        # Patch threading.Thread to prevent reader_loop from starting
        with patch("mcp_hangar.stdio_client.threading.Thread") as mock_thread_cls:
            mock_thread_instance = MagicMock()
            mock_thread_cls.return_value = mock_thread_instance

            client = StdioClient(mock_popen)

        with patch("mcp_hangar.stdio_client.uuid.uuid4") as mock_uuid:
            mock_uuid.return_value = MagicMock()
            mock_uuid.return_value.__str__ = MagicMock(return_value="fail-uuid-456")

            with pytest.raises(ClientError, match="write_failed"):
                client.call("test_method", {})

        # Verify: pending request was cleaned up after write failure
        assert "fail-uuid-456" not in client.pending, (
            f"Pending request must be removed after write failure. Remaining: {list(client.pending.keys())}"
        )

        client.closed = True  # Prevent close from doing work
