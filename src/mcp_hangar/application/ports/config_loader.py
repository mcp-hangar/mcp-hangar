"""Config loader port.

Defines IConfigLoader so the reload handler can load and apply configuration
without importing from server.config (which is in the server layer).
"""

from abc import ABC, abstractmethod
from typing import Any


class IConfigLoader(ABC):
    """Interface for loading and applying configuration to the running process.

    Application layer uses this port; server.config provides the implementation.
    A reload goes through the same functions startup does, so a section cannot
    be applied at boot and left stale by a reload (#1424).
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
    def apply_mcp_servers(self, mcp_servers_config: dict[str, Any]) -> None:
        """Apply a mcp_servers configuration section to the running system.

        Registers new mcp_servers, updates existing ones, and replaces the
        previous configuration's governance overlays in one step.

        Args:
            mcp_servers_config: Mapping of mcp_server_id -> mcp_server spec dict.
        """
