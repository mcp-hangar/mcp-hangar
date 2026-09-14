"""Per-tenant execution budgets: how many calls a tenant may have executing, and how fast it may start them (#1445).

`execution.max_concurrency` bounds the process, not a tenant. One tenant's
burst could take every slot, and every other tenant was then queued behind it
or refused. A budget bounds each tenant on its own: `max_concurrency` calls
executing at once, started at `rps` per second on average and at most `burst`
at once (a token bucket).

Where it is taken
    In two steps, both after the policy gates -- tool access, withdrawal,
    pins, the circuit breaker and the validators -- so a call one of them
    refuses spends nothing.

    - The token, and the check that the caller has a budget at all, come next:
      before the approval hold and the cold start
      (`BatchExecutor._gate_tenant_budget`). A caller with no budget, or over
      its rate, is refused before anyone is asked to approve its call, and
      before its call starts a stopped server. A call refused after this step
      -- denied or expired at approval, no longer valid after the hold, its
      server failing to start -- gets its token back.
    - The slot comes last, just before the execution slot and the upstream
      call (`BatchExecutor._enforce_tenant_budget`). A call held for approval,
      or waiting on a cold start, holds no slot, and the slot is given back
      when the call returns, on every path.

    Two consequences follow. An approved call is refused if its tenant's slots
    are all taken when it is dispatched: the refusal's log line names the
    approval, and running the call again needs a new one. And a tenant at its
    concurrency limit can still start a stopped server, with a call that is
    then refused.

Who is held to which budget
    - A tenant listed in `execution.tenant_limits` has its own budget.
    - Any other tenant has a budget of its own, built from the `"*"` entry.
      `"*"` is a template, not one pool that every unlisted tenant shares: a
      pool would let one unlisted tenant take all of it, which is what budgets
      exist to prevent. Callers with no tenant -- every caller, when
      authentication is off -- count as one caller, and share one budget.
    - With no `"*"` entry, a tenant that is not listed and a caller with no
      tenant are refused. A table of budgets says who may run, so it fails
      closed.
    - No `tenant_limits`, or an empty one, means no budgets: the gateway
      behaves as it did before.

Per process
    Counted in this process, as `execution.max_concurrency` is. N replicas
    admit up to N times each budget, so a deployment sizes a budget for its
    replica count.

Never waits
    A call over its budget is refused at once with `TenantQuotaExceeded`. It is
    neither queued nor retried: a queued call would hold the executor's worker
    thread -- on the front door, a thread of the event loop's shared pool --
    for as long as it waited.

Reload
    `configure` reconciles in place. A tenant whose limits are unchanged keeps
    its budget, with its calls in flight and its tokens. A tenant whose limits
    changed gets a new budget that keeps counting the calls it has in flight,
    so lowering a limit never lets more than the new limit start. A tenant
    that is no longer configured is refused from then on. Its budget is kept
    while calls on it are still running, so they are counted again if the
    tenant comes back, and it is dropped once they finish. Calls already
    running when budgets are first turned on are not counted.

Limits
    `max_concurrency` and `burst` are integers from 1 to `MAX_COUNT`, and `rps`
    is a number above 0 and at most `MAX_RPS`. `"none"` is not a tenant id
    here: it is the refusal metric's `budget` label for a caller that no entry
    applied to.

A budget is kept only while it differs from a new one: a call in flight, or
tokens spent. Once many are kept, the idle and full ones are dropped, which
changes no answer, because a new budget is idle and full.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
import threading
import time
from typing import Any, TypeGuard

#: The entry a tenant that is not listed is held to.
DEFAULT_BUDGET = "*"

#: The keys of a budget entry. All three are required.
BUDGET_KEYS = frozenset({"max_concurrency", "rps", "burst"})

#: Why a call was refused, as the refusal metric's `reason` label says it.
NO_BUDGET = "no_budget"
CONCURRENCY = "concurrency"
RATE = "rate"

#: The `budget` label of a refusal that no entry applied to. Refused as a
#: tenant id, so the label cannot also name a tenant.
NO_ENTRY = "none"

#: The largest `max_concurrency` and `burst`. A number beyond any real limit
#: is a typo, and one too large for a float would fail every call instead of
#: the configuration.
MAX_COUNT = 10**9

#: The largest `rps`, for the same reason.
MAX_RPS = 1_000_000

#: How many budgets are kept before the idle, full ones are dropped.
_PRUNE_AT = 1024


@dataclass(frozen=True)
class TenantLimits:
    """One checked budget entry."""

    max_concurrency: int
    rps: float
    burst: int

    def as_config(self) -> dict[str, Any]:
        """The entry as `config.yaml` writes it."""
        return {"max_concurrency": self.max_concurrency, "rps": self.rps, "burst": self.burst}


def parse_tenant_limits(raw: object) -> dict[str, TenantLimits]:
    """Check `execution.tenant_limits`. Returns an empty dict when it is absent.

    Stricter than the schema check, which stops at `execution`'s own keys: a
    misspelt key in an entry would leave a tenant with a budget nobody meant.

    Raises:
        ValueError: Naming the entry and what is wrong with it.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"must be a mapping of tenant id to budget, got {type(raw).__name__}")
    return {_tenant_id(tenant): _limits(tenant, values) for tenant, values in raw.items()}


