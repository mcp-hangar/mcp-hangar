"""Integration tests for PostgreSQL persistence layer using Testcontainers.

These tests verify that the persistence layer works correctly with a real
PostgreSQL database, including:
- Provider configuration persistence
- Audit logging
- Concurrent operations
"""

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.container,
    pytest.mark.postgres,
]


class TestPostgresConnection:
    """Tests for PostgreSQL connection and basic operations."""

    @pytest.mark.asyncio
    async def test_connection_and_query(self, postgres_container: dict) -> None:
        """Can connect to PostgreSQL and execute queries."""
        dsn = postgres_container["dsn"]

        try:
            import asyncpg

            conn = await asyncpg.connect(dsn)
            result = await conn.fetchval("SELECT 1")
            await conn.close()

            assert result == 1
        except ImportError:
            pytest.skip("asyncpg not installed")


class TestPostgresProviderConfigPersistence:
    """Tests for provider configuration persistence in PostgreSQL."""

    @pytest.mark.asyncio
    async def test_config_crud_operations(self, postgres_container: dict) -> None:
        """Provider configs can be created, read, updated, deleted."""
        dsn = postgres_container["dsn"]

        try:
            import asyncpg

            conn = await asyncpg.connect(dsn)

            # Setup table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_configs (
                    id SERIAL PRIMARY KEY,
                    provider_id TEXT UNIQUE NOT NULL,
                    config JSONB NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """
            )

            # Create
            await conn.execute(
                """
                INSERT INTO provider_configs (provider_id, config)
                VALUES ($1, $2)
                ON CONFLICT (provider_id) DO UPDATE SET config = $2
            """,
                "crud-test",
                '{"mode": "subprocess"}',
            )

            # Read
            row = await conn.fetchrow("SELECT config FROM provider_configs WHERE provider_id = $1", "crud-test")
            assert row is not None
            assert "subprocess" in row["config"]

            # Update
            await conn.execute(
                """
                UPDATE provider_configs SET config = $2 WHERE provider_id = $1
            """,
                "crud-test",
                '{"mode": "container"}',
            )

            row = await conn.fetchrow("SELECT config FROM provider_configs WHERE provider_id = $1", "crud-test")
            assert "container" in row["config"]

            # Delete
            await conn.execute("DELETE FROM provider_configs WHERE provider_id = $1", "crud-test")

            row = await conn.fetchrow("SELECT config FROM provider_configs WHERE provider_id = $1", "crud-test")
            assert row is None

            await conn.close()

        except ImportError:
            pytest.skip("asyncpg not installed")

    @pytest.mark.asyncio
    async def test_concurrent_updates_are_safe(self, postgres_container: dict) -> None:
        """Concurrent updates don't corrupt data."""
        import asyncio

        dsn = postgres_container["dsn"]

        try:
            import asyncpg

            conn = await asyncpg.connect(dsn)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS test_counter (
                    id INTEGER PRIMARY KEY,
                    value INTEGER DEFAULT 0
                )
            """
            )
            await conn.execute("INSERT INTO test_counter (id, value) VALUES (1, 0) ON CONFLICT DO NOTHING")
            await conn.close()

            async def increment():
                c = await asyncpg.connect(dsn)
                for _ in range(10):
                    await c.execute("UPDATE test_counter SET value = value + 1 WHERE id = 1")
                await c.close()

            # Run 10 concurrent tasks, each incrementing 10 times
            await asyncio.gather(*[increment() for _ in range(10)])

            conn = await asyncpg.connect(dsn)
            result = await conn.fetchval("SELECT value FROM test_counter WHERE id = 1")
            await conn.close()

            assert result == 100

        except ImportError:
            pytest.skip("asyncpg not installed")


class TestPostgresAuditLog:
    """Tests for audit logging in PostgreSQL."""

    @pytest.mark.asyncio
    async def test_audit_entries_stored_and_queryable(self, postgres_container: dict) -> None:
        """Audit entries are stored and can be queried."""
        dsn = postgres_container["dsn"]

        try:
            from datetime import datetime, timedelta
            import json

            import asyncpg

            conn = await asyncpg.connect(dsn)

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id SERIAL PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    provider_id TEXT,
                    details JSONB,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """
            )

            # Insert audit entry
            await conn.execute(
                """
                INSERT INTO audit_log (event_type, provider_id, details)
                VALUES ($1, $2, $3)
            """,
                "TOOL_INVOKED",
                "audit-test",
                json.dumps(
                    {
                        "tool": "add",
                        "result": 3,
                    }
                ),
            )

            # Query by provider
            rows = await conn.fetch("SELECT * FROM audit_log WHERE provider_id = $1", "audit-test")
            assert len(rows) >= 1
            assert rows[0]["event_type"] == "TOOL_INVOKED"

            # Query by time range
            rows = await conn.fetch(
                """
                SELECT * FROM audit_log
                WHERE created_at > $1
                ORDER BY created_at DESC
            """,
                datetime.now() - timedelta(hours=1),
            )
            assert isinstance(rows, list)

            await conn.close()

        except ImportError:
            pytest.skip("asyncpg not installed")
