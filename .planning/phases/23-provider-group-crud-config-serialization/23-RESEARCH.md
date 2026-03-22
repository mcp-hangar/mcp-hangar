# Phase 23: Provider/Group CRUD + Config Serialization - Research

**Researched:** 2026-03-22
**Domain:** Python DDD/CQRS - CRUD commands, domain events, REST endpoints, YAML serialization
**Confidence:** HIGH

## Summary

Phase 23 adds full CRUD control for providers and groups via CQRS commands and REST endpoints, plus
a config serializer that dumps in-memory state back to YAML with backup rotation. This is all
within `packages/core` (Python only).

The codebase already has well-established patterns for every layer: frozen dataclass commands,
`CommandHandler` subclasses, `register_*_handlers()` factory functions, Starlette route modules
with a `*_routes` list, and `HangarJSONResponse` for all API responses. Phase 23 follows these
patterns exactly — no new libraries, no architectural invention required.

Three distinct capabilities must be built: (1) provider CRUD CQRS commands + handlers that
mutate `PROVIDERS` state and emit new domain events; (2) group CRUD CQRS commands + handlers that
mutate `GROUPS` state and reuse/extend existing group events; (3) a `config_serializer.py` module
that serializes in-memory `Provider` and `ProviderGroup` objects back to the YAML format that
`load_config()` already parses.

**Primary recommendation:** Follow the existing auth command/handler split pattern
(`auth_commands.py` / `auth_handlers.py`) for provider and group CRUD. Build the config serializer
as the inverse of `_load_provider_config()` / `_load_group_config()` in `server/config.py`.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CRUD-01 | Provider CQRS commands (Create/Update/Delete) + handlers + new domain events (ProviderRegistered with source field, ProviderUpdated, ProviderDeregistered) + REST endpoints POST/PUT/DELETE /api/providers | Commands follow frozen dataclass pattern; handlers mutate PROVIDERS dict / repository; events extend domain/events.py; routes extend server/api/providers.py |
| CRUD-02 | Group CQRS commands (CreateGroup, UpdateGroup, DeleteGroup, AddGroupMember, RemoveGroupMember) + handlers + new GroupUpdated/GroupDeleted events + REST endpoints POST/PUT/DELETE /api/groups + member management endpoints | Group events live in domain/model/provider_group.py; GROUPS dict accessed via ApplicationContext; handler mirrors _load_group_config() logic |
| CRUD-03 | Config serializer module (server/config_serializer.py): serialize_providers(), serialize_groups(), serialize_full_config(), write_config_backup() with rotation (bak1..bak5) + REST endpoints POST /api/config/export, POST /api/config/backup | Serializer is inverse of server/config.py load functions; yaml.dump() already available; existing config_routes extended with two new routes |
</phase_requirements>

---

## Standard Stack

### Core (all already in use — HIGH confidence)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `dataclasses` (stdlib) | 3.11 | Frozen command dataclasses | All commands are `@dataclass(frozen=True)` |
| `yaml` (PyYAML) | already installed | YAML serialization for config backup | Used in `server/config.py` for load |
| `starlette` | already installed | HTTP routing and request/response | All API modules use Starlette |
| `threading` (stdlib) | 3.11 | Thread safety for GROUPS mutation | GROUPS is accessed from multiple threads |
| `pathlib` (stdlib) | 3.11 | Backup rotation file management | Already used in server/config.py |

No new dependencies required.

---

## Architecture Patterns

### Pattern 1: Frozen Dataclass Commands

**What:** CQRS commands are `@dataclass(frozen=True)` subclasses of `Command`.
**When to use:** All state-changing operations.

```python
# Source: packages/core/mcp_hangar/application/commands/commands.py

@dataclass(frozen=True)
class CreateProviderCommand(Command):
    """Command to create and register a new provider."""

    provider_id: str
    mode: str
    command: list[str] | None = None
    image: str | None = None
    endpoint: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    idle_ttl_s: int = 300
    health_check_interval_s: int = 60
    description: str | None = None
    # ... additional optional Provider constructor params
    source: str = "api"  # "api" | "config" | "discovery"
```

**Constraint for frozen dataclasses with mutable default fields:** Use `field(default_factory=...)`.
Fields that use `list` or `dict` as defaults MUST use `field(default_factory=list)` / `field(default_factory=dict)`.

### Pattern 2: CommandHandler Subclass

**What:** Handler receives dependencies via constructor, implements `handle(command) -> Any`.
**File location:** New handler methods go in a new file: `application/commands/crud_handlers.py`.

