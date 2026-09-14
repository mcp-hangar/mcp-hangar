"""Bound authenticated tenants before they can occupy execution/DB slots.

Limits are per process, deliberately matching execution.max_concurrency. A
deployment must budget replica_count * limit. Only configured tenant IDs allocate
state; unknown tenants fail closed. Refusals never wait or retry a tool call.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import math
import threading
import time


class TenantQuotaExceeded(Exception):
    """Admission refused before any tool executes."""


@dataclass
class _Budget:
    concurrency: int
    rps: float
    burst: int
    tokens: float
    updated: float
    active: int = 0


class TenantAdmission:
    def __init__(self, limits: dict):
        self._lock = threading.Lock()
        self._budgets: dict[str, _Budget] = {}
        for tenant, values in limits.items():
            if not isinstance(tenant, str) or not tenant or not isinstance(values, dict):
                raise ValueError("execution.tenant_limits requires named tenant budgets")
            concurrency, rps, burst = (values.get(k) for k in ("max_concurrency", "rps", "burst"))
            if (
                type(concurrency) is not int
                or concurrency < 1
                or type(burst) is not int
                or burst < 1
                or type(rps) not in (int, float)
                or not math.isfinite(rps)
                or rps <= 0
            ):
                raise ValueError("tenant max_concurrency, rps and burst must be positive finite numbers")
            self._budgets[tenant] = _Budget(concurrency, rps, burst, float(burst), time.monotonic())

    @contextmanager
    def acquire(self, tenant: str | None):
        # An absent section preserves single-tenant/internal gateway behaviour.
        if not self._budgets:
            yield
            return
        with self._lock:
            budget = self._budgets.get(tenant)
            if budget is None:
                raise TenantQuotaExceeded("No execution budget for this tenant")
            now = time.monotonic()
            budget.tokens = min(budget.burst, budget.tokens + (now - budget.updated) * budget.rps)
            budget.updated = now
            if budget.active >= budget.concurrency or budget.tokens < 1:
                raise TenantQuotaExceeded("Tenant execution budget exhausted")
            budget.tokens -= 1
            budget.active += 1
        try:
            yield
        finally:
            with self._lock:
                budget.active -= 1


_admission = TenantAdmission({})


def configure_tenant_admission(limits: dict) -> None:
    global _admission
    if not isinstance(limits, dict):
        raise ValueError("execution.tenant_limits must be a mapping")
    _admission = TenantAdmission(limits)


def get_tenant_admission() -> TenantAdmission:
    return _admission
