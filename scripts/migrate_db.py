"""Database migrations for MCP Hangar knowledge base.

NOTE: Migrations are now run automatically when the server starts!
This script is kept for manual operations (check, reset).

Creates required tables for:
- Tool invocation cache
- Audit logs
- Provider state history
"""

import asyncio
import sys
from datetime import datetime

# SQL migrations in order
MIGRATIONS = [
    # Migration 001: Initial schema
    {
        "version": 1,
        "name": "initial_schema",
        "sql": """
-- Migration tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tool invocation cache
CREATE TABLE IF NOT EXISTS tool_cache (
    id SERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    tool TEXT NOT NULL,
    arguments_hash TEXT NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE(provider, tool, arguments_hash)
);

CREATE INDEX IF NOT EXISTS idx_tool_cache_lookup 
    ON tool_cache(provider, tool, arguments_hash);
CREATE INDEX IF NOT EXISTS idx_tool_cache_expires 
    ON tool_cache(expires_at);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    event_type TEXT NOT NULL,
    provider TEXT,
    tool TEXT,
    arguments JSONB,
    result_summary TEXT,
    duration_ms INTEGER,
    success BOOLEAN NOT NULL,
    error_message TEXT,
    correlation_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp 
    ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_provider 
    ON audit_log(provider, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_correlation 
    ON audit_log(correlation_id);

-- Provider state history
CREATE TABLE IF NOT EXISTS provider_state_history (
    id SERIAL PRIMARY KEY,
    provider_id TEXT NOT NULL,
    old_state TEXT,
    new_state TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_provider_state_provider 
    ON provider_state_history(provider_id, timestamp DESC);

-- Knowledge entities (for memory provider sync)
CREATE TABLE IF NOT EXISTS knowledge_entities (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    entity_type TEXT NOT NULL,
    observations JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_entities_name 
    ON knowledge_entities(name);
CREATE INDEX IF NOT EXISTS idx_knowledge_entities_type 
    ON knowledge_entities(entity_type);

-- Knowledge relations
CREATE TABLE IF NOT EXISTS knowledge_relations (
    id SERIAL PRIMARY KEY,
    from_entity TEXT NOT NULL REFERENCES knowledge_entities(name) ON DELETE CASCADE,
    to_entity TEXT NOT NULL REFERENCES knowledge_entities(name) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(from_entity, to_entity, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_from 
    ON knowledge_relations(from_entity);
CREATE INDEX IF NOT EXISTS idx_knowledge_relations_to 
    ON knowledge_relations(to_entity);
""",
    },
    # Migration 002: Add metrics table
    {
        "version": 2,
        "name": "metrics_table",
        "sql": """
-- Provider metrics
CREATE TABLE IF NOT EXISTS provider_metrics (
    id SERIAL PRIMARY KEY,
    provider_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    labels JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_provider_metrics_lookup 
    ON provider_metrics(provider_id, metric_name, timestamp DESC);

-- Cleanup old cache entries (function)
CREATE OR REPLACE FUNCTION cleanup_expired_cache() RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM tool_cache WHERE expires_at < NOW();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
""",
    },
]


async def get_current_version(conn) -> int:
    """Get current schema version."""
    try:
        result = await conn.fetchval(
            "SELECT MAX(version) FROM schema_migrations"
        )
        return result or 0
    except Exception:
        return 0


async def run_migrations(dsn: str, target_version: int | None = None) -> list[str]:
    """Run pending migrations.

    Args:
        dsn: PostgreSQL connection string
        target_version: Optional target version (default: latest)

    Returns:
        List of applied migration names
    """
    try:
        import asyncpg
    except ImportError:
        print("Error: asyncpg not installed. Run: pip install asyncpg")
        return []

    conn = await asyncpg.connect(dsn)
    applied = []

    try:
        current_version = await get_current_version(conn)
        max_version = target_version or max(m["version"] for m in MIGRATIONS)

        print(f"Current schema version: {current_version}")
        print(f"Target schema version: {max_version}")

        for migration in MIGRATIONS:
            version = migration["version"]
            name = migration["name"]

            if version <= current_version:
                continue

            if target_version and version > target_version:
                break

            print(f"Applying migration {version}: {name}...")

            await conn.execute(migration["sql"])
            await conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
                version,
                name,
            )

            applied.append(name)
            print(f"  ✓ Applied: {name}")

        if not applied:
            print("No migrations to apply.")
        else:
            print(f"\n✅ Applied {len(applied)} migration(s)")

    finally:
        await conn.close()

    return applied


async def check_connection(dsn: str) -> bool:
    """Check if database is reachable."""
    try:
        import asyncpg
        conn = await asyncpg.connect(dsn, timeout=5)
        await conn.close()
        return True
    except Exception as e:
        print(f"Connection failed: {e}")
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MCP Hangar database migrations")
    parser.add_argument(
        "--dsn",
        default="postgresql://mcp:secret@localhost:5432/mcp_hangar",
        help="PostgreSQL connection string",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check connection, don't run migrations",
    )
    parser.add_argument(
        "--version",
        type=int,
        help="Target migration version",
    )

    args = parser.parse_args()

    if args.check:
        ok = asyncio.run(check_connection(args.dsn))
        sys.exit(0 if ok else 1)

    applied = asyncio.run(run_migrations(args.dsn, args.version))
    sys.exit(0 if applied is not None else 1)


if __name__ == "__main__":
    main()