```python
# Source: packages/core/mcp_hangar/application/commands/handlers.py (pattern)

class CreateProviderHandler(CommandHandler):
    """Handler for CreateProviderCommand."""

    def __init__(self, repository: IProviderRepository, event_bus: EventBus, groups: dict):
        self._repository = repository
        self._event_bus = event_bus
        self._groups = groups

    def handle(self, command: CreateProviderCommand) -> dict[str, Any]:
        if self._repository.exists(command.provider_id):
            raise ValidationError(f"Provider already exists: {command.provider_id}")
        provider = Provider(
            provider_id=command.provider_id,
            mode=command.mode,
            ...
        )
        self._repository.add(command.provider_id, provider)
        self._event_bus.publish(ProviderRegistered(
            provider_id=command.provider_id,
            source=command.source,
            mode=command.mode,
        ))
        return {"provider_id": command.provider_id, "created": True}
```

### Pattern 3: Domain Events as Frozen Dataclasses

**What:** Domain events extend `DomainEvent`, use `@dataclass`, call `super().__init__()` via `__post_init__`.
**File location:** New provider CRUD events go in `domain/events.py`. New group events go in `domain/model/provider_group.py` (following the existing `GroupCreated`, `GroupMemberAdded` pattern already there).

```python
# Source: packages/core/mcp_hangar/domain/events.py (pattern)

@dataclass
class ProviderRegistered(DomainEvent):
    """Published when a provider is registered via API, config, or discovery."""

    provider_id: str
    source: str  # "api" | "config" | "discovery"
    mode: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderUpdated(DomainEvent):
    """Published when a provider configuration is updated."""

    provider_id: str
    source: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderDeregistered(DomainEvent):
    """Published when a provider is deleted/deregistered."""

    provider_id: str
    source: str

    def __post_init__(self):
        super().__init__()
```

For groups, new events follow `Group*` naming (NOT `ProviderGroup*`) and live in `domain/model/provider_group.py`:

```python
@dataclass
class GroupUpdated(DomainEvent):
    """Published when a group configuration is updated."""

    group_id: str

    def __post_init__(self):
        super().__init__()


@dataclass
class GroupDeleted(DomainEvent):
    """Published when a group is deleted."""

    group_id: str

    def __post_init__(self):
        super().__init__()
```

### Pattern 4: Starlette Route Module

**What:** Each API module defines a `*_routes` list of `Route` objects and async handler functions.
**File location:** Extend `server/api/providers.py` and `server/api/groups.py`.

```python
# Source: packages/core/mcp_hangar/server/api/providers.py (pattern)

async def create_provider(request: Request) -> HangarJSONResponse:
    body = await request.json()
    result = await dispatch_command(CreateProviderCommand(
        provider_id=body["provider_id"],
        mode=body["mode"],
        ...
    ))
    return HangarJSONResponse(result, status_code=201)


async def update_provider(request: Request) -> HangarJSONResponse:
    provider_id = request.path_params["provider_id"]
    body = await request.json()
    result = await dispatch_command(UpdateProviderCommand(provider_id=provider_id, **body))
    return HangarJSONResponse(result)


async def delete_provider(request: Request) -> HangarJSONResponse:
    provider_id = request.path_params["provider_id"]
    result = await dispatch_command(DeleteProviderCommand(provider_id=provider_id))
    return HangarJSONResponse(result)

# Extend provider_routes list:
provider_routes = [
    Route("/", list_providers, methods=["GET"]),
    Route("/", create_provider, methods=["POST"]),    # NEW
    Route("/{provider_id:str}", get_provider, methods=["GET"]),
    Route("/{provider_id:str}", update_provider, methods=["PUT"]),    # NEW
    Route("/{provider_id:str}", delete_provider, methods=["DELETE"]),  # NEW
    ...
]
```

**Critical:** Starlette `Route` does not automatically merge same-path routes — each method+path pair is a separate `Route` object. Multiple Route objects can share the same path if they have different methods.

### Pattern 5: Config Serializer (Inverse of Load)

**What:** `server/config_serializer.py` serializes in-memory state to the YAML structure that `server/config.py` already parses.
**Approach:** Study `_load_provider_config()` to derive `_serialize_provider()` — mirror field extraction in reverse.

```python
# Source: packages/core/mcp_hangar/server/config.py (inverse pattern)

def serialize_providers() -> dict[str, Any]:
    """Serialize all in-memory providers to YAML-compatible dict."""
    ctx = get_context()
    result = {}
    for provider_id, provider in ctx.repository.get_all().items():
        result[provider_id] = _serialize_provider(provider)
    return result


def _serialize_provider(provider: Provider) -> dict[str, Any]:
    """Convert Provider aggregate to YAML-compatible spec dict."""
    spec: dict[str, Any] = {
        "mode": provider.mode.value,
        "idle_ttl_s": provider._idle_ttl.value,
        "health_check_interval_s": provider._health_check_interval.value,
    }
    if provider._command:
        spec["command"] = provider._command
    if provider._image:
        spec["image"] = provider._image
    if provider._endpoint:
        spec["endpoint"] = provider._endpoint
    if provider._env:
        spec["env"] = provider._env
    if provider._description:
        spec["description"] = provider._description
    # ... other optional fields
    return spec
```

