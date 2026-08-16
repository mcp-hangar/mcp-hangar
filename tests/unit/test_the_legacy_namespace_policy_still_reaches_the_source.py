"""`allowed_namespaces` / `denied_namespaces` moved; the old location must still work, loudly.

They used to live under `discovery.security`, where the core applied them behind
a check on the source's name. They belong to the kubernetes source now, and
`_migrate_namespace_policy` carries the old location over so an existing config
keeps working.

The stake is in that function's own docstring: moving a *security* setting
silently is the migration you must not do quietly -- a deployment that denied
`kube-system` would start accepting it and nothing would say so. Which is the
failure these tests exist to catch, and which nothing checked until now. The
fields the policy used to be read from were deleted in #939 precisely because
this path reads the raw config dict instead; that made the deletion safe and
left this the only thing standing between an operator and a silent widening.
"""

from structlog.testing import capture_logs

from mcp_hangar.server.bootstrap.discovery import _migrate_namespace_policy

DEPRECATION_EVENT = "discovery_namespace_policy_deprecated_location"

LEGACY = {"security": {"allowed_namespaces": ["team-a"], "denied_namespaces": ["kube-system"]}}


def _migrate(source_type, source_config, discovery_config=LEGACY):
    with capture_logs() as logs:
        migrated = _migrate_namespace_policy(source_type, source_config, discovery_config)
    deprecations = [e for e in logs if e.get("event") == DEPRECATION_EVENT]
    return migrated, deprecations


class TestWhenTheLegacyLocationIsTheOnlyOne:
    def test_the_policy_is_carried_onto_the_source(self):
        migrated, _ = _migrate("kubernetes", {"in_cluster": False})

        assert migrated["denied_namespaces"] == ["kube-system"]
        assert migrated["allowed_namespaces"] == ["team-a"]
        assert migrated["in_cluster"] is False, "the source's own keys must survive the merge"

    def test_it_says_so_once_per_key(self):
        _, deprecations = _migrate("kubernetes", {})

        assert {e["key"] for e in deprecations} == {"allowed_namespaces", "denied_namespaces"}
        assert all(e.get("log_level") == "warning" for e in deprecations)


class TestWhenTheSourceDeclaresItsOwn:
    """The new location wins, and silently -- there is nothing deprecated in use."""

    def test_the_legacy_value_does_not_overwrite_it(self):
        migrated, _ = _migrate("kubernetes", {"denied_namespaces": ["only-this"]})

        assert migrated["denied_namespaces"] == ["only-this"]

    def test_only_the_key_taken_from_the_old_location_is_warned_about(self):
        _, deprecations = _migrate("kubernetes", {"denied_namespaces": ["only-this"]})

        assert [e["key"] for e in deprecations] == ["allowed_namespaces"]


class TestWhenThereIsNothingToMigrate:
    def test_a_non_kubernetes_source_is_left_alone(self):
        source = {"path": "/etc/mcp-hangar/providers.d/"}

        migrated, deprecations = _migrate("filesystem", source)

        assert migrated == source
        assert deprecations == []

    def test_an_absent_legacy_block_adds_nothing(self):
        migrated, deprecations = _migrate("kubernetes", {"in_cluster": True}, discovery_config={})

        assert migrated == {"in_cluster": True}
        assert deprecations == []


def test_the_callers_config_is_not_mutated():
    """The loop in `create_discovery_orchestrator` rebinds `source_config` to the
    return value. If the function mutated its argument instead of copying, the
    two would agree and the bug would only surface for a caller that kept the
    original -- which is exactly the kind of thing nobody notices until it bites.
    """
    source = {"in_cluster": False}

    _migrate_namespace_policy("kubernetes", source, LEGACY)

    assert source == {"in_cluster": False}
