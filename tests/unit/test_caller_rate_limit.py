"""Each caller's own command-bus rate limit, under the one all callers share (#1471).

These drive the limiter with the identity contextvar set by hand. That the
caller a served request authenticated as is the one charged is shown over the
real transport in tests/integration/test_caller_rate_limit_on_the_served_app.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from starlette.requests import Request

from mcp_hangar.application.commands import StartMcpServerCommand
from mcp_hangar.bootstrap.runtime import create_runtime, install_command_bus_rate_limit
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.exceptions import RATE_LIMIT_ALL_CALLERS, RATE_LIMIT_CALLER, RateLimitExceeded
from mcp_hangar.domain.security.rate_limiter import (
    InMemoryRateLimiter,
    RateLimitConfig,
    RateLimitResult,
    reset_rate_limiter,
)
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.infrastructure.caller_rate_limit import (
    caller_of,
    caller_rate_limit,
    CallerBuckets,
    charge,
    configure_caller_rate_limit,
    parse_per_caller,
    reset_caller_rate_limit,
)
from mcp_hangar.infrastructure.command_bus import CommandBus, RateLimitMiddleware
from mcp_hangar.server import validation
from mcp_hangar.server.api.middleware import error_handler
from mcp_hangar.server.validation import READ_ONLY_TOOLS, not_rate_limited

#: Tokens per second: nothing refills while a test runs.
NEVER = 0.001
#: Tokens per second: a bucket is full again by the next line.
AT_ONCE = 1_000_000.0

A = ("tenant:a", "agent-a")
B = ("tenant:b", "agent-b")


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    reset_caller_rate_limit()
    reset_rate_limiter()
    yield
    reset_caller_rate_limit()
    reset_rate_limiter()


def _identity(principal: str | None, tenant: str | None = None) -> IdentityContext:
    principal_type: Any = "anonymous" if principal is None else "service"
    return IdentityContext(
        caller=CallerIdentity(
            user_id=principal, agent_id=None, session_id=None, principal_type=principal_type, tenant_id=tenant
        )
    )


@contextmanager
def _as(identity: IdentityContext | None) -> Iterator[None]:
    token = identity_context_var.set(identity)
    try:
        yield
    finally:
        identity_context_var.reset(token)


def _caller(tenant: str, principal: str) -> IdentityContext:
    return _identity(principal, tenant)


def _shared(burst: int, rps: float = NEVER) -> InMemoryRateLimiter:
    return InMemoryRateLimiter(RateLimitConfig(requests_per_second=rps, burst_size=burst))


def _per_caller(burst: int, rps: float = NEVER) -> None:
    configure_caller_rate_limit(RateLimitConfig(requests_per_second=rps, burst_size=burst))


class TestWhoIsACaller:
    def test_a_caller_is_its_tenant_and_its_principal(self):
        assert caller_of(_caller("tenant:a", "agent-a")) == ("tenant:a", "agent-a")

    def test_a_principal_in_two_tenants_is_two_callers(self):
        assert caller_of(_caller("tenant:a", "agent")) != caller_of(_caller("tenant:b", "agent"))

    def test_callers_with_neither_are_one_caller(self):
        assert caller_of(None) == caller_of(_identity(None)) == (None, None)

    def test_an_anonymous_caller_with_a_tenant_is_that_tenant(self):
        assert caller_of(_identity(None, "tenant:a")) == ("tenant:a", None)


class TestPerCallerConfig:
    def test_absent_means_off(self):
        assert parse_per_caller(None) is None

    def test_both_keys(self):
        config = parse_per_caller({"rps": 2, "burst": 3})

        assert config is not None
        assert (config.requests_per_second, config.burst_size) == (2.0, 3)

    @pytest.mark.parametrize(
        "raw",
        [
            [],
            {"rps": 1},
            {"rps": 1, "burst": 1, "extra": 1},
            {"rps": 0, "burst": 1},
            {"rps": True, "burst": 1},
            {"rps": "1", "burst": 1},
            {"rps": float("inf"), "burst": 1},
            {"rps": 10**400, "burst": 1},
            {"rps": 1, "burst": 0},
            {"rps": 1, "burst": 1.5},
            {"rps": 1, "burst": True},
        ],
    )
    def test_anything_else_is_refused(self, raw: object):
        with pytest.raises(ValueError, match=r"rate_limit\.per_caller"):
            parse_per_caller(raw)


class TestCallerBuckets:
    def test_each_caller_and_key_has_its_own(self):
        buckets = CallerBuckets(RateLimitConfig(requests_per_second=NEVER, burst_size=1))

        assert buckets.take(A, "k")[0] is not None
        refused, wait = buckets.take(A, "k")
        assert refused is None and wait > 0
        assert buckets.take(B, "k")[0] is not None
        assert buckets.take(A, "other")[0] is not None

    def test_a_refilled_bucket_is_dropped_when_a_new_caller_arrives(self):
        buckets = CallerBuckets(RateLimitConfig(requests_per_second=AT_ONCE, burst_size=1))

        buckets.take(A, "k")
        buckets.take(B, "k")

        assert len(buckets) == 1

    def test_a_bucket_that_has_not_refilled_is_kept(self):
        buckets = CallerBuckets(RateLimitConfig(requests_per_second=NEVER, burst_size=1))

        buckets.take(A, "k")
        buckets.take(B, "k")

        assert len(buckets) == 2

    def test_past_the_cap_new_callers_share_one_bucket(self):
        buckets = CallerBuckets(RateLimitConfig(requests_per_second=NEVER, burst_size=1), max_buckets=2)

        assert buckets.take(A, "k")[0] is not None
        assert buckets.take(B, "k")[0] is not None
        assert buckets.take(("tenant:c", "agent-c"), "k")[0] is not None
        assert buckets.take(("tenant:d", "agent-d"), "k")[0] is None, "the overflow bucket is shared"
        assert buckets.take(("tenant:c", "agent-c"), "k")[0] is None
        assert len(buckets) == 3
        assert buckets.take(A, "k")[0] is None, "a caller under the cap keeps its own bucket"


class TestCharge:
    def test_without_per_caller_only_the_shared_budget_is_charged(self):
        shared = _shared(burst=1)

        with _as(_caller(*A)):
            assert charge(shared, "k") is None
        with _as(_caller(*B)):
            refusal = charge(shared, "k")

        assert refusal is not None and refusal.scope == RATE_LIMIT_ALL_CALLERS
        assert refusal.message.startswith(
            "RateLimitExceeded: the rate limit all callers share for k is used up (1 at once"
        )
        assert refusal.retry_after is not None and refusal.retry_after > 0

    def test_one_caller_using_up_its_budget_does_not_refuse_another(self):
        _per_caller(burst=2)
        shared = _shared(burst=100)

        with _as(_caller(*A)):
            outcomes = [charge(shared, "k") for _ in range(3)]
        with _as(_caller(*B)):
            other = charge(shared, "k")

        assert outcomes[:2] == [None, None]
        assert outcomes[2] is not None and outcomes[2].scope == RATE_LIMIT_CALLER
        assert other is None

    def test_a_caller_over_its_budget_spends_none_of_the_shared_one(self):
        _per_caller(burst=1)
        shared = _shared(burst=2)

        with _as(_caller(*A)):
            outcomes = [charge(shared, "k") for _ in range(5)]
        with _as(_caller(*B)):
            other = charge(shared, "k")
        with _as(_caller("tenant:c", "agent-c")):
            third = charge(shared, "k")

        assert outcomes[0] is None
        assert all(refusal is not None and refusal.scope == RATE_LIMIT_CALLER for refusal in outcomes[1:])
        assert other is None, "A's refused calls spent the budget all callers share"
        assert third is not None and third.scope == RATE_LIMIT_ALL_CALLERS

    def test_a_call_the_shared_budget_refuses_gets_its_callers_token_back(self):
        _per_caller(burst=1)
        shared = _shared(burst=1)

        with _as(_caller(*B)):
            assert charge(shared, "k") is None
        with _as(_caller(*A)):
            refused = charge(shared, "k")
            shared.reset_all()
            again = charge(shared, "k")

        assert refused is not None and refused.scope == RATE_LIMIT_ALL_CALLERS
        assert again is None, "the call the shared budget refused kept its caller's token"

    def test_anonymous_callers_share_one_budget(self):
        _per_caller(burst=1)
        shared = _shared(burst=100)

        with _as(None):
            assert charge(shared, "k") is None
        with _as(_identity(None)):
            refusal = charge(shared, "k")

        assert refusal is not None and refusal.scope == RATE_LIMIT_CALLER

    def test_a_shared_limiter_without_a_config_is_reported_without_its_rate(self):
        shared = Mock()
        shared.consume.return_value = RateLimitResult(allowed=False, remaining=0, reset_at=0.0, limit=5)

        refusal = charge(shared, "k")

        assert refusal is not None
        assert refusal.details["rps"] is None and "refilled" not in refusal.message


class TestTheRefusal:
    def test_the_message_and_details_say_the_same(self):
        refusal = RateLimitExceeded(
            limit=3, retry_after=1.234, scope=RATE_LIMIT_CALLER, key="InvokeToolCommand", rps=0.5
        )

        assert refusal.message == (
            "RateLimitExceeded: this caller's rate limit for InvokeToolCommand is used up "
            "(3 at once, refilled at 0.5 per second). Retry after 1.23s."
        )
        assert refusal.details == {
            "limit": 3,
            "window_seconds": 2,
            "retry_after": 1.234,
            "scope": "caller",
            "key": "InvokeToolCommand",
            "rps": 0.5,
        }

    def test_without_retry_after_it_reads_as_before(self):
        refusal = RateLimitExceeded(limit=100, window_seconds=60)

        assert refusal.message == "Rate limit exceeded: 100 requests per 60s"
        assert refusal.retry_after is None

    @staticmethod
    def _answer(exc: Exception) -> Any:
        request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
        return asyncio.run(error_handler(request, exc))

    def test_the_http_api_answers_429_with_retry_after(self):
        response = self._answer(RateLimitExceeded(limit=1, retry_after=1.2, scope=RATE_LIMIT_CALLER, key="k"))

        assert response.status_code == 429
        assert response.headers["retry-after"] == "2"
        body = json.loads(response.body)
        assert body["error"]["code"] == "RateLimitExceeded"
        assert body["error"]["details"]["retry_after"] == 1.2

    def test_a_refusal_without_retry_after_has_no_header(self):
        response = self._answer(RateLimitExceeded(limit=1, window_seconds=1))

        assert response.status_code == 429
        assert "retry-after" not in response.headers


class TestTheCommandBus:
    @staticmethod
    def _bus(shared: InMemoryRateLimiter) -> CommandBus:
        bus = CommandBus()
        handler = Mock()
        handler.handle.return_value = None
        bus.register(StartMcpServerCommand, handler)
        bus.add_middleware(RateLimitMiddleware(rate_limiter=shared))
        return bus

    def test_each_caller_is_charged_on_its_own(self):
        _per_caller(burst=1)
        bus = self._bus(_shared(burst=100))

        with _as(_caller(*A)):
            bus.send(StartMcpServerCommand(mcp_server_id="s"))
            with pytest.raises(RateLimitExceeded) as refused:
                bus.send(StartMcpServerCommand(mcp_server_id="s"))
        with _as(_caller(*B)):
            bus.send(StartMcpServerCommand(mcp_server_id="s"))

        assert refused.value.scope == RATE_LIMIT_CALLER
        assert "for StartMcpServerCommand" in refused.value.message

    def _runtime(self) -> Any:
        runtime = create_runtime(command_bus=CommandBus(), env={})
        handler = Mock()
        handler.handle.return_value = None
        runtime.command_bus.register(StartMcpServerCommand, handler)
        return runtime

    def test_startup_puts_both_budgets_in_force(self):
        runtime = self._runtime()

        shared, per_caller = install_command_bus_rate_limit(
            runtime, {"rate_limit": {"rps": NEVER, "burst": 3, "per_caller": {"rps": NEVER, "burst": 2}}}, env={}
        )

        assert (shared.burst_size, per_caller and per_caller.burst_size) == (3, 2)
        with _as(_caller(*A)):
            runtime.command_bus.send(StartMcpServerCommand(mcp_server_id="s"))
            runtime.command_bus.send(StartMcpServerCommand(mcp_server_id="s"))
            with pytest.raises(RateLimitExceeded, match="this caller's rate limit"):
                runtime.command_bus.send(StartMcpServerCommand(mcp_server_id="s"))
        with _as(_caller(*B)):
            runtime.command_bus.send(StartMcpServerCommand(mcp_server_id="s"))
            with pytest.raises(RateLimitExceeded, match="the rate limit all callers share"):
                runtime.command_bus.send(StartMcpServerCommand(mcp_server_id="s"))

    def test_without_per_caller_the_shared_budget_is_as_before(self):
        runtime = self._runtime()

        _, per_caller = install_command_bus_rate_limit(runtime, {"rate_limit": {"rps": NEVER, "burst": 2}}, env={})

        assert per_caller is None and caller_rate_limit() is None
        with _as(_caller(*A)):
            runtime.command_bus.send(StartMcpServerCommand(mcp_server_id="s"))
        with _as(_caller(*B)):
            runtime.command_bus.send(StartMcpServerCommand(mcp_server_id="s"))
        with _as(_caller(*A)), pytest.raises(RateLimitExceeded, match="the rate limit all callers share"):
            runtime.command_bus.send(StartMcpServerCommand(mcp_server_id="s"))

    def test_a_malformed_per_caller_changes_nothing(self):
        _per_caller(burst=7)
        runtime = self._runtime()

        with pytest.raises(ValueError, match=r"rate_limit\.per_caller"):
            install_command_bus_rate_limit(runtime, {"rate_limit": {"burst": 1, "per_caller": {"rps": 1}}}, env={})

        in_force = caller_rate_limit()
        assert in_force is not None and in_force.burst_size == 7
        assert runtime.rate_limit_config.burst_size != 1


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestTheToolLevelCheck:
    def test_it_charges_each_caller_on_its_own(self, monkeypatch: pytest.MonkeyPatch):
        security = Mock()
        monkeypatch.setattr(
            validation,
            "get_context",
            lambda: SimpleNamespace(rate_limiter=_shared(burst=100), security_handler=security),
        )
        _per_caller(burst=1)

        with _as(_caller(*A)):
            validation.check_rate_limit("hangar_start:s")
            with pytest.raises(RateLimitExceeded, match="this caller's rate limit for hangar_start:s") as refused:
                validation.check_rate_limit("hangar_start:s")
        with _as(_caller(*B)):
            validation.check_rate_limit("hangar_start:s")

        security.log_rate_limit_exceeded.assert_called_once_with(limit=1, window_seconds=refused.value.window_seconds)

    def test_the_read_only_tools_are_the_ones_registered_without_a_limit(self, monkeypatch: pytest.MonkeyPatch):
        from mcp_hangar.server.tools import discovery, groups, hangar, health, mcp_server

        checks: dict[str, Any] = {}

        def recorder(*, tool_name: str, check_rate_limit: Any, **_: Any) -> Any:
            checks[tool_name] = check_rate_limit
            return lambda func: func

        stub = SimpleNamespace(tool=lambda *_a, **_k: lambda func: func)
        for module, register in (
            (groups, groups.register_group_tools),
            (hangar, hangar.register_hangar_tools),
            (hangar, hangar.register_load_tools),
            (mcp_server, mcp_server.register_mcp_server_tools),
            (health, health.register_health_tools),
            (discovery, discovery.register_discovery_tools),
        ):
            monkeypatch.setattr(module, "mcp_tool_wrapper", recorder)
            register(stub)

        assert checks.keys() >= READ_ONLY_TOOLS
        assert {name for name, check in checks.items() if check is not_rate_limited} == READ_ONLY_TOOLS
        assert not_rate_limited("hangar_list") is None

    def test_hangar_sources_is_charged(self, monkeypatch: pytest.MonkeyPatch):
        """It runs every discovery source's health check, a call out of Hangar (#1479)."""
        from mcp_hangar.server.tools import discovery

        checks: dict[str, Any] = {}

        def recorder(*, tool_name: str, check_rate_limit: Any, **_: Any) -> Any:
            checks[tool_name] = check_rate_limit
            return lambda func: func

        monkeypatch.setattr(discovery, "mcp_tool_wrapper", recorder)
        discovery.register_discovery_tools(SimpleNamespace(tool=lambda *_a, **_k: lambda func: func))
        shared = _shared(burst=1)
        monkeypatch.setattr(
            validation, "get_context", lambda: SimpleNamespace(rate_limiter=shared, security_handler=Mock())
        )

        assert "hangar_sources" not in READ_ONLY_TOOLS
        checks["hangar_sources"]("global")
        with pytest.raises(RateLimitExceeded, match="the rate limit all callers share for hangar_sources"):
            checks["hangar_sources"]("global")
