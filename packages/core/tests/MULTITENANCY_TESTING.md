# Multi-Tenancy Testing Guide

## Overview

Comprehensive testing for MCP-Hangar's multi-tenancy and governance features.

## Test Coverage

### Unit Tests (`tests/unit/test_tenant.py`)
**19 tests** covering core domain logic:

#### Tenant Aggregate (11 tests)
- ✅ Create tenant with default/custom quotas
- ✅ Suspend/reactivate tenant
- ✅ Quota checks (allow/block/warning)
- ✅ Usage recording (invocations, cold starts, providers)
- ✅ Namespace management (add/remove)
- ✅ Quota updates with events

#### Namespace Aggregate (6 tests)
- ✅ Create namespace
- ✅ Provider tracking (add/remove)
- ✅ Effective quotas (override/inherit from tenant)
- ✅ Quota capping at tenant limit

#### Value Objects (2 tests)
- ✅ TenantId validation (K8s naming rules)
- ✅ NamespaceId validation (K8s naming rules)

### Integration Tests (`tests/integration/test_multitenancy.py`)
**24 tests** covering cross-aggregate interactions:

#### Tenant Isolation (3 tests)
- ✅ Cross-tenant namespace isolation
- ✅ Namespace-tenant ownership
- ✅ Tenant namespace tracking

#### Quota Enforcement (5 tests)
- ✅ Block operations beyond quota
- ✅ Allow operations within quota
- ✅ Per-namespace quota overrides
- ✅ Tool invocation quotas
- ✅ Warning at 80% threshold

#### Suspended Tenant Behavior (3 tests)
- ✅ Block namespace creation when suspended
- ✅ Block provider creation when suspended
- ✅ Resume operations after reactivation

#### Cross-Aggregate Interactions (4 tests)
- ✅ Namespace creation updates tenant usage
- ✅ Provider creation updates namespace and tenant
- ✅ Namespace deletion updates tenant
- ✅ Provider deletion updates counts

#### Event Sourcing (3 tests)
- ✅ Tenant lifecycle events
- ✅ Quota exceeded events
- ✅ Namespace creation events

#### Principal-Tenant Association (3 tests)
- ✅ Principal with tenant_id
- ✅ Organization-level principal (no tenant)
- ✅ System principal access

#### Resource Scope Hierarchy (3 tests)
- ✅ Organization scope includes all tenants
- ✅ Tenant scope includes own namespaces
- ✅ Namespace scope requires exact match

## Running Tests

### All Multi-Tenancy Tests
```bash
pytest tests/unit/test_tenant.py tests/integration/test_multitenancy.py -v
```

### Unit Tests Only
```bash
pytest tests/unit/test_tenant.py -v
```

### Integration Tests Only
```bash
pytest tests/integration/test_multitenancy.py -v
```

### With Coverage
```bash
pytest tests/unit/test_tenant.py tests/integration/test_multitenancy.py --cov=mcp_hangar.domain.model --cov-report=html
```

## Test Results Summary

```
Total: 43 tests
✅ Passed: 43 (100%)
❌ Failed: 0
⏭️  Skipped: 0
⏱️  Duration: ~0.26s
```

## Test Scenarios

### Quota Enforcement Flow
```python
# 1. Create tenant with quota
tenant = Tenant(quotas=TenantQuotas(max_providers=5))

# 2. Check before operation
result = tenant.check_quota("providers", requested_amount=1)
assert result.allowed is True

# 3. Record usage
tenant.record_usage("provider_create", 1)

# 4. Check again when at limit
# (after 4 more creations)
result = tenant.check_quota("providers", requested_amount=1)
assert result.allowed is False  # Quota exceeded
```

### Tenant Isolation
```python
# Each tenant has isolated namespaces
tenant_a = Tenant(tenant_id=TenantId("team-a"), ...)
tenant_b = Tenant(tenant_id=TenantId("team-b"), ...)

namespace_a = Namespace(tenant_id=tenant_a.tenant_id, ...)
namespace_b = Namespace(tenant_id=tenant_b.tenant_id, ...)

# Cannot access across tenants
assert namespace_a.tenant_id != namespace_b.tenant_id
```

### Namespace Quota Overrides
```python
# Tenant has max_providers=100
tenant = Tenant(quotas=TenantQuotas(max_providers=100))

# Namespace restricts to 10
namespace = Namespace(
    quota_overrides=NamespaceQuotaOverrides(max_providers=10)
)

# Effective quota is the lower value
effective = namespace.get_effective_quota("max_providers", 100)
assert effective == 10
```

## Event Verification

All operations emit domain events for audit and reactions:

```python
tenant = Tenant(...)
events = tenant.collect_events()

# TenantCreated event
assert isinstance(events[0], TenantCreated)
assert events[0].tenant_id == "test-team"

# Quota exceeded event
tenant.check_quota("providers", requested_amount=999)
events = tenant.collect_events()
assert any(isinstance(e, QuotaExceeded) for e in events)
```

## Testing Patterns

### Arrange-Act-Assert
```python
def test_quota_check_blocks_when_exceeded(self):
    # Arrange
    quotas = TenantQuotas(max_providers=5)
    tenant = Tenant(quotas=quotas, ...)
    tenant._usage.provider_count = 5

    # Act
    result = tenant.check_quota("providers", requested_amount=1)

    # Assert
    assert result.allowed is False
    assert result.current == 5
```

### Event Collection
```python
def test_suspend_emits_event(self):
    tenant = Tenant(...)
    tenant.collect_events()  # Clear creation events

    # Act
    tenant.suspend("reason", "admin")

    # Assert
    events = tenant.collect_events()
    assert len(events) == 1
    assert isinstance(events[0], TenantSuspended)
```

## Coverage Goals

- **Domain Model**: 100% coverage
- **Value Objects**: 100% coverage
- **Events**: 100% emission verification
- **Integration**: All cross-aggregate flows tested

## Next Steps

1. **Service Layer Tests**: Test TenantService, CatalogService
2. **Repository Tests**: Test persistence layer
3. **API Tests**: Test REST endpoints
4. **E2E Tests**: Full user scenarios
5. **Performance Tests**: Quota enforcement overhead
6. **Load Tests**: Multi-tenant isolation under load

## References

- [Domain Model](../../mcp_hangar/domain/model/)
- [Value Objects](../../mcp_hangar/domain/value_objects.py)
- [Events](../../mcp_hangar/domain/events.py)
- [TASK-003 Specification](../../TASK-003.md)
