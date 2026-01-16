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
from .recovery_service import RecoveryService
from .sqlite_event_store import SQLiteEventStore
from .unit_of_work import SQLiteUnitOfWork

__all__ = [
    "Database",
    "DatabaseConfig",
    "EventSerializationError",
    "EventSerializer",
    "IEventUpcaster",
    "InMemoryAuditRepository",
    "InMemoryEventStore",
    "InMemoryProviderConfigRepository",
    "RecoveryService",
    "UpcasterChain",
    "register_event_type",
    "SQLiteAuditRepository",
    "SQLiteEventStore",
    "SQLiteProviderConfigRepository",
    "SQLiteUnitOfWork",
]
