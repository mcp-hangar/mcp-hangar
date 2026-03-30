"""Tests for persistence layer - audit repository."""

from datetime import datetime, timedelta, UTC

import pytest

from mcp_hangar.domain.contracts.persistence import AuditAction, AuditEntry
from mcp_hangar.infrastructure.persistence import (
    Database,
    DatabaseConfig,
    InMemoryAuditRepository,
    SQLiteAuditRepository,
)


@pytest.fixture
def audit_entry() -> AuditEntry:
    """Create a test audit entry."""
    return AuditEntry(
        entity_id="test-provider",
        entity_type="provider",
        action=AuditAction.STARTED,
        timestamp=datetime.now(UTC),
        actor="system",
        new_state={"state": "ready"},
        metadata={"startup_ms": 150},
        correlation_id="corr-123",
    )


class TestInMemoryAuditRepository:
    """Tests for in-memory audit repository."""

    @pytest.fixture
    def repo(self) -> InMemoryAuditRepository:
        """Create repository instance."""
        return InMemoryAuditRepository()

    @pytest.mark.asyncio
    async def test_append_and_get_by_entity(self, repo: InMemoryAuditRepository, audit_entry: AuditEntry):
        """Test appending and retrieving by entity."""
        await repo.append(audit_entry)

        results = await repo.get_by_entity(audit_entry.entity_id)

        assert len(results) == 1
        assert results[0].entity_id == audit_entry.entity_id
        assert results[0].action == AuditAction.STARTED

    @pytest.mark.asyncio
    async def test_get_by_entity_with_type_filter(self, repo: InMemoryAuditRepository, audit_entry: AuditEntry):
        """Test filtering by entity type."""
        await repo.append(audit_entry)

        # Different entity type
        other_entry = AuditEntry(
            entity_id=audit_entry.entity_id,
            entity_type="tool_invocation",
            action=AuditAction.UPDATED,
            timestamp=datetime.now(UTC),
            actor="user",
        )
        await repo.append(other_entry)

        results = await repo.get_by_entity(audit_entry.entity_id, entity_type="provider")

        assert len(results) == 1
        assert results[0].entity_type == "provider"

    @pytest.mark.asyncio
    async def test_get_by_time_range(self, repo: InMemoryAuditRepository):
        """Test filtering by time range."""
        now = datetime.now(UTC)

        # Add entries at different times
        old_entry = AuditEntry(
            entity_id="provider-1",
            entity_type="provider",
            action=AuditAction.STARTED,
            timestamp=now - timedelta(hours=2),
            actor="system",
        )
        recent_entry = AuditEntry(
            entity_id="provider-2",
            entity_type="provider",
            action=AuditAction.STOPPED,
            timestamp=now - timedelta(minutes=30),
            actor="system",
        )

        await repo.append(old_entry)
        await repo.append(recent_entry)

        # Query last hour
        results = await repo.get_by_time_range(
            start=now - timedelta(hours=1),
            end=now,
        )

        assert len(results) == 1
        assert results[0].entity_id == "provider-2"

    @pytest.mark.asyncio
    async def test_get_by_correlation_id(self, repo: InMemoryAuditRepository, audit_entry: AuditEntry):
        """Test filtering by correlation ID."""
        await repo.append(audit_entry)

        # Add unrelated entry
        other_entry = AuditEntry(
            entity_id="other-provider",
            entity_type="provider",
            action=AuditAction.STOPPED,
            timestamp=datetime.now(UTC),
            actor="system",
            correlation_id="different-corr",
        )
        await repo.append(other_entry)

        results = await repo.get_by_correlation_id(audit_entry.correlation_id)

        assert len(results) == 1
        assert results[0].correlation_id == "corr-123"

    @pytest.mark.asyncio
    async def test_max_entries_pruning(self):
        """Test that old entries are pruned when max is exceeded."""
        repo = InMemoryAuditRepository(max_entries=10)

        # Add more than max
        for i in range(15):
            entry = AuditEntry(
                entity_id=f"provider-{i}",
                entity_type="provider",
                action=AuditAction.STARTED,
                timestamp=datetime.now(UTC),
                actor="system",
            )
            await repo.append(entry)

        results = await repo.get_by_time_range(
            start=datetime(2000, 1, 1, tzinfo=UTC),
            end=datetime.now(UTC),
            limit=20,
        )

        assert len(results) == 10

    @pytest.mark.asyncio
    async def test_identity_fields_round_trip(self, repo: InMemoryAuditRepository):
        """Test that identity fields are preserved through append and retrieval."""
        entry = AuditEntry(
            entity_id="tool-invoke-1",
            entity_type="tool_invocation",
            action=AuditAction.TOOL_INVOKED,
            timestamp=datetime.now(UTC),
            actor="agent",
            caller_user_id="user-42",
            caller_agent_id="agent-7",
            caller_session_id="sess-abc",
            caller_principal_type="api_key",
            correlation_id="corr-456",
        )
        await repo.append(entry)

        results = await repo.get_by_entity("tool-invoke-1")

        assert len(results) == 1
        result = results[0]
        assert result.caller_user_id == "user-42"
        assert result.caller_agent_id == "agent-7"
        assert result.caller_session_id == "sess-abc"
        assert result.caller_principal_type == "api_key"

    @pytest.mark.asyncio
    async def test_get_by_caller_returns_matching_entries(self, repo: InMemoryAuditRepository):
        """Test get_by_caller filters by caller_user_id."""
        now = datetime.now(UTC)
        entry_user_a = AuditEntry(
            entity_id="tool-1",
            entity_type="tool_invocation",
            action=AuditAction.TOOL_INVOKED,
            timestamp=now,
            actor="agent",
            caller_user_id="user-a",
        )
        entry_user_b = AuditEntry(
            entity_id="tool-2",
            entity_type="tool_invocation",
            action=AuditAction.TOOL_INVOKED,
            timestamp=now,
            actor="agent",
            caller_user_id="user-b",
        )
        await repo.append(entry_user_a)
        await repo.append(entry_user_b)

        results = await repo.get_by_caller("user-a")

        assert len(results) == 1
        assert results[0].entity_id == "tool-1"
        assert results[0].caller_user_id == "user-a"

    @pytest.mark.asyncio
    async def test_get_by_caller_with_action_filter(self, repo: InMemoryAuditRepository):
        """Test get_by_caller respects optional action filter."""
        now = datetime.now(UTC)
        tool_entry = AuditEntry(
            entity_id="tool-1",
            entity_type="tool_invocation",
            action=AuditAction.TOOL_INVOKED,
            timestamp=now,
            actor="agent",
            caller_user_id="user-a",
        )
        start_entry = AuditEntry(
            entity_id="provider-1",
            entity_type="provider",
            action=AuditAction.STARTED,
            timestamp=now,
            actor="agent",
            caller_user_id="user-a",
        )
        await repo.append(tool_entry)
        await repo.append(start_entry)

        results = await repo.get_by_caller("user-a", action=AuditAction.TOOL_INVOKED)

        assert len(results) == 1
        assert results[0].action == AuditAction.TOOL_INVOKED

    @pytest.mark.asyncio
    async def test_get_by_caller_returns_empty_for_unknown_user(self, repo: InMemoryAuditRepository):
        """Test get_by_caller returns empty list for nonexistent caller."""
        results = await repo.get_by_caller("nonexistent-user")
        assert results == []

    @pytest.mark.asyncio
    async def test_get_by_caller_respects_pagination(self, repo: InMemoryAuditRepository):
        """Test get_by_caller supports limit and offset."""
        now = datetime.now(UTC)
        for i in range(5):
            entry = AuditEntry(
                entity_id=f"tool-{i}",
                entity_type="tool_invocation",
                action=AuditAction.TOOL_INVOKED,
                timestamp=now + timedelta(seconds=i),
                actor="agent",
                caller_user_id="user-a",
            )
            await repo.append(entry)

        page1 = await repo.get_by_caller("user-a", limit=2, offset=0)
        page2 = await repo.get_by_caller("user-a", limit=2, offset=2)

        assert len(page1) == 2
        assert len(page2) == 2


