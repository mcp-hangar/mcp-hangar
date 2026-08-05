"""Anything the system writes to the event store has to be readable back.

`EventSerializer.serialize` works for any `DomainEvent` -- it just dumps the
instance dict. `deserialize` needs the class, and looks it up in a hand-curated
table. The two never agreed: the table held 30 of the 116 event classes in the
codebase, so 86 types could be appended to a stream and then not read back.

That is not theoretical. `auth.storage.driver: event_sourcing` rebuilds API keys
and role assignments by replaying their streams, and all five events those
aggregates emit -- `ApiKeyCreated`, `ApiKeyRevoked`, `KeyRotated`,
`RoleAssigned`, `RoleRevoked` -- were missing from the table. Every API key
created under that driver was durably written and permanently unreadable: the
next process to open the database raised `EventSerializationError` on the first
read. The nine group events in `domain/model/mcp_server_group.py` were in the
same position, which is what the never-called `register_event_type` helper
existed to solve.

The first two test classes are the reproduction. The third is what keeps the
table from drifting out of agreement again, which is the actual defect -- a
curated list of everything is a list that will be incomplete.
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest

from mcp_hangar.auth.infrastructure.event_sourced_store import (
    EventSourcedApiKeyStore,
    EventSourcedRoleStore,
    _hash_key,
)
from mcp_hangar.domain.exceptions import RevokedCredentialsError
from mcp_hangar.domain import events as events_pkg
from mcp_hangar.domain.events import LEGACY_EVENT_TYPE_NAMES, DomainEvent
from mcp_hangar.infrastructure.persistence.event_serializer import EVENT_TYPE_MAP
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "events.db")


class TestApiKeysSurviveARestart:
    """The store is durable; before this fix it was also write-only."""

    def test_a_key_created_before_a_restart_is_still_listed_after_one(self, db_path):
        before = EventSourcedApiKeyStore(event_store=SQLiteEventStore(db_path), event_publisher=None)
        before.create_key(principal_id="svc:a", name="deploy-key")

        # A new store over the same database is what the next process does.
        after = EventSourcedApiKeyStore(event_store=SQLiteEventStore(db_path), event_publisher=None)
        keys = after.list_keys("svc:a")

        assert [key.name for key in keys] == ["deploy-key"]

    def test_the_key_still_authenticates_after_a_restart(self, db_path):
        """The failure mode in production terms: every credential stops working."""
        before = EventSourcedApiKeyStore(event_store=SQLiteEventStore(db_path), event_publisher=None)
        raw_key = before.create_key(principal_id="svc:a", name="deploy-key")

        after = EventSourcedApiKeyStore(event_store=SQLiteEventStore(db_path), event_publisher=None)
        principal = after.get_principal_for_key(_hash_key(raw_key))

        assert principal is not None and str(principal.id) == "svc:a"

    def test_a_revocation_survives_too(self, db_path):
        """A revoked key coming back to life after a restart is the worse direction."""
        before = EventSourcedApiKeyStore(event_store=SQLiteEventStore(db_path), event_publisher=None)
        raw_key = before.create_key(principal_id="svc:a", name="deploy-key")
        key_id = before.list_keys("svc:a")[0].key_id
        before.revoke_key(key_id, revoked_by="admin")

        after = EventSourcedApiKeyStore(event_store=SQLiteEventStore(db_path), event_publisher=None)

        with pytest.raises(RevokedCredentialsError):
            after.get_principal_for_key(_hash_key(raw_key))

    def test_the_count_is_right_after_a_restart(self, db_path):
        before = EventSourcedApiKeyStore(event_store=SQLiteEventStore(db_path), event_publisher=None)
        for index in range(3):
            before.create_key(principal_id="svc:a", name=f"key-{index}")

        after = EventSourcedApiKeyStore(event_store=SQLiteEventStore(db_path), event_publisher=None)

        assert after.count_keys("svc:a") == 3


class TestRoleAssignmentsSurviveARestart:
    def test_an_assigned_role_is_still_assigned_after_a_restart(self, db_path):
        before = EventSourcedRoleStore(event_store=SQLiteEventStore(db_path), event_publisher=None)
        before.assign_role(principal_id="svc:a", role_name="viewer", assigned_by="admin")

        after = EventSourcedRoleStore(event_store=SQLiteEventStore(db_path), event_publisher=None)

        assert "viewer" in [role.name for role in after.get_roles_for_principal("svc:a")]

    def test_a_revoked_role_stays_revoked_after_a_restart(self, db_path):
        """A privilege coming back after a restart is a security failure, not a bug."""
        before = EventSourcedRoleStore(event_store=SQLiteEventStore(db_path), event_publisher=None)
        before.assign_role(principal_id="svc:a", role_name="admin", assigned_by="admin")
        before.revoke_role(principal_id="svc:a", role_name="admin", revoked_by="admin")

        after = EventSourcedRoleStore(event_store=SQLiteEventStore(db_path), event_publisher=None)

        assert "admin" not in [role.name for role in after.get_roles_for_principal("svc:a")]


def _all_event_classes() -> dict[str, type[DomainEvent]]:
    """Every concrete event class, wherever it is defined.

    Includes `domain/model/mcp_server_group.py`, which defines nine of them
    outside the events package to avoid a circular import.
    """
    from mcp_hangar.domain.model import mcp_server_group

    found: dict[str, type[DomainEvent]] = {}
    for module in (events_pkg, mcp_server_group):
        for name, obj in vars(module).items():
            if not (inspect.isclass(obj) and issubclass(obj, DomainEvent) and obj is not DomainEvent):
                continue
            if obj.__name__ != name or name in LEGACY_EVENT_TYPE_NAMES:
                continue  # an alias, resolved to its canonical name before lookup
            found[name] = obj
    return found


class TestEverythingWritableIsAlsoReadable:
    """`serialize` accepts any event; `deserialize` must not accept fewer.

    A registry that lists what may be read back, maintained by hand against a
    writer that accepts everything, is a registry that will disagree with the
    writer -- and it did, for 86 of 116 types. The asymmetry is the defect, so
    this is the test that matters rather than the two reproductions above.
    """

    def test_every_event_class_can_be_deserialised(self):
        missing = sorted(set(_all_event_classes()) - set(EVENT_TYPE_MAP))
        assert missing == [], (
            f"{len(missing)} event classes can be written to a stream but not read back, "
            f"so replaying a stream containing one raises EventSerializationError: {missing}"
        )

    def test_the_registry_holds_no_aliases(self):
        """Legacy names resolve to canonical ones before lookup; entries for them are dead."""
        aliases = sorted(set(EVENT_TYPE_MAP) & set(LEGACY_EVENT_TYPE_NAMES))
        assert aliases == [], f"deprecated names registered directly: {aliases}"

    def test_every_registered_name_matches_its_class(self):
        mismatched = [name for name, cls in EVENT_TYPE_MAP.items() if cls.__name__ != name]
        assert mismatched == [], f"registered under a name that is not the class's own: {mismatched}"

    def test_the_registry_is_not_trivially_small(self):
        """Guards against a discovery bug that silently registers nothing.

        A floor, not a census. Coverage is guaranteed by
        `test_every_event_class_can_be_deserialised`, which derives the expected
        set from the class hierarchy and needs no maintenance; this only catches
        the registry coming up empty or near-empty. The number moved from 100
        when eight event classes with no producer and no consumer were deleted,
        which is a legitimate way for it to fall.
        """
        assert len(EVENT_TYPE_MAP) >= 50


class TestTheAggregateEventsSpecifically:
    """The five that broke authentication, named so a regression is legible."""

    @pytest.mark.parametrize(
        "event_type",
        ["ApiKeyCreated", "ApiKeyRevoked", "KeyRotated", "RoleAssigned", "RoleRevoked"],
    )
    def test_it_is_registered(self, event_type):
        assert event_type in EVENT_TYPE_MAP


def test_a_group_event_round_trips_through_the_store():
    """The case `register_event_type` was written for, and never called for."""
    from mcp_hangar.domain.model.mcp_server_group import GroupCreated
    from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer

    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteEventStore(str(Path(directory) / "events.db"))
        event = GroupCreated(group_id="g1", strategy="round_robin", min_healthy=1)
        store.append(stream_id="group:g1", events=[event], expected_version=-1)

        restored = list(store.read_stream("group:g1"))

    assert [type(item).__name__ for item in restored] == ["GroupCreated"]
    assert restored[0].group_id == "g1"
    _ = EventSerializer  # imported to make the dependency explicit in this test
