"""Structured logging configuration using structlog.

This module provides centralized logging configuration for the entire application.
It supports both development (colored, readable) and production (JSON) output formats.

Usage:
    from mcp_hangar.logging_config import setup_logging, get_logger

    # At application startup
    setup_logging(level="INFO", json_format=True)

    # In any module
    logger = get_logger(__name__)
    logger.info("event_name", key="value", count=42)
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Sequence

import structlog
from structlog.types import Processor


def _add_service_context(_logger: logging.Logger, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Add service-level context to all log entries."""
    event_dict.setdefault("service", "mcp-hangar")
    return event_dict


def _sanitize_sensitive_data(_logger: logging.Logger, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive fields from log output."""
    sensitive_keys = {
        "password",
        "secret",
        "token",
        "api_key",
        "authorization",
        "credential",
    }

    def redact(obj: Any, depth: int = 0) -> Any:
        if depth > 5:  # Prevent infinite recursion
            return obj
        if isinstance(obj, dict):
            return {k: "[REDACTED]" if k.lower() in sensitive_keys else redact(v, depth + 1) for k, v in obj.items()}
        if isinstance(obj, list):
            return [redact(item, depth + 1) for item in obj]
        return obj

    return redact(event_dict)


def _drop_color_message_key(_logger: logging.Logger, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Remove the color_message key that uvicorn adds."""
    event_dict.pop("color_message", None)
    return event_dict


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
    development: bool | None = None,
    log_file: str | None = None,
) -> None:
    """Configure structlog for the entire application.

    This function should be called once at application startup, before any logging occurs.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_format: If True, output logs as JSON (recommended for production).
        development: If True, use colored console output. Defaults to not json_format.
        log_file: Optional path to log file. If provided, logs will also be written to this file.
    """
    if development is None:
        development = not json_format

    # Shared processors for all log entries
    shared_processors: Sequence[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        _add_service_context,
        _sanitize_sensitive_data,
        _drop_color_message_key,
        structlog.processors.UnicodeDecoder(),
    ]

    if development:
        # Colored, readable output for development
        renderer: Processor = structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=structlog.dev.plain_traceback,
        )
    else:
        # JSON output for production
        renderer = structlog.processors.JSONRenderer()

    # Configure structlog
    structlog.configure(
        processors=list(shared_processors)
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Create formatter for stdlib logging integration
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=list(shared_processors),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level.upper())

    # Console handler (stderr for MCP compatibility)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Optional file handler
    if log_file:
        try:
            from pathlib import Path

            Path(log_file).parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
            # Always use JSON format for file logs
            file_formatter = structlog.stdlib.ProcessorFormatter(
                foreign_pre_chain=list(shared_processors),
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(),
                ],
            )
            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            root_logger.warning(f"Could not setup file logging: {e}")

    # Silence noisy third-party loggers or ensure they use structlog
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Uvicorn loggers - set higher level to suppress INFO messages
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # MCP library - keep at INFO but it will be formatted by structlog
    # logging.getLogger("mcp").setLevel(logging.INFO)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a configured structlog logger.

    Args:
        name: Logger name (typically __name__). If None, returns root logger.

    Returns:
        A bound structlog logger with all configured processors.

    Example:
        logger = get_logger(__name__)
        logger.info("user_logged_in", user_id=123, ip="192.168.1.1")
    """
    return structlog.get_logger(name)


# Convenience aliases for common log levels
def debug(event: str, **kwargs: Any) -> None:
    """Log a debug message."""
    get_logger().debug(event, **kwargs)


def info(event: str, **kwargs: Any) -> None:
    """Log an info message."""
    get_logger().info(event, **kwargs)


def warning(event: str, **kwargs: Any) -> None:
    """Log a warning message."""
    get_logger().warning(event, **kwargs)


def error(event: str, **kwargs: Any) -> None:
    """Log an error message."""
    get_logger().error(event, **kwargs)


def exception(event: str, **kwargs: Any) -> None:
    """Log an exception with traceback."""
    get_logger().exception(event, **kwargs)
