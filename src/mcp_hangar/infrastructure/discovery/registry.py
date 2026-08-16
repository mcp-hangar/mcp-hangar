"""Where a discovery source type is turned into a discovery source.

Adding a source used to mean editing the core. `server/bootstrap/discovery.py`
carried an `if/elif` over every known `source_type` and unpacked each one's
config keys itself -- so a new source meant a branch in the delivery layer, an
entry in the adapter package's lazy loader, and core knowing what
`socket_path` means. The port itself was never the problem: it is three methods
and it is fine.

This is the composition half, and it lives in infrastructure because that is
where knowledge of concrete adapters belongs. Two ways in:

* built-in sources register their factory below;
* anything else registers through the ``mcp_hangar.discovery_sources`` entry
  point group, which mirrors what `entrypoint_source` already does for MCP
  servers -- packages advertise servers there, and now packages can advertise
  *sources* the same way.

A factory receives the mode and the source's own configuration dict, opaque and
unread by anything else. That is the whole point: core never learns a new
source's option names, so a third-party source is one file and a `pyproject`
entry rather than a patch.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from mcp_hangar.domain.discovery.discovery_source import DiscoverySource
from mcp_hangar.domain.value_objects.discovery import DiscoveryMode
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

#: Entry point group third-party discovery sources register under.
ENTRY_POINT_GROUP: Final = "mcp_hangar.discovery_sources"

#: Builds a source from its mode and its own config. The config dict is passed
#: through untouched -- only the factory knows what its keys mean.
SourceFactory = Callable[[DiscoveryMode, dict[str, Any]], DiscoverySource]

_FACTORIES: dict[str, SourceFactory] = {}
_ENTRY_POINTS_LOADED = False


class UnknownDiscoverySourceError(ValueError):
    """A configured `source_type` has no factory.

    Raised rather than skipped. A discovery source that is configured and
    silently does nothing is the failure mode this codebase keeps finding: the
    operator believes the fleet is being watched, the logs carry one warning at
    startup, and nothing else ever says otherwise.
    """

    def __init__(self, source_type: str, known: list[str]) -> None:
        self.source_type = source_type
        self.known = known
        super().__init__(
            f"unknown discovery source type {source_type!r}; known types: {', '.join(known) or '(none)'}. "
            f"Third-party sources register under the {ENTRY_POINT_GROUP!r} entry point group."
        )


class UnknownDiscoveryModeError(ValueError):
    """A configured `mode` is not a `DiscoveryMode`.

    A named type of its own rather than a bare `ValueError` for the same reason
    the one above is: `create_discovery_orchestrator` wraps source construction
    in a fault barrier, so a plain `ValueError` would be logged and swallowed
    and the source would be absent instead of additive -- the same silence in
    a different place.
    """

    def __init__(self, mode: str, known: list[str]) -> None:
        self.mode = mode
        self.known = known
        super().__init__(f"unknown discovery mode {mode!r}; expected one of: {', '.join(known)}")


def register_source_factory(source_type: str, factory: SourceFactory, *, replace: bool = False) -> None:
    """Make `source_type` constructible.

    Args:
        source_type: The value used in configuration.
        factory: Callable taking (mode, config) and returning a source.
        replace: Allow overriding an existing registration. Off by default so a
            plugin cannot quietly shadow a built-in source -- taking over
            `kubernetes` should be a decision, not an import side effect.

    Raises:
        ValueError: if the type is already registered and `replace` is False.
    """
    if not replace and source_type in _FACTORIES:
        raise ValueError(
            f"discovery source {source_type!r} is already registered; pass replace=True to override it deliberately"
        )
    _FACTORIES[source_type] = factory


def available_source_types() -> list[str]:
    """Every registered source type, entry points included."""
    _load_entry_points()
    return sorted(_FACTORIES)


def create_source(source_type: str, config: dict[str, Any]) -> DiscoverySource:
    """Build a configured source.

    Args:
        source_type: The configured type.
        config: That source's configuration, passed to its factory untouched.

    Returns:
        The source.

    Raises:
        UnknownDiscoverySourceError: if nothing is registered for the type.
        UnknownDiscoveryModeError: if `mode` is not a `DiscoveryMode`.
    """
    _load_entry_points()
    factory = _FACTORIES.get(source_type)
    if factory is None:
        raise UnknownDiscoverySourceError(source_type, sorted(_FACTORIES))

    mode_str = str(config.get("mode", "additive"))
    try:
        mode = DiscoveryMode(mode_str)
    except ValueError as exc:
        raise UnknownDiscoveryModeError(mode_str, [m.value for m in DiscoveryMode]) from exc
    return factory(mode, config)


def _load_entry_points() -> None:
    """Register third-party sources advertised through the entry point group.

    Once per process. A plugin that fails to load is logged and skipped rather
    than taken as a reason to refuse startup: a broken third-party package must
    not be able to stop the gateway, and the configured-but-missing case is
    already covered -- `create_source` raises when the type it needs is absent.
    """
    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED = True

    from importlib.metadata import entry_points

    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        try:
            factory = entry_point.load()
        except Exception as e:  # noqa: BLE001 -- a third-party import must not stop startup
            logger.warning("discovery_source_plugin_failed", source_type=entry_point.name, error=str(e))
            continue
        if entry_point.name in _FACTORIES:
            logger.warning(
                "discovery_source_plugin_ignored",
                source_type=entry_point.name,
                detail="a source of this type is already registered; the plugin was not applied",
            )
            continue
        _FACTORIES[entry_point.name] = factory
        logger.info("discovery_source_plugin_registered", source_type=entry_point.name)


# ---------------------------------------------------------------------------
# Built-in sources
#
# Each factory owns its own option names. They moved here from the delivery
# layer, which had no business knowing what `socket_path` or `label_selector`
# mean; a third-party factory is written exactly like these.
# ---------------------------------------------------------------------------


def _kubernetes(mode: DiscoveryMode, config: dict[str, Any]) -> DiscoverySource:
    from . import KubernetesDiscoverySource

    allowed = config.get("allowed_namespaces")
    denied = config.get("denied_namespaces")
    return KubernetesDiscoverySource(
        mode=mode,
        namespaces=config.get("namespaces"),
        label_selector=config.get("label_selector"),
        in_cluster=config.get("in_cluster", True),
        allowed_namespaces=set(allowed) if allowed is not None else None,
        denied_namespaces=set(denied) if denied is not None else None,
    )


def _docker(mode: DiscoveryMode, config: dict[str, Any]) -> DiscoverySource:
    from . import DockerDiscoverySource

    return DockerDiscoverySource(mode=mode, socket_path=config.get("socket_path"))


def _filesystem(mode: DiscoveryMode, config: dict[str, Any]) -> DiscoverySource:
    from . import FilesystemDiscoverySource

    path = Path(config.get("path", "/etc/mcp-hangar/mcp_servers.d/"))
    if not path.is_absolute():
        path = Path.cwd() / path
    return FilesystemDiscoverySource(
        mode=mode,
        path=str(path),
        pattern=config.get("pattern", "*.yaml"),
        watch=config.get("watch", True),
    )


def _entrypoint(mode: DiscoveryMode, config: dict[str, Any]) -> DiscoverySource:
    from . import EntrypointDiscoverySource

    return EntrypointDiscoverySource(mode=mode, group=config.get("group", "mcp.mcp_servers"))


for _name, _factory in (
    ("kubernetes", _kubernetes),
    ("docker", _docker),
    ("filesystem", _filesystem),
    ("entrypoint", _entrypoint),
):
    register_source_factory(_name, _factory)
