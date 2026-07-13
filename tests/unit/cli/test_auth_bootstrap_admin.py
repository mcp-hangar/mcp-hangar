"""CLI tests for `mcp-hangar auth bootstrap-admin`.

Store-level durability/concurrency/transaction semantics are proven in
``test_initial_admin_bootstrap.py`` (SQLite) and ``test_auth_coverage_batch4.py``
(Postgres, mocked psycopg2). This file covers the CLI surface #451 added:
that it reuses the durable ``bootstrap_auth`` composition, fails closed on
non-durable / disabled / anonymous configs, refuses a second bootstrap without
mutating storage, records the local bootstrap actor, and prints NO credential.
"""

from textwrap import dedent
from unittest.mock import patch

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


def _write_config(tmp_path, *, driver="sqlite", enabled=True, allow_anonymous=False):
    """Write a minimal server config with an auth section and return its path."""
    db = tmp_path / "auth.db"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent(
            f"""
            mcp_servers: {{}}
            auth:
              enabled: {str(enabled).lower()}
              allow_anonymous: {str(allow_anonymous).lower()}
              api_key:
                enabled: true
              storage:
                driver: {driver}
                path: {db}
            """
        ).strip()
        + "\n"
    )
    return cfg, db


def _invoke(runner, *args):
    from mcp_hangar.server.cli.main import app

    return runner.invoke(app, ["auth", "bootstrap-admin", *args])


def _error_text(result):
    """The refusal message. CliRunner invokes the app directly, so a raised
    CLIError propagates as ``result.exception`` rather than being rendered to
    stdout (that rendering happens in ``cli_main``). Its ``__str__`` is the
    user-facing message."""
    return str(result.exception) if result.exception else result.output


class TestBootstrapAdminSuccess:
    def test_grants_global_admin_on_sqlite(self, runner, tmp_path):
        cfg, db = _write_config(tmp_path)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert result.exit_code == 0, result.output
        # The grant took effect: the principal now holds the global admin role.
        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteRoleStore

        role_store = SQLiteRoleStore(db)
        roles = {r.name for r in role_store.get_roles_for_principal("user:admin")}
        assert roles == {"admin"}

    def test_success_reports_key_id_and_actor(self, runner, tmp_path):
        cfg, _ = _write_config(tmp_path)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert result.exit_code == 0, result.output
        assert "user:admin" in result.output
        # The local bootstrap actor is recorded in the CLI output.
        assert "local-cli-bootstrap" in result.output


class TestBootstrapAdminRefusesSecondRun:
    def test_second_run_refused_without_mutating_storage(self, runner, tmp_path):
        cfg, db = _write_config(tmp_path)

        first = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")
        assert first.exit_code == 0, first.output

        # Snapshot durable state after the winning claim.
        import sqlite3

        def _counts():
            conn = sqlite3.connect(db)
            try:
                keys = conn.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0]
                assigns = conn.execute("SELECT COUNT(*) FROM role_assignments").fetchone()[0]
                return keys, assigns
            finally:
                conn.close()

        before = _counts()

        second = _invoke(runner, "--config", str(cfg), "--principal", "user:other")
        assert second.exit_code != 0
        assert "already" in _error_text(second).lower()
        # No mutation: the loser changed nothing.
        assert _counts() == before


class TestBootstrapAdminFailsClosed:
    def test_rejects_non_durable_memory_driver(self, runner, tmp_path):
        cfg, db = _write_config(tmp_path, driver="memory")

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert result.exit_code != 0
        assert "durable" in _error_text(result).lower()
        assert not db.exists()  # nothing was created

    def test_rejects_when_auth_disabled(self, runner, tmp_path):
        cfg, _ = _write_config(tmp_path, enabled=False)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert result.exit_code != 0
        assert "disabled" in _error_text(result).lower()

    def test_rejects_anonymous_policy(self, runner, tmp_path):
        cfg, _ = _write_config(tmp_path, allow_anonymous=True)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert result.exit_code != 0
        assert "anonymous" in _error_text(result).lower()

    def test_missing_config_is_actionable(self, runner, tmp_path):
        missing = tmp_path / "nope.yaml"

        result = _invoke(runner, "--config", str(missing), "--principal", "user:admin")

        assert result.exit_code != 0
        assert str(missing) in _error_text(result)


class TestBootstrapAdminPrintsNoCredential:
    def test_raw_key_is_never_emitted(self, runner, tmp_path):
        cfg, _ = _write_config(tmp_path)
        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        sentinel_raw = "RAWSECRET_do_not_print_me"
        with patch.object(
            SQLiteApiKeyStore,
            "bootstrap_initial_admin",
            return_value=(sentinel_raw, "key-abc123"),
        ) as spy:
            result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert result.exit_code == 0, result.output
        # The raw credential must never reach stdout...
        assert sentinel_raw not in result.output
        # ...while the non-secret key id is fine to surface.
        assert "key-abc123" in result.output
        # And the claim is performed with the local bootstrap actor.
        assert spy.call_args.kwargs["actor"] == "local-cli-bootstrap"
