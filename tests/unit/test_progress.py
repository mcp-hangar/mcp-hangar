"""Tests for progress tracking module."""

import asyncio
import threading
import time

import pytest

from mcp_hangar.progress import (
    create_progress_tracker,
    EventType,
    get_stage_message,
    ProgressEvent,
    ProgressOperation,
    ProgressStage,
    ProgressTracker,
)


class TestProgressStage:
    """Tests for ProgressStage enum."""

    def test_stage_values(self):
        """Test that all expected stages exist."""
        assert ProgressStage.COLD_START.value == "cold_start"
        assert ProgressStage.LAUNCHING.value == "launching"
        assert ProgressStage.READY.value == "ready"
        assert ProgressStage.EXECUTING.value == "executing"
        assert ProgressStage.COMPLETE.value == "complete"
        assert ProgressStage.FAILED.value == "failed"
        assert ProgressStage.RETRYING.value == "retrying"


class TestProgressEvent:
    """Tests for ProgressEvent dataclass."""

    def test_basic_event_creation(self):
        """Test creating a basic progress event."""
        event = ProgressEvent(
            type=EventType.PROGRESS,
            stage=ProgressStage.LAUNCHING,
            message="Starting provider...",
            elapsed_ms=150.5,
        )
        assert event.type == EventType.PROGRESS
        assert event.stage == ProgressStage.LAUNCHING
        assert event.message == "Starting provider..."
        assert event.elapsed_ms == 150.5

    def test_event_to_dict(self):
        """Test event serialization."""
        event = ProgressEvent(
            type=EventType.PROGRESS,
            stage=ProgressStage.READY,
            message="Provider ready",
            elapsed_ms=200.0,
            details={"tools_count": 5},
        )
        result = event.to_dict()
        assert result["type"] == "progress"
        assert result["stage"] == "ready"
        assert result["message"] == "Provider ready"
        assert result["details"]["tools_count"] == 5

    def test_result_event(self):
        """Test result event with data."""
        event = ProgressEvent(
            type=EventType.RESULT,
            stage=ProgressStage.COMPLETE,
            message="Operation completed",
            elapsed_ms=500.0,
            data={"result": "success"},
        )
        assert event.type == EventType.RESULT
        assert event.data == {"result": "success"}

    def test_error_event(self):
        """Test error event with exception."""
        exc = ValueError("Something failed")
        event = ProgressEvent(
            type=EventType.ERROR,
            stage=ProgressStage.FAILED,
            message="Operation failed",
            elapsed_ms=100.0,
            exception=exc,
        )
        assert event.type == EventType.ERROR
        assert event.exception == exc


class TestProgressTracker:
    """Tests for ProgressTracker."""

    def test_basic_progress_tracking(self):
        """Test basic progress reporting."""
        tracker = ProgressTracker(provider="math", operation="add")

        tracker.report(ProgressStage.LAUNCHING, "Starting...")
        tracker.report(ProgressStage.READY, "Ready!")
        tracker.complete({"result": 42})

        events = tracker.get_all_events()
        assert len(events) == 3
        assert events[0].stage == ProgressStage.LAUNCHING
        assert events[1].stage == ProgressStage.READY
        assert events[2].type == EventType.RESULT

    def test_callback_invocation(self):
        """Test that callback is invoked for each progress event."""
        callback_calls = []

        def on_progress(stage: str, message: str, elapsed_ms: float):
            callback_calls.append((stage, message))

        tracker = ProgressTracker(callback=on_progress)
        tracker.report(ProgressStage.COLD_START, "Provider is cold")
        tracker.report(ProgressStage.READY, "Provider ready")

        assert len(callback_calls) == 2
        assert callback_calls[0] == ("cold_start", "Provider is cold")
        assert callback_calls[1] == ("ready", "Provider ready")

    def test_elapsed_time_tracking(self):
        """Test that elapsed time is tracked correctly."""
        tracker = ProgressTracker()

        # Wait a bit to ensure measurable elapsed time
        time.sleep(0.01)
        tracker.report(ProgressStage.READY, "Ready")

        events = tracker.get_all_events()
        assert events[0].elapsed_ms >= 10  # At least 10ms

    def test_complete_marks_done(self):
        """Test that complete() marks tracker as done."""
        tracker = ProgressTracker()
        tracker.report(ProgressStage.READY, "Ready")
        tracker.complete({"data": "result"})

        assert tracker._completed is True
        assert tracker._result == {"data": "result"}

    def test_fail_marks_done_with_error(self):
        """Test that fail() marks tracker as done with error."""
        tracker = ProgressTracker()
        error = ValueError("Something went wrong")
        tracker.fail(error)

        assert tracker._completed is True
        assert tracker._error == error

    def test_iterator_protocol(self):
        """Test synchronous iteration over events."""
        tracker = ProgressTracker()

        # Start a thread to produce events
        def produce_events():
            time.sleep(0.01)
            tracker.report(ProgressStage.LAUNCHING, "Starting")
            time.sleep(0.01)
            tracker.report(ProgressStage.READY, "Ready")
            time.sleep(0.01)
            tracker.complete({"result": "done"})

        thread = threading.Thread(target=produce_events)
        thread.start()

        # Collect events from iterator
        events = list(tracker)
        thread.join()

        assert len(events) == 3
        assert events[-1].type == EventType.RESULT


