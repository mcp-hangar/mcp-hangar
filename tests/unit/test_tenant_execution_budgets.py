"""Per-tenant execution budgets (#1445): the bucket, the default entry, a reload, and where the executor takes it.

The budget is driven here with a clock the test moves, so a refill is exact.
The served path -- two tenants with two API keys, over streamable HTTP -- is
``tests/integration/test_tenant_budgets_on_the_served_app.py``.

Naming: neutral placeholders only (tenant:a, tenant:b, server_a, read_item).
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.server import config_schema
from mcp_hangar.server.config import apply_process_config, check_process_config
from mcp_hangar.server.config_serializer import serialize_execution_config
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec, tenant_admission
from mcp_hangar.server.tools.batch import executor as executor_module
from mcp_hangar.server.tools.batch.concurrency import reset_concurrency_manager
from mcp_hangar.server.tools.batch.models import CallResult
from mcp_hangar.server.tools.batch.tenant_admission import (
    CONCURRENCY,
    configure_tenant_limits,
    DEFAULT_BUDGET,
    get_tenant_admission,
    Grant,
    MAX_COUNT,
    MAX_RPS,
    NO_BUDGET,
    NO_ENTRY,
    parse_tenant_limits,
    RATE,
    Refusal,
    Reservation,
    reset_tenant_admission,
    TenantAdmission,
    TenantLimits,
)

A = "tenant:a"
B = "tenant:b"
#: A refill so slow that no test lives to see a token come back.
NEVER = 1e-9


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _limits(max_concurrency: int = 1, rps: float = 1.0, burst: int = 1) -> TenantLimits:
    return TenantLimits(max_concurrency=max_concurrency, rps=rps, burst=burst)


def _entry(max_concurrency: Any = 1, rps: Any = 1, burst: Any = 1) -> dict[str, Any]:
    return {"max_concurrency": max_concurrency, "rps": rps, "burst": burst}


def _granted(admission: TenantAdmission, tenant: str | None) -> Grant:
    granted = admission.admit(tenant)
    assert isinstance(granted, Grant), granted
    return granted


def _reserved(admission: TenantAdmission, tenant: str | None) -> Reservation:
    reserved = admission.reserve(tenant)
    assert isinstance(reserved, Reservation), reserved
    return reserved


@pytest.fixture
def budgets():
    """The process's budgets, forgotten afterwards with every call counted against them."""
    yield configure_tenant_limits
    reset_tenant_admission()


