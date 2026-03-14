"""Infrastructure persistence layer.

Provides implementations of domain persistence contracts
using SQLite, in-memory storage, and other backends.
"""

from .audit_repository import InMemoryAuditRepository, SQLiteAuditRepository
from .config_repository import InMemoryProviderConfigRepository, SQLiteProviderConfigRepository
from .database import Database, DatabaseConfig
from .event_serializer import EventSerializationError, EventSerializer, register_event_type
from .event_upcaster import IEventUpcaster, UpcasterChain
from .in_memory_event_store import InMemoryEventStore
from .log_buffer import (
    DEFAULT_MAX_LINES,
    ProviderLogBuffer,
    clear_log_buffer_registry,
    get_log_buffer,
    get_or_create_log_buffer,
    remove_log_buffer,
    set_log_buffer,
)
from .recovery_service import RecoveryService
from .sqlite_event_store import SQLiteEventStore
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
    "InMemoryProviderConfigRepository",
    "ProviderLogBuffer",
    "RecoveryService",
    "UpcasterChain",
    "clear_log_buffer_registry",
    "get_log_buffer",
    "get_or_create_log_buffer",
    "register_event_type",
    "remove_log_buffer",
    "set_log_buffer",
    "SQLiteAuditRepository",
    "SQLiteEventStore",
    "SQLiteProviderConfigRepository",
    "SQLiteUnitOfWork",
]
