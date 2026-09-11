"""Unit tests for batch concurrency management.

Tests the two-level semaphore model (global + per-provider) used by
the BatchExecutor to control parallel execution of tool invocations.
"""

import asyncio
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.server.tools.batch import concurrency
from mcp_hangar.server.tools.batch.concurrency import (
    BATCH_CONCURRENCY_QUEUED_TOTAL,
    BATCH_CONCURRENCY_WAIT_SECONDS,
    BATCH_INFLIGHT_CALLS,
    ConcurrencyManager,
    DEFAULT_GLOBAL_CONCURRENCY,
    DEFAULT_PROVIDER_CONCURRENCY,
    get_concurrency_manager,
    init_concurrency_manager,
    reset_concurrency_manager,
)
from mcp_hangar.server.tools.batch.executor import BatchExecutor
from mcp_hangar.server.tools.batch.models import CallResult, CallSpec

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the module-level singleton before and after each test."""
    reset_concurrency_manager()
    yield
    reset_concurrency_manager()


# ---------------------------------------------------------------------------
# Construction & defaults
# ---------------------------------------------------------------------------


class TestConcurrencyManagerConstruction:
    """Tests for ConcurrencyManager initialization and defaults."""

    def test_default_limits(self):
        """Default limits match module constants."""
        cm = ConcurrencyManager()
        assert cm.global_limit == DEFAULT_GLOBAL_CONCURRENCY
        assert cm.default_mcp_server_limit == DEFAULT_PROVIDER_CONCURRENCY

    def test_custom_limits(self):
        """Custom limits are respected."""
        cm = ConcurrencyManager(global_limit=100, default_mcp_server_limit=5)
        assert cm.global_limit == 100
        assert cm.default_mcp_server_limit == 5

    def test_unlimited_global(self):
        """global_limit=0 means unlimited (no global semaphore)."""
        cm = ConcurrencyManager(global_limit=0, default_mcp_server_limit=10)
        assert cm.global_limit == 0

    def test_unlimited_provider(self):
        """default_mcp_server_limit=0 means unlimited per provider."""
        cm = ConcurrencyManager(global_limit=50, default_mcp_server_limit=0)
        assert cm.default_mcp_server_limit == 0

    def test_negative_global_limit_raises(self):
        """Negative global_limit raises ValueError."""
        with pytest.raises(ValueError, match="global_limit must be >= 0"):
            ConcurrencyManager(global_limit=-1)

    def test_negative_provider_limit_raises(self):
        """Negative default_mcp_server_limit raises ValueError."""
        with pytest.raises(ValueError, match="default_mcp_server_limit must be >= 0"):
            ConcurrencyManager(default_mcp_server_limit=-1)


# ---------------------------------------------------------------------------
# Per-provider limit management
# ---------------------------------------------------------------------------


class TestProviderLimits:
    """Tests for set_mcp_server_limit and get_mcp_server_limit."""

    def test_default_mcp_server_limit(self):
        """Providers without explicit limits use the default."""
        cm = ConcurrencyManager(default_mcp_server_limit=7)
        assert cm.get_mcp_server_limit("any-provider") == 7

    def test_set_mcp_server_limit(self):
        """Explicit per-provider limit overrides the default."""
        cm = ConcurrencyManager(default_mcp_server_limit=10)
        cm.set_mcp_server_limit("slow-api", 3)
        assert cm.get_mcp_server_limit("slow-api") == 3
        assert cm.get_mcp_server_limit("fast-api") == 10  # Still default

    def test_set_mcp_server_limit_unlimited(self):
        """Setting limit to 0 makes the provider unlimited."""
        cm = ConcurrencyManager(default_mcp_server_limit=10)
        cm.set_mcp_server_limit("unlimited-api", 0)
        assert cm.get_mcp_server_limit("unlimited-api") == 0

    def test_set_mcp_server_limit_negative_raises(self):
        """Negative per-provider limit raises ValueError."""
        cm = ConcurrencyManager()
        with pytest.raises(ValueError, match="limit must be >= 0"):
            cm.set_mcp_server_limit("api", -5)

    def test_update_provider_limit(self):
        """Updating a provider limit replaces the semaphore."""
        cm = ConcurrencyManager(default_mcp_server_limit=10)
        cm.set_mcp_server_limit("api", 5)
        assert cm.get_mcp_server_limit("api") == 5
        cm.set_mcp_server_limit("api", 20)
        assert cm.get_mcp_server_limit("api") == 20


# ---------------------------------------------------------------------------
# Acquire / release (core concurrency behavior)
# ---------------------------------------------------------------------------


class TestAcquireRelease:
    """Tests for the acquire() context manager."""

    def test_acquire_yields_wait_time(self):
        """acquire() yields a float representing wait time in seconds."""
        cm = ConcurrencyManager(global_limit=10, default_mcp_server_limit=10)
        with cm.acquire("test") as wait_s:
            assert isinstance(wait_s, float)
            assert wait_s >= 0

    def test_acquire_releases_on_normal_exit(self):
        """Slots are released after the context manager exits normally."""
        cm = ConcurrencyManager(global_limit=1, default_mcp_server_limit=1)

        with cm.acquire("test"):
            pass

        # Should be able to acquire again (slots freed)
        with cm.acquire("test"):
            pass

    def test_acquire_releases_on_exception(self):
        """Slots are released even if the block raises an exception."""
        cm = ConcurrencyManager(global_limit=1, default_mcp_server_limit=1)

        with pytest.raises(RuntimeError):
            with cm.acquire("test"):
                raise RuntimeError("boom")

        # Should be able to acquire again (slots freed)
        with cm.acquire("test"):
            pass

    def test_global_concurrency_limit_respected(self):
        """With global_limit=N, at most N calls execute simultaneously."""
        limit = 5
        cm = ConcurrencyManager(global_limit=limit, default_mcp_server_limit=0)

        concurrent_count = 0
        max_concurrent = 0
        lock = threading.Lock()

        def worker(worker_id: int):
            nonlocal concurrent_count, max_concurrent
            with cm.acquire(f"provider-{worker_id % 3}"):
                with lock:
                    concurrent_count += 1
                    max_concurrent = max(max_concurrent, concurrent_count)
                time.sleep(0.05)
                with lock:
                    concurrent_count -= 1

        # Launch more workers than the global limit
        total_workers = limit * 3
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(total_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert max_concurrent <= limit
        assert max_concurrent >= min(limit, total_workers)  # Should actually reach the limit

    def test_provider_concurrency_limit_respected(self):
        """Per-provider limit caps concurrent calls to that provider."""
        provider_limit = 3
        cm = ConcurrencyManager(global_limit=0, default_mcp_server_limit=provider_limit)

        concurrent_count = 0
        max_concurrent = 0
        lock = threading.Lock()

        def worker():
            nonlocal concurrent_count, max_concurrent
            with cm.acquire("same-provider"):
                with lock:
                    concurrent_count += 1
                    max_concurrent = max(max_concurrent, concurrent_count)
                time.sleep(0.05)
                with lock:
                    concurrent_count -= 1

        total_workers = provider_limit * 3
        threads = [threading.Thread(target=worker) for _ in range(total_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert max_concurrent <= provider_limit
        assert max_concurrent >= min(provider_limit, total_workers)

    def test_provider_isolation(self):
        """Provider A at max concurrency does not block Provider B."""
        cm = ConcurrencyManager(global_limit=0, default_mcp_server_limit=1)

        provider_a_started = threading.Event()
        provider_b_done = threading.Event()

        def slow_a():
            with cm.acquire("provider-a"):
                provider_a_started.set()
                # Hold the slot for a while
                time.sleep(0.5)

        def fast_b():
            # Wait until A is definitely holding its slot
            provider_a_started.wait(timeout=2)
            with cm.acquire("provider-b"):
                provider_b_done.set()

        t_a = threading.Thread(target=slow_a)
        t_b = threading.Thread(target=fast_b)
        t_a.start()
        t_b.start()

        # B should complete quickly even though A is holding its slot
        assert provider_b_done.wait(timeout=2), "Provider B was blocked by Provider A"

        t_a.join(timeout=2)
        t_b.join(timeout=2)

    def test_both_limits_apply(self):
        """When both global and provider limits are set, the stricter one wins."""
        # Global=3, Provider=5 -> effective max is 3 for a single provider
        cm = ConcurrencyManager(global_limit=3, default_mcp_server_limit=5)

        concurrent_count = 0
        max_concurrent = 0
        lock = threading.Lock()

        def worker():
            nonlocal concurrent_count, max_concurrent
            with cm.acquire("single-provider"):
                with lock:
                    concurrent_count += 1
                    max_concurrent = max(max_concurrent, concurrent_count)
                time.sleep(0.05)
                with lock:
                    concurrent_count -= 1

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Global limit (3) is stricter than provider limit (5)
        assert max_concurrent <= 3

    def test_mixed_providers_global_limit(self):
        """Global=5, Provider A=3, Provider B=3: total concurrent <= 5."""
        cm = ConcurrencyManager(global_limit=5, default_mcp_server_limit=3)

        concurrent_count = 0
        max_concurrent = 0
        lock = threading.Lock()

        def worker(provider: str):
            nonlocal concurrent_count, max_concurrent
            with cm.acquire(provider):
                with lock:
                    concurrent_count += 1
                    max_concurrent = max(max_concurrent, concurrent_count)
                time.sleep(0.05)
                with lock:
                    concurrent_count -= 1

        # 6 calls: 3 to A + 3 to B.  Each provider allows 3, but global=5
        threads = []
        for i in range(3):
            threads.append(threading.Thread(target=worker, args=("A",)))
            threads.append(threading.Thread(target=worker, args=("B",)))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert max_concurrent <= 5

    def test_unlimited_global(self):
        """global_limit=0 means no global semaphore, all run in parallel."""
        cm = ConcurrencyManager(global_limit=0, default_mcp_server_limit=0)

        concurrent_count = 0
        max_concurrent = 0
        lock = threading.Lock()
        total = 20

        def worker(i: int):
            nonlocal concurrent_count, max_concurrent
            with cm.acquire(f"provider-{i}"):
                with lock:
                    concurrent_count += 1
                    max_concurrent = max(max_concurrent, concurrent_count)
                time.sleep(0.05)
                with lock:
                    concurrent_count -= 1

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(total)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # With no limits, all should run concurrently
        assert max_concurrent >= total - 2  # Allow small scheduling variance


# ---------------------------------------------------------------------------
# True parallelism (not chunking)
# ---------------------------------------------------------------------------


class TestTrueParallelism:
    """Verify that semaphores provide true parallel execution, not wave chunking."""

    def test_parallel_not_chunked(self):
        """With concurrency=N and N calls, total time is roughly 1 call time (not N).

        10 calls x 50ms each, concurrency=10: should complete in ~50ms, not ~500ms.
        """
        cm = ConcurrencyManager(global_limit=10, default_mcp_server_limit=10)

        start = time.monotonic()

        def worker():
            with cm.acquire("test"):
                time.sleep(0.05)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        elapsed = time.monotonic() - start
        # Should be close to 50ms (1 wave), not 500ms (10 sequential) or
        # 100ms+ (chunked at 5). Allow generous headroom for CI.
        assert elapsed < 0.3, f"Expected ~50ms but took {elapsed * 1000:.0f}ms (chunking suspected)"

    def test_semaphore_allows_immediate_start_on_slot_free(self):
        """When a slot frees up mid-wave, queued calls start immediately.

        This is the key difference from sequential chunking: a fast call
        completing allows the next queued call to start without waiting
        for the entire wave.

        Setup: concurrency=2, 3 calls: [fast(25ms), slow(100ms), queued(25ms)]
        With semaphore: total ~ 100ms (fast completes, queued starts, slow finishes)
        With chunking(2): total ~ 125ms (wave1=[fast,slow]->100ms, wave2=[queued]->25ms)
        """
        cm = ConcurrencyManager(global_limit=2, default_mcp_server_limit=0)

        timestamps: dict[str, float] = {}
        lock = threading.Lock()

        def timed_worker(name: str, duration: float):
            with cm.acquire("test"):
                with lock:
                    timestamps[f"{name}_start"] = time.monotonic()
                time.sleep(duration)
                with lock:
                    timestamps[f"{name}_end"] = time.monotonic()

        threads = [
            threading.Thread(target=timed_worker, args=("fast", 0.025)),
            threading.Thread(target=timed_worker, args=("slow", 0.1)),
            threading.Thread(target=timed_worker, args=("queued", 0.025)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # The "queued" call should start before "slow" finishes,
        # because "fast" frees a slot at ~25ms while "slow" still has ~75ms left.
        # Allow some scheduling jitter.
        total_elapsed = max(timestamps.values()) - min(timestamps.values())
        # With semaphore: total ~ 100ms.  With chunking: total ~ 125ms.
        assert total_elapsed < 0.15, (
            f"Total elapsed {total_elapsed * 1000:.0f}ms suggests chunking, expected ~100ms with semaphore backpressure"
        )


# ---------------------------------------------------------------------------
# Wait time reporting
# ---------------------------------------------------------------------------


class TestWaitTimeReporting:
    """Tests that acquire() reports wait time accurately."""

    def test_no_contention_fast_wait(self):
        """Without contention, wait time is near zero."""
        cm = ConcurrencyManager(global_limit=100, default_mcp_server_limit=100)
        with cm.acquire("test") as wait_s:
            assert wait_s < 0.01

    def test_contention_reports_positive_wait(self):
        """Under contention, blocked callers report positive wait time."""
        cm = ConcurrencyManager(global_limit=1, default_mcp_server_limit=0)

        holder_started = threading.Event()
        wait_times: list[float] = []

        def holder():
            with cm.acquire("test"):
                holder_started.set()
                time.sleep(0.1)

        def waiter():
            holder_started.wait(timeout=2)
            time.sleep(0.01)  # Ensure we arrive after holder
            with cm.acquire("test") as wait_s:
                wait_times.append(wait_s)

        t1 = threading.Thread(target=holder)
        t2 = threading.Thread(target=waiter)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(wait_times) == 1
        assert wait_times[0] > 0.01, f"Expected positive wait, got {wait_times[0]}"


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestConcurrencyStats:
    """Tests for get_stats()."""

    def test_stats_reflect_defaults(self):
        """Stats show global and default provider limits."""
        cm = ConcurrencyManager(global_limit=50, default_mcp_server_limit=10)
        stats = cm.get_stats()
        assert stats["global_limit"] == 50
        assert stats["default_mcp_server_limit"] == 10
        assert stats["mcp_server_overrides"] == {}

    def test_stats_reflect_overrides(self):
        """Stats include per-provider overrides."""
        cm = ConcurrencyManager(global_limit=50, default_mcp_server_limit=10)
        cm.set_mcp_server_limit("slow", 3)
        cm.set_mcp_server_limit("fast", 20)
        stats = cm.get_stats()
        overrides = stats["mcp_server_overrides"]
        assert isinstance(overrides, dict)
        assert overrides["slow"] == 3
        assert overrides["fast"] == 20

    def test_stats_unlimited_shown_as_string(self):
        """Unlimited limits are shown as 'unlimited' in stats."""
        cm = ConcurrencyManager(global_limit=0, default_mcp_server_limit=0)
        stats = cm.get_stats()
        assert stats["global_limit"] == "unlimited"
        assert stats["default_mcp_server_limit"] == "unlimited"

    def test_stats_provider_unlimited(self):
        """Per-provider unlimited shown as 'unlimited'."""
        cm = ConcurrencyManager()
        cm.set_mcp_server_limit("api", 0)
        stats = cm.get_stats()
        overrides = stats["mcp_server_overrides"]
        assert isinstance(overrides, dict)
        assert overrides["api"] == "unlimited"


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------


class TestSingleton:
    """Tests for module-level singleton functions."""

    def test_get_creates_default(self):
        """get_concurrency_manager() creates a default instance."""
        cm = get_concurrency_manager()
        assert cm.global_limit == DEFAULT_GLOBAL_CONCURRENCY
        assert cm.default_mcp_server_limit == DEFAULT_PROVIDER_CONCURRENCY

    def test_get_returns_same_instance(self):
        """get_concurrency_manager() returns the same instance."""
        cm1 = get_concurrency_manager()
        cm2 = get_concurrency_manager()
        assert cm1 is cm2

    def test_init_replaces_singleton(self):
        """init_concurrency_manager() replaces the singleton."""
        cm1 = get_concurrency_manager()
        cm2 = init_concurrency_manager(global_limit=99, default_mcp_server_limit=7)
        assert cm2 is not cm1
        assert cm2.global_limit == 99
        assert cm2.default_mcp_server_limit == 7
        assert get_concurrency_manager() is cm2

    def test_init_with_provider_limits(self):
        """init_concurrency_manager() accepts provider-level limits."""
        cm = init_concurrency_manager(
            global_limit=50,
            default_mcp_server_limit=10,
            mcp_server_limits={"slow": 2, "fast": 25},
        )
        assert cm.get_mcp_server_limit("slow") == 2
        assert cm.get_mcp_server_limit("fast") == 25
        assert cm.get_mcp_server_limit("default") == 10

    def test_reset_clears_singleton(self):
        """reset_concurrency_manager() forces a new instance on next get."""
        cm1 = get_concurrency_manager()
        reset_concurrency_manager()
        cm2 = get_concurrency_manager()
        assert cm1 is not cm2


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Ensure existing configs without concurrency settings still work."""

    def test_default_config_works(self):
        """Without explicit concurrency config, reasonable defaults apply."""
        cm = ConcurrencyManager()

        # Should be able to run many calls with defaults
        results = []

        def worker(i: int):
            with cm.acquire(f"provider-{i % 5}") as wait_s:
                results.append((i, wait_s))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(results) == 20

    def test_no_concurrency_config_uses_defaults(self):
        """init with no arguments produces safe defaults."""
        cm = init_concurrency_manager()
        assert cm.global_limit == DEFAULT_GLOBAL_CONCURRENCY
        assert cm.default_mcp_server_limit == DEFAULT_PROVIDER_CONCURRENCY


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestConcurrencyMetrics:
    """Tests that concurrency metrics are recorded correctly."""

    def test_inflight_gauge_increments_and_decrements(self):
        """Global inflight gauge reflects in-progress calls."""
        cm = ConcurrencyManager(global_limit=10, default_mcp_server_limit=10)

        # Snapshot initial state
        initial_samples = BATCH_INFLIGHT_CALLS.collect()
        initial_value = initial_samples[0].value if initial_samples else 0

        inside_value = None

        def capture():
            nonlocal inside_value
            with cm.acquire("test"):
                samples = BATCH_INFLIGHT_CALLS.collect()
                # Find the unlabeled gauge value
                inside_value = samples[0].value if samples else None

        t = threading.Thread(target=capture)
        t.start()
        t.join(timeout=5)

        assert inside_value is not None
        assert inside_value > initial_value

        # After release, the gauge should be back to initial
        final_samples = BATCH_INFLIGHT_CALLS.collect()
        final_value = final_samples[0].value if final_samples else 0
        assert final_value == initial_value

    def test_wait_histogram_observed(self):
        """Wait time histogram is observed on each acquire."""
        cm = ConcurrencyManager(global_limit=10, default_mcp_server_limit=10)

        with cm.acquire("test-prov"):
            pass

        # At least one observation should have been added.
        # The Histogram API records buckets/sum/count internally;
        # at minimum the observe() call should not raise.
        samples = BATCH_CONCURRENCY_WAIT_SECONDS.collect()
        assert len(samples) > 0

    def test_queued_counter_incremented_on_contention(self):
        """Queued counter increases when a call has to wait."""
        cm = ConcurrencyManager(global_limit=1, default_mcp_server_limit=0)

        holder_started = threading.Event()

        def holder():
            with cm.acquire("q-test"):
                holder_started.set()
                time.sleep(0.1)

        def waiter():
            holder_started.wait(timeout=2)
            time.sleep(0.01)
            with cm.acquire("q-test"):
                pass

        t1 = threading.Thread(target=holder)
        t2 = threading.Thread(target=waiter)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # The queued counter should have been incremented for the waiter
        samples = BATCH_CONCURRENCY_QUEUED_TOTAL.collect()
        # At least one sample with mcp_server=q-test should exist
        found = any(s.labels.get("mcp_server") == "q-test" and s.value > 0 for s in samples)
        assert found, f"Expected queued counter for 'q-test', got {samples}"


