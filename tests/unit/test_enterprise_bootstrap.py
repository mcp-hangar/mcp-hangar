"""Community-tier behavior for entry point-based enterprise bootstrap."""

# pyright: reportMissingParameterType=false, reportPrivateLocalImportUsage=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false

from mcp_hangar.domain.value_objects.license import LicenseTier
from mcp_hangar.server.bootstrap import enterprise as enterprise_bootstrap


def test_load_enterprise_modules_returns_empty_components_without_entry_points(monkeypatch):
    """Community and OSS installs should work when no enterprise plugin is registered."""
    monkeypatch.setattr(
        enterprise_bootstrap.importlib.metadata,
        "entry_points",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("community tier should not query entry points")),
    )

    components = enterprise_bootstrap.load_enterprise_modules(LicenseTier.COMMUNITY, {})

    assert components == enterprise_bootstrap.EnterpriseComponents()
