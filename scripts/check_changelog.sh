#!/usr/bin/env bash
set -euo pipefail

# Inputs from environment
: "${BASE_SHA:?BASE_SHA must be set}"
: "${HEAD_SHA:?HEAD_SHA must be set}"
: "${PR_LABELS:=}"

TRIGGERING_PATTERN='^(src/|enterprise/|pyproject\.toml$|packages/(operator|helm-charts|ui)/)'

changed_files=$(git diff --name-only "$BASE_SHA".."$HEAD_SHA")

has_triggering_file=false
while IFS= read -r file; do
  if echo "$file" | grep -qE "$TRIGGERING_PATTERN"; then
    has_triggering_file=true
    break
  fi
done <<< "$changed_files"

if [ "$has_triggering_file" = false ]; then
  echo "No triggering files changed. CHANGELOG entry not required."
  exit 0
fi

if echo "$PR_LABELS" | grep -q "skip-changelog"; then
  echo "skip-changelog label present. Skipping check."
  exit 0
fi

if ! echo "$changed_files" | grep -qx "CHANGELOG.md"; then
  echo "::error::CHANGELOG.md not modified. Add an entry to \`## [Unreleased]\` in CHANGELOG.md or apply the \`skip-changelog\` label."
  exit 1
fi

unreleased_addition=$(git diff "$BASE_SHA".."$HEAD_SHA" -- CHANGELOG.md \
  | awk '/^@@/{ in_diff=1; next } in_diff && /^\+.*\[Unreleased\]/{ found=1; next } found && /^## \[/{ exit } found && /^\+[^+]/{ print; exit }')

if [ -z "$unreleased_addition" ]; then
  echo "::error::No new line found under \`## [Unreleased]\` in CHANGELOG.md. Add an entry or apply the \`skip-changelog\` label."
  exit 1
fi

echo "CHANGELOG entry found."
exit 0
