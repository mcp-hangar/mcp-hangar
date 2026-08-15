"""The auth read-model projection and the audit log it feeds."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from mcp_hangar.domain.contracts.event_store import IEventStore
from mcp_hangar.domain.events import (
    ApiKeyCreated,
    ApiKeyRevoked,
    DomainEvent,
    RoleAssigned,
    RoleRevoked,
)


class TestAuthProjection:
    """Tests for AuthProjection read model."""

    def test_apply_api_key_created(self):
        """Lines 122-144: apply ApiKeyCreated event."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        event = ApiKeyCreated(
            key_id="kid-1",
            principal_id="svc-1",
            key_name="test-key",
            expires_at=None,
            created_by="admin",
        )
        proj.apply(event)

        model = proj.get_key_by_id("kid-1")
        assert model is not None
        assert model.key_id == "kid-1"
        assert model.principal_id == "svc-1"
        assert model.name == "test-key"
        assert model.revoked is False

    def test_apply_api_key_revoked(self):
        """Lines 146-158: apply ApiKeyRevoked event."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            ApiKeyCreated(
                key_id="kid-2",
                principal_id="svc-2",
                key_name="to-revoke",
                expires_at=None,
                created_by="admin",
            )
        )
        proj.apply(
            ApiKeyRevoked(
                key_id="kid-2",
                principal_id="svc-2",
                revoked_by="security",
                reason="compromised",
            )
        )

        model = proj.get_key_by_id("kid-2")
        assert model is not None
        assert model.revoked is True
        assert model.revoked_by == "security"
        assert model.revocation_reason == "compromised"

    def test_apply_api_key_revoked_for_unknown_key_is_noop(self):
        """ApiKeyRevoked for unknown key_id does not crash."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            ApiKeyRevoked(
                key_id="unknown",
                principal_id="svc-x",
                revoked_by="admin",
                reason="",
            )
        )
        assert proj.get_key_by_id("unknown") is None

    def test_apply_role_assigned(self):
        """Lines 160-184: apply RoleAssigned event."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        event = RoleAssigned(
            principal_id="svc-3",
            role_name="admin",
            scope="global",
            assigned_by="system",
        )
        proj.apply(event)

        assignments = proj.get_roles_for_principal("svc-3")
        assert len(assignments) == 1
        assert assignments[0].role_name == "admin"

    def test_apply_role_assigned_idempotent(self):
        """Lines 175-183: duplicate assignment is ignored."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        event = RoleAssigned(
            principal_id="svc-idem",
            role_name="viewer",
            scope="global",
            assigned_by="system",
        )
        proj.apply(event)
        proj.apply(event)

        assignments = proj.get_roles_for_principal("svc-idem")
        assert len(assignments) == 1

    def test_apply_role_revoked(self):
        """Lines 186-194: apply RoleRevoked event."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            RoleAssigned(
                principal_id="svc-4",
                role_name="viewer",
                scope="global",
                assigned_by="system",
            )
        )
        proj.apply(
            RoleRevoked(
                principal_id="svc-4",
                role_name="viewer",
                scope="global",
                revoked_by="admin",
            )
        )

        assignments = proj.get_roles_for_principal("svc-4")
        assert len(assignments) == 0

    def test_apply_role_revoked_for_unknown_principal_is_noop(self):
        """RoleRevoked for unknown principal does not crash."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            RoleRevoked(
                principal_id="nobody",
                role_name="admin",
                scope="global",
                revoked_by="system",
            )
        )

    def test_get_keys_for_principal(self):
        """Lines 205-209: get keys for a principal."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            ApiKeyCreated(
                key_id="kid-a",
                principal_id="svc-5",
                key_name="key-a",
                expires_at=None,
                created_by="admin",
            )
        )
        proj.apply(
            ApiKeyCreated(
                key_id="kid-b",
                principal_id="svc-5",
                key_name="key-b",
                expires_at=None,
                created_by="admin",
            )
        )

        keys = proj.get_keys_for_principal("svc-5")
        assert len(keys) == 2

    def test_get_active_key_count(self):
        """Lines 211-214: count active (non-revoked) keys."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            ApiKeyCreated(
                key_id="kid-c",
                principal_id="svc-6",
                key_name="key-c",
                expires_at=None,
                created_by="admin",
            )
        )
        proj.apply(
            ApiKeyCreated(
                key_id="kid-d",
                principal_id="svc-6",
                key_name="key-d",
                expires_at=None,
                created_by="admin",
            )
        )
        proj.apply(
            ApiKeyRevoked(
                key_id="kid-c",
                principal_id="svc-6",
                revoked_by="admin",
                reason="",
            )
        )

        assert proj.get_active_key_count("svc-6") == 1

    def test_has_role_with_wildcard_scope(self):
        """Lines 221-228: has_role with scope='*'."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            RoleAssigned(
                principal_id="svc-7",
                role_name="developer",
                scope="tenant:abc",
                assigned_by="system",
            )
        )

        assert proj.has_role("svc-7", "developer") is True
        assert proj.has_role("svc-7", "developer", scope="*") is True
        assert proj.has_role("svc-7", "admin") is False

    def test_has_role_with_specific_scope(self):
        """has_role with specific scope matches global too."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            RoleAssigned(
                principal_id="svc-8",
                role_name="viewer",
                scope="global",
                assigned_by="system",
            )
        )

        assert proj.has_role("svc-8", "viewer", scope="tenant:abc") is True

    def test_get_stats(self):
        """Lines 234-250: get projection statistics."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            ApiKeyCreated(
                key_id="kid-s1",
                principal_id="svc-s",
                key_name="stat-key",
                expires_at=None,
                created_by="admin",
            )
        )
        proj.apply(
            RoleAssigned(
                principal_id="svc-s",
                role_name="admin",
                scope="global",
                assigned_by="system",
            )
        )

        stats = proj.get_stats()
        assert stats["total_api_keys"] == 1
        assert stats["active_api_keys"] == 1
        assert stats["revoked_api_keys"] == 0
        assert stats["total_principals_with_keys"] == 1
        assert stats["total_role_assignments"] == 1
        assert stats["total_principals_with_roles"] == 1

    def test_catchup_without_event_store_returns_zero(self):
        """Lines 90-91: catchup with no event_store returns 0."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        assert proj.catchup() == 0

    def test_catchup_processes_events_from_store(self):
        """Lines 82-105: catchup reads from event store."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        mock_store = Mock(spec=IEventStore)
        event = ApiKeyCreated(
            key_id="kid-cu",
            principal_id="svc-cu",
            key_name="catchup-key",
            expires_at=None,
            created_by="system",
        )
        mock_store.read_all.return_value = iter([(1, "api_key:abc", event)])

        proj = AuthProjection(event_store=mock_store)
        count = proj.catchup()

        assert count == 1
        assert proj.get_key_by_id("kid-cu") is not None

    def test_apply_unrecognized_event_is_noop(self):
        """Lines 107-120: apply with unrecognized event type does nothing."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()

        class UnknownEvent(DomainEvent):
            def __init__(self):
                super().__init__()

        proj.apply(UnknownEvent())
        # Should not raise, stats should be empty
        stats = proj.get_stats()
        assert stats["total_api_keys"] == 0

    def test_api_key_created_with_expiry(self):
        """ApiKeyCreated event with expires_at set."""
        from mcp_hangar.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        future_ts = (datetime.now(UTC) + timedelta(days=30)).timestamp()
        proj.apply(
            ApiKeyCreated(
                key_id="kid-exp",
                principal_id="svc-exp",
                key_name="exp-key",
                expires_at=future_ts,
                created_by="admin",
            )
        )

        model = proj.get_key_by_id("kid-exp")
        assert model.expires_at is not None