class TestTheConfigIsChecked:
    def test_each_entry_is_read(self) -> None:
        parsed = parse_tenant_limits({A: _entry(2, 5, 3), DEFAULT_BUDGET: _entry(1, 0.5, 1)})

        assert parsed == {A: TenantLimits(2, 5.0, 3), DEFAULT_BUDGET: TenantLimits(1, 0.5, 1)}

    @pytest.mark.parametrize("absent", [None, {}])
    def test_no_section_is_no_budget(self, absent: Any) -> None:
        assert parse_tenant_limits(absent) == {}

    @pytest.mark.parametrize(
        ("raw", "named"),
        [
            ([A], "mapping of tenant id to budget"),
            ({7: _entry()}, "quote a numeric id"),
            ({"": _entry()}, "non-empty string"),
            ({NO_ENTRY: _entry()}, "is reserved"),
            ({A: 3}, "must be a mapping"),
            ({A: {**_entry(), "max_concurency": 1}}, "unknown ['max_concurency']"),
            ({A: {"max_concurrency": 1, "rps": 1}}, "missing ['burst']"),
            ({A: _entry(max_concurrency=0)}, "max_concurrency"),
            ({A: _entry(max_concurrency=True)}, "max_concurrency"),
            ({A: _entry(max_concurrency=1.5)}, "max_concurrency"),
            ({A: _entry(max_concurrency="2")}, "max_concurrency"),
            ({A: _entry(max_concurrency=MAX_COUNT + 1)}, "max_concurrency"),
            ({A: _entry(max_concurrency=10**400)}, "max_concurrency"),
            ({A: _entry(burst=0)}, "burst"),
            ({A: _entry(burst=True)}, "burst"),
            ({A: _entry(burst=MAX_COUNT + 1)}, "burst"),
            ({A: _entry(burst=10**400)}, "burst"),
            ({A: _entry(rps=0)}, "rps"),
            ({A: _entry(rps=-1)}, "rps"),
            ({A: _entry(rps=math.nan)}, "rps"),
            ({A: _entry(rps=math.inf)}, "rps"),
            ({A: _entry(rps=True)}, "rps"),
            ({A: _entry(rps="5")}, "rps"),
            ({A: _entry(rps=MAX_RPS + 1)}, "rps"),
            ({A: _entry(rps=10**400)}, "rps"),
        ],
    )
    def test_a_bad_entry_is_refused_and_named(self, raw: Any, named: str) -> None:
        with pytest.raises(ValueError, match=re.escape(named)):
            parse_tenant_limits(raw)

    def test_the_process_check_refuses_it_before_anything_is_applied(self) -> None:
        with pytest.raises(ConfigurationError, match=r"execution\.tenant_limits"):
            check_process_config({"execution": {"tenant_limits": {A: {"rps": 1}}}})

    @pytest.mark.parametrize("key", ["max_concurrency", "burst", "rps"])
    def test_a_number_too_large_for_a_float_is_a_configuration_error(self, key: str) -> None:
        """Not an `OverflowError` from the check, and not one from every later call."""
        with pytest.raises(ConfigurationError, match=rf"execution\.tenant_limits: .*{key}"):
            check_process_config({"execution": {"tenant_limits": {A: {**_entry(), key: 10**400}}}})

    def test_the_largest_values_allowed_admit_a_call(self) -> None:
        admission = TenantAdmission(parse_tenant_limits({A: _entry(MAX_COUNT, MAX_RPS, MAX_COUNT)}))

        _granted(admission, A).release()

    def test_the_schema_knows_the_key(self) -> None:
        assert config_schema.validate_config({"execution": {"tenant_limits": {A: _entry()}}}) == []


