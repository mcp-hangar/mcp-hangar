#!/usr/bin/env bash
set -euo pipefail

MODE="solo"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ "$MODE" != "solo" && "$MODE" != "community" ]]; then
  echo "Invalid mode: $MODE. Use 'solo' or 'community'."
  exit 1
fi

NAME_WITH_OWNER=$(gh repo view --json nameWithOwner -q .nameWithOwner)
OWNER="${NAME_WITH_OWNER%/*}"
REPO="${NAME_WITH_OWNER#*/}"
BRANCH="main"

if [[ "$MODE" == "solo" ]]; then
  REQUIRED_REVIEWERS=0
  REQUIRE_CODEOWNER_REVIEWS=false
  ENFORCE_ADMINS=false
else
  REQUIRED_REVIEWERS=1
  REQUIRE_CODEOWNER_REVIEWS=true
  ENFORCE_ADMINS=true
fi

PAYLOAD=$(cat <<EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "pr-validation / required-check",
      "enterprise-boundary",
      "pr-title / validate",
      "changelog / check",
      "branch-name / validate",
      "pr-body / validate"
    ]
  },
  "enforce_admins": $ENFORCE_ADMINS,
  "required_pull_request_reviews": {
    "required_approving_review_count": $REQUIRED_REVIEWERS,
    "require_code_owner_reviews": $REQUIRE_CODEOWNER_REVIEWS
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true
}
EOF
)

if [[ "$DRY_RUN" == true ]]; then
  echo "DRY RUN — would apply to repos/$OWNER/$REPO/branches/$BRANCH/protection:"
  echo "$PAYLOAD" | python3 -m json.tool
  exit 0
fi

echo "$PAYLOAD" | gh api -X PUT "repos/$OWNER/$REPO/branches/$BRANCH/protection" --input -

echo ""
echo "Branch protection applied ($MODE mode). Current state:"
gh api "repos/$OWNER/$REPO/branches/$BRANCH/protection" \
  --jq '{checks: .required_status_checks.contexts, reviews: .required_pull_request_reviews.required_approving_review_count, linear: .required_linear_history.enabled}'
