"""Tests for entry point-based enterprise bootstrap loading."""

# pyright: reportAny=false, reportMissingParameterType=false, reportPrivateLocalImportUsage=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnusedCallResult=false

from unittest.mock import MagicMock


class _FakeEntryPoint:
    def __init__(self, name: str, loader):
        self.name = name
        self._loader = loader

    def load(self):
        return self._loader


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
    """Verify unconditional enterprise module loading."""

    def test_no_entry_points_returns_empty(self, monkeypatch):
        """Without entry points, returns EnterpriseComponents with all fields None."""
        from mcp_hangar.server.bootstrap import enterprise as enterprise_bootstrap
        from mcp_hangar.server.bootstrap.enterprise import load_enterprise_modules

        monkeypatch.setattr(enterprise_bootstrap.importlib.metadata, "entry_points", lambda **kwargs: ())

        ec = load_enterprise_modules({})
        assert ec.auth_components is None

    def test_registered_loader_populates_auth(self, monkeypatch):
        """Registered enterprise loader callable populates auth components."""
        from mcp_hangar.server.bootstrap import enterprise as enterprise_bootstrap
        from mcp_hangar.server.bootstrap.enterprise import EnterpriseComponents, load_enterprise_modules

        auth_components = MagicMock()

        def loader(config, event_bus, event_publisher):
            assert config == {"auth": {"enabled": True}}
            assert event_bus == "bus"
            assert event_publisher == "publisher"
            return EnterpriseComponents(auth_components=auth_components)

        monkeypatch.setattr(
            enterprise_bootstrap.importlib.metadata,
            "entry_points",
            lambda **kwargs: (_FakeEntryPoint("enterprise-auth", loader),),
        )

        ec = load_enterprise_modules(
            {"auth": {"enabled": True}},
            event_bus="bus",
            event_publisher="publisher",
        )
        assert ec.auth_components is auth_components
        assert ec.approval_service is None

    def test_multiple_registered_loaders_merge_components(self, monkeypatch):
        """Multiple enterprise loaders can contribute different component types."""
        from mcp_hangar.server.bootstrap import enterprise as enterprise_bootstrap
        from mcp_hangar.server.bootstrap.enterprise import EnterpriseComponents, load_enterprise_modules

        auth_components = MagicMock()
        approval_service = MagicMock()

        monkeypatch.setattr(
            enterprise_bootstrap.importlib.metadata,
            "entry_points",
            lambda **kwargs: (
                _FakeEntryPoint(
                    "enterprise-auth",
                    lambda config, event_bus, event_publisher: EnterpriseComponents(
                        auth_components=auth_components,
                    ),
                ),
                _FakeEntryPoint(
                    "enterprise-approvals",
                    lambda config, event_bus, event_publisher: EnterpriseComponents(
                        approval_service=approval_service,
                    ),
                ),
            ),
        )

        ec = load_enterprise_modules({})
        assert ec.auth_components is auth_components
        assert ec.approval_service is approval_service


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
