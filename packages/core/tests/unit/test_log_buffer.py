"""Unit tests for LogLine value object, IProviderLogBuffer, ProviderLogBuffer, and registry."""

import threading
import time

import pytest

from mcp_hangar.domain.contracts.log_buffer import IProviderLogBuffer
from mcp_hangar.domain.value_objects.log import LogLine
from mcp_hangar.infrastructure.persistence.log_buffer import (
    DEFAULT_MAX_LINES,
    ProviderLogBuffer,
    clear_log_buffer_registry,
    get_log_buffer,
    get_or_create_log_buffer,
    remove_log_buffer,
    set_log_buffer,
)


# ---------------------------------------------------------------------------
# LogLine
# ---------------------------------------------------------------------------


class TestLogLine:
    def test_creates_with_required_fields(self):
        line = LogLine(provider_id="math", stream="stderr", content="error: boom")
        assert line.provider_id == "math"
        assert line.stream == "stderr"
        assert line.content == "error: boom"

    def test_recorded_at_defaults_to_now(self):
        before = time.time()
        line = LogLine(provider_id="p", stream="stdout", content="hello")
        after = time.time()
        assert before <= line.recorded_at <= after

    def test_recorded_at_accepts_explicit_value(self):
        line = LogLine(provider_id="p", stream="stdout", content="hi", recorded_at=1_000_000.0)
        assert line.recorded_at == 1_000_000.0

    def test_frozen_immutable(self):
        line = LogLine(provider_id="p", stream="stdout", content="hi")
        with pytest.raises((AttributeError, TypeError)):
            line.content = "changed"  # type: ignore[misc]

    def test_to_dict_contains_all_keys(self):
        line = LogLine(provider_id="math", stream="stderr", content="oops", recorded_at=42.0)
        d = line.to_dict()
        assert d == {
            "provider_id": "math",
            "stream": "stderr",
            "content": "oops",
            "recorded_at": 42.0,
        }

    def test_stdout_and_stderr_are_valid_streams(self):
        LogLine(provider_id="p", stream="stdout", content="out")
        LogLine(provider_id="p", stream="stderr", content="err")


# ---------------------------------------------------------------------------
# ProviderLogBuffer
# ---------------------------------------------------------------------------


