"""A secret in a tool call's arguments must not survive into the audit trail.

`approvals` has redacted the same dict in two passes since #1130, and its own
docstring says why: without it a secret "was written verbatim into the SQLite
approval record and served to every `approval:read` holder through the REST
DTO". `ToolInvocationRequested` carried the caller's arguments untouched down
two other exits -- the event store, and `/ws/events` under `audit:read` -- so
the same secret sat in the database for the retention of the log (#1168).

Redaction happens in the event's own `__post_init__`, which is what makes these
tests about the event rather than about one call site: there is no construction
path that keeps the values.
"""

from __future__ import annotations

import json

from mcp_hangar.domain.events import ToolInvocationRequested
from mcp_hangar.domain.security.argument_redaction import hash_arguments
from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer

# The two examples the approvals docstring uses to describe the leak it closed.
_JWT = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.e30.x"
_NESTED = {"password": "hunter2"}


def _event(arguments: dict) -> ToolInvocationRequested:
    return ToolInvocationRequested(mcp_server_id="s", tool_name="t", correlation_id="c", arguments=arguments)


class TestTheEventStore:
    def test_neither_shape_of_secret_survives_serialization(self):
        event = _event({"body": _JWT, "config": dict(_NESTED)})

        _, payload = EventSerializer().serialize(event)

        assert "hunter2" not in payload
        assert "eyJhbGciOiJIUzI1NiJ9" not in payload

    def test_the_websocket_payload_carries_neither(self):
        # `/ws/events` sends `json.dumps(event.to_dict())` for every domain event.
        event = _event({"body": _JWT, "config": dict(_NESTED)})

        payload = json.dumps(event.to_dict(), default=str)

        assert "hunter2" not in payload
        assert "eyJhbGciOiJIUzI1NiJ9" not in payload

    def test_the_ordinary_arguments_are_still_there(self):
        """Redaction is not deletion: an auditor still sees the shape of the call."""
        event = _event({"repo": "acme/widgets", "dry_run": True, "config": dict(_NESTED)})

        assert event.arguments["repo"] == "acme/widgets"
        assert event.arguments["dry_run"] is True
        assert event.arguments["config"]["password"] == "[REDACTED]"


class TestTheCallKeepsItsIdentity:
    def test_the_hash_is_over_the_raw_arguments(self):
        raw = {"body": _JWT, "config": dict(_NESTED)}

        event = _event(dict(raw))

        assert event.arguments_hash == hash_arguments(raw)

    def test_two_calls_with_different_secrets_do_not_hash_alike(self):
        # The property redaction would destroy if the hash were taken after it:
        # both of these redact to the same marker.
        first = _event({"token": "ghp_" + "a" * 36})
        second = _event({"token": "ghp_" + "b" * 36})

        assert first.arguments_hash != second.arguments_hash

    def test_a_stored_hash_is_not_recomputed_on_replay(self):
        """A row is rebuilt from redacted arguments; recomputing would rewrite it."""
        serializer = EventSerializer()
        original = _event({"body": _JWT})
        _, payload = serializer.serialize(original)

        restored = serializer.deserialize("ToolInvocationRequested", payload)

        assert restored.arguments_hash == original.arguments_hash

    def test_an_empty_payload_has_no_hash_to_carry(self):
        assert _event({}).arguments_hash == ""
