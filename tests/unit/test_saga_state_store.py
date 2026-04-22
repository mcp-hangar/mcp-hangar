"""Tests for SagaStateStore persistence and saga serialization."""

import json
from unittest.mock import MagicMock

from mcp_hangar.domain.model.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from mcp_hangar.infrastructure.persistence.database_common import (
    MigrationRunner,
    SQLiteConfig,
    SQLiteConnectionFactory,
)
from mcp_hangar.infrastructure.persistence.saga_state_store import (
    SAGA_STORE_MIGRATIONS,
    NullSagaStateStore,
    SagaStateStore,
)

# Import application.commands FIRST to break circular import chain:
# Importing commands triggers: commands.__init__ -> reload_handler -> server.config
# -> server.state -> application.sagas (completing the sagas package init).
# After that, importing individual saga submodules works normally.
from mcp_hangar.application.commands import Command  # noqa: F401
from mcp_hangar.application.sagas.group_rebalance_saga import GroupRebalanceSaga
from mcp_hangar.application.sagas.mcp_server_failover_saga import (
    FailoverConfig,
    FailoverState,
    McpServerFailoverEventSaga,
)
from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga


class TestSagaStateStoreCheckpoint:
    """Test SagaStateStore.checkpoint() persistence."""

    def test_checkpoint_persists_state(self):
        """Test that checkpoint() writes state to SQLite and load() retrieves it."""
        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        store = SagaStateStore(factory)

        state_data = {"retry_state": {"p1": {"retries": 2}}}
        store.checkpoint("mcp_server_recovery", "saga-123", state_data, 42)

        result = store.load("mcp_server_recovery")
        assert result is not None
        assert result["state_data"] == state_data
        assert result["last_event_position"] == 42

    def test_checkpoint_overwrites_previous_state(self):
        """Test that checkpoint() with same saga_type+saga_id overwrites previous state."""
        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        store = SagaStateStore(factory)

        store.checkpoint("mcp_server_recovery", "saga-123", {"version": 1}, 10)
        store.checkpoint("mcp_server_recovery", "saga-123", {"version": 2}, 20)

        result = store.load("mcp_server_recovery")
        assert result is not None
        assert result["state_data"] == {"version": 2}
        assert result["last_event_position"] == 20


class TestSagaStateStoreLoad:
    """Test SagaStateStore.load() retrieval."""

    def test_load_returns_none_for_unknown_saga_type(self):
        """Test that load() returns None for an unknown saga_type."""
        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        store = SagaStateStore(factory)

        result = store.load("nonexistent_saga")
        assert result is None


class TestSagaStateStoreIdempotency:
    """Test SagaStateStore.mark_processed() and is_processed()."""

    def test_mark_processed_and_is_processed(self):
        """Test that mark_processed() records event position and is_processed() returns True."""
        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        store = SagaStateStore(factory)

        store.mark_processed("mcp_server_recovery", 42)

        assert store.is_processed("mcp_server_recovery", 42) is True

    def test_is_processed_returns_false_for_unrecorded_position(self):
        """Test that is_processed() returns False for an unrecorded position."""
        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        store = SagaStateStore(factory)

        assert store.is_processed("mcp_server_recovery", 99) is False

    def test_is_processed_returns_false_for_different_saga_type(self):
        """Test that is_processed() is scoped to saga_type."""
        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        store = SagaStateStore(factory)

        store.mark_processed("mcp_server_recovery", 42)

        assert store.is_processed("mcp_server_failover", 42) is False


class TestSagaStateStoreMigrations:
    """Test that MigrationRunner creates the expected tables."""

    def test_migrations_create_saga_state_table(self):
        """Test that the migration creates saga_state and saga_idempotency tables."""
        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        runner = MigrationRunner(factory, SAGA_STORE_MIGRATIONS, table_name="saga_state_migrations")
        applied = runner.run()

        assert applied == 1

        with factory.get_connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('saga_state', 'saga_idempotency') ORDER BY name"
            )
            tables = [row[0] for row in cursor.fetchall()]

        assert "saga_idempotency" in tables
        assert "saga_state" in tables


class TestMcpServerRecoverySagaSerialization:
    """Test McpServerRecoverySaga.to_dict() / from_dict() round-trip."""

    def test_to_dict_serializes_retry_state(self):
        """Test that to_dict() returns retry state dict."""
        saga = McpServerRecoverySaga()
        saga._retry_state = {
            "p1": {"retries": 2, "last_attempt": 1000.0, "next_retry": 1010.0},
            "p2": {"retries": 0, "last_attempt": 0, "next_retry": 0},
        }

        result = saga.to_dict()

        assert "retry_state" in result
        assert result["retry_state"]["p1"]["retries"] == 2

    def test_from_dict_restores_retry_state(self):
        """Test that from_dict() restores retry state from serialized data."""
        saga = McpServerRecoverySaga()
        data = {
            "retry_state": {
                "p1": {"retries": 3, "last_attempt": 500.0, "next_retry": 510.0},
            }
        }

        saga.from_dict(data)

        assert saga._retry_state["p1"]["retries"] == 3
        assert saga._retry_state["p1"]["last_attempt"] == 500.0

    def test_round_trip_serialization(self):
        """Test full round-trip: to_dict -> from_dict preserves state."""
        saga = McpServerRecoverySaga()
        saga._retry_state = {
            "p1": {"retries": 1, "last_attempt": 100.0, "next_retry": 105.0},
        }

        serialized = saga.to_dict()

        restored_saga = McpServerRecoverySaga()
        restored_saga.from_dict(serialized)

        assert restored_saga._retry_state == saga._retry_state


