"""Tests for LogStreamBroadcaster and ws_logs_endpoint (LOG-04)."""

import asyncio
import threading
import time


from mcp_hangar.domain.value_objects.log import LogLine
from mcp_hangar.infrastructure.persistence.log_buffer import (
    ProviderLogBuffer,
)
from mcp_hangar.server.api.ws.logs import LogStreamBroadcaster, _line_to_message, get_log_broadcaster


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_line(provider_id: str = "svc", stream: str = "stderr", content: str = "msg") -> LogLine:
    return LogLine(provider_id=provider_id, stream=stream, content=content)  # type: ignore[arg-type]


def _run_in_new_loop(coro):
    """Run *coro* in a fresh event loop and return the result."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# LogStreamBroadcaster unit tests
# ---------------------------------------------------------------------------


class TestLogStreamBroadcaster:
    """Unit tests for LogStreamBroadcaster."""

    def test_register_increases_subscriber_count(self):
        """register() adds a subscriber for the given provider."""
        bc = LogStreamBroadcaster()
        loop = asyncio.new_event_loop()
        try:
            q: asyncio.Queue = asyncio.Queue()
            bc.register("svc", q, loop)
            assert bc.subscriber_count("svc") == 1
        finally:
            loop.close()

    def test_unregister_removes_subscriber(self):
        """unregister() removes the queue and decrements count."""
        bc = LogStreamBroadcaster()
        loop = asyncio.new_event_loop()
        try:
            q: asyncio.Queue = asyncio.Queue()
            bc.register("svc", q, loop)
            bc.unregister("svc", q)
            assert bc.subscriber_count("svc") == 0
        finally:
            loop.close()

    def test_unregister_unknown_queue_is_silent(self):
        """unregister() with an unknown queue does not raise."""
        bc = LogStreamBroadcaster()
        q: asyncio.Queue = asyncio.Queue()
        bc.unregister("svc", q)  # should not raise

    def test_subscriber_count_unknown_provider_is_zero(self):
        """subscriber_count() returns 0 for a provider with no subscribers."""
        bc = LogStreamBroadcaster()
        assert bc.subscriber_count("no-such-provider") == 0

    def test_multiple_subscribers_tracked_independently(self):
        """Two providers have independent subscriber counts."""
        bc = LogStreamBroadcaster()
        loop = asyncio.new_event_loop()
        try:
            q1: asyncio.Queue = asyncio.Queue()
            q2: asyncio.Queue = asyncio.Queue()
            bc.register("svc-a", q1, loop)
            bc.register("svc-b", q2, loop)
            assert bc.subscriber_count("svc-a") == 1
            assert bc.subscriber_count("svc-b") == 1
        finally:
            loop.close()

    def test_notify_delivers_to_subscriber(self):
        """notify() enqueues the line on the subscriber's queue via call_soon_threadsafe."""
        received: list[LogLine] = []

        async def _run():
            bc = LogStreamBroadcaster()
            loop = asyncio.get_event_loop()
            q: asyncio.Queue = asyncio.Queue()
            bc.register("svc", q, loop)

            line = _make_line("svc")
            bc.notify(line)

            # Give call_soon_threadsafe a moment to schedule the put
            result = await asyncio.wait_for(q.get(), timeout=1.0)
            received.append(result)

        _run_in_new_loop(_run())
        assert len(received) == 1
        assert received[0].content == "msg"

    def test_notify_only_delivers_to_matching_provider(self):
        """notify() does not deliver to subscribers of a different provider."""

        async def _run():
            bc = LogStreamBroadcaster()
            loop = asyncio.get_event_loop()
            q_a: asyncio.Queue = asyncio.Queue()
            q_b: asyncio.Queue = asyncio.Queue()
            bc.register("svc-a", q_a, loop)
            bc.register("svc-b", q_b, loop)

            bc.notify(_make_line("svc-a", content="for-a"))
            await asyncio.sleep(0.05)

            assert q_a.qsize() == 1
            assert q_b.qsize() == 0

        _run_in_new_loop(_run())

    def test_notify_drops_when_queue_full(self):
        """notify() silently drops lines when the queue is at capacity."""

        async def _run():
            bc = LogStreamBroadcaster()
            loop = asyncio.get_event_loop()
            # Queue with capacity 1
            q: asyncio.Queue = asyncio.Queue(maxsize=1)
            bc.register("svc", q, loop)

            bc.notify(_make_line("svc", content="first"))
            bc.notify(_make_line("svc", content="second"))  # should be dropped
            await asyncio.sleep(0.05)

            assert q.qsize() == 1
            item = q.get_nowait()
            assert item.content == "first"

        _run_in_new_loop(_run())

    def test_notify_from_different_thread(self):
        """notify() called from a background thread delivers to the async queue."""
        received: list[LogLine] = []

        async def _run():
            bc = LogStreamBroadcaster()
            loop = asyncio.get_event_loop()
            q: asyncio.Queue = asyncio.Queue()
            bc.register("svc", q, loop)

            line = _make_line("svc", content="from-thread")

            def _thread_func():
                time.sleep(0.01)
                bc.notify(line)

            t = threading.Thread(target=_thread_func, daemon=True)
            t.start()

            result = await asyncio.wait_for(q.get(), timeout=2.0)
            received.append(result)
            t.join(timeout=1.0)

        _run_in_new_loop(_run())
        assert received[0].content == "from-thread"


