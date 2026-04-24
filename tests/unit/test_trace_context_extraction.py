"""Tests for W3C TraceContext extraction in batch executor.

The BatchExecutor._execute_call method must extract W3C TraceContext
from incoming call metadata before creating a child span. If metadata
contains 'traceparent', the span should be a child of the incoming
trace context. If no traceparent is present, execution proceeds without
crashing.
"""

import threading
from unittest.mock import MagicMock, patch


from mcp_hangar.server.tools.batch.models import CallSpec


# A valid W3C traceparent header value
VALID_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class TestTraceContextExtraction:
    """BatchExecutor must extract W3C TraceContext from incoming call metadata."""

    def _make_call_spec(
        self,
        metadata: dict[str, str] | None = None,
    ) -> CallSpec:
        """Create a minimal CallSpec for testing."""
        return CallSpec(
            index=0,
            call_id="test-call-1",
            mcp_server="math",
            tool="add",
            arguments={"a": 1},
            metadata=metadata,
        )

    @patch("mcp_hangar.server.tools.batch.executor.get_context")
    def test_extract_trace_context_called_when_traceparent_present(
        self,
        mock_get_context: MagicMock,
    ) -> None:
        """extract_trace_context is called when call metadata has traceparent."""
        # Setup mock context to avoid real provider lookups
        mock_ctx = MagicMock()
        mock_ctx.get_mcp_server.return_value = None
        mock_ctx.mcp_server_exists.return_value = False
        mock_get_context.return_value = mock_ctx

        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        executor = BatchExecutor()
        call_spec = self._make_call_spec(
            metadata={"traceparent": VALID_TRACEPARENT},
        )

        with patch("mcp_hangar.server.tools.batch.executor.extract_trace_context") as mock_extract:
            mock_extract.return_value = None
            # Execute -- will fail at provider lookup, but we only care
            # about whether extract_trace_context was called
            _ = executor._execute_call(
                call_spec,
                cancel_event=threading.Event(),
                global_timeout=60.0,
                batch_start_time=0.0,
            )
            mock_extract.assert_called_once_with({"traceparent": VALID_TRACEPARENT})

    @patch("mcp_hangar.server.tools.batch.executor.get_context")
    def test_extract_called_with_empty_dict_when_no_traceparent(
        self,
        mock_get_context: MagicMock,
    ) -> None:
        """extract_trace_context is called with empty dict when metadata is empty."""
        mock_ctx = MagicMock()
        mock_ctx.get_mcp_server.return_value = None
        mock_ctx.mcp_server_exists.return_value = False
        mock_get_context.return_value = mock_ctx

        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        executor = BatchExecutor()
        call_spec = self._make_call_spec(metadata={})

        with patch("mcp_hangar.server.tools.batch.executor.extract_trace_context") as mock_extract:
            mock_extract.return_value = None
            executor._execute_call(
                call_spec,
                cancel_event=threading.Event(),
                global_timeout=60.0,
                batch_start_time=0.0,
            )
            # Called with empty dict -- no crash
            mock_extract.assert_called_once_with({})

    @patch("mcp_hangar.server.tools.batch.executor.get_context")
    def test_extract_called_with_empty_dict_when_metadata_is_none(
        self,
        mock_get_context: MagicMock,
    ) -> None:
        """extract_trace_context is called with {} when metadata is None."""
        mock_ctx = MagicMock()
        mock_ctx.get_mcp_server.return_value = None
        mock_ctx.mcp_server_exists.return_value = False
        mock_get_context.return_value = mock_ctx

        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        executor = BatchExecutor()
        call_spec = self._make_call_spec(metadata=None)

        with patch("mcp_hangar.server.tools.batch.executor.extract_trace_context") as mock_extract:
            mock_extract.return_value = None
            executor._execute_call(
                call_spec,
                cancel_event=threading.Event(),
                global_timeout=60.0,
                batch_start_time=0.0,
            )
            # None normalized to {} -- no crash
            mock_extract.assert_called_once_with({})