# ---------------------------------------------------------------------------
# Thread safety of ConcurrencyManager itself
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Verify that ConcurrencyManager operations are thread-safe."""

    def test_concurrent_set_mcp_server_limit(self):
        """set_mcp_server_limit from multiple threads does not corrupt state."""
        cm = ConcurrencyManager()
        errors = []

        def setter(mcp_server_id: str, limit: int):
            try:
                for _ in range(50):
                    cm.set_mcp_server_limit(mcp_server_id, limit)
                    cm.get_mcp_server_limit(mcp_server_id)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=setter, args=(f"p-{i}", i)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread safety violation: {errors}"

    def test_concurrent_acquire_different_providers(self):
        """Concurrent acquisitions on different providers are independent."""
        cm = ConcurrencyManager(global_limit=0, default_mcp_server_limit=1)
        results = []
        lock = threading.Lock()

        def worker(provider: str):
            with cm.acquire(provider):
                with lock:
                    results.append(provider)
                time.sleep(0.02)

        # 10 different providers, each with limit=1: all should run concurrently
        threads = [threading.Thread(target=worker, args=(f"p-{i}",)) for i in range(10)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        elapsed = time.monotonic() - start

        assert len(results) == 10
        # All should run in parallel (~20ms), not serially (~200ms)
        assert elapsed < 0.15, f"Expected ~20ms, got {elapsed * 1000:.0f}ms"


# ---------------------------------------------------------------------------
# The executor's concurrency.acquire span (#1273)
# ---------------------------------------------------------------------------

SERVER = "math"
TOOL = "add"
SLOW_INVOKE_S = 0.1
HOLD_S = 0.05

# A holder of one slot, and which server a second caller asks for to contend
# for it: another server behind a global limit of one, or the same server.
CONTENDED_SLOTS = pytest.mark.parametrize(
    ("global_limit", "server_limit", "other_caller_server"),
    [(1, 0, "other"), (0, 1, SERVER)],
    ids=["global-slot", "server-slot"],
)


class _Upstream:
    """The command bus the executor sends ``InvokeToolCommand`` to.

    ``send`` sets ``invoking``, holds the invoke for ``hold`` seconds (until
    ``release`` is set when ``hold`` is None), then raises ``raises`` if given.
    """

    def __init__(self, *, hold: float | None = 0.0, raises: BaseException | None = None) -> None:
        self.hold = hold
        self.raises = raises
        self.invoking = threading.Event()
        self.release = threading.Event()

    def send(self, command: Any) -> dict[str, Any]:
        self.invoking.set()
        self.release.wait(self.hold)
        if self.raises is not None:
            raise self.raises
        return {"content": [{"type": "text", "text": "ok"}]}


class _Running:
    """One call through ``BatchExecutor._execute_call`` on its own worker thread."""

    def __init__(self, executor: BatchExecutor, call: CallSpec) -> None:
        self.result: CallResult | None = None
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, args=(executor, call), daemon=True)
        self._thread.start()

    def _run(self, executor: BatchExecutor, call: CallSpec) -> None:
        try:
            self.result = executor._execute_call(call, threading.Event(), 60.0, time.perf_counter())
        except BaseException as exc:  # noqa: BLE001 -- handed to the test, which asserts on it
            self.error = exc

    def join(self) -> "_Running":
        self._thread.join(timeout=10)
        assert not self._thread.is_alive(), "the call never finished"
        return self


class _Harness:
    """The executor with a real SDK exporter behind its tracer.

    The gates are emptied: they pass or refuse a call before capacity is asked
    for, and everything asserted here happens after them. The retry store is
    bypassed, so ``max_retries`` alone decides whether the retry span opens.
    ``queued`` is set when a caller of ``ConcurrencyManager.acquire`` found no
    free slot and is about to block, so contention is ordered by an event.
    """

    def __init__(self, exporter: Any, app_ctx: MagicMock, queued: threading.Event) -> None:
        self.exporter = exporter
        self.app_ctx = app_ctx
        self.queued = queued

    def run(self, cm: ConcurrencyManager, upstream: _Upstream, *, max_retries: int = 1) -> _Running:
        self.app_ctx.command_bus.send.side_effect = upstream.send
        call = CallSpec(
            index=0, call_id="call-1273", mcp_server=SERVER, tool=TOOL, arguments={}, max_retries=max_retries
        )
        return _Running(BatchExecutor(concurrency_manager=cm), call)

    def spans(self) -> dict[str, Any]:
        finished = self.exporter.get_finished_spans()
        by_name = {span.name: span for span in finished}
        assert len(by_name) == len(finished), "one call opens each span once"
        return by_name


@pytest.fixture()
def harness():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    queued = threading.Event()
    manager_logger = concurrency.logger

    class _QueueSignal:
        """The manager's logger, setting ``queued`` on its ``*_wait_start`` lines."""

        def __getattr__(self, name: str) -> Any:
            return getattr(manager_logger, name)

        def debug(self, event: str, **kwargs: Any) -> None:
            if event.endswith("_wait_start"):
                queued.set()
            manager_logger.debug(event, **kwargs)

    app_ctx = MagicMock()
    module = "mcp_hangar.server.tools.batch.executor"
    with (
        patch(f"{module}.get_tracer", return_value=provider.get_tracer(module)),
        patch(f"{module}.get_context", return_value=app_ctx),
        patch(f"{module}._GATES", ()),
        patch(f"{module}.configured_retry_policy", return_value=None),
        patch.object(concurrency, "logger", _QueueSignal()),
    ):
        yield _Harness(exporter, app_ctx, queued)
    provider.shutdown()