class TestProgressOperation:
    """Tests for ProgressOperation context manager."""

    def test_context_manager_success(self):
        """Test context manager with successful operation."""
        events = []

        def callback(stage, message, elapsed):
            events.append((stage, message))

        with ProgressOperation("math", "add", callback=callback) as tracker:
            tracker.report(ProgressStage.LAUNCHING, "Starting")
            tracker.complete({"result": 5})

        assert len(events) == 2

    def test_context_manager_auto_fail_on_exception(self):
        """Test that exception triggers fail()."""
        tracker = None

        with pytest.raises(ValueError), ProgressOperation("test", "op") as t:
            tracker = t
            raise ValueError("Test error")

        assert tracker._completed is True
        assert tracker._error is not None


class TestGetStageMessage:
    """Tests for get_stage_message helper."""

    def test_cold_start_message(self):
        """Test cold start message."""
        msg = get_stage_message(ProgressStage.COLD_START)
        assert "cold" in msg.lower()

    def test_executing_message_with_tool(self):
        """Test executing message with tool name."""
        msg = get_stage_message(ProgressStage.EXECUTING, tool="add")
        assert "add" in msg

    def test_complete_message_with_elapsed(self):
        """Test complete message with elapsed time."""
        msg = get_stage_message(ProgressStage.COMPLETE, elapsed_ms=150.0)
        assert "150" in msg

    def test_retrying_message_with_attempts(self):
        """Test retrying message with attempt info."""
        msg = get_stage_message(ProgressStage.RETRYING, attempt=2, max_attempts=3)
        assert "2" in msg and "3" in msg


class TestCreateProgressTracker:
    """Tests for create_progress_tracker factory function."""

    def test_creates_tracker_with_callback(self):
        """Test factory creates tracker with callback."""
        callback_calls = []

        def cb(stage, msg, elapsed):
            callback_calls.append(stage)

        tracker = create_progress_tracker(
            provider="test",
            operation="op",
            callback=cb,
        )
        tracker.report(ProgressStage.READY, "Ready")

        assert len(callback_calls) == 1


@pytest.mark.asyncio
class TestAsyncProgressIteration:
    """Tests for async progress iteration."""

    async def test_async_iterator(self):
        """Test async iteration over events."""
        tracker = ProgressTracker()

        async def produce_events():
            await asyncio.sleep(0.01)
            tracker.report(ProgressStage.LAUNCHING, "Starting")
            await asyncio.sleep(0.01)
            tracker.complete({"result": "done"})

        # Start producer task
        task = asyncio.create_task(produce_events())

        # Collect events
        events = []
        async for event in tracker:
            events.append(event)
            if event.type == EventType.RESULT:
                break

        await task
        assert len(events) >= 1
        assert events[-1].type == EventType.RESULT


class TestProgressEventDetails:
    """Tests for ProgressEvent details handling."""

    def test_event_with_empty_details(self):
        """Test event with no details."""
        event = ProgressEvent(
            type=EventType.PROGRESS,
            stage=ProgressStage.READY,
            message="Ready",
            elapsed_ms=100.0,
        )
        result = event.to_dict()
        assert "details" not in result or result.get("details") is None

    def test_event_with_nested_details(self):
        """Test event with nested details dict."""
        event = ProgressEvent(
            type=EventType.PROGRESS,
            stage=ProgressStage.COMPLETE,
            message="Done",
            elapsed_ms=500.0,
            details={
                "provider_id": "math",
                "tool_name": "add",
                "duration_ms": 50.5,
            },
        )
        result = event.to_dict()
        assert result["details"]["tool_name"] == "add"


