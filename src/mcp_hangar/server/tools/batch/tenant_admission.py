"""Per-tenant execution budgets: how many calls a tenant may have in flight, and how fast it may start them (#1445).

`execution.max_concurrency` bounds the process, not a tenant. One tenant's
burst could take every slot, and every other tenant was then queued behind it
or refused. A budget bounds each tenant on its own: `max_concurrency` calls in
flight at once, started at `rps` per second on average and at most `burst` at
once (a token bucket).

Where it is taken
    After every policy gate, and before the execution slot and the upstream
    call (`BatchExecutor._enforce_tenant_budget`). A call the tool-access
    policy refuses, a withdrawn tool, a pin that does not match, a validator's
    refusal and a call held for a human's approval spend nothing: neither a
    token nor a slot. The slot is given back when the call returns, on every
    path.

Who is held to which budget
    - A tenant listed in `execution.tenant_limits` has its own budget.
    - Any other tenant has a budget of its own, built from the `"*"` entry.
      `"*"` is a template, not one pool that every unlisted tenant shares: a
      pool would let one unlisted tenant take all of it, which is what budgets
      exist to prevent. Callers with no tenant count as one caller, and share
      one budget.
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
    neither queued nor retried: a call queued for a slot holds a worker thread
    that does nothing, and worker threads are what the budget protects.

Reload
    `configure` reconciles in place. A tenant whose limits are unchanged keeps
    its budget, with its in-flight count and tokens. A tenant whose limits
    changed gets a new budget that keeps counting the calls it has in flight,
    so lowering a limit never lets more than the new limit start. A tenant
    that is no longer configured is dropped, and a call still running on it
    releases into nothing.

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

#: The `budget` label of a refusal that no entry applied to.
NO_ENTRY = "none"

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
    if isinstance(tenant, str) and tenant:
        return tenant
    raise ValueError(f"tenant id {tenant!r} must be a non-empty string (quote a numeric id in YAML)")


def _limits(tenant: object, values: object) -> TenantLimits:
    where = f"entry {tenant!r}"
    if not isinstance(values, Mapping):
        raise ValueError(f"{where} must be a mapping with the keys {sorted(BUDGET_KEYS)}")
    unknown = sorted(str(key) for key in values if key not in BUDGET_KEYS)
    missing = sorted(BUDGET_KEYS - set(values))
    if unknown or missing:
        raise ValueError(
            f"{where} must have exactly the keys {sorted(BUDGET_KEYS)}: unknown {unknown}, missing {missing}"
        )
    concurrency, rps, burst = values["max_concurrency"], values["rps"], values["burst"]
    if not _positive_int(concurrency):
        raise ValueError(f"{where}: max_concurrency must be an integer of at least 1, got {concurrency!r}")
    if not _positive_int(burst):
        raise ValueError(f"{where}: burst must be an integer of at least 1, got {burst!r}")
    if isinstance(rps, bool) or not isinstance(rps, int | float) or not math.isfinite(rps) or rps <= 0:
        raise ValueError(f"{where}: rps must be a finite number above 0, got {rps!r}")
    return TenantLimits(max_concurrency=concurrency, rps=float(rps), burst=burst)


def _positive_int(value: object) -> TypeGuard[int]:
    """An int of at least 1. A bool is an int to Python, and is not one here."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


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

    def admit(self, tenant_id: str | None) -> Grant | Refusal:
        """Take a slot and a token for one call of *tenant_id*, or say why not. Never waits for a slot."""
        with self._lock:
            if not self._limits:
                return _UNBUDGETED
            entry = self._entry_for(tenant_id)
            if entry is None:
                return Refusal(budget=NO_ENTRY, reason=NO_BUDGET)
            now = self._clock()
            budget = self._budgets.get(tenant_id)
            if budget is None:
                budget = self._add(tenant_id, entry, now)
            else:
                budget.refill(now)
            # The slot first: a call refused for concurrency spends no token.
            if budget.slots.active >= budget.limits.max_concurrency:
                return Refusal(budget=budget.entry, reason=CONCURRENCY)
            if budget.tokens < 1:
                return Refusal(budget=budget.entry, reason=RATE)
            budget.tokens -= 1
            budget.slots.active += 1
            return Grant(self._lock, budget.slots)

    def configure(self, limits: Mapping[str, TenantLimits]) -> None:
        """Put *limits* in force, keeping every budget whose limits did not change."""
        with self._lock:
            self._limits = dict(limits)
            now = self._clock()
            for tenant_id, budget in list(self._budgets.items()):
                entry = self._entry_for(tenant_id)
                if entry is None:
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

    def _add(self, tenant_id: str | None, entry: tuple[str, TenantLimits], now: float) -> _Budget:
        if len(self._budgets) >= self._prune_at:
            for idle in [key for key, budget in self._budgets.items() if budget.is_new(now)]:
                del self._budgets[idle]
            # Swept again only once the survivors have doubled, so a flood of
            # busy tenants costs one sweep per doubling, not one per tenant.
            self._prune_at = max(_PRUNE_AT, 2 * len(self._budgets))
        name, limits = entry
        budget = _Budget(limits=limits, entry=name, tokens=float(limits.burst), updated=now, slots=_Slots())
        self._budgets[tenant_id] = budget
        return budget


_admission = TenantAdmission()


def get_tenant_admission() -> TenantAdmission:
    """The process's budgets, which every executor admits against."""
    return _admission


def configure_tenant_limits(limits: Mapping[str, TenantLimits]) -> None:
    """Put *limits* in force for the process; see `TenantAdmission.configure`."""
    _admission.configure(limits)
