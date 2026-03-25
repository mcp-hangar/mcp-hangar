"""Tests for behavioral profiling bootstrap and BehavioralProfiler facade.

Covers:
- BehavioralProfiler satisfying IBehavioralProfiler Protocol
- BehavioralProfiler delegation to BaselineStore in LEARNING mode
- BehavioralProfiler no-op behavior in DISABLED and ENFORCING modes
- bootstrap_behavioral() function returns configured profiler
- NullBehavioralProfiler fallback behavior
- Default config values
"""

import time

import pytest

from mcp_hangar.domain.contracts.behavioral import IBehavioralProfiler, NullBehavioralProfiler
from mcp_hangar.domain.value_objects.behavioral import BehavioralMode, NetworkObservation


def _make_observation(provider_id: str = "test-provider") -> NetworkObservation:
    """Create a test NetworkObservation."""
    return NetworkObservation(
        timestamp=time.time(),
        provider_id=provider_id,
        destination_host="api.example.com",
        destination_port=443,
        protocol="https",
        direction="outbound",
    )


class TestBehavioralProfilerProtocol:
    """Test that BehavioralProfiler satisfies IBehavioralProfiler Protocol."""

    def test_behavioral_profiler_satisfies_protocol(self) -> None:
        from enterprise.behavioral.baseline_store import BaselineStore
        from enterprise.behavioral.profiler import BehavioralProfiler

        store = BaselineStore(db_path=":memory:")
        profiler = BehavioralProfiler(baseline_store=store)
        assert isinstance(profiler, IBehavioralProfiler)

    def test_null_profiler_satisfies_protocol(self) -> None:
        null = NullBehavioralProfiler()
        assert isinstance(null, IBehavioralProfiler)


class TestBehavioralProfilerDelegation:
    """Test BehavioralProfiler delegates correctly based on mode."""

    def test_profiler_records_in_learning_mode(self) -> None:
        from enterprise.behavioral.baseline_store import BaselineStore
        from enterprise.behavioral.profiler import BehavioralProfiler

        store = BaselineStore(db_path=":memory:")
        profiler = BehavioralProfiler(baseline_store=store)

        # Set mode to LEARNING
        profiler.set_mode("test-provider", BehavioralMode.LEARNING)

        # Record observation
        obs = _make_observation("test-provider")
        profiler.record_observation(obs)

        # Verify observation was stored
        records = store.get_observations("test-provider")
        assert len(records) == 1
        assert records[0]["host"] == "api.example.com"

    def test_profiler_ignores_in_disabled_mode(self) -> None:
        from enterprise.behavioral.baseline_store import BaselineStore
        from enterprise.behavioral.profiler import BehavioralProfiler

        store = BaselineStore(db_path=":memory:")
        profiler = BehavioralProfiler(baseline_store=store)

        # Default mode is DISABLED
        assert profiler.get_mode("test-provider") == BehavioralMode.DISABLED

        # Record observation
        obs = _make_observation("test-provider")
        profiler.record_observation(obs)

        # Verify nothing stored
        records = store.get_observations("test-provider")
        assert len(records) == 0

    def test_profiler_stores_in_enforcing_mode(self) -> None:
        from enterprise.behavioral.baseline_store import BaselineStore
        from enterprise.behavioral.profiler import BehavioralProfiler

        store = BaselineStore(db_path=":memory:")
        profiler = BehavioralProfiler(baseline_store=store)

        # Set mode to ENFORCING
        profiler.set_mode("test-provider", BehavioralMode.ENFORCING)

        # Record observation
        obs = _make_observation("test-provider")
        profiler.record_observation(obs)

        # ENFORCING mode now stores after deviation check (Phase 44 refactoring)
        records = store.get_observations("test-provider")
        assert len(records) == 1

    def test_profiler_get_mode_delegates_to_store(self) -> None:
        from enterprise.behavioral.baseline_store import BaselineStore
        from enterprise.behavioral.profiler import BehavioralProfiler

        store = BaselineStore(db_path=":memory:")
        profiler = BehavioralProfiler(baseline_store=store)

        # Default is DISABLED
        assert profiler.get_mode("test-provider") == BehavioralMode.DISABLED

        # Set to LEARNING via profiler
        profiler.set_mode("test-provider", BehavioralMode.LEARNING)
        assert profiler.get_mode("test-provider") == BehavioralMode.LEARNING


class TestBootstrapBehavioral:
    """Test bootstrap_behavioral() factory function."""

    def test_bootstrap_behavioral_returns_profiler(self) -> None:
        from enterprise.behavioral.bootstrap import bootstrap_behavioral

        profiler = bootstrap_behavioral(db_path=":memory:")
        assert isinstance(profiler, IBehavioralProfiler)

    def test_behavioral_config_defaults(self) -> None:
        from enterprise.behavioral.bootstrap import bootstrap_behavioral

        # No config passed -- should use defaults
        profiler = bootstrap_behavioral(db_path=":memory:")
        # Default mode for unknown provider is DISABLED
        assert profiler.get_mode("unknown-provider") == BehavioralMode.DISABLED


class TestNullBehavioralProfilerFallback:
    """Test NullBehavioralProfiler used as fallback."""

    def test_fallback_returns_null_profiler(self) -> None:
        null = NullBehavioralProfiler()
        assert null.get_mode("any-provider") == BehavioralMode.DISABLED

    def test_fallback_set_mode_is_noop(self) -> None:
        null = NullBehavioralProfiler()
        # Should not raise
        null.set_mode("any-provider", BehavioralMode.LEARNING)
        # Mode still DISABLED (no-op)
        assert null.get_mode("any-provider") == BehavioralMode.DISABLED

    def test_fallback_record_observation_is_noop(self) -> None:
        null = NullBehavioralProfiler()
        obs = _make_observation()
        # Should not raise
        null.record_observation(obs)


class TestServerBootstrapWiring:
    """Test that server bootstrap wires behavioral profiler into ApplicationContext."""

    def test_application_context_has_behavioral_profiler_field(self) -> None:
        """ApplicationContext dataclass must have behavioral_profiler field."""
        from mcp_hangar.server.bootstrap import ApplicationContext

        # Check that the field exists (dataclass field inspection)
        import dataclasses

        field_names = [f.name for f in dataclasses.fields(ApplicationContext)]
        assert "behavioral_profiler" in field_names

    def test_bootstrap_with_no_behavioral_config(self) -> None:
        """Bootstrap with empty config (no behavioral section) must not break."""
        from mcp_hangar.server.bootstrap import bootstrap

        ctx = bootstrap(config_dict={"providers": {}})
        try:
            # behavioral_profiler should be set (either real or null)
            assert ctx.behavioral_profiler is not None
            assert isinstance(ctx.behavioral_profiler, IBehavioralProfiler)
        finally:
            ctx.shutdown()

    def test_enterprise_behavioral_available_flag(self) -> None:
        """When enterprise.behavioral.bootstrap is importable, flag is True."""
        from mcp_hangar.server.bootstrap import _enterprise_behavioral_available

        # Since enterprise module is available in test environment
        assert _enterprise_behavioral_available is True
