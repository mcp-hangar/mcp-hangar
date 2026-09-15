"""Configuration loading and mcp_server registration.

Uses ApplicationContext for dependency injection (DIP).

Configuration mutates the shared runtime repository and group registry during
startup so the rest of the server observes the same mcp_server state.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import copy
from dataclasses import dataclass, field
import functools
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, cast, ParamSpec, TypeVar

import yaml

from ..domain.exceptions import ConfigurationError, ConfigurationUnavailableError
from ..domain.model import LoadBalancerStrategy, McpServer, McpServerGroup, McpServerMode
from ..domain.security.input_validator import validate_mcp_server_id
from ..domain.value_objects.capabilities import McpServerCapabilities
from ..domain.value_objects.tool_digest import DigestEnforcement, ToolDigest
from ..application.ports.config_loader import IConfigLoader, PreparedServers
from ..logging_config import get_logger

from .config_schema import ConfigSchemaError, strict_mode, validate_config
from .bootstrap.group_circuit_metric import observe_group_circuit
from .state import get_group_rebalance_saga, get_runtime, GROUPS
from .tools.batch.concurrency import DEFAULT_GLOBAL_CONCURRENCY, DEFAULT_PROVIDER_CONCURRENCY, init_concurrency_manager
from .tools.batch.tenant_admission import configure_tenant_limits, parse_tenant_limits, TenantLimits

if TYPE_CHECKING:
    from ..application.read_models.tool_projection import ToolProjectionRegistry
    from ..domain.policies.header_exposure import HeaderExposurePolicy
    from ..domain.services.tool_access_resolver import PolicyKind, ToolAccessResolver
    from ..domain.value_objects.ui_resource import UiResourcePolicy

logger = get_logger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _mcp_server_repository():
    """Return the shared mcp_server repository."""
    return get_runtime().repository


def _new_policies() -> "ToolAccessResolver":
    from ..domain.services.tool_access_resolver import ToolAccessResolver

    return ToolAccessResolver()


def _new_projections() -> "ToolProjectionRegistry":
    from ..application.read_models.tool_projection import ToolProjectionRegistry

    return ToolProjectionRegistry()


@dataclass
class _StagedConfig:
    """One configuration's servers, groups and governance overlays, before any of them is in effect.

    `load_config` builds everything here, then `commit` puts it in force in an
    order that leaves no gap a concurrent call could fall into (#1424): first
    the tool-access policies, the withdrawals and pins, and the
    `header_exposure` blocks, each swapped in under one lock; then the servers
    and groups they govern. A reload used to clear the policy set and register
    it again server by server, so a call that arrived in between was resolved
    against no policies at all, and a server was in the repository before its
    policy was registered.
    """

    policies: "ToolAccessResolver" = field(default_factory=_new_policies)
    projections: "ToolProjectionRegistry" = field(default_factory=_new_projections)
    header_exposure: "dict[str, HeaderExposurePolicy]" = field(default_factory=dict)
    servers: dict[str, McpServer] = field(default_factory=dict)
    groups: dict[str, McpServerGroup] = field(default_factory=dict)
    #: The spec each server in `servers` was built from: a top-level entry, or
    #: a group's inline member entry.
    specs: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: What each server in `servers` was built with: the `McpServer` arguments
    #: its spec gives, every default applied. A later reload keeps the running
    #: server only when it would build it with the same ones (#1426).
    built_with: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The REST endpoint's stored policies, read when a reload is prepared so
    #: that a store that cannot be read refuses the reload. None: no store.
    stored_policies: list[Any] | None = None

    def keeps(self, mcp_server_id: str, running: Any) -> bool:
        """Whether this configuration keeps *running* as that server, rather than replacing it."""
        return self.servers.get(mcp_server_id) is running

    def commit(self, *, replace: bool) -> None:
        """Put this configuration in force. Nothing here fails on the file: `build_config` checked it.

        Args:
            replace: Whether it replaces the previous configuration, which is
                what a reload does, or adds to it, which is what a first load
                does. See `ToolAccessResolver.adopt_config_policies` for what
                happens to a policy a runtime caller set.
        """
        from ..application.read_models.tool_projection import get_tool_projection_registry
        from ..domain.policies.header_exposure import adopt_header_exposure_policies
        from ..domain.services import get_tool_access_resolver

        # Outside the resolver lock: this may wait on a database.
        stored = _stored_policies_now(self.stored_policies) if replace else None
        resolver = get_tool_access_resolver()
        with resolver.locked():
            resolver.adopt_config_policies(self.policies, replace=replace)
            if stored is not None:
                # What startup does after the file: the REST endpoint's stored
                # policies go over it, so a reload and a restart agree on a
                # scope both define. Under the same lock, so no call is resolved
                # against the file's policy on that scope in between; from rows
                # already read, so nothing waits on the store while it is held.
                _replay_stored_rows(stored)
        get_tool_projection_registry().adopt_config_overlays(self.projections, replace=replace)
        adopt_header_exposure_policies(self.header_exposure, replace=replace)

        repository = _mcp_server_repository()
        for mcp_server_id, mcp_server in self.servers.items():
            repository.add(mcp_server_id, mcp_server)
        if replace:
            _BUILT_FROM.clear()
        _BUILT_FROM.update(
            {sid: (self.built_with[sid], server) for sid, server in self.servers.items() if sid in self.built_with}
        )
        for group_id, group in self.groups.items():
            GROUPS[group_id] = group
            # After the group is in GROUPS, which the gauge's writer checks (#1357).
            observe_group_circuit(group)
        if replace:
            # Replaced, never cleared first: the front door finds a member's
            # group only in GROUPS, so while it was empty a member was checked
            # as a standalone server and its group's deny list, access policies
            # and withdrawals did not apply (#1424).
            for group_id in [known for known in GROUPS if known not in self.groups]:
                del GROUPS[group_id]


#: Each server a configuration built, with the `McpServer` arguments it was
#: built with. A reload keeps the running server only when the file would build
#: it with the same ones (#1424, #1426).
_BUILT_FROM: dict[str, tuple[dict[str, Any], McpServer]] = {}


def _kept_or_built(mcp_server_id: str, built_with: dict[str, Any], built: McpServer) -> McpServer:
    """The running server, when the file would build it with the arguments it was built with; *built* otherwise.

    A reload used to put a fresh copy of every server in the repository and
    stop only the ones it counted as changed. An unchanged running server was
    replaced without a stop: its process kept running outside idle timeout, GC
    and shutdown, and the next call started a second one (#1424). Only the
    object built with these very arguments, and still the running one, is
    kept; anything else is replaced, and the reload stops what it replaces.

    The arguments, not the spec text: every default is applied, so a default
    left out and the same value spelled out are one server (#1426). And only
    what the server is built from. A server's `tools` access block, `access`,
    `tool_access`, `tool_projection` and `header_exposure` go to the policy,
    projection and header-exposure registries, and a group member entry's
    `weight` and `priority` to its group. The configuration swaps all of those
    in whether or not it keeps the server, so changing one keeps the server
    running and still takes effect.

    Still the running one means as configured, too. The REST update endpoint
    rewrites a running server's `env`, `description` and intervals in place,
    and a restart applies the file over such an edit; so does a reload. A
    server whose configurable fields no longer read as the file builds them is
    replaced by one built from the file.
    """
    previous = _BUILT_FROM.get(mcp_server_id)
    if previous is None or previous[0] != built_with:
        return built
    running = _mcp_server_repository().get(mcp_server_id)
    if running is not previous[1] or _runtime_editable(running) != _runtime_editable(built):
        return built
    return running


def _runtime_editable(server: McpServer) -> tuple[Any, ...]:
    """What `McpServer.update_config` can rewrite on a running server, as it reads now.

    Read off the aggregate rather than `to_config_dict()`, which redacts `env`:
    a REST edit of a secret would compare equal.
    """
    return (
        server._description,
        dict(server._env or {}),
        server._idle_ttl.seconds,
        server._health_check_interval.seconds,
    )


def _tap_store() -> Any:
    """The REST endpoint's policy store the running auth components hold, or None."""
    from .context import get_context

    return getattr(getattr(get_context(), "auth_components", None), "tap_store", None)


def _read_stored_policies() -> list[Any] | None:
    """Read the REST endpoint's stored policies while a reload can still be refused (#1424).

    None when there is no store. A store that cannot be read refuses the
    reload: applying the file without its rows would loosen every scope both
    define, and report success.
    """
    store = _tap_store()
    if store is None:
        return None
    try:
        return list(store.list_all_policies())
    except Exception as e:  # noqa: BLE001 -- any store failure refuses the reload, before anything changed
        raise ConfigurationUnavailableError(
            "The stored tool-access policies could not be read, so the reload was refused and nothing was "
            "changed. Reload again once the policy store is reachable."
        ) from e


def _stored_policies_now(read_when_prepared: list[Any] | None) -> list[Any] | None:
    """Read the store again just before the swap, outside the resolver lock.

    So a REST write made after the reload was prepared is not undone by older
    rows. If this read fails, the rows read when the reload was prepared are
    used: by now the reload has stopped servers, and those rows are the store
    as it was a moment ago.
    """
    if read_when_prepared is None:
        return None
    try:
        return list(_tap_store().list_all_policies())
    except Exception as e:  # noqa: BLE001 -- fault-barrier: fall back to the rows read when prepared
        logger.warning("stored_tool_access_policy_reread_failed", error=str(e), error_type=type(e).__name__)
        return read_when_prepared


class _StoredRows:
    """A policy store that answers from rows already read.

    Handed to the replay startup uses, `auth.bootstrap._replay_tap_policies`,
    which asks its store for every row. A reload runs it under the resolver
    lock, where nothing may wait on a database.
    """

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def list_all_policies(self) -> list[Any]:
        return self._rows


def _replay_stored_rows(rows: list[Any]) -> None:
    from ..auth.bootstrap import _replay_tap_policies

    _replay_tap_policies(_StoredRows(rows))


_staged: ContextVar[_StagedConfig | None] = ContextVar("mcp_hangar_staged_config", default=None)


@contextmanager
def _building(staged: _StagedConfig) -> Iterator[_StagedConfig]:
    """Make *staged* the configuration the loaders below write into."""
    token = _staged.set(staged)
    try:
        yield staged
    finally:
        _staged.reset(token)


@contextmanager
def _staging() -> Iterator[_StagedConfig]:
    """Join the configuration being built; or build one, and commit it when the body returns.

    If the body raises, nothing is committed and the running configuration is
    left as it was.
    """
    outer = _staged.get()
    if outer is not None:
        yield outer
        return
    with _building(_StagedConfig()) as staged:
        yield staged
    staged.commit(replace=False)


def _staged_config() -> _StagedConfig:
    """The configuration being built. Only called inside `_staging`."""
    staged = _staged.get()
    if staged is None:
        raise RuntimeError("a configuration is written only inside _staging()")
    return staged


def _within_staging(load: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run *load* inside `_staging`: joining a load in progress, or as a load of its own."""

    @functools.wraps(load)
    def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _staging():
            return load(*args, **kwargs)

    return run


# Environment variable pattern: ${VAR_NAME} or ${VAR_NAME:-default}
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively interpolate environment variables in configuration values.

    Supports patterns:
    - ${VAR_NAME} - Replace with environment variable value
    - ${VAR_NAME:-default} - Replace with value or default if not set

    Args:
        config: Configuration dictionary with potential env var references.

    Returns:
        New dictionary with environment variables interpolated.
    """

    def interpolate_value(value: Any) -> Any:
        if isinstance(value, str):

            def replace_env_var(match: re.Match[str]) -> str:
                var_name = match.group(1)
                default = match.group(2)
                env_value = os.environ.get(var_name)
                if env_value is not None:
                    return env_value
                if default is not None:
                    return default
                raise ConfigurationError(
                    f"Required environment variable '${{{var_name}}}' is not set and has no default. "
                    f"Use '${{{var_name}:-default}}' to provide a default value, "
                    f"or '${{{var_name}:-}}' to explicitly allow an empty value.",
                    details={"var_name": var_name},
                )

            return _ENV_VAR_PATTERN.sub(replace_env_var, value)
        elif isinstance(value, dict):
            return {k: interpolate_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [interpolate_value(item) for item in value]
        return value

    return cast(dict[str, Any], interpolate_value(config))


def _read_config_file(config_path: str) -> Any:
    """Parse a YAML configuration file, and nothing else.

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file is invalid YAML
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(path) as f:
        return yaml.safe_load(f)


def load_config_from_file(config_path: str) -> dict[str, Any]:
    """
    Load configuration from YAML file.

    Reads and checks it (see `prepare_config`); applies nothing. Reload, the
    `serve` logging preload and the config API read a file this way.

    Args:
        config_path: Path to YAML configuration file

    Returns:
        Configuration dictionary

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file is invalid YAML
    """
    return prepare_config(_read_config_file(config_path), source=config_path)


def prepare_config(config: Any, *, source: str) -> dict[str, Any]:
    """Interpolate and check one configuration mapping, wherever it came from.

    The file loader and `bootstrap(config_dict=...)` both come through here, so
    a dict is held to what a file is held to: `${VAR}` resolved once, the
    `mcp_servers` rule, and the schema check with its strict mode (#1415).

    Args:
        config: The parsed document or the caller's dict. Not mutated.
        source: What to name in errors and warnings: a path, or `config_dict`.

    Returns:
        A new, interpolated configuration dictionary.

    Raises:
        ValueError: If it is empty, not a mapping, or has no `mcp_servers`
            section and no discovery to fill one.
        ConfigSchemaError: If it carries an unknown key under strict mode.
        ConfigurationError: If a `${VAR}` has no value and no default.
    """
    # Once, over everything, rather than in the one place that happened to need
    # it first. `${VAR}` was interpolated only inside `mcp_servers.<id>.auth`,
    # while the documentation described it as a property of configuration --
    # the production checklist tells an operator to keep secrets out of the file
    # this way, the transport guide says "configuration values support" it, and
    # the reference documents it for Langfuse keys. All of that was true of one
    # sub-block.
    #
    # Found by running the multi-replica recipe against the published 2.5.0-rc.2
    # image: `persistence.postgresql.password: ${HANGAR_DB_PASSWORD}` reached
    # psycopg2 as those twenty-two literal characters, and three pods failed
    # with `password authentication failed for user "hangar"`. The alternative
    # -- writing the password into the file -- is what the checklist exists to
    # prevent.
    if not config:
        raise ValueError(f"Invalid configuration: missing 'mcp_servers' section in {source}")
    if not isinstance(config, dict):
        raise ValueError(f"Invalid configuration: {source} must be a mapping, not {type(config).__name__}")

    config = _interpolate_env_vars(config)

    if "mcp_servers" not in config:
        # A discovery-only deployment (e.g. container providers found via
        # `discovery.enabled: true`) legitimately has no static mcp_servers
        # section -- servers arrive later via discovery bootstrap. Default to
        # an empty map in that case instead of hard-failing config load.
        # Configs with neither a static section nor a server source configured
        # are still rejected, since that is almost always a typo (e.g. the
        # Helm chart rendering `providers:` instead of `mcp_servers:`, see
        # mcp-hangar/helm-charts#15).
        discovery_config = config.get("discovery")
        discovery_enabled = isinstance(discovery_config, dict) and bool(discovery_config.get("enabled", False))
        if not discovery_enabled:
            raise ValueError(f"Invalid configuration: missing 'mcp_servers' section in {source}")
        config["mcp_servers"] = {}

    _reject_or_warn_on_unknown_keys(config, source)

    return cast(dict[str, Any], config)


def _reject_or_warn_on_unknown_keys(config: dict[str, Any], source: str) -> None:
    """Say something about a key nothing reads, instead of ignoring it.

    Warns today and refuses under `HANGAR_CONFIG_STRICT`; the default becomes
    refusal in 3.0.0. Rejecting is correct -- `auth: {enabledd: true}` is a
    gateway that believes it enabled authentication -- and is also a breaking
    change for anyone carrying a stale key, so it gets a release of notice
    rather than arriving in a patch. See `config_schema.py`.
    """
    problems = validate_config(config)
    if not problems:
        return

    if strict_mode():
        raise ConfigSchemaError(f"Invalid configuration in {source}:\n  " + "\n  ".join(problems))

    for problem in problems:
        logger.warning("unknown_config_key", config_path=source, detail=problem)


def load_config(config: dict[str, Any], *, replace: bool = False) -> None:
    """
    Load mcp_server and group configuration.

    Creates McpServer aggregates and McpServerGroup aggregates based on mode,
    with the governance each one declares: `build_config`, then commit. A load
    that fails part-way leaves the running configuration as it was.

    Args:
        config: Dictionary mapping mcp_server IDs to mcp_server spec dictionaries
        replace: Whether this is the whole configuration, replacing the previous
            one, which is what a reload passes. A policy, withdrawal, pin,
            `header_exposure` block or group the previous file had and this one
            does not is then gone. Without it the entries are added, as on a
            first load.
    """
    build_config(config).commit(replace=replace)


def build_config(config: dict[str, Any]) -> _StagedConfig:
    """Build and check every server, group and governance block in *config*, and put none in force.

    Everything that can refuse a file refuses here -- an `access` or
    `header_exposure` block, a pin, a capabilities block -- so a reload builds
    first and stops servers only once nothing is left to fail on the file
    (#1424). It used to build after stopping them, and a refused block left
    a reload half-applied.

    Every top-level server is built before any group, so a group member that
    names one resolves to it whatever the order in the file (#1437). In file
    order, a group listed before its member's server built the member from the
    member entry alone: a server with no command, and not the one the
    repository held under that id.

    Args:
        config: Dictionary mapping mcp_server IDs to mcp_server spec dictionaries

    Returns:
        The built configuration; `commit` puts it in force.
    """
    with _building(_StagedConfig()) as staged:
        groups: list[tuple[str, dict[str, Any]]] = []
        for mcp_server_id, spec_dict in config.items():
            result = validate_mcp_server_id(mcp_server_id)
            if not result.valid:
                logger.warning("skipping_invalid_mcp_server_id", mcp_server_id=mcp_server_id)
                continue

            mode = spec_dict.get("mode", "subprocess")

            if mode == "group":
                groups.append((mcp_server_id, spec_dict))
                continue

            _load_mcp_server_config(mcp_server_id, spec_dict)

        for group_id, spec_dict in groups:
            _load_group_config(group_id, spec_dict)
    return staged


def _parse_strategy(strategy_str: str, group_id: str) -> LoadBalancerStrategy:
    """Parse load balancer strategy string."""
    try:
        return LoadBalancerStrategy(strategy_str)
    except ValueError:
        logger.warning(
            "unknown_strategy_using_default",
            strategy=strategy_str,
            group_id=group_id,
            default="round_robin",
        )
        return LoadBalancerStrategy.ROUND_ROBIN


#: What a group's member entry sets about its place in the group, rather than
#: about the server: the only keys read when the member is a declared server.
_MEMBER_ENTRY_KEYS = frozenset({"id", "weight", "priority", "tools"})

#: What an inline member entry must set to be a server of its own, by mode: the
#: fields `_load_mcp_server_config` builds each kind of server from (#1437).
_RUNS_WITH: dict[McpServerMode, tuple[str, ...]] = {
    McpServerMode.SUBPROCESS: ("command",),
    McpServerMode.DOCKER: ("image", "build"),
    McpServerMode.CONTAINER: ("image", "build"),
    McpServerMode.REMOTE: ("endpoint",),
}
_RUNS_WITH_HINT = "subprocess needs 'command', docker needs 'image' or 'build', remote needs 'endpoint'"


def _defines_a_server(member_spec: dict[str, Any]) -> bool:
    """Whether a member entry says how to run its server, so it can be built from the entry alone.

    The mode is read as the server reads it, with `McpServerMode.normalize`. A
    mode that does not normalise is left to the server's own check, which
    refuses it. `url` is not read: the loader builds a remote server from
    `endpoint` alone, so a member with only `url` would have no address.
    """
    try:
        mode = McpServerMode.normalize(member_spec.get("mode", "subprocess"))
    except (ValueError, TypeError):
        return True
    required = _RUNS_WITH.get(mode)
    return required is None or any(member_spec.get(key) for key in required)


def _load_group_members(
    group: McpServerGroup,
    group_id: str,
    members: list[dict[str, Any]],
) -> None:
    """Load group members from configuration."""
    from ..domain.model.mcp_server_config import parse_tools_access_config

    saga = get_group_rebalance_saga()
    resolver = _staged_config().policies

    for member_spec in members:
        member_id = member_spec.get("id")
        if not member_id:
            logger.warning("skipping_group_member_without_id", group_id=group_id)
            continue

        result = validate_mcp_server_id(member_id)
        if not result.valid:
            logger.warning("skipping_invalid_member_id", member_id=member_id)
            continue

        # Use the mcp_server this load already built: the top-level entry of
        # that id, which `build_config` builds before any group (#1437), or an
        # earlier group's inline member. Only create a new one from member_spec
        # if not found. Not the running repository: a reload builds before it
        # removes anything, and reusing the running aggregate would ignore an
        # edited inline member (#1424).
        member_mcp_server = _staged_config().servers.get(member_id)
        if member_mcp_server is None:
            if not _defines_a_server(member_spec):
                raise ConfigurationError(
                    f"Group '{group_id}' member '{member_id}' names no server: mcp_servers declares no "
                    f"'{member_id}', and the member entry does not say how to run one ({_RUNS_WITH_HINT}). "
                    f"Declare '{member_id}' under mcp_servers, or give the member entry its own definition."
                )
            member_mcp_server = _load_mcp_server_config(member_id, member_spec)
        else:
            ignored = sorted(set(member_spec) - _MEMBER_ENTRY_KEYS)
            if ignored:
                # The file cannot mean both. The member is the server declared
                # under that id, whatever the member entry says.
                logger.warning(
                    "group_member_entry_settings_ignored",
                    group_id=group_id,
                    member_id=member_id,
                    ignored=ignored,
                    reason="a server of this id is already declared; the member is that server",
                )
            logger.debug(
                "group_member_resolved_from_mcp_servers",
                group_id=group_id,
                member_id=member_id,
                mode=member_mcp_server.mode.value,
            )
        group.add_member(
            member_mcp_server,
            weight=member_spec.get("weight", 1),
            priority=member_spec.get("priority", 1),
        )

        # Parse member-level tool access policy
        member_tools_config = member_spec.get("tools")
        if isinstance(member_tools_config, dict):
            try:
                tools_access_config = parse_tools_access_config(member_tools_config)
                if tools_access_config is not None:
                    member_tools_policy = tools_access_config.to_policy()
                    resolver.set_member_policy(
                        group_id=group_id,
                        member_id=member_id,
                        policy=member_tools_policy,
                        mcp_server_id=member_id,
                    )
                    logger.debug(
                        "member_tool_access_policy_set",
                        group_id=group_id,
                        member_id=member_id,
                        has_allow_list=bool(member_tools_policy.allow_list),
                        has_deny_list=bool(member_tools_policy.deny_list),
                        has_approval_list=bool(member_tools_policy.approval_list),
                    )
            except ValueError as e:
                logger.warning(
                    "invalid_member_tools_access_config",
                    group_id=group_id,
                    member_id=member_id,
                    error=str(e),
                )

        if saga:
            saga.register_member(member_id, group_id)


def _register_config_pins(
    tp_registry: Any,
    mcp_server_id: str,
    pins: Any,
    *,
    tenant_id: str | None,
) -> None:
    """Register a `{tool_name: sha256}` pin block on the projection registry.

    Shared by the all-tenants block (`tool_projection.pins`) and the per-tenant
    one (`tool_projection.tenant_overrides.<tenant>.pins`) so the two cannot
    validate differently. A malformed entry is dropped with a warning naming it,
    which is what the per-tenant block did before it had a sibling.

    Args:
        tp_registry: The ToolProjectionRegistry to register on.
        mcp_server_id: Owning mcp_server identifier.
        pins: The raw mapping from the config file; a non-mapping is ignored.
        tenant_id: Tenant the pins apply to, or ``None`` for all tenants.
    """
    if not isinstance(pins, dict):
        return
    for tool_name, sha256 in pins.items():
        if not (isinstance(tool_name, str) and tool_name):
            continue
        if not isinstance(sha256, str):
            logger.warning(
                "invalid_config_digest_pin",
                mcp_server_id=mcp_server_id,
                tool=tool_name,
                tenant_id=tenant_id,
                error="pin value must be a string sha256",
            )
            continue
        try:
            digest = ToolDigest(tool_name=tool_name, sha256=sha256)
        except ValueError as e:
            logger.warning(
                "invalid_config_digest_pin",
                mcp_server_id=mcp_server_id,
                tool=tool_name,
                tenant_id=tenant_id,
                error=str(e),
            )
            continue
        tp_registry.set_config_pin(mcp_server_id, tool_name, tenant_id, digest)
        logger.debug(
            "config_digest_pin_registered",
            mcp_server_id=mcp_server_id,
            tool=tool_name,
            tenant_id=tenant_id,
        )


#: Kinds an ``access:`` block may govern (#1028). Tools are NOT here: they keep
#: their existing ``tools:`` block, because a second spelling for a policy that
#: already has one is how two configs come to mean different things.
_ACCESS_KINDS: tuple["PolicyKind", ...] = ("prompt", "resource")

#: ``tool_projection`` keys that withdraw something, and what they withdraw.
#: ``withdrawn`` keeps its bare, tool-only meaning (#1028 backward compat).
_WITHDRAWAL_KEYS: dict[str, str] = {
    "withdrawn": "tool",
    "withdrawn_prompts": "prompt",
    "withdrawn_resources": "resource",
}


def _register_access_policies(
    access_config: Any,
    register: Callable[[Any, "PolicyKind"], None],
    *,
    where: str,
) -> None:
    """Register one ``access:`` block's per-kind policies (#1028).

    ::

        access:
          prompt:   {deny_list: ["draft_*"]}
          resource: {allow_list: ["docs://*"]}

    Same parser, same value object and same resolver as ``tools:`` -- only the
    kind the policy is keyed under differs, so prompts and resources inherit the
    merge semantics, the per-tenant overlays and the fail-closed front-door
    branch instead of growing a weaker copy of them.

    ``approval_list`` is the exception, and it is REFUSED here (#1042). It was
    documented as inherited too, and it is not: ``requires_approval()`` has one
    consumer, the tool call path, so an approval-listed prompt or resource was
    served immediately -- fail-open, while the startup check refused the boot
    over the same three lines. A configuration that asks for enforcement no path
    performs is refused rather than accepted quietly, the way per-tenant pins
    without an identity are (#902). Whether the hold belongs on
    ``resources/read`` / ``prompts/get`` at all is #1045; until that is answered
    the answer here is "not supported", not "invalid".

    A missing or non-mapping block registers nothing, which leaves that kind
    unrestricted for this scope -- the rule tools have always followed for an
    undefined scope, applied per kind.

    Raises:
        ConfigurationError: When a non-tool kind carries an ``approval_list``.
    """
    if not isinstance(access_config, dict):
        return

    from ..domain.model.mcp_server_config import parse_tools_access_config

    for unknown in sorted(set(access_config) - set(_ACCESS_KINDS)):
        logger.warning("unknown_access_kind", where=where, kind=unknown, known=list(_ACCESS_KINDS))

    for kind in _ACCESS_KINDS:
        spec = access_config.get(kind)
        if not isinstance(spec, dict):
            continue
        try:
            parsed = parse_tools_access_config(spec)
        except ValueError as e:
            logger.warning("invalid_access_config", where=where, kind=kind, error=str(e))
            continue
        if parsed is None:
            continue
        if parsed.approval_list:
            from ..domain.exceptions import ConfigurationError

            raise ConfigurationError(
                f"access.{kind}.approval_list on {where} asks for a human approval hold that no "
                f"{kind} path performs: the gate runs on tool calls only, so an approval-listed "
                f"{kind} would be served immediately while the startup check refused the boot over "
                "it. Use deny_list to withhold it, or track #1045 for the hold itself."
            )
        register(parsed.to_policy(), kind)
        logger.debug("access_policy_set", where=where, kind=kind)


def _register_config_withdrawals(
    tp_registry: Any,
    mcp_server_id: str,
    block: dict[str, Any],
    tenant_id: str | None,
) -> None:
    """Apply every ``withdrawn*`` list in one ``tool_projection`` scope.

    ``withdrawn:`` still means tools and only tools; ``withdrawn_prompts:`` and
    ``withdrawn_resources:`` withdraw the other two kinds through the same
    overlay (#1028). Resources are named by their UPSTREAM uri, the form the
    policy patterns and the ``ui://`` guard also read.
    """
    for key, kind in _WITHDRAWAL_KEYS.items():
        names = block.get(key, [])
        if not isinstance(names, list):
            continue
        for name in names:
            if not (isinstance(name, str) and name):
                continue
            tp_registry.set_config_withdrawal(mcp_server_id, name, tenant_id=tenant_id, kind=kind)
            logger.debug(
                "config_withdrawal_registered",
                mcp_server_id=mcp_server_id,
                tool=name,
                kind=kind,
                tenant_id=tenant_id,
            )


def _register_header_exposure_block(scope_id: str, block: Any) -> None:
    """Apply one ``header_exposure:`` block (#1057), whatever scope declared it.

    A malformed block is refused rather than warn-skipped. `deny_annotated`
    exists to stop a secret being handed to every intermediary on the path; a
    typo that silently resolves to the default would report the control as on
    while nothing is denied.
    """
    from ..domain.policies.header_exposure import HeaderExposurePolicy

    policy = HeaderExposurePolicy.from_config(block)
    if policy is None:
        return
    _staged_config().header_exposure[scope_id] = policy
    logger.debug(
        "header_exposure_registered",
        scope_id=scope_id,
        on_violation=policy.on_violation,
        patterns=len(policy.deny_annotated),
    )


def _register_tool_projection_block(scope_id: str, tool_projection_config: Any) -> None:
    """Apply one ``tool_projection:`` block, whatever scope declared it (#1038).

    Withdrawals are a config overlay on the ToolProjectionRegistry, so
    ``resolve()`` returns a withdrawn projection for the named tools even before
    they are discovered by ``build_from_tools`` (see #244 design note).

    Groups were silently excluded until #1038: only the server branch read this
    block, so a group could declare neither a withdrawal nor a pin, and the
    prompts and resources surfaces -- which look a group up under its GROUP id --
    had no id under which either control could be both declared and read.

    Args:
        scope_id: The mcp_server or group id the entries are keyed under.
        tool_projection_config: The block, or anything else (ignored).
    """
    if not isinstance(tool_projection_config, dict):
        return

    tp_registry = _staged_config().projections

    # Digest-enforcement mode for pin mismatches (audit/warn/block).
    enforcement_raw = tool_projection_config.get("digest_enforcement")
    if enforcement_raw is not None:
        try:
            tp_registry.set_digest_enforcement(scope_id, DigestEnforcement(enforcement_raw))
        except ValueError:
            logger.warning(
                "invalid_digest_enforcement_config",
                mcp_server_id=scope_id,
                value=enforcement_raw,
            )

    # All-tenants digest pins: {tool_name: sha256}. The counterpart of
    # `withdrawn:`, and the only pin that holds a caller carrying no tenant
    # identity -- which is every caller when auth is off (#902).
    _register_config_pins(tp_registry, scope_id, tool_projection_config.get("pins", {}), tenant_id=None)

    # Global withdrawals (all tenants), of every kind.
    _register_config_withdrawals(tp_registry, scope_id, tool_projection_config, tenant_id=None)

    tenant_overrides_config = tool_projection_config.get("tenant_overrides", {})
    if isinstance(tenant_overrides_config, dict):
        for tenant_id_key, tenant_spec in tenant_overrides_config.items():
            if not isinstance(tenant_spec, dict):
                continue
            _register_config_withdrawals(tp_registry, scope_id, tenant_spec, tenant_id=tenant_id_key)
            _register_config_pins(tp_registry, scope_id, tenant_spec.get("pins", {}), tenant_id=tenant_id_key)


@_within_staging
def _load_mcp_server_config(mcp_server_id: str, spec_dict: dict[str, Any]) -> McpServer:  # noqa: C901 -- baseline CC=37; split before extending
    """Load a single mcp_server configuration.

    Joins the configuration `load_config` is building; called on its own, it is
    a load of one server and is in force when it returns.
    """
    from ..domain.model.mcp_server_config import parse_tools_access_config

    user = spec_dict.get("user")
    if user == "current":
        user = f"{os.getuid()}:{os.getgid()}"

    # Parse tools config - can be either:
    # 1. A list of predefined tool schemas
    # 2. A dict with allow_list/deny_list/approval_list for access policy
    tools_config = spec_dict.get("tools")
    tools = None
    tools_access_policy = None

    if tools_config:
        if isinstance(tools_config, list):
            # List format: predefined tool schemas
            tools = []
            for tool_spec in tools_config:
                tools.append(
                    {
                        "name": tool_spec.get("name"),
                        "description": tool_spec.get("description", ""),
                        "inputSchema": tool_spec.get("inputSchema", tool_spec.get("input_schema", {})),
                        "outputSchema": tool_spec.get("outputSchema", tool_spec.get("output_schema")),
                    }
                )
        elif isinstance(tools_config, dict):
            # Dict format: access policy (allow_list / deny_list / approval_list)
            try:
                tools_access_config = parse_tools_access_config(tools_config)
                if tools_access_config is not None:
                    tools_access_policy = tools_access_config.to_policy()
            except ValueError as e:
                logger.warning(
                    "invalid_tools_access_config",
                    mcp_server_id=mcp_server_id,
                    error=str(e),
                )

    # Process auth configuration for remote mcp_servers.
    #
    # Not interpolated here. `load_config_from_file` does it once over the whole
    # document, and this block used to do it a second time -- left behind when
    # the call moved outwards. A second pass is not idempotent: it reads the
    # *result* of the first one, so a secret that legitimately contains `${...}`
    # -- which generated passwords produce -- is taken as another reference.
    # `R9${x}q!` failed the boot with "Required environment variable '${x}' is
    # not set", and if `x` happened to exist the credential was substituted
    # again and silently wrong, which is the worse of the two.
    auth_config = spec_dict.get("auth")

    # Parse capabilities declaration
    capabilities_data = spec_dict.get("capabilities")
    capabilities = None
    if capabilities_data is not None:
        try:
            capabilities = McpServerCapabilities.from_dict(capabilities_data)
        except (ValueError, TypeError) as e:
            from ..domain.exceptions import ConfigurationError

            raise ConfigurationError(f"Invalid capabilities for mcp_server '{mcp_server_id}': {e}") from e
    else:
        logger.warning(
            "mcp_server_no_capabilities_declared",
            mcp_server_id=mcp_server_id,
            hint="Add a 'capabilities' block to declare resource requirements",
        )

    # Everything the server is built from, and nothing else: a reload keeps the
    # running server when these are unchanged (#1426).
    built_with: dict[str, Any] = {
        "mode": spec_dict.get("mode", "subprocess"),
        "command": spec_dict.get("command"),
        "image": spec_dict.get("image"),
        "endpoint": spec_dict.get("endpoint"),
        "env": spec_dict.get("env", {}),
        "idle_ttl_s": spec_dict.get("idle_ttl_s", 300),
        "health_check_interval_s": spec_dict.get("health_check_interval_s", 60),
        "max_consecutive_failures": spec_dict.get("max_consecutive_failures", 3),
        "volumes": spec_dict.get("volumes", []),
        "build": spec_dict.get("build"),
        "resources": spec_dict.get("resources", {"memory": "512m", "cpu": "1.0"}),
        "network": spec_dict.get("network") or spec_dict.get("network_mode", "none"),
        "read_only": spec_dict.get("read_only", True),
        "user": user,
        "container_command": spec_dict.get("command"),  # For docker mode: override entrypoint
        "container_args": spec_dict.get("args"),  # For docker mode: override CMD
        "description": spec_dict.get("description"),
        "tools": tools,
        # HTTP transport configuration
        "auth": auth_config,
        "tls": spec_dict.get("tls"),
        "http": spec_dict.get("http"),
        # Capability declarations
        "capabilities": capabilities,
    }
    # A copy, so nothing the server does with its arguments changes what it is compared by.
    _staged_config().built_with[mcp_server_id] = copy.deepcopy(built_with)
    mcp_server = _kept_or_built(
        mcp_server_id, _staged_config().built_with[mcp_server_id], McpServer(mcp_server_id=mcp_server_id, **built_with)
    )
    _staged_config().servers[mcp_server_id] = mcp_server
    _staged_config().specs[mcp_server_id] = spec_dict

    # Register tool access policy if configured
    if tools_access_policy is not None:
        resolver = _staged_config().policies
        resolver.set_mcp_server_policy(mcp_server_id, tools_access_policy)

        # Update metrics
        from ..metrics import TOOL_ACCESS_POLICY_ACTIVE

        TOOL_ACCESS_POLICY_ACTIVE.set(1, mcp_server=mcp_server_id)

        logger.debug(
            "mcp_server_tool_access_policy_set",
            mcp_server_id=mcp_server_id,
            has_allow_list=bool(tools_access_policy.allow_list),
            has_deny_list=bool(tools_access_policy.deny_list),
            has_approval_list=bool(tools_access_policy.approval_list),
        )

    # Parse the mcp_server-level prompt / resource policies (#1028). Keyed by
    # kind in the SAME resolver the `tools:` block above feeds, so one config
    # reload cannot leave the two surfaces disagreeing.
    _register_access_policies(
        spec_dict.get("access"),
        lambda policy, kind: _staged_config().policies.set_mcp_server_policy(mcp_server_id, policy, kind=kind),
        where=f"mcp_servers.{mcp_server_id}",
    )

    # Parse per-tenant (member-scope) tool access policies:
    # tool_access:
    #   member:
    #     "tenant:a":
    #       deny_list: [dangerous_tool]
    #       access:
    #         prompt: {deny_list: [internal_*]}
    tool_access_config = spec_dict.get("tool_access")
    if isinstance(tool_access_config, dict):
        member_policies_config = tool_access_config.get("member", {})
        if isinstance(member_policies_config, dict):
            resolver = _staged_config().policies
            for tenant_id, member_policy_spec in member_policies_config.items():
                if not isinstance(member_policy_spec, dict):
                    continue

                def _register_member_access(policy: Any, kind: "PolicyKind", tenant_id: str = tenant_id) -> None:
                    resolver.set_standalone_member_policy(mcp_server_id, tenant_id, policy, kind=kind)

                _register_access_policies(
                    member_policy_spec.get("access"),
                    _register_member_access,
                    where=f"mcp_servers.{mcp_server_id}.tool_access.member.{tenant_id}",
                )
                try:
                    member_tools_cfg = parse_tools_access_config(member_policy_spec)
                    if member_tools_cfg is not None:
                        member_policy = member_tools_cfg.to_policy()
                        resolver.set_standalone_member_policy(mcp_server_id, tenant_id, member_policy)
                        logger.debug(
                            "standalone_member_tool_access_policy_set",
                            mcp_server_id=mcp_server_id,
                            tenant_id=tenant_id,
                            has_allow_list=bool(member_policy.allow_list),
                            has_deny_list=bool(member_policy.deny_list),
                            has_approval_list=bool(member_policy.approval_list),
                        )
                except ValueError as e:
                    logger.warning(
                        "invalid_standalone_member_tools_access_config",
                        mcp_server_id=mcp_server_id,
                        tenant_id=tenant_id,
                        error=str(e),
                    )

    _register_tool_projection_block(mcp_server_id, spec_dict.get("tool_projection"))
    _register_header_exposure_block(mcp_server_id, spec_dict.get("header_exposure"))

    # Register per-mcp_server concurrency limit if specified
    mcp_server_max_concurrency = spec_dict.get("max_concurrency")
    if mcp_server_max_concurrency is not None:
        from .tools.batch.concurrency import get_concurrency_manager

        try:
            cm = get_concurrency_manager()
            cm.set_mcp_server_limit(mcp_server_id, int(mcp_server_max_concurrency))
        except Exception as e:  # noqa: BLE001 -- fault-barrier: concurrency config failure must not crash mcp_server setup
            logger.warning(
                "mcp_server_concurrency_limit_failed",
                mcp_server_id=mcp_server_id,
                max_concurrency=mcp_server_max_concurrency,
                error=str(e),
            )

    logger.debug(
        "mcp_server_loaded",
        mcp_server_id=mcp_server_id,
        mode=spec_dict.get("mode", "subprocess"),
        max_concurrency=mcp_server_max_concurrency,
    )
    return mcp_server


def _load_group_config(group_id: str, spec_dict: dict[str, Any]) -> None:
    """Load a mcp_server group configuration, into the configuration `load_config` is building."""
    from ..domain.model.mcp_server_config import parse_tools_access_config

    strategy = _parse_strategy(spec_dict.get("strategy", "round_robin"), group_id)
    health_config = spec_dict.get("health", {})
    circuit_config = spec_dict.get("circuit_breaker", {})

    group = McpServerGroup(
        group_id=group_id,
        strategy=strategy,
        min_healthy=spec_dict.get("min_healthy", 1),
        auto_start=spec_dict.get("auto_start", True),
        unhealthy_threshold=health_config.get("unhealthy_threshold", 2),
        healthy_threshold=health_config.get("healthy_threshold", 1),
        # `reset_timeout_s` is not read: it never had an effect on a group and
        # was removed (#1398). `config_schema` names it at load.
        circuit_failure_threshold=circuit_config.get("failure_threshold", 10),
        description=spec_dict.get("description"),
    )

    # Parse group-level tool access policy
    group_tools_config = spec_dict.get("tools")
    group_tools_policy = None
    if isinstance(group_tools_config, dict):
        try:
            tools_access_config = parse_tools_access_config(group_tools_config)
            if tools_access_config is not None:
                group_tools_policy = tools_access_config.to_policy()
        except ValueError as e:
            logger.warning(
                "invalid_group_tools_access_config",
                group_id=group_id,
                error=str(e),
            )

    # Register group-level policy
    if group_tools_policy is not None:
        resolver = _staged_config().policies
        resolver.set_group_policy(group_id, group_tools_policy)

        # Update metrics
        from ..metrics import TOOL_ACCESS_POLICY_ACTIVE

        TOOL_ACCESS_POLICY_ACTIVE.set(1, mcp_server=group_id)

        logger.debug(
            "group_tool_access_policy_set",
            group_id=group_id,
            has_allow_list=bool(group_tools_policy.allow_list),
            has_deny_list=bool(group_tools_policy.deny_list),
            has_approval_list=bool(group_tools_policy.approval_list),
        )

    # Group-level prompt / resource policies (#1028), same block shape as a
    # server's. A group member is checked against its group on every surface.
    _register_access_policies(
        spec_dict.get("access"),
        lambda policy, kind: _staged_config().policies.set_group_policy(group_id, policy, kind=kind),
        where=f"mcp_servers.{group_id}",
    )

    # A group is a governed scope like a server: its own withdrawals and pins,
    # keyed under the group id -- which is the id the prompts and resources
    # surfaces resolve a group by (#1038).
    _register_tool_projection_block(group_id, spec_dict.get("tool_projection"))
    _register_header_exposure_block(group_id, spec_dict.get("header_exposure"))

    _load_group_members(group, group_id, spec_dict.get("members", []))

    # Parse per-tenant canary / version routing policy (#275). Targets are
    # validated against actual group members; invalid entries are warn-skipped.
    canary_config = spec_dict.get("canary")
    if isinstance(canary_config, dict):
        from ..domain.model.mcp_server_group import CanaryPolicy

        canary_member = canary_config.get("member", "") or ""
        split_pct = canary_config.get("split_pct", 0)
        if not isinstance(split_pct, int) or isinstance(split_pct, bool) or not (0 <= split_pct <= 100):
            logger.warning("invalid_canary_split_pct", group_id=group_id, value=split_pct)
            split_pct = 0
        if canary_member and group.get_member(canary_member) is None:
            logger.warning("canary_member_not_in_group", group_id=group_id, member=canary_member)
            canary_member = ""
        pinned: dict[str, str] = {}
        pinned_raw = canary_config.get("pinned_tenants", {})
        if isinstance(pinned_raw, dict):
            for tenant, member_id in pinned_raw.items():
                if isinstance(tenant, str) and isinstance(member_id, str) and group.get_member(member_id) is not None:
                    pinned[tenant] = member_id
                else:
                    logger.warning("invalid_canary_pin", group_id=group_id, tenant=tenant, member=member_id)
        if canary_member or pinned:
            group.set_canary_policy(
                CanaryPolicy(canary_member=canary_member, split_pct=split_pct, pinned_tenants=pinned)
            )
            logger.info(
                "group_canary_policy_set",
                group_id=group_id,
                canary_member=canary_member or None,
                split_pct=split_pct,
                pins=len(pinned),
            )

    # Into GROUPS, with its circuit gauge, when the configuration is committed.
    _staged_config().groups[group_id] = group
    logger.info(
        "group_loaded",
        group_id=group_id,
        member_count=group.total_count,
        strategy=strategy.value,
    )


def _init_topology_mode_from_config(full_config: dict[str, Any]) -> None:
    """Apply tool_access.mode from the top-level config to the resolver.

    Valid values: "egress" (default, backward-compatible) | "front_door".

    An ABSENT key still means "egress": a deployment that never opted in must
    not be silently switched to the fail-closed topology by an upgrade.

    An UNRECOGNISED value is a different situation and is now a hard error. An
    operator who wrote ``mode: front-door`` (hyphen) or ``mode: frontdoor``
    was configuring this deliberately, and quietly resolving their typo to
    ``egress`` hands them the permissive topology while their config file says
    otherwise -- a warning in the log is not a fair trade for that. Refusing to
    start puts the failure where the operator is already looking.

    Args:
        full_config: Full configuration dictionary.

    Raises:
        ConfigurationError: If tool_access.mode is present but not a valid mode.
    """
    from ..domain.services import get_tool_access_resolver
    from ..domain.services.tool_access_resolver import configured_topology_mode

    # Parsed where the reload handler parses it too, so a reload compares the
    # file with the running mode by the same rule startup applied it by. A
    # reload never changes the mode: it refuses a file that would.
    mode = configured_topology_mode(full_config)
    get_tool_access_resolver().set_topology_mode(mode)
    logger.debug("tool_access_topology_mode_set", mode=mode)


def _init_required_catalogue_from_config(full_config: dict[str, Any]) -> None:
    """Apply ``tool_access.required_catalogue`` (#1446).

    ::

        tool_access:
          mode: front_door
          required_catalogue:
            servers: [payments, search-pool]
            retry_for_s: 600

    The servers a front-door replica must have projected once before
    ``/health/ready`` answers 200, for at most ``retry_for_s`` after the
    configuration is first applied. The window counts from that first apply,
    before the rest of boot and the warm-up, so ``retry_for_s`` must cover
    those too; see `server/catalogue_readiness.py`. Absent
    means readiness keeps today's rule. In ``egress`` the block is checked, so a
    name that is not in ``mcp_servers`` is refused there too, and then not
    applied: readiness there does not depend on backends.

    Raises:
        ConfigurationError: If the block is malformed or names an unknown server.
    """
    from ..domain.services.tool_access_resolver import configured_topology_mode
    from .catalogue_readiness import configure_required_catalogue, required_catalogue

    required = required_catalogue(full_config)
    if required is not None and configured_topology_mode(full_config) != "front_door":
        logger.info("required_catalogue_not_applied", reason="egress")
        required = None
    configure_required_catalogue(required)


def _init_param_validation_from_config(full_config: dict[str, Any]) -> None:
    """Apply ``headers.param_validation.required`` (ADR-025 Decision 2).

    ::

        headers:
          param_validation:
            required: true

    Off by default. On, a ``tools/call`` whose ``Mcp-Param-*`` headers could not
    be validated against its body -- the SDK's pre-dispatch schema listing
    failed, so it skipped the check and would dispatch anyway -- is refused with
    ``HEADER_MISMATCH`` instead of served.

    Global to the front door rather than per-server: the condition it reacts to
    is a property of the request, not of the upstream the call would reach, so
    it does not belong beside the per-server ``header_exposure:`` block even
    though the two govern the same SEP.

    An unrecognised value is a hard error for the same reason ``tool_access.mode``
    is: quietly resolving ``required: "yes"`` to off hands an operator the
    permissive behaviour while their config file says otherwise.

    Args:
        full_config: Full configuration dictionary.

    Raises:
        ConfigurationError: If ``required`` is present but not a boolean.
    """
    from ..fastmcp_server.flat_tool_projection import set_param_validation_required

    required = _param_validation_required(full_config)
    set_param_validation_required(required)
    if required:
        logger.info("param_validation_required_enabled")


def _param_validation_required(full_config: dict[str, Any]) -> bool:
    """``headers.param_validation.required``, checked. Absent means off."""
    headers_config = full_config.get("headers")
    block = headers_config.get("param_validation") if isinstance(headers_config, dict) else None
    raw = block.get("required") if isinstance(block, dict) else None

    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise ConfigurationError(
            f"Invalid headers.param_validation.required {raw!r}. It must be a boolean; "
            "omit the key entirely to keep serving calls whose Mcp-Param-* headers could not be validated."
        )
    return raw


def _init_resource_links_from_config(full_config: dict[str, Any]) -> None:
    """Apply ``resource_links.max_per_tenant`` (#1146).

    ::

        resource_links:
          max_per_tenant: 4096

    The number of handed-out ``resource_link`` references the front door
    remembers for one tenant before that tenant's oldest is forgotten (#1139).
    Absent means today's default, so nobody's behaviour changes on upgrade.

    An invalid value is a hard error for the reason ``tool_access.mode`` and
    ``headers.param_validation.required`` are: a limit that quietly falls back
    to the default reads as applied. ``True`` is an ``int`` to ``isinstance``
    and is refused explicitly -- ``max_per_tenant: yes`` is not a cap of 1.

    Raises:
        ConfigurationError: If ``max_per_tenant`` is present but not a positive integer.
    """
    from ..fastmcp_server.resource_link_read_through import set_max_links_per_tenant

    cap = _max_links_per_tenant(full_config)
    set_max_links_per_tenant(cap)
    logger.debug("resource_links_max_per_tenant_set", max_per_tenant=cap)


def _max_links_per_tenant(full_config: dict[str, Any]) -> int:
    """``resource_links.max_per_tenant``, checked. Absent means the default."""
    from ..fastmcp_server.resource_link_read_through import DEFAULT_MAX_LINKS_PER_TENANT

    section = full_config.get("resource_links")
    raw = section.get("max_per_tenant") if isinstance(section, dict) else None

    if raw is None:
        return DEFAULT_MAX_LINKS_PER_TENANT
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ConfigurationError(
            f"Invalid resource_links.max_per_tenant {raw!r}. It must be a positive integer; "
            f"omit the key entirely to keep the default of {DEFAULT_MAX_LINKS_PER_TENANT}."
        )
    return raw


def _init_ui_resources_from_config(full_config: dict[str, Any]) -> None:
    """Build the process ``ui://`` guard from the config file (#1048).

    ::

        ui_resources:
          tenants:
            "tenant:a":
              allowlist: ["ui://reports/", "ui://dash/q3"]
              csp: "default-src 'none'; …"   # optional; the restrictive default otherwise

    A tenant with no entry keeps the fail-closed default -- an empty allowlist,
    which denies every ``ui://`` resource -- and so does a caller with no tenant
    at all. There is deliberately no way to turn consent off here: SEP-1865
    mandates it, and ``UiResourcePolicy.require_consent`` is not read from the
    file (ADR-024).

    An unparseable entry is warn-skipped rather than fatal, which leaves that
    tenant denied: the failure of this block cannot open the surface it guards.

    The policies are replaced on the process guard rather than on a new guard,
    so the consent gate bootstrap attaches survives a reload; and an absent
    block replaces them with none, so deleting it and reloading restores the
    fail-closed default (#1424).

    Args:
        full_config: Full configuration dictionary.
    """
    from ..domain.services.ui_resource_guard import get_ui_resource_guard

    get_ui_resource_guard().replace_policies(_ui_resource_policies(full_config))


def _ui_resource_policies(full_config: dict[str, Any]) -> "dict[str, UiResourcePolicy]":
    """Each tenant's ``ui://`` policy from the ``ui_resources`` block; none when it is absent."""
    from ..domain.value_objects.ui_resource import UiResourcePolicy

    ui_config = full_config.get("ui_resources")
    tenants_config = ui_config.get("tenants") if isinstance(ui_config, dict) else None
    if not isinstance(tenants_config, dict):
        return {}

    policies: dict[str, UiResourcePolicy] = {}
    for tenant_id, spec in tenants_config.items():
        if not isinstance(spec, dict):
            logger.warning("invalid_ui_resource_policy", tenant_id=tenant_id, reason="not a mapping")
            continue
        raw_allowlist = spec.get("allowlist", [])
        if not isinstance(raw_allowlist, list) or not all(isinstance(entry, str) and entry for entry in raw_allowlist):
            logger.warning("invalid_ui_resource_policy", tenant_id=tenant_id, reason="allowlist must be a list of str")
            continue
        csp = spec.get("csp")
        try:
            policy = (
                UiResourcePolicy(allowlist=frozenset(raw_allowlist), csp=csp)
                if isinstance(csp, str) and csp
                else UiResourcePolicy(allowlist=frozenset(raw_allowlist))
            )
        except (TypeError, ValueError) as e:
            logger.warning("invalid_ui_resource_policy", tenant_id=tenant_id, error=str(e))
            continue
        policies[str(tenant_id)] = policy
        logger.debug("ui_resource_policy_set", tenant_id=tenant_id, allowlist_size=len(policy.allowlist))
    return policies


def _init_concurrency_from_config(full_config: dict[str, Any]) -> None:
    """Initialize the ConcurrencyManager from configuration.

    Reads ``execution.max_concurrency`` for the global limit and
    per-mcp_server ``max_concurrency`` values from the ``mcp_servers`` section.

    Called during load_configuration before mcp_servers are loaded, so that
    per-mcp_server limits set via _load_mcp_server_config are applied on top.

    Args:
        full_config: Full configuration dictionary.
    """
    init_concurrency_manager(**_concurrency_limits(full_config))


def _concurrency_limits(full_config: dict[str, Any]) -> dict[str, Any]:
    """The ConcurrencyManager's limits: ``execution`` and each server's ``max_concurrency``."""
    execution_config = full_config.get("execution", {})

    global_limit_raw = execution_config.get("max_concurrency")
    if global_limit_raw is not None:
        # 0 or null in config means unlimited
        global_limit = int(global_limit_raw) if global_limit_raw else 0
    else:
        global_limit = DEFAULT_GLOBAL_CONCURRENCY

    default_mcp_server_limit_raw = execution_config.get("default_mcp_server_concurrency")
    if default_mcp_server_limit_raw is not None:
        default_mcp_server_limit = int(default_mcp_server_limit_raw) if default_mcp_server_limit_raw else 0
    else:
        default_mcp_server_limit = DEFAULT_PROVIDER_CONCURRENCY

    # Collect per-mcp_server limits from mcp_servers section
    mcp_server_limits: dict[str, int] = {}
    mcp_servers_config = full_config.get("mcp_servers", {})
    for mcp_server_id, spec in mcp_servers_config.items():
        if isinstance(spec, dict):
            pmc = spec.get("max_concurrency")
            if pmc is not None:
                mcp_server_limits[mcp_server_id] = int(pmc)

    return {
        "global_limit": global_limit,
        "default_mcp_server_limit": default_mcp_server_limit,
        "mcp_server_limits": mcp_server_limits,
    }


def _init_tenant_limits_from_config(full_config: dict[str, Any]) -> None:
    """Put `execution.tenant_limits` in force (#1445).

    Reconciled, not replaced: a tenant whose limits did not change keeps its
    budget, with the calls it has in flight and the tokens it has spent, so a
    reload -- a byte-identical one from the file watcher included -- neither
    frees slots that running calls hold nor refills a spent budget. An absent
    section removes every budget. See `tools/batch/tenant_admission.py`.
    """
    limits = _tenant_limits(full_config)
    configure_tenant_limits(limits)
    if limits:
        logger.info("tenant_limits_configured", entries=sorted(limits))


def _tenant_limits(full_config: dict[str, Any]) -> dict[str, TenantLimits]:
    """The checked ``execution.tenant_limits``, empty when there is none."""
    execution_config = full_config.get("execution") or {}
    return parse_tenant_limits(execution_config.get("tenant_limits") if isinstance(execution_config, dict) else None)


def _init_interceptors_from_config(full_config: dict[str, Any]) -> None:
    """Register opt-in built-in interceptors (validators) from configuration.

    Reads the optional top-level ``interceptors.validators`` list and rebuilds
    the batch executor's ValidatorPipeline accordingly. **Off by default:** an
    absent or empty section registers no validators, preserving current
    behavior (the tool-call path runs an empty pipeline).

    Args:
        full_config: Full configuration dictionary.
    """
    from .tools.batch import configure_interceptors

    configure_interceptors(_validator_specs(full_config))


def _validator_specs(full_config: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The ``interceptors.validators`` list, or None when there is none."""
    interceptors_config = full_config.get("interceptors")
    if isinstance(interceptors_config, dict):
        raw = interceptors_config.get("validators")
        if isinstance(raw, list):
            return raw
    return None


def http_graceful_shutdown_timeout(full_config: dict[str, Any]) -> int | None:
    """``http.graceful_shutdown_timeout_s``, checked (#1447). Absent means uvicorn's default.

    ::

        http:
          graceful_shutdown_timeout_s: 90

    How long ``serve --http`` waits, once it is told to stop, for the requests
    already in flight to finish before it cancels them. uvicorn's own default is
    ``None``: it waits as long as they take, so the only bound is whatever ends
    the process -- in Kubernetes, the kubelet's SIGKILL at the end of the pod's
    grace period. Absent keeps exactly that, so nobody's behaviour changes on
    upgrade.

    Read when the HTTP server starts, and passed to uvicorn then. A reload
    checks the value, so a bad one refuses the reload like any other section,
    but the running server keeps the bound it started with until it restarts.

    Whole seconds: uvicorn declares the option as ``int | None``, and a pod's
    ``terminationGracePeriodSeconds``, which has to exceed it, is counted in
    whole seconds too. ``True`` is an ``int`` to ``isinstance`` and is refused
    explicitly -- ``graceful_shutdown_timeout_s: yes`` is not a bound of 1.

    Raises:
        ConfigurationError: If ``http`` is not a mapping, or the key is present
            and not a positive integer.
    """
    section = full_config.get("http")
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigurationError(f"Invalid http section {section!r}. It must be a mapping.")

    raw = section.get("graceful_shutdown_timeout_s")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ConfigurationError(
            f"Invalid http.graceful_shutdown_timeout_s {raw!r}. It must be a positive whole number of "
            "seconds; omit the key entirely to keep uvicorn's default, which waits for in-flight "
            "requests without a bound."
        )
    return raw


#: Every process-wide section, in the order startup has always applied them:
#: the concurrency manager before the executor that reads it, and before the
#: servers set their own limits on it. A reload applies the same list (#1424);
#: it used to apply none of it.
_PROCESS_SECTIONS: tuple[Callable[[dict[str, Any]], None], ...] = (
    _init_concurrency_from_config,
    _init_tenant_limits_from_config,
    _init_topology_mode_from_config,
    _init_required_catalogue_from_config,
    _init_param_validation_from_config,
    _init_resource_links_from_config,
    _init_interceptors_from_config,
    _init_ui_resources_from_config,
)


def check_process_config(full_config: dict[str, Any]) -> None:
    """Refuse a configuration whose process-wide sections cannot be applied, and apply none.

    A reload calls this before it stops a server, so a bad value in any
    section fails the reload with everything still running as it was.
    `apply_process_config` makes the same check first, so the sections are
    applied all together or not at all.

    Raises:
        ConfigurationError: Naming the section that is wrong.
    """
    from ..application.services.interceptor_registry import build_validator_pipeline
    from ..domain.services.tool_access_resolver import configured_topology_mode
    from .catalogue_readiness import required_catalogue

    configured_topology_mode(full_config)
    required_catalogue(full_config)
    _param_validation_required(full_config)
    _max_links_per_tenant(full_config)
    _ui_resource_policies(full_config)
    # Checked, not applied: `run_http` reads it when the server starts (#1447).
    http_graceful_shutdown_timeout(full_config)
    try:
        _concurrency_limits(full_config)
    except (TypeError, ValueError) as e:
        raise ConfigurationError(f"Invalid concurrency limit in execution or mcp_servers: {e}") from e
    try:
        _tenant_limits(full_config)
    except ValueError as e:
        raise ConfigurationError(f"Invalid execution.tenant_limits: {e}") from e
    try:
        build_validator_pipeline(_validator_specs(full_config))
    except (TypeError, ValueError) as e:
        raise ConfigurationError(f"Invalid interceptors.validators: {e}") from e


def apply_process_config(full_config: dict[str, Any]) -> None:
    """Apply every process-wide section of a configuration: startup's step, and a reload's.

    `tool_access.mode`, `tool_access.required_catalogue`, `execution`,
    `headers.param_validation`, `resource_links`, `interceptors` and
    `ui_resources`. A section that is absent is put back to its default, so
    deleting a block and reloading removes it. Checked first, so nothing is
    applied unless all of it can be.
    """
    check_process_config(full_config)
    for apply_section in _PROCESS_SECTIONS:
        apply_section(full_config)


class ServerConfigLoader(IConfigLoader):
    """IConfigLoader implementation backed by server-layer config functions.

    Used by ReloadConfigurationHandler to load and apply configuration without
    importing server-layer symbols from the application layer.

    Declares the base class rather than matching it by shape: IConfigLoader is
    an ABC, so nothing checked the two agreed while this only duck-typed it, and
    a rename on either side would have surfaced as an AttributeError at reload
    time.
    """

    def load_from_file(self, path: str) -> dict[str, Any]:
        """Load and parse a configuration file.

        Args:
            path: Path to the YAML configuration file.

        Returns:
            Parsed configuration as a dictionary.
        """
        return load_config_from_file(path)

    def check_process_config(self, full_config: dict[str, Any]) -> None:
        """Refuse a configuration whose process-wide sections cannot be applied."""
        check_process_config(full_config)

    def apply_process_config(self, full_config: dict[str, Any]) -> None:
        """Apply every process-wide section, as startup does."""
        apply_process_config(full_config)

    def prepare_mcp_servers(self, mcp_servers_config: dict[str, Any]) -> _StagedConfig:
        """Build and check a mcp_servers section and read the stored policies; put nothing in force.

        Both can refuse a reload, and both do it here, before anything is
        stopped. See `build_config` and `_read_stored_policies`.
        """
        prepared = build_config(mcp_servers_config)
        prepared.stored_policies = _read_stored_policies()
        return prepared

    def commit_mcp_servers(self, prepared: PreparedServers) -> None:
        """Put a prepared section in force, replacing the previous one's servers, groups and overlays."""
        cast(_StagedConfig, prepared).commit(replace=True)


def load_configuration(config_path: str | None = None, *, load_servers: bool = True) -> dict[str, Any]:
    """Load mcp_server configuration from file or use defaults.

    Args:
        config_path: Where to read from. Defaults to `MCP_CONFIG`.
        load_servers: Whether to build the declared servers as well as read the
            file. **Bootstrap passes False**, and the reason is not tidiness:
            building a server reaches for the runtime, and the runtime is a
            singleton that takes the storage backend *at construction* because
            it is frozen afterwards. Loading servers here therefore built the
            runtime before the backend had been selected, so a gateway with a
            storage backend and any server in `config.yaml` silently got the
            in-memory config repository -- no durable fleet, nothing for
            recovery to read, and one `fleet_writer_absent` log line that reads
            like a configuration choice rather than a defect.

    Returns:
        Full configuration dictionary
    """
    if config_path is None:
        config_path = os.getenv("MCP_CONFIG", "config.yaml")

    if Path(config_path).exists():
        logger.info("loading_config_from_file", config_path=config_path)
        return apply_configuration(_read_config_file(config_path), source=config_path, load_servers=load_servers)

    logger.info("config_not_found_using_default", config_path=config_path)
    default_config = {
        "math_subprocess": {
            "mode": "subprocess",
            "command": ["python", "-m", "examples.provider_math.server"],
            # Explicit even though the subprocess launcher now defaults to
            # it: this config is what a reader copies as a starting point.
            "env": {"MCP_TRANSPORT": "stdio"},
            "idle_ttl_s": 180,
        },
    }
    return apply_configuration({"mcp_servers": default_config}, source="the default config", load_servers=load_servers)


def apply_configuration(config: Any, *, source: str, load_servers: bool = True) -> dict[str, Any]:
    """Check one configuration and apply its process-wide sections.

    The one function a configuration goes through, whether it was read from a
    file or handed to `bootstrap(config_dict=...)`. The file path used to be
    the only caller: the dict path merged the caller's dict into whatever
    `MCP_CONFIG` or `./config.yaml` held and applied none of this, so a dict's
    `tool_access.mode`, `execution`, `headers.param_validation`,
    `resource_links`, `interceptors` and `ui_resources` were dropped without a
    word, and so was the schema check (#1415). Every other section is read
    further into `bootstrap()` from the dict this returns, on both paths.

    What stays with the file: reading it, and watching it for reload. No
    setting resolves a relative path against the file's directory -- each
    reader opens it as given, so relative to the working directory -- and a
    dict resolves it the same way.

    Args:
        config: The parsed document or the caller's dict. Not mutated.
        source: What to name in errors and warnings: a path, or `config_dict`.
        load_servers: Whether to build the declared servers too; see
            `load_configuration` for why bootstrap passes False.

    Returns:
        The checked, interpolated configuration.
    """
    full_config = prepare_config(config, source=source)
    apply_process_config(full_config)
    if load_servers:
        load_config(full_config.get("mcp_servers", {}))
    return full_config
