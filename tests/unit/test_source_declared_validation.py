"""A source answers for its own world; the core never learns its vocabulary.

`SecurityValidator` used to branch on the source's name and then apply namespace
rules read from the core's own `SecurityConfig`. Two costs, and the second is
the expensive one:

* the core's security config spoke Kubernetes, so a Consul author would find
  fields that do not apply to them and none that do;
* a new source either passed that check **vacuously** -- the branch simply did
  not match, so nothing was validated and nothing said so -- or its author had
  to edit security code to be checked at all.

The hook is optional by design. Making it abstract would break every existing
third-party source, which is the opposite of the point.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.discovery.security_validator import (
    SecurityConfig,
    SecurityValidator,
    ValidationResult,
)
from mcp_hangar.domain.discovery.discovery_source import (
    DiscoveryMode,
    DiscoverySource,
    SourcePolicyViolation,
)


def _discovered(name: str = "srv", source_type: str = "pretend", **metadata: Any) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        source_type=source_type,
        mode="subprocess",
        connection_info={"command": ["echo"]},
        metadata=metadata,
        fingerprint="fp",
    )


class _SourceWithItsOwnRules(DiscoverySource):
    """A source whose vocabulary the core has never heard of."""

    def __init__(self, *, allowed_datacenters: set[str]) -> None:
        super().__init__(mode=DiscoveryMode.ADDITIVE)
        self._allowed = allowed_datacenters

    @property
    def source_type(self) -> str:
        return "pretend"

    async def discover(self) -> list:
        return []

    async def health_check(self) -> bool:
        return True

    def policy_violation(self, mcp_server: Any) -> SourcePolicyViolation | None:
        datacenter = mcp_server.metadata.get("datacenter", "")
        if datacenter not in self._allowed:
            return SourcePolicyViolation(
                reason=f"Datacenter {datacenter!r} is not allowed",
                details={"datacenter": datacenter, "allowed": sorted(self._allowed)},
            )
        return None


class _SilentSource(DiscoverySource):
    """A source with no policy of its own -- the common case."""

    @property
    def source_type(self) -> str:
        return "silent"

    async def discover(self) -> list:
        return []

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
class TestTheSourceDecides:
    async def test_a_source_can_refuse_in_its_own_vocabulary(self) -> None:
        validator = SecurityValidator(SecurityConfig())
        source = _SourceWithItsOwnRules(allowed_datacenters={"dc1"})

        report = await validator.validate(_discovered(datacenter="dc9"), source=source)

        assert not report.is_passed
        assert report.result is ValidationResult.FAILED_SOURCE
        # The reason and the details come from the source untouched -- the core
        # has no idea what a datacenter is and does not need one.
        assert "dc9" in report.reason
        assert report.details["allowed"] == ["dc1"]

    async def test_it_accepts_what_its_own_rules_allow(self) -> None:
        validator = SecurityValidator(SecurityConfig())
        source = _SourceWithItsOwnRules(allowed_datacenters={"dc1"})

        report = await validator.validate(_discovered(datacenter="dc1"), source=source)

        assert report.result is not ValidationResult.FAILED_SOURCE

    async def test_a_source_without_rules_raises_no_objection(self) -> None:
        # The default hook. An existing third-party source that predates this
        # keeps working without implementing anything.
        validator = SecurityValidator(SecurityConfig())

        report = await validator.validate(_discovered(source_type="silent"), source=_SilentSource())

        assert report.result is not ValidationResult.FAILED_SOURCE


class TestTheCoreNoLongerKnowsSourceNames:
    def test_the_validator_does_not_branch_on_source_type(self) -> None:
        import pathlib

        source_file = (
            pathlib.Path(__file__).resolve().parents[2] / "src/mcp_hangar/application/discovery/security_validator.py"
        )
        text = source_file.read_text(encoding="utf-8")

        assert "if mcp_server.source_type ==" not in text, (
            "a security component recognising sources by name is what this change removed: "
            "every new source then passes vacuously or forces its author into security code"
        )


class TestTheKubernetesRulesKeptTheirMeaning:
    """The rules moved house; what they do must not have moved with them.

    Tested through `NamespacePolicy` rather than through the source, because the
    source needs the optional `kubernetes` package, which CI does not install --
    so testing them there would skip them there, and a security rule that is
    green because it never ran is the worst outcome available.
    """

    def _policy(self, **kwargs: Any):
        from mcp_hangar.infrastructure.discovery.kubernetes_source import NamespacePolicy

        return NamespacePolicy(**kwargs)

    def test_the_default_denied_namespaces_are_still_denied(self) -> None:
        policy = self._policy()

        assert policy.violation("kube-system") is not None
        assert policy.violation("default") is not None

    def test_denied_still_wins_over_allowed(self) -> None:
        policy = self._policy(allowed=frozenset({"apps"}), denied=frozenset({"apps"}))

        violation = policy.violation("apps")

        assert violation is not None
        assert "denied" in violation.reason

    def test_an_allow_list_still_excludes_everything_else(self) -> None:
        policy = self._policy(allowed=frozenset({"apps"}), denied=frozenset())

        assert policy.violation("apps") is None
        assert policy.violation("other") is not None

    def test_no_allow_list_means_everything_not_denied(self) -> None:
        policy = self._policy(denied=frozenset({"secret"}))

        assert policy.violation("anything") is None
        assert policy.violation("secret") is not None

    def test_the_details_still_name_what_was_checked(self) -> None:
        # The report an operator reads has to say which namespace and against
        # which list, exactly as the core's version did.
        violation = self._policy(denied=frozenset({"secret"})).violation("secret")

        assert violation is not None
        assert violation.details["namespace"] == "secret"
        assert violation.details["denied_namespaces"] == ["secret"]
