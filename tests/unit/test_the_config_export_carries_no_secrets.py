"""`POST /config/export` is served under `config:read` and must not hand over secrets.

`serialize_full_config` had two leaks. A subprocess server's `env` went into the
export verbatim -- which is where its credentials live, and where the docs tell
an operator to put them (`GITHUB_TOKEN: ${GITHUB_TOKEN}`) -- and the
pass-through sections were copied from `ctx.full_config`, which is the
INTERPOLATED document, so an OIDC client secret referenced as `${VAR}` left as
its resolved value. No built-in role but `admin:*` carries `config:read`, so the
exposure is every custom role written for the export/diff use case: a GitOps
drift bot, a config reviewer (#1169).

`_sanitize` in `server/api/config.py` does not cover this: it runs on
`GET /config` only, is top-level-only by its own docstring, and strips by key
fragment -- and neither `mcp_servers` nor `auth` is one of those fragments.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.value_objects import McpServerMode

_TOKEN = "ghp_" + "a" * 36
_SECRET = "s3cr3t-oidc-client-secret"


def _server_with_env(env: dict[str, str]) -> McpServer:
    return McpServer(
        mcp_server_id="github",
        mode=McpServerMode.SUBPROCESS,
        command=["python", "-m", "server"],
        env=env,
    )


class TestASubprocessServersEnv:
    def test_a_credential_is_not_in_the_spec(self):
        spec = _server_with_env({"GITHUB_TOKEN": _TOKEN}).to_config_dict()

        assert spec["env"]["GITHUB_TOKEN"] == "[REDACTED]"

    def test_a_credential_under_a_neutral_name_is_caught_by_shape(self):
        """The reason redaction is two passes: the key name is not always a tell."""
        spec = _server_with_env({"SETTINGS": f"Authorization: Bearer {_TOKEN}"}).to_config_dict()

        assert _TOKEN not in spec["env"]["SETTINGS"]

    def test_an_ordinary_variable_is_left_alone(self):
        """Redaction is not deletion: the export is still a readable config."""
        spec = _server_with_env({"LOG_LEVEL": "debug", "GITHUB_TOKEN": _TOKEN}).to_config_dict()

        assert spec["env"]["LOG_LEVEL"] == "debug"


class TestThePassThroughSections:
    @pytest.fixture
    def exported(self):
        from mcp_hangar.server.config_serializer import serialize_full_config

        ctx = MagicMock()
        ctx.repository.get_all.return_value = {}
        ctx.groups = {}
        # As stored: `${OIDC_CLIENT_SECRET}` is already resolved by the time
        # bootstrap puts the document on the context.
        ctx.full_config = {
            "mcp_servers": {},
            "auth": {"enabled": True, "oidc": {"issuer": "https://idp", "client_secret": _SECRET}},
            "event_store": {"enabled": True, "driver": "sqlite", "path": "data/events.db"},
        }

        manager = MagicMock()
        manager.global_limit = 0
        manager.default_mcp_server_limit = 0

        with (
            patch("mcp_hangar.server.config_serializer.get_context", return_value=ctx),
            patch("mcp_hangar.server.tools.batch.concurrency.get_concurrency_manager", return_value=manager),
        ):
            return serialize_full_config(mcp_servers={}, groups={})

    def test_the_oidc_client_secret_does_not_leave(self, exported):
        assert exported["auth"]["oidc"]["client_secret"] == "[REDACTED]"

    def test_the_rest_of_the_section_survives(self, exported):
        # A section named `auth` matches the sensitive-key list itself, so the
        # obvious redaction replaces the whole block and the export stops being
        # a config file.
        assert exported["auth"]["enabled"] is True
        assert exported["auth"]["oidc"]["issuer"] == "https://idp"
        assert exported["event_store"]["driver"] == "sqlite"

    def test_the_exported_yaml_contains_neither_secret(self, exported):
        import yaml

        text = yaml.safe_dump(exported)

        assert _SECRET not in text
        assert _TOKEN not in text
