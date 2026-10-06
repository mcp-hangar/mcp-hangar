"""Which configuration file a command reads -- one answer for every command (#1657).

Each command used to pick its own: `init`, `add` and `remove` used
`~/.config/mcp-hangar/config.yaml`, `status` searched that and then
`./config.yaml`, and `pin`, `config check` and `serve` read `$MCP_CONFIG` or
`./config.yaml`. So `pin --check` straight after `init` found no file, and a
`serve` started on the implicit `./config.yaml` handed bootstrap no path, which
left every reload (watcher, SIGHUP, the tool, REST) with nothing to read.

The order, highest first:

1. a path on the command line -- the command's own flag or argument, then the
   global ``--config``;
2. ``$MCP_CONFIG``;
3. ``./config.yaml``, if the working directory has one;
4. ``~/.config/mcp-hangar/config.yaml``, the file ``init`` writes.

Step 3 keeps every setup that ran ``serve`` beside its ``config.yaml`` working;
step 4 is what a new user has after ``init``. The resolved path is always
passed on, so bootstrap watches the file the command read.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_ENV_VAR = "MCP_CONFIG"
CWD_CONFIG_NAME = "config.yaml"


class ConfigPathSource(enum.Enum):
    """Where a resolved configuration path came from."""

    FLAG = "flag"
    ENV = "env"
    CWD = "cwd"
    USER = "user"


@dataclass(frozen=True)
class ResolvedConfigPath:
    """A configuration path and the rule that chose it."""

    path: Path
    source: ConfigPathSource

    @property
    def explicit(self) -> bool:
        """Whether someone named this file, rather than a default finding it."""
        return self.source in (ConfigPathSource.FLAG, ConfigPathSource.ENV)


def user_config_path() -> Path:
    """The file `init` writes when nothing names another. Read at call time, so `HOME` is honoured."""
    return Path.home() / ".config" / "mcp-hangar" / "config.yaml"


def resolve_config_path(*explicit: Path | str | None) -> ResolvedConfigPath:
    """Resolve the configuration file by the module's order.

    Args:
        explicit: Paths given on the command line, most specific first -- a
            command's own flag before the global ``--config``. ``None`` and
            empty values are skipped.
    """
    for candidate in explicit:
        if candidate:
            return ResolvedConfigPath(Path(candidate), ConfigPathSource.FLAG)

    from_env = os.environ.get(CONFIG_ENV_VAR)
    if from_env:
        return ResolvedConfigPath(Path(from_env), ConfigPathSource.ENV)

    # Absolute, because `init` writes this path into an MCP client's config,
    # and the client starts Hangar from a working directory of its own.
    in_cwd = Path.cwd() / CWD_CONFIG_NAME
    if in_cwd.is_file():
        return ResolvedConfigPath(in_cwd, ConfigPathSource.CWD)

    return ResolvedConfigPath(user_config_path(), ConfigPathSource.USER)


DEFAULT_RULE_HELP = "Defaults to $MCP_CONFIG, else ./config.yaml if present, else ~/.config/mcp-hangar/config.yaml."

__all__ = [
    "CONFIG_ENV_VAR",
    "DEFAULT_RULE_HELP",
    "ConfigPathSource",
    "ResolvedConfigPath",
    "resolve_config_path",
    "user_config_path",
]
