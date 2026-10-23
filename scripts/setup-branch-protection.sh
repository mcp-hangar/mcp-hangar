#!/usr/bin/env bash
# Raise main's classic branch protection; never lower it (#1709).
#
# The previous version wrote a fixed list of six required checks, two of which
# no job reports, and solo mode set enforce_admins to false. Run against a
# protected main it removed eight required checks and the admin rule. This one
# starts from the protection main has now and only adds to it:
#
#   - required checks: the current ones plus every --add NAME. Each must be a
#     check that reported on the head of the most recently merged PR, so a
#     typo or a renamed job cannot make every PR wait for a check that never
#     comes. Push-only runs are not used: the PR gates (pr-title, pr-body, ...)
#     do not run on a push to main.
#   - enforce_admins, linear history, conversation resolution, force-push and
#     deletion bans: kept if set, set if --mode community asks for them.
#   - required reviews: the larger of the current count and the mode's.
#
# The main-integrity ruleset is separate and not touched here.
#
# Usage:
#   scripts/setup-branch-protection.sh [--mode solo|community] [--add CHECK]... [--dry-run]
set -euo pipefail

MODE="solo"
DRY_RUN=false
ADD=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --add) ADD+=("$2"); shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ "$MODE" != "solo" && "$MODE" != "community" ]]; then
  echo "Invalid mode: $MODE. Use 'solo' or 'community'." >&2
  exit 1
fi

REPO_SLUG=$(gh repo view --json nameWithOwner -q .nameWithOwner)
BRANCH="main"

CURRENT=$(gh api "repos/$REPO_SLUG/branches/$BRANCH/protection" 2>/dev/null || echo '{}')
HEAD_SHA=$(gh pr list -R "$REPO_SLUG" --state merged --base "$BRANCH" --limit 1 --json headRefOid --jq '.[0].headRefOid')
REPORTED=$(gh api --paginate "repos/$REPO_SLUG/commits/$HEAD_SHA/check-runs?per_page=100" --jq '.check_runs[].name' | sort -u)

PAYLOAD=$(MODE="$MODE" CURRENT="$CURRENT" REPORTED="$REPORTED" python3 - "${ADD[@]+"${ADD[@]}"}" <<'PY'
import json, os, sys

mode = os.environ["MODE"]
current = json.loads(os.environ["CURRENT"])
reported = set(filter(None, os.environ["REPORTED"].splitlines()))
add = sys.argv[1:]

checks = current.get("required_status_checks") or {}
contexts = list(checks.get("contexts") or [])
for name in add:
    if name not in contexts:
        contexts.append(name)

missing = [c for c in contexts if c not in reported]
if missing:
    sys.exit(
        "refusing: these required checks did not report on the last merged PR, so every PR would wait "
        "for them forever: " + ", ".join(missing)
    )

def enabled(key):
    return bool((current.get(key) or {}).get("enabled"))

community = mode == "community"
reviews = current.get("required_pull_request_reviews") or {}
payload = {
    "required_status_checks": {"strict": bool(checks.get("strict")) or community, "contexts": sorted(contexts)},
    "enforce_admins": enabled("enforce_admins") or community,
    "required_pull_request_reviews": {
        "required_approving_review_count": max(int(reviews.get("required_approving_review_count", 0)), 1 if community else 0),
        "require_code_owner_reviews": bool(reviews.get("require_code_owner_reviews")) or community,
        "dismiss_stale_reviews": bool(reviews.get("dismiss_stale_reviews")),
        "require_last_push_approval": bool(reviews.get("require_last_push_approval")),
    },
    "restrictions": None,
    "required_linear_history": enabled("required_linear_history") or community,
    "allow_force_pushes": enabled("allow_force_pushes") and not community,
    "allow_deletions": enabled("allow_deletions") and not community,
    "required_conversation_resolution": enabled("required_conversation_resolution") or community,
}
print(json.dumps(payload, indent=2))
PY
)

if [[ "$DRY_RUN" == true ]]; then
  echo "DRY RUN -- would apply to repos/$REPO_SLUG/branches/$BRANCH/protection:"
  echo "$PAYLOAD"
  exit 0
fi

echo "$PAYLOAD" | gh api -X PUT "repos/$REPO_SLUG/branches/$BRANCH/protection" --input - >/dev/null

echo "Branch protection raised ($MODE mode). Current state:"
gh api "repos/$REPO_SLUG/branches/$BRANCH/protection" \
  --jq '{checks: .required_status_checks.contexts, admins: .enforce_admins.enabled, reviews: .required_pull_request_reviews.required_approving_review_count, linear: .required_linear_history.enabled}'
