"""Config loader port.

Defines IConfigLoader so the reload handler can load and apply configuration
without importing from server.config (which is in the server layer).
"""

from abc import ABC, abstractmethod
from typing import Any, Protocol


class PreparedServers(Protocol):
    """A configuration's servers, groups and governance, built and checked, and not in force yet.

    Opaque to the application layer except for what the reload diff needs.
    """

    #: Every server the file declares, by id: the top-level entries and each
    #: group's inline members, whose spec is the member entry itself.
    specs: dict[str, dict[str, Any]]

    def keeps(self, mcp_server_id: str, running: Any) -> bool:
        """Whether the new configuration keeps *running* as that server, rather than replacing it.

        It keeps a server the file would build with the same settings, every
        default applied, and that still reads as configured. Governance the
        server declares, and its place in a group, are not among the settings:
        they are swapped in whether or not it is kept.

        A running server it does not keep is replaced when it is committed, so
        a reload must stop it first. One it keeps must not be stopped: the
        commit puts that same object back.
        """
        ...


class IConfigLoader(ABC):
    """Interface for loading and applying configuration to the running process.

    Application layer uses this port; server.config provides the implementation.
    A reload goes through the same functions startup does, so a section cannot
    be applied at boot and left stale by a reload (#1424). Everything that can
    refuse a file refuses in `check_process_config` or `prepare_mcp_servers`,
    before a reload stops anything.
    """

    @abstractmethod
    def load_from_file(self, path: str) -> dict[str, Any]:
        """Load and parse a configuration file.

        Args:
            path: Path to the YAML configuration file.

        Returns:
            Parsed configuration as a dictionary.

        Raises:
            ConfigurationError: If the file cannot be read or parsed.
        """

    @abstractmethod
    def check_process_config(self, full_config: dict[str, Any]) -> None:
        """Refuse a configuration whose process-wide sections cannot be applied.

        Changes nothing. A reload calls it before it stops any server.

        Args:
            full_config: The whole configuration, as `load_from_file` returned it.

        Raises:
            ConfigurationError: Naming the section that is wrong.
        """

    @abstractmethod
    def apply_process_config(self, full_config: dict[str, Any]) -> None:
        """Apply every process-wide section, the way startup does.

        `tool_access.mode`, `execution`, `headers.param_validation`,
        `resource_links`, `interceptors` and `ui_resources`. An absent section
        is put back to its default.

        Args:
            full_config: The whole configuration, as `load_from_file` returned it.
        """

    @abstractmethod
    def prepare_mcp_servers(self, mcp_servers_config: dict[str, Any]) -> PreparedServers:
        """Build and check a mcp_servers section, and put none of it in force.

        Args:
            mcp_servers_config: Mapping of mcp_server_id -> mcp_server spec dict.

        Raises:
            ConfigurationError: If a server or group block is invalid.
        """

    @abstractmethod
    def commit_mcp_servers(self, prepared: PreparedServers) -> None:
        """Put a prepared section in force, replacing the previous one's.

        Its servers, its groups, and its governance overlays, which are swapped
        in rather than cleared and registered again. Does not fail on the file:
        everything that could was checked by `prepare_mcp_servers`.

        Args:
            prepared: What `prepare_mcp_servers` returned.
        """