def _duration_s(span: Any) -> float:
    return (span.end_time - span.start_time) / 1e9


def _slots_free(cm: ConcurrencyManager, queued: threading.Event) -> bool:
    """A fresh caller takes both slots without queuing. On a thread: a leaked
    permit would block it rather than the test."""
    taken = threading.Event()

    def take() -> None:
        with cm.acquire(SERVER):
            taken.set()

    threading.Thread(target=take, daemon=True).start()
    return taken.wait(5) and not queued.is_set()


@pytest.mark.otel_sdk
class TestExecutorWaitSpan:
    """``concurrency.acquire`` spans the wait for capacity, not the call (#1273).

    Its duration used to include the invoke and every retry, because the span
    stayed open for as long as the permit was held. Driven through
    ``BatchExecutor._execute_call`` with a real ``ConcurrencyManager``.
    """

    @pytest.mark.parametrize("max_retries", [1, 2], ids=["single-attempt", "retry-policy"])
    def test_a_slow_invoke_with_free_slots_stays_out_of_the_wait_span(self, harness, max_retries):
        cm = ConcurrencyManager(global_limit=10, default_mcp_server_limit=10)

        run = harness.run(cm, _Upstream(hold=SLOW_INVOKE_S), max_retries=max_retries).join()

        assert run.error is None and run.result is not None and run.result.success
        spans = harness.spans()
        call, acquire = spans[f"batch.call.{TOOL}"], spans["concurrency.acquire"]
        invoke = spans["invoke_with_retry" if max_retries > 1 else "command.send.InvokeToolCommand"]
        assert acquire.end_time <= invoke.start_time, "the wait span must end before the invoke starts"
        assert _duration_s(acquire) < _duration_s(invoke)
        assert acquire.parent.span_id == call.context.span_id
        assert invoke.parent.span_id == call.context.span_id, "the invoke must hang off the call span"
        if max_retries > 1:
            assert spans["command.send.InvokeToolCommand"].parent.span_id == invoke.context.span_id
        assert dict(acquire.attributes) == {"mcp.server.id": SERVER, "concurrency.wait_ms": pytest.approx(0, abs=10)}

    @CONTENDED_SLOTS
    def test_a_contended_slot_is_the_wait_the_span_measures(
        self, harness, global_limit, server_limit, other_caller_server
    ):
        cm = ConcurrencyManager(global_limit=global_limit, default_mcp_server_limit=server_limit)
        holding, done = threading.Event(), threading.Event()

        def holder() -> None:
            with cm.acquire(other_caller_server):
                holding.set()
                done.wait(10)

        threading.Thread(target=holder, daemon=True).start()
        assert holding.wait(5)
        run = harness.run(cm, _Upstream(hold=SLOW_INVOKE_S))
        assert harness.queued.wait(5), "the call must queue behind the holder"
        # The call is blocked on the slot; holding it longer only sets a floor
        # under the wait. The ordering comes from the events above.
        time.sleep(HOLD_S)
        done.set()
        run.join()

        assert run.error is None and run.result is not None and run.result.success
        spans = harness.spans()
        call, acquire = spans[f"batch.call.{TOOL}"], spans["concurrency.acquire"]
        invoke = spans["command.send.InvokeToolCommand"]
        assert acquire.attributes["concurrency.wait_ms"] >= HOLD_S * 1000 - 1
        assert _duration_s(acquire) >= HOLD_S - 0.001
        assert acquire.end_time <= invoke.start_time
        assert acquire.parent.span_id == call.context.span_id
        assert invoke.parent.span_id == call.context.span_id

    @CONTENDED_SLOTS
    def test_the_permit_is_held_until_the_invoke_returns(
        self, harness, global_limit, server_limit, other_caller_server
    ):
        cm = ConcurrencyManager(global_limit=global_limit, default_mcp_server_limit=server_limit)
        upstream = _Upstream(hold=None)
        run = harness.run(cm, upstream)
        assert upstream.invoking.wait(5)
        assert not harness.queued.is_set()
        acquired = threading.Event()

        def other_caller() -> None:
            with cm.acquire(other_caller_server):
                acquired.set()

        threading.Thread(target=other_caller, daemon=True).start()
        assert harness.queued.wait(5), "a second caller must queue while the first is invoking"
        assert not acquired.is_set()
        upstream.release.set()
        run.join()
        assert acquired.wait(5), "the permit must be released once the invoke returns"

    @pytest.mark.parametrize(
        "raises",
        [None, RuntimeError, asyncio.CancelledError],
        ids=["success", "exception", "cancellation"],
    )
    def test_the_permit_is_released_however_the_invoke_ends(self, harness, raises):
        cm = ConcurrencyManager(global_limit=1, default_mcp_server_limit=1)

        run = harness.run(cm, _Upstream(raises=raises("upstream") if raises else None)).join()

        if raises is asyncio.CancelledError:
            # Not an Exception: it escapes the call's fault barrier, as before.
            assert isinstance(run.error, asyncio.CancelledError)
        else:
            assert run.error is None and run.result is not None
            assert run.result.success is (raises is None)
        assert _slots_free(cm, harness.queued), "both slots must be free once the call is over"
        assert harness.spans()["concurrency.acquire"].status.is_ok