**Note:** The serializer accesses private attributes (`provider._idle_ttl`, `provider._command`, etc.)
via the aggregate's internal state. This is acceptable because:
(a) the serializer is in the `server/` layer (same layer as `config.py` which already accesses PROVIDERS),
(b) there is no public property API that exposes raw config-level values like `_command` or `_image`.

An alternative is to add a `to_config_dict()` method on `Provider` and `ProviderGroup`. This is
cleaner (encapsulation) and avoids private-attribute access. The planner should choose: add
`to_config_dict()` to `Provider` and `ProviderGroup`, OR access private attributes in the serializer.
**Recommendation: add `to_config_dict()` to both aggregates** — it's the correct DDD pattern and
makes the serializer testable without inspecting internals.

### Pattern 6: Backup Rotation

**What:** `write_config_backup(path)` writes a YAML backup with rotation: config.yaml.bak1..bak5.
**Approach:** Shift existing backups down, oldest (bak5) is deleted.

```python
def write_config_backup(config_path: str) -> str:
    """Write backup with rotation. Returns path of written backup."""
    base = Path(config_path)
    # Rotate: bak4->bak5, bak3->bak4, ..., bak1->bak2
    for i in range(5, 1, -1):
        older = base.with_suffix(f".bak{i}")
        newer = base.with_suffix(f".bak{i - 1}")
        if newer.exists():
            newer.rename(older)
    backup_path = base.with_suffix(".bak1")
    content = yaml.dump(serialize_full_config(), default_flow_style=False, sort_keys=True)
    backup_path.write_text(content, encoding="utf-8")
    return str(backup_path)
```

### Recommended File Structure (new files)

```
mcp_hangar/
├── application/
│   └── commands/
│       ├── crud_commands.py          # CreateProviderCommand, UpdateProviderCommand,
│       │                             # DeleteProviderCommand, CreateGroupCommand,
│       │                             # UpdateGroupCommand, DeleteGroupCommand,
│       │                             # AddGroupMemberCommand, RemoveGroupMemberCommand
│       └── crud_handlers.py          # All 8 CRUD handlers + register_crud_handlers()
├── domain/
│   ├── events.py                     # ADD: ProviderRegistered, ProviderUpdated, ProviderDeregistered
│   └── model/
│       └── provider_group.py         # ADD: GroupUpdated, GroupDeleted events
└── server/
    ├── api/
    │   ├── providers.py              # ADD: create_provider, update_provider, delete_provider routes
    │   ├── groups.py                 # ADD: CRUD routes for groups + member management
    │   └── config.py                 # ADD: export and backup routes
    └── config_serializer.py          # NEW: serialize_providers(), serialize_groups(),
                                      # serialize_full_config(), write_config_backup()
```

### Anti-Patterns to Avoid

- **Accessing `GROUPS` global directly from handlers:** Handlers should receive `groups: dict` via constructor injection, mirroring how `repository` is injected. Do NOT import `GROUPS` inside handlers.
- **Holding `GROUPS` dict lock during provider I/O:** The `delete_group` handler that calls `group.stop_all()` must do so OUTSIDE any lock on the groups dict — snapshot, release, then call.
- **Modifying `PROVIDERS` dict directly from REST handlers:** All mutations MUST go through CQRS handlers, never direct dict mutation in endpoint functions.
- **Using `asyncio` in domain or application layer:** All handlers are synchronous; `dispatch_command` / `dispatch_query` bridge async→sync via `run_in_threadpool`.
- **`ProviderGroup*` naming for new group events:** Use `Group*` (e.g., `GroupUpdated` not `ProviderGroupUpdated`).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| YAML output | Custom serializer | `yaml.dump()` (PyYAML, already installed) | Handles all YAML edge cases |
| File rotation | Manual rename loop | `pathlib.Path.rename()` | Atomic on same filesystem |
| JSON body parsing | Custom body reader | `await request.json()` + try/except JSONDecodeError | Starlette pattern already in place |
| Thread-safe GROUPS mutation | Custom locking | Access via `ApplicationContext` within threadpool + existing `GROUPS` dict + a module-level `threading.Lock` for group dict mutations | Consistent with existing PROVIDERS pattern |

---

## Common Pitfalls

### Pitfall 1: GROUPS dict is not repository-backed

**What goes wrong:** `GROUPS` is a plain `dict[str, ProviderGroup]` (see `server/state.py:87`).
Unlike `PROVIDERS` (which is a `ProviderDict` wrapper around `IProviderRepository`), `GROUPS`
has no thread-safe wrapper. Direct concurrent mutation is unsafe.

**Why it happens:** Groups were not designed with a repository abstraction (yet).

**How to avoid:** Add a module-level `threading.Lock` guard in the group CRUD handler for
all read-modify-write operations on `GROUPS`. For Phase 23, introduce a `_groups_lock` inside
the handler. Pattern:

