"""Tests for inbound stateless protocol negotiation (SEP-2575, issue #291).

Hangar reads the client's ``protocolVersion``/``capabilities`` from
``params._meta`` per request (no ``initialize`` handshake) and exposes them via a
request-scoped contextvar. These tests pin the parse and fail-safe behaviour.
"""

from __future__ import annotations

import contextvars

from mcp_hangar.negotiation import (
    get_current_protocol_negotiation,
    ProtocolNegotiation,
    read_protocol_negotiation,
    set_current_protocol_negotiation,
)
from mcp_hangar.protocol import (
    _META_CAPABILITIES_KEY,
    _META_PROTOCOL_VERSION_KEY,
    SUPPORTED_PROTOCOL_VERSION,
)


def test_reads_version_and_capabilities_when_present() -> None:
    meta = {
        _META_PROTOCOL_VERSION_KEY: "2026-07-28",
        _META_CAPABILITIES_KEY: {"sampling": {}, "roots": {"listChanged": True}},
    }

    negotiation = read_protocol_negotiation(meta)

    assert negotiation.protocol_version == "2026-07-28"
    assert dict(negotiation.capabilities) == {"sampling": {}, "roots": {"listChanged": True}}


def test_client_version_overrides_default() -> None:
    negotiation = read_protocol_negotiation({_META_PROTOCOL_VERSION_KEY: "2099-01-01"})

    assert negotiation.protocol_version == "2099-01-01"
    assert dict(negotiation.capabilities) == {}


def test_none_meta_defaults_fail_safe() -> None:
    negotiation = read_protocol_negotiation(None)

    assert negotiation.protocol_version == SUPPORTED_PROTOCOL_VERSION
    assert dict(negotiation.capabilities) == {}


def test_empty_meta_defaults_fail_safe() -> None:
    negotiation = read_protocol_negotiation({})

    assert negotiation.protocol_version == SUPPORTED_PROTOCOL_VERSION
    assert dict(negotiation.capabilities) == {}


def test_garbage_meta_does_not_raise_and_defaults() -> None:
    # Wrong types for both keys, plus an unrelated key -- must not raise.
    meta = {
        _META_PROTOCOL_VERSION_KEY: 12345,  # not a str
        _META_CAPABILITIES_KEY: ["not", "a", "mapping"],
        "unrelated": object(),
    }

    negotiation = read_protocol_negotiation(meta)

    assert negotiation.protocol_version == SUPPORTED_PROTOCOL_VERSION
    assert dict(negotiation.capabilities) == {}


def test_blank_version_string_falls_back_to_default() -> None:
    negotiation = read_protocol_negotiation({_META_PROTOCOL_VERSION_KEY: ""})

    assert negotiation.protocol_version == SUPPORTED_PROTOCOL_VERSION


def test_negotiation_is_frozen() -> None:
    negotiation = ProtocolNegotiation()
    try:
        negotiation.protocol_version = "mutated"  # type: ignore[misc]
    except Exception:  # noqa: BLE001 -- frozen dataclass raises FrozenInstanceError
        pass
    else:
        raise AssertionError("ProtocolNegotiation should be immutable (frozen)")


def test_contextvar_round_trips() -> None:
    def _in_scope() -> None:
        negotiation = read_protocol_negotiation(
            {
                _META_PROTOCOL_VERSION_KEY: "2026-07-28",
                _META_CAPABILITIES_KEY: {"sampling": {}},
            }
        )
        set_current_protocol_negotiation(negotiation)

        current = get_current_protocol_negotiation()
        assert current is not None
        assert current.protocol_version == "2026-07-28"
        assert dict(current.capabilities) == {"sampling": {}}

    # Run in an isolated context so the set does not leak across tests.
    contextvars.copy_context().run(_in_scope)


def test_contextvar_inherited_by_copy_context() -> None:
    negotiation = read_protocol_negotiation({_META_PROTOCOL_VERSION_KEY: "2026-07-28"})

    def _outer() -> None:
        set_current_protocol_negotiation(negotiation)

        # A snapshot taken after the set (mirrors batch worker copy_context()).
        def _worker() -> None:
            current = get_current_protocol_negotiation()
            assert current is not None
            assert current.protocol_version == "2026-07-28"

        contextvars.copy_context().run(_worker)

    contextvars.copy_context().run(_outer)