class TestMcpServerFailoverSagaSerialization:
    """Test McpServerFailoverEventSaga.to_dict() / from_dict() round-trip."""

    def test_to_dict_serializes_failover_state(self):
        """Test that to_dict() serializes failover_configs and active_failovers."""
        saga = McpServerFailoverEventSaga()
        saga._failover_configs["p1"] = FailoverConfig(
            primary_id="p1", backup_id="p1-backup", auto_failback=True, failback_delay_s=30.0
        )
        saga._active_failovers["p1"] = FailoverState(
            primary_id="p1", backup_id="p1-backup", failed_at=1000.0, backup_started_at=1001.0, is_active=True
        )
        saga._active_backups = {"p1-backup"}
        saga._pending_failback_timers = {"p1": "timer-id-abc"}

        result = saga.to_dict()

        assert "failover_configs" in result
        assert result["failover_configs"]["p1"]["primary_id"] == "p1"
        assert "active_failovers" in result
        assert result["active_failovers"]["p1"]["failed_at"] == 1000.0
        assert "active_backups" in result
        assert "p1-backup" in result["active_backups"]
        assert "pending_failback_timers" in result

    def test_from_dict_restores_failover_state(self):
        """Test that from_dict() restores all failover state."""
        saga = McpServerFailoverEventSaga()
        data = {
            "failover_configs": {
                "p1": {
                    "primary_id": "p1",
                    "backup_id": "p1-backup",
                    "auto_failback": False,
                    "failback_delay_s": 60.0,
                },
            },
            "active_failovers": {
                "p1": {
                    "primary_id": "p1",
                    "backup_id": "p1-backup",
                    "failed_at": 2000.0,
                    "backup_started_at": None,
                    "is_active": True,
                },
            },
            "active_backups": ["p1-backup"],
            "pending_failback_timers": {},
        }

        saga.from_dict(data)

        assert saga._failover_configs["p1"].primary_id == "p1"
        assert saga._failover_configs["p1"].auto_failback is False
        assert saga._active_failovers["p1"].failed_at == 2000.0
        assert saga._active_failovers["p1"].backup_started_at is None
        assert "p1-backup" in saga._active_backups
        # Timers are not restored across restarts
        assert saga._pending_failback_timers == {}

    def test_round_trip_serialization(self):
        """Test full round-trip: to_dict -> json -> from_dict preserves state."""
        saga = McpServerFailoverEventSaga()
        saga._failover_configs["p1"] = FailoverConfig(
            primary_id="p1", backup_id="p1-backup", auto_failback=True, failback_delay_s=30.0
        )
        saga._active_failovers["p1"] = FailoverState(
            primary_id="p1", backup_id="p1-backup", failed_at=1000.0, backup_started_at=1001.0, is_active=True
        )
        saga._active_backups = {"p1-backup"}
        saga._pending_failback_timers = {}

        serialized = saga.to_dict()
        # Simulate JSON round-trip (what SagaStateStore does)
        json_str = json.dumps(serialized)
        deserialized = json.loads(json_str)

        restored = McpServerFailoverEventSaga()
        restored.from_dict(deserialized)

        assert restored._failover_configs["p1"].primary_id == "p1"
        assert restored._active_failovers["p1"].failed_at == 1000.0
        assert "p1-backup" in restored._active_backups
        assert restored._pending_failback_timers == {}


class TestGroupRebalanceSagaSerialization:
    """Test GroupRebalanceSaga.to_dict() / from_dict()."""

    def test_to_dict_returns_empty_dict(self):
        """Test that to_dict() returns empty dict for stateless saga."""
        saga = GroupRebalanceSaga()

        result = saga.to_dict()

        assert result == {}

    def test_from_dict_is_noop(self):
        """Test that from_dict() is a no-op for stateless saga."""
        saga = GroupRebalanceSaga()
        saga._member_to_group = {"p1": "g1"}

        # from_dict should not affect the transient _member_to_group
        saga.from_dict({})

        assert saga._member_to_group == {"p1": "g1"}


class TestNullSagaStateStore:
    """Test NullSagaStateStore (null object pattern)."""

    def test_checkpoint_does_nothing(self):
        """Test that checkpoint() on NullSagaStateStore does not raise."""
        store = NullSagaStateStore()
        store.checkpoint("test", "id", {"key": "val"}, 1)

    def test_load_returns_none(self):
        """Test that load() returns None."""
        store = NullSagaStateStore()
        assert store.load("test") is None

    def test_mark_processed_does_nothing(self):
        """Test that mark_processed() does not raise."""
        store = NullSagaStateStore()
        store.mark_processed("test", 1)

    def test_is_processed_returns_false(self):
        """Test that is_processed() returns False."""
        store = NullSagaStateStore()
        assert store.is_processed("test", 1) is False