```python
class CreateGroupHandler(CommandHandler):
    def __init__(self, groups: dict, event_bus: EventBus):
        self._groups = groups
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def handle(self, command: CreateGroupCommand) -> dict[str, Any]:
        with self._lock:
            if command.group_id in self._groups:
                raise ValidationError(f"Group already exists: {command.group_id}")
            group = ProviderGroup(...)
            self._groups[command.group_id] = group
        # Publish event OUTSIDE lock (no I/O under lock)
        self._event_bus.publish(GroupCreated(...))
        return {"group_id": command.group_id, "created": True}
```

### Pitfall 2: Delete provider while running

**What goes wrong:** Deleting a running (READY/DEGRADED/INITIALIZING) provider without stopping
it first leaves an orphaned process. CRUD-01 requires "Deleting a running provider stops it first."

**How to avoid:** `DeleteProviderHandler.handle()` must call `provider.shutdown()` before
removing from repository. Use existing `StopProviderCommand` dispatch or call `provider.shutdown()`
directly:

```python
def handle(self, command: DeleteProviderCommand) -> dict[str, Any]:
    provider = self._repository.get(command.provider_id)
    if provider is None:
        raise ProviderNotFoundError(command.provider_id)
    # Stop if running (outside repository lock — I/O pattern)
    if provider.state not in (ProviderState.COLD, ProviderState.DEAD):
        provider.shutdown()
        # publish ProviderStopped events
        for event in provider.collect_events():
            self._event_bus.publish(event)
    self._repository.remove(command.provider_id)
    self._event_bus.publish(ProviderDeregistered(provider_id=command.provider_id, source="api"))
    return {"provider_id": command.provider_id, "deleted": True}
```

### Pitfall 3: Starlette Route conflict — same path, multiple methods

**What goes wrong:** If two `Route` objects share the same path pattern, Starlette only matches
the first one registered. For example, `Route("/{provider_id:str}", ...)` appears twice for
GET and PUT — the second is silently never reached.

**How to avoid:** Use Starlette's `methods=` parameter to declare multiple methods on a single
`Route`, OR use a single `Route` that dispatches based on `request.method`. The codebase uses
the multiple-Route approach with explicit methods. Both GET and PUT on the same path work correctly
as separate `Route` objects **only if** Starlette is configured to do method-based routing —
which it does natively via the `methods` parameter.

Verify: existing `providers.py` already has `Route("/{provider_id:str}/start", ...)` and
`Route("/{provider_id:str}/stop", ...)` as separate routes — confirming the multiple-Route
per-path pattern works.

**Confirmed safe pattern:**
```python
Route("/{provider_id:str}", get_provider, methods=["GET"]),
Route("/{provider_id:str}", update_provider, methods=["PUT"]),
Route("/{provider_id:str}", delete_provider, methods=["DELETE"]),
```

### Pitfall 4: Serializer accessing Provider private attributes

**What goes wrong:** `Provider` exposes minimal public state (mostly `state`, `id`, `mode`,
`get_tool_names()`). Config-level fields like `_command`, `_image`, `_idle_ttl.value` etc. are
private. Accessing `._command` from `config_serializer.py` violates encapsulation.

**How to avoid:** Add `to_config_dict()` to both `Provider` and `ProviderGroup` aggregates.
This method returns the YAML-compatible config representation and is the clean DDD approach.

Alternatively (acceptable for expedience): access private attributes from `config_serializer.py`
since it is in the `server/` layer, which is already the composition root and has privileged access.

### Pitfall 5: UpdateProvider handler loses existing ProviderGroup membership

**What goes wrong:** If a provider is a member of a group and it is replaced (delete+recreate)
during UpdateProvider, the group's `_members` dict still holds a reference to the OLD provider
object.

**How to avoid:** `UpdateProviderCommand` must NOT replace the Provider object. Instead, it
updates mutable configuration on the existing Provider instance (or marks it for config update
that takes effect on next restart). For Phase 23 scope, the safest approach is:
- If provider is in a group: only update metadata fields (`_description`, `_env`), return error
  for fields that require a restart.
- If provider is COLD or DEAD: can fully reconstruct.

OR: Simplest approach for v5.0 — `UpdateProviderCommand` only updates config fields that do NOT
require rebuilding the aggregate. Full replacement requires explicit Delete + Create.

### Pitfall 6: yaml.dump vs yaml.safe_dump for config serialization

**What goes wrong:** `yaml.dump()` can serialize Python objects with tags (e.g., `!!python/object`).
Config files should only use safe YAML primitives.

**How to avoid:** Always use `yaml.dump(..., Dumper=yaml.SafeDumper)` or `yaml.safe_dump(...)`.

---

## Code Examples

### Register CRUD Handlers Pattern

