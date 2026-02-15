"""Comprehensive test suite for AuthRateLimiter.

Tests cover:
- Config validation
- Happy path (allowed requests)
- Lockout triggering and expiry
- Exponential backoff on repeated lockouts
- Per-IP isolation
- Window-based attempt expiry
- Status reporting
- Clear operations
- Domain event emission
"""

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.domain.events import RateLimitLockout, RateLimitUnlock
from mcp_hangar.infrastructure.auth.rate_limiter import (
    AuthRateLimiter,
    AuthRateLimitConfig,
)


class TestAuthRateLimiterConfig:
    """Tests for AuthRateLimitConfig validation."""

    def test_default_config_values(self):
        """Verify default config values."""
        config = AuthRateLimitConfig()

        assert config.enabled is True
        assert config.max_attempts == 10
        assert config.window_seconds == 60
        assert config.lockout_seconds == 300
        assert config.cleanup_interval == 300

    def test_custom_config(self):
        """Verify all fields can be overridden."""
        config = AuthRateLimitConfig(
            enabled=False,
            max_attempts=5,
            window_seconds=120,
            lockout_seconds=600,
            cleanup_interval=180,
        )

        assert config.enabled is False
        assert config.max_attempts == 5
        assert config.window_seconds == 120
        assert config.lockout_seconds == 600
        assert config.cleanup_interval == 180


class TestAuthRateLimiterHappyPath:
    """Tests for normal allowed request flow."""

    @patch("time.time")
    def test_first_request_from_ip_is_allowed(self, mock_time):
        """New IP returns allowed=True, remaining=max_attempts, reason=no_previous_attempts."""
        mock_time.return_value = 1000.0
        limiter = AuthRateLimiter()

        result = limiter.check_rate_limit("192.168.1.100")

        assert result.allowed is True
        assert result.remaining == 10
        assert result.retry_after is None
        assert result.reason == "no_previous_attempts"

    @patch("time.time")
    def test_requests_within_limit_allowed(self, mock_time):
        """Record (max_attempts - 1) failures, check_rate_limit still returns allowed=True with remaining=1."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(max_attempts=10)
        limiter = AuthRateLimiter(config)

        # Record 9 failures (max_attempts - 1)
        for i in range(9):
            limiter.record_failure("192.168.1.100")

        result = limiter.check_rate_limit("192.168.1.100")

        assert result.allowed is True
        assert result.remaining == 1
        assert result.retry_after is None
        assert result.reason == "within_limit"

    @patch("time.time")
    def test_success_clears_tracker(self, mock_time):
        """Record failures, call record_success, verify IP is clean (remaining resets to max_attempts)."""
        mock_time.return_value = 1000.0
        limiter = AuthRateLimiter()

        # Record some failures
        for i in range(5):
            limiter.record_failure("192.168.1.100")

        # Successful auth clears tracker
        limiter.record_success("192.168.1.100")

        # Check - should be clean
        result = limiter.check_rate_limit("192.168.1.100")

        assert result.allowed is True
        assert result.remaining == 10
        assert result.reason == "no_previous_attempts"

    @patch("time.time")
    def test_disabled_limiter_always_allows(self, mock_time):
        """Config with enabled=False, record many failures, check_rate_limit always returns allowed=True."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(enabled=False)
        limiter = AuthRateLimiter(config)

        # Record many failures
        for i in range(20):
            limiter.record_failure("192.168.1.100")

        result = limiter.check_rate_limit("192.168.1.100")

        assert result.allowed is True
        assert result.remaining == 10  # Still reports max_attempts
        assert result.retry_after is None
        assert result.reason == "rate_limiting_disabled"


