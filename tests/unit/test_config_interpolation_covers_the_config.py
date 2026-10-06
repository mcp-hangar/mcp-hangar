"""`${VAR}` works wherever the documentation says it does, which is everywhere.

It was interpolated inside `mcp_servers.<id>.auth` and nowhere else, while the
docs described it as a property of configuration: the production checklist tells
an operator to keep secrets out of the file this way, the transport guide says
"configuration values support environment variable interpolation", and the
reference documents it for Langfuse keys.

Found by running the multi-replica recipe against the published 2.5.0-rc.2
image. `persistence.postgresql.password: ${HANGAR_DB_PASSWORD}` reached psycopg2
as those twenty-two literal characters and three pods failed with `password
authentication failed for user "hangar"` -- with the variable correctly set, from
a Secret, exactly as the recipe and the Helm chart both instruct.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.server.config import load_config_from_file


@pytest.fixture
def config_file(tmp_path: Path):
    def write(config: dict) -> str:
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(config))
        return str(path)

    return write


class TestTheBlocksThatHoldSecrets:
    def test_the_postgresql_password(self, config_file, monkeypatch) -> None:
        # The one that failed on a real cluster.
        monkeypatch.setenv("HANGAR_DB_PASSWORD", "s3cret")
        path = config_file(
            {
                "mcp_servers": {},
                "persistence": {
                    "backend": "postgresql",
                    "postgresql": {"host": "db", "password": "${HANGAR_DB_PASSWORD}"},
                },
            }
        )

        config = load_config_from_file(path)

        assert config["persistence"]["postgresql"]["password"] == "s3cret"

    def test_an_otlp_endpoint(self, config_file, monkeypatch) -> None:
        # Was a Langfuse key, documented as interpolating and it did not; that
        # block is gone (#1683), and `observability` is still a section like any other.
        monkeypatch.setenv("OTLP_ENDPOINT", "http://collector:4317")
        path = config_file({"mcp_servers": {}, "observability": {"tracing": {"otlp_endpoint": "${OTLP_ENDPOINT}"}}})

        config = load_config_from_file(path)

        assert config["observability"]["tracing"]["otlp_endpoint"] == "http://collector:4317"

    def test_an_approval_adapter_signing_secret(self, config_file, monkeypatch) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "whsec")
        path = config_file({"mcp_servers": {}, "approvals": {"slack": {"signing_secret": "${SLACK_SIGNING_SECRET}"}}})

        config = load_config_from_file(path)

        assert config["approvals"]["slack"]["signing_secret"] == "whsec"

    def test_the_block_that_already_worked_still_does(self, config_file, monkeypatch) -> None:
        monkeypatch.setenv("MCP_API_KEY", "k-1")
        path = config_file(
            {
                "mcp_servers": {
                    "a": {"mode": "remote", "endpoint": "http://x/mcp", "auth": {"api_key": "${MCP_API_KEY}"}}
                }
            }
        )

        config = load_config_from_file(path)

        assert config["mcp_servers"]["a"]["auth"]["api_key"] == "k-1"


class TestItReachesEveryShape:
    def test_inside_a_list(self, config_file, monkeypatch) -> None:
        monkeypatch.setenv("HOME_DIR", "/srv")
        path = config_file({"mcp_servers": {"a": {"mode": "subprocess", "command": ["run", "--root", "${HOME_DIR}"]}}})

        config = load_config_from_file(path)

        assert config["mcp_servers"]["a"]["command"] == ["run", "--root", "/srv"]

    def test_a_default_when_the_variable_is_absent(self, config_file, monkeypatch) -> None:
        monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
        path = config_file(
            {"mcp_servers": {}, "persistence": {"postgresql": {"host": "${NOT_SET_ANYWHERE:-localhost}"}}}
        )

        config = load_config_from_file(path)

        assert config["persistence"]["postgresql"]["host"] == "localhost"

    def test_an_empty_default_is_allowed_explicitly(self, config_file, monkeypatch) -> None:
        monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
        path = config_file({"mcp_servers": {}, "persistence": {"postgresql": {"password": "${NOT_SET_ANYWHERE:-}"}}})

        config = load_config_from_file(path)

        assert config["persistence"]["postgresql"]["password"] == ""

    def test_a_missing_variable_with_no_default_names_itself(self, config_file, monkeypatch) -> None:
        # Loud beats a gateway that starts and cannot authenticate, which is the
        # failure this whole change came from.
        monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
        path = config_file({"mcp_servers": {}, "persistence": {"postgresql": {"password": "${NOT_SET_ANYWHERE}"}}})

        with pytest.raises(ConfigurationError) as excinfo:
            load_config_from_file(path)

        assert "NOT_SET_ANYWHERE" in str(excinfo.value)

    def test_values_that_are_not_strings_are_untouched(self, config_file) -> None:
        path = config_file({"mcp_servers": {}, "coordination": {"lease_ttl_s": 15, "renew_interval_s": 5.0}})

        config = load_config_from_file(path)

        assert config["coordination"] == {"lease_ttl_s": 15, "renew_interval_s": 5.0}

    def test_a_value_with_no_variable_in_it_is_unchanged(self, config_file) -> None:
        path = config_file({"mcp_servers": {}, "persistence": {"postgresql": {"host": "db.internal.example"}}})

        config = load_config_from_file(path)

        assert config["persistence"]["postgresql"]["host"] == "db.internal.example"
