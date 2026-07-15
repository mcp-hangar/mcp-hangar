"""Auth administration commands.

Currently exposes ``bootstrap-admin``: the one-time, durable grant of the
initial global administrator for a fresh auth store. This is the escape hatch
for a deployment where API-key auth is enabled and anonymous access is off, so
no administrator yet exists to create the first one through the protected API.

Usage:
    mcp-hangar auth bootstrap-admin --config config.yaml --principal user:admin
"""

from pathlib import Path
from typing import Annotated

from rich.console import Console
import typer

from ..errors import CLIError, ConfigNotFoundError

app = typer.Typer(
    name="auth",
    help="Authentication administration commands.",
    no_args_is_help=True,
)

console = Console()

# Storage drivers that provide a durable, transactional initial-admin claim.
# `memory` is volatile and `event_sourcing` is not a bootstrap store, so both
# are refused rather than silently granting a non-durable admin.
_DURABLE_DRIVERS = {"sqlite", "postgresql", "postgres"}


@app.command(name="bootstrap-admin")
def bootstrap_admin_command(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to the server config.yaml whose durable auth backend to bootstrap.",
        ),
    ],
    principal: Annotated[
        str,
        typer.Option(
            "--principal",
            help="Existing external principal to grant global admin (e.g. 'user:admin').",
        ),
    ],
    key_name: Annotated[
        str,
        typer.Option(
            "--key-name",
            help="Human-readable label recorded for the bootstrap key.",
        ),
    ] = "initial admin",
) -> None:
    """Grant the one-time initial global admin using the configured durable backend.

    Reuses the server's own ``bootstrap_auth()`` storage composition -- it never
    constructs an in-memory store -- and performs a single atomic claim. The
    claim succeeds exactly once for the whole deployment; a second run refuses
    without mutating storage. No credential is printed: the grant is for an
    existing external principal (which authenticates via its own OIDC/API-key
    identity), and the incidental bootstrap key is never surfaced.
    """
    # Import here so the CLI stays importable even when the optional auth stack
    # is unavailable, and to keep --help fast.
    from mcp_hangar.auth.bootstrap import bootstrap_auth
    from mcp_hangar.auth.config import parse_auth_config
    from mcp_hangar.domain.contracts.authentication import IInitialAdminBootstrapStore
    from mcp_hangar.server.config import load_config_from_file

    if not config.is_file():
        raise ConfigNotFoundError(str(config))

    try:
        full_config = load_config_from_file(str(config))
    except CLIError:
        raise
    except Exception as e:  # noqa: BLE001 -- surface any parse error as an actionable CLI error
        raise CLIError(
            f"Could not read config {str(config)!r}: {e}",
            suggestions=["Check that the file is valid YAML and readable."],
            exit_code=1,
        ) from e

    auth_config = parse_auth_config(full_config.get("auth"))

    # Preconditions -- each refusal is fail-closed and names the exact fix.
    if not auth_config.enabled:
        raise CLIError(
            "Auth is disabled, so there is no administrator to bootstrap.",
            suggestions=["Set `auth.enabled: true` in the config, then re-run."],
            exit_code=1,
        )
    if auth_config.allow_anonymous:
        raise CLIError(
            "Anonymous access is allowed; bootstrap-admin is only for a non-anonymous policy.",
            suggestions=[
                "Set `auth.allow_anonymous: false` (an anonymous deployment needs no bootstrap admin).",
            ],
            exit_code=1,
        )

    driver = auth_config.storage.driver.lower()
    if driver not in _DURABLE_DRIVERS:
        raise CLIError(
            f"Auth storage driver {driver!r} is not durable; the initial admin cannot be bootstrapped on it.",
            suggestions=[
                "Set `auth.storage.driver` to `sqlite` or `postgresql` (a durable backend).",
                "`memory` and `event_sourcing` do not provide a transactional bootstrap claim.",
            ],
            exit_code=1,
        )

    components = bootstrap_auth(auth_config)
    store = components.api_key_store
    if not isinstance(store, IInitialAdminBootstrapStore):
        raise CLIError(
            "The configured auth backend does not support initial-admin bootstrap.",
            suggestions=["Use a durable `sqlite` or `postgresql` auth store."],
            exit_code=2,
        )

    # Single atomic claim. Global scope (no tenant): the initial admin is global.
    result = store.bootstrap_initial_admin(
        principal_id=principal,
        key_name=key_name,
        actor="local-cli-bootstrap",
    )

    if result is None:
        raise CLIError(
            "The initial administrator has already been bootstrapped; nothing was changed.",
            suggestions=[
                "The one-time claim is already spent -- use the standard key/role API as the existing admin.",
            ],
            exit_code=1,
        )

    _raw_key, key_id = result
    console.print("[green]Initial global admin bootstrapped.[/green]")
    console.print(f"  principal : {principal}")
    console.print(f"  key id    : {key_id}")
    console.print("  actor     : local-cli-bootstrap")
    console.print(
        "\nNo API key secret is printed by design: the grant is a global admin "
        f"[bold]role[/bold] for {principal!r}, which authenticates via its own identity."
    )
