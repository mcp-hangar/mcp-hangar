"""`auth bootstrap-admin` works on the deployment shape that needs it most.

`/api/auth/**` requires an admin principal with no carve-out for the first
call, so a fresh gateway answers 401 to `POST /api/auth/keys` and this command
is the only way in. It read `auth.storage.driver` alone -- which defaults to
`memory` -- so on a deployment that made the one storage decision it refused
with "driver 'memory' is not durable" and left no way in at all.

The second case here is the one that then bit: the claim inserts the admin's
role assignment, `bootstrap_auth()` seeds `auth.role_assignments` earlier in
the same process, and the insert had no conflict clause -- so naming the same
principal in both killed the claim on a duplicate key.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mcp_hangar.server.cli.commands.auth import app


# Typer collapses a single-command group, so `bootstrap-admin` is not an
# argument here even though it is on the real command line.
runner = CliRunner()


def _said(result, text: str) -> bool:
    """Whether the command said `text`, on either stream.

    CLIError writes to stderr and the success report to stdout, and the runner
    keeps them apart -- so a check against `output` alone silently never sees a
    refusal.
    """
    streams = [result.output]
    try:
        streams.append(result.stderr)
    except ValueError:  # stderr not separately captured on this Click
        pass
    if result.exception is not None:
        # CLIError is rendered by the top-level CLI error handler in real use;
        # under the runner it arrives as the raised exception instead.
        streams.append(str(result.exception))
    return any(text in stream for stream in streams)


def _write(tmp_path, body: str):
    config = tmp_path / "config.yaml"
    config.write_text(body)
    return config


def _one_storage_config(tmp_path, *, role_assignments: str = "") -> str:
    return f"""
logging: {{level: WARNING}}
persistence:
  backend: sqlite
  sqlite: {{data_dir: {tmp_path / "data"}}}
auth:
  enabled: true
  allow_anonymous: false
  api_key: {{enabled: true, header_name: X-API-Key}}
{role_assignments}
mcp_servers: {{}}
"""


class TestASelectedBackendIsDurableEnough:
    def test_it_bootstraps_without_an_auth_storage_block(self, tmp_path) -> None:
        config = _write(tmp_path, _one_storage_config(tmp_path))

        result = runner.invoke(
            app,
            ["--config", str(config), "--principal", "service:probe", "--show-key"],
        )

        assert result.exit_code == 0, result.output
        assert "mcp_" in result.output

    def test_the_old_refusal_no_longer_fires(self, tmp_path) -> None:
        # The message named `auth.storage.driver`, which a one-storage config
        # deliberately does not set. Pinned because it is the symptom an
        # operator searches for.
        config = _write(tmp_path, _one_storage_config(tmp_path))

        result = runner.invoke(
            app,
            ["--config", str(config), "--principal", "service:probe", "--show-key"],
        )

        assert not _said(result, "is not durable")

    def test_a_config_with_no_storage_decision_at_all_is_still_refused(self, tmp_path) -> None:
        # `memory` is not a backend and provides no transactional claim; that
        # refusal is correct and must survive.
        config = _write(
            tmp_path,
            """
logging: {level: WARNING}
auth:
  enabled: true
  allow_anonymous: false
  api_key: {enabled: true, header_name: X-API-Key}
mcp_servers: {}
""",
        )

        result = runner.invoke(
            app,
            ["--config", str(config), "--principal", "service:probe", "--show-key"],
        )

        assert result.exit_code != 0
        assert _said(result, "not durable")


class TestTheClaimSurvivesAConfiguredRoleAssignment:
    def test_bootstrapping_a_principal_the_config_also_grants(self, tmp_path) -> None:
        # `bootstrap_auth()` seeds these before the claim runs, so the claim's
        # own insert meets a row that is already there. `assign_role` beside it
        # has always tolerated that; the claim did not, and died on a UNIQUE
        # violation with nothing saying the fix was "pick another principal".
        config = _write(
            tmp_path,
            _one_storage_config(
                tmp_path,
                role_assignments="""  role_assignments:
    - {principal: "service:probe", role: admin, scope: global}""",
            ),
        )

        result = runner.invoke(
            app,
            ["--config", str(config), "--principal", "service:probe", "--show-key"],
        )

        assert result.exit_code == 0, result.output
        assert not _said(result, "UNIQUE constraint")
        assert not _said(result, "duplicate key")

    def test_the_claim_is_still_one_shot(self, tmp_path) -> None:
        config = _write(tmp_path, _one_storage_config(tmp_path))
        args = ["--config", str(config), "--principal", "service:probe", "--show-key"]

        assert runner.invoke(app, args).exit_code == 0

        second = runner.invoke(app, args)

        assert second.exit_code != 0
        assert _said(second, "already been bootstrapped")


@pytest.mark.parametrize("driver", ["sqlite"])
def test_the_legacy_driver_path_still_works(tmp_path, driver) -> None:
    # The change routes around `auth.storage` when a backend is selected; it
    # must not break the deployments that still name a driver.
    config = _write(
        tmp_path,
        f"""
logging: {{level: WARNING}}
auth:
  enabled: true
  allow_anonymous: false
  api_key: {{enabled: true, header_name: X-API-Key}}
  storage: {{driver: {driver}, path: {tmp_path / "auth.db"}}}
mcp_servers: {{}}
""",
    )

    result = runner.invoke(
        app,
        ["--config", str(config), "--principal", "service:probe", "--show-key"],
    )

    assert result.exit_code == 0, result.output
