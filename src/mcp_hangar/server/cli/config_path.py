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

A file that is not there is fatal, whichever rule chose it (#1650). A missing
file used to boot a built-in demo configuration with every ``hangar_*`` tool and
none of the operator's pins, policies or auth, so a typo in ``--config`` gave an
ungoverned gateway. ``require_config_file`` is the check, and it says where the
path came from.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from pathlib import Path

from ...domain.exceptions import ConfigurationError

CONFIG_ENV_VAR = "MCP_CONFIG"
CWD_CONFIG_NAME = "config.yaml"


class ConfigPathSource(enum.Enum):
    """Where a resolved configuration path came from."""

    FLAG = "flag"
    ENV = "env"
    CWD = "cwd"
    USER = "user"
    #: Handed to `bootstrap(config_path=...)` or `load_configuration` by a caller.
    ARGUMENT = "argument"


@dataclass(frozen=True)
class ResolvedConfigPath:
    """A configuration path and the rule that chose it."""

    path: Path
    source: ConfigPathSource

    @property
    def explicit(self) -> bool:
        """Whether someone named this file, rather than a default finding it."""
        return self.source in (ConfigPathSource.FLAG, ConfigPathSource.ENV, ConfigPathSource.ARGUMENT)


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


_NAMED_BY = {
    ConfigPathSource.FLAG: "named on the command line",
    ConfigPathSource.ENV: f"named by ${CONFIG_ENV_VAR}",
    ConfigPathSource.ARGUMENT: "passed as config_path",
    ConfigPathSource.CWD: "found as ./config.yaml",
    ConfigPathSource.USER: "the default",
}


class MissingConfigFileError(ConfigurationError):
    """The configuration file a boot would read is not a readable file (#1650)."""

    def __init__(self, resolved: ResolvedConfigPath, problem: str):
        if resolved.source is ConfigPathSource.USER:
            message = (
                f"No configuration file: there is no ./config.yaml and {resolved.path} {problem}. "
                "Run 'mcp-hangar init' to create one, or name one with --config or $MCP_CONFIG."
            )
        else:
            message = (
                f"Configuration file {resolved.path} ({_NAMED_BY[resolved.source]}) {problem}. "
                "Nothing is served without the configuration that was asked for."
            )
        super().__init__(message, details={"path": str(resolved.path), "source": resolved.source.value})
        self.resolved = resolved


def require_config_file(resolved: ResolvedConfigPath) -> Path:
    """Return the path if it is a readable regular file, else raise `MissingConfigFileError`.

    There is no fallback configuration: whichever rule chose the path, a gateway
    started without the file would run with none of its policies.
    """
    target = resolved.path
    if not target.exists():
        raise MissingConfigFileError(resolved, "does not exist")
    if not target.is_file():
        raise MissingConfigFileError(resolved, "is not a regular file")
    if not os.access(target, os.R_OK):
        raise MissingConfigFileError(resolved, "is not readable")
    return target


DEFAULT_RULE_HELP = "Defaults to $MCP_CONFIG, else ./config.yaml if present, else ~/.config/mcp-hangar/config.yaml."

__all__ = [
    "CONFIG_ENV_VAR",
    "DEFAULT_RULE_HELP",
    "ConfigPathSource",
    "MissingConfigFileError",
    "ResolvedConfigPath",
    "require_config_file",
    "resolve_config_path",
    "user_config_path",
]
