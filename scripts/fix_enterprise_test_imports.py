"""Fix imports in moved enterprise test files.

Replaces old core paths with enterprise paths.
"""

import re
from pathlib import Path

REPLACEMENTS = [
    # auth infrastructure
    (r"from mcp_hangar\.infrastructure\.auth\.", "from enterprise.auth.infrastructure."),
    # auth commands/queries
    (r"from mcp_hangar\.application\.commands\.auth_commands", "from enterprise.auth.commands.commands"),
    (r"from mcp_hangar\.application\.commands\.auth_handlers", "from enterprise.auth.commands.handlers"),
    (r"from mcp_hangar\.application\.queries\.auth_queries", "from enterprise.auth.queries.queries"),
    (r"from mcp_hangar\.application\.queries\.auth_handlers", "from enterprise.auth.queries.handlers"),
    # auth server
    (r"from mcp_hangar\.server\.auth_bootstrap", "from enterprise.auth.bootstrap"),
    (r"from mcp_hangar\.server\.auth_config", "from enterprise.auth.config"),
    (r"from mcp_hangar\.server\.auth_cli", "from enterprise.auth.cli"),
    (r"from mcp_hangar\.server\.http_auth_middleware", "from enterprise.auth.http_middleware"),
    (r"from mcp_hangar\.server\.api\.auth", "from enterprise.auth.api.routes"),
    # auth roles
    (r"from mcp_hangar\.domain\.security\.roles", "from enterprise.auth.roles"),
    # observability
    (r"from mcp_hangar\.infrastructure\.observability\.langfuse_adapter", "from enterprise.integrations.langfuse"),
    # sqlite event store
    (r"from mcp_hangar\.infrastructure\.persistence\.sqlite_event_store", "from enterprise.persistence.sqlite_event_store"),
]

ENTERPRISE_TESTS = Path(__file__).parent.parent / "enterprise" / "tests"


def fix_file(path: Path) -> None:
    original = path.read_text(encoding="utf-8")
    content = original
    for pattern, replacement in REPLACEMENTS:
        content = re.sub(pattern, replacement, content)
    if content != original:
        path.write_text(content, encoding="utf-8")
        print(f"  Fixed: {path.relative_to(ENTERPRISE_TESTS.parent.parent)}")


def main() -> None:
    for py_file in sorted(ENTERPRISE_TESTS.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        fix_file(py_file)
    print("Done.")


if __name__ == "__main__":
    main()
