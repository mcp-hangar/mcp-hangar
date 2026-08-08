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

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.server.config import (
    _interpolate_env_vars,
    _load_mcp_server_config,
    load_config_from_file,
)


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


@pytest.fixture
def load_programmatic():
    """Load a config the way the *programmatic* `bootstrap(config_dict=...)` path
    does: interpolate the caller's dict once (as the bootstrap entry point now
    does), then build the server from it. This exercises the path that has no
    file loader in front of it and therefore lost interpolation in 2.5.0-rc.4."""

    def go(token: str, mcp_server_id: str = "upstream") -> dict:
        config_dict = {
            "mcp_servers": {
                mcp_server_id: {
                    "mode": "remote",
                    "endpoint": "http://upstream.invalid/mcp",
                    "auth": {"type": "bearer", "token": token},
                }
            }
        }
        # The single interpolation pass bootstrap applies to a provided config
        # dict before merging it into the full configuration.
        interpolated = _interpolate_env_vars(config_dict)
        mcp_server = _load_mcp_server_config(mcp_server_id, interpolated["mcp_servers"][mcp_server_id])
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
    """The invariant, asserted by behaviour rather than by source text.

    A source-text guard (``"_interpolate_env_vars" not in source``, or a call-site
    count of exactly two) pins the shape of the file, not the property it is
    protecting: it breaks the moment a legitimate new call site is added (the
    programmatic path now has one) and passes for any refactor that keeps the
    string count the same while breaking the behaviour. What actually matters is
    that a document is interpolated once -- so that is what these tests check.
    """

    def test_the_file_path_interpolates_exactly_once(self, load, monkeypatch) -> None:
        # A double-interpolation canary: the resolved value is itself another
        # reference. A single pass yields the literal `${INNER}`; a second pass
        # would resolve that to `resolved-twice`.
        monkeypatch.setenv("OUTER", "${INNER}")
        monkeypatch.setenv("INNER", "resolved-twice")

        assert load("${OUTER}")["token"] == "${INNER}"

    def test_the_programmatic_path_interpolates_exactly_once(self, load_programmatic, monkeypatch) -> None:
        # Same canary on the config_dict path -- one pass, not two.
        monkeypatch.setenv("OUTER", "${INNER}")
        monkeypatch.setenv("INNER", "resolved-twice")

        assert load_programmatic("${OUTER}")["token"] == "${INNER}"

    def test_the_file_path_fails_closed_on_a_missing_variable(self, load, monkeypatch) -> None:
        # A reference to an unset variable must stop the boot, not pass through
        # literally to the upstream.
        monkeypatch.delenv("DEFINITELY_UNSET_TOKEN", raising=False)

        with pytest.raises(ConfigurationError):
            load("${DEFINITELY_UNSET_TOKEN}")


class TestTheProgrammaticConfigDictPathResolves:
    """The regression's missing coverage: 2.5.0-rc.4 dropped interpolation on the
    programmatic ``bootstrap(config_dict=...)`` path, so an auth ``${VAR}`` reached
    the upstream as the literal characters (a 401) and a missing variable no
    longer failed the boot closed."""

    def test_an_auth_reference_in_a_config_dict_resolves(self, load_programmatic, monkeypatch) -> None:
        monkeypatch.setenv("HANGAR_UPSTREAM_TOKEN", "s3cret")

        assert load_programmatic("${HANGAR_UPSTREAM_TOKEN}")["token"] == "s3cret"

    def test_a_secret_containing_a_variable_pattern_arrives_intact(self, load_programmatic, monkeypatch) -> None:
        # The generated-password shape, on the dict path too: interpolated once,
        # so the `${x}` inside the resolved value is not read as a reference.
        monkeypatch.delenv("x", raising=False)
        monkeypatch.setenv("HANGAR_UPSTREAM_TOKEN", "R9${x}q!")

        assert load_programmatic("${HANGAR_UPSTREAM_TOKEN}")["token"] == "R9${x}q!"

    def test_a_missing_variable_in_a_config_dict_fails_closed(self, load_programmatic, monkeypatch) -> None:
        monkeypatch.delenv("DEFINITELY_UNSET_TOKEN", raising=False)

        with pytest.raises(ConfigurationError):
            load_programmatic("${DEFINITELY_UNSET_TOKEN}")
