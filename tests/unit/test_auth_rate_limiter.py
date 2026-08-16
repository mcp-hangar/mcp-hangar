"""The auth-facing rate limiter: lockout, decay and the module-level instance."""

from unittest.mock import Mock, patch

from mcp_hangar.auth.infrastructure.rate_limiter import (
    AuthRateLimitConfig,
    AuthRateLimiter,
    get_auth_rate_limiter,
    set_auth_rate_limiter,
)


class TestAuthRateLimitConfig:
    """Tests for AuthRateLimitConfig dataclass."""

    def test_defaults(self):
        config = AuthRateLimitConfig()
        assert config.enabled is True
        assert config.max_attempts == 10
        assert config.window_seconds == 60
        assert config.lockout_seconds == 300
        assert config.lockout_escalation_factor == 2.0
        assert config.max_lockout_seconds == 3600


class TestAuthRateLimiter:
    """Tests for AuthRateLimiter class."""

    def _make_limiter(
        self,
        config: AuthRateLimitConfig | None = None,
        event_publisher=None,
    ) -> AuthRateLimiter:
        return AuthRateLimiter(config=config, event_publisher=event_publisher)

    def test_enabled_property(self):
        """Line 118: enabled property."""
        limiter = self._make_limiter(config=AuthRateLimitConfig(enabled=True))
        assert limiter.enabled is True
        limiter2 = self._make_limiter(config=AuthRateLimitConfig(enabled=False))
        assert limiter2.enabled is False

    def test_check_rate_limit_disabled_always_allows(self):
        """Lines 129-135: disabled rate limiter allows all."""
        limiter = self._make_limiter(config=AuthRateLimitConfig(enabled=False))
        result = limiter.check_rate_limit("1.2.3.4")
        assert result.allowed is True
        assert result.reason == "rate_limiting_disabled"

    def test_check_rate_limit_no_previous_attempts(self):
        """Lines 143-149: IP with no tracker is allowed."""
        limiter = self._make_limiter()
        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            result = limiter.check_rate_limit("1.2.3.4")
        assert result.allowed is True
        assert result.reason == "no_previous_attempts"

    def test_check_rate_limit_locked_out(self):
        """Lines 152-165: IP currently locked returns not allowed with retry_after."""
        config = AuthRateLimitConfig(max_attempts=2, window_seconds=60, lockout_seconds=300)
        limiter = self._make_limiter(config=config)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.record_failure("1.2.3.4")
            # This check triggers the lockout
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False
            assert result.reason == "rate_limit_exceeded"

            # Subsequent check while locked
            mock_time.time.return_value = 1010.0
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False
            assert result.reason == "locked_out"
            assert result.retry_after is not None
            assert result.retry_after > 0

    def test_check_rate_limit_lockout_expired_unlocks(self):
        """Lines 166-177: expired lockout clears locked_until, publishes event."""
        publisher = Mock()
        config = AuthRateLimitConfig(
            max_attempts=2,
            window_seconds=60,
            lockout_seconds=10,
            cleanup_interval=99999,  # Prevent cleanup
        )
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.record_failure("1.2.3.4")
            # Trigger lockout
            limiter.check_rate_limit("1.2.3.4")
            publisher.reset_mock()

            # Time past lockout
            mock_time.time.return_value = 1100.0
            result = limiter.check_rate_limit("1.2.3.4")
            # Should be allowed (lockout expired, old attempts outside window)
            assert result.allowed is True
            # Unlock event should have been published
            publisher.assert_called()

    def test_check_rate_limit_escalation(self):
        """Lines 188-194: lockout escalation with factor."""
        config = AuthRateLimitConfig(
            max_attempts=1,
            window_seconds=60,
            lockout_seconds=10,
            lockout_escalation_factor=2.0,
            max_lockout_seconds=3600,
            cleanup_interval=99999,
        )
        publisher = Mock()
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            # First lockout: 10 seconds
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False
            assert result.retry_after == 10.0  # 10 * 2^0

            # Wait for lockout to expire, record failure again
            mock_time.time.return_value = 1100.0
            limiter.check_rate_limit("1.2.3.4")  # clears lockout
            limiter.record_failure("1.2.3.4")
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False
            assert result.retry_after == 20.0  # 10 * 2^1

    def test_check_rate_limit_escalation_capped(self):
        """Lines 189-193: lockout capped at max_lockout_seconds."""
        config = AuthRateLimitConfig(
            max_attempts=1,
            window_seconds=60,
            lockout_seconds=1000,
            lockout_escalation_factor=10.0,
            max_lockout_seconds=2000,
            cleanup_interval=99999,
        )
        limiter = self._make_limiter(config=config)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False
            # First lockout: min(1000 * 10^0, 2000) = 1000
            assert result.retry_after == 1000.0

            # Expire lockout and trigger second
            mock_time.time.return_value = 3000.0
            limiter.check_rate_limit("1.2.3.4")  # expire lockout
            limiter.record_failure("1.2.3.4")
            result = limiter.check_rate_limit("1.2.3.4")
            # Second lockout: min(1000 * 10^1, 2000) = 2000 (capped)
            assert result.retry_after == 2000.0

    def test_check_rate_limit_within_limit(self):
        """Lines 217-222: attempts within limit returns allowed."""
        config = AuthRateLimitConfig(max_attempts=5, window_seconds=60)
        limiter = self._make_limiter(config=config)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.record_failure("1.2.3.4")
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is True
            assert result.remaining == 3  # 5 - 2
            assert result.reason == "within_limit"

    def test_record_failure_disabled_noop(self):
        """Lines 230-231: disabled limiter does nothing on record_failure."""
        limiter = self._make_limiter(config=AuthRateLimitConfig(enabled=False))
        limiter.record_failure("1.2.3.4")
        assert len(limiter._trackers) == 0

    def test_record_failure_creates_tracker(self):
        """Lines 236-246: record_failure creates tracker and appends timestamp."""
        limiter = self._make_limiter()
        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
        assert "1.2.3.4" in limiter._trackers
        assert len(limiter._trackers["1.2.3.4"].attempts) == 1

    def test_record_success_disabled_noop(self):
        """Lines 254-255: disabled limiter does nothing on record_success."""
        limiter = self._make_limiter(config=AuthRateLimitConfig(enabled=False))
        limiter.record_success("1.2.3.4")

    def test_record_success_clears_tracker(self):
        """Lines 257-269: record_success deletes tracker."""
        limiter = self._make_limiter()
        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
        limiter.record_success("1.2.3.4")
        assert "1.2.3.4" not in limiter._trackers

    def test_record_success_publishes_unlock_event_if_locked(self):
        """Lines 260-267: record_success publishes unlock event for locked tracker."""
        publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=1, window_seconds=60, lockout_seconds=300)
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.check_rate_limit("1.2.3.4")  # Triggers lockout
            publisher.reset_mock()

            limiter.record_success("1.2.3.4")
            # Should publish RateLimitUnlock event
            publisher.assert_called_once()
            event = publisher.call_args[0][0]
            assert event.unlock_reason == "success"

    def test_record_success_no_event_if_not_locked(self):
        """Lines 258-268: no unlock event if tracker exists but not locked."""
        publisher = Mock()
        limiter = self._make_limiter(event_publisher=publisher)
        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
        limiter.record_success("1.2.3.4")
        publisher.assert_not_called()

    def test_get_status_unknown_ip(self):
        """Lines 282-289: get_status for unknown IP returns defaults."""
        limiter = self._make_limiter()
        status = limiter.get_status("1.2.3.4")
        assert status["ip"] == "1.2.3.4"
        assert status["attempts"] == 0
        assert status["remaining"] == 10
        assert status["locked"] is False
        assert status["locked_until"] is None

    def test_get_status_with_attempts(self):
        """Lines 291-301: get_status with recent attempts."""
        config = AuthRateLimitConfig(max_attempts=5, window_seconds=60)
        limiter = self._make_limiter(config=config)
        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.record_failure("1.2.3.4")
            status = limiter.get_status("1.2.3.4")
        assert status["attempts"] == 2
        assert status["remaining"] == 3
        assert status["locked"] is False

    def test_get_status_locked(self):
        """Lines 295-301: get_status when IP is locked."""
        config = AuthRateLimitConfig(max_attempts=1, window_seconds=60, lockout_seconds=300)
        limiter = self._make_limiter(config=config)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.check_rate_limit("1.2.3.4")  # Triggers lockout
            status = limiter.get_status("1.2.3.4")
        assert status["locked"] is True
        assert status["locked_until"] is not None

    def test_clear_all(self):
        """Lines 309-322: clear(None) clears all trackers and publishes unlock events."""
        publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=1, window_seconds=60, lockout_seconds=300)
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.check_rate_limit("1.2.3.4")  # Lock out
            limiter.record_failure("5.6.7.8")
            publisher.reset_mock()

        limiter.clear()
        assert len(limiter._trackers) == 0
        # Should publish unlock event for the locked IP
        publisher.assert_called_once()

    def test_clear_specific_ip(self):
        """Lines 323-334: clear(ip) clears specific IP."""
        publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=1, window_seconds=60, lockout_seconds=300)
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.check_rate_limit("1.2.3.4")  # Lock out
            limiter.record_failure("5.6.7.8")
            publisher.reset_mock()

        limiter.clear("1.2.3.4")
        assert "1.2.3.4" not in limiter._trackers
        assert "5.6.7.8" in limiter._trackers
        # Unlock event for locked IP
        publisher.assert_called_once()

    def test_clear_specific_ip_not_locked_no_event(self):
        """Lines 323-334: clear specific IP that is not locked does not publish event."""
        publisher = Mock()
        limiter = self._make_limiter(event_publisher=publisher)
        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
        limiter.clear("1.2.3.4")
        publisher.assert_not_called()

    def test_clear_nonexistent_ip(self):
        limiter = self._make_limiter()
        # Should not raise
        limiter.clear("nonexistent")

    def test_maybe_cleanup_skips_when_interval_not_reached(self):
        """Lines 341-343: _maybe_cleanup only runs when interval exceeded."""
        config = AuthRateLimitConfig(cleanup_interval=600)
        limiter = self._make_limiter(config=config)
        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            # check_rate_limit calls _maybe_cleanup
            mock_time.time.return_value = 1100.0  # 100s < 600s interval
            limiter.check_rate_limit("1.2.3.4")
        # Tracker should still exist (no cleanup)
        assert "1.2.3.4" in limiter._trackers

    def test_do_cleanup_removes_stale_trackers(self):
        """Lines 351-380: _do_cleanup removes old, unlocked trackers."""
        publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=5, window_seconds=60, cleanup_interval=10)
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("old-ip")

            # Fast forward past window
            mock_time.time.return_value = 2000.0
            removed = limiter.force_cleanup()
        assert removed == 1
        assert "old-ip" not in limiter._trackers

    def test_do_cleanup_keeps_locked_trackers(self):
        """Lines 358-359: cleanup keeps locked IPs."""
        config = AuthRateLimitConfig(
            max_attempts=1,
            window_seconds=60,
            lockout_seconds=3600,
            cleanup_interval=10,
        )
        limiter = self._make_limiter(config=config)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("locked-ip")
            limiter.check_rate_limit("locked-ip")  # Triggers lockout

            mock_time.time.return_value = 1500.0  # Lockout not expired
            removed = limiter.force_cleanup()
        assert removed == 0
        assert "locked-ip" in limiter._trackers

    def test_do_cleanup_removes_expired_lockout_publishes_unlock(self):
        """Lines 364-371: cleanup removes expired lockout and publishes event."""
        publisher = Mock()
        config = AuthRateLimitConfig(
            max_attempts=1,
            window_seconds=60,
            lockout_seconds=10,
            cleanup_interval=10,
        )
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("expired-lockout-ip")
            limiter.check_rate_limit("expired-lockout-ip")
            publisher.reset_mock()

            # Past both window and lockout
            mock_time.time.return_value = 2000.0
            removed = limiter.force_cleanup()
        assert removed == 1
        # Should publish cleanup unlock event
        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        assert event.unlock_reason == "cleanup"

    def test_do_cleanup_keeps_tracker_with_recent_attempts(self):
        """Lines 361-362: cleanup keeps trackers with recent attempts."""
        config = AuthRateLimitConfig(max_attempts=5, window_seconds=60, cleanup_interval=10)
        limiter = self._make_limiter(config=config)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("recent-ip")

            mock_time.time.return_value = 1030.0  # Within window
            removed = limiter.force_cleanup()
        assert removed == 0
        assert "recent-ip" in limiter._trackers

    def test_force_cleanup_returns_count(self):
        """Lines 388-390: force_cleanup returns removed count."""
        config = AuthRateLimitConfig(window_seconds=60, cleanup_interval=10)
        limiter = self._make_limiter(config=config)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("ip1")
            limiter.record_failure("ip2")

            mock_time.time.return_value = 2000.0
            removed = limiter.force_cleanup()
        assert removed == 2

    def test_publish_event_swallows_exception(self):
        """Lines 109-113: _publish_event logs but does not raise."""
        publisher = Mock(side_effect=RuntimeError("event fail"))
        config = AuthRateLimitConfig(max_attempts=1, window_seconds=60, lockout_seconds=10)
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("mcp_hangar.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            # This should not raise despite publisher failure
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False

    def test_publish_event_noop_when_no_publisher(self):
        """Lines 109: _publish_event does nothing when publisher is None."""
        limiter = self._make_limiter(event_publisher=None)
        limiter._publish_event(object())  # Should not raise


class TestModuleLevelRateLimiter:
    """Tests for get_auth_rate_limiter and set_auth_rate_limiter."""

    def teardown_method(self):
        """Reset global state between tests."""
        import mcp_hangar.auth.infrastructure.rate_limiter as rl_module

        rl_module._default_limiter = None

    def test_get_auth_rate_limiter_creates_default(self):
        """Lines 404-406: get_auth_rate_limiter creates default when None."""
        import mcp_hangar.auth.infrastructure.rate_limiter as rl_module

        rl_module._default_limiter = None
        limiter = get_auth_rate_limiter()
        assert limiter is not None
        assert isinstance(limiter, AuthRateLimiter)

    def test_get_auth_rate_limiter_returns_same_instance(self):
        """get_auth_rate_limiter is idempotent."""
        import mcp_hangar.auth.infrastructure.rate_limiter as rl_module

        rl_module._default_limiter = None
        limiter1 = get_auth_rate_limiter()
        limiter2 = get_auth_rate_limiter()
        assert limiter1 is limiter2

    def test_set_auth_rate_limiter(self):
        """Line 416: set_auth_rate_limiter overrides global."""
        custom = AuthRateLimiter(config=AuthRateLimitConfig(max_attempts=42))
        set_auth_rate_limiter(custom)
        assert get_auth_rate_limiter() is custom