```python
# packages/core/mcp_hangar/application/commands/crud_handlers.py

def register_crud_handlers(
    command_bus: CommandBus,
    repository: IProviderRepository,
    event_bus: EventBus,
    groups: dict,
) -> None:
    """Register all CRUD command handlers with the command bus."""
    command_bus.register(CreateProviderCommand, CreateProviderHandler(repository, event_bus))
    command_bus.register(UpdateProviderCommand, UpdateProviderHandler(repository, event_bus))
    command_bus.register(DeleteProviderCommand, DeleteProviderHandler(repository, event_bus))
    command_bus.register(CreateGroupCommand, CreateGroupHandler(groups, event_bus))
    command_bus.register(UpdateGroupCommand, UpdateGroupHandler(groups, event_bus))
    command_bus.register(DeleteGroupCommand, DeleteGroupHandler(groups, event_bus))
    command_bus.register(AddGroupMemberCommand, AddGroupMemberHandler(repository, groups, event_bus))
    command_bus.register(RemoveGroupMemberCommand, RemoveGroupMemberHandler(groups, event_bus))
    logger.info("crud_handlers_registered")
```

### Wire into bootstrap/cqrs.py

```python
# packages/core/mcp_hangar/server/bootstrap/cqrs.py
# In init_cqrs():

from ...application.commands.crud_handlers import register_crud_handlers
from ..state import GROUPS

register_crud_handlers(
    runtime.command_bus,
    PROVIDER_REPOSITORY,
    runtime.event_bus,
    GROUPS,
)
```

### Test Pattern for CRUD Handlers

```python
# packages/core/tests/unit/test_crud_command_handlers.py

class TestCreateProviderHandler:
    @pytest.fixture
    def repository(self):
        return InMemoryProviderRepository()

    @pytest.fixture
    def event_bus(self):
        bus = Mock()
        return bus

    @pytest.fixture
    def handler(self, repository, event_bus):
        return CreateProviderHandler(repository, event_bus)

    def test_create_provider_adds_to_repository(self, handler, repository):
        command = CreateProviderCommand(
            provider_id="test-provider",
            mode="subprocess",
            command=["python", "-m", "test"],
        )
        result = handler.handle(command)
        assert repository.exists("test-provider")
        assert result["provider_id"] == "test-provider"

    def test_create_provider_emits_registered_event(self, handler, event_bus):
        command = CreateProviderCommand(
            provider_id="test-provider",
            mode="subprocess",
            command=["python", "-m", "test"],
        )
        handler.handle(command)
        event_bus.publish.assert_called_once()
        event = event_bus.publish.call_args[0][0]
        assert isinstance(event, ProviderRegistered)
        assert event.provider_id == "test-provider"
        assert event.source == "api"

    def test_create_duplicate_raises_validation_error(self, handler, repository):
        command = CreateProviderCommand(
            provider_id="test-provider",
            mode="subprocess",
            command=["python", "-m", "test"],
        )
        handler.handle(command)
        with pytest.raises(ValidationError):
            handler.handle(command)
```

### Test Pattern for REST API Endpoints (from existing test_api_providers.py)

```python
# packages/core/tests/unit/test_api_crud_providers.py

@pytest.fixture
def api_client(mock_context):
    from mcp_hangar.server.api import create_api_router
    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
        app = create_api_router()
        client = TestClient(app, raise_server_exceptions=False)
        yield client


class TestCreateProvider:
    def test_returns_201_on_success(self, api_client):
        response = api_client.post("/providers/", json={
            "provider_id": "new-provider",
            "mode": "subprocess",
            "command": ["python", "-m", "test"],
        })
        assert response.status_code == 201

    def test_returns_409_for_duplicate(self, api_client):
        # Mock command bus raises ValidationError for duplicate
        response = api_client.post("/providers/", json={
            "provider_id": "existing-provider",
            "mode": "subprocess",
        })
        assert response.status_code == 422
```

### Config Serializer Pattern

```python
# packages/core/mcp_hangar/server/config_serializer.py

import yaml
from pathlib import Path
from typing import Any

from ..domain.model import Provider, ProviderGroup
from .context import get_context


def serialize_providers() -> dict[str, Any]:
    """Serialize all providers to YAML-compatible dict."""
    ctx = get_context()
    return {
        provider_id: provider.to_config_dict()
        for provider_id, provider in ctx.repository.get_all().items()
    }


def serialize_groups() -> dict[str, Any]:
    """Serialize all groups to YAML-compatible dict."""
    ctx = get_context()
    return {
        group_id: group.to_config_dict()
        for group_id, group in ctx.groups.items()
    }


def serialize_full_config() -> dict[str, Any]:
    """Serialize complete in-memory state to YAML-compatible dict."""
    return {
        "providers": {**serialize_providers(), **serialize_groups()},
    }


def write_config_backup(config_path: str) -> str:
    """Write current state as YAML backup with rotation (bak1..bak5)."""
    base = Path(config_path)
    # Rotate: shift existing backups up (bak4->bak5, ..., bak1->bak2)
    for i in range(5, 1, -1):
        older = base.parent / f"{base.name}.bak{i}"
        newer = base.parent / f"{base.name}.bak{i - 1}"
        if newer.exists():
            newer.rename(older)
    backup_path = base.parent / f"{base.name}.bak1"
    content = yaml.dump(
        serialize_full_config(),
        default_flow_style=False,
        sort_keys=True,
        allow_unicode=True,
    )
    backup_path.write_text(content, encoding="utf-8")
    return str(backup_path)
```

