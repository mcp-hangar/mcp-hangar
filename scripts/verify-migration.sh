#!/bin/bash
set -euo pipefail

# MCP-Hangar Monorepo Migration Script
# Run from repo root
# This script helps migrate from flat structure to packages monorepo

echo "🚀 Starting monorepo restructure verification..."

# Safety check
if [ ! -d "packages/core/mcp_hangar" ]; then
    echo "❌ packages/core/mcp_hangar not found. Run the migration first."
    exit 1
fi

echo "📁 Verifying structure..."

# Check core package
if [ ! -f "packages/core/pyproject.toml" ]; then
    echo "❌ packages/core/pyproject.toml missing"
    exit 1
fi

if [ ! -d "packages/core/tests" ]; then
    echo "❌ packages/core/tests missing"
    exit 1
fi

# Check operator
if [ ! -f "packages/operator/go.mod" ]; then
    echo "❌ packages/operator/go.mod missing"
    exit 1
fi

# Check helm charts
if [ ! -f "packages/helm-charts/mcp-hangar/Chart.yaml" ]; then
    echo "❌ packages/helm-charts/mcp-hangar/Chart.yaml missing"
    exit 1
fi

if [ ! -f "packages/helm-charts/mcp-hangar-operator/Chart.yaml" ]; then
    echo "❌ packages/helm-charts/mcp-hangar-operator/Chart.yaml missing"
    exit 1
fi

echo "✅ Structure verified!"

# Test core package
echo ""
echo "🐍 Testing Python core..."
cd packages/core

if command -v pip &> /dev/null; then
    echo "Installing dependencies..."
    pip install -e ".[dev]" -q 2>/dev/null || echo "⚠️  pip install failed, continuing..."

    if command -v pytest &> /dev/null; then
        echo "Running tests..."
        pytest -x -q 2>/dev/null && echo "✅ Python tests passed" || echo "⚠️  Some tests failed"
    else
        echo "⚠️  pytest not found, skipping tests"
    fi
else
    echo "⚠️  pip not found, skipping Python tests"
fi

cd ../..

# Test helm charts
echo ""
echo "⎈ Testing Helm charts..."
if command -v helm &> /dev/null; then
    helm lint packages/helm-charts/mcp-hangar && echo "✅ mcp-hangar chart valid" || echo "❌ mcp-hangar chart invalid"
    helm lint packages/helm-charts/mcp-hangar-operator && echo "✅ mcp-hangar-operator chart valid" || echo "❌ mcp-hangar-operator chart invalid"
else
    echo "⚠️  helm not found, skipping chart validation"
fi

# Summary
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Migration verification complete!"
echo ""
echo "New structure:"
echo "  packages/"
echo "    core/           <- Python (mcp_hangar + tests + pyproject.toml)"
echo "    operator/       <- Go operator"
echo "    helm-charts/    <- Helm charts + CRDs"
echo ""
echo "Next steps:"
echo "  1. Test: make all"
echo "  2. Commit: git add -A && git commit -m 'refactor: restructure to packages monorepo'"
echo "  3. Push and verify CI"
echo ""
