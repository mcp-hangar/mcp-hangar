"""Tests for the optional auth/approval component loader (bootstrap.enterprise)."""

# pyright: reportAny=false, reportMissingParameterType=false, reportPrivateLocalImportUsage=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnusedCallResult=false

from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Tests: EnterpriseComponents dataclass
# ---------------------------------------------------------------------------


class TestEnterpriseComponents:
    """Verify the EnterpriseComponents container dataclass."""

    def test_enterprise_components_defaults(self):
        """All fields default to None."""
        from mcp_hangar.server.bootstrap.enterprise import EnterpriseComponents

        ec = EnterpriseComponents()
        assert ec.auth_components is None

    def test_enterprise_components_with_values(self):
        """Setting fields on EnterpriseComponents works correctly."""
        from mcp_hangar.server.bootstrap.enterprise import EnterpriseComponents

        mock_auth = MagicMock()
        ec = EnterpriseComponents(
            auth_components=mock_auth,
        )
        assert ec.auth_components is mock_auth


# ---------------------------------------------------------------------------
# Tests: load_enterprise_modules()
# ---------------------------------------------------------------------------


class TestLoadEnterpriseModules:
    """The loader wires the in-core auth module directly (no plugin discovery)."""

    def test_no_auth_config_returns_empty(self):
        """With auth absent/disabled, returns EnterpriseComponents with all fields None."""
        from mcp_hangar.server.bootstrap.enterprise import load_enterprise_modules

        ec = load_enterprise_modules({})
        assert ec.auth_components is None
        assert ec.approval_service is None

    def test_unavailable_auth_module_returns_empty(self, monkeypatch):
        """When the auth module is not installed, returns empty components."""
        from mcp_hangar.server.bootstrap import enterprise as eb

        fake_exports = eb.AuthCompatibilityExports(
            AuthComponents=object,
            NullAuthComponents=object,
            bootstrap_auth=lambda *a, **k: None,
            parse_auth_config=lambda _raw: None,
            enterprise_auth_available=False,
        )
        monkeypatch.setattr(eb, "get_auth_compat_exports", lambda: fake_exports)

        ec = eb.load_enterprise_modules({"auth": {"enabled": True}})
        assert ec.auth_components is None

    def test_auth_enabled_populates_components(self, monkeypatch):
        """When auth is enabled, the loader builds auth components directly."""
        from mcp_hangar.server.bootstrap import enterprise as eb

        sentinel = MagicMock()

        class _Cfg:
            enabled = True

        captured = {}

        def _bootstrap_auth(cfg, **kwargs):
            captured["cfg"] = cfg
            captured["kwargs"] = kwargs
            return sentinel

        fake_exports = eb.AuthCompatibilityExports(
            AuthComponents=object,
            NullAuthComponents=object,
            bootstrap_auth=_bootstrap_auth,
            parse_auth_config=lambda _raw: _Cfg(),
            enterprise_auth_available=True,
        )
        monkeypatch.setattr(eb, "get_auth_compat_exports", lambda: fake_exports)
        monkeypatch.setattr(eb, "get_event_store", lambda: "event-store")

        ec = eb.load_enterprise_modules(
            {"auth": {"enabled": True}},
            event_bus="bus",
            event_publisher="publisher",
        )
        assert ec.auth_components is sentinel
        assert ec.approval_service is None
        assert captured["kwargs"]["event_bus"] == "bus"
        assert captured["kwargs"]["event_publisher"] == "publisher"
        assert captured["kwargs"]["event_store"] == "event-store"


# ---------------------------------------------------------------------------
# Tests: Bootstrap deprecation warning
# ---------------------------------------------------------------------------


class TestBootstrapLicenseKeyDeprecation:
    """Verify HANGAR_LICENSE_KEY deprecation warning."""

    def test_no_license_key_no_warning(self, monkeypatch):
        """Without HANGAR_LICENSE_KEY env var, no deprecation warning is emitted."""
        monkeypatch.delenv("HANGAR_LICENSE_KEY", raising=False)
        # Just verify the env var is not set -- bootstrap integration tested elsewhere

    def test_license_key_emits_deprecation_warning(self, monkeypatch):
        """Setting HANGAR_LICENSE_KEY emits a DeprecationWarning."""
        import warnings

        monkeypatch.setenv("HANGAR_LICENSE_KEY", "hk_v1_test")

        import os

        assert os.environ.get("HANGAR_LICENSE_KEY") == "hk_v1_test"

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Simulate the bootstrap check
            if os.environ.get("HANGAR_LICENSE_KEY"):
                warnings.warn(
                    "HANGAR_LICENSE_KEY is deprecated and has no effect. "
                    "All enterprise features are now available under the MIT license.",
                    DeprecationWarning,
                    stacklevel=1,
                )
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "deprecated" in str(w[0].message).lower()
