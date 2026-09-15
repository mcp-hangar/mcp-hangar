"""Each caller's own command-bus rate limit, under the one all callers share (#1471).

`rate_limit` is one budget per command type, and every caller spends it, so one
caller's tool calls could use up `InvokeToolCommand` for every other caller.

Two budgets
    - `rate_limit` (`rps`, `burst`) keeps its meaning: the budget all callers
      share, one per command type. It is what every deployment configured, so
      what a gateway admits in total does not change.
    - `rate_limit.per_caller` (`rps`, `burst`) is new, and off when absent.
      With it, each caller also has a budget of its own, one per command type,
      under the shared one.

    A call is charged to its caller's budget first. A caller that has used up
    its own is refused there, and spends none of the shared budget. A call the
    shared budget then refuses gets its caller's token back: it did not run.

Who is a caller
    The tenant and the principal of `get_identity_context()`. A principal in two
    tenants is two callers, and each principal of a tenant is its own. A caller
    with neither is one caller, with one budget: every anonymous caller, every
    caller when authentication is off, and work no request started.

Not charged
    The listing and inspection tools, `server/validation.READ_ONLY_TOOLS`.

Bounded
    A bucket that has refilled is dropped when a new caller arrives: a new one
    would be full too, so dropping it changes no answer. At most
    `MAX_CALLER_BUCKETS` are kept. Past that, every new caller shares one
    overflow bucket, so callers past the cap get one caller's budget between
    them, and memory stops growing.

Per process, read at startup
    As `rate_limit` is. N replicas admit N times each budget.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable, Mapping
import math
import threading
from typing import Any, Protocol

from ..context import get_identity_context
from ..domain.exceptions import RATE_LIMIT_ALL_CALLERS, RATE_LIMIT_CALLER, RateLimitExceeded
from ..domain.security.rate_limiter import RateLimitConfig, TokenBucket
from ..domain.value_objects.identity import IdentityContext

#: The keys of `rate_limit.per_caller`. Both are required.
PER_CALLER_KEYS = frozenset({"rps", "burst"})

#: The most caller buckets kept at once.
MAX_CALLER_BUCKETS = 10_000

#: A caller: its tenant and its principal, each None when it has none.
Caller = tuple[str | None, str | None]

#: The bucket every caller past `MAX_CALLER_BUCKETS` shares. Equal to no caller's key.
_OVERFLOW: Hashable = object()


class _SharedLimiter(Protocol):
    """The rate limiter all callers share: `InMemoryRateLimiter`, or anything that consumes by key."""

    def consume(self, key: str) -> Any: ...


def caller_of(identity: IdentityContext | None) -> Caller:
    """The caller *identity* is charged as: its tenant, and its principal unless it is anonymous."""
    if identity is None:
        return (None, None)
    caller = identity.caller
    return (caller.tenant_id, None if caller.principal_type == "anonymous" else caller.user_id)


def parse_per_caller(raw: object) -> RateLimitConfig | None:
    """Check `rate_limit.per_caller`. None when it is absent.

    Raises:
        ValueError: Naming what is wrong with it.
    """
    if raw is None:
        return None
    where = "rate_limit.per_caller"
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where} must be a mapping with the keys {sorted(PER_CALLER_KEYS)}, got {type(raw).__name__}")
    unknown = sorted(str(key) for key in raw if key not in PER_CALLER_KEYS)
    missing = sorted(PER_CALLER_KEYS - set(raw))
    if unknown or missing:
        raise ValueError(
            f"{where} must have exactly the keys {sorted(PER_CALLER_KEYS)}: unknown {unknown}, missing {missing}"
        )
    rps, burst = raw["rps"], raw["burst"]
    rate = _rate(rps)
    if rate is None:
        raise ValueError(f"{where}.rps must be a number above 0, got {rps!r}")
    if isinstance(burst, bool) or not isinstance(burst, int) or burst < 1:
        raise ValueError(f"{where}.burst must be an integer of at least 1, got {burst!r}")
    return RateLimitConfig(requests_per_second=rate, burst_size=burst)


def _rate(value: object) -> float | None:
    """*value* as a rate, or None when it is not a finite number above 0. A bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        rate = float(value)
    except OverflowError:  # an int too large for a float
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


class CallerBuckets:
    """A token bucket per caller and key, each sized by `config`, kept bounded."""

    def __init__(self, config: RateLimitConfig, *, max_buckets: int = MAX_CALLER_BUCKETS) -> None:
        self.config = config
        self._max_buckets = max_buckets
        self._lock = threading.Lock()
        #: Least recently used first.
        self._buckets: OrderedDict[Hashable, TokenBucket] = OrderedDict()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)

    def take(self, caller: Caller, key: str) -> tuple[TokenBucket | None, float]:
        """Take a token for one call by *caller* on *key*.

        Returns:
            The bucket the token came from, to give it back to, and 0; or
            None and the seconds until the bucket has a token again.
        """
        with self._lock:
            # Under the lock, so a bucket is never dropped between finding it
            # and spending from it.
            bucket = self._bucket((caller, key))
            allowed, wait = bucket.consume()
        return (bucket, 0.0) if allowed else (None, wait)

    def _bucket(self, wanted: Hashable) -> TokenBucket:
        bucket = self._buckets.get(wanted)
        if bucket is None:
            while self._buckets and next(iter(self._buckets.values())).is_full():
                self._buckets.popitem(last=False)
            if len(self._buckets) >= self._max_buckets:
                wanted = _OVERFLOW
                bucket = self._buckets.get(wanted)
        if bucket is None:
            bucket = TokenBucket(rate=self.config.requests_per_second, capacity=self.config.burst_size)
            self._buckets[wanted] = bucket
        else:
            self._buckets.move_to_end(wanted)
        return bucket


_per_caller: CallerBuckets | None = None


def configure_caller_rate_limit(config: RateLimitConfig | None) -> None:
    """Put *config* in force as each caller's budget, every bucket full. None turns it off."""
    global _per_caller
    _per_caller = CallerBuckets(config) if config is not None else None


def caller_rate_limit() -> RateLimitConfig | None:
    """Each caller's budget in force, or None when there is none."""
    buckets = _per_caller
    return buckets.config if buckets is not None else None


def reset_caller_rate_limit() -> None:
    """Turn each caller's budget off (for testing)."""
    configure_caller_rate_limit(None)


def charge(shared: _SharedLimiter, key: str) -> RateLimitExceeded | None:
    """Charge one call on *key* to this caller's budget, then to *shared*.

    Returns the refusal rather than raising it, so the command bus can close
    its span and count it first. None means the call may run.
    """
    buckets = _per_caller
    taken: TokenBucket | None = None
    if buckets is not None:
        taken, wait = buckets.take(caller_of(get_identity_context()), key)
        if taken is None:
            return RateLimitExceeded(
                limit=buckets.config.burst_size,
                retry_after=wait,
                scope=RATE_LIMIT_CALLER,
                key=key,
                rps=buckets.config.requests_per_second,
            )
    result = shared.consume(key)
    if result.allowed:
        return None
    if taken is not None:
        taken.give_back()
    config = getattr(shared, "config", None)
    return RateLimitExceeded(
        limit=result.limit,
        retry_after=result.retry_after or 0.0,
        scope=RATE_LIMIT_ALL_CALLERS,
        key=key,
        rps=config.requests_per_second if isinstance(config, RateLimitConfig) else None,
    )
