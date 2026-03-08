"""
Command Bus - dispatches commands to their handlers.

Commands represent intent to change the system state.
Each command has exactly one handler.

Supports a middleware pipeline that intercepts command dispatch.
Middleware executes in registration order before the handler.

Note: Command classes are defined in application.commands to maintain
proper layer separation (infrastructure should not define business commands).
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from mcp_hangar.logging_config import get_logger

if TYPE_CHECKING:
    from ..application.commands import Command
    from ..domain.security.rate_limiter import RateLimiter

logger = get_logger(__name__)


class CommandHandler(ABC):
    """Base class for command handlers."""

    @abstractmethod
    def handle(self, command: "Command") -> Any:
        """Handle the command and return result."""
        pass


class CommandBusMiddleware(ABC):
    """Middleware that intercepts command dispatch.

    Middleware can inspect, validate, or reject commands before they reach handlers.
    """

    @abstractmethod
    def __call__(self, command: "Command", next_handler: Callable[["Command"], Any]) -> Any:
        """Process the command, optionally calling next_handler to continue.

        Args:
            command: The command being dispatched.
            next_handler: Call this to continue the middleware chain.

        Returns:
            The result from the handler (or from rejection).

        Raises:
            Any exception to reject the command.
        """


class CommandBus:
    """
    Dispatches commands to their registered handlers.

    Each command type can have exactly one handler.
    The bus is responsible for routing commands to the appropriate handler.
    Supports a middleware pipeline executed before handler dispatch.
    """

    def __init__(self):
        self._handlers: dict[type, CommandHandler] = {}
        self._middleware: list[CommandBusMiddleware] = []

    def register(self, command_type: type, handler: CommandHandler) -> None:
        """
        Register a handler for a command type.

        Args:
            command_type: The type of command to handle
            handler: The handler instance

        Raises:
            ValueError: If a handler is already registered for this command type
        """
        if command_type in self._handlers:
            raise ValueError(f"Handler already registered for {command_type.__name__}")
        self._handlers[command_type] = handler
        logger.debug("command_handler_registered", command_type=command_type.__name__)

    def unregister(self, command_type: type) -> bool:
        """
        Unregister a handler for a command type.

        Returns:
            True if handler was removed, False if not found
        """
        if command_type in self._handlers:
            del self._handlers[command_type]
            return True
        return False

    def add_middleware(self, middleware: CommandBusMiddleware) -> None:
        """Add middleware to the command bus pipeline.

        Middleware is executed in registration order before the handler.

        Args:
            middleware: The middleware to add.
        """
        self._middleware.append(middleware)
        logger.debug("command_bus_middleware_added", middleware=type(middleware).__name__)

    def send(self, command: "Command") -> Any:
        """
        Send a command through middleware pipeline to its handler.

        Args:
            command: The command to execute

        Returns:
            The result from the handler

        Raises:
            ValueError: If no handler is registered for this command type
        """
        command_type = type(command)
        handler = self._handlers.get(command_type)

        if handler is None:
            raise ValueError(f"No handler registered for {command_type.__name__}")

        logger.debug("command_dispatching", command_type=command_type.__name__)

        # Build middleware chain (innermost = handler.handle)
        def final_handler(cmd: "Command") -> Any:
            return handler.handle(cmd)

        # Wrap in middleware (reverse order so first-registered runs first)
        chain = final_handler
        for mw in reversed(self._middleware):

            def make_step(middleware: CommandBusMiddleware, next_step: Callable) -> Callable:
                def step(cmd: "Command") -> Any:
                    return middleware(cmd, next_step)

                return step

            chain = make_step(mw, chain)

        return chain(command)

    def has_handler(self, command_type: type) -> bool:
        """Check if a handler is registered for the command type."""
        return command_type in self._handlers


class RateLimitMiddleware(CommandBusMiddleware):
    """Middleware that enforces rate limiting on all commands.

    Checks rate limit before allowing command dispatch. Raises
    RateLimitExceeded if the rate limit is exceeded.
    """

    def __init__(self, rate_limiter: "RateLimiter"):
        """Initialize with rate limiter.

        Args:
            rate_limiter: Rate limiter instance to check against.
        """
        self._rate_limiter = rate_limiter

    def __call__(self, command: "Command", next_handler: Callable[["Command"], Any]) -> Any:
        """Check rate limit before dispatching command."""
        # Use command type name as rate limit key for granularity
        key = type(command).__name__
        result = self._rate_limiter.consume(key)

        if not result.allowed:
            # Update Prometheus metrics
            try:
                from mcp_hangar import metrics as prometheus_metrics

                prometheus_metrics.RATE_LIMIT_HITS_TOTAL.inc(endpoint=key)
            except Exception:  # fault-barrier: metrics failure must not block rate limit enforcement
                pass

            from mcp_hangar.domain.exceptions import RateLimitExceeded

            raise RateLimitExceeded(
                limit=result.limit,
                window_seconds=int(1.0 / result.limit) if result.limit else 1,
            )

        return next_handler(command)


# Global command bus instance
_command_bus: CommandBus | None = None


def get_command_bus() -> CommandBus:
    """Get the global command bus instance."""
    global _command_bus
    if _command_bus is None:
        _command_bus = CommandBus()
    return _command_bus


def reset_command_bus() -> None:
    """Reset the global command bus (for testing)."""
    global _command_bus
    _command_bus = None
