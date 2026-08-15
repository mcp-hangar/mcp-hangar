"""CLI tests for `mcp-hangar auth bootstrap-admin`.

Store-level durability/concurrency/transaction semantics are proven in
``test_initial_admin_bootstrap.py`` (SQLite) and ``test_postgres_auth_store.py``
(Postgres, mocked psycopg2). This file covers the CLI surface #451 added:
that it reuses the durable ``bootstrap_auth`` composition, fails closed on
non-durable / disabled / anonymous configs, refuses a second bootstrap without
mutating storage, records the local bootstrap actor, and prints no credential
unless ``--show-key`` asks for one.

Whether that flag is required is decided before the claim: see
``test_the_bootstrap_claim_is_not_spent_on_an_unusable_grant.py``.
"""

from textwrap import dedent
from unittest.mock import patch

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


def _write_config(tmp_path, *, driver="sqlite", enabled=True, allow_anonymous=False, oidc=False, api_key=True):
    """Write a minimal server config with an auth section and return its path.

    ``oidc`` decides whether an identity other than an API key can carry the
    administrator, which is what makes omitting ``--show-key`` sensible: with
    no trusted issuer the command refuses rather than spending its one-shot
    claim on a grant nobody could present.
    """
    db = tmp_path / "auth.db"
    cfg = tmp_path / "config.yaml"
    # Indented to sit under `auth:` -- written literally rather than dedented,
    # because dedent would flatten it to a top-level key.
    oidc_block = (
        "\n  oidc:\n    enabled: true\n    issuer: https://auth.example.com\n    audience: mcp-hangar" if oidc else ""
    )
    cfg.write_text(
        dedent(
            f"""
            mcp_servers: {{}}
            auth:
              enabled: {str(enabled).lower()}
              allow_anonymous: {str(allow_anonymous).lower()}
              api_key:
                enabled: {str(api_key).lower()}
              storage:
                driver: {driver}
                path: {db}
            """
        ).strip()
        + oidc_block
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


def _suggestions(result):
    """The refusal's suggestion lines, or an empty list for non-CLIError exits."""
    return list(getattr(result.exception, "suggestions", []) or [])


def _claim_is_spent(db) -> bool:
    """Whether the one-shot claim row exists -- what refuses a second bootstrap."""
    import sqlite3

    if not db.exists():
        return False
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM initial_admin_bootstrap").fetchone()[0] > 0
    finally:
        conn.close()


class TestBootstrapAdminSuccess:
    def test_grants_global_admin_on_sqlite(self, runner, tmp_path):
        cfg, db = _write_config(tmp_path)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin", "--show-key")

        assert result.exit_code == 0, result.output
        # The grant took effect: the principal now holds the global admin role.
        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteRoleStore

        role_store = SQLiteRoleStore(db)
        roles = {r.name for r in role_store.get_roles_for_principal("user:admin")}
        assert roles == {"admin"}

    def test_success_reports_key_id_and_actor(self, runner, tmp_path):
        cfg, _ = _write_config(tmp_path)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin", "--show-key")

        assert result.exit_code == 0, result.output
        assert "user:admin" in result.output
        # The local bootstrap actor is recorded in the CLI output.
        assert "local-cli-bootstrap" in result.output


class TestBootstrapAdminRefusesSecondRun:
    def test_second_run_refused_without_mutating_storage(self, runner, tmp_path):
        cfg, db = _write_config(tmp_path)

        first = _invoke(runner, "--config", str(cfg), "--principal", "user:admin", "--show-key")
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

        second = _invoke(runner, "--config", str(cfg), "--principal", "user:other", "--show-key")
        assert second.exit_code != 0
        assert "already" in _error_text(second).lower()
        # No mutation: the loser changed nothing.
        assert _counts() == before

    def test_flagless_second_run_reports_already_bootstrapped_not_a_false_suggestion(self, runner, tmp_path):
        # API-key-only deployment (no OIDC). The first run spends the one-shot
        # claim. A flagless second run must consult the store's spend state and
        # report the accurate "already bootstrapped; nothing changed" outcome --
        # NOT the pre-claim "re-run with --show-key; the claim has not been
        # spent" suggestion, which is false once the claim is spent and sends the
        # operator to a secret that is no longer recoverable. This is the rc.4
        # regression the amended flagged second-run test had masked.
        cfg, db = _write_config(tmp_path)

        first = _invoke(runner, "--config", str(cfg), "--principal", "user:admin", "--show-key")
        assert first.exit_code == 0, first.output
        assert _claim_is_spent(db)

        second = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert second.exit_code != 0
        assert "already" in _error_text(second).lower()
        suggestions = " ".join(_suggestions(second))
        # The misleading pre-claim advice ("the claim has not been spent, so this
        # works") must be gone now that the claim is spent.
        assert "not been spent" not in suggestions
        # Instead it names the recovery that does not require the lost secret:
        # clear the spent claim row / start from a fresh store.
        assert "initial_admin_bootstrap" in suggestions
        assert "fresh store" in suggestions


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


class TestBootstrapAdminRefusesUnusablePrintedKey:
    def test_oidc_and_show_key_refused_when_api_key_disabled_without_spending_claim(self, runner, tmp_path):
        # An OIDC issuer is trusted (so identity_authenticator=True), but
        # API-key auth is disabled. `--show-key` would print an API key that no
        # authenticator will ever accept, while spending the one-shot claim. Both
        # rc.4 refusals required `not identity_authenticator`, so neither fired
        # once OIDC was present -- the same "unusable grant" #833 fixed for the
        # no-OIDC case, surviving here. The enabled check must apply regardless
        # of OIDC, and the claim must not be spent.
        cfg, db = _write_config(tmp_path, oidc=True, api_key=False)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin", "--show-key")

        assert result.exit_code != 0
        text = _error_text(result).lower()
        suggestions = " ".join(_suggestions(result)).lower()
        assert "api-key auth is disabled" in text or "api-key auth is disabled" in suggestions
        assert "pointless" in text
        # The one-shot claim survives the refusal.
        assert not _claim_is_spent(db)

    def test_oidc_flagless_is_unaffected_when_api_key_disabled(self, runner, tmp_path):
        # The same config without `--show-key` is fine: the grant is an OIDC
        # admin role, the byproduct key is never printed, and the claim proceeds.
        cfg, db = _write_config(tmp_path, oidc=True, api_key=False)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert result.exit_code == 0, result.output
        assert _claim_is_spent(db)


class TestBootstrapAdminPrintsNoCredentialUnlessAsked:
    def test_raw_key_is_not_emitted_without_the_flag(self, runner, tmp_path):
        # An OIDC deployment: the principal authenticates on its own identity,
        # so withholding the secret costs it nothing and putting a global admin
        # credential in terminal scrollback would be gratuitous.
        cfg, _ = _write_config(tmp_path, oidc=True)
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