def _tenant_id(tenant: object) -> str:
    if not isinstance(tenant, str) or not tenant:
        raise ValueError(f"tenant id {_shown(tenant)} must be a non-empty string (quote a numeric id in YAML)")
    if tenant == NO_ENTRY:
        raise ValueError(
            f"tenant id {NO_ENTRY!r} is reserved: it is the refusal metric's label for a caller no entry applied to"
        )
    return tenant


def _limits(tenant: object, values: object) -> TenantLimits:
    where = f"entry {_shown(tenant)}"
    if not isinstance(values, Mapping):
        raise ValueError(f"{where} must be a mapping with the keys {sorted(BUDGET_KEYS)}")
    unknown = sorted(str(key) for key in values if key not in BUDGET_KEYS)
    missing = sorted(BUDGET_KEYS - set(values))
    if unknown or missing:
        raise ValueError(
            f"{where} must have exactly the keys {sorted(BUDGET_KEYS)}: unknown {unknown}, missing {missing}"
        )
    concurrency, rps, burst = values["max_concurrency"], values["rps"], values["burst"]
    if not _count(concurrency):
        raise ValueError(
            f"{where}: max_concurrency must be an integer from 1 to {MAX_COUNT}, got {_shown(concurrency)}"
        )
    if not _count(burst):
        raise ValueError(f"{where}: burst must be an integer from 1 to {MAX_COUNT}, got {_shown(burst)}")
    rate = _rate(rps)
    if rate is None:
        raise ValueError(f"{where}: rps must be a number above 0 and at most {MAX_RPS}, got {_shown(rps)}")
    return TenantLimits(max_concurrency=concurrency, rps=rate, burst=burst)


def _count(value: object) -> TypeGuard[int]:
    """An int from 1 to `MAX_COUNT`. A bool is an int to Python, and is not one here."""
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_COUNT


def _rate(value: object) -> float | None:
    """*value* as a rate, or None when it is not a number above 0 and at most `MAX_RPS`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        rate = float(value)
    except OverflowError:  # an int too large for a float
        return None
    return rate if math.isfinite(rate) and 0 < rate <= MAX_RPS else None


def _shown(value: object) -> str:
    """*value* for an error message, cut short: a 400-digit number is not worth printing."""
    text = repr(value)
    return text if len(text) <= 40 else f"{text[:37]}..."


class _Slots:
    """A budget's in-flight count, shared with the budget a reload rebuilds from it."""

    __slots__ = ("active",)

    def __init__(self) -> None:
        self.active = 0


