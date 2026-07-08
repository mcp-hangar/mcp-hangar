"""Tests for command-bus rate-limit configuration wiring (#395).

Covers config-over-env precedence for ``rate_limit.rps`` / ``rate_limit.burst``
and that the resolved config flows into the constructed ``RateLimiter``.
"""

import pytest

from mcp_hangar.bootstrap.runtime import (
    apply_rate_limit_config,
    create_runtime,
    resolve_rate_limit_config,
)
from mcp_hangar.domain.security.rate_limiter import InMemoryRateLimiter, reset_rate_limiter


@pytest.fixture(autouse=True)
def _reset_global_rate_limiter():
    """Ensure each test starts and ends with a clean global rate limiter."""
    reset_rate_limiter()
    yield
    reset_rate_limiter()


class TestResolveRateLimitConfig:
    """Precedence resolution: config.yaml > env > defaults."""

    def test_defaults_when_nothing_set(self):
        cfg = resolve_rate_limit_config(env={})
        assert cfg.requests_per_second == 10.0
        assert cfg.burst_size == 20

    def test_env_fallback(self):
        cfg = resolve_rate_limit_config(
            env={"MCP_RATE_LIMIT_RPS": "25", "MCP_RATE_LIMIT_BURST": "50"},
        )
        assert cfg.requests_per_second == 25.0
        assert cfg.burst_size == 50

    def test_config_overrides_env(self):
        cfg = resolve_rate_limit_config(
            {"rps": 100, "burst": 200},
            env={"MCP_RATE_LIMIT_RPS": "25", "MCP_RATE_LIMIT_BURST": "50"},
        )
        assert cfg.requests_per_second == 100.0
        assert cfg.burst_size == 200

    def test_partial_config_falls_back_per_field(self):
        # rps from config, burst from env
        cfg = resolve_rate_limit_config(
            {"rps": 100},
            env={"MCP_RATE_LIMIT_BURST": "77"},
        )
        assert cfg.requests_per_second == 100.0
        assert cfg.burst_size == 77


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
