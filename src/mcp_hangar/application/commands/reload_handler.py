"""Command handler for configuration reload."""

import time
from dataclasses import dataclass
from typing import Any

from ...domain.contracts.command import CommandHandler
from ...domain.contracts.event_bus import IEventBus
from ...domain.events import ConfigurationReloaded, ConfigurationReloadFailed, ConfigurationReloadRequested
from ...domain.exceptions import ConfigurationError, ConfigurationRestartRequiredError
from ...domain.repository import IMcpServerRepository
from ...domain.services import get_tool_access_resolver
from ...domain.services.tool_access_resolver import configured_topology_mode
from ...logging_config import get_logger
from ..ports.config_loader import IConfigLoader, PreparedServers
from .commands import ReloadConfigurationCommand

logger = get_logger(__name__)


@dataclass(frozen=True)
class _ServerDiff:
    """How the reloaded file's servers differ from the running ones."""

    added: list[str]
    removed: list[str]
    updated: list[str]
    unchanged: list[str]


class ReloadConfigurationHandler(CommandHandler):
    """Handler for ReloadConfigurationCommand.

    Reloads the configuration file and applies the whole of it, through the
    functions startup uses (#1424):

    - every process-wide section: `execution`, `headers.param_validation`,
      `resource_links`, `interceptors` and `ui_resources`. A section deleted
      from the file goes back to its default;
    - `mcp_servers`: adds new servers, removes deleted ones, restarts the ones
      whose server settings changed, and keeps every other one running: the
      same object, with its process, sessions, health and circuit state. What
      counts as changed is `PreparedServers.keeps`: a policy, pin, withdrawal,
      `header_exposure` or group-membership change alone restarts nothing, and
      takes effect all the same (#1426);
    - the groups, and the tool-access policies, withdrawals, pins and
      `header_exposure` blocks the servers declare, swapped in rather than
      cleared and registered again, so no call is resolved without them. The
      REST endpoint's stored policies are replayed over the file's, as at
      startup; any other policy set at runtime is kept unless the file now
      defines the same scope. A server the reload removes takes its policies
      with it.

    A file that changes `tool_access.mode` is refused, and nothing is changed:
    the front-door tool surface is built at startup, so the mode needs a
    restart. Every process-wide section and every server and group block is
    checked before any server is stopped, so a bad value anywhere also changes
    nothing.
    """

    def __init__(
        self,
        mcp_server_repository: IMcpServerRepository,
        event_bus: IEventBus,
        current_config_path: str | None = None,
        *,
        config_loader: IConfigLoader,
        groups: dict | None = None,
    ):
        """Initialize the handler.

        Args:
            mcp_server_repository: Repository for mcp_server persistence.
            event_bus: Event bus for publishing events.
            current_config_path: Current configuration file path.
            config_loader: Config loader for loading/applying configuration.
                Required. It used to be optional, with a "legacy path" that
                imported `server.config` directly -- the exact import
                `IConfigLoader` and `ServerConfigLoader` were introduced to
                remove. Bootstrap has always injected the adapter, so the
                fallback only ran in tests, which meant the tested path and the
                production path were different ones.
            groups: The group registry. Not cleared by a reload any more: the
                committed configuration replaces its entries (#1424).
        """
        self._repository = mcp_server_repository
        self._event_bus = event_bus
        self._current_config_path = current_config_path
        self._config_loader = config_loader
        self._groups = groups if groups is not None else {}

    def handle(self, command: ReloadConfigurationCommand) -> dict[str, Any]:
        """Handle the reload configuration command.

        Args:
            command: The command to handle.

        Returns:
            Dictionary with reload results.

        Raises:
            ConfigurationError: If configuration is invalid or cannot be loaded.
            ConfigurationRestartRequiredError: If the file changes
                `tool_access.mode`. Nothing is changed.
        """
        start_time = time.perf_counter()

        # Determine config path
        config_path = command.config_path or self._current_config_path
        if not config_path:
            raise ConfigurationError("No configuration path specified")

        self._event_bus.publish(
            ConfigurationReloadRequested(
                config_path=config_path,
                requested_by=command.requested_by,
                force=not command.graceful,
            )
        )

        try:
            diff = self._reload(config_path, graceful=command.graceful)
        except ConfigurationError as e:
            # Already a domain error whose message was written to be shown to
            # the operator (e.g. "Failed to stop mcp_server '<id>' ..."). Let
            # it through unchanged rather than burying it inside a generic
            # wrapper -- and rather than re-wrapping, which would also lose
            # its own status mapping.
            self._report_failure(e, config_path, command.requested_by, start_time)
            raise
        except Exception as e:  # noqa: BLE001 -- fault-barrier: wrap reload errors in ConfigurationError for callers
            # An unexpected internal failure: its text can carry filesystem
            # paths, stringified underlying errors, and other internals, and the
            # REST error envelope renders MCPError.message verbatim to the
            # caller. The event and the log keep the full detail for operators;
            # the caller gets a generic 500 with nothing to leak.
            self._report_failure(e, config_path, command.requested_by, start_time)
            raise ConfigurationError("Configuration reload failed due to an internal error") from e

        duration_ms = (time.perf_counter() - start_time) * 1000
        self._event_bus.publish(
            ConfigurationReloaded(
                config_path=config_path,
                mcp_servers_added=diff.added,
                mcp_servers_removed=diff.removed,
                mcp_servers_updated=diff.updated,
                mcp_servers_unchanged=diff.unchanged,
                reload_duration_ms=duration_ms,
                requested_by=command.requested_by,
            )
        )
        logger.info(
            "configuration_reloaded",
            config_path=config_path,
            duration_ms=duration_ms,
            added=len(diff.added),
            removed=len(diff.removed),
            updated=len(diff.updated),
        )
        return {
            "success": True,
            "config_path": config_path,
            "mcp_servers_added": diff.added,
            "mcp_servers_removed": diff.removed,
            "mcp_servers_updated": diff.updated,
            "mcp_servers_unchanged": diff.unchanged,
            "duration_ms": duration_ms,
        }

    def _reload(self, config_path: str, *, graceful: bool) -> _ServerDiff:
        """Check and build all of the file first; then apply what can no longer fail on it."""
        new_full_config = self._config_loader.load_from_file(config_path)
        self._refuse_a_topology_change(new_full_config, config_path)
        self._config_loader.check_process_config(new_full_config)
        # Every server, group and governance block, built and checked before
        # anything is stopped: a bad block fails the reload with the running
        # configuration untouched. It used to surface only after the servers
        # were stopped and the process-wide sections applied (#1424).
        prepared = self._config_loader.prepare_mcp_servers(new_full_config.get("mcp_servers", {}))

        current_mcp_servers = dict(self._repository.get_all())
        diff = self._diff(current_mcp_servers, prepared)

        self._stop(current_mcp_servers, diff.removed + diff.updated, graceful=graceful)
        self._remove(diff.removed)

        # The same function startup applies these sections with. The topology
        # mode among them is the running one, which the check above ensured.
        self._config_loader.apply_process_config(new_full_config)
        # Servers and groups, and their policies, withdrawals, pins and
        # header_exposure blocks swapped in rather than cleared and registered
        # again. The group registry is replaced, not emptied first.
        self._config_loader.commit_mcp_servers(prepared)

        for mcp_server_id in diff.added:
            logger.info("mcp_server_added", mcp_server_id=mcp_server_id)
        for mcp_server_id in diff.updated:
            logger.info("mcp_server_updated", mcp_server_id=mcp_server_id)
        return diff

    def _refuse_a_topology_change(self, new_full_config: dict[str, Any], config_path: str) -> None:
        """A reload keeps the topology mode; a file that changes it needs a restart.

        The front-door tool surface is built at startup. A reload that moved the
        resolver to another mode would leave that surface and the access rules
        disagreeing -- which is what a reload did until #1424, silently, by
        resetting every gateway to `egress`.
        """
        running = get_tool_access_resolver().topology_mode
        requested = configured_topology_mode(new_full_config)
        if requested == running:
            return
        raise ConfigurationRestartRequiredError(
            f"{config_path} sets tool_access.mode to {requested!r}, and this gateway runs as {running!r}. "
            "A reload cannot change the topology mode, because the tool surface is built at startup. "
            "Nothing was changed. Restart the gateway to apply the new mode.",
            details={"running_mode": running, "requested_mode": requested},
        )

    def _diff(self, current: dict[str, Any], prepared: PreparedServers) -> _ServerDiff:
        """Compare the running servers with every server the file declares.

        The declared servers include each group's inline members. Counting only
        the top-level keys made every inline member look removed: it was
        stopped, rebuilt, and stripped of the policies set on it at runtime.

        A running server the new configuration does not keep is being replaced,
        so it counts as updated and is stopped. Replacing it without a stop left
        its process running with nothing to stop it (#1424).

        And a server it keeps is unchanged, and never stopped: the commit puts
        that very object back. The diff used to run a check of its own, on part
        of the spec, and counted a server whose file left `resources` out as
        changed on every reload. It stopped it, and the commit put the stopped
        server back (#1426).
        """
        new_ids = set(prepared.specs)
        current_ids = set(current)

        updated: list[str] = []
        unchanged: list[str] = []
        for mcp_server_id in sorted(new_ids & current_ids):
            if prepared.keeps(mcp_server_id, current[mcp_server_id]):
                unchanged.append(mcp_server_id)
            else:
                updated.append(mcp_server_id)

        diff = _ServerDiff(
            added=sorted(new_ids - current_ids),
            removed=sorted(current_ids - new_ids),
            updated=updated,
            unchanged=unchanged,
        )
        logger.info(
            "config_reload_diff_calculated",
            added=len(diff.added),
            removed=len(diff.removed),
            updated=len(diff.updated),
            unchanged=len(diff.unchanged),
        )
        return diff

    def _stop(self, current: dict[str, Any], mcp_server_ids: list[str], *, graceful: bool) -> None:
        for mcp_server_id in mcp_server_ids:
            mcp_server = current.get(mcp_server_id)
            if not mcp_server:
                continue
            try:
                # McpServer exposes shutdown() as its lifecycle API. Stop
                # failures must abort reload rather than report a successful
                # replacement with the old runtime still alive.
                mcp_server.shutdown()
                logger.info("mcp_server_stopped_for_reload", mcp_server_id=mcp_server_id, graceful=graceful)
            except Exception as e:  # noqa: BLE001 -- preserve the shutdown failure as a reload failure
                logger.error("mcp_server_stop_failed_during_reload", mcp_server_id=mcp_server_id, error=str(e))
                raise ConfigurationError(f"Failed to stop mcp_server '{mcp_server_id}' for reload: {e}") from e

    def _remove(self, mcp_server_ids: list[str]) -> None:
        resolver = get_tool_access_resolver()
        for mcp_server_id in mcp_server_ids:
            self._repository.remove(mcp_server_id)
            # Its policies go with it, as on `hangar_unload`: the id is now free
            # for a later server, which must not inherit them (#1028). The
            # file's own entries would go in the swap anyway; this also takes
            # the ones a runtime caller set.
            resolver.remove_mcp_server_policy(mcp_server_id)
            logger.info("mcp_server_removed", mcp_server_id=mcp_server_id)

    def _report_failure(self, error: Exception, config_path: str, requested_by: str, start_time: float) -> None:
        duration_ms = (time.perf_counter() - start_time) * 1000
        self._event_bus.publish(
            ConfigurationReloadFailed(
                config_path=config_path,
                reason=str(error),
                error_type=type(error).__name__,
                requested_by=requested_by,
            )
        )
        logger.error(
            "configuration_reload_failed",
            config_path=config_path,
            error=str(error),
            error_type=type(error).__name__,
            duration_ms=duration_ms,
        )