class TestTheBucket:
    def test_with_no_budgets_every_caller_is_admitted_and_nothing_is_counted(self) -> None:
        admission = TenantAdmission()

        granted = [_granted(admission, tenant) for tenant in (A, A, B, None)]

        assert admission.in_flight(A) == 0
        for grant in granted:
            grant.release()
        _reserved(admission, A).refund()

    def test_a_tenant_at_its_concurrency_is_refused_and_another_is_not(self) -> None:
        admission = TenantAdmission({A: _limits(1, 100, 100), B: _limits(1, 100, 100)})
        held = _granted(admission, A)

        assert admission.admit(A) == Refusal(budget=A, reason=CONCURRENCY)
        _granted(admission, B)
        held.release()
        _granted(admission, A)

    def test_a_refusal_for_concurrency_spends_no_token(self) -> None:
        admission = TenantAdmission({A: _limits(max_concurrency=1, rps=NEVER, burst=2)})
        held = _granted(admission, A)

        assert admission.admit(A) == Refusal(budget=A, reason=CONCURRENCY)
        held.release()

        _granted(admission, A).release()  # the second token was still there
        assert admission.admit(A) == Refusal(budget=A, reason=RATE)

    def test_the_rate_budget_refills_with_time(self) -> None:
        clock = _Clock()
        admission = TenantAdmission({A: _limits(max_concurrency=10, rps=2, burst=1)}, clock=clock)
        _granted(admission, A).release()

        assert admission.admit(A) == Refusal(budget=A, reason=RATE)
        clock.now += 0.25  # half a token
        assert admission.admit(A) == Refusal(budget=A, reason=RATE)
        clock.now += 0.25
        _granted(admission, A)

    def test_a_refill_stops_at_the_burst(self) -> None:
        clock = _Clock()
        admission = TenantAdmission({A: _limits(max_concurrency=10, rps=1, burst=2)}, clock=clock)
        _granted(admission, A).release()
        clock.now += 1000

        _granted(admission, A).release()
        _granted(admission, A).release()
        assert admission.admit(A) == Refusal(budget=A, reason=RATE)

    def test_a_slot_is_given_back_once(self) -> None:
        admission = TenantAdmission({A: _limits(max_concurrency=2, rps=100, burst=100)})
        first = _granted(admission, A)
        _granted(admission, A)

        first.release()
        first.release()

        assert admission.in_flight(A) == 1

    @pytest.mark.parametrize("caller", [B, None], ids=["unlisted", "no tenant"])
    def test_without_a_default_entry_an_unlisted_caller_is_refused(self, caller: str | None) -> None:
        admission = TenantAdmission({A: _limits()})

        assert admission.admit(caller) == Refusal(budget=NO_ENTRY, reason=NO_BUDGET)

    def test_the_default_entry_gives_each_unlisted_tenant_a_budget_of_its_own(self) -> None:
        admission = TenantAdmission({DEFAULT_BUDGET: _limits(max_concurrency=1, rps=100, burst=100)})
        _granted(admission, "tenant:x")

        assert admission.admit("tenant:x") == Refusal(budget=DEFAULT_BUDGET, reason=CONCURRENCY)
        _granted(admission, "tenant:y")

    def test_callers_with_no_tenant_share_one_default_budget(self) -> None:
        admission = TenantAdmission({DEFAULT_BUDGET: _limits(max_concurrency=1, rps=100, burst=100)})
        _granted(admission, None)

        assert admission.admit(None) == Refusal(budget=DEFAULT_BUDGET, reason=CONCURRENCY)

    def test_a_listed_tenant_is_held_to_its_own_entry_not_the_default(self) -> None:
        admission = TenantAdmission({DEFAULT_BUDGET: _limits(1, 100, 100), A: _limits(2, 100, 100)})

        _granted(admission, A)
        _granted(admission, A)
        assert admission.admit(A) == Refusal(budget=A, reason=CONCURRENCY)

    def test_idle_full_budgets_are_dropped_once_many_are_kept_and_busy_ones_are_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tenant_admission, "_PRUNE_AT", 3)
        clock = _Clock()
        admission = TenantAdmission({DEFAULT_BUDGET: _limits(max_concurrency=1, rps=1, burst=1)}, clock=clock)
        busy = _granted(admission, "tenant:0")
        for tenant in ("tenant:1", "tenant:2"):
            _granted(admission, tenant).release()
        clock.now += 10  # every token back: tenant:1 and tenant:2 are now what a new budget is

        _granted(admission, "tenant:3")

        assert sorted(key for key in admission._budgets if key is not None) == ["tenant:0", "tenant:3"]
        assert admission.admit("tenant:0") == Refusal(budget=DEFAULT_BUDGET, reason=CONCURRENCY)
        busy.release()


class TestAReservation:
    """The token is taken first (before an approval hold), the slot later."""

    def test_a_reserved_token_is_spent_before_any_slot_is_taken(self) -> None:
        admission = TenantAdmission({A: _limits(max_concurrency=1, rps=NEVER, burst=1)})
        reserved = _reserved(admission, A)

        assert admission.reserve(A) == Refusal(budget=A, reason=RATE)
        assert admission.in_flight(A) == 0
        assert isinstance(reserved.grant(), Grant)
        assert admission.in_flight(A) == 1

    def test_a_refund_gives_the_token_back_once(self) -> None:
        admission = TenantAdmission({A: _limits(max_concurrency=10, rps=NEVER, burst=3)})
        first = _reserved(admission, A)
        _reserved(admission, A)

        first.refund()
        first.refund()

        # Two tokens left, not three: the second refund gave nothing back.
        _reserved(admission, A)
        _reserved(admission, A)
        assert admission.reserve(A) == Refusal(budget=A, reason=RATE)

    def test_a_slot_refused_at_the_grant_gives_the_token_back(self) -> None:
        admission = TenantAdmission({A: _limits(max_concurrency=1, rps=NEVER, burst=2)})
        held = _granted(admission, A)
        waiting = _reserved(admission, A)

        assert waiting.grant() == Refusal(budget=A, reason=CONCURRENCY)
        held.release()

        _granted(admission, A)  # the refused call's token

    def test_a_reservation_is_granted_once(self) -> None:
        admission = TenantAdmission({A: _limits(max_concurrency=10, rps=100, burst=100)})
        reserved = _reserved(admission, A)
        reserved.grant()

        with pytest.raises(RuntimeError, match="granted once"):
            reserved.grant()
        reserved.refund()  # does nothing
        assert admission.in_flight(A) == 1

    def test_an_entry_removed_while_the_call_waited_refuses_the_slot(self) -> None:
        admission = TenantAdmission({A: _limits(), B: _limits()})
        reserved = _reserved(admission, A)

        admission.configure({B: _limits()})

        assert reserved.grant() == Refusal(budget=NO_ENTRY, reason=NO_BUDGET)

    def test_budgets_turned_off_while_the_call_waited_admit_it_uncounted(self) -> None:
        admission = TenantAdmission({A: _limits()})
        reserved = _reserved(admission, A)

        admission.configure({})

        assert isinstance(reserved.grant(), Grant)
        assert admission.in_flight(A) == 0