class TestBootstrapSagaWiring:
    """Test init_saga() bootstrap wiring for state persistence and restoration."""

    def test_init_saga_creates_saga_state_store_with_sqlite(self):
        """init_saga() creates SagaStateStore when event_store config has SQLite driver."""
        from mcp_hangar.server.bootstrap.cqrs import _create_saga_state_store

        config = {"event_store": {"driver": "sqlite", "path": "data/events.db"}}
        store = _create_saga_state_store(config)

        assert isinstance(store, SagaStateStore)

    def test_init_saga_uses_null_store_when_no_event_store(self):
        """init_saga() uses NullSagaStateStore when event_store is not configured."""
        from mcp_hangar.server.bootstrap.cqrs import _create_saga_state_store

        store = _create_saga_state_store(None)
        assert isinstance(store, NullSagaStateStore)

    def test_init_saga_uses_null_store_when_memory_driver(self):
        """init_saga() uses NullSagaStateStore when event_store driver is memory."""
        from mcp_hangar.server.bootstrap.cqrs import _create_saga_state_store

        config = {"event_store": {"driver": "memory"}}
        store = _create_saga_state_store(config)

        assert isinstance(store, NullSagaStateStore)

    def test_restore_saga_state_loads_recovery_saga(self):
        """init_saga helper restores McpServerRecoverySaga state from store."""
        from mcp_hangar.server.bootstrap.cqrs import _restore_saga_state

        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        store = SagaStateStore(factory)

        # Pre-populate state in the store
        state_data = {"retry_state": {"p1": {"retries": 3, "last_attempt": 100.0, "next_retry": 110.0}}}
        store.checkpoint("mcp_server_recovery", "saga-id", state_data, 42)

        saga = McpServerRecoverySaga()
        _restore_saga_state(store, saga)

        assert saga._retry_state["p1"]["retries"] == 3

    def test_restore_saga_state_loads_failover_saga(self):
        """init_saga helper restores McpServerFailoverEventSaga state from store."""
        from mcp_hangar.server.bootstrap.cqrs import _restore_saga_state

        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        store = SagaStateStore(factory)

        # Pre-populate state
        state_data = {
            "failover_configs": {
                "p1": {"primary_id": "p1", "backup_id": "p1-backup", "auto_failback": True, "failback_delay_s": 30.0}
            },
            "active_failovers": {},
            "active_backups": [],
            "pending_failback_timers": {},
        }
        store.checkpoint("mcp_server_failover_event", "saga-id", state_data, 10)

        saga = McpServerFailoverEventSaga()
        _restore_saga_state(store, saga)

        assert saga._failover_configs["p1"].primary_id == "p1"

    def test_restore_saga_state_handles_missing_state(self):
        """init_saga helper handles missing persisted state gracefully (first boot)."""
        from mcp_hangar.server.bootstrap.cqrs import _restore_saga_state

        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        store = SagaStateStore(factory)

        saga = McpServerRecoverySaga()
        # Should not raise
        _restore_saga_state(store, saga)

        # State should be default (empty)
        assert saga._retry_state == {}

    def test_restore_group_circuit_breakers(self):
        """Circuit breaker state is restored for provider groups from saga state store."""
        from mcp_hangar.server.bootstrap.cqrs import _restore_group_circuit_breakers

        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        store = SagaStateStore(factory)

        # Pre-populate CB state in the store
        cb_state = {
            "state": "open",
            "failure_count": 5,
            "failure_threshold": 10,
            "reset_timeout_s": 60.0,
            "opened_at": 1000.0,
        }
        store.checkpoint("circuit_breaker", "group-1", cb_state, 0)

        # Create a mock group
        mock_group = MagicMock()
        mock_group.id = "group-1"
        mock_group._circuit_breaker = CircuitBreaker()

        groups = {"group-1": mock_group}
        _restore_group_circuit_breakers(store, groups)

        # Verify CB was replaced
        new_cb = mock_group._circuit_breaker
        assert isinstance(new_cb, CircuitBreaker)
        assert new_cb.state == CircuitState.OPEN
        assert new_cb.failure_count == 5

    def test_save_group_circuit_breakers(self):
        """save_group_circuit_breakers persists CB state for all groups."""
        from mcp_hangar.server.bootstrap.cqrs import save_group_circuit_breakers

        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        store = SagaStateStore(factory)

        # Create a mock group with open CB
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=5))
        # Force open by recording failures
        for _ in range(5):
            cb.record_failure()

        mock_group = MagicMock()
        mock_group.id = "test-group"
        mock_group._circuit_breaker = cb

        groups = {"test-group": mock_group}
        save_group_circuit_breakers(store, groups)

        # Verify it was saved
        result = store.load("circuit_breaker")
        assert result is not None
        assert result["state_data"]["state"] == "open"
