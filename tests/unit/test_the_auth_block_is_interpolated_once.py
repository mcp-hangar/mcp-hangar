"""A secret that contains a literal `${...}` survives being loaded.

Interpolation used to live inside `mcp_servers.<id>.auth` and nowhere else.
When it moved out to cover the whole document, the original call stayed where
it was, so that one block was interpolated twice.

A second pass is not a no-op, because it reads the output of the first. A
generated password like `R9${x}q!` arrives correctly from the environment, and
the second pass reads `${x}` in it as another reference:

    ConfigurationError: Required environment variable '${x}' is not set

That is the loud failure. The quiet one is worse -- if `x` happens to be set,
the password is substituted a second time and the server is configured with a
credential nobody wrote.

Interpolation is a property of the document, applied once as it is read. What
comes out of it is a value, not more configuration.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mcp_hangar.server.config import _load_mcp_server_config, load_config_from_file


@pytest.fixture
def load(tmp_path: Path):
    """Load a config the way the server does, and return the auth block as parsed."""

    def go(token: str, mcp_server_id: str = "upstream") -> dict:
        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "mcp_servers": {
                        mcp_server_id: {
                            "mode": "remote",
                            "endpoint": "http://upstream.invalid/mcp",
                            "auth": {"type": "bearer", "token": token},
                        }
                    }
                }
            )
        )
        config = load_config_from_file(str(path))
        mcp_server = _load_mcp_server_config(mcp_server_id, config["mcp_servers"][mcp_server_id])
        return mcp_server._auth_config

    return go


class TestASecretThatLooksLikeAReference:
    def test_a_password_containing_a_variable_pattern_arrives_intact(self, load, monkeypatch) -> None:
        # The shape a password generator produces. `x` is deliberately not set:
        # under the second pass this raised and the boot never completed.
        monkeypatch.delenv("x", raising=False)
        monkeypatch.setenv("HANGAR_UPSTREAM_TOKEN", "R9${x}q!")

        assert load("${HANGAR_UPSTREAM_TOKEN}")["token"] == "R9${x}q!"

    def test_it_is_not_substituted_again_when_the_variable_does_exist(self, load, monkeypatch) -> None:
        # The quiet half. This one never raised -- it just handed the upstream a
        # different credential than the operator stored.
        monkeypatch.setenv("x", "SUBSTITUTED")
        monkeypatch.setenv("HANGAR_UPSTREAM_TOKEN", "R9${x}q!")

        assert load("${HANGAR_UPSTREAM_TOKEN}")["token"] == "R9${x}q!"

    def test_a_literal_written_directly_in_the_file_is_still_a_reference(self, load, monkeypatch) -> None:
        # Interpolation itself is unchanged: what the operator writes in the
        # document is a reference, and it resolves once.
        monkeypatch.setenv("HANGAR_UPSTREAM_TOKEN", "s3cret")

        assert load("${HANGAR_UPSTREAM_TOKEN}")["token"] == "s3cret"


class TestTheBlockIsInterpolatedExactlyOnce:
    def test_the_loader_no_longer_interpolates_the_auth_block_itself(self) -> None:
        # The redundant call read the result of the document-wide pass. Guarding
        # the source keeps a future edit from reintroducing a second read.
        import inspect

        from mcp_hangar.server import config as config_module

        source = inspect.getsource(config_module._load_mcp_server_config)

        assert "_interpolate_env_vars" not in source

    def test_the_document_wide_pass_is_the_only_one(self) -> None:
        import inspect

        from mcp_hangar.server import config as config_module

        calls = inspect.getsource(config_module).count("_interpolate_env_vars(")

        # One definition, one call site: `load_config_from_file`.
        assert calls == 2