class TestProgressTrackerAdvanced:
    """Advanced tests for ProgressTracker."""

    def test_multiple_reports(self):
        """Test multiple progress reports."""
        tracker = ProgressTracker(provider="test", operation="multi")

        tracker.report(ProgressStage.COLD_START, "Cold")
        tracker.report(ProgressStage.LAUNCHING, "Launching")
        tracker.report(ProgressStage.INITIALIZING, "Initializing")
        tracker.report(ProgressStage.READY, "Ready")
        tracker.report(ProgressStage.EXECUTING, "Executing")
        tracker.complete({"result": "done"})

        events = tracker.get_all_events()
        assert len(events) == 6
        assert events[0].stage == ProgressStage.COLD_START
        assert events[-1].type == EventType.RESULT

    def test_report_with_details(self):
        """Test report with additional details."""
        tracker = ProgressTracker()
        tracker.report(
            ProgressStage.EXECUTING,
            "Running query",
            details={"sql": "SELECT * FROM users", "estimated_rows": 1000},
        )

        events = tracker.get_all_events()
        assert len(events) == 1
        assert events[0].details["sql"] == "SELECT * FROM users"

    def test_fail_includes_error_info(self):
        """Test that fail() includes error information."""
        tracker = ProgressTracker()
        error = ValueError("Something went wrong")
        tracker.fail(error)

        events = tracker.get_all_events()
        assert len(events) == 1
        assert events[0].type == EventType.ERROR
        assert events[0].stage == ProgressStage.FAILED
        assert events[0].exception == error

    def test_complete_marks_done(self):
        """Test that complete marks tracker as done."""
        tracker = ProgressTracker()
        tracker.complete({"result": "done"})
        assert tracker._completed is True

    def test_fail_marks_done(self):
        """Test that fail marks tracker as done."""
        tracker = ProgressTracker()
        tracker.fail(ValueError("error"))
        assert tracker._completed is True


class TestGetStageMessageAdvanced:
    """Advanced tests for get_stage_message."""

    def test_all_stages_have_messages(self):
        """Test that all stages have default messages."""
        for stage in ProgressStage:
            msg = get_stage_message(stage)
            assert isinstance(msg, str)
            assert len(msg) > 0

    def test_cold_start_with_provider(self):
        """Test cold_start message with provider name."""
        msg = get_stage_message(ProgressStage.COLD_START, provider="sqlite")
        assert "cold" in msg.lower()

    def test_failed_with_error(self):
        """Test failed message with error info."""
        msg = get_stage_message(ProgressStage.FAILED, error="Connection refused")
        assert "Connection refused" in msg or "failed" in msg.lower()

    def test_processing_message(self):
        """Test processing stage message."""
        msg = get_stage_message(ProgressStage.PROCESSING)
        assert "process" in msg.lower()


class TestProgressOperationAdvanced:
    """Advanced tests for ProgressOperation context manager."""

    def test_context_manager_with_callback(self):
        """Test context manager passes callback correctly."""
        events = []

        def track_progress(stage, message, elapsed):
            events.append(stage)

        with ProgressOperation("test", "op", callback=track_progress) as tracker:
            tracker.report(ProgressStage.READY, "Ready")
            tracker.complete({"done": True})

        assert "ready" in events
        assert "complete" in events

    def test_context_manager_exception_includes_traceback(self):
        """Test that exceptions in context manager are properly handled."""
        tracker = None

        try:
            with ProgressOperation("test", "op") as t:
                tracker = t
                raise RuntimeError("Test error")
        except RuntimeError:
            pass

        assert tracker._completed is True
        assert tracker._error is not None
        assert isinstance(tracker._error, RuntimeError)


class TestProgressCallbackTypes:
    """Tests for progress callback type handling."""

    def test_callback_receives_correct_args(self):
        """Test callback receives stage, message, elapsed_ms."""
        received_args = []

        def callback(stage, message, elapsed_ms):
            received_args.append(
                {
                    "stage": stage,
                    "message": message,
                    "elapsed_ms": elapsed_ms,
                }
            )

        tracker = ProgressTracker(callback=callback)
        tracker.report(ProgressStage.READY, "Test message")

        assert len(received_args) == 1
        assert received_args[0]["stage"] == "ready"
        assert received_args[0]["message"] == "Test message"
        assert isinstance(received_args[0]["elapsed_ms"], float)

    def test_callback_exception_does_not_break_tracker(self):
        """Test that callback exception doesn't break tracker."""

        def bad_callback(stage, message, elapsed_ms):
            raise ValueError("Callback error")

        tracker = ProgressTracker(callback=bad_callback)
        # Should not raise
        tracker.report(ProgressStage.READY, "Test")
        tracker.complete({"result": "ok"})

        assert tracker._completed is True
