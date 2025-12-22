"""Circuit Breaker pattern implementation.

The Circuit Breaker pattern prevents cascading failures by stopping
requests to a failing service and allowing it time to recover.

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Failing, all requests rejected immediately
- HALF_OPEN: Testing if service recovered (not implemented - we auto-reset)
"""

from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Optional


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""

    failure_threshold: int = 10
    reset_timeout_s: float = 60.0

    def __post_init__(self):
        self.failure_threshold = max(1, self.failure_threshold)
        self.reset_timeout_s = max(1.0, self.reset_timeout_s)


class CircuitBreaker:
    """
    Circuit breaker that opens after reaching failure threshold.

    Thread-safe implementation that tracks failures and automatically
    resets after a timeout period.
    """

    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        """
        Initialize circuit breaker.

        Args:
            config: Circuit breaker configuration
        """
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: Optional[float] = None
        self._lock = threading.Lock()

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

        If circuit is open, checks if reset timeout has elapsed.
        If so, closes the circuit and allows the request.

        Returns:
            True if request should proceed, False if circuit is open
        """
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            # Check if we should try to close
            if self._should_reset():
                self._close()
                return True

            return False

    def record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            self._failure_count = 0
            if self._state == CircuitState.OPEN:
                self._close()

    def record_failure(self) -> bool:
        """
        Record a failed operation.

        Returns:
            True if circuit just opened, False otherwise
        """
        with self._lock:
            self._failure_count += 1

            if (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self._config.failure_threshold
            ):
                self._open()
                return True

            return False

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        with self._lock:
            self._close()

    def _should_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self._opened_at is None:
            return True
        return time.time() - self._opened_at >= self._config.reset_timeout_s

    def _open(self) -> None:
        """Open the circuit (must hold lock)."""
        self._state = CircuitState.OPEN
        self._opened_at = time.time()

    def _close(self) -> None:
        """Close the circuit (must hold lock)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = None

    def to_dict(self) -> dict:
        """Get circuit breaker status as dictionary."""
        with self._lock:
            return {
                "state": self._state.value,
                "is_open": self._state == CircuitState.OPEN,
                "failure_count": self._failure_count,
                "failure_threshold": self._config.failure_threshold,
                "reset_timeout_s": self._config.reset_timeout_s,
            }
