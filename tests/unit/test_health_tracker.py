"""Tests for HealthTracker entity."""

import time

from mcp_hangar.domain.model.health_tracker import HealthTracker


class TestHealthTracker:
    """Test suite for HealthTracker."""

    def test_initial_state(self):
        """Test initial state of health tracker."""
        tracker = HealthTracker()

        assert tracker.consecutive_failures == 0
        assert tracker.total_invocations == 0
        assert tracker.total_failures == 0
        assert tracker.last_success_at is None
        assert tracker.last_failure_at is None
        assert tracker.success_rate == 1.0
        assert tracker.can_retry() is True
        assert tracker.should_degrade() is False

    def test_record_success(self):
        """Test recording a successful operation."""
        tracker = HealthTracker()

        tracker.record_success()

        assert tracker.consecutive_failures == 0
        assert tracker.total_invocations == 1
        assert tracker.total_failures == 0
        assert tracker.last_success_at is not None
        assert tracker.success_rate == 1.0

    def test_record_failure(self):
        """Test recording a failed operation."""
        tracker = HealthTracker()

        tracker.record_failure()

        assert tracker.consecutive_failures == 1
        assert tracker.total_invocations == 1
        assert tracker.total_failures == 1
        assert tracker.last_failure_at is not None
        assert tracker.success_rate == 0.0

    def test_consecutive_failures_reset_on_success(self):
        """Test that consecutive failures reset on success."""
        tracker = HealthTracker()

        tracker.record_failure()
        tracker.record_failure()
        assert tracker.consecutive_failures == 2

        tracker.record_success()
        assert tracker.consecutive_failures == 0
        assert tracker.total_failures == 2  # Total not reset

    def test_should_degrade_threshold(self):
        """Test degradation threshold detection."""
        tracker = HealthTracker(max_consecutive_failures=3)

        tracker.record_failure()
        assert tracker.should_degrade() is False

        tracker.record_failure()
        assert tracker.should_degrade() is False

        tracker.record_failure()
        assert tracker.should_degrade() is True

    def test_can_retry_backoff(self):
        """Test exponential backoff for retry logic."""
        tracker = HealthTracker()

        # First failure - backoff is 2^1 = 2 seconds
        tracker.record_failure()
        assert tracker.can_retry() is False

        # Wait for backoff
        time.sleep(0.1)
        assert tracker.can_retry() is False  # Still within 2 second backoff

    def test_time_until_retry_no_failure(self):
        """Test time until retry with no failure."""
        tracker = HealthTracker()
        assert tracker.time_until_retry() == 0.0

    def test_time_until_retry_after_failure(self):
        """Test time until retry after failure."""
        tracker = HealthTracker()
        tracker.record_failure()

        time_left = tracker.time_until_retry()
        assert time_left > 0
        assert time_left <= 2.2  # 2^1 = 2s base, +10% jitter tolerance

    def test_record_invocation_failure(self):
        """Test recording invocation failure (non-consecutive)."""
        tracker = HealthTracker()

        tracker.record_invocation_failure()

        assert tracker.consecutive_failures == 0  # Not incremented
        assert tracker.total_failures == 1
        assert tracker.total_invocations == 1

    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        tracker = HealthTracker()

        tracker.record_success()
        tracker.record_success()
        tracker.record_failure()
        tracker.record_success()

        # 3 successes out of 4 = 75%
        assert tracker.success_rate == 0.75

    def test_reset(self):
        """Test resetting health tracker."""
        tracker = HealthTracker()

        tracker.record_failure()
        tracker.record_failure()
        tracker.record_success()

        tracker.reset()

        assert tracker.consecutive_failures == 0
        assert tracker.total_invocations == 0
        assert tracker.total_failures == 0
        assert tracker.last_success_at is None
        assert tracker.last_failure_at is None

    def test_to_dict(self):
        """Test dictionary representation."""
        tracker = HealthTracker()
        tracker.record_success()

        result = tracker.to_dict()

        assert "consecutive_failures" in result
        assert "last_success_at" in result
        assert "last_failure_at" in result
        assert "total_invocations" in result
        assert "total_failures" in result
        assert "success_rate" in result
        assert "can_retry" in result
        assert "time_until_retry" in result

    def test_custom_max_consecutive_failures(self):
        """Test custom max consecutive failures threshold."""
        tracker = HealthTracker(max_consecutive_failures=5)

        for _ in range(4):
            tracker.record_failure()

        assert tracker.should_degrade() is False

        tracker.record_failure()
        assert tracker.should_degrade() is True


