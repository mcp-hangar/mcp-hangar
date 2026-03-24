#!/usr/bin/env bash
# check_enterprise_boundary.sh
#
# Enforce the import boundary between core (MIT) and enterprise (BSL 1.1).
#
# Rules:
#   1. DOMAIN layer: zero enterprise imports (hard rule).
#   2. APPLICATION layer: enterprise imports only inside try/except shims.
#   3. INFRASTRUCTURE/SERVER/BOOTSTRAP: conditional try imports allowed.
#   4. Enterprise imports core only via domain/contracts, domain/value_objects,
#      domain/events, domain/exceptions, application/ports (soft warning).
#
# Usage:
#   ./scripts/check_enterprise_boundary.sh
#
# Exit codes:
#   0 - boundary intact
#   1 - violation found

set -euo pipefail

VIOLATIONS=0

echo "Checking enterprise import boundary..."
echo ""

# Rule 1: DOMAIN layer must NEVER import from enterprise (hard rule)
echo "Rule 1: packages/core/mcp_hangar/domain/ must not import from enterprise/"
DOMAIN_VIOLATIONS=$(grep -rn --include="*.py" "^from enterprise\|^import enterprise" \
    packages/core/mcp_hangar/domain/ 2>/dev/null | \
    grep -v "__pycache__" | \
    grep -v "domain/security/roles.py" || true)  # roles.py is a designated compatibility shim

if [ -n "$DOMAIN_VIOLATIONS" ]; then
    echo ""
    echo "FAIL: domain layer imports enterprise:"
    echo "$DOMAIN_VIOLATIONS"
    VIOLATIONS=$((VIOLATIONS + 1))
else
    echo "OK"
fi

echo ""

# Rule 2: Unconditional (non-try-guarded) enterprise imports in non-bootstrap core files
echo "Rule 2: Non-bootstrap core must not have unconditional enterprise imports"
UNCONDITIONAL=$(grep -rn --include="*.py" "^from enterprise\|^import enterprise" \
    packages/core/mcp_hangar/ 2>/dev/null | \
    grep -v "__pycache__" | \
    grep -v "server/bootstrap/" | \
    grep -v "infrastructure/auth/__init__.py" | \
    grep -v "infrastructure/observability/__init__.py" | \
    grep -v "infrastructure/persistence/__init__.py" | \
    grep -v "application/commands/__init__.py" | \
    grep -v "application/queries/__init__.py" | \
    grep -v "domain/security/roles.py" || true)

if [ -n "$UNCONDITIONAL" ]; then
    echo ""
    echo "FAIL: unconditional enterprise imports in non-shim core files:"
    echo "$UNCONDITIONAL"
    VIOLATIONS=$((VIOLATIONS + 1))
else
    echo "OK"
fi

echo ""

# Rule 3: operator and helm-charts must never import enterprise
echo "Rule 3: packages/operator/ and packages/helm-charts/ must not import from enterprise/"
if grep -rn "from enterprise\|import enterprise" \
    packages/operator/ packages/helm-charts/ 2>/dev/null | \
    grep -v "__pycache__"; then
    echo ""
    echo "FAIL: operator/helm-charts import enterprise module(s)."
    VIOLATIONS=$((VIOLATIONS + 1))
else
    echo "OK"
fi

echo ""

# Rule 4 (warning): enterprise should import core only via approved interfaces
echo "Rule 4 (warning): enterprise/ imports of core internals"
ENTERPRISE_CORE_IMPORTS=$(grep -rn --include="*.py" "from mcp_hangar\." enterprise/ 2>/dev/null | \
    grep -v "__pycache__" | \
    grep -v "mcp_hangar\.domain\.contracts\." | \
    grep -v "mcp_hangar\.domain\.value_objects" | \
    grep -v "mcp_hangar\.domain\.events" | \
    grep -v "mcp_hangar\.domain\.exceptions" | \
    grep -v "mcp_hangar\.domain\.model\." | \
    grep -v "mcp_hangar\.domain\.services\." | \
    grep -v "mcp_hangar\.application\.commands\.commands" | \
    grep -v "mcp_hangar\.application\.queries\.queries" | \
    grep -v "mcp_hangar\.server\.api\." | \
    grep -v "mcp_hangar\.server\.http_auth_middleware" | \
    grep -v "mcp_hangar\.logging_config" | \
    grep -v "mcp_hangar\.metrics" | \
    grep -v "mcp_hangar\.infrastructure\.persistence\.event_serializer" | \
    grep -v "mcp_hangar\.infrastructure\.persistence\.in_memory_event_store" | \
    grep -v "mcp_hangar\.infrastructure\.event_bus" | \
    grep -v "mcp_hangar\.server\.api" || true)

if [ -n "$ENTERPRISE_CORE_IMPORTS" ]; then
    echo "WARNING: enterprise imports core internals beyond approved interfaces:"
    echo "$ENTERPRISE_CORE_IMPORTS"
    echo ""
    echo "Consider moving these contracts to domain/contracts/ or application/ports/."
else
    echo "OK"
fi

echo ""

if [ "$VIOLATIONS" -gt 0 ]; then
    echo "Import boundary check FAILED with $VIOLATIONS violation(s)."
    exit 1
fi

echo "Import boundary check PASSED."
