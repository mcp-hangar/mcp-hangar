"""The identity attributes the governance enrichment boundary sets (#1278).

These replace the decorator tests deleted with `TracedMcpServerService`. Those
asserted span attributes on a class nothing in `src/` constructed, which is the
dead-wiring ADR-029 retires: they passed for years while the real call path
emitted none of it.

What they cannot do is prove the boundary sees a real caller's identity over a
real transport -- the contextvar is bound by ASGI middleware and re-bound per
surface, and a mock context proves nothing about either. That is
`tests/live/test_t3_governance_identity.py`, which the acceptance criteria of
#1278 require and this file deliberately does not stand in for.

The caller's own identifiers are opt-in (#1276, #1580): the tests that expect
them take the ``caller_ids_opted_in`` fixture, and the default is pinned by
``test_caller_ids_are_off_spans_by_default``.
"""

from __future__ import annotations

import pytest

from mcp_hangar.context import identity_context_var, set_fallback_identity
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.observability import tracing
from mcp_hangar.observability.conventions import MCP, Caller
from mcp_hangar.server.tools.batch.executor import _identity_span_attributes


@pytest.fixture
def bound_identity():
    """Bind an identity for the duration of one test, and unbind it after."""
    tokens = []

    def _bind(identity: IdentityContext | None) -> None:
        tokens.append(identity_context_var.set(identity))

    yield _bind
    for token in reversed(tokens):
        identity_context_var.reset(token)


@pytest.fixture
def caller_ids_opted_in():
    """The operator set ``observability.tracing.caller_ids: true``."""
    tracing.set_caller_ids_on_spans(True)
    yield
    tracing.set_caller_ids_on_spans(False)


#: The keys that name the caller, and are off spans unless opted in.
CALLER_ID_KEYS = (Caller.ID, MCP.USER_ID, MCP.AGENT_ID, MCP.SESSION_ID)


def _identity(**kwargs) -> IdentityContext:
    caller = CallerIdentity(
        user_id=kwargs.pop("user_id", None),
        agent_id=kwargs.pop("agent_id", None),
        session_id=kwargs.pop("session_id", None),
        principal_type=kwargs.pop("principal_type", "anonymous"),
        tenant_id=kwargs.pop("tenant_id", None),
    )
    return IdentityContext(caller=caller, correlation_id=kwargs.pop("correlation_id", None))


def test_no_bound_identity_sets_nothing(bound_identity) -> None:
    bound_identity(None)
    set_fallback_identity(None)

    assert _identity_span_attributes() == {}


def test_caller_ids_are_off_spans_by_default(bound_identity) -> None:
    """#1276 decision 1: tenant and principal type on spans, user, agent and session ids only on opt-in."""
    assert tracing.caller_ids_on_spans() is False
    bound_identity(
        _identity(
            user_id="u-1",
            agent_id="a-1",
            session_id="s-1",
            principal_type="user",
            tenant_id="acme",
            correlation_id="c-1",
        )
    )

    assert _identity_span_attributes() == {
        Caller.TYPE: "user",
        Caller.TENANT: "acme",
        MCP.CORRELATION_ID: "c-1",
    }


def test_every_known_field_reaches_its_convention_key(bound_identity, caller_ids_opted_in) -> None:
    bound_identity(
        _identity(
            user_id="u-1",
            agent_id="a-1",
            session_id="s-1",
            principal_type="user",
            tenant_id="acme",
            correlation_id="c-1",
        )
    )

    assert _identity_span_attributes() == {
        Caller.TYPE: "user",
        Caller.ID: "u-1",
        Caller.TENANT: "acme",
        MCP.USER_ID: "u-1",
        MCP.AGENT_ID: "a-1",
        MCP.SESSION_ID: "s-1",
        MCP.CORRELATION_ID: "c-1",
    }


def test_unknown_values_are_omitted_not_exported_empty(bound_identity) -> None:
    """An absent tenant must not become `mcp.caller.tenant_id=""`.

    An empty attribute is worse than a missing one: a query for the calls that
    had a tenant would select every call ever made, and the operator would not
    see that it had.
    """
    bound_identity(_identity(principal_type="anonymous"))

    attributes = _identity_span_attributes()

    assert attributes == {Caller.TYPE: "anonymous"}
    assert Caller.TENANT not in attributes
    assert MCP.SESSION_ID not in attributes


def test_caller_id_falls_back_to_the_agent_when_there_is_no_user(bound_identity, caller_ids_opted_in) -> None:
    bound_identity(_identity(agent_id="a-1", principal_type="service", user_id="svc"))
    assert _identity_span_attributes()[Caller.ID] == "svc"

    bound_identity(_identity(agent_id="a-1"))
    assert _identity_span_attributes()[Caller.ID] == "a-1"


def test_baggage_is_not_a_source(bound_identity, caller_ids_opted_in) -> None:
    """A caller id in baggage is a claim nothing authenticated; it must not be read.

    Anything on the path can write baggage, including the caller. If the
    boundary read it, a tenant label on an exported span -- the thing quota and
    audit queries are keyed on -- would be attacker-chosen.
    """
    pytest.importorskip("opentelemetry.baggage")
    from opentelemetry import baggage
    from opentelemetry import context as otel_context

    bound_identity(None)
    set_fallback_identity(None)
    token = otel_context.attach(
        baggage.set_baggage("mcp.caller.tenant_id", "attacker", baggage.set_baggage("mcp.caller.id", "attacker"))
    )
    try:
        assert _identity_span_attributes() == {}
    finally:
        otel_context.detach(token)


def test_the_stdio_fallback_identity_is_read(bound_identity) -> None:
    """ADR-026: a stdio session's declared principal is bound process-wide, not per request."""
    bound_identity(None)
    set_fallback_identity(_identity(user_id="declared", principal_type="user", tenant_id="acme"))
    try:
        assert _identity_span_attributes()[Caller.TENANT] == "acme"
        assert not set(CALLER_ID_KEYS) & set(_identity_span_attributes())
        tracing.set_caller_ids_on_spans(True)
        assert _identity_span_attributes()[Caller.ID] == "declared"
    finally:
        tracing.set_caller_ids_on_spans(False)
        set_fallback_identity(None)