@dataclass
class _Budget:
    limits: TenantLimits
    #: The entry it was built from: the tenant id, or `"*"`.
    entry: str
    tokens: float
    updated: float
    slots: _Slots

    def refill(self, now: float) -> None:
        self.tokens = min(float(self.limits.burst), self.tokens + (now - self.updated) * self.limits.rps)
        self.updated = now

    def give_back_token(self) -> None:
        self.tokens = min(float(self.limits.burst), self.tokens + 1)

    def is_new(self, now: float) -> bool:
        """Whether a budget built now would give every answer this one gives."""
        self.refill(now)
        return self.slots.active == 0 and self.tokens >= self.limits.burst


class Grant:
    """A slot taken from a budget. `release` gives it back; a second release does nothing."""

    __slots__ = ("_lock", "_slots")

    def __init__(self, lock: threading.Lock | None, slots: _Slots | None) -> None:
        self._lock = lock
        self._slots = slots

    def release(self) -> None:
        if self._lock is None:
            return
        with self._lock:
            slots, self._slots = self._slots, None
            if slots is not None:
                slots.active -= 1


#: What a call is given when no budgets are configured.
_UNBUDGETED = Grant(None, None)


@dataclass(frozen=True)
class Refusal:
    """Why a call was not admitted.

    Attributes:
        budget: The entry that refused it, a tenant id or `"*"`, or `"none"`.
        reason: `no_budget`, `concurrency` or `rate`.
    """

    budget: str
    reason: str


class Reservation:
    """A token taken for one call, before its slot (`TenantAdmission.reserve`).

    `grant` takes the slot, or refuses the call and gives the token back.
    `refund` gives the token back for a call refused before it got that far.
    The first of them ends the reservation: a later `refund` does nothing, and
    a later `grant` is a bug and raises.
    """

    __slots__ = ("_admission", "open", "tenant_id")

    def __init__(self, admission: TenantAdmission | None, tenant_id: str | None) -> None:
        self._admission = admission
        self.tenant_id = tenant_id
        self.open = True

    def grant(self) -> Grant | Refusal:
        if self._admission is None:
            return _UNBUDGETED
        return self._admission.grant(self)

    def refund(self) -> None:
        if self._admission is not None:
            self._admission.refund(self)


#: What a call is given when no budgets are configured.
_UNRESERVED = Reservation(None, None)


