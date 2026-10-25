"""``mcp-hangar auth bootstrap-admin`` and ``auth.role_assignments`` do not undo each other (F-auth-1).

A boot revokes the grants ``auth.role_assignments`` applied and no longer
lists. Two things must survive that:

- the bootstrap admin. ``bootstrap-admin`` composes the stores through
  ``bootstrap_auth``, which applies the file's lines first, so a line naming
  the same principal wrote the grant as the file's; the claim's own insert was
  then ignored, and removing the line later revoked the one administrator the
  one-shot claim had made.
- grants another config's lines made. ``bootstrap-admin --config X`` is a
  one-off command, add-only as it always was: it must not revoke what a file
  other than X granted, and certainly not when it refuses to run.

Only names that exist before the fix are imported, so without it these fail on
their assertions rather than at collection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mcp_hangar.auth.bootstrap import bootstrap_auth
from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig

pytestmark = pytest.mark.security

ADMIN = "user:admin"
OTHER = "svc:other-admin"


def _write_config(tmp_path: Path, db: Path, principals: list[str], name: str = "config.yaml") -> Path:
    """A config with API-key auth on *db* and one ``admin`` line per principal."""
    grants = "".join(f"\n    - {{principal: '{p}', role: admin, scope: global}}" for p in principals) or " []"
    cfg = tmp_path / name
    cfg.write_text(
        "mcp_servers: {}\n"
        "auth:\n"
        "  enabled: true\n"
        "  allow_anonymous: false\n"
        "  api_key:\n"
        "    enabled: true\n"
        "  storage:\n"
        "    driver: sqlite\n"
        f"    path: {db}\n"
        f"  role_assignments:{grants}\n"
    )
    return cfg


def _bootstrap_admin(*args: str):
    from mcp_hangar.server.cli.main import app

    return CliRunner().invoke(app, ["auth", "bootstrap-admin", *args])


def _serve_boot(db: Path, principals: list[str]):
    """What ``serve`` does to the role store at boot."""
    return bootstrap_auth(
        AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="sqlite", path=str(db)),
            api_key=ApiKeyAuthConfig(enabled=False),
            role_assignments=[RoleAssignment(principal=p, role="admin") for p in principals],
        )
    )


def _roles(components, principal: str) -> set[str]:
    return {r.name for r in components.role_store.get_roles_for_principal(principal)}


def _stored_roles(db: Path, principal: str) -> set[str]:
    """What the database holds, read without a boot that would re-apply anything."""
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteRoleStore

    store = SQLiteRoleStore(str(db))
    try:
        return {r.name for r in store.get_roles_for_principal(principal)}
    finally:
        store.close()


def test_the_bootstrap_admin_keeps_admin_when_the_file_drops_the_same_line(tmp_path: Path) -> None:
    db = tmp_path / "auth.db"
    cfg = _write_config(tmp_path, db, [ADMIN])

    result = _bootstrap_admin("--config", str(cfg), "--principal", ADMIN, "--show-key")
    assert result.exit_code == 0, result.output

    after = _serve_boot(db, [])
    assert _roles(after, ADMIN) == {"admin"}


def test_a_refused_bootstrap_admin_run_revokes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "auth.db"
    _serve_boot(db, [OTHER])
    cfg = _write_config(tmp_path, db, [], name="other.yaml")

    # Without --show-key and no OIDC issuer the command refuses before its claim.
    result = _bootstrap_admin("--config", str(cfg), "--principal", ADMIN)
    assert result.exit_code != 0

    assert _stored_roles(db, OTHER) == {"admin"}


def test_a_successful_bootstrap_admin_run_revokes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "auth.db"
    _serve_boot(db, [OTHER])
    cfg = _write_config(tmp_path, db, [], name="other.yaml")

    result = _bootstrap_admin("--config", str(cfg), "--principal", ADMIN, "--show-key")
    assert result.exit_code == 0, result.output

    assert _stored_roles(db, OTHER) == {"admin"}
    assert _stored_roles(db, ADMIN) == {"admin"}
