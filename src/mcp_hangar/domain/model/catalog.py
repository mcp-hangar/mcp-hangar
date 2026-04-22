"""McpServerEntry domain model for the static MCP mcp_server catalog.

Represents a known MCP mcp_server definition that can be browsed, searched,
and deployed via the catalog API. Entries are either builtin (seeded from
catalog_seed.yaml, cannot be deleted) or custom (added via API, deletable).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpServerEntry:
    """A single entry in the static MCP mcp_server catalog.

    Not frozen — SQLiteRepository._row_to_entry() needs to assign fields
    when constructing from sqlite3.Row objects.

    Attributes:
        entry_id: UUID string uniquely identifying this catalog entry.
        name: Human-readable name (e.g. "filesystem", "brave-search").
        description: Short description of what the mcp_server does.
        mode: McpServer mode — "subprocess", "docker", or "remote".
        command: Command list for subprocess mode (e.g. ["uvx", "mcp-server-filesystem"]).
        image: Docker image name for docker mode (None for subprocess/remote).
        tags: Categorization tags (e.g. ["files", "local"]).
        verified: Whether this entry has been verified by the MCP Hangar team.
        source: Origin of this entry — "builtin" or "custom".
        required_env: Environment variable names required by this mcp_server.
        builtin: If True, this entry cannot be deleted via the API.
    """

    entry_id: str
    name: str
    description: str
    mode: str
    command: list[str] = field(default_factory=list)
    image: str | None = None
    tags: list[str] = field(default_factory=list)
    verified: bool = False
    source: str = "custom"
    required_env: list[str] = field(default_factory=list)
    builtin: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary for JSON responses.

        Returns:
            Dict with all fields; list fields returned as-is (already JSON-safe).
        """
        return {
            "entry_id": self.entry_id,
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "command": self.command,
            "image": self.image,
            "tags": self.tags,
            "verified": self.verified,
            "source": self.source,
            "required_env": self.required_env,
            "builtin": self.builtin,
        }