---

## Interface Contracts for Plan Executor

### File Paths (exact)

| New File | Purpose |
|----------|---------|
| `mcp_hangar/application/commands/crud_commands.py` | 8 CQRS commands (frozen dataclasses) |
| `mcp_hangar/application/commands/crud_handlers.py` | 8 handlers + `register_crud_handlers()` |
| `mcp_hangar/server/config_serializer.py` | 4 public functions |

| Modified File | Change |
|---------------|--------|
| `mcp_hangar/domain/events.py` | Add `ProviderRegistered`, `ProviderUpdated`, `ProviderDeregistered` |
| `mcp_hangar/domain/model/provider_group.py` | Add `GroupUpdated`, `GroupDeleted` events |
| `mcp_hangar/domain/model/provider.py` | Add `to_config_dict() -> dict[str, Any]` method |
| `mcp_hangar/domain/model/provider_group.py` | Add `to_config_dict() -> dict[str, Any]` method |
| `mcp_hangar/server/api/providers.py` | Add `create_provider`, `update_provider`, `delete_provider` + routes |
| `mcp_hangar/server/api/groups.py` | Add group CRUD + member management endpoints + routes |
| `mcp_hangar/server/api/config.py` | Add `export_config`, `backup_config` endpoints + routes |
| `mcp_hangar/server/bootstrap/cqrs.py` | Call `register_crud_handlers()` in `init_cqrs()` |

### Command Signatures (exact)

```python
# crud_commands.py

@dataclass(frozen=True)
class CreateProviderCommand(Command):
    provider_id: str
    mode: str                                      # "subprocess" | "docker" | "remote"
    command: list[str] | None = None
    image: str | None = None
    endpoint: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    idle_ttl_s: int = 300
    health_check_interval_s: int = 60
    max_consecutive_failures: int = 3
    volumes: list[str] = field(default_factory=list)
    network: str = "none"
    read_only: bool = True
    description: str | None = None
    source: str = "api"


@dataclass(frozen=True)
class UpdateProviderCommand(Command):
    provider_id: str
    description: str | None = None
    env: dict[str, str] | None = None
    idle_ttl_s: int | None = None
    health_check_interval_s: int | None = None
    source: str = "api"


@dataclass(frozen=True)
class DeleteProviderCommand(Command):
    provider_id: str
    source: str = "api"


@dataclass(frozen=True)
class CreateGroupCommand(Command):
    group_id: str
    strategy: str = "round_robin"
    min_healthy: int = 1
    description: str | None = None
    auto_start: bool = True
    unhealthy_threshold: int = 2
    healthy_threshold: int = 1
    circuit_failure_threshold: int = 10
    circuit_reset_timeout_s: float = 60.0


@dataclass(frozen=True)
class UpdateGroupCommand(Command):
    group_id: str
    description: str | None = None
    min_healthy: int | None = None
    strategy: str | None = None


@dataclass(frozen=True)
class DeleteGroupCommand(Command):
    group_id: str


@dataclass(frozen=True)
class AddGroupMemberCommand(Command):
    group_id: str
    member_id: str
    weight: int = 1
    priority: int = 1


@dataclass(frozen=True)
class RemoveGroupMemberCommand(Command):
    group_id: str
    member_id: str
```

### Handler Return Shapes

| Handler | Return dict |
|---------|-------------|
| `CreateProviderHandler` | `{"provider_id": str, "created": True}` |
| `UpdateProviderHandler` | `{"provider_id": str, "updated": True}` |
| `DeleteProviderHandler` | `{"provider_id": str, "deleted": True}` |
| `CreateGroupHandler` | `{"group_id": str, "created": True}` |
| `UpdateGroupHandler` | `{"group_id": str, "updated": True}` |
| `DeleteGroupHandler` | `{"group_id": str, "deleted": True}` |
| `AddGroupMemberHandler` | `{"group_id": str, "member_id": str, "added": True}` |
| `RemoveGroupMemberHandler` | `{"group_id": str, "member_id": str, "removed": True}` |

### REST Endpoint Signatures

**Provider CRUD:**

| Method | Path | Handler | Status |
|--------|------|---------|--------|
| POST | `/api/providers/` | `create_provider` | 201 |
| PUT | `/api/providers/{provider_id}` | `update_provider` | 200 |
| DELETE | `/api/providers/{provider_id}` | `delete_provider` | 200 |

**Group CRUD:**

