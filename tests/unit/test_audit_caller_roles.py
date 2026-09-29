"""A tool call's audit record names what authorized it, and nothing else does (#1347).

``mcp.caller.roles`` was defined without a writer. The ``tool:invoke`` check in
``_authorize_calls`` discarded its decision, so the role that admitted a call
was known for an instant and then lost. It is now read from that decision --
the matched role, or ``opa_policy`` for a call an OPA policy admitted without
one -- carried on the call, and set on a copy of the caller's identity in the
call's worker, which is where the invocation events and a gate's
``ToolCallRefused`` read the caller from.

Nothing is looked up a second time and nothing is invented: auth off, a
``tool:invoke`` denial, or a decision that names no role leaves the record
without the attribute. It is never put on a span (#1276, #1580).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar.application.event_handlers.audit_event_handler import OTLPAuditEventHandler
from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.opa_authorizer import CombinedAuthorizer, OPAAuthorizer
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.compliance import CEFExporter, JSONLinesExporter, LEEFExporter, SyslogExporter
from mcp_hangar.context import get_identity_context, identity_context_var
from mcp_hangar.domain.contracts.authorization import AuthorizationResult
from mcp_hangar.domain.events import ToolCallRefused, ToolInvocationCompleted, ToolInvocationFailed
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter
from mcp_hangar.observability.conventions import Caller
from mcp_hangar.server.tools import batch
from mcp_hangar.server.tools.batch import OPA_POLICY_ROLE, _authorize_calls, _run_calls
from tests.unit.test_batch_gate_precedence import (
    _SERVER,
    _TENANT,
    _TOOL,
    _arrange_catalogue,
    _arrange_tool_access_denied,
    _reset_singletons,  # noqa: F401 -- autouse fixture
    ctx,  # noqa: F401 -- fixture
)

_PRINCIPAL = "user:alice"


def _principal() -> Principal:
    return Principal(id=PrincipalId(_PRINCIPAL), type=PrincipalType.USER, tenant_id=_TENANT)


def _identity() -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=_PRINCIPAL, agent_id=None, session_id=None, principal_type="user", tenant_id=_TENANT
        ),
        correlation_id="corr-1",
    )


def _rbac(role: str | None) -> RBACAuthorizer:
    store = InMemoryRoleStore()
    if role is not None:
        store.assign_role(principal_id=_PRINCIPAL, role_name=role)
    return RBACAuthorizer(store)


def _opa(allowed: bool) -> OPAAuthorizer:
    """The real OPA authorizer over an HTTP client that answers *allowed*."""
    opa = OPAAuthorizer(opa_url="http://opa.invalid")
    response = Mock(json=Mock(return_value={"result": allowed}), raise_for_status=Mock())
    opa._client = Mock(post=Mock(return_value=response))
    return opa


def _components(authorizer: Any, *, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(enabled=enabled, authz_middleware=AuthorizationMiddleware(authorizer=authorizer))


def _authorize(authorizer: Any, *, enabled: bool = True) -> tuple[dict[int, Any], dict[int, tuple[str, ...]], Mock]:
    app = Mock()
    app.auth_components = _components(authorizer, enabled=enabled)
    roles: dict[int, tuple[str, ...]] = {}
    calls = [{"mcp_server": _SERVER, "tool": _TOOL, "arguments": {}}]
    with patch("mcp_hangar.server.tools.batch.get_context", return_value=app):
        denied = _authorize_calls(calls, ["c-1"], _principal(), "b", identity=_identity(), authorizing_roles=roles)
    return denied, roles, app.event_bus


class _Fixed:
    """An authorizer that returns one decision, to reach what no shipped authorizer returns."""

    def __init__(self, decision: AuthorizationResult) -> None:
        self._decision = decision

    def authorize(self, _request: Any) -> AuthorizationResult:
        return self._decision


class TestTheDecisionNamesWhatAuthorized:
    def test_an_rbac_allow_names_the_matched_role(self) -> None:
        denied, roles, _bus = _authorize(_rbac("developer"))

        assert (denied, roles) == ({}, {0: ("developer",)})

    def test_an_opa_allow_names_the_policy(self) -> None:
        denied, roles, _bus = _authorize(_opa(True))

        assert (denied, roles) == ({}, {0: (OPA_POLICY_ROLE,)})
        assert OPA_POLICY_ROLE == "opa_policy"

    @pytest.mark.parametrize("require_both", [False, True], ids=["rbac-first", "require-both"])
    def test_a_combined_allow_names_the_rbac_role(self, require_both: bool) -> None:
        authorizer = CombinedAuthorizer(_rbac("developer"), _opa(True), require_both=require_both)

        assert _authorize(authorizer)[1] == {0: ("developer",)}

    def test_an_opa_override_of_an_rbac_denial_names_the_policy(self) -> None:
        """RBAC found no role; OPA admitted the call. The policy authorized it, and no role did."""
        authorizer = CombinedAuthorizer(_rbac(None), _opa(True), require_both=False)

        assert _authorize(authorizer)[1] == {0: (OPA_POLICY_ROLE,)}

    def test_with_auth_off_nothing_is_recorded(self) -> None:
        denied, roles, _bus = _authorize(_rbac("developer"), enabled=False)

        assert (denied, roles) == ({}, {})

    def test_a_tool_invoke_denial_records_no_role(self) -> None:
        denied, roles, bus = _authorize(_rbac("viewer"))

        assert list(denied) == [0] and roles == {}
        [event] = [c.args[0] for c in bus.publish.call_args_list if isinstance(c.args[0], ToolCallRefused)]
        assert event.identity_context is not None and event.identity_context["roles"] == []
        assert Caller.ROLES not in _otlp_record(event)

    @pytest.mark.parametrize(
        "decision",
        [AuthorizationResult.allow(reason="system_principal"), AuthorizationResult.allow(reason="opa_policy_like")],
        ids=["no-role", "other-reason"],
    )
    def test_a_decision_that_names_nothing_records_nothing(self, decision: AuthorizationResult) -> None:
        assert _authorize(_Fixed(decision))[1] == {}


# --- through `_run_calls`: the decision, the worker, the events ------------------


@pytest.fixture
def served(ctx) -> Iterator[Any]:  # noqa: F811 -- the fixture imported above
    """`_run_calls` over the gate fixture's healthy server, with RBAC binding `developer`."""
    ctx.auth_components = _components(_rbac("developer"))
    seen: list[IdentityContext | None] = []

    def dispatched(*_a: Any, **_k: Any) -> dict[str, Any]:
        seen.append(get_identity_context())
        return {"ok": True}

    ctx.command_bus.send.side_effect = dispatched
    ctx.seen = seen
    with patch("mcp_hangar.server.tools.batch.get_context", return_value=ctx):
        yield ctx


