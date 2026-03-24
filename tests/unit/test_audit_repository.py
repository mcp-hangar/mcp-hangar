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