class TestAReload:
    def test_unchanged_limits_keep_the_budget_its_calls_and_its_tokens(self) -> None:
        admission = TenantAdmission({A: _limits(max_concurrency=2, rps=NEVER, burst=3)})
        held = _granted(admission, A)
        budget = admission._budgets[A]

        admission.configure({A: _limits(max_concurrency=2, rps=NEVER, burst=3)})

        assert admission._budgets[A] is budget
        assert admission.in_flight(A) == 1
        second = _granted(admission, A)
        assert admission.admit(A) == Refusal(budget=A, reason=CONCURRENCY)
        held.release()
        second.release()
        # The reload refilled nothing: two of the three tokens stay spent.
        _granted(admission, A).release()
        assert admission.admit(A) == Refusal(budget=A, reason=RATE)

    def test_a_lowered_limit_keeps_counting_the_calls_in_flight(self) -> None:
        admission = TenantAdmission({A: _limits(max_concurrency=3, rps=100, burst=100)})
        held = [_granted(admission, A) for _ in range(3)]

        admission.configure({A: _limits(max_concurrency=1, rps=100, burst=100)})

        for grant in held[:2]:
            grant.release()
            assert admission.admit(A) == Refusal(budget=A, reason=CONCURRENCY)
        held[2].release()
        _granted(admission, A)

    def test_a_lowered_burst_caps_the_tokens_left(self) -> None:
        admission = TenantAdmission({A: _limits(max_concurrency=10, rps=NEVER, burst=5)})
        _granted(admission, A).release()

        admission.configure({A: _limits(max_concurrency=10, rps=NEVER, burst=2)})

        _granted(admission, A).release()
        _granted(admission, A).release()
        assert admission.admit(A) == Refusal(budget=A, reason=RATE)

    def test_a_removed_tenant_is_refused_and_a_call_still_running_releases_harmlessly(self) -> None:
        limits = _limits(max_concurrency=1, rps=100, burst=100)
        admission = TenantAdmission({A: limits, B: limits})
        running = _granted(admission, A)

        admission.configure({B: limits})
        assert admission.admit(A) == Refusal(budget=NO_ENTRY, reason=NO_BUDGET)
        running.release()

        admission.configure({A: limits, B: limits})
        assert admission.in_flight(A) == 0
        _granted(admission, A)

    def test_a_tenant_removed_and_added_back_still_counts_its_calls(self) -> None:
        limits = _limits(max_concurrency=1, rps=100, burst=100)
        admission = TenantAdmission({A: limits, B: limits})
        running = _granted(admission, A)

        admission.configure({B: limits})
        admission.configure({A: limits, B: limits})

        assert admission.admit(A) == Refusal(budget=A, reason=CONCURRENCY)
        running.release()
        _granted(admission, A)

    def test_a_removed_tenant_is_forgotten_once_its_calls_finish(self) -> None:
        admission = TenantAdmission({A: _limits(), B: _limits()})
        running = _granted(admission, A)
        admission.configure({B: _limits()})
        running.release()

        admission.configure({B: _limits()})

        assert A not in admission._budgets

    def test_a_tenant_moved_from_the_default_to_its_own_entry_keeps_its_calls(self) -> None:
        admission = TenantAdmission({DEFAULT_BUDGET: _limits(max_concurrency=1, rps=100, burst=100)})
        _granted(admission, A)

        admission.configure({DEFAULT_BUDGET: _limits(1, 100, 100), A: _limits(max_concurrency=1, rps=100, burst=100)})

        assert admission.admit(A) == Refusal(budget=A, reason=CONCURRENCY)

    def test_no_budgets_turns_admission_off(self) -> None:
        admission = TenantAdmission({A: _limits()})
        running = _granted(admission, A)

        admission.configure({})

        _granted(admission, B)
        running.release()