def _call(**kwargs: Any) -> dict[str, Any]:
    return _run_calls(
        [{"mcp_server": _SERVER, "tool": _TOOL, "arguments": {}}],
        principal=_principal(),
        identity=_identity(),
        request_ctx=None,
        max_concurrency=1,
        timeout=30.0,
        fail_fast=False,
        max_attempts=1,
        **kwargs,
    )


class TestTheRoleReachesTheCall:
    def test_the_dispatched_call_runs_under_an_identity_naming_the_role(self, served) -> None:
        _arrange_catalogue()

        assert _call()["success"] is True
        [identity] = served.seen
        assert identity is not None and identity.caller.roles == ("developer",)
        assert identity.caller.user_id == _PRINCIPAL and identity.caller.tenant_id == _TENANT

    def test_the_batch_identity_is_copied_not_changed(self, served) -> None:
        _arrange_catalogue()
        bound = _identity()
        token = identity_context_var.set(bound)
        try:
            _call()
            assert get_identity_context() is bound and bound.caller.roles == ()
        finally:
            identity_context_var.reset(token)

    def test_a_gate_refusal_after_authorization_names_the_role(self, served) -> None:
        _arrange_catalogue()
        _arrange_tool_access_denied()

        assert _call()["success"] is False
        [event] = [c.args[0] for c in served.event_bus.publish.call_args_list if isinstance(c.args[0], ToolCallRefused)]
        assert event.gate == "tool_access"
        assert event.identity_context is not None and event.identity_context["roles"] == ["developer"]
        assert _otlp_record(event)[Caller.ROLES] == "developer"

    def test_with_auth_off_the_call_carries_no_role(self, served) -> None:
        served.auth_components = _components(_rbac("developer"), enabled=False)
        _arrange_catalogue()

        assert _call()["success"] is True
        [identity] = served.seen
        assert identity is not None and identity.caller.roles == ()

    def test_no_span_carries_the_roles(self, served, monkeypatch) -> None:
        """Not even with caller ids opted onto spans: that opt-in names ids, not roles (#1580)."""
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from mcp_hangar.observability.tracing import _TextFreeTracer

        memory = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(memory))
        tracer = _TextFreeTracer(provider.get_tracer("test"))
        monkeypatch.setattr("mcp_hangar.server.tools.batch.executor.caller_ids_on_spans", lambda: True)
        monkeypatch.setattr("mcp_hangar.server.tools.batch.executor.get_tracer", lambda *_a: tracer)
        monkeypatch.setattr(batch, "get_tracer", lambda *_a: tracer)
        _arrange_catalogue()

        _call()
        _arrange_tool_access_denied()
        _call()

        spans = memory.get_finished_spans()
        assert any(s.name.startswith("batch.call.") for s in spans), [s.name for s in spans]
        assert any((s.attributes or {}).get("mcp.user.id") == _PRINCIPAL for s in spans), "the opt-in took effect"
        assert not [s.name for s in spans if Caller.ROLES in (s.attributes or {})]


