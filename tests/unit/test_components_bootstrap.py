"""Behavior for entry point-based component bootstrap."""

# pyright: reportMissingParameterType=false, reportPrivateLocalImportUsage=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false

from mcp_hangar.server.bootstrap import components as components_bootstrap


def test_load_components_returns_empty_components_without_entry_points(monkeypatch):
    """OSS installs should work when no optional plugin is registered."""
    monkeypatch.setattr(
        components_bootstrap.importlib.metadata,
        "entry_points",
        lambda **kwargs: (),
    )

    components = components_bootstrap.load_components({})

    assert components == components_bootstrap.ServerComponents()
