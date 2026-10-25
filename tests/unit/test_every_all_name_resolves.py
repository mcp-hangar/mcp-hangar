"""Every name a package's `__all__` exports exists (#2068).

`mcp_hangar.observability.__all__` kept an empty string after a name was
removed, so `from mcp_hangar.observability import *` raised AttributeError.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import mcp_hangar


def _packages_with_all() -> list[str]:
    found = []
    for info in pkgutil.walk_packages(mcp_hangar.__path__, prefix="mcp_hangar."):
        if not info.ispkg:
            continue
        try:
            module = importlib.import_module(info.name)
        except ImportError:  # an optional extra is not installed
            continue
        if hasattr(module, "__all__"):
            found.append(info.name)
    return found


@pytest.mark.parametrize("name", _packages_with_all())
def test_every_exported_name_exists(name: str) -> None:
    module = importlib.import_module(name)

    missing = [entry for entry in module.__all__ if not entry or not hasattr(module, entry)]

    assert missing == [], f"{name}.__all__ names what it does not define: {missing}"
