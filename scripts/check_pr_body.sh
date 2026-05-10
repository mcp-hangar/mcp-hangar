#!/usr/bin/env bash
set -euo pipefail

: "${PR_BODY:?PR_BODY must be set}"
: "${PR_LABELS:=}"

if echo "$PR_LABELS" | grep -q "trivial"; then
  echo "trivial label present. Skipping body check."
  exit 0
fi

stripped=$(echo "$PR_BODY" | tr -d '\r' | sed 's/<!--.*-->//g' | sed '/<!--/,/-->/d')

required_sections=("## Why" "## What" "## How tested" "## Risk and rollback" "## CHANGELOG note")
failed=false

for section in "${required_sections[@]}"; do
  if ! echo "$stripped" | grep -qF "$section"; then
    echo "::error::Missing section: \`$section\`. Add it with content, or apply the \`trivial\` label."
    failed=true
    continue
  fi

  content=$(echo "$stripped" | sed -n "/^${section}$/,/^## /{ /^## /d; p; }" | sed '/^[[:space:]]*$/d')
  if [ -z "$content" ]; then
    echo "::error::Section \`$section\` is empty. Add content, or apply the \`trivial\` label."
    failed=true
  fi
done

if [ "$failed" = true ]; then
  exit 1
fi

echo "All required sections present with content."
exit 0
