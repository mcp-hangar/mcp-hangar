"""Config serializer -- inverse of server/config.py.

This module serializes in-memory provider and group state back to a
YAML-compatible dict structure, mirroring the format that server/config.py
reads on startup.  It is the persistence-side of the CRUD cycle.

Public API:
    serialize_providers(providers=None) -> dict[str, Any]
    serialize_groups(groups=None) -> dict[str, Any]
    serialize_full_config(providers=None, groups=None) -> dict[str, Any]
    write_config_backup(config_path: str) -> str
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .context import get_context

if TYPE_CHECKING:
    from ..domain.model.provider import Provider
    from ..domain.model.provider_group import ProviderGroup


def serialize_providers(providers: dict[str, Provider] | None = None) -> dict[str, Any]:
    """Return a YAML-compatible snapshot of all providers.

    Args:
        providers: Optional explicit dict of ``{provider_id: Provider}``.  When
            *None* the live application context is queried via ``get_context()``.

    Returns:
        Dict mapping each provider ID to its ``to_config_dict()`` output.
    """
    if providers is None:
        ctx = get_context()
        providers = ctx.repository.get_all()
    return {provider_id: provider.to_config_dict() for provider_id, provider in providers.items()}


def serialize_groups(groups: dict[str, ProviderGroup] | None = None) -> dict[str, Any]:
    """Return a YAML-compatible snapshot of all provider groups.

    Args:
        groups: Optional explicit dict of ``{group_id: ProviderGroup}``.  When
            *None* the live application context is queried via ``get_context()``.

    Returns:
        Dict mapping each group ID to its ``to_config_dict()`` output.
    """
    if groups is None:
        ctx = get_context()
        groups = ctx.groups
    return {group_id: group.to_config_dict() for group_id, group in groups.items()}


def serialize_full_config(
    providers: dict[str, Provider] | None = None,
    groups: dict[str, ProviderGroup] | None = None,
) -> dict[str, Any]:
    """Return the complete config as a YAML-compatible dict.

    Merges providers and groups under a single ``"providers"`` key,
    matching the top-level structure expected by ``server/config.py``.

    Args:
        providers: Optional explicit providers dict.  Passed through to
            :func:`serialize_providers`.
        groups: Optional explicit groups dict.  Passed through to
            :func:`serialize_groups`.

    Returns:
        ``{"providers": {**serialized_providers, **serialized_groups}}``
    """
    serialized_providers = serialize_providers(providers)
    serialized_groups = serialize_groups(groups)
    return {"providers": {**serialized_providers, **serialized_groups}}


def write_config_backup(config_path: str) -> str:
    """Rotate bak1..bak5 and write current state as the new bak1.

    Rotation order (oldest first):
        bak4 -> bak5  (bak5 is overwritten / dropped)
        bak3 -> bak4
        bak2 -> bak3
        bak1 -> bak2
        <new> -> bak1

    Args:
        config_path: Absolute or relative path to the config YAML file.
            Backup files are created alongside it with ``.bakN`` suffixes.

    Returns:
        String path of the newly written bak1 file.
    """
    base = Path(config_path)
    # Rotate: bak4->bak5, bak3->bak4, bak2->bak3, bak1->bak2
    for i in range(5, 1, -1):
        older = base.parent / f"{base.name}.bak{i}"
        newer = base.parent / f"{base.name}.bak{i - 1}"
        if newer.exists():
            newer.rename(older)
    backup_path = base.parent / f"{base.name}.bak1"
    content = yaml.safe_dump(
        serialize_full_config(),
        default_flow_style=False,
        sort_keys=True,
        allow_unicode=True,
    )
    backup_path.write_text(content, encoding="utf-8")
    return str(backup_path)
