"""Circuit Breaker pattern implementation.

The Circuit Breaker pattern prevents cascading failures by stopping
requests to a failing service and allowing it time to recover.

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Failing, all requests rejected immediately
- HALF_OPEN: Probing recovery; limited requests allowed to test the service
"""

from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Any


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""

    failure_threshold: int = 10
    reset_timeout_s: float = 60.0
    probe_count: int = 1

    def __post_init__(self):
        self.failure_threshold = max(1, self.failure_threshold)
        self.reset_timeout_s = max(1.0, self.reset_timeout_s)
        self.probe_count = max(1, self.probe_count)


class CircuitBreaker:
    """
    Circuit breaker that opens after reaching failure threshold.

    Thread-safe implementation that tracks failures and transitions through
    CLOSED -> OPEN -> HALF_OPEN -> CLOSED (recovery) or HALF_OPEN -> OPEN (failure).

    The HALF_OPEN state gates exactly probe_count requests before deciding
    whether to close (on success) or re-open (on failure).
    """

    def __init__(self, config: CircuitBreakerConfig | None = None):
        """
        Initialize circuit breaker.

        Args:
            config: Circuit breaker configuration
        """
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._probe_successes = 0
        self._probes_allowed = 0
        self._lock = threading.Lock()
        # Callback invoked with (old_state, new_state) on every transition.
        # Set by external code (e.g. to emit metrics/events) after construction.
        self._on_state_change: Any = None

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (blocking requests)."""
        with self._lock:
            return self._state == CircuitState.OPEN

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        """Get current failure count."""
        with self._lock:
            return self._failure_count

    def allow_request(self) -> bool:
        """
        Check if a request should be allowed.

        - CLOSED: always allow.
        - OPEN: if reset timeout elapsed, transition to HALF_OPEN and allow
          exactly probe_count probe requests; otherwise reject.
        - HALF_OPEN: allow while remaining probe slots exist; reject once
          all slots are consumed (waiting for results to come back).

        Returns:
            True if request should proceed, False if circuit is open or probes exhausted.
        """
        callback = None
        result = False
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                if self._should_reset():
                    old = self._state
                    self._enter_half_open()
                    callback = (old, self._state)
                    result = True
                else:
                    return False

            elif self._state == CircuitState.HALF_OPEN:
                if self._probes_allowed < self._config.probe_count:
                    self._probes_allowed += 1
                    result = True
                else:
                    result = False

        if callback is not None:
            self._fire_state_change(*callback)
        return result

    def record_success(self) -> None:
        """Record a successful operation.

        In HALF_OPEN, accumulates successes; closes circuit once all
        probe_count probes have succeeded.
        In CLOSED, resets failure count.
        In OPEN, closes immediately (e.g. after manual reset).
        """
        callback = None
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._probe_successes += 1
                if self._probe_successes >= self._config.probe_count:
                    old = self._state
                    self._close()
                    callback = (old, self._state)
            elif self._state == CircuitState.OPEN:
                old = self._state
                self._close()
                callback = (old, self._state)
            else:
                self._failure_count = 0

        if callback is not None:
            self._fire_state_change(*callback)

    def record_failure(self) -> bool:
        """
        Record a failed operation.

        Returns:
            True if circuit just opened (CLOSED -> OPEN or HALF_OPEN -> OPEN),
            False otherwise.
        """
        callback = None
        opened = False
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                old = self._state
                self._open()
                opened = True
                callback = (old, self._state)
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._config.failure_threshold:
                    old = self._state
                    self._open()
                    opened = True
                    callback = (old, self._state)
            # OPEN: additional failures are ignored (already open)

        if callback is not None:
            self._fire_state_change(*callback)
        return opened

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        callback = None
        with self._lock:
            old = self._state
            # Always clear failure count on manual reset
            self._failure_count = 0
            if old != CircuitState.CLOSED:
                self._close()
                callback = (old, CircuitState.CLOSED)

        if callback is not None:
            self._fire_state_change(*callback)

    def _should_reset(self) -> bool:
        """Check if enough time has passed to attempt reset (must hold lock)."""
        if self._opened_at is None:
            return True
        return time.time() - self._opened_at >= self._config.reset_timeout_s

    def _open(self) -> None:
        """Open the circuit (must hold lock)."""
        self._state = CircuitState.OPEN
        self._opened_at = time.time()
        self._probe_successes = 0
        self._probes_allowed = 0

    def _enter_half_open(self) -> None:
        """Transition to HALF_OPEN (must hold lock)."""
        self._state = CircuitState.HALF_OPEN
        self._probe_successes = 0
        self._probes_allowed = 1  # First probe already granted by allow_request

    def _close(self) -> None:
        """Close the circuit (must hold lock)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = None
        self._probe_successes = 0
        self._probes_allowed = 0

    def _fire_state_change(self, old: CircuitState, new: CircuitState) -> None:
        """Invoke the state-change callback outside of any lock."""
        if self._on_state_change is not None:
            try:
                self._on_state_change(old, new)
            except Exception:  # noqa: BLE001 -- observer callback must never crash the breaker
                pass

    def to_dict(self) -> dict[str, Any]:
        """Get circuit breaker status as dictionary."""
        with self._lock:
            return {
                "state": self._state.value,
                "is_open": self._state == CircuitState.OPEN,
                "failure_count": self._failure_count,
                "failure_threshold": self._config.failure_threshold,
                "reset_timeout_s": self._config.reset_timeout_s,
                "probe_count": self._config.probe_count,
                "opened_at": self._opened_at,
            }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CircuitBreaker":
        """Restore circuit breaker from a serialized dictionary.

        Reconstructs a CircuitBreaker with its full state including
        open/closed/half_open status, failure count, and opened_at timestamp.
        Missing fields use safe defaults (closed state, zero failures).

        Old snapshots that predate HALF_OPEN (state value absent or unknown)
        are treated as CLOSED for safe forward compatibility.

        Args:
            d: Dictionary from to_dict(), or partial dict with safe defaults.

        Returns:
            CircuitBreaker instance with restored state.
        """
        config = CircuitBreakerConfig(
            failure_threshold=d.get("failure_threshold", 10),
            reset_timeout_s=d.get("reset_timeout_s", 60.0),
            probe_count=d.get("probe_count", 1),
        )
        cb = cls(config=config)
        raw_state = d.get("state", "closed")
        try:
            cb._state = CircuitState(raw_state)
        except ValueError:
            # Unknown state value (e.g. future extension) -- default to CLOSED
            cb._state = CircuitState.CLOSED
        cb._failure_count = d.get("failure_count", 0)
        cb._opened_at = d.get("opened_at")
        return cb
