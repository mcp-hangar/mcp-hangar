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
        assert time_left <= 2.0  # 2^1 = 2 seconds max

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
