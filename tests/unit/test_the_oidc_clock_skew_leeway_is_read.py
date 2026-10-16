"""``oidc.clock_skew_leeway_seconds`` is read, bounded, and reaches the issuer entry (#1654).

The key was documented and never parsed: ``clock_skew_leeway_seconds: 5`` left
the leeway at 60, so an operator could not tighten token time validation.
"""

from __future__ import annotations

import pytest

from mcp_hangar.auth.config import MAX_CLOCK_SKEW_LEEWAY_SECONDS, parse_auth_config
from mcp_hangar.domain.exceptions import ConfigurationError


def _oidc(**extra):
    return {"enabled": True, "oidc": {"enabled": True, "issuer": "https://idp.example", "audience": "api", **extra}}


def test_the_default_is_unchanged():
    config = parse_auth_config(_oidc())

    assert config.oidc.clock_skew_leeway_seconds == 60
    assert config.oidc.resolved_issuers()[0].clock_skew_leeway_seconds == 60


@pytest.mark.parametrize("seconds", [0, 5, MAX_CLOCK_SKEW_LEEWAY_SECONDS])
def test_a_configured_leeway_reaches_the_issuer_the_validator_is_built_from(seconds):
    config = parse_auth_config(_oidc(clock_skew_leeway_seconds=seconds))

    assert config.oidc.clock_skew_leeway_seconds == seconds
    assert config.oidc.resolved_issuers()[0].clock_skew_leeway_seconds == seconds


def test_an_issuer_inherits_the_leeway_and_may_override_it():
    config = parse_auth_config(
        _oidc(
            clock_skew_leeway_seconds=5,
            issuers=[
                {"issuer": "https://a.example", "audience": "api"},
                {"issuer": "https://b.example", "audience": "api", "clock_skew_leeway_seconds": 0},
            ],
        )
    )

    assert [i.clock_skew_leeway_seconds for i in config.oidc.resolved_issuers()] == [5, 0]


@pytest.mark.parametrize("bad", [-1, MAX_CLOCK_SKEW_LEEWAY_SECONDS + 1, "5", 2.5, True, None])
def test_a_value_that_is_not_a_bounded_whole_number_refuses_the_configuration(bad):
    with pytest.raises(ConfigurationError, match="clock_skew_leeway_seconds"):
        parse_auth_config(_oidc(clock_skew_leeway_seconds=bad))


def test_a_bad_per_issuer_value_names_where_it_is():
    with pytest.raises(ConfigurationError, match=r"oidc\.issuers\.clock_skew_leeway_seconds"):
        parse_auth_config(
            _oidc(issuers=[{"issuer": "https://a.example", "audience": "api", "clock_skew_leeway_seconds": -5}])
        )
