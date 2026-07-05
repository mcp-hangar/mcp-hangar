"""Tests for configurable command-bus rate limiting.

The command-bus rate limit (rps / burst) can be set via a ``rate_limit:`` section
in ``config.yaml``. Precedence is: config value > env var > built-in default.
"""

import pytest

from mcp_hangar.bootstrap.runtime import (
    DEFAULT_RATE_LIMIT_BURST,
    DEFAULT_RATE_LIMIT_RPS,
    create_runtime,
    resolve_rate_limit_config,
)
from mcp_hangar.domain.security.rate_limiter import InMemoryRateLimiter, reset_rate_limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Ensure each test builds a fresh global rate limiter."""
    reset_rate_limiter()
    yield
    reset_rate_limiter()


# --- resolve_rate_limit_config: precedence rules ----------------------------


def test_defaults_when_no_config_and_no_env():
    """Absent config and absent env -> built-in defaults."""
    cfg = resolve_rate_limit_config(rate_limit=None, env={})

    assert cfg.requests_per_second == float(DEFAULT_RATE_LIMIT_RPS)
    assert cfg.burst_size == int(DEFAULT_RATE_LIMIT_BURST)


def test_env_used_when_no_config():
    """Env vars are the fallback when config is absent."""
    cfg = resolve_rate_limit_config(
        rate_limit=None,
        env={"MCP_RATE_LIMIT_RPS": "42", "MCP_RATE_LIMIT_BURST": "7"},
    )

    assert cfg.requests_per_second == 42.0
    assert cfg.burst_size == 7


def test_config_used_when_present():
    """Config values are honored."""
    cfg = resolve_rate_limit_config(rate_limit={"rps": 5, "burst": 9}, env={})

    assert cfg.requests_per_second == 5.0
    assert cfg.burst_size == 9


def test_config_wins_over_env():
    """When both config and env are present, config wins."""
    cfg = resolve_rate_limit_config(
        rate_limit={"rps": 3, "burst": 4},
        env={"MCP_RATE_LIMIT_RPS": "99", "MCP_RATE_LIMIT_BURST": "88"},
    )

    assert cfg.requests_per_second == 3.0
    assert cfg.burst_size == 4


def test_partial_config_falls_back_per_key():
    """A partial config only overrides the keys it sets; others fall back to env."""
    cfg = resolve_rate_limit_config(
        rate_limit={"rps": 2},
        env={"MCP_RATE_LIMIT_RPS": "99", "MCP_RATE_LIMIT_BURST": "88"},
    )

    assert cfg.requests_per_second == 2.0  # from config
    assert cfg.burst_size == 88  # from env


# --- create_runtime: end-to-end wiring --------------------------------------


def test_create_runtime_uses_config_values():
    """A rate_limit config produces a RateLimiter with those values."""
    runtime = create_runtime(rate_limit={"rps": 5, "burst": 9}, env={})

    assert runtime.rate_limit_config.requests_per_second == 5.0
    assert runtime.rate_limit_config.burst_size == 9
    assert isinstance(runtime.rate_limiter, InMemoryRateLimiter)
    assert runtime.rate_limiter.config.requests_per_second == 5.0
    assert runtime.rate_limiter.config.burst_size == 9


def test_create_runtime_defaults_unchanged():
    """No config and no env -> default behavior is unchanged."""
    runtime = create_runtime(rate_limit=None, env={})

    assert runtime.rate_limit_config.requests_per_second == 10.0
    assert runtime.rate_limit_config.burst_size == 20


def test_create_runtime_config_wins_over_env():
    """Config present AND env present -> config wins."""
    runtime = create_runtime(
        rate_limit={"rps": 3, "burst": 4},
        env={"MCP_RATE_LIMIT_RPS": "99", "MCP_RATE_LIMIT_BURST": "88"},
    )

    assert runtime.rate_limit_config.requests_per_second == 3.0
    assert runtime.rate_limit_config.burst_size == 4
    assert isinstance(runtime.rate_limiter, InMemoryRateLimiter)
    assert runtime.rate_limiter.config.requests_per_second == 3.0
    assert runtime.rate_limiter.config.burst_size == 4
