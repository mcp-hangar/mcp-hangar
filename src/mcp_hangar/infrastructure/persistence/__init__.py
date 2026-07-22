"""Infrastructure persistence layer.

Provides implementations of domain persistence contracts
using SQLite, in-memory storage, and other backends.
"""

import importlib

from .audit_repository import InMemoryAuditRepository, SQLiteAuditRepository
from .config_repository import InMemoryMcpServerConfigRepository, SQLiteMcpServerConfigRepository
from .database import Database, DatabaseConfig
from .event_serializer import EventSerializationError, EventSerializer, register_event_type
from .event_upcaster import IEventUpcaster, UpcasterChain
from .in_memory_event_store import InMemoryEventStore
from .log_buffer import (
    DEFAULT_MAX_LINES,
    McpServerLogBuffer,
    clear_log_buffer_registry,
    get_log_buffer,
    get_or_create_log_buffer,
    remove_log_buffer,
    set_log_buffer,
)
from .recovery_service import RecoveryService
from .unit_of_work import SQLiteUnitOfWork

__all__ = [
    "Database",
    "DatabaseConfig",
    "DEFAULT_MAX_LINES",
    "EventSerializationError",
    "EventSerializer",
    "IEventUpcaster",
    "InMemoryAuditRepository",
    "InMemoryEventStore",
    "InMemoryMcpServerConfigRepository",
    "McpServerLogBuffer",
    "RecoveryService",
    "UpcasterChain",
    "clear_log_buffer_registry",
    "get_log_buffer",
    "get_or_create_log_buffer",
    "register_event_type",
    "remove_log_buffer",
    "set_log_buffer",
    "SQLiteAuditRepository",
    "SQLiteMcpServerConfigRepository",
    "SQLiteUnitOfWork",
]

# legacy aliases
InMemoryProviderConfigRepository = InMemoryMcpServerConfigRepository
SQLiteProviderConfigRepository = SQLiteMcpServerConfigRepository
ProviderLogBuffer = McpServerLogBuffer


def __getattr__(name: str):  # noqa: ANN001
    if name == "SQLiteEventStore":
        try:
            return getattr(importlib.import_module("mcp_hangar.infrastructure.persistence.sqlite_event_store"), name)
        except ImportError as err:
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r} (persistence backend not installed)"
            ) from err
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
