"""Catalog seed loader for populating the MCP provider catalog on first boot.

Loads entries from a YAML file into McpCatalogRepository if the catalog
is empty. Subsequent calls are no-ops (idempotent).
"""

import uuid
from pathlib import Path
from typing import Any

import yaml

from ...domain.contracts.catalog import McpCatalogRepository
from ...domain.model.catalog import McpProviderEntry
from ...logging_config import get_logger

logger = get_logger(__name__)


def load_catalog_seed(repo: McpCatalogRepository, seed_path: Path) -> int:
    """Seed the catalog with builtin entries from a YAML file.

    Idempotent: if the catalog already has entries, returns 0 immediately
    without reading or parsing the YAML file.

    The YAML file must contain a top-level 'entries' list. Each entry
    in the list maps to McpProviderEntry fields. The loader sets
    builtin=True and source="builtin" for all seeded entries.
    If entry_id is absent in the YAML, a UUID is generated.

    Args:
        repo: McpCatalogRepository to seed.
        seed_path: Path to the YAML file (e.g. data/catalog_seed.yaml).

    Returns:
        Number of entries loaded. 0 if catalog was already seeded.
    """
    if repo.count() > 0:
        logger.debug("catalog_seed_skipped", reason="already_seeded", count=repo.count())
        return 0

    if not seed_path.exists():
        logger.warning("catalog_seed_file_not_found", path=str(seed_path))
        return 0

    with seed_path.open() as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    raw_entries: list[dict] = data.get("entries", [])
    loaded = 0

    for raw in raw_entries:
        entry = McpProviderEntry(
            entry_id=raw.get("entry_id") or str(uuid.uuid4()),
            name=raw["name"],
            description=raw.get("description", ""),
            mode=raw.get("mode", "subprocess"),
            command=raw.get("command", []),
            image=raw.get("image"),
            tags=raw.get("tags", []),
            verified=raw.get("verified", True),
            source="builtin",
            required_env=raw.get("required_env", []),
            builtin=True,
        )
        repo.add_entry(entry)
        loaded += 1

    logger.info("catalog_seeded", entries_loaded=loaded, seed_path=str(seed_path))
    return loaded
