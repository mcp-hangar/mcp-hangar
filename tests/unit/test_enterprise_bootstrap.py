"""Behavior for entry point-based enterprise bootstrap."""

# pyright: reportMissingParameterType=false, reportPrivateLocalImportUsage=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false

from mcp_hangar.server.bootstrap import enterprise as enterprise_bootstrap


def test_load_enterprise_modules_returns_empty_components_without_entry_points(monkeypatch):
    """OSS installs should work when no enterprise plugin is registered."""
    monkeypatch.setattr(
        enterprise_bootstrap.importlib.metadata,
        "entry_points",
        lambda **kwargs: (),
    )

    components = enterprise_bootstrap.load_enterprise_modules({})

    assert components == enterprise_bootstrap.EnterpriseComponents()