class TestProviderLogBuffer:
    def _make_buffer(self, max_lines: int = 5) -> ProviderLogBuffer:
        return ProviderLogBuffer("test-provider", max_lines=max_lines)

    def _line(self, content: str, stream: str = "stdout") -> LogLine:
        return LogLine(provider_id="test-provider", stream=stream, content=content)  # type: ignore[arg-type]

    # --- interface compliance ---

    def test_implements_interface(self):
        buf = self._make_buffer()
        assert isinstance(buf, IProviderLogBuffer)

    def test_provider_id_property(self):
        buf = ProviderLogBuffer("my-provider", max_lines=10)
        assert buf.provider_id == "my-provider"

    # --- append / tail / clear ---

    def test_tail_empty_buffer(self):
        buf = self._make_buffer()
        assert buf.tail(10) == []

    def test_append_single_line(self):
        buf = self._make_buffer()
        line = self._line("hello")
        buf.append(line)
        assert buf.tail(1) == [line]

    def test_tail_returns_chronological_order(self):
        buf = self._make_buffer()
        lines = [self._line(f"msg-{i}") for i in range(3)]
        for line in lines:
            buf.append(line)
        assert buf.tail(3) == lines

    def test_tail_limits_result(self):
        buf = self._make_buffer()
        lines = [self._line(f"msg-{i}") for i in range(4)]
        for line in lines:
            buf.append(line)
        result = buf.tail(2)
        assert result == lines[-2:]

    def test_tail_returns_all_when_n_exceeds_length(self):
        buf = self._make_buffer()
        lines = [self._line(f"msg-{i}") for i in range(3)]
        for line in lines:
            buf.append(line)
        assert buf.tail(100) == lines

    def test_ring_buffer_discards_oldest_when_full(self):
        buf = self._make_buffer(max_lines=3)
        lines = [self._line(f"msg-{i}") for i in range(5)]
        for line in lines:
            buf.append(line)
        result = buf.tail(10)
        assert result == lines[-3:]

    def test_clear_empties_buffer(self):
        buf = self._make_buffer()
        buf.append(self._line("hello"))
        buf.append(self._line("world"))
        buf.clear()
        assert buf.tail(10) == []

    def test_len_reflects_stored_count(self):
        buf = self._make_buffer()
        assert len(buf) == 0
        buf.append(self._line("a"))
        buf.append(self._line("b"))
        assert len(buf) == 2

    def test_len_capped_at_max_lines(self):
        buf = self._make_buffer(max_lines=3)
        for i in range(10):
            buf.append(self._line(f"msg-{i}"))
        assert len(buf) == 3

    # --- thread safety ---

    def test_concurrent_appends_are_safe(self):
        buf = ProviderLogBuffer("p", max_lines=1000)
        errors: list[Exception] = []

        def writer(n: int) -> None:
            try:
                for i in range(n):
                    buf.append(LogLine(provider_id="p", stream="stdout", content=f"{n}-{i}"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(50,)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(buf) == min(500, 1000)

    def test_concurrent_tail_while_writing(self):
        buf = ProviderLogBuffer("p", max_lines=500)
        stop = threading.Event()
        errors: list[Exception] = []

        def reader() -> None:
            while not stop.is_set():
                try:
                    buf.tail(10)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        def writer() -> None:
            for i in range(200):
                buf.append(LogLine(provider_id="p", stream="stderr", content=f"line-{i}"))

        r = threading.Thread(target=reader)
        w = threading.Thread(target=writer)
        r.start()
        w.start()
        w.join()
        stop.set()
        r.join()

        assert errors == []


# ---------------------------------------------------------------------------
# Singleton registry
# ---------------------------------------------------------------------------


class TestLogBufferRegistry:
    def setup_method(self) -> None:
        clear_log_buffer_registry()

    def teardown_method(self) -> None:
        clear_log_buffer_registry()

    def test_get_log_buffer_returns_none_when_unregistered(self):
        assert get_log_buffer("unknown") is None

    def test_set_and_get_log_buffer(self):
        buf = ProviderLogBuffer("math", max_lines=100)
        set_log_buffer("math", buf)
        assert get_log_buffer("math") is buf

    def test_set_log_buffer_overwrites_previous(self):
        buf1 = ProviderLogBuffer("p", max_lines=10)
        buf2 = ProviderLogBuffer("p", max_lines=20)
        set_log_buffer("p", buf1)
        set_log_buffer("p", buf2)
        assert get_log_buffer("p") is buf2

    def test_get_or_create_creates_new_buffer(self):
        buf = get_or_create_log_buffer("new-provider")
        assert isinstance(buf, ProviderLogBuffer)
        assert buf.provider_id == "new-provider"

    def test_get_or_create_returns_existing_buffer(self):
        buf1 = get_or_create_log_buffer("p")
        buf2 = get_or_create_log_buffer("p")
        assert buf1 is buf2

    def test_get_or_create_uses_custom_max_lines(self):
        buf = get_or_create_log_buffer("p", max_lines=42)
        assert isinstance(buf, ProviderLogBuffer)
        assert buf._max_lines == 42

    def test_get_or_create_does_not_override_existing_max_lines(self):
        """Second call with different max_lines must not replace the existing buffer."""
        buf1 = get_or_create_log_buffer("p", max_lines=42)
        buf2 = get_or_create_log_buffer("p", max_lines=999)
        assert buf1 is buf2
        assert buf2._max_lines == 42

    def test_remove_log_buffer(self):
        set_log_buffer("p", ProviderLogBuffer("p"))
        remove_log_buffer("p")
        assert get_log_buffer("p") is None

    def test_remove_nonexistent_is_noop(self):
        remove_log_buffer("ghost")  # should not raise

    def test_clear_registry(self):
        set_log_buffer("p1", ProviderLogBuffer("p1"))
        set_log_buffer("p2", ProviderLogBuffer("p2"))
        clear_log_buffer_registry()
        assert get_log_buffer("p1") is None
        assert get_log_buffer("p2") is None

    def test_default_max_lines_constant(self):
        assert DEFAULT_MAX_LINES == 1000

    def test_concurrent_get_or_create_creates_only_one_buffer(self):
        results: list[IProviderLogBuffer] = []
        lock = threading.Lock()
        errors: list[Exception] = []

        def create() -> None:
            try:
                buf = get_or_create_log_buffer("shared-provider")
                with lock:
                    results.append(buf)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=create) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == 20
        first = results[0]
        assert all(r is first for r in results)
