"""Tests for configurable command-bus rate limiting.

The command-bus rate limit (rps / burst) can be set via a ``rate_limit:`` section
in ``config.yaml``. Precedence is: config value > env var > built-in default.
"""

import pytest

from mcp_hangar.bootstrap.runtime import (
    DEFAULT_RATE_LIMIT_BURST,
    DEFAULT_RATE_LIMIT_RPS,
    apply_rate_limit_config,
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


class TestApplyRateLimitConfig:
    """The config value must flow into the constructed RateLimiter."""

    def test_config_flows_into_constructed_rate_limiter(self):
        runtime = create_runtime(env={})
        assert isinstance(runtime.rate_limiter, InMemoryRateLimiter)
        # Baseline env/default behavior.
        assert runtime.rate_limiter.config.requests_per_second == 10.0
        assert runtime.rate_limiter.config.burst_size == 20

        effective = apply_rate_limit_config(
            runtime,
            {"rate_limit": {"rps": 42, "burst": 84}},
            env={},
        )

        assert effective.requests_per_second == 42.0
        assert effective.burst_size == 84
        # Flows into the actual limiter instance...
        assert runtime.rate_limiter.config.requests_per_second == 42.0
        assert runtime.rate_limiter.config.burst_size == 84
        # ...and the runtime's reported config.
        assert runtime.rate_limit_config.requests_per_second == 42.0
        assert runtime.rate_limit_config.burst_size == 84

    def test_absent_section_preserves_env_default(self):
        runtime = create_runtime(env={"MCP_RATE_LIMIT_RPS": "15", "MCP_RATE_LIMIT_BURST": "30"})
        assert runtime.rate_limiter.config.requests_per_second == 15.0

        # No rate_limit section -> env/default behavior unchanged.
        apply_rate_limit_config(
            runtime,
            {"mcp_servers": {}},
            env={"MCP_RATE_LIMIT_RPS": "15", "MCP_RATE_LIMIT_BURST": "30"},
        )

        assert runtime.rate_limiter.config.requests_per_second == 15.0
        assert runtime.rate_limiter.config.burst_size == 30

    def test_new_buckets_use_updated_config(self):
        runtime = create_runtime(env={})
        limiter = runtime.rate_limiter
        assert isinstance(limiter, InMemoryRateLimiter)

        # Force a bucket under the old config.
        limiter.consume("SomeCommand")

        apply_rate_limit_config(runtime, {"rate_limit": {"rps": 5, "burst": 3}}, env={})

        # Old bucket dropped; a freshly created bucket reflects the new burst limit.
        result = limiter.check("SomeCommand")
        assert result.limit == 3


class TestCreateRuntimeEnvBaseline:
    """create_runtime keeps env-only backward-compatible behavior."""

    def test_env_only_construction(self):
        runtime = create_runtime(env={"MCP_RATE_LIMIT_RPS": "7", "MCP_RATE_LIMIT_BURST": "9"})
        assert runtime.rate_limit_config.requests_per_second == 7.0
        assert runtime.rate_limit_config.burst_size == 9