| Method | Path | Handler | Status |
|--------|------|---------|--------|
| POST | `/api/groups/` | `create_group` | 201 |
| PUT | `/api/groups/{group_id}` | `update_group` | 200 |
| DELETE | `/api/groups/{group_id}` | `delete_group` | 200 |
| POST | `/api/groups/{group_id}/members` | `add_group_member` | 201 |
| DELETE | `/api/groups/{group_id}/members/{member_id}` | `remove_group_member` | 200 |

**Config:**

| Method | Path | Handler | Status |
|--------|------|---------|--------|
| POST | `/api/config/export` | `export_config` | 200, returns `{"yaml": "..."}` |
| POST | `/api/config/backup` | `backup_config` | 200, returns `{"path": "..."}` |

### Config Serializer Module Public API

```python
# server/config_serializer.py

def serialize_providers() -> dict[str, Any]:
    """Returns: {provider_id: {mode: ..., command: [...], ...}}"""

def serialize_groups() -> dict[str, Any]:
    """Returns: {group_id: {mode: "group", strategy: ..., members: [...]}}"""

def serialize_full_config() -> dict[str, Any]:
    """Returns: {"providers": {**providers, **groups}}"""

def write_config_backup(config_path: str) -> str:
    """Writes bak1..bak5 rotation. Returns path of bak1."""
```

### Provider.to_config_dict() Signature

```python
# domain/model/provider.py -- new method on Provider

def to_config_dict(self) -> dict[str, Any]:
    """Return YAML-compatible config spec dict.

    Returns the minimal representation needed for round-trip:
    load_config(to_config_dict()) produces an equivalent Provider.
    """
```

### ProviderGroup.to_config_dict() Signature