class TestHealthTrackerJitter:
    """Tests for jitter in HealthTracker backoff."""

    def test_calculate_backoff_within_jitter_range(self):
        """_calculate_backoff() returns value within jitter range of base backoff."""
        tracker = HealthTracker(jitter_factor=0.1)
        tracker.record_failure()  # consecutive_failures = 1, base = 2^1 = 2.0

        results = [tracker._calculate_backoff() for _ in range(100)]

        for r in results:
            assert 2.0 * 0.9 <= r <= 2.0 * 1.1, f"Backoff {r} outside jitter range"

    def test_calculate_backoff_randomness(self):
        """Two consecutive calls may return different values (randomness)."""
        tracker = HealthTracker(jitter_factor=0.1)
        tracker.record_failure()
        tracker.record_failure()  # consecutive_failures = 2, base = 4.0

        results = {tracker._calculate_backoff() for _ in range(50)}
        # With randomness, we expect more than 1 distinct value over 50 calls
        assert len(results) > 1, "Expected different values from jittered backoff"

    def test_calculate_backoff_zero_failures(self):
        """Backoff with 0 failures returns value near 1.0 (2^0 = 1)."""
        tracker = HealthTracker(jitter_factor=0.1)
        # 0 consecutive failures, base = 2^0 = 1.0

        results = [tracker._calculate_backoff() for _ in range(100)]
        for r in results:
            assert 0.9 <= r <= 1.1, f"Backoff {r} not near 1.0"

    def test_calculate_backoff_respects_max_ceiling(self):
        """Backoff respects max ceiling of 60.0 even with jitter."""
        tracker = HealthTracker(jitter_factor=0.1)
        # 10 failures -> base = 2^10 = 1024, capped at 60
        for _ in range(10):
            tracker.record_failure()

        results = [tracker._calculate_backoff() for _ in range(100)]
        for r in results:
            assert r <= 60.0, f"Backoff {r} exceeds 60.0 ceiling"

    def test_calculate_backoff_deterministic_with_zero_jitter(self):
        """jitter_factor=0.0 produces exact deterministic backoff."""
        tracker = HealthTracker(jitter_factor=0.0)
        tracker.record_failure()  # base = 2^1 = 2.0

        results = [tracker._calculate_backoff() for _ in range(10)]
        assert all(r == 2.0 for r in results), f"Expected all 2.0, got {results}"

    def test_can_retry_works_with_jittered_backoff(self):
        """can_retry() works correctly with jittered backoff."""
        tracker = HealthTracker(jitter_factor=0.0)
        tracker.record_failure()

        # Immediately after failure, cannot retry (backoff = 2^1 = 2.0s)
        assert tracker.can_retry() is False

    def test_time_until_retry_works_with_jittered_backoff(self):
        """time_until_retry() works correctly with jittered backoff."""
        tracker = HealthTracker(jitter_factor=0.0)
        tracker.record_failure()

        remaining = tracker.time_until_retry()
        assert remaining > 0.0
        assert remaining <= 2.0  # base = 2^1 = 2.0

    def test_get_health_check_interval_cold(self):
        """get_health_check_interval() returns 0.0 for COLD (skip)."""
        tracker = HealthTracker()
        assert tracker.get_health_check_interval("cold") == 0.0

    def test_get_health_check_interval_initializing(self):
        """get_health_check_interval() returns 0.0 for INITIALIZING (skip)."""
        tracker = HealthTracker()
        assert tracker.get_health_check_interval("initializing") == 0.0

    def test_get_health_check_interval_ready(self):
        """get_health_check_interval() returns normal_interval for READY."""
        tracker = HealthTracker()
        assert tracker.get_health_check_interval("ready", normal_interval=10.0) == 10.0
        assert tracker.get_health_check_interval("ready", normal_interval=5.0) == 5.0

    def test_get_health_check_interval_degraded(self):
        """get_health_check_interval() returns backoff value for DEGRADED."""
        tracker = HealthTracker(jitter_factor=0.0)
        tracker.record_failure()
        tracker.record_failure()  # consecutive_failures = 2, base = 4.0

        interval = tracker.get_health_check_interval("degraded")
        assert interval == 4.0

    def test_get_health_check_interval_dead(self):
        """get_health_check_interval() returns max ceiling (60.0) for DEAD."""
        tracker = HealthTracker()
        assert tracker.get_health_check_interval("dead") == 60.0
