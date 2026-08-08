"""`bootstrap-admin` decides which flag is right before it spends the claim.

The claim is one-shot and the key it mints is stored hashed, so a run that ends
without printing the secret can be neither repeated nor recovered from. The
command used to end such a run by printing:

    If API keys are this deployment's only authenticator, re-run with
    --show-key -- the claim is one-shot, so do it now rather than after.

The claim had been spent by the line above it. Re-running was refused
("The initial administrator has already been bootstrapped"), and the secret it
would have printed was already in the database as a hash. The advice arrived at
the exact moment it stopped being possible to take.

The refusal that replaces it costs one command. `bootstrap-admin` is the only
subcommand in the auth CLI, and every `/api/auth/**` route requires an existing
admin, so what the message cost was the deployment.
"""

from __future__ import annotations

import sqlite3
from textwrap import dedent

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


def _write_config(tmp_path, *, oidc: bool = False, api_key: bool = True):
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
              enabled: true
              allow_anonymous: false
              api_key:
                enabled: {str(api_key).lower()}
              storage:
                driver: sqlite
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
    return str(result.exception) if result.exception else result.output


def _claim_is_spent(db) -> bool:
    """Whether the one-shot row exists, which is what refuses the second run."""
    if not db.exists():
        return False
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM initial_admin_bootstrap").fetchone()[0] > 0
    finally:
        conn.close()


class TestApiKeysAreTheOnlyWayIn:
    def test_omitting_show_key_is_refused(self, runner, tmp_path) -> None:
        cfg, _ = _write_config(tmp_path)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert result.exit_code != 0
        assert "only authenticator" in _error_text(result)

    def test_the_claim_survives_the_refusal(self, runner, tmp_path) -> None:
        # The whole point. A refusal that spent the claim would be the same dead
        # end with better wording.
        cfg, db = _write_config(tmp_path)

        _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert not _claim_is_spent(db)

    def test_the_advice_can_then_be_taken(self, runner, tmp_path) -> None:
        cfg, db = _write_config(tmp_path)

        refused = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")
        assert refused.exit_code != 0

        granted = _invoke(runner, "--config", str(cfg), "--principal", "user:admin", "--show-key")

        assert granted.exit_code == 0, granted.output
        assert "api key" in granted.output
        assert _claim_is_spent(db)

    def test_no_authenticator_at_all_is_refused_before_the_claim(self, runner, tmp_path) -> None:
        # Nothing could present this administrator: no issuer is trusted and API
        # key auth is off, so even a printed secret would be rejected.
        cfg, db = _write_config(tmp_path, api_key=False)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin", "--show-key")

        assert result.exit_code != 0
        assert "No authenticator" in _error_text(result)
        assert not _claim_is_spent(db)


class TestAnIdentityDeploymentIsUnaffected:
    def test_omitting_show_key_is_accepted_when_an_issuer_is_trusted(self, runner, tmp_path) -> None:
        cfg, db = _write_config(tmp_path, oidc=True)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert result.exit_code == 0, result.output
        assert _claim_is_spent(db)

    def test_the_silent_branch_no_longer_offers_a_second_chance(self, runner, tmp_path) -> None:
        cfg, _ = _write_config(tmp_path, oidc=True)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        assert "re-run" not in result.output.lower()
        # It says what actually happened instead.
        assert "spent" in result.output

    def test_it_names_the_issuer_the_principal_will_authenticate_against(self, runner, tmp_path) -> None:
        cfg, _ = _write_config(tmp_path, oidc=True)

        result = _invoke(runner, "--config", str(cfg), "--principal", "user:admin")

        # `count`, not `in`: a containment test against a host-shaped literal
        # reads as URL validation to CodeQL, and this is an assertion about a
        # printed sentence.
        assert result.output.count("auth.example.com") == 1


class TestTheSpentClaimNamesRealRecovery:
    def test_the_second_run_does_not_send_the_operator_to_an_api_it_cannot_reach(self, runner, tmp_path) -> None:
        cfg, _ = _write_config(tmp_path)

        first = _invoke(runner, "--config", str(cfg), "--principal", "user:admin", "--show-key")
        assert first.exit_code == 0, first.output

        second = _invoke(runner, "--config", str(cfg), "--principal", "user:admin", "--show-key")

        assert second.exit_code != 0
        from mcp_hangar.server.cli.errors import CLIError

        assert isinstance(second.exception, CLIError)
        suggestions = " ".join(second.exception.suggestions)
        # A way out that does not require already holding the credential.
        assert "initial_admin_bootstrap" in suggestions
        assert "fresh store" in suggestions