# --- the audit handler and the exporters -----------------------------------------


def _identity_dict(roles: tuple[str, ...]) -> dict[str, Any]:
    return _identity().with_roles(roles).to_dict()


def _events(identity: dict[str, Any] | None) -> list[object]:
    return [
        ToolInvocationCompleted(mcp_server_id=_SERVER, tool_name=_TOOL, duration_ms=1.0, identity_context=identity),
        ToolInvocationFailed(
            mcp_server_id=_SERVER, tool_name=_TOOL, duration_ms=1.0, error_type="tool_error", identity_context=identity
        ),
        ToolCallRefused(
            mcp_server_id=_SERVER,
            tool_name=_TOOL,
            correlation_id="c-1",
            elapsed_ms=1.0,
            gate="tool_access",
            gate_reason="tool_not_in_access_policy",
            identity_context=identity,
        ),
    ]


_EVENT_IDS = ["success", "error", "denied"]


def _otlp_record(event: object) -> dict[str, Any]:
    exporter = OTLPAuditExporter()
    with patch.object(exporter, "_emit_log_record") as emit:
        OTLPAuditEventHandler(audit_exporter=exporter).handle(event)
    [call] = emit.call_args_list
    return dict(call.args[0])


class TestTheHandlerPassesTheRoles:
    @pytest.mark.parametrize("event", _events(_identity_dict(("developer",))), ids=_EVENT_IDS)
    def test_every_record_kind_passes_them(self, event) -> None:
        exporter = MagicMock()
        OTLPAuditEventHandler(audit_exporter=exporter).handle(event)

        assert exporter.export_tool_invocation.call_args.kwargs["caller_roles"] == "developer"
        assert _otlp_record(event)[Caller.ROLES] == "developer"

    @pytest.mark.parametrize("event", _events(_identity_dict(())) + _events(None), ids=[*_EVENT_IDS, *_EVENT_IDS])
    def test_without_roles_the_attribute_is_absent(self, event) -> None:
        exporter = MagicMock()
        OTLPAuditEventHandler(audit_exporter=exporter).handle(event)

        assert exporter.export_tool_invocation.call_args.kwargs["caller_roles"] is None
        assert Caller.ROLES not in _otlp_record(event)

    def test_an_event_persisted_before_roles_existed_reads_as_none(self) -> None:
        legacy = {k: v for k, v in _identity_dict(()).items() if k != "roles"}
        exporter = MagicMock()
        OTLPAuditEventHandler(audit_exporter=exporter).handle(_events(legacy)[0])

        assert exporter.export_tool_invocation.call_args.kwargs["caller_roles"] is None