class TestAuthAuditLog:
    """Tests for AuthAuditLog projection."""

    def test_apply_api_key_created_creates_entry(self):
        """Lines 282-293: audit entry for ApiKeyCreated."""
        from mcp_hangar.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(
            ApiKeyCreated(
                key_id="kid-al",
                principal_id="svc-al",
                key_name="audit-key",
                expires_at=None,
                created_by="admin",
            )
        )

        entries = log.query()
        assert len(entries) == 1
        assert entries[0]["event_type"] == "api_key_created"

    def test_apply_api_key_revoked_creates_entry(self):
        """Lines 295-305: audit entry for ApiKeyRevoked."""
        from mcp_hangar.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(
            ApiKeyRevoked(
                key_id="kid-rev",
                principal_id="svc-rev",
                revoked_by="admin",
                reason="test",
            )
        )

        entries = log.query()
        assert len(entries) == 1
        assert entries[0]["event_type"] == "api_key_revoked"

    def test_apply_role_assigned_creates_entry(self):
        """Lines 307-317: audit entry for RoleAssigned."""
        from mcp_hangar.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(
            RoleAssigned(
                principal_id="svc-ra",
                role_name="admin",
                scope="global",
                assigned_by="system",
            )
        )

        entries = log.query()
        assert len(entries) == 1
        assert entries[0]["event_type"] == "role_assigned"

    def test_apply_role_revoked_creates_entry(self):
        """Lines 319-329: audit entry for RoleRevoked."""
        from mcp_hangar.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(
            RoleRevoked(
                principal_id="svc-rr",
                role_name="admin",
                scope="global",
                revoked_by="system",
            )
        )

        entries = log.query()
        assert len(entries) == 1
        assert entries[0]["event_type"] == "role_revoked"

    def test_apply_unknown_event_returns_none(self):
        """Lines 331: unknown event returns None from _event_to_entry."""
        from mcp_hangar.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()

        class SomeOtherEvent(DomainEvent):
            def __init__(self):
                super().__init__()

        log.apply(SomeOtherEvent())
        entries = log.query()
        assert len(entries) == 0

    def test_query_filter_by_principal(self):
        """Lines 354-356: query with principal_id filter."""
        from mcp_hangar.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(ApiKeyCreated(key_id="k1", principal_id="svc-a", key_name="k", expires_at=None, created_by="admin"))
        log.apply(ApiKeyCreated(key_id="k2", principal_id="svc-b", key_name="k", expires_at=None, created_by="admin"))

        entries = log.query(principal_id="svc-a")
        assert len(entries) == 1
        assert entries[0]["details"]["key_id"] == "k1"

    def test_query_filter_by_event_type(self):
        """Lines 357-358: query with event_type filter."""
        from mcp_hangar.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(ApiKeyCreated(key_id="k1", principal_id="svc-a", key_name="k", expires_at=None, created_by="admin"))
        log.apply(RoleAssigned(principal_id="svc-a", role_name="admin", scope="global", assigned_by="system"))

        entries = log.query(event_type="role_assigned")
        assert len(entries) == 1
        assert entries[0]["event_type"] == "role_assigned"

    def test_query_filter_by_since(self):
        """Lines 359-360: query with since filter."""
        from mcp_hangar.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()

        event1 = ApiKeyCreated(key_id="k1", principal_id="svc-a", key_name="k", expires_at=None, created_by="admin")
        # Override occurred_at for predictable test
        event1.occurred_at = 1000.0
        log.apply(event1)

        event2 = ApiKeyCreated(key_id="k2", principal_id="svc-a", key_name="k", expires_at=None, created_by="admin")
        event2.occurred_at = 2000.0
        log.apply(event2)

        entries = log.query(since=1500.0)
        assert len(entries) == 1
        assert entries[0]["details"]["key_id"] == "k2"

    def test_query_limit(self):
        """Lines 363-364: query with limit."""
        from mcp_hangar.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        for i in range(10):
            log.apply(
                ApiKeyCreated(
                    key_id=f"k-{i}",
                    principal_id="svc-a",
                    key_name="k",
                    expires_at=None,
                    created_by="admin",
                )
            )

        entries = log.query(limit=3)
        assert len(entries) == 3

    def test_max_entries_trim(self):
        """Lines 277-278: entries are trimmed when over max."""
        from mcp_hangar.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog(max_entries=5)
        for i in range(10):
            log.apply(
                ApiKeyCreated(
                    key_id=f"k-{i}",
                    principal_id="svc-a",
                    key_name="k",
                    expires_at=None,
                    created_by="admin",
                )
            )

        entries = log.query(limit=100)
        assert len(entries) == 5
