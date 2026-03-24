"""Fix relative imports in moved enterprise files.

This script rewrites all relative imports in enterprise/ to use
absolute import paths.

Run from repo root:
    python scripts/fix_enterprise_imports.py
"""

import re
from pathlib import Path


REPLACEMENTS: list[tuple[str, str]] = [
    # --- 3-dot deep relative (was in mcp_hangar/application/xxx/ or infrastructure/auth/) ---
    # auth_commands -> enterprise.auth.commands.commands (module was renamed)
    (r"from \.auth_commands import", "from enterprise.auth.commands.commands import"),
    (r"from \.auth_queries import", "from enterprise.auth.queries.queries import"),
    # 3-dot: domain security roles -> enterprise
    (r"from \.\.\.domain\.security\.roles import", "from enterprise.auth.roles import"),
    (r"from \.\.\.domain\.security import roles", "from enterprise.auth import roles"),
    # 3-dot: infrastructure.auth -> enterprise.auth.infrastructure
    (r"from \.\.\.infrastructure\.auth\.", "from enterprise.auth.infrastructure."),
    # 3-dot: domain -> mcp_hangar.domain
    (r"from \.\.\.domain\.", "from mcp_hangar.domain."),
    # 3-dot: application -> mcp_hangar.application
    (r"from \.\.\.application\.", "from mcp_hangar.application."),
    # 3-dot: logging_config -> mcp_hangar.logging_config
    (r"from \.\.\.logging_config import", "from mcp_hangar.logging_config import"),
    # 3-dot: metrics -> mcp_hangar.metrics
    (r"from \.\.\.metrics import", "from mcp_hangar.metrics import"),

    # --- 2-dot deep relative (was in mcp_hangar/server/ or mcp_hangar/infrastructure/observability/) ---
    # 2-dot: auth_config -> enterprise.auth.config (same bootstrap package, now enterprise)
    (r"from \.auth_config import", "from enterprise.auth.config import"),
    # 2-dot: domain security roles -> enterprise
    (r"from \.\.domain\.security\.roles import", "from enterprise.auth.roles import"),
    # 2-dot: infrastructure.auth -> enterprise.auth.infrastructure
    (r"from \.\.infrastructure\.auth\.", "from enterprise.auth.infrastructure."),
    # 2-dot: domain -> mcp_hangar.domain
    (r"from \.\.domain\.", "from mcp_hangar.domain."),
    # 2-dot: application -> mcp_hangar.application
    (r"from \.\.application\.", "from mcp_hangar.application."),
    # 2-dot: logging_config
    (r"from \.\.logging_config import", "from mcp_hangar.logging_config import"),
    # 2-dot: metrics
    (r"from \.\.metrics import", "from mcp_hangar.metrics import"),
    # 2-dot: value_objects (direct, without domain.)
    (r"from \.\.value_objects import", "from mcp_hangar.domain.value_objects import"),

    # --- Inline / same-level inside enterprise subpackages ---
    # routes.py: from .middleware -> from mcp_hangar.server.api.middleware
    (r"from \.middleware import", "from mcp_hangar.server.api.middleware import"),
    (r"from \.serializers import", "from mcp_hangar.server.api.serializers import"),
    # queries/handlers.py: from .queries -> mcp_hangar.application.queries.queries (QueryHandler base)
    (r"from \.queries import QueryHandler", "from mcp_hangar.application.queries.queries import QueryHandler"),
    # commands/handlers.py: from .commands -> mcp_hangar.application.commands.commands (CommandHandler already replaced above via 3-dot)

    # --- enterprise-internal cross refs (infrastructure -> persistence event serializer) ---
    (r"from \.event_serializer import", "from mcp_hangar.infrastructure.persistence.event_serializer import"),

    # --- 3-dot: application.ports.observability inline imports ---
    # already covered by the application rule above
]

ENTERPRISE_ROOT = Path(__file__).parent.parent / "enterprise"


def fix_file(path: Path) -> int:
    """Apply all replacements to a single file. Returns number of changes."""
    original = path.read_text(encoding="utf-8")
    content = original
    for pattern, replacement in REPLACEMENTS:
        content = re.sub(pattern, replacement, content)
    if content != original:
        path.write_text(content, encoding="utf-8")
        changes = sum(
            1 for p, r in REPLACEMENTS if re.search(p, original)
        )
        print(f"  Fixed: {path.relative_to(ENTERPRISE_ROOT.parent)}")
        return changes
    return 0


def main() -> None:
    total = 0
    for py_file in sorted(ENTERPRISE_ROOT.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        total += fix_file(py_file)
    print(f"\nDone. Total files with fixes: {total}")


if __name__ == "__main__":
    main()