def _json_roles(line: str) -> Any:
    return json.loads(line).get("caller_roles")


#: format -> (exporter, what its line holds for role `developer`), each in the format's own convention.
COMPLIANCE: dict[str, tuple[Callable[..., Any], str]] = {
    "cef": (CEFExporter, "spriv=developer"),
    "leef": (LEEFExporter, "\trole=developer"),
    "syslog": (SyslogExporter, 'roles="developer"'),
}


class TestComplianceLinesCarryTheRoles:
    @pytest.mark.parametrize("fmt", sorted(COMPLIANCE))
    @pytest.mark.parametrize("event", _events(_identity_dict(("developer",))), ids=_EVENT_IDS)
    def test_the_line_names_the_role(self, fmt: str, event) -> None:
        factory, field = COMPLIANCE[fmt]
        lines: list[str] = []
        OTLPAuditEventHandler(audit_exporter=factory(output_fn=lines.append)).handle(event)

        [line] = lines
        assert field in line, line

    @pytest.mark.parametrize("fmt", sorted(COMPLIANCE))
    @pytest.mark.parametrize("event", _events(_identity_dict(())), ids=_EVENT_IDS)
    def test_without_roles_the_line_names_none(self, fmt: str, event) -> None:
        factory, field = COMPLIANCE[fmt]
        lines: list[str] = []
        OTLPAuditEventHandler(audit_exporter=factory(output_fn=lines.append)).handle(event)

        [line] = lines
        assert field.split("=")[0] + "=" not in line, line

    @pytest.mark.parametrize("event", _events(_identity_dict(("developer",))), ids=_EVENT_IDS)
    def test_the_json_line_names_the_role(self, event) -> None:
        lines: list[str] = []
        OTLPAuditEventHandler(audit_exporter=JSONLinesExporter(output_fn=lines.append)).handle(event)

        assert _json_roles(lines[0]) == "developer"

    @pytest.mark.parametrize("event", _events(_identity_dict(())), ids=_EVENT_IDS)
    def test_without_roles_the_json_line_has_none(self, event) -> None:
        lines: list[str] = []
        OTLPAuditEventHandler(audit_exporter=JSONLinesExporter(output_fn=lines.append)).handle(event)

        assert "caller_roles" not in json.loads(lines[0])

    def test_a_policy_admitted_call_reads_the_same_in_every_format(self) -> None:
        event = _events(_identity_dict((OPA_POLICY_ROLE,)))[0]
        lines: list[str] = []
        for factory in (CEFExporter, LEEFExporter, SyslogExporter, JSONLinesExporter):
            OTLPAuditEventHandler(audit_exporter=factory(output_fn=lines.append)).handle(event)

        assert all(OPA_POLICY_ROLE in line for line in lines), lines


# Where `mcp.caller.roles` may appear: its definition as a literal, and one read
# of `Caller.ROLES`, by the exporter that puts it on the audit record. Any other
# read -- a span helper, an enrichment boundary -- could put a caller's roles
# on a span (#1347, #1628).
_ROLES_USES = {
    ("observability/conventions.py", "literal"),
    ("infrastructure/observability/otlp_audit_exporter.py", "Caller.ROLES"),
}


def test_only_the_audit_exporter_reads_the_caller_roles_attribute() -> None:
    import ast
    from pathlib import Path

    import mcp_hangar

    root = Path(mcp_hangar.__file__).parent
    found = set()
    for source in sorted(root.rglob("*.py")):
        where = source.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr == "ROLES":
                found.add((where, "Caller.ROLES"))
            elif isinstance(node, ast.Constant) and node.value == Caller.ROLES:
                found.add((where, "literal"))
    assert found == _ROLES_USES, found