class TestSQLiteAuditRepository:
    """Tests for SQLite audit repository."""

    @pytest.fixture
    def database(self, tmp_path) -> Database:
        """Create test database (sync fixture)."""
        return Database(DatabaseConfig(path=str(tmp_path / "test.db")))

    @pytest.fixture
    def repo(self, database: Database) -> SQLiteAuditRepository:
        """Create repository instance."""
        return SQLiteAuditRepository(database)

    @pytest.mark.asyncio
    async def test_append_and_get_by_entity(
        self, database: Database, repo: SQLiteAuditRepository, audit_entry: AuditEntry
    ):
        """Test appending and retrieving by entity."""
        await database.initialize()
        await repo.append(audit_entry)

        results = await repo.get_by_entity(audit_entry.entity_id)

        assert len(results) == 1
        assert results[0].entity_id == audit_entry.entity_id
        assert results[0].action == AuditAction.STARTED
        assert results[0].metadata == audit_entry.metadata

    @pytest.mark.asyncio
    async def test_get_by_entity_with_pagination(self, database: Database, repo: SQLiteAuditRepository):
        """Test pagination of results."""
        await database.initialize()
        for i in range(10):
            entry = AuditEntry(
                entity_id="provider-1",
                entity_type="provider",
                action=AuditAction.STATE_CHANGED,
                timestamp=datetime.now(UTC) + timedelta(seconds=i),
                actor="system",
            )
            await repo.append(entry)

        page1 = await repo.get_by_entity("provider-1", limit=3, offset=0)
        page2 = await repo.get_by_entity("provider-1", limit=3, offset=3)

        assert len(page1) == 3
        assert len(page2) == 3

    @pytest.mark.asyncio
    async def test_get_by_time_range_with_action_filter(self, database: Database, repo: SQLiteAuditRepository):
        """Test filtering by action type."""
        await database.initialize()
        now = datetime.now(UTC)

        started = AuditEntry(
            entity_id="provider-1",
            entity_type="provider",
            action=AuditAction.STARTED,
            timestamp=now,
            actor="system",
        )
        stopped = AuditEntry(
            entity_id="provider-1",
            entity_type="provider",
            action=AuditAction.STOPPED,
            timestamp=now,
            actor="system",
        )

        await repo.append(started)
        await repo.append(stopped)

        results = await repo.get_by_time_range(
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=1),
            action=AuditAction.STARTED,
        )

        assert len(results) == 1
        assert results[0].action == AuditAction.STARTED

    @pytest.mark.asyncio
    async def test_count_by_entity(self, database: Database, repo: SQLiteAuditRepository):
        """Test counting entries by entity."""
        await database.initialize()
        for i in range(5):
            entry = AuditEntry(
                entity_id="provider-1",
                entity_type="provider",
                action=AuditAction.STATE_CHANGED,
                timestamp=datetime.now(UTC),
                actor="system",
            )
            await repo.append(entry)

        count = await repo.count_by_entity("provider-1")

        assert count == 5

    @pytest.mark.asyncio
    async def test_get_recent_actions(self, database: Database, repo: SQLiteAuditRepository):
        """Test getting recent actions of specific type."""
        await database.initialize()
        for i in range(3):
            started = AuditEntry(
                entity_id=f"provider-{i}",
                entity_type="provider",
                action=AuditAction.STARTED,
                timestamp=datetime.now(UTC),
                actor="system",
            )
            await repo.append(started)

        for i in range(2):
            stopped = AuditEntry(
                entity_id=f"provider-{i}",
                entity_type="provider",
                action=AuditAction.STOPPED,
                timestamp=datetime.now(UTC),
                actor="system",
            )
            await repo.append(stopped)

        results = await repo.get_recent_actions(
            entity_type="provider",
            action=AuditAction.STARTED,
            limit=10,
        )

        assert len(results) == 3
        assert all(r.action == AuditAction.STARTED for r in results)

    @pytest.mark.asyncio
    async def test_serialization_of_complex_metadata(self, database: Database, repo: SQLiteAuditRepository):
        """Test that complex metadata is properly serialized/deserialized."""
        await database.initialize()
        entry = AuditEntry(
            entity_id="provider-1",
            entity_type="provider",
            action=AuditAction.STARTED,
            timestamp=datetime.now(UTC),
            actor="system",
            old_state={"previous": "config", "nested": {"key": "value"}},
            new_state={"new": "config", "list": [1, 2, 3]},
            metadata={
                "duration_ms": 150.5,
                "tools": ["tool1", "tool2"],
                "flags": {"enabled": True},
            },
        )

        await repo.append(entry)

        results = await repo.get_by_entity("provider-1")

        assert len(results) == 1
        result = results[0]
        assert result.old_state == {"previous": "config", "nested": {"key": "value"}}
        assert result.new_state == {"new": "config", "list": [1, 2, 3]}
        assert result.metadata["duration_ms"] == 150.5
        assert result.metadata["tools"] == ["tool1", "tool2"]

    @pytest.mark.asyncio
    async def test_identity_fields_round_trip(self, database: Database, repo: SQLiteAuditRepository):
        """Test that identity fields survive SQLite insert and select."""
        await database.initialize()
        entry = AuditEntry(
            entity_id="tool-invoke-1",
            entity_type="tool_invocation",
            action=AuditAction.TOOL_INVOKED,
            timestamp=datetime.now(UTC),
            actor="agent",
            caller_user_id="user-42",
            caller_agent_id="agent-7",
            caller_session_id="sess-abc",
            caller_principal_type="api_key",
            metadata={"tool_name": "read_file"},
            correlation_id="corr-789",
        )

        await repo.append(entry)

        results = await repo.get_by_entity("tool-invoke-1")

        assert len(results) == 1
        result = results[0]
        assert result.caller_user_id == "user-42"
        assert result.caller_agent_id == "agent-7"
        assert result.caller_session_id == "sess-abc"
        assert result.caller_principal_type == "api_key"
        assert result.action == AuditAction.TOOL_INVOKED
        assert result.correlation_id == "corr-789"

    @pytest.mark.asyncio
    async def test_identity_fields_default_to_none(self, database: Database, repo: SQLiteAuditRepository):
        """Test that entries without identity fields read back with None."""
        await database.initialize()
        entry = AuditEntry(
            entity_id="provider-1",
            entity_type="provider",
            action=AuditAction.STARTED,
            timestamp=datetime.now(UTC),
            actor="system",
        )

        await repo.append(entry)

        results = await repo.get_by_entity("provider-1")

        assert len(results) == 1
        result = results[0]
        assert result.caller_user_id is None
        assert result.caller_agent_id is None
        assert result.caller_session_id is None
        assert result.caller_principal_type is None

    @pytest.mark.asyncio
    async def test_get_by_caller(self, database: Database, repo: SQLiteAuditRepository):
        """Test get_by_caller queries by caller_user_id in SQLite."""
        await database.initialize()
        now = datetime.now(UTC)

        entry_user_a = AuditEntry(
            entity_id="tool-1",
            entity_type="tool_invocation",
            action=AuditAction.TOOL_INVOKED,
            timestamp=now,
            actor="agent",
            caller_user_id="user-a",
            caller_agent_id="agent-1",
        )
        entry_user_b = AuditEntry(
            entity_id="tool-2",
            entity_type="tool_invocation",
            action=AuditAction.TOOL_INVOKED,
            timestamp=now,
            actor="agent",
            caller_user_id="user-b",
        )
        entry_no_caller = AuditEntry(
            entity_id="provider-1",
            entity_type="provider",
            action=AuditAction.STARTED,
            timestamp=now,
            actor="system",
        )

        await repo.append(entry_user_a)
        await repo.append(entry_user_b)
        await repo.append(entry_no_caller)

        results = await repo.get_by_caller("user-a")

        assert len(results) == 1
        assert results[0].entity_id == "tool-1"
        assert results[0].caller_user_id == "user-a"
        assert results[0].caller_agent_id == "agent-1"

    @pytest.mark.asyncio
    async def test_get_by_caller_with_action_filter(self, database: Database, repo: SQLiteAuditRepository):
        """Test get_by_caller respects action filter in SQLite."""
        await database.initialize()
        now = datetime.now(UTC)

        tool_entry = AuditEntry(
            entity_id="tool-1",
            entity_type="tool_invocation",
            action=AuditAction.TOOL_INVOKED,
            timestamp=now,
            actor="agent",
            caller_user_id="user-a",
        )
        start_entry = AuditEntry(
            entity_id="provider-1",
            entity_type="provider",
            action=AuditAction.STARTED,
            timestamp=now,
            actor="agent",
            caller_user_id="user-a",
        )

        await repo.append(tool_entry)
        await repo.append(start_entry)

        results = await repo.get_by_caller("user-a", action=AuditAction.TOOL_INVOKED)

        assert len(results) == 1
        assert results[0].action == AuditAction.TOOL_INVOKED

    @pytest.mark.asyncio
    async def test_get_by_caller_with_pagination(self, database: Database, repo: SQLiteAuditRepository):
        """Test get_by_caller supports limit and offset in SQLite."""
        await database.initialize()
        now = datetime.now(UTC)

        for i in range(5):
            entry = AuditEntry(
                entity_id=f"tool-{i}",
                entity_type="tool_invocation",
                action=AuditAction.TOOL_INVOKED,
                timestamp=now + timedelta(seconds=i),
                actor="agent",
                caller_user_id="user-a",
            )
            await repo.append(entry)

        page1 = await repo.get_by_caller("user-a", limit=2, offset=0)
        page2 = await repo.get_by_caller("user-a", limit=2, offset=2)

        assert len(page1) == 2
        assert len(page2) == 2

    @pytest.mark.asyncio
    async def test_get_by_caller_returns_empty_for_unknown_user(
        self, database: Database, repo: SQLiteAuditRepository
    ):
        """Test get_by_caller returns empty list for nonexistent caller."""
        await database.initialize()

        results = await repo.get_by_caller("nonexistent-user")

        assert results == []

    @pytest.mark.asyncio
    async def test_identity_fields_in_time_range_query(self, database: Database, repo: SQLiteAuditRepository):
        """Test that identity fields are returned in time range queries."""
        await database.initialize()
        now = datetime.now(UTC)

        entry = AuditEntry(
            entity_id="tool-1",
            entity_type="tool_invocation",
            action=AuditAction.TOOL_INVOKED,
            timestamp=now,
            actor="agent",
            caller_user_id="user-42",
            caller_agent_id="agent-7",
            caller_session_id="sess-abc",
            caller_principal_type="jwt",
        )
        await repo.append(entry)

        results = await repo.get_by_time_range(
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=1),
        )

        assert len(results) == 1
        result = results[0]
        assert result.caller_user_id == "user-42"
        assert result.caller_agent_id == "agent-7"
        assert result.caller_session_id == "sess-abc"
        assert result.caller_principal_type == "jwt"

    @pytest.mark.asyncio
    async def test_identity_fields_in_correlation_query(self, database: Database, repo: SQLiteAuditRepository):
        """Test that identity fields are returned in correlation ID queries."""
        await database.initialize()
        entry = AuditEntry(
            entity_id="tool-1",
            entity_type="tool_invocation",
            action=AuditAction.TOOL_INVOKED,
            timestamp=datetime.now(UTC),
            actor="agent",
            caller_user_id="user-99",
            caller_agent_id="agent-5",
            correlation_id="corr-identity-test",
        )
        await repo.append(entry)

        results = await repo.get_by_correlation_id("corr-identity-test")

        assert len(results) == 1
        assert results[0].caller_user_id == "user-99"
        assert results[0].caller_agent_id == "agent-5"