# ---------------------------------------------------------------------------
# ProviderLogBuffer on_append callback integration
# ---------------------------------------------------------------------------


class TestProviderLogBufferOnAppend:
    """Tests for the on_append callback wired into ProviderLogBuffer."""

    def test_on_append_called_on_each_append(self):
        """on_append callback fires once per appended line."""
        calls: list[LogLine] = []
        buf = ProviderLogBuffer("svc", on_append=calls.append)
        line = _make_line("svc")
        buf.append(line)
        assert calls == [line]

    def test_on_append_called_multiple_times(self):
        """on_append fires for each of several appended lines."""
        calls: list[LogLine] = []
        buf = ProviderLogBuffer("svc", on_append=calls.append)
        for i in range(5):
            buf.append(LogLine(provider_id="svc", stream="stderr", content=str(i)))
        assert len(calls) == 5

    def test_on_append_none_does_not_raise(self):
        """Buffer without on_append callback appends without error."""
        buf = ProviderLogBuffer("svc")
        buf.append(_make_line("svc"))  # should not raise

    def test_on_append_called_outside_lock(self):
        """on_append is called after the lock is released (no deadlock from reentrant access)."""
        buf = ProviderLogBuffer("svc")
        accessed: list[bool] = []

        def _callback(line: LogLine) -> None:
            # Re-enter the buffer (calls tail) -- would deadlock if lock is still held
            accessed.append(len(buf.tail(10)) > 0)

        buf._on_append = _callback
        buf.append(_make_line("svc"))
        assert accessed == [True]


# ---------------------------------------------------------------------------
# _line_to_message helper
# ---------------------------------------------------------------------------


class TestLineToMessage:
    """Tests for the _line_to_message serialization helper."""

    def test_message_type_is_log_line(self):
        """type field is 'log_line'."""
        msg = _line_to_message(_make_line())
        assert msg["type"] == "log_line"

    def test_message_contains_all_fields(self):
        """Message includes provider_id, stream, content, recorded_at."""
        line = LogLine(provider_id="svc", stream="stdout", content="hello")
        msg = _line_to_message(line)
        assert msg["provider_id"] == "svc"
        assert msg["stream"] == "stdout"
        assert msg["content"] == "hello"
        assert isinstance(msg["recorded_at"], float)


# ---------------------------------------------------------------------------
# get_log_broadcaster singleton
# ---------------------------------------------------------------------------


class TestGetLogBroadcaster:
    """Tests for the module-level singleton accessor."""

    def test_returns_same_instance_on_repeated_calls(self):
        """get_log_broadcaster() always returns the same object."""
        assert get_log_broadcaster() is get_log_broadcaster()

    def test_returns_log_stream_broadcaster_instance(self):
        """Return value is a LogStreamBroadcaster."""
        assert isinstance(get_log_broadcaster(), LogStreamBroadcaster)
