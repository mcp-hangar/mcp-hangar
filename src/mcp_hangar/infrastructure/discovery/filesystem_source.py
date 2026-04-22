"""Filesystem Discovery Source.

Discovers MCP mcp_servers from YAML files in a directory.
Supports file watching for automatic updates.

Default Path: /etc/mcp-hangar/mcp_servers.d/*.yaml

Example McpServer File:
    # /etc/mcp-hangar/mcp_servers.d/custom-tool.yaml
    name: custom-tool
    enabled: true
    mode: subprocess

    connection:
      command: python
      args:
        - -m
        - my_custom_mcp_server
      env:
        LOG_LEVEL: INFO

    metadata:
      owner: platform-team
      version: "1.2.0"
"""

import asyncio
from pathlib import Path
from typing import Any

from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource

from ...logging_config import get_logger

logger = get_logger(__name__)

# Optional dependencies
try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    logger.debug("PyYAML package not installed, FilesystemDiscoverySource unavailable")

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    logger.debug("watchdog package not installed, file watching unavailable")


class FilesystemDiscoverySource(DiscoverySource):
    """Discover MCP mcp_servers from YAML files in a directory.

    Scans a directory for YAML files containing mcp_server definitions.
    Optionally watches for file changes using inotify/fsevents.

    File Format:
        name: mcp_server-name
        enabled: true
        mode: subprocess|http|sse

        connection:
          command: [python, -m, my_server]
          # or
          host: localhost
          port: 8080

        metadata:
          key: value
    """

    DEFAULT_PATH = "/etc/mcp-hangar/mcp_servers.d/"
    DEFAULT_PATTERN = "*.yaml"

    def __init__(
        self,
        path: str | None = None,
        pattern: str = DEFAULT_PATTERN,
        mode: DiscoveryMode = DiscoveryMode.ADDITIVE,
        watch: bool = True,
        default_ttl: int = 90,
    ):
        """Initialize filesystem discovery source.

        Args:
            path: Directory path to scan (default: /etc/mcp-hangar/mcp_servers.d/)
            pattern: Glob pattern for files (default: *.yaml)
            mode: Discovery mode (default: additive)
            watch: Enable file watching (requires watchdog)
            default_ttl: Default TTL for discovered mcp_servers
        """
        super().__init__(mode)

        if not YAML_AVAILABLE:
            raise ImportError(
                "PyYAML package is required for FilesystemDiscoverySource. Install with: pip install pyyaml"
            )

        self.path = Path(path or self.DEFAULT_PATH)
        self.pattern = pattern
        self.watch = watch and WATCHDOG_AVAILABLE
        self.default_ttl = default_ttl

        self._observer: Observer | None = None
        self._event_handler: FileSystemEventHandler | None = None
        self._cached_mcp_servers: dict[str, DiscoveredMcpServer] = {}

    @property
    def source_type(self) -> str:
        return "filesystem"

    async def discover(self) -> list[DiscoveredMcpServer]:
        """Discover mcp_servers from YAML files.

        Returns:
            List of discovered mcp_servers
        """
        mcp_servers = []

        if not self.path.exists():
            logger.warning(f"Discovery path does not exist: {self.path}")
            return mcp_servers

        if not self.path.is_dir():
            logger.warning(f"Discovery path is not a directory: {self.path}")
            return mcp_servers

        # Also check for .yml extension
        patterns = [self.pattern]
        if self.pattern == "*.yaml":
            patterns.append("*.yml")

        seen_files = set()
        for pattern in patterns:
            for file_path in self.path.glob(pattern):
                if file_path in seen_files:
                    continue
                seen_files.add(file_path)

                try:
                    mcp_server = self._parse_file(file_path)
                    if mcp_server:
                        mcp_servers.append(mcp_server)
                        self._cached_mcp_servers[mcp_server.name] = mcp_server
                        await self.on_mcp_server_discovered(mcp_server)
                except Exception as e:  # noqa: BLE001 -- infra-boundary: skip malformed config file
                    logger.error(f"Failed to parse {file_path}: {e}")

        logger.debug(f"Filesystem discovery found {len(mcp_servers)} mcp_servers")
        return mcp_servers

    def _parse_file(self, file_path: Path) -> DiscoveredMcpServer | None:
        """Parse YAML file into DiscoveredMcpServer.

        Args:
            file_path: Path to YAML file

        Returns:
            DiscoveredMcpServer or None if disabled/invalid
        """
        try:
            with open(file_path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            # Multi-document YAML or invalid YAML - skip silently
            logger.debug(f"Skipping non-mcp_server YAML file {file_path}: {e}")
            return None

        if not data:
            logger.debug(f"Empty file: {file_path}")
            return None

        if not isinstance(data, dict):
            logger.debug(f"Not a dict, skipping: {file_path}")
            return None

        # Check if this looks like a mcp_server definition (must have name or mode)
        # Skip files that look like docker-compose or k8s manifests
        if "services" in data or "apiVersion" in data or "kind" in data:
            logger.debug(f"Skipping non-mcp_server file (docker-compose or k8s): {file_path}")
            return None

        if not data.get("enabled", True):
            logger.debug(f"McpServer disabled in {file_path}")
            return None

        # Must have either 'name' or 'mode' to be considered a mcp_server
        if "name" not in data and "mode" not in data and "connection" not in data:
            logger.debug(f"Not a mcp_server definition, skipping: {file_path}")
            return None

        name = data.get("name", file_path.stem)
        mode = data.get("mode", "subprocess")
        ttl = data.get("ttl", self.default_ttl)

        # Parse connection info
        connection = data.get("connection", {})
        connection_info = self._parse_connection(connection, mode)

        # Parse metadata
        metadata = {
            "file_path": str(file_path),
            "file_name": file_path.name,
            **data.get("metadata", {}),
        }

        return DiscoveredMcpServer.create(
            name=name,
            source_type=self.source_type,
            mode=mode,
            connection_info=connection_info,
            metadata=metadata,
            ttl_seconds=ttl,
        )

    def _parse_connection(self, connection: dict[str, Any], mode: str) -> dict[str, Any]:
        """Parse connection configuration.

        Args:
            connection: Connection dict from YAML
            mode: McpServer mode

        Returns:
            Normalized connection info
        """
        result = {}

        if mode in ("subprocess", "stdio"):
            # Command-based connection
            command = connection.get("command")
            args = connection.get("args", [])

            if isinstance(command, list):
                result["command"] = command
            elif isinstance(command, str):
                result["command"] = [command] + args

            if "env" in connection:
                result["env"] = connection["env"]

        elif mode in ("http", "sse", "remote"):
            # Network-based connection
            result["host"] = connection.get("host", "localhost")
            result["port"] = int(connection.get("port", 8080))
            result["health_path"] = connection.get("health_path", "/health")

            if "endpoint" in connection:
                result["endpoint"] = connection["endpoint"]

        # Copy any additional fields
        for key in ("timeout", "retry_count", "retry_delay"):
            if key in connection:
                result[key] = connection[key]

        return result

    async def health_check(self) -> bool:
        """Check if discovery path exists and is readable.

        Returns:
            True if path is accessible
        """
        try:
            return self.path.exists() and self.path.is_dir()
        except Exception as e:  # noqa: BLE001 -- infra-boundary: health check returns unhealthy on error
            logger.warning(f"Filesystem health check failed: {e}")
            return False

    async def start(self) -> None:
        """Start the filesystem discovery source and file watcher."""
        if not self.watch or not WATCHDOG_AVAILABLE:
            logger.info("Filesystem discovery source started (watching disabled)")
            return

        if not self.path.exists():
            logger.warning(f"Cannot start watcher, path does not exist: {self.path}")
            return

        try:
            self._event_handler = _FileChangeHandler(self)
            self._observer = Observer()
            self._observer.schedule(self._event_handler, str(self.path), recursive=False)
            self._observer.start()
            logger.info(f"Filesystem discovery source started (watching {self.path})")
        except Exception as e:  # noqa: BLE001 -- infra-boundary: watcher start failure is non-fatal
            logger.error(f"Failed to start file watcher: {e}")

    async def stop(self) -> None:
        """Stop the filesystem discovery source and file watcher."""
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception as e:  # noqa: BLE001 -- infra-boundary: best-effort watcher stop
                logger.warning(f"Error stopping file watcher: {e}")
            finally:
                self._observer = None
                self._event_handler = None

        logger.info("Filesystem discovery source stopped")

    async def _handle_file_change(self, file_path: Path, event_type: str) -> None:
        """Handle file system events.

        Args:
            file_path: Changed file path
            event_type: Type of change (created, modified, deleted)
        """
        if file_path.suffix not in (".yaml", ".yml"):
            return

        if event_type == "deleted":
            # Find mcp_server by file path and notify loss
            for name, mcp_server in list(self._cached_mcp_servers.items()):
                if mcp_server.metadata.get("file_path") == str(file_path):
                    del self._cached_mcp_servers[name]
                    await self.on_mcp_server_lost(name)
                    break
        else:
            # Created or modified - re-parse
            try:
                new_mcp_server = self._parse_file(file_path)
                if new_mcp_server:
                    old_mcp_server = self._cached_mcp_servers.get(new_mcp_server.name)
                    if old_mcp_server and old_mcp_server.fingerprint != new_mcp_server.fingerprint:
                        await self.on_mcp_server_changed(old_mcp_server, new_mcp_server)
                    elif not old_mcp_server:
                        await self.on_mcp_server_discovered(new_mcp_server)
                    self._cached_mcp_servers[new_mcp_server.name] = new_mcp_server
            except Exception as e:  # noqa: BLE001 -- infra-boundary: skip individual file change on error
                logger.error(f"Error parsing changed file {file_path}: {e}")


# Only define _FileChangeHandler when watchdog is available
if WATCHDOG_AVAILABLE:

    class _FileChangeHandler(FileSystemEventHandler):
        """Watchdog event handler for file changes."""

        def __init__(self, source: FilesystemDiscoverySource):
            self.source = source
            self._loop: asyncio.AbstractEventLoop | None = None

        def _get_loop(self) -> asyncio.AbstractEventLoop:
            if self._loop is None or self._loop.is_closed():
                try:
                    self._loop = asyncio.get_running_loop()
                except RuntimeError:
                    self._loop = asyncio.new_event_loop()
            return self._loop

        def _schedule_async(self, coro):
            """Schedule async coroutine from sync context."""
            loop = self._get_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, loop)
            else:
                loop.run_until_complete(coro)

        def on_created(self, event):
            if not event.is_directory:
                self._schedule_async(self.source._handle_file_change(Path(event.src_path), "created"))

        def on_modified(self, event):
            if not event.is_directory:
                self._schedule_async(self.source._handle_file_change(Path(event.src_path), "modified"))

        def on_deleted(self, event):
            if not event.is_directory:
                self._schedule_async(self.source._handle_file_change(Path(event.src_path), "deleted"))

    # Make handler available when watchdog is installed
    FileChangeHandler = _FileChangeHandler
else:
    # Stub for when watchdog is not available
    _FileChangeHandler = None
    FileChangeHandler = None