```python
# domain/model/provider_group.py -- new method on ProviderGroup

def to_config_dict(self) -> dict[str, Any]:
    """Return YAML-compatible config spec dict.

    Includes mode="group", strategy, min_healthy, members list.
    """
```

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest |
| Config file | `pytest.ini` / `pyproject.toml` |
| Quick run command | `pytest tests/unit/test_crud_command_handlers.py -x -q` |
| Full suite command | `pytest tests/unit/ tests/integration/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command |
|--------|----------|-----------|-------------------|
| CRUD-01 | CreateProviderHandler creates provider in repository | unit | `pytest tests/unit/test_crud_command_handlers.py::TestCreateProviderHandler -x` |
| CRUD-01 | CreateProviderHandler emits ProviderRegistered with source field | unit | `pytest tests/unit/test_crud_command_handlers.py::TestCreateProviderHandler::test_emits_registered_event -x` |
| CRUD-01 | UpdateProviderHandler updates existing provider | unit | `pytest tests/unit/test_crud_command_handlers.py::TestUpdateProviderHandler -x` |
| CRUD-01 | DeleteProviderHandler stops running provider then removes | unit | `pytest tests/unit/test_crud_command_handlers.py::TestDeleteProviderHandler -x` |
| CRUD-01 | POST /api/providers returns 201 | unit | `pytest tests/unit/test_api_crud_providers.py::TestCreateProvider -x` |
| CRUD-01 | PUT /api/providers/{id} returns 200 | unit | `pytest tests/unit/test_api_crud_providers.py::TestUpdateProvider -x` |
| CRUD-01 | DELETE /api/providers/{id} returns 200 | unit | `pytest tests/unit/test_api_crud_providers.py::TestDeleteProvider -x` |
| CRUD-02 | CreateGroupHandler creates group in GROUPS dict | unit | `pytest tests/unit/test_crud_command_handlers.py::TestCreateGroupHandler -x` |
| CRUD-02 | DeleteGroupHandler stops all members before removal | unit | `pytest tests/unit/test_crud_command_handlers.py::TestDeleteGroupHandler -x` |
| CRUD-02 | AddGroupMemberHandler adds provider to existing group | unit | `pytest tests/unit/test_crud_command_handlers.py::TestAddGroupMemberHandler -x` |
| CRUD-02 | Group CRUD REST endpoints return expected status codes | unit | `pytest tests/unit/test_api_crud_groups.py -x` |
| CRUD-03 | serialize_providers() produces round-trip compatible dict | unit | `pytest tests/unit/test_config_serializer.py::TestSerializeProviders -x` |
| CRUD-03 | serialize_full_config() includes providers and groups | unit | `pytest tests/unit/test_config_serializer.py::TestSerializeFullConfig -x` |
| CRUD-03 | write_config_backup() creates bak1 file | unit | `pytest tests/unit/test_config_serializer.py::TestWriteConfigBackup -x` |
| CRUD-03 | Backup rotation shifts bak1→bak2→...→bak5, deletes oldest | unit | `pytest tests/unit/test_config_serializer.py::TestBackupRotation -x` |
| CRUD-03 | POST /api/config/export returns YAML string | unit | `pytest tests/unit/test_api_crud_config.py::TestExportConfig -x` |
| CRUD-03 | POST /api/config/backup returns file path | unit | `pytest tests/unit/test_api_crud_config.py::TestBackupConfig -x` |

### Wave 0 Gaps (files that do not yet exist)

- [ ] `tests/unit/test_crud_command_handlers.py` — covers CRUD-01, CRUD-02 handler unit tests
- [ ] `tests/unit/test_api_crud_providers.py` — covers CRUD-01 REST endpoint tests
- [ ] `tests/unit/test_api_crud_groups.py` — covers CRUD-02 REST endpoint tests
- [ ] `tests/unit/test_config_serializer.py` — covers CRUD-03 serializer tests
- [ ] `tests/unit/test_api_crud_config.py` — covers CRUD-03 REST export/backup endpoint tests

---

## Open Questions

1. **UpdateProvider: in-place mutation vs. full replacement?**
   - What we know: `Provider` stores all config as private attributes. Some (like `_idle_ttl`)
     have no public setter. Changing `_mode` after construction would require full rebuild.
   - What's unclear: Does CRUD-01 require that changes take effect immediately on a running
     provider, or only on next start?
   - Recommendation: For v5.0, `UpdateProvider` only updates non-restart fields (`description`,
     `env`, `idle_ttl_s`, `health_check_interval_s`). Structural changes (mode, command, image)
     require Delete + Create, documented in the API response.

2. **Provider.to_config_dict() — handling secrets in env?**
   - What we know: `server/config.py` interpolates `${ENV_VAR}` patterns. Serialized output
     would emit resolved values (secrets in plaintext) or blank strings.
   - What's unclear: Should `to_config_dict()` redact env vars matching sensitive patterns?
   - Recommendation: Apply the same redaction logic from `server/api/config.py::_sanitize()`
     to the `env` dict in `to_config_dict()`. This is optional for Phase 23 but noted for Phase 29.

3. **Group.to_config_dict() — member provider definitions**
   - What we know: Groups reference existing Provider objects. The YAML format includes
     member definitions inline if the provider is not defined in the top-level `providers:` section.
   - What's unclear: For `serialize_full_config()`, should group members be emitted as cross-refs
     (`id: provider-1`) or full inline specs?
   - Recommendation: If the provider_id exists in `serialize_providers()` output, emit only
     `{id: member_id, weight: N, priority: N}`. This mirrors how `_load_group_members()` resolves
     members: "Use already-loaded provider if it exists."

---

## Sources

### Primary (HIGH confidence — direct code inspection)

- `packages/core/mcp_hangar/application/commands/commands.py` — command base pattern
- `packages/core/mcp_hangar/application/commands/handlers.py` — handler pattern + `_get_provider` + `_publish_events`
- `packages/core/mcp_hangar/application/commands/auth_commands.py` + `auth_handlers.py` — split file pattern for domain-specific commands
- `packages/core/mcp_hangar/domain/events.py` — event dataclass pattern
- `packages/core/mcp_hangar/domain/model/provider_group.py` — existing Group events, `GroupCreated/GroupMemberAdded/GroupMemberRemoved`, `to_status_dict()`
- `packages/core/mcp_hangar/domain/model/provider.py` — Provider constructor params, `to_status_dict()`, `shutdown()`
- `packages/core/mcp_hangar/server/api/providers.py` — Route list pattern, `dispatch_command/dispatch_query`
- `packages/core/mcp_hangar/server/api/groups.py` — GROUPS dict access via `get_context()`
- `packages/core/mcp_hangar/server/api/config.py` — config route pattern, existing config_routes
- `packages/core/mcp_hangar/server/api/router.py` — router Mount pattern
- `packages/core/mcp_hangar/server/api/middleware.py` — `dispatch_command`, `dispatch_query`, error envelope
- `packages/core/mcp_hangar/server/config.py` — `_load_provider_config()`, `_load_group_config()` (inverse for serializer)
- `packages/core/mcp_hangar/server/state.py` — `GROUPS` is plain dict (no thread-safe wrapper)
- `packages/core/mcp_hangar/server/bootstrap/cqrs.py` — `init_cqrs()` where new handlers must be registered
- `packages/core/mcp_hangar/domain/repository.py` — `IProviderRepository.add()`, `remove()`, `exists()`
- `packages/core/tests/unit/test_api_providers.py` — REST test pattern with `patch("...get_context")`
- `packages/core/tests/unit/test_api_groups.py` — REST test pattern for groups
- `packages/core/tests/unit/test_auth_command_handlers.py` — handler unit test pattern
- `.planning/milestones/v5.0-REQUIREMENTS.md` — exact field names, event names, endpoint specs

### Secondary (MEDIUM confidence)

- `.planning/STATE.md` section "v5.0 Key Discoveries" — pre-verified findings on existing events/gaps

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — direct code inspection, all libraries already used
- Architecture patterns: HIGH — mirror of existing auth command/handler split
- Pitfalls: HIGH — thread-safety gap in GROUPS dict confirmed from code, other pitfalls are direct analysis
- Serializer design: MEDIUM — open questions on partial update and secret handling, but approach is clear

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable codebase, no fast-moving dependencies)
