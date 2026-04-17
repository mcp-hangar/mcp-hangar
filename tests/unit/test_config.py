import pytest

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.server.config import _interpolate_env_vars  # pyright: ignore[reportPrivateUsage]


def test_missing_env_var_raises_configuration_error(monkeypatch: pytest.MonkeyPatch):
    """${VAR} without default must raise ConfigurationError when VAR is not set."""
    monkeypatch.delenv("MISSING_VAR_XYZ", raising=False)
    with pytest.raises(ConfigurationError, match="MISSING_VAR_XYZ"):
        _interpolate_env_vars({"key": "${MISSING_VAR_XYZ}"})


def test_env_var_with_default_works(monkeypatch: pytest.MonkeyPatch):
    """${VAR:-default} must return default when VAR is not set."""
    monkeypatch.delenv("ABSENT_VAR_ZZZ", raising=False)
    result = _interpolate_env_vars({"key": "${ABSENT_VAR_ZZZ:-fallback_value}"})
    assert result["key"] == "fallback_value"


def test_env_var_explicit_empty_default_works(monkeypatch: pytest.MonkeyPatch):
    """${VAR:-} (empty default) must return empty string without raising."""
    monkeypatch.delenv("ABSENT_VAR_ZZZ2", raising=False)
    result = _interpolate_env_vars({"key": "${ABSENT_VAR_ZZZ2:-}"})
    assert result["key"] == ""
