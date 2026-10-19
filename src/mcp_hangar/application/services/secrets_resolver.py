"""Secrets resolver for MCP mcp_server environment variables.

This module provides functionality to resolve required secrets for MCP mcp_servers
from environment variables and configuration files.
"""

import os
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

from ...domain.security.input_validator import is_env_var_name
from ...logging_config import get_logger
from .registry_ids import secrets_dir_segments

logger = get_logger(__name__)


@dataclass
class SecretsResult:
    """Result of secrets resolution.

    Attributes:
        resolved: Dictionary mapping secret names to their values.
        missing: List of secret names that could not be resolved, the invalid
            ones included.
        sources: Dictionary mapping secret names to their source ("env" or "file").
        invalid: The requested names that are not environment variable names.
            Refused before the environment or any file was consulted.
        not_allowed: The requested names the operator has not bound to this
            server (`hot_loading.secrets.allow`). Not looked up either.
    """

    resolved: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)
    invalid: list[str] = field(default_factory=list)
    not_allowed: list[str] = field(default_factory=list)

    @property
    def all_resolved(self) -> bool:
        """Check if all required secrets were resolved."""
        return len(self.missing) == 0


class SecretsResolver:
    """Resolves secrets for MCP mcp_servers from environment and files.

    Resolution order:
    1. Environment variable (exact name)
    2. File: ~/.config/mcp-hangar/secrets/{registry name, one directory per segment}/{VAR_NAME}
    3. File: ~/.config/mcp-hangar/secrets/{VAR_NAME}

    The names come from a registry entry, which is not trusted. A name that is
    not an environment variable name (`ENV_KEY_PATTERN`) is refused before
    anything is looked up, every segment of the server part of the path is
    checked (`secrets_dir_segments`), and
    a file is read only when its resolved path is still inside the secrets
    directory -- which also refuses a symlink pointing out of it.

    Nor does the entry decide which names are looked up: `resolve` is given the
    names the operator bound to the server, and any other name is reported
    missing without the environment or a file being consulted.

    Attributes:
        secrets_dir: Base directory for secrets files.
    """

    DEFAULT_SECRETS_DIR = Path.home() / ".config" / "mcp-hangar" / "secrets"

    def __init__(self, secrets_dir: Path | None = None):
        """Initialize the secrets resolver.

        Args:
            secrets_dir: Optional custom secrets directory.
        """
        self._secrets_dir = secrets_dir or self.DEFAULT_SECRETS_DIR

    @property
    def secrets_dir(self) -> Path:
        """Get the secrets directory."""
        return self._secrets_dir

    def resolve(self, required: list[str], mcp_server_name: str, *, allowed: Collection[str]) -> SecretsResult:
        """Resolve required secrets for a mcp_server.

        Args:
            required: List of required secret names (environment variable names).
            mcp_server_name: Name of the mcp_server (used for mcp_server-specific secrets).
            allowed: The names this server may receive. Required, so that no
                caller can resolve an untrusted list by leaving it out.

        Returns:
            SecretsResult with resolved secrets and any missing ones.
        """
        result = SecretsResult()

        for secret_name in required:
            if not is_env_var_name(secret_name):
                result.invalid.append(secret_name)
                result.missing.append(secret_name)
                logger.warning("secret_name_refused", secret_name=secret_name, mcp_server=mcp_server_name)
                continue
            if secret_name not in allowed:
                result.not_allowed.append(secret_name)
                result.missing.append(secret_name)
                logger.warning("secret_not_bound_to_server", secret_name=secret_name, mcp_server=mcp_server_name)
                continue
            value, source = self._resolve_single(secret_name, mcp_server_name)
            if value is not None:
                result.resolved[secret_name] = value
                result.sources[secret_name] = source
                logger.debug(
                    "secret_resolved",
                    secret_name=secret_name,
                    mcp_server=mcp_server_name,
                    source=source,
                )
            else:
                result.missing.append(secret_name)
                logger.debug(
                    "secret_not_found",
                    secret_name=secret_name,
                    mcp_server=mcp_server_name,
                )

        return result

    def _resolve_single(self, secret_name: str, mcp_server_name: str) -> tuple[str | None, str]:
        """Resolve a single secret, whose name `resolve` has already checked.

        Args:
            secret_name: Name of the secret to resolve.
            mcp_server_name: Name of the mcp_server.

        Returns:
            Tuple of (value, source) or (None, "") if not found.
        """
        value = os.environ.get(secret_name)
        if value is not None:
            return value, "env"

        for candidate in self._candidate_files(secret_name, mcp_server_name):
            value = self._read_inside(candidate)
            if value is not None:
                return value, "file"

        return None, ""

    def _candidate_files(self, secret_name: str, mcp_server_name: str) -> list[Path]:
        """The per-server file, when the server has a usable directory name, then the global one."""
        candidates = []
        segments = secrets_dir_segments(mcp_server_name)
        if segments is not None:
            candidates.append(self._secrets_dir.joinpath(*segments, secret_name))
        candidates.append(self._secrets_dir / secret_name)
        return candidates

    def _read_inside(self, candidate: Path) -> str | None:
        """Read `candidate` if it is a file inside the secrets directory once both are resolved.

        Both sides are resolved: an unresolved base would refuse every file on
        a host where the directory sits behind a symlink (`/tmp` on macOS), and
        an unresolved candidate would let a symlink inside the directory point
        out of it. The read goes through the resolved path, so what was checked
        is what is read.
        """
        try:
            base = self._secrets_dir.resolve()
            target = candidate.resolve()
        except (OSError, RuntimeError):
            return None
        if not target.is_relative_to(base):
            logger.warning("secret_file_outside_secrets_dir", path=str(candidate))
            return None
        if not target.is_file():
            return None
        try:
            return target.read_text().strip()
        except (OSError, PermissionError) as e:
            logger.warning(
                "secret_file_read_error",
                path=str(candidate),
                error=str(e),
            )
            return None

    def get_missing_instructions(self, result: SecretsResult, mcp_server_name: str) -> str:
        """Generate instructions for the secrets a resolution could not deliver.

        Args:
            result: The resolution that came up short.
            mcp_server_name: Name of the mcp_server.

        Returns:
            Human-readable instructions for setting up the secrets.
        """
        lines: list[str] = []
        if result.invalid:
            lines.extend(
                [
                    f"The registry entry for {mcp_server_name!r} asks for variable(s) whose names are not "
                    "environment variable names, so they were not looked up and nothing can provide them:",
                    "",
                ]
            )
            lines.extend(f"  - {name!r}" for name in result.invalid)
            lines.append("")

        if result.not_allowed:
            lines.extend(
                [
                    f"{mcp_server_name!r} asks for variable(s) this gateway has not bound to it, "
                    "so they were not looked up:",
                    "",
                ]
            )
            lines.extend(f"  - {name}" for name in result.not_allowed)
            lines.extend(
                [
                    "",
                    "Which secrets a server loaded from the registry may receive is the operator's decision, "
                    f"made under `hot_loading.secrets.allow` for {mcp_server_name!r}, by its exact registry name.",
                ]
            )
            # Prose only, never a ready-to-paste grant: the reader is an
            # agent, the entry chose the names, and a config snippet is what an
            # agent with write access to config.yaml would need to grant itself
            # the secret through a reload.
            lines.extend(
                f"Ask the operator to allow {name!r} for {mcp_server_name!r} under hot_loading.secrets.allow."
                for name in result.not_allowed
            )
            lines.append("")

        unbound = set(result.invalid) | set(result.not_allowed)
        missing = [name for name in result.missing if name not in unbound]
        if not missing:
            return "\n".join(lines).rstrip()

        segments = secrets_dir_segments(mcp_server_name)
        lines.extend([f"The following secrets are required for {mcp_server_name!r}:", ""])
        lines.extend(f"  - {secret}" for secret in missing)
        lines.extend(["", "You can provide these secrets in one of the following ways:"])
        base = self._display_dir()
        ways: list[tuple[str, list[str]]] = [("Environment variables:", [f"export {s}=your_value" for s in missing])]
        if segments is not None:
            per_server = "/".join((base, *segments))
            ways.append((f"Files in {per_server}/:", [f"echo 'your_value' > {per_server}/{s}" for s in missing]))
        ways.append((f"Global files in {base}/:", [f"echo 'your_value' > {base}/{s}" for s in missing]))
        for number, (title, commands) in enumerate(ways, start=1):
            lines.extend(["", f"{number}. {title}"])
            lines.extend(f"   {command}" for command in commands)

        return "\n".join(lines)

    def _display_dir(self) -> str:
        """The secrets directory as the instructions show it: under `~` when it is in the home directory."""
        try:
            return f"~/{self._secrets_dir.relative_to(Path.home()).as_posix()}"
        except ValueError:
            return str(self._secrets_dir)
