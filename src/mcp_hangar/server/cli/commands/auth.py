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


def _durable_store_for(full_config: dict, auth_config) -> object | None:
    """The backend to bootstrap on, or `None` to use the legacy driver path.

    Two questions used to be one. The command asked only about
    `auth.storage.driver`, which defaults to `memory`, so a deployment that had
    made the one storage decision (`persistence.backend`) was told its auth
    storage was not durable -- on exactly the deployments where this command is
    the only way in, since `/api/auth/**` requires an admin principal with no
    carve-out for the first call.

    A selected backend is durable by construction: `create_backend` refuses one
    that does not serve every persisted concern, and `memory` is not a backend
    at all. So the driver question only applies when no backend was selected.

    Raises:
        CLIError: when neither a backend nor a durable driver is configured.
    """
    from mcp_hangar.server.bootstrap.persistence import select_backend

    try:
        backend = select_backend(full_config)
    except Exception as e:  # noqa: BLE001 -- config errors become actionable CLI errors
        raise CLIError(
            f"Could not open the configured storage backend: {e}",
            suggestions=["Check the `persistence:` block in the config."],
            exit_code=1,
        ) from e

    if backend is not None:
        return backend

    driver = auth_config.storage.driver.lower()
    if driver not in _DURABLE_DRIVERS:
        raise CLIError(
            f"Auth storage driver {driver!r} is not durable; the initial admin cannot be bootstrapped on it.",
            suggestions=[
                "Set `auth.storage.driver` to `sqlite` or `postgresql` (a durable backend).",
                "Or select one backend for everything with `persistence.backend`.",
                "`memory` and `event_sourcing` do not provide a transactional bootstrap claim.",
            ],
            exit_code=1,
        )
    return None


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
    show_key: Annotated[
        bool,
        typer.Option(
            "--show-key",
            help=(
                "Print the bootstrap API key's secret. Required for a deployment "
                "whose only authenticator is API keys, where nothing else can "
                "produce a first credential."
            ),
        ),
    ] = False,
) -> None:
    """Grant the one-time initial global admin using the configured durable backend.

    Reuses the server's own ``bootstrap_auth()`` storage composition -- it never
    constructs an in-memory store -- and performs a single atomic claim. The
    claim succeeds exactly once for the whole deployment; a second run refuses
    without mutating storage.

    The claim creates an API key as well as the role, and the secret is not
    printed unless ``--show-key`` asks for it. That default is right for the
    case this was built for -- an OIDC principal, which authenticates on its own
    identity and needs no key -- and it left a deployment whose *only*
    authenticator is API keys with nowhere to go: every ``/api/auth/**`` route
    requires an admin, so the first key cannot be minted over HTTP, and the one
    command that could hand it over threw it away. The key row was created
    regardless, so what the operator got was an unusable credential and no way
    to reach their own gateway.

    Which flag is right is therefore decided *before* the claim, not reported
    after it: on a deployment with no trusted OIDC issuer, omitting
    ``--show-key`` is refused and the claim stays unspent. Saying "re-run with
    --show-key" afterwards was advice about a run that can never happen.
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

    persistence_backend = _durable_store_for(full_config, auth_config)

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

    components = bootstrap_auth(auth_config, persistence_backend=persistence_backend)
    store = components.api_key_store
    if not isinstance(store, IInitialAdminBootstrapStore):
        raise CLIError(
            "The configured auth backend does not support initial-admin bootstrap.",
            suggestions=["Use a durable `sqlite` or `postgresql` auth store."],
            exit_code=2,
        )

    # Whether anything other than an API key can carry this principal. Empty
    # exactly when no OIDC issuer is trusted, which is the same condition under
    # which a `JWTAuthenticator` was not built.
    identity_authenticator = bool(components.oidc_issuers)

    # The refusals below are ahead of the claim, and that position is the whole
    # point. The claim is one-shot and the key it mints is stored hashed, so a
    # run that ends without printing a *usable* secret cannot be repeated and
    # cannot be recovered from: the message "re-run with --show-key" used to be
    # printed at the exact moment re-running stopped being possible. A refusal
    # costs the operator one command; the advice cost them the deployment.
    if not auth_config.api_key.enabled:
        # An API key is only ever a usable credential when API-key auth is on.
        # With it off, both the printed secret (`--show-key`) and the silently
        # minted key are dead weight -- so the enabled check applies whether or
        # not an OIDC issuer is also trusted (the OIDC+--show-key case is the
        # gap #833 left behind).
        if not identity_authenticator:
            raise CLIError(
                "No authenticator is configured, so no administrator could ever present itself.",
                reason="API-key auth is disabled and no OIDC issuer is trusted.",
                suggestions=[
                    "Set `auth.api_key.enabled: true`, or configure `auth.oidc`, then re-run.",
                    "The one-time claim has not been spent.",
                ],
                exit_code=1,
            )
        if show_key:
            raise CLIError(
                "Printing an API key is pointless here: API-key auth is disabled, so no "
                "authenticator would ever accept the printed secret.",
                reason="`auth.api_key.enabled` is false, but `--show-key` asks to print an API key.",
                suggestions=[
                    "Re-run without `--show-key`: the grant is a global admin role for the OIDC "
                    "principal, which authenticates on its own identity and needs no key.",
                    "Or set `auth.api_key.enabled: true` if API keys should be accepted, then re-run.",
                    "The one-time claim has not been spent.",
                ],
                exit_code=1,
            )
    # API keys are the only authenticator and the secret would not be printed, so
    # the minted key would be unusable -- but "re-run with --show-key" is only
    # true advice while the claim is unspent. Consult the store's spend state
    # (read-only, never consuming the claim) so an already-bootstrapped
    # deployment falls through to the accurate "already bootstrapped; nothing
    # changed" report from the store call below instead of a suggestion it can no
    # longer act on.
    if not show_key and not identity_authenticator and not store.is_initial_admin_bootstrapped():
        raise CLIError(
            "Nothing could use this administrator: API keys are the only authenticator, "
            "and the key's secret would not be printed.",
            reason=(
                "The claim is one-shot and the key is stored hashed, so a secret not printed "
                "now cannot be obtained later."
            ),
            suggestions=[
                "Re-run with `--show-key` to print it. The claim has not been spent, so this works.",
                "Configure `auth.oidc` instead if the administrator authenticates on its own "
                "identity, and re-run without the flag.",
            ],
            exit_code=1,
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
            reason="The one-time claim is spent. It cannot be replayed, and the key it minted is stored hashed.",
            suggestions=[
                "As that administrator, create further keys and roles through `/api/auth/**`.",
                "If its secret was never printed, it is not recoverable: clear the "
                "`initial_admin_bootstrap` row in the auth store, or start from a fresh store, "
                "and re-run with `--show-key`.",
            ],
            exit_code=1,
        )

    raw_key, key_id = result
    console.print("[green]Initial global admin bootstrapped.[/green]")
    console.print(f"  principal : {principal}")
    console.print(f"  key id    : {key_id}")
    console.print("  actor     : local-cli-bootstrap")
    if show_key:
        console.print(f"\n  api key   : [bold]{raw_key}[/bold]")
        console.print(
            "\nThis secret is shown once and is not recoverable. It is stored hashed.\n"
            "Anyone holding it is a global administrator of this deployment."
        )
    else:
        console.print(
            f"\nNo API key secret printed: the grant is a global admin [bold]role[/bold] for "
            f"{principal!r}, which authenticates via its own identity against "
            f"{', '.join(components.oidc_issuers)}.\n"
            "A key was minted with the role and is stored hashed. Its secret was not printed and "
            "cannot be obtained now -- the claim is spent. Nothing needs it: authenticate as the "
            "principal and use `/api/auth/**` to create keys."
        )