@pytest.fixture
def process_path():
    """What `apply_process_config` sets, put back afterwards."""
    yield
    reset_tenant_admission()
    reset_concurrency_manager()
    reset_tool_access_resolver()


@pytest.mark.usefixtures("process_path")
class TestTheProcessPath:
    """`apply_process_config` is what startup and every reload apply (#1424)."""

    CONFIG: dict[str, Any] = {"execution": {"tenant_limits": {A: _entry(max_concurrency=2, rps=NEVER, burst=3)}}}

    def test_a_reload_of_the_same_file_keeps_the_calls_in_flight_and_the_tokens_spent(self) -> None:
        apply_process_config(self.CONFIG)
        admission = get_tenant_admission()
        held = _granted(admission, A)

        apply_process_config(copy.deepcopy(self.CONFIG))

        assert admission.in_flight(A) == 1
        second = _granted(admission, A)
        assert admission.admit(A) == Refusal(budget=A, reason=CONCURRENCY)
        held.release()
        second.release()
        _granted(admission, A).release()
        assert admission.admit(A) == Refusal(budget=A, reason=RATE)

    def test_a_reload_without_the_section_removes_every_budget(self) -> None:
        apply_process_config(self.CONFIG)
        running = _granted(get_tenant_admission(), A)

        apply_process_config({})

        assert get_tenant_admission().limits == {}
        _granted(get_tenant_admission(), B)
        running.release()

    def test_a_reload_that_fails_leaves_the_budgets_in_force(self) -> None:
        apply_process_config(self.CONFIG)
        running = _granted(get_tenant_admission(), A)

        with pytest.raises(ConfigurationError):
            apply_process_config({"execution": {"tenant_limits": {A: {"rps": 1}}}})

        assert get_tenant_admission().limits == {A: TenantLimits(2, NEVER, 3)}
        assert get_tenant_admission().in_flight(A) == 1
        running.release()

    def test_an_export_writes_the_budgets_back(self) -> None:
        apply_process_config(self.CONFIG)

        exported = serialize_execution_config()["tenant_limits"]

        assert parse_tenant_limits(exported) == get_tenant_admission().limits


# --- where the executor takes it ------------------------------------------------

_SERVER = "server_a"
_TOOL = "read_item"
_OTHER = "other_item"
_TOO_FAST = "This tenant's execution budget is exhausted: calls started too fast"
_IN_FLIGHT = "This tenant's execution budget is exhausted: too many calls in flight"
_NO_BUDGET = "No execution budget is configured for this tenant"


def _identity(tenant_id: str | None) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


@pytest.fixture
def ctx(budgets):
    """A ready server whose every gate is open, the way `test_batch_gate_precedence.py` builds it."""
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    get_tool_projection_registry().build_from_tools(
        _SERVER, [ToolSchema(name=name, description=name, input_schema={}) for name in (_TOOL, _OTHER)]
    )
    context = Mock()
    context.command_bus.send.return_value = {"ok": True}
    context.governed_task_store = None
    context.approval_gate = None
    context.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"), has_tools=False, health=Mock(should_degrade=Mock(return_value=False))
    )
    context.mcp_server_exists.return_value = True
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=context),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=context),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as val_groups,
    ):
        exec_groups.get.return_value = None
        val_groups.get.return_value = None
        yield context
    reset_tool_projection_registry()
    reset_tool_access_resolver()


def _run(tenant: str | None, tool: str = _TOOL) -> CallResult:
    """One call by *tenant*, on the executor's worker pool, as `hangar_call` makes it."""
    token = identity_context_var.set(_identity(tenant))
    try:
        batch = BatchExecutor().execute(
            batch_id="b",
            calls=[CallSpec(index=0, call_id="c-1", mcp_server=_SERVER, tool=tool, arguments={})],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )
    finally:
        identity_context_var.reset(token)
    return batch.results[0]


