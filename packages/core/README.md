# packages/core -- Moved to src/

The Python package source code previously located here has been moved to the
repository root as part of the v0.13.0 structural reorganisation.
New layout:

- `src/mcp_hangar/` -- Python package source (was `packages/core/mcp_hangar/`)
- `tests/` -- Test suite (was `packages/core/tests/`)
- `pyproject.toml` -- Package configuration at repo root
- `Dockerfile` -- Container build at repo root
See [PRODUCT_ARCHITECTURE.md](../../docs/internal/PRODUCT_ARCHITECTURE.md) for rationale.
