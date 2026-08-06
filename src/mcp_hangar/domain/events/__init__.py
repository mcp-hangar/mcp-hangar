# pyright: reportExplicitAny=false

"""Domain events for MCP Hangar.

Events capture important business occurrences and allow decoupled reactions.

This was a single 2197-line module. It is now a package, split along the section
boundaries the file already carried as comments -- so a reader looking for the
approval events no longer scrolls past four hundred lines of authorization ones.

Every name is re-exported here, so ``from mcp_hangar.domain.events import X``
keeps working unchanged. There are 141 such imports across the tree and this
refactor is deliberately not the place to touch them.

The module order below is definition order and it is load-bearing: the
``Provider*`` aliases subclass their ``McpServer*`` counterparts, so a module may
only import from one listed above it.
"""

from .base import (
    DomainEvent,
)
from .producer import (
    UNKNOWN_PRODUCER,
    current_instance_id,
    set_instance_id,
)
from .lifecycle import (
    CircuitBreakerStateChanged,
    McpServerDegraded,
    McpServerStarted,
    McpServerStateChanged,
    McpServerStopped,
)
from .invocation import (
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from .tasks import (
    DigestMismatchInTask,
    TaskCancelled,
    TaskCompleted,
    TaskConsentDecided,
    TaskCreated,
    TaskFailed,
    TaskInputRequired,
)
from .health import (
    HealthCheckFailed,
    HealthCheckPassed,
    McpServerIdleDetected,
)
from .discovery import (
    DiscoveryCycleCompleted,
    DiscoverySourceHealthChanged,
    McpServerApproved,
    McpServerDiscovered,
    McpServerDiscoveryConfigChanged,
    McpServerDiscoveryLost,
    McpServerQuarantined,
    ProviderApproved,
    ProviderDegraded,
    ProviderDiscovered,
    ProviderDiscoveryConfigChanged,
    ProviderDiscoveryLost,
    ProviderIdleDetected,
    ProviderQuarantined,
    ProviderStarted,
    ProviderStateChanged,
    ProviderStopped,
)
from .auth import (
    ApiKeyCreated,
    ApiKeyRevoked,
    AuthenticationFailed,
    AuthenticationSucceeded,
    AuthorizationDenied,
    AuthorizationGranted,
    CostReportGenerated,
    KeyRotated,
    NamespaceCreated,
    NamespaceDeleted,
    PolicyPushRejected,
    QuotaExceeded,
    QuotaUpdated,
    QuotaWarningThresholdReached,
    RateLimitLockout,
    RateLimitUnlock,
    RoleAssigned,
    RoleRevoked,
    TenantCreated,
    TenantReactivated,
    TenantSuspended,
)
from .operations import (
    BatchCallCompleted,
    BatchInvocationCompleted,
    BatchInvocationRequested,
    ConfigurationReloadFailed,
    ConfigurationReloadRequested,
    ConfigurationReloaded,
    McpServerHotLoaded,
    McpServerHotUnloaded,
    McpServerLoadAttempted,
    McpServerLoadFailed,
)
from .administration import (
    CustomRoleCreated,
    CustomRoleDeleted,
    CustomRoleUpdated,
    McpServerDeregistered,
    McpServerRegistered,
    McpServerUpdated,
    ProviderDeregistered,
    ProviderRegistered,
    ProviderUpdated,
    ToolAccessPolicyCleared,
    ToolAccessPolicySet,
)
from .enforcement import (
    BehavioralDeviationDetected,
    CapabilityViolationDetected,
    DigestMismatchEvent,
    EgressBlocked,
    EgressPolicyCleared,
    EgressPolicySet,
    EgressPolicyViolationObserved,
    McpServerCapabilityQuarantineReleased,
    McpServerCapabilityQuarantined,
    ProviderCapabilityQuarantineReleased,
    ProviderCapabilityQuarantined,
    ResponseTruncated,
)
from .analysis import (
    DetectionRuleMatched,
    EnforcementActionTaken,
)
from .approvals import (
    ToolApprovalDenied,
    ToolApprovalExpired,
    ToolApprovalGranted,
    ToolApprovalRequested,
    ToolRestored,
    ToolWithdrawn,
    ToolWithdrawnRejected,
)
from .interceptors import (
    InterceptorInvoked,
)
from .aliases import (
    ProviderHotLoaded,
    ProviderHotUnloaded,
    ProviderLoadAttempted,
    ProviderLoadFailed,
)


def _legacy_event_type_names() -> dict[str, str]:
    """Map each deprecated event-type name to the name that supersedes it.

    The `provider` -> `mcp_server` rename (2026-04-22) landed after v1.0.1, so
    event stores written by any earlier release hold rows typed `ProviderStarted`,
    `ProviderDiscovered` and so on. Replaying such a row has to produce the
    modern class, and its schema version has to be looked up under the modern
    name, or the row silently misses both its handlers and its upcasters.

    Derived rather than hand-listed: the aliases come in two shapes -- subclasses
    (`ProviderStarted(McpServerStarted)`) and plain assignments
    (`ProviderHotLoaded = McpServerHotLoaded`) -- and a hand-written table would
    drift the moment one is added or retired. `test_legacy_event_names` pins the
    result against the full alias inventory.
    """
    names: dict[str, str] = {}
    for exported_name, obj in list(globals().items()):
        if not (isinstance(obj, type) and issubclass(obj, DomainEvent) and obj is not DomainEvent):
            continue
        if obj.__name__ != exported_name:
            # An assignment alias: the name it is bound to is not its own.
            names[exported_name] = obj.__name__
        else:
            base = obj.__mro__[1]
            if base is not DomainEvent and issubclass(base, DomainEvent):
                names[exported_name] = base.__name__
    return names


LEGACY_EVENT_TYPE_NAMES: dict[str, str] = _legacy_event_type_names()
"""Deprecated event-type name -> the name that supersedes it. See above."""


def canonical_event_type(event_type: str) -> str:
    """Resolve a possibly-deprecated event-type name to the current one."""
    return LEGACY_EVENT_TYPE_NAMES.get(event_type, event_type)


__all__ = [
    "LEGACY_EVENT_TYPE_NAMES",
    "canonical_event_type",
    "ProviderLoadFailed",
    "ProviderLoadAttempted",
    "ProviderHotUnloaded",
    "ProviderHotLoaded",
    "ApiKeyCreated",
    "ApiKeyRevoked",
    "AuthenticationFailed",
    "AuthenticationSucceeded",
    "AuthorizationDenied",
    "AuthorizationGranted",
    "BatchCallCompleted",
    "BatchInvocationCompleted",
    "BatchInvocationRequested",
    "BehavioralDeviationDetected",
    "CapabilityViolationDetected",
    "CircuitBreakerStateChanged",
    "ConfigurationReloadFailed",
    "ConfigurationReloadRequested",
    "ConfigurationReloaded",
    "CostReportGenerated",
    "CustomRoleCreated",
    "CustomRoleDeleted",
    "CustomRoleUpdated",
    "DetectionRuleMatched",
    "DigestMismatchEvent",
    "DigestMismatchInTask",
    "DiscoveryCycleCompleted",
    "DiscoverySourceHealthChanged",
    "UNKNOWN_PRODUCER",
    "DomainEvent",
    "current_instance_id",
    "set_instance_id",
    "EgressBlocked",
    "EgressPolicyCleared",
    "EgressPolicySet",
    "EgressPolicyViolationObserved",
    "EnforcementActionTaken",
    "HealthCheckFailed",
    "HealthCheckPassed",
    "InterceptorInvoked",
    "KeyRotated",
    "McpServerApproved",
    "McpServerCapabilityQuarantineReleased",
    "McpServerCapabilityQuarantined",
    "McpServerDegraded",
    "McpServerDeregistered",
    "McpServerDiscovered",
    "McpServerDiscoveryConfigChanged",
    "McpServerDiscoveryLost",
    "McpServerHotLoaded",
    "McpServerHotUnloaded",
    "McpServerIdleDetected",
    "McpServerLoadAttempted",
    "McpServerLoadFailed",
    "McpServerQuarantined",
    "McpServerRegistered",
    "McpServerStarted",
    "McpServerStateChanged",
    "McpServerStopped",
    "McpServerUpdated",
    "NamespaceCreated",
    "NamespaceDeleted",
    "PolicyPushRejected",
    "ProviderApproved",
    "ProviderCapabilityQuarantineReleased",
    "ProviderCapabilityQuarantined",
    "ProviderDegraded",
    "ProviderDeregistered",
    "ProviderDiscovered",
    "ProviderDiscoveryConfigChanged",
    "ProviderDiscoveryLost",
    "ProviderIdleDetected",
    "ProviderQuarantined",
    "ProviderRegistered",
    "ProviderStarted",
    "ProviderStateChanged",
    "ProviderStopped",
    "ProviderUpdated",
    "QuotaExceeded",
    "QuotaUpdated",
    "QuotaWarningThresholdReached",
    "RateLimitLockout",
    "RateLimitUnlock",
    "ResponseTruncated",
    "RoleAssigned",
    "RoleRevoked",
    "TaskCancelled",
    "TaskCompleted",
    "TaskConsentDecided",
    "TaskCreated",
    "TaskFailed",
    "TaskInputRequired",
    "TenantCreated",
    "TenantReactivated",
    "TenantSuspended",
    "ToolAccessPolicyCleared",
    "ToolAccessPolicySet",
    "ToolApprovalDenied",
    "ToolApprovalExpired",
    "ToolApprovalGranted",
    "ToolApprovalRequested",
    "ToolInvocationCompleted",
    "ToolInvocationFailed",
    "ToolInvocationRequested",
    "ToolRestored",
    "ToolWithdrawn",
    "ToolWithdrawnRejected",
]