def _sent(ctx: Mock) -> list[str]:
    return [type(call.args[0]).__name__ for call in ctx.command_bus.send.call_args_list]


class TestTheExecutor:
    def test_a_call_over_its_budget_is_refused_before_the_backend(self, ctx, budgets) -> None:
        budgets({A: _limits(max_concurrency=5, rps=NEVER, burst=1)})

        served = _run(A)
        refused = _run(A)

        assert served.success is True, served.error
        assert (refused.success, refused.error_type, refused.error) == (False, "TenantQuotaExceeded", _TOO_FAST)
        assert ctx.command_bus.send.call_count == 1

    def test_the_tenant_charged_is_the_callers_in_the_worker_thread(self, ctx, budgets) -> None:
        budgets({A: _limits(max_concurrency=5, rps=NEVER, burst=1), B: _limits(max_concurrency=5, rps=NEVER, burst=1)})
        _run(A)

        assert _run(A).error_type == "TenantQuotaExceeded"
        assert _run(B).success is True
        assert _run(None).error_type == "TenantQuotaExceeded"  # no tenant, and no "*"

    @pytest.mark.parametrize(
        ("arrange", "refusal"),
        [
            (
                lambda: get_tool_access_resolver().set_mcp_server_policy(
                    _SERVER, ToolAccessPolicy(deny_list=(_OTHER,))
                ),
                "ToolAccessDeniedError",
            ),
            (
                lambda: get_tool_projection_registry().build_from_tools(
                    _SERVER,
                    [ToolSchema(name=name, description=name, input_schema={}) for name in (_TOOL, _OTHER)],
                    tenant_overrides={_OTHER: {A: "withdrawn"}},
                ),
                "ToolWithdrawnError",
            ),
            (
                lambda: get_tool_projection_registry().set_config_pin(
                    _SERVER, _OTHER, A, ToolDigest(tool_name=_OTHER, sha256="a" * 64)
                ),
                "ToolDigestMismatchError",
            ),
        ],
        ids=["tool_access", "withdrawn", "digest_pin"],
    )
    def test_a_call_a_policy_gate_refuses_spends_nothing(self, ctx, budgets, arrange, refusal) -> None:
        budgets({A: _limits(max_concurrency=1, rps=NEVER, burst=1)})
        arrange()

        assert _run(A, _OTHER).error_type == refusal
        assert _run(A).success is True  # the one token was still there
        assert _run(A).error_type == "TenantQuotaExceeded"

    def test_during_the_approval_hold_the_token_is_taken_and_the_slot_is_not(self, ctx, budgets) -> None:
        budgets({A: _limits(max_concurrency=1, rps=NEVER, burst=1)})
        seen: list[tuple[int, Any]] = []

        def approval_gate(*_args: Any, **_kwargs: Any) -> None:
            seen.append((get_tenant_admission().in_flight(A), get_tenant_admission().reserve(A)))

        with patch.object(BatchExecutor, "_check_approval_gate", side_effect=approval_gate):
            assert _run(A).success is True

        assert seen == [(0, Refusal(budget=A, reason=RATE))]

    def test_a_call_over_its_rate_is_never_held_for_approval(self, ctx, budgets) -> None:
        budgets({A: _limits(max_concurrency=5, rps=NEVER, burst=1)})
        asked: list[str] = []

        with patch.object(
            BatchExecutor, "_check_approval_gate", side_effect=lambda call, *_a, **_k: asked.append(call.tool)
        ):
            assert _run(A).success is True
            assert _run(A).error_type == "TenantQuotaExceeded"

        assert asked == [_TOOL]

    def test_a_call_denied_at_approval_gets_its_token_back(self, ctx, budgets) -> None:
        budgets({A: _limits(max_concurrency=5, rps=NEVER, burst=1)})
        denied = CallResult(
            index=0, call_id="c-1", success=False, error="no", error_type="ApprovalDenied", elapsed_ms=0
        )

        with patch.object(BatchExecutor, "_check_approval_gate", return_value=denied):
            assert _run(A).error_type == "ApprovalDenied"

        assert _run(A).success is True
        assert _run(A).error_type == "TenantQuotaExceeded"

    def test_a_gate_that_raises_after_the_token_was_taken_gives_it_back(self, ctx, budgets) -> None:
        budgets({A: _limits(max_concurrency=5, rps=NEVER, burst=1)})

        with patch.object(BatchExecutor, "_check_approval_gate", side_effect=RuntimeError("boom")):
            assert _run(A).success is False

        assert _run(A).success is True

    def test_an_unlisted_tenant_does_not_start_a_stopped_server(self, ctx, budgets) -> None:
        budgets({A: _limits(max_concurrency=5, rps=100, burst=100)})
        ctx.get_mcp_server.return_value.state.value = "cold"

        refused = _run(B)

        assert (refused.error_type, refused.error) == ("TenantQuotaExceeded", _NO_BUDGET)
        assert _sent(ctx) == []
        assert _run(A).success is True
        assert _sent(ctx) == ["StartMcpServerCommand", "InvokeToolCommand"]

    def test_a_call_whose_server_fails_to_start_gets_its_token_back(self, ctx, budgets) -> None:
        budgets({A: _limits(max_concurrency=5, rps=NEVER, burst=1)})
        server = ctx.get_mcp_server.return_value
        server.state.value = "cold"
        ctx.command_bus.send.side_effect = RuntimeError("the server did not start")

        assert _run(A).error_type == "McpServerStartError"

        server.state.value = "ready"
        ctx.command_bus.send.side_effect = None
        assert _run(A).success is True
        assert _run(A).error_type == "TenantQuotaExceeded"

    def test_an_approved_call_refused_for_its_slot_names_the_approval_and_keeps_its_token(self, ctx, budgets) -> None:
        budgets({A: _limits(max_concurrency=1, rps=NEVER, burst=2)})
        held = _granted(get_tenant_admission(), A)

        def approved(*_args: Any, **_kwargs: Any) -> None:
            executor_module._approval_loop_local.approval_id = "approval-1"

        with (
            patch.object(BatchExecutor, "_check_approval_gate", side_effect=approved),
            patch.object(executor_module, "logger") as log,
        ):
            refused = _run(A)

        assert (refused.error_type, refused.error) == ("TenantQuotaExceeded", _IN_FLIGHT)
        (line,) = [call for call in log.warning.call_args_list if call.args[0] == "tenant_quota_exceeded"]
        assert (line.kwargs["approval_id"], line.kwargs["reason"]) == ("approval-1", CONCURRENCY)
        held.release()
        assert _run(A).success is True  # the refused call's token came back

    def test_the_slot_is_held_while_the_invoke_runs(self, ctx, budgets) -> None:
        budgets({A: _limits(max_concurrency=1, rps=100, burst=100)})
        seen: list[int] = []

        def invoke(_command: Any) -> dict[str, Any]:
            seen.append(get_tenant_admission().in_flight(A))
            return {"ok": True}

        ctx.command_bus.send.side_effect = invoke

        assert _run(A).success is True
        assert seen == [1]
        assert get_tenant_admission().in_flight(A) == 0

    def test_the_slot_is_released_when_the_dispatch_raises(self, ctx, budgets) -> None:
        budgets({A: _limits(max_concurrency=1, rps=100, burst=100)})

        with patch.object(BatchExecutor, "_dispatch", side_effect=RuntimeError("boom")):
            assert _run(A).success is False

        assert get_tenant_admission().in_flight(A) == 0
        assert _run(A).success is True

    @pytest.mark.parametrize("relayed", [True, False], ids=["relayed", "relay_off"])
    def test_the_slot_is_released_when_the_upstream_answers_with_a_task(self, ctx, budgets, relayed) -> None:
        budgets({A: _limits(max_concurrency=1, rps=100, burst=100)})
        ctx.command_bus.send.return_value = {"task": {"taskId": "task-1", "status": "working"}}
        ctx.governed_task_store = Mock() if relayed else None

        result = _run(A)

        assert result.error_type == (None if relayed else "TaskRelayNotSupported")
        assert (result.relay_capture is not None) is relayed
        assert get_tenant_admission().in_flight(A) == 0
