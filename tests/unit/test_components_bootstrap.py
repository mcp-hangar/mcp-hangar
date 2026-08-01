"""Behavior for entry point-based component bootstrap."""

# pyright: reportMissingParameterType=false, reportPrivateLocalImportUsage=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false

from mcp_hangar.server.bootstrap import components as components_bootstrap


def test_load_components_loads_no_auth_without_entry_points(monkeypatch):
    """OSS installs should work when no optional plugin is registered.

    The approval gate is in-core and unconditional (#678), so "nothing
    registered" means no *auth* components -- not an empty container.
    """
    monkeypatch.setattr(
        components_bootstrap.importlib.metadata,
        "entry_points",
        lambda **kwargs: (),
    )

    components = components_bootstrap.load_components({})

    assert components.auth_components is None
    assert components.approval_service is not None
