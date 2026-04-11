"""Tests for cloud.buffer -- bounded event buffer."""

from mcp_hangar.cloud.buffer import EventBuffer


class TestEventBuffer:
    def test_push_and_drain(self):
        buf = EventBuffer(max_size=100)
        buf.push({"event_type": "ToolInvocationCompleted"})
        buf.push({"event_type": "ProviderStarted"})
        batch = buf.drain(10)
        assert len(batch) == 2
        assert batch[0]["event_type"] == "ToolInvocationCompleted"
        assert buf.size == 0

    def test_drain_respects_max_items(self):
        buf = EventBuffer(max_size=100)
        for i in range(10):
            buf.push({"i": i})
        batch = buf.drain(3)
        assert len(batch) == 3
        assert buf.size == 7

    def test_drain_empty_buffer(self):
        buf = EventBuffer()
        assert buf.drain(100) == []

    def test_overflow_drops_oldest(self):
        buf = EventBuffer(max_size=3)
        buf.push({"i": 0})
        buf.push({"i": 1})
        buf.push({"i": 2})
        buf.push({"i": 3})  # should evict i=0
        batch = buf.drain(10)
        assert len(batch) == 3
        assert batch[0]["i"] == 1
        assert buf.dropped >= 1

    def test_dropped_counter(self):
        buf = EventBuffer(max_size=2)
        buf.push({"a": 1})
        buf.push({"a": 2})
        assert buf.dropped == 0
        buf.push({"a": 3})  # overflow
        assert buf.dropped == 1
        buf.push({"a": 4})  # overflow again
        assert buf.dropped == 2

    def test_thread_safety(self):
        """Push from multiple threads concurrently."""
        import threading

        buf = EventBuffer(max_size=10_000)
        n_threads = 4
        n_per_thread = 500

        def pusher():
            for i in range(n_per_thread):
                buf.push({"i": i})

        threads = [threading.Thread(target=pusher) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = 0
        while True:
            batch = buf.drain(1000)
            if not batch:
                break
            total += len(batch)
        assert total == n_threads * n_per_thread