class TenantAdmission:
    """The budgets in force, and the calls admitted against them."""

    def __init__(
        self, limits: Mapping[str, TenantLimits] | None = None, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._limits: dict[str, TenantLimits] = dict(limits or {})
        self._budgets: dict[str | None, _Budget] = {}
        self._prune_at = _PRUNE_AT

    @property
    def limits(self) -> dict[str, TenantLimits]:
        """The entries in force, by tenant id and `"*"`."""
        with self._lock:
            return dict(self._limits)

    def in_flight(self, tenant_id: str | None) -> int:
        """How many of *tenant_id*'s calls hold a slot."""
        with self._lock:
            budget = self._budgets.get(tenant_id)
            return budget.slots.active if budget is not None else 0

    def reserve(self, tenant_id: str | None) -> Reservation | Refusal:
        """Take a token for one call of *tenant_id*, or say why not. Never waits."""
        with self._lock:
            if not self._limits:
                return _UNRESERVED
            entry = self._entry_for(tenant_id)
            if entry is None:
                return Refusal(budget=NO_ENTRY, reason=NO_BUDGET)
            budget = self._budget_for(tenant_id, entry)
            if budget.tokens < 1:
                return Refusal(budget=budget.entry, reason=RATE)
            budget.tokens -= 1
            return Reservation(self, tenant_id)

    def grant(self, reservation: Reservation) -> Grant | Refusal:
        """Take the slot of a reserved call, or refuse it and give its token back. Never waits."""
        with self._lock:
            if not reservation.open:
                raise RuntimeError("a reservation is granted once, and never after its refund")
            reservation.open = False
            if not self._limits:
                return _UNBUDGETED  # budgets were turned off while the call waited
            entry = self._entry_for(reservation.tenant_id)
            if entry is None:
                return Refusal(budget=NO_ENTRY, reason=NO_BUDGET)  # its entry went while the call waited
            budget = self._budget_for(reservation.tenant_id, entry)
            if budget.slots.active >= budget.limits.max_concurrency:
                budget.give_back_token()
                return Refusal(budget=budget.entry, reason=CONCURRENCY)
            budget.slots.active += 1
            return Grant(self._lock, budget.slots)

    def refund(self, reservation: Reservation) -> None:
        """Give back the token of a reserved call that was refused before its slot. Once."""
        with self._lock:
            if not reservation.open:
                return
            reservation.open = False
            budget = self._budgets.get(reservation.tenant_id)
            if budget is not None and self._entry_for(reservation.tenant_id) is not None:
                budget.refill(self._clock())
                budget.give_back_token()

    def admit(self, tenant_id: str | None) -> Grant | Refusal:
        """Take a token and a slot at once: `reserve`, then `grant`."""
        reserved = self.reserve(tenant_id)
        return reserved if isinstance(reserved, Refusal) else reserved.grant()

    def configure(self, limits: Mapping[str, TenantLimits]) -> None:
        """Put *limits* in force, keeping every budget whose limits did not change."""
        with self._lock:
            self._limits = dict(limits)
            now = self._clock()
            for tenant_id, budget in list(self._budgets.items()):
                entry = self._entry_for(tenant_id)
                if entry is None:
                    # Refused from now on. Kept while calls on it run, so they
                    # are counted again if the tenant comes back.
                    if budget.slots.active == 0:
                        del self._budgets[tenant_id]
                    continue
                name, new = entry
                budget.entry = name
                if new == budget.limits:
                    continue
                budget.refill(now)
                self._budgets[tenant_id] = _Budget(
                    limits=new, entry=name, tokens=min(budget.tokens, float(new.burst)), updated=now, slots=budget.slots
                )

    def _entry_for(self, tenant_id: str | None) -> tuple[str, TenantLimits] | None:
        if tenant_id is not None and (listed := self._limits.get(tenant_id)) is not None:
            return tenant_id, listed
        default = self._limits.get(DEFAULT_BUDGET)
        return (DEFAULT_BUDGET, default) if default is not None else None

    def _budget_for(self, tenant_id: str | None, entry: tuple[str, TenantLimits]) -> _Budget:
        now = self._clock()
        budget = self._budgets.get(tenant_id)
        if budget is None:
            return self._add(tenant_id, entry, now)
        budget.refill(now)
        return budget

    def _add(self, tenant_id: str | None, entry: tuple[str, TenantLimits], now: float) -> _Budget:
        if len(self._budgets) >= self._prune_at:
            for idle in [key for key, budget in self._budgets.items() if self._droppable(key, budget, now)]:
                del self._budgets[idle]
            # Swept again only once the survivors have doubled, so a flood of
            # busy tenants costs one sweep per doubling, not one per tenant.
            self._prune_at = max(_PRUNE_AT, 2 * len(self._budgets))
        name, limits = entry
        budget = _Budget(limits=limits, entry=name, tokens=float(limits.burst), updated=now, slots=_Slots())
        self._budgets[tenant_id] = budget
        return budget

    def _droppable(self, tenant_id: str | None, budget: _Budget, now: float) -> bool:
        """Idle and full, or idle with no entry left: dropping it changes no answer."""
        if budget.slots.active == 0 and self._entry_for(tenant_id) is None:
            return True
        return budget.is_new(now)


_admission = TenantAdmission()


def get_tenant_admission() -> TenantAdmission:
    """The process's budgets, which every executor admits against."""
    return _admission


def configure_tenant_limits(limits: Mapping[str, TenantLimits]) -> None:
    """Put *limits* in force for the process; see `TenantAdmission.configure`."""
    _admission.configure(limits)


def reset_tenant_admission() -> None:
    """Forget every budget, and every call counted against one (for testing).

    `configure_tenant_limits({})` is not a reset: it keeps a budget that still
    has calls in flight, as a reload must.
    """
    global _admission
    _admission = TenantAdmission()
