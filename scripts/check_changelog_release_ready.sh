#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-CHANGELOG.md}"
REPO_URL="https://github.com/mcp-hangar/mcp-hangar"
errors=0

err() { echo "::error file=${FILE},line=${1}::${2}"; errors=$((errors + 1)); }

# Invariant 1: Standard preamble (# Changelog + 4 content lines)
[[ "$(sed -n '1p' "$FILE")" == "# Changelog" ]] || err 1 "First line must be '# Changelog'"
[[ -z "$(sed -n '2p' "$FILE")" ]] || err 2 "Line 2 must be blank"
[[ "$(sed -n '3p' "$FILE")" == "All notable changes to this project will be documented in this file." ]] \
  || err 3 "Line 3 must be the standard Keep-a-Changelog description"
[[ -z "$(sed -n '4p' "$FILE")" ]] || err 4 "Line 4 must be blank"
grep -qP '^The format is based on \[Keep a Changelog\]' <(sed -n '5p' "$FILE") \
  || err 5 "Line 5 must reference Keep a Changelog format"

# Invariant 2: Exactly one [Unreleased] heading
unreleased_count=$(grep -cP '^\#\# \[Unreleased\]' "$FILE")
[[ "$unreleased_count" -eq 1 ]] || err 0 "Expected exactly 1 '## [Unreleased]' heading, found ${unreleased_count}"

# Invariant 3: [Unreleased] subsections are standard
unreleased_line=$(grep -nP '^\#\# \[Unreleased\]' "$FILE" | head -1 | cut -d: -f1)
next_version_line=$(grep -nP '^\#\# \[\d+' "$FILE" | head -1 | cut -d: -f1)
if [[ -n "$next_version_line" ]]; then
  allowed='Added|Changed|Deprecated|Removed|Fixed|Security'
  while IFS=: read -r ln content; do
    if [[ "$content" =~ ^###\  ]] && ! echo "$content" | grep -qP "^### ($allowed)$"; then
      err "$ln" "Invalid subsection in [Unreleased]: '${content}'. Allowed: ${allowed}"
    fi
  done < <(sed -n "$((unreleased_line+1)),$((next_version_line-1))p" "$FILE" | grep -nP '^\#\#\# ' \
    | awk -v off="$unreleased_line" -F: '{printf "%d:%s\n", $1+off, $2}')
fi

# Invariant 4 & 5: Version headings format + descending order
version_pattern='^\#\# \[([0-9]+\.[0-9]+\.[0-9]+(-[a-z]+\.[0-9]+)?)\] - ([0-9]{4}-[0-9]{2}-[0-9]{2})$'
prev_version=""
while IFS=: read -r ln content; do
  if ! echo "$content" | grep -qP "$version_pattern"; then
    err "$ln" "Version heading does not match expected format: '${content}'"
    continue
  fi
  ver=$(echo "$content" | grep -oP '\[\K[0-9]+\.[0-9]+\.[0-9]+(-[a-z]+\.[0-9]+)?' )
  if [[ -n "$prev_version" ]]; then
    if printf '%s\n%s\n' "$ver" "$prev_version" | sort -V | head -1 | grep -qx "$prev_version"; then
      err "$ln" "Version ${ver} is not in descending order (follows ${prev_version})"
    fi
  fi
  prev_version="$ver"
done < <(grep -nP '^\#\# \[[0-9]' "$FILE")

# Invariant 6: Reference link block completeness
mapfile -t headings < <(grep -oP '^\#\# \[\K[^\]]+' "$FILE")
for heading in "${headings[@]}"; do
  if ! grep -qP "^\[${heading//./\\.}\]: https://github.com/" "$FILE"; then
    err 0 "Missing reference link for [${heading}]. Add: [${heading}]: ${REPO_URL}/compare/..."
  fi
done
# Validate link format
while IFS=: read -r ln content; do
  if ! echo "$content" | grep -qP '^\[[^\]]+\]: https://github.com/mcp-hangar/mcp-hangar/(compare|releases/tag)/'; then
    err "$ln" "Reference link has wrong format: '${content}'"
  fi
done < <(grep -nP '^\[' "$FILE" | tail -n +1 | grep -P '^\d+:\[')

# Invariant 7: No trailing whitespace or tab indentation
while IFS=: read -r ln _; do
  err "$ln" "Trailing whitespace detected"
done < <(grep -nP ' +$' "$FILE")
while IFS=: read -r ln _; do
  err "$ln" "Tab indentation detected (use spaces)"
done < <(grep -nP '^\t' "$FILE")

if [[ "$errors" -gt 0 ]]; then
  echo "FAILED: ${errors} issue(s) found in ${FILE}"
  exit 1
fi
echo "OK: ${FILE} is release-please ready"
