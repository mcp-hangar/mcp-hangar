#!/usr/bin/env bash
set -euo pipefail

OWNER="${OWNER:-mcp-hangar}"
PROJECT_TITLE="${PROJECT_TITLE:-MCP Hangar}"

project_number=$(
  gh project list --owner "$OWNER" --format json \
    | python3 -c "
import json, sys
for p in json.load(sys.stdin).get('projects', []):
    if p.get('title') == '$PROJECT_TITLE':
        print(p['number'])
        sys.exit(0)
" 2>/dev/null || true
)

if [ -z "$project_number" ]; then
  echo "Creating project '$PROJECT_TITLE' under $OWNER..."
  project_number=$(
    gh project create --owner "$OWNER" --title "$PROJECT_TITLE" --format json \
      | python3 -c "import json,sys; print(json.load(sys.stdin)['number'])"
  )
  echo "Created project #$project_number."
else
  echo "Project '$PROJECT_TITLE' already exists (#$project_number)."
fi

existing_fields=$(
  gh project field-list "$project_number" --owner "$OWNER" --format json
)

field_exists() {
  echo "$existing_fields" \
    | python3 -c "
import json, sys
for f in json.load(sys.stdin).get('fields', []):
    if f.get('name') == sys.argv[1]:
        sys.exit(0)
sys.exit(1)
" "$1"
}

if ! field_exists "Priority"; then
  echo "Creating field: Priority"
  gh project field-create "$project_number" --owner "$OWNER" \
    --name "Priority" --data-type "SINGLE_SELECT" \
    --single-select-options "p0-critical,p1-high,p2-normal,p3-low"
else
  echo "Field 'Priority' already exists."
fi

if ! field_exists "Scope"; then
  echo "Creating field: Scope"
  gh project field-create "$project_number" --owner "$OWNER" \
    --name "Scope" --data-type "SINGLE_SELECT" \
    --single-select-options "core,enterprise,cli,operator,helm,ui,observability,security,docs,deps,release,infra,tests,repo"
else
  echo "Field 'Scope' already exists."
fi

if ! field_exists "Target Release"; then
  echo "Creating field: Target Release"
  gh project field-create "$project_number" --owner "$OWNER" \
    --name "Target Release" --data-type "TEXT"
else
  echo "Field 'Target Release' already exists."
fi

if ! field_exists "Estimate (LOC)"; then
  echo "Creating field: Estimate (LOC)"
  gh project field-create "$project_number" --owner "$OWNER" \
    --name "Estimate (LOC)" --data-type "NUMBER"
else
  echo "Field 'Estimate (LOC)' already exists."
fi

echo ""
echo "NOTE: Status field options (Triage, Backlog, Ready, In Progress, In Review,"
echo "Blocked, Done) must be configured manually in the Project UI."
echo "The gh CLI does not support modifying built-in field options."
echo "See docs/development/PROJECT_BOARD.md for instructions."

echo ""
echo "Project #$project_number setup complete."
echo "URL: https://github.com/orgs/$OWNER/projects/$project_number"
echo ""
echo "Final state:"
gh project field-list "$project_number" --owner "$OWNER"