class TestAuthRateLimiterLockout:
    """Tests for lockout triggering and expiry."""

    @patch("time.time")
    def test_lockout_triggered_at_max_attempts(self, mock_time):
        """Record max_attempts failures, check_rate_limit returns allowed=False with reason=rate_limit_exceeded."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(max_attempts=10, lockout_seconds=300)
        limiter = AuthRateLimiter(config)

        # Record max_attempts failures
        for i in range(10):
            limiter.record_failure("192.168.1.100")

        result = limiter.check_rate_limit("192.168.1.100")

        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after == 300
        assert result.reason == "rate_limit_exceeded"

    @patch("time.time")
    def test_locked_ip_stays_locked_until_expiry(self, mock_time):
        """Trigger lockout, advance time by half lockout, verify still locked with decreased retry_after."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(max_attempts=5, lockout_seconds=300)
        limiter = AuthRateLimiter(config)

        # Trigger lockout
        for i in range(5):
            limiter.record_failure("192.168.1.100")
        limiter.check_rate_limit("192.168.1.100")  # Triggers lockout

        # Advance time by half lockout period (150 seconds)
        mock_time.return_value = 1150.0

        result = limiter.check_rate_limit("192.168.1.100")

        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after == pytest.approx(150.0, rel=0.01)
        assert result.reason == "locked_out"

    @patch("time.time")
    def test_lockout_expires_and_allows_retry(self, mock_time):
        """Trigger lockout, advance time past lockout_seconds, verify check returns allowed=True (attempts cleared)."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(max_attempts=5, lockout_seconds=300, cleanup_interval=600)
        limiter = AuthRateLimiter(config)

        # Trigger lockout
        for i in range(5):
            limiter.record_failure("192.168.1.100")
        limiter.check_rate_limit("192.168.1.100")

        # Advance time past lockout period (but not cleanup interval)
        mock_time.return_value = 1301.0

        result = limiter.check_rate_limit("192.168.1.100")

        assert result.allowed is True
        assert result.remaining == 5  # Attempts cleared, max_attempts = 5 in config
        assert result.retry_after is None
        assert result.reason == "within_limit"

    @patch("time.time")
    def test_exponential_backoff_on_repeated_lockouts(self, mock_time):
        """Trigger lockout, let it expire, trigger lockout again.

        Second lockout should have lockout_seconds * escalation_factor duration.
        """
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(
            max_attempts=3,
            lockout_seconds=60,
            max_lockout_seconds=3600,
            lockout_escalation_factor=2.0,
        )
        limiter = AuthRateLimiter(config)

        # First lockout (60s)
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        result = limiter.check_rate_limit("192.168.1.100")
        assert result.allowed is False
        assert result.retry_after == 60  # Base lockout

        # Let lockout expire
        mock_time.return_value = 1061.0

        # Second lockout (120s = 60 * 2^1)
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        result = limiter.check_rate_limit("192.168.1.100")
        assert result.allowed is False
        assert result.retry_after == 120  # Doubled

        # Let lockout expire
        mock_time.return_value = 1181.0

        # Third lockout (240s = 60 * 2^2)
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        result = limiter.check_rate_limit("192.168.1.100")
        assert result.allowed is False
        assert result.retry_after == 240  # Doubled again

    @patch("time.time")
    def test_exponential_backoff_caps_at_max_lockout(self, mock_time):
        """Trigger enough lockouts to exceed max_lockout_seconds. Verify lockout duration is capped."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(
            max_attempts=2,
            lockout_seconds=100,
            max_lockout_seconds=500,  # Cap at 500s
            lockout_escalation_factor=2.0,
        )
        limiter = AuthRateLimiter(config)

        expected_lockouts = [100, 200, 400, 500, 500]  # 800 would exceed cap

        for idx, expected_lockout in enumerate(expected_lockouts):
            # Trigger lockout
            for i in range(2):
                limiter.record_failure("192.168.1.100")
            result = limiter.check_rate_limit("192.168.1.100")

            assert result.allowed is False
            assert result.retry_after == expected_lockout, f"Lockout {idx + 1} should be {expected_lockout}s"

            # Let lockout expire
            mock_time.return_value += expected_lockout + 1


