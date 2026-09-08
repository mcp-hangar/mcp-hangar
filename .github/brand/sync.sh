#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
output="$root/.github/brand"
temp="$(mktemp -d)"
trap 'rm -rf "$temp"' EXIT

gh repo clone mcp-hangar/brand "$temp/brand" -- --branch main --depth 1
source_sha="$(git -C "$temp/brand" rev-parse HEAD)"

cp "$temp/brand/social/github-header-1280x640.png" "$output/github-header-1280x640.png"
cp "$temp/brand/marks/gate-brand.svg" "$output/gate-brand.svg"
cp "$temp/brand/lockups/primary-dark.svg" "$output/primary-dark.svg"
printf 'mcp-hangar/brand@%s\n' "$source_sha" > "$output/SOURCE"
printf 'Synced mcp-hangar/brand@%s\n' "$source_sha"
