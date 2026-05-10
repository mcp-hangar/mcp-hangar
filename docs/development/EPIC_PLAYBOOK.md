# Epic Playbook

> **Status:** Skeleton. Expand after the first real epic completes. Canonical flow lives in [GIT_FLOW.md Flow 3: Epic and ADR](GIT_FLOW.md#flow-3-epic-and-adr).

## When an epic is warranted

An epic groups related work that spans multiple PRs and requires an ADR.
Single-PR features do not need an epic.
Example: replacing the event store backend.

<!-- TODO: expand after first epic completes -->

## Decomposition into child issues

Break the epic into 5-8 child issues, each at most 400 LOC, each with its own acceptance criteria.
Children should be independently mergeable.
Example: 5-8 child issues, each <=400 LOC, each with its own AC.

<!-- TODO: expand after first epic completes -->

## ADR coupling

The ADR backing the epic must be merged with `Status: Accepted` before the first child issue opens.
Implementation does not start until the architectural decision is ratified.
Example: ADR-NNN merged with `Status: Accepted` before child #1 opens.

<!-- TODO: expand after first epic completes -->

## Tracking on the GH Project board

The epic issue carries a `Target Release` field. Children inherit the field value.
Use the project board to visualize progress across children.
Example: epic issue carries `Target Release: 1.2.0`; children inherit the field.

<!-- TODO: expand after first epic completes -->

## Closing the epic

The epic auto-closes when the last child PR merges via `Closes #<epic>` in the PR body.
Verify all acceptance criteria on the epic issue before final close.
Example: epic auto-closes when last child PR merges via `Closes #<epic>`.

<!-- TODO: expand after first epic completes -->