class TestAuthRateLimiterPerIpIsolation:
    """Tests for per-IP isolation."""

    @patch("time.time")
    def test_different_ips_tracked_independently(self, mock_time):
        """Lock out IP-A, verify IP-B is still allowed."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(max_attempts=3)
        limiter = AuthRateLimiter(config)

        # Lock out IP-A
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        result_a = limiter.check_rate_limit("192.168.1.100")
        assert result_a.allowed is False

        # IP-B is still allowed
        result_b = limiter.check_rate_limit("192.168.1.200")
        assert result_b.allowed is True
        assert result_b.remaining == 3

    @patch("time.time")
    def test_success_on_one_ip_does_not_affect_another(self, mock_time):
        """Record failures on both IPs, success on IP-A, verify IP-B still has failures."""
        mock_time.return_value = 1000.0
        limiter = AuthRateLimiter()

        # Record failures on both IPs
        for i in range(5):
            limiter.record_failure("192.168.1.100")
            limiter.record_failure("192.168.1.200")

        # Success on IP-A
        limiter.record_success("192.168.1.100")

        # IP-A should be clean
        result_a = limiter.check_rate_limit("192.168.1.100")
        assert result_a.remaining == 10

        # IP-B should still have failures
        result_b = limiter.check_rate_limit("192.168.1.200")
        assert result_b.remaining == 5


class TestAuthRateLimiterWindowExpiry:
    """Tests for window-based attempt expiry."""

    @patch("time.time")
    def test_old_attempts_pruned_from_window(self, mock_time):
        """Record failures, advance time past window_seconds, verify remaining resets (old attempts no longer count)."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(window_seconds=60, max_attempts=10)
        limiter = AuthRateLimiter(config)

        # Record 7 failures
        for i in range(7):
            limiter.record_failure("192.168.1.100")

        result = limiter.check_rate_limit("192.168.1.100")
        assert result.remaining == 3

        # Advance time past window
        mock_time.return_value = 1061.0

        result = limiter.check_rate_limit("192.168.1.100")
        assert result.remaining == 10  # Old attempts no longer count

    @patch("time.time")
    def test_attempts_at_window_boundary(self, mock_time):
        """Record attempts across window boundary, verify only in-window attempts are counted."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(window_seconds=60, max_attempts=10)
        limiter = AuthRateLimiter(config)

        # Record 3 failures at t=1000
        for i in range(3):
            limiter.record_failure("192.168.1.100")

        # Advance to t=1030 (30s later, still in window)
        mock_time.return_value = 1030.0
        for i in range(4):
            limiter.record_failure("192.168.1.100")

        # Check at t=1065 (65s from start, first 3 should be expired)
        mock_time.return_value = 1065.0
        result = limiter.check_rate_limit("192.168.1.100")

        # Only the 4 attempts from t=1030 should count
        assert result.remaining == 6


class TestAuthRateLimiterGetStatus:
    """Tests for get_status method."""

    @patch("time.time")
    def test_status_for_unknown_ip(self, mock_time):
        """Returns attempts=0, remaining=max_attempts, locked=False."""
        mock_time.return_value = 1000.0
        limiter = AuthRateLimiter()

        status = limiter.get_status("192.168.1.100")

        assert status["ip"] == "192.168.1.100"
        assert status["attempts"] == 0
        assert status["remaining"] == 10
        assert status["locked"] is False
        assert status["locked_until"] is None

    @patch("time.time")
    def test_status_for_locked_ip(self, mock_time):
        """Trigger lockout, verify locked=True, locked_until is set."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(max_attempts=3, lockout_seconds=300)
        limiter = AuthRateLimiter(config)

        # Trigger lockout
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        limiter.check_rate_limit("192.168.1.100")

        status = limiter.get_status("192.168.1.100")

        assert status["locked"] is True
        assert status["locked_until"] == 1300.0
        assert status["remaining"] == 0


class TestAuthRateLimiterClear:
    """Tests for clear method."""

    @patch("time.time")
    def test_clear_specific_ip(self, mock_time):
        """Add trackers for 2 IPs, clear one, verify other remains."""
        mock_time.return_value = 1000.0
        limiter = AuthRateLimiter()

        # Add failures for 2 IPs
        for i in range(5):
            limiter.record_failure("192.168.1.100")
            limiter.record_failure("192.168.1.200")

        # Clear IP-A
        limiter.clear("192.168.1.100")

        # IP-A should be clean
        result_a = limiter.check_rate_limit("192.168.1.100")
        assert result_a.remaining == 10

        # IP-B should still have failures
        result_b = limiter.check_rate_limit("192.168.1.200")
        assert result_b.remaining == 5

    @patch("time.time")
    def test_clear_all(self, mock_time):
        """Add trackers, clear(None), verify all gone."""
        mock_time.return_value = 1000.0
        limiter = AuthRateLimiter()

        # Add failures for multiple IPs
        for ip in ["192.168.1.100", "192.168.1.200", "192.168.1.300"]:
            for i in range(5):
                limiter.record_failure(ip)

        # Clear all
        limiter.clear(None)

        # All IPs should be clean
        for ip in ["192.168.1.100", "192.168.1.200", "192.168.1.300"]:
            result = limiter.check_rate_limit(ip)
            assert result.remaining == 10


