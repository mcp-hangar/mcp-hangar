#!/usr/bin/env bash
# Idempotent label sync from .github/labels.yml to GitHub.
# Requires: gh (authenticated), yq v4+
# Usage: bash scripts/sync-labels.sh
set -euo pipefail

MANIFEST="$(cd "$(dirname "$0")/.." && pwd)/.github/labels.yml"

if [[ ! -f "$MANIFEST" ]]; then
  echo "ERROR: manifest not found at $MANIFEST" >&2
  exit 1
fi

if ! command -v yq &>/dev/null; then
  echo "ERROR: yq is required but not found. Install: https://github.com/mikefarah/yq" >&2
  exit 1
fi

if ! command -v gh &>/dev/null; then
  echo "ERROR: gh CLI is required but not found." >&2
  exit 1
fi

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
LABEL_COUNT="$(yq 'length' "$MANIFEST")"

echo "Syncing $LABEL_COUNT labels to $REPO"
echo "---"

created=0
updated=0
unchanged=0

for i in $(seq 0 $((LABEL_COUNT - 1))); do
  name="$(yq -r ".[$i].name" "$MANIFEST")"
  color="$(yq -r ".[$i].color" "$MANIFEST")"
  desc="$(yq -r ".[$i].description" "$MANIFEST")"

  if gh label create "$name" --repo "$REPO" --color "$color" --description "$desc" 2>/dev/null; then
    echo "[+] created  $name"
    created=$((created + 1))
  else
    current="$(gh label list --repo "$REPO" --limit 200 --json name,color,description \
      | yq -r ".[] | select(.name == \"$name\") | .color + \"|\" + .description")"
    cur_color="$(echo "$current" | cut -d'|' -f1)"
    cur_desc="$(echo "$current" | cut -d'|' -f2-)"

    if [[ "$cur_color" == "$color" && "$cur_desc" == "$desc" ]]; then
      echo "[=] unchanged $name"
      unchanged=$((unchanged + 1))
    else
      gh label edit "$name" --repo "$REPO" --color "$color" --description "$desc"
      echo "[~] updated  $name"
      updated=$((updated + 1))
    fi
  fi
done

echo "---"
echo "Done: $created created, $updated updated, $unchanged unchanged"
