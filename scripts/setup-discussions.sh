#!/usr/bin/env bash
# Curate GitHub Discussions categories: keep only RFC and Q&A.
# Enabling Discussions itself is a UI step (Settings > Features > Discussions).
# This script handles category curation after Discussions is enabled.
set -euo pipefail

OWNER="${OWNER:-mcp-hangar}"
REPO="${REPO:-mcp-hangar}"

has_discussions=$(gh api "repos/$OWNER/$REPO" --jq '.has_discussions')
if [ "$has_discussions" != "true" ]; then
  echo "Discussions not enabled. Enable via: Settings > Features > Discussions"
  exit 1
fi

query='query($owner:String!,$name:String!){repository(owner:$owner,name:$name){discussionCategories(first:20){nodes{id name}}}}'
categories=$(gh api graphql -f query="$query" -f owner="$OWNER" -f name="$REPO" --jq '.data.repository.discussionCategories.nodes')

UNWANTED=("General" "Ideas" "Polls" "Show and tell" "Announcements")
for name in "${UNWANTED[@]}"; do
  id=$(echo "$categories" | jq -r --arg n "$name" '.[] | select(.name==$n) | .id // empty')
  if [ -n "$id" ]; then
    mutation='mutation($id:ID!){deleteDiscussionCategory(input:{id:$id}){clientMutationId}}'
    if gh api graphql -f query="$mutation" -f id="$id" >/dev/null 2>&1; then
      echo "Deleted category: $name"
    else
      echo "Manual UI step required: delete category '$name'"
    fi
  fi
done

existing=$(gh api graphql -f query="$query" -f owner="$OWNER" -f name="$REPO" --jq '.data.repository.discussionCategories.nodes[].name')
repo_id=$(gh api "repos/$OWNER/$REPO" --jq '.node_id')
create='mutation($repoId:ID!,$name:String!,$emoji:String!,$desc:String!,$format:DiscussionCategoryFormat!){createDiscussionCategory(input:{repositoryId:$repoId,name:$name,emoji:$emoji,description:$desc,format:$format}){discussionCategory{name}}}'

if ! echo "$existing" | grep -qx "RFC"; then
  gh api graphql -f query="$create" -f repoId="$repo_id" -f name="RFC" -f emoji=":page_facing_up:" -f desc="Architecture proposals and design discussions" -f format="OPEN_DISCUSSION" >/dev/null
  echo "Created category: RFC"
fi

if ! echo "$existing" | grep -qx "Q&A"; then
  gh api graphql -f query="$create" -f repoId="$repo_id" -f name="Q&A" -f emoji=":question:" -f desc="Questions and answers" -f format="QUESTION_ANSWER" >/dev/null
  echo "Created category: Q&A"
fi

echo ""
echo "Final categories:"
gh api graphql -f query="$query" -f owner="$OWNER" -f name="$REPO" --jq '.data.repository.discussionCategories.nodes[].name'