class TestAuthRateLimiterDomainEvents:
    """Tests for domain event emission."""

    @patch("time.time")
    def test_lockout_emits_rate_limit_lockout_event(self, mock_time):
        """Trigger lockout, verify event_publisher called with RateLimitLockout event."""
        mock_time.return_value = 1000.0
        event_publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=3, lockout_seconds=300)
        limiter = AuthRateLimiter(config, event_publisher=event_publisher)

        # Trigger lockout
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        limiter.check_rate_limit("192.168.1.100")

        # Verify event was published
        assert event_publisher.call_count == 1
        event = event_publisher.call_args[0][0]
        assert isinstance(event, RateLimitLockout)
        assert event.source_ip == "192.168.1.100"
        assert event.lockout_duration_seconds == 300
        assert event.lockout_count == 1
        assert event.failed_attempts == 3

    @patch("time.time")
    def test_lockout_expiry_emits_unlock_event(self, mock_time):
        """Trigger lockout, advance time past expiry, call check_rate_limit, verify RateLimitUnlock emitted."""
        mock_time.return_value = 1000.0
        event_publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=3, lockout_seconds=300, cleanup_interval=600)
        limiter = AuthRateLimiter(config, event_publisher=event_publisher)

        # Trigger lockout
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        limiter.check_rate_limit("192.168.1.100")

        # Reset mock to ignore lockout event
        event_publisher.reset_mock()

        # Advance time past lockout expiry (but not cleanup interval)
        mock_time.return_value = 1301.0
        limiter.check_rate_limit("192.168.1.100")

        # Verify unlock event was published
        assert event_publisher.call_count == 1
        event = event_publisher.call_args[0][0]
        assert isinstance(event, RateLimitUnlock)
        assert event.source_ip == "192.168.1.100"
        assert event.lockout_count == 1
        assert event.unlock_reason == "expired"

    @patch("time.time")
    def test_success_emits_unlock_event_if_locked(self, mock_time):
        """Trigger lockout, call record_success, verify RateLimitUnlock emitted with unlock_reason=success."""
        mock_time.return_value = 1000.0
        event_publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=3, lockout_seconds=300)
        limiter = AuthRateLimiter(config, event_publisher=event_publisher)

        # Trigger lockout
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        limiter.check_rate_limit("192.168.1.100")

        # Reset mock to ignore lockout event
        event_publisher.reset_mock()

        # Successful auth
        limiter.record_success("192.168.1.100")

        # Verify unlock event was published
        assert event_publisher.call_count == 1
        event = event_publisher.call_args[0][0]
        assert isinstance(event, RateLimitUnlock)
        assert event.source_ip == "192.168.1.100"
        assert event.lockout_count == 1
        assert event.unlock_reason == "success"

    @patch("time.time")
    def test_success_does_not_emit_unlock_if_not_locked(self, mock_time):
        """Record some failures (no lockout), call record_success, verify NO RateLimitUnlock emitted."""
        mock_time.return_value = 1000.0
        event_publisher = Mock()
        limiter = AuthRateLimiter(event_publisher=event_publisher)

        # Record some failures but not enough to trigger lockout
        for i in range(5):
            limiter.record_failure("192.168.1.100")

        # Successful auth
        limiter.record_success("192.168.1.100")

        # Verify NO unlock event was published (IP was not locked)
        assert event_publisher.call_count == 0

    @patch("time.time")
    def test_no_event_publisher_does_not_raise(self, mock_time):
        """Create AuthRateLimiter without event_publisher, trigger lockout - should not raise."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(max_attempts=3, lockout_seconds=300)
        limiter = AuthRateLimiter(config)  # No event_publisher

        # Trigger lockout - should not raise
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        result = limiter.check_rate_limit("192.168.1.100")

        # Verify lockout occurred
        assert result.allowed is False
        assert result.reason == "rate_limit_exceeded"


class TestAuthRateLimiterCleanup:
    """Tests for cleanup edge cases."""

    @patch("time.time")
    def test_cleanup_runs_after_interval(self, mock_time):
        """Set cleanup_interval=1, record failure, advance time by 2s, verify tracker was removed."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(cleanup_interval=1, window_seconds=60)
        limiter = AuthRateLimiter(config)

        # Record one failure at t=1000
        limiter.record_failure("192.168.1.100")

        # Advance time past window + cleanup interval
        mock_time.return_value = 1062.0

        # Trigger cleanup via check_rate_limit
        result = limiter.check_rate_limit("192.168.1.100")

        # Tracker should be removed (no recent attempts in 60s window)
        assert result.remaining == 10
        assert result.reason == "no_previous_attempts"

    @patch("time.time")
    def test_cleanup_preserves_active_lockouts(self, mock_time):
        """Trigger lockout, force_cleanup before lockout expires, verify tracker is still present."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(max_attempts=3, lockout_seconds=300)
        limiter = AuthRateLimiter(config)

        # Trigger lockout
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        limiter.check_rate_limit("192.168.1.100")

        # Advance time but not past lockout expiry
        mock_time.return_value = 1100.0

        # Force cleanup
        removed_count = limiter.force_cleanup()

        # Tracker should NOT be removed (lockout still active)
        assert removed_count == 0
        status = limiter.get_status("192.168.1.100")
        assert status["locked"] is True

    @patch("time.time")
    def test_cleanup_removes_expired_lockouts_with_no_activity(self, mock_time):
        """Trigger lockout, advance time past lockout + window, force_cleanup, verify tracker removed."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(max_attempts=3, lockout_seconds=300, window_seconds=60)
        limiter = AuthRateLimiter(config)

        # Trigger lockout at t=1000
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        limiter.check_rate_limit("192.168.1.100")

        # Advance time past lockout expiry + window (300s lockout + 60s window)
        mock_time.return_value = 1400.0

        # Force cleanup
        removed_count = limiter.force_cleanup()

        # Tracker should be removed (lockout expired, no recent attempts)
        assert removed_count == 1
        status = limiter.get_status("192.168.1.100")
        assert status["attempts"] == 0
        assert status["locked"] is False

    @patch("time.time")
    def test_cleanup_emits_unlock_for_expired_lockouts(self, mock_time):
        """Trigger lockout, advance time past lockout, force_cleanup, verify unlock event emitted."""
        mock_time.return_value = 1000.0
        event_publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=3, lockout_seconds=300, window_seconds=60)
        limiter = AuthRateLimiter(config, event_publisher=event_publisher)

        # Trigger lockout
        for i in range(3):
            limiter.record_failure("192.168.1.100")
        limiter.check_rate_limit("192.168.1.100")

        # Reset mock to ignore lockout event
        event_publisher.reset_mock()

        # Advance time past lockout + window
        mock_time.return_value = 1400.0

        # Force cleanup
        limiter.force_cleanup()

        # Verify unlock event was published
        assert event_publisher.call_count == 1
        event = event_publisher.call_args[0][0]
        assert isinstance(event, RateLimitUnlock)
        assert event.source_ip == "192.168.1.100"
        assert event.lockout_count == 1
        assert event.unlock_reason == "cleanup"

    @patch("time.time")
    def test_force_cleanup_returns_removed_count(self, mock_time):
        """Add multiple stale trackers, call force_cleanup, verify returns correct count."""
        mock_time.return_value = 1000.0
        config = AuthRateLimitConfig(window_seconds=60)
        limiter = AuthRateLimiter(config)

        # Add failures for 3 IPs at t=1000
        for ip in ["192.168.1.100", "192.168.1.200", "192.168.1.300"]:
            for i in range(3):
                limiter.record_failure(ip)

        # Advance time past window
        mock_time.return_value = 1100.0

        # Force cleanup
        removed_count = limiter.force_cleanup()

        # All 3 trackers should be removed
        assert removed_count == 3

        # Verify all trackers are gone
        for ip in ["192.168.1.100", "192.168.1.200", "192.168.1.300"]:
            status = limiter.get_status(ip)
            assert status["attempts"] == 0
