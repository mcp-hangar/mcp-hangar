"""Discovery orchestrator initialization."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from ...application.discovery import DiscoveryConfig, DiscoveryOrchestrator
from ...domain.security.input_validator import InputValidator
from ...domain.value_objects.provenance import Provenance
from ...infrastructure.discovery.registry import UnknownDiscoverySourceError, create_source
from ...application.commands.crud_commands import CreateMcpServerCommand, DeleteMcpServerCommand
from ...domain.contracts.fleet import NotTheManagerError
from ...logging_config import get_logger
from ..state import get_runtime, set_discovery_orchestrator
from .coordination import may_manage

logger = get_logger(__name__)


def create_discovery_orchestrator(config: dict[str, Any]) -> DiscoveryOrchestrator | None:
    """Create discovery orchestrator from config (not started).

    Args:
        config: Full configuration dictionary.

    Returns:
        DiscoveryOrchestrator instance or None if disabled.
    """
    discovery_config = config.get("discovery", {})
    if not discovery_config.get("enabled", False):
        logger.info("discovery_disabled")
        return None

    logger.info("discovery_initializing")

    repository = get_runtime().repository
    static_mcp_servers = set(repository.get_all_ids())
    orchestrator_config = DiscoveryConfig.from_dict(discovery_config)
    orchestrator = DiscoveryOrchestrator(
        config=orchestrator_config,
        static_mcp_servers=static_mcp_servers,
        input_validator=InputValidator(),
        # Without this the orchestrator has nowhere to record what it did, and
        # the five discovery event classes stay what they were for a year: a
        # vocabulary with no emitter (#762). Resolved lazily -- this runs during
        # bootstrap, and the runtime is not assembled yet at import time.
        event_bus=_runtime_event_bus(),
        # Discovery registers and deregisters servers in storage every replica
        # shares. Asked per cycle, so a lease lost mid-life stops the next one.
        may_manage=may_manage,
    )

    sources_config = discovery_config.get("sources", [])
    for source_config in sources_config:
        source_type = source_config.get("type")
        source_config = _migrate_namespace_policy(source_type, source_config, discovery_config)
        try:
            orchestrator.add_source(create_source(source_type, source_config))
        except UnknownDiscoverySourceError:
            # Deliberately NOT caught. A configured source that silently does
            # nothing is the failure this codebase keeps finding: the operator
            # believes the fleet is watched and one startup warning is the only
            # thing that ever says otherwise. `init_event_store` already refuses
            # an unknown driver the same way.
            raise
        except ImportError as e:
            # An optional dependency is missing -- a deployment shape, not a
            # mistake in the configuration. Degrade, as before.
            logger.warning(
                "discovery_source_unavailable",
                source_type=source_type,
                error=str(e),
            )
        except Exception as e:  # noqa: BLE001 -- fault-barrier: discovery source init failure must not crash bootstrap
            logger.error(
                "discovery_source_error",
                source_type=source_type,
                error=str(e),
            )

    # Set up registration callbacks
    orchestrator.on_register = _on_mcp_server_register
    orchestrator.on_deregister = _on_mcp_server_deregister

    set_discovery_orchestrator(orchestrator)
    return orchestrator


def _runtime_event_bus():
    """The event bus, or None if the runtime has not been assembled yet.

    Bootstrap order is not something this module should assert: discovery is
    initialised alongside everything else, and a missing bus means the events
    are not recorded rather than that startup fails. Discovery working without
    a log is a degradation; discovery refusing to start because of one is not.
    """
    try:
        return get_runtime().event_bus
    except Exception as e:  # noqa: BLE001 -- boundary: no runtime yet is a shape, not a fault
        logger.warning("discovery_events_not_recorded", detail="no event bus at bootstrap", error=str(e))
        return None


def _migrate_namespace_policy(
    source_type: str, source_config: dict[str, Any], discovery_config: dict[str, Any]
) -> dict[str, Any]:
    """Carry the old namespace policy over to the source that now owns it.

    `allowed_namespaces` / `denied_namespaces` used to live under
    `discovery.security`, where the core applied them behind a check on the
    source's name. They belong to the kubernetes source, which is the only thing
    that knows what a namespace is -- but moving a *security* setting silently
    is the one migration you must not do quietly: a deployment that denied
    `kube-system` would start accepting it, and nothing would say so.

    So the old location still works for now, is preferred only when the new one
    is absent, and warns every time it is used.

    Args:
        source_type: The configured source type.
        source_config: That source's own configuration.
        discovery_config: The whole discovery block, for the legacy location.

    Returns:
        The source config, with the legacy policy merged in when it applied.
    """
    if source_type != "kubernetes":
        return source_config

    legacy = discovery_config.get("security", {})
    migrated = dict(source_config)
    for key in ("allowed_namespaces", "denied_namespaces"):
        if key in migrated or key not in legacy:
            continue
        migrated[key] = legacy[key]
        logger.warning(
            "discovery_namespace_policy_deprecated_location",
            key=key,
            detail=(
                f"`discovery.security.{key}` is deprecated; move it to the kubernetes source's "
                "own configuration. It is being applied from the old location for now."
            ),
        )
    return migrated


def _runtime_addresses_of(mcp_server) -> frozenset[str] | None:
    """The addresses a source says the runtime reported for this server.

    Returns None rather than an empty set when a source reports nothing, because
    the two mean different things downstream: None means "unscoped, apply the
    strict policy", which is what a source that cannot vouch for an address
    should get.
    """
    reported = getattr(mcp_server, "metadata", None) or {}
    addresses = reported.get("runtime_addresses") or ()
    cleaned = frozenset(str(a) for a in addresses if a)
    return cleaned or None


async def _on_mcp_server_register(mcp_server) -> bool:
    """Callback when discovery wants to register a mcp_server.

    Args:
        mcp_server: Discovered mcp_server information.

    Returns:
        True if registration succeeded, False otherwise.
    """
    try:
        conn_info = mcp_server.connection_info
        mode = mcp_server.mode

        if mode == "container":
            mcp_server_mode = "docker"
        elif mode in ("http", "sse"):
            mcp_server_mode = "remote"
        elif mode in ("subprocess", "docker", "remote"):
            mcp_server_mode = mode
        else:
            logger.warning(
                "unknown_mcp_server_mode_skipping",
                mode=mode,
                mcp_server_name=mcp_server.name,
            )
            return False

        mcp_server_kwargs: dict[str, Any] = {
            "mcp_server_id": mcp_server.name,
            "mode": mcp_server_mode,
            "description": f"Discovered from {mcp_server.source_type}",
        }

        if mcp_server_mode == "docker":
            image = conn_info.get("image")
            if not image:
                logger.warning(
                    "container_mcp_server_no_image_skipping",
                    mcp_server_name=mcp_server.name,
                )
                return False
            mcp_server_kwargs["image"] = image
            mcp_server_kwargs["read_only"] = conn_info.get("read_only", False)
            if conn_info.get("command"):
                mcp_server_kwargs["command"] = conn_info.get("command")

            volumes = conn_info.get("volumes", [])
            if not volumes:
                volumes = _auto_add_volumes(mcp_server.name)
            if volumes:
                mcp_server_kwargs["volumes"] = volumes

        elif mcp_server_mode == "remote":
            host = conn_info.get("host")
            port = conn_info.get("port")
            endpoint = conn_info.get("endpoint")
            if endpoint:
                mcp_server_kwargs["endpoint"] = endpoint
            elif host and port:
                mcp_server_kwargs["endpoint"] = f"http://{host}:{port}"
            else:
                logger.warning(
                    "http_mcp_server_no_endpoint_skipping",
                    mcp_server_name=mcp_server.name,
                )
                return False
        else:
            command = conn_info.get("command")
            if not command:
                logger.warning(
                    "subprocess_mcp_server_no_command_skipping",
                    mcp_server_name=mcp_server.name,
                )
                return False
            mcp_server_kwargs["command"] = command

        mcp_server_kwargs["env"] = conn_info.get("env", {})

        # Through the command bus, not around it. Building the aggregate here
        # skipped everything the command handler does: the duplicate guard, the
        # SSRF check on a remote endpoint, and `McpServerRegistered` -- so a
        # server could join the fleet automatically, unvalidated, leaving one
        # log line and no record. `source` carries the provenance the CRUD path
        # has always carried for hand-registered servers.
        # `provenance` is set here and nowhere a request can reach: it is what
        # the SSRF policy branches on, so it has to be established by the
        # construction path rather than read off a field. `source` stays a label
        # for an operator; a policy keyed on that string would be settable by
        # anyone who can reach a route that forwards it.
        #
        # The addresses come from what the runtime reported for this container
        # or pod, so provenance grants a *specific address* rather than an
        # address class. Absent -- a source that reports none -- the strict
        # human policy applies, which is the safe direction to fail.
        runtime_addresses = _runtime_addresses_of(mcp_server)
        get_runtime().command_bus.send(
            CreateMcpServerCommand(
                **cast(Any, mcp_server_kwargs),
                source=f"discovery:{mcp_server.source_type}",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=runtime_addresses,
            )
        )
        logger.info(
            "discovery_registered_mcp_server",
            mcp_server_name=mcp_server.name,
            mode=mcp_server_mode,
        )
        return True
    except Exception as e:  # noqa: BLE001 -- fault-barrier: registration failure must not crash discovery cycle
        logger.error(
            "discovery_registration_failed",
            mcp_server_name=mcp_server.name,
            error=str(e),
        )
        return False


async def _on_mcp_server_deregister(name: str, reason: str):
    """Callback when discovery wants to deregister a mcp_server.

    Args:
        name: McpServer name to deregister.
        reason: Reason for deregistration.
    """
    try:
        if not get_runtime().repository.exists(name):
            return

        # Through the command bus, like the registration on the way in. It used
        # to stop the server and drop it from the in-memory fleet directly,
        # which meant a discovered server's *departure* was the one fleet change
        # nothing recorded: no `McpServerDeregistered` in the log, and -- since
        # the fleet became durable (#794) -- the row left behind, so the server
        # came back at the next restart. Registration persisted; deregistration
        # did not.
        #
        # `provenance` is set here and nowhere a request can reach. It marks
        # this as a convergence loop's decision rather than an operator's, which
        # is what makes it fenced: this instance may have stalled long enough to
        # lose the management lease and not know it yet.
        get_runtime().command_bus.send(
            DeleteMcpServerCommand(
                mcp_server_id=name,
                source=f"discovery:{reason}",
                provenance=Provenance.DISCOVERY,
            )
        )
        logger.info(
            "discovery_deregistered_mcp_server",
            mcp_server_name=name,
            reason=reason,
        )
    except NotTheManagerError as e:
        # Expected in a cluster, and not a failure: this instance decided on the
        # deregistration under a tenure that has since ended, so the fleet it
        # was reasoning about is not the current one. The instance that holds
        # the lease now will reach its own conclusion.
        logger.info("discovery_deregistration_fenced", mcp_server_name=name, detail=str(e))
    except Exception as e:  # noqa: BLE001 -- fault-barrier: deregistration failure must not crash discovery cycle
        logger.error(
            "discovery_deregistration_failed",
            mcp_server_name=name,
            error=str(e),
        )


def _auto_add_volumes(mcp_server_name: str) -> list[str]:
    """Auto-add persistent volumes for known stateful mcp_servers.

    Args:
        mcp_server_name: McpServer name to check for known volume patterns.

    Returns:
        List of volume mount strings.
    """
    volumes = []
    mcp_server_name_lower = mcp_server_name.lower()
    data_base = Path("./data").absolute()

    try:
        if "memory" in mcp_server_name_lower:
            memory_dir = data_base / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)
            memory_dir.chmod(0o777)
            volumes.append(f"{memory_dir}:/app/data:rw")
            logger.info(
                "auto_added_memory_volume",
                mcp_server_name=mcp_server_name,
                volume=f"{memory_dir}:/app/data",
            )

        elif "filesystem" in mcp_server_name_lower:
            fs_dir = data_base / "filesystem"
            fs_dir.mkdir(parents=True, exist_ok=True)
            fs_dir.chmod(0o777)
            volumes.append(f"{fs_dir}:/data:rw")
            logger.info(
                "auto_added_filesystem_volume",
                mcp_server_name=mcp_server_name,
                volume=f"{fs_dir}:/data",
            )
    except OSError as e:
        logger.warning(
            "auto_volume_creation_failed",
            mcp_server_name=mcp_server_name,
            error=str(e),
        )

    return volumes
