# AGENTS.md -- ADR Governance

This file governs all files inside `docs/adr/`. It is read by AI coding
agents and human contributors before creating, modifying, or referencing
Architecture Decision Records (ADRs).

For repo-wide conventions (build commands, source layout, testing, coding
style), see the root [`AGENTS.md`](https://github.com/mcp-hangar/mcp-hangar/blob/main/AGENTS.md).
For contribution workflow (PRs, licensing), see
[`CONTRIBUTING.md`](https://github.com/mcp-hangar/mcp-hangar/blob/main/CONTRIBUTING.md).

---

## 1. Purpose and audience

The `docs/adr/` directory contains every Architecture Decision Record for
MCP Hangar. Each ADR captures a single architectural decision: the context,
the decision itself, and the consequences. ADRs are the project's long-term
memory for decisions that constrain future work.

**Primary readers:** AI coding agents (GitHub Copilot, Claude Code, Cursor,
etc.) and human contributors. Both audiences must be able to write a
compliant ADR after reading this file and one example (e.g.,
[ADR-001](ADR-001-cqrs.md)).

---

## 2. When to write an ADR

Write an ADR when **all** of the following are true:

- The decision will likely be re-litigated in 6-12 months.
- Reversing the decision costs materially more than re-deciding it.
- The decision cuts across packages, services, or trust boundaries.
- The decision constrains future contributors who were not in the room.

### When NOT to write an ADR

| Category | Example | Why not |
|----------|---------|---------|
| Validation steps or spikes | "Run Tetragon on Talos+k3s this week" | Operational, not architectural. Track in issues. |
| Library swaps that are easily reversible | "Switch from `httpx` to `aiohttp`" | Low reversal cost. |
| Code style decisions | "Use 120-char lines" | Covered by linter config (`.markdownlint.json`, `ruff`). |
| Roadmap-level planning | "Ship v2.0 in Q3 2027" | Belongs in `ROADMAP.md` (strategy) or the issue tracker (delivery). |
| Customer commitments | "Onboard XTB by Q2" | Business document, not architecture. |
| Feature designs implementing an existing ADR | "Add KubeArmor TracingPolicy translator" | Implementation of ADR-006, not a new decision. |
| Open questions awaiting resolution | "Which LSM hook order for KubeArmor + Tetragon?" | Track in issues until decided, then write ADR. |

---

## 3. File naming and numbering

| Rule | Detail |
|------|--------|
| Pattern | `ADR-NNN-kebab-name.md` (three-digit, zero-padded) |
| Sequence | Monotonic across the project. Never reuse a number. Never renumber. |
| Slug | Names the decision, not just the topic area. Good: `ADR-001-cqrs.md`, `ADR-007-langfuse-integration.md`. Bad: `ADR-008-security.md`. |
| Timestamps | None. No date prefixes. |
| Folders | Flat. No sub-directories inside `docs/adr/`. |

To determine the next number, list existing files:

```bash
ls docs/adr/ADR-*.md | tail -1
```

---

## 4. Title rules

Format: `# ADR-NNN: <title>`

The title names the **decision**, not the topic area. Both short and
descriptive titles are acceptable:

- Short: `ADR-001: Command Query Responsibility Segregation (CQRS)`
- Descriptive: `ADR-005: SEP-1763 Interceptor Framework Compliance`

The title appears on one line. No subtitle, no secondary heading.

---

## 5. Required header block

Immediately after the title, three lines, each on its own line:

```markdown
**Status:** <Status>
**Date:** YYYY-MM-DD
**Authors:** MCP Hangar Team
```

Rules:

- **Status** must be one of the five values defined in section 8.
- **Date** is the date the ADR was first written (not last edited).
- **Authors** is always `MCP Hangar Team`. Never an individual name.
- No other header fields. No `Scope:`, `Supersedes:`, `Owner:`, or
  similar. Supersession is handled in the Status line (see section 8).

A blank line separates the header block from the first body section.

---

## 6. Required body sections

Three sections, in this exact order. All are mandatory.

### `## Context`

Problem statement and forces. May enumerate considered alternatives in
prose if the discussion is short. For longer alternative analysis, use the
optional `## Alternatives Considered` section (see section 7).

### `## Decision`

The decision in declarative form. Sub-sections (`###`) are encouraged for
technical detail:

- Design choices in table form: see [ADR-004](ADR-004-sep-1766-digest-pinning.md).
- Component-by-component breakdown: see [ADR-001](ADR-001-cqrs.md).
- Alignment maps and gap analysis: see [ADR-005](ADR-005-sep-1763-interceptor-compliance.md).

### `## Consequences`

Must contain both `### Positive` and `### Negative` sub-sections.
May contain `### Neutral` for trade-offs that are neither strictly positive
nor negative (see [ADR-007](ADR-007-langfuse-integration.md) for the
pattern).

---

## 7. Optional body sections

These sections are permitted but not required. They appear after
`## Consequences`, in any order.

### `## Alternatives Considered`

Used when alternatives analysis is too long for the Context section.
Format as a numbered list with explicit rejection or deferral status per
option. See [ADR-007](ADR-007-langfuse-integration.md) for the canonical
pattern:

```markdown
### 1. Alternative Name

- **Rejected**: Reason for rejection.

### 2. Another Alternative

- **Deferred**: Reason for deferral.
```

### `## References`

Links to specs, prior art, related ADRs, or implementation files. Used by
[ADR-004](ADR-004-sep-1766-digest-pinning.md),
[ADR-005](ADR-005-sep-1763-interceptor-compliance.md), and
[ADR-007](ADR-007-langfuse-integration.md).

### Decision sub-sections

Additional `###` sub-sections within `## Decision` or `## Consequences`
are encouraged when they fit:

| Sub-section | Where used | Purpose |
|-------------|-----------|---------|
| `### Risks and Mitigations` | [ADR-004](ADR-004-sep-1766-digest-pinning.md) Consequences | Table: Risk / Likelihood / Impact / Mitigation |
| `### Upstream Tracking` | [ADR-005](ADR-005-sep-1763-interceptor-compliance.md) Consequences | Checklist for decisions tied to evolving external specs |
| `### Implementation Details` | [ADR-001](ADR-001-cqrs.md) Decision | File paths and component details |

---

## 8. Status taxonomy

Five values. No others.

| Status | Definition | Permitted transitions |
|--------|------------|----------------------|
| Proposed | Written, awaiting decision and review. | Proposed -> Accepted, Proposed -> Rejected |
| Accepted | Committed; work has started or shipped. | Accepted -> Superseded, Accepted -> Deprecated |
| Superseded by ADR-NNN | Replaced by a later ADR; kept for history. | None (terminal) |
| Deprecated | No longer recommended; decision reversed without a direct successor. | None (terminal) |
| Rejected | Explicitly rejected after review; kept to prevent re-litigation. | None (terminal) |

**Forbidden transitions:**

- Rejected -> anything. Write a new ADR instead.
- Superseded -> anything. Terminal state.
- Deprecated -> anything. Terminal state.

When superseding, the Status line includes the successor:

```markdown
**Status:** Superseded by [ADR-012](ADR-012-kubearmor-primary.md)
```

---

## 9. Immutability rule

Once an ADR reaches **Accepted** status, its body is immutable. Permitted
edits to an Accepted ADR:

| Edit type | Allowed |
|-----------|---------|
| Status change (Accepted -> Superseded / Deprecated) | Yes |
| Adding `Superseded by [ADR-NNN](ADR-NNN-name.md)` to Status line | Yes |
| Typo fixes that do not change meaning | Yes |
| Broken-link fixes | Yes |
| Changing the decision, rationale, or consequences | No -- write a new ADR |

### Worked example

> The team later decides to replace Tetragon with KubeArmor as the primary
> runtime enforcement backend. Do **not** edit ADR-006. Instead:
>
> 1. Write `ADR-012-kubearmor-primary.md` with the new decision.
> 2. In ADR-012, reference ADR-006 in Context or References.
> 3. Update ADR-006 Status line to
>    `**Status:** Superseded by [ADR-012](ADR-012-kubearmor-primary.md)`.
> 4. Update `docs/adr/README.md` index in the same PR.

---

## 10. Cross-references

### In prose

Reference by number plus short title:

> As established in ADR-001 (CQRS), the CommandBus middleware pipeline...

### For navigation

Relative Markdown links:

```markdown
[ADR-001](ADR-001-cqrs.md)
```

### Companion ADRs

ADRs that cover related decisions reference each other in their References
sections. Example: ADR-004 (SEP-1766 Digest Pinning) and ADR-005
(SEP-1763 Interceptors) are companions covering two related MCP SEPs.

### Supersession

Supersession is **bidirectional**:

- **New ADR:** References the old ADR in Status line or References section.
- **Old ADR:** Status line updated to `Superseded by [ADR-NNN](...)`.

Both updates happen in the same pull request.

---

## 11. Forbidden in ADR bodies

| Forbidden | Rationale |
|-----------|-----------|
| `## TL;DR` sections | The Decision section serves this role. |
| Open questions | Track in the issue tracker, not in ADRs. |
| Validation steps or "this week" task lists | Operational content, not architectural. |
| Roadmap content or "next sections" planning | Belongs in `ROADMAP.md` or issue tracker. |
| Individual author credits | Always `MCP Hangar Team`. |
| Multiple unrelated decisions | One decision per ADR. Split if needed. |
| Extra header fields (`Scope:`, `Supersedes: none`, `Owner:`) | Use the three-line header only (section 5). |
| `## Problem` as a section heading | Use `## Context` (section 6). |

---

## 12. README.md maintenance rule

`docs/adr/README.md` is the index and glossary for ADRs. It must be
updated in the **same pull request** as any of the following changes:

- New ADR added.
- ADR status changed (including supersession).
- ADR renamed or removed.

The README structure is maintained by a separate task. See
[README.md](README.md) for the current index and glossary.

---

## 13. Review cadence

**Quarterly** (30 minutes):

1. Read titles and statuses of all ADRs.
2. Identify silent supersession: decisions that have been reversed in
   practice but whose ADR still says Accepted.
3. Write new ADRs or update statuses as needed.
4. Verify README.md index matches the directory listing.

---

## 14. Pre-task checklist for AI agents

Before creating or modifying any ADR, confirm:

- [ ] Read this file (`docs/adr/AGENTS.md`) in full.
- [ ] List existing ADRs to determine the next available number:
      `ls docs/adr/ADR-*.md | tail -1`
- [ ] Read at least two existing Accepted ADRs as style reference
      (e.g., [ADR-001](ADR-001-cqrs.md) and
      [ADR-007](ADR-007-langfuse-integration.md)).
- [ ] Confirm the decision warrants an ADR (section 2 criteria met).
- [ ] Confirm no existing ADR already covers this decision
      (`grep -l "<keyword>" docs/adr/ADR-*.md`).
- [ ] Read root [`AGENTS.md`](https://github.com/mcp-hangar/mcp-hangar/blob/main/AGENTS.md) for repo-wide conventions
      if this is your first task in this repository.

---

## 15. Post-task checklist for AI agents

After creating or modifying any ADR, confirm:

- [ ] `docs/adr/README.md` updated in the same PR (new entry, status
      change, or supersession reflected).
- [ ] Markdownlint passes:
      `npx markdownlint-cli docs/adr/<file>.md` (config:
      `.markdownlint.json`).
- [ ] All internal cross-references resolve (relative links to other ADRs
      point to existing files).
- [ ] If superseding: bidirectional references in place (old ADR Status
      updated, new ADR references old).
- [ ] If MkDocs is configured for this ADR:
      `uv run mkdocs build --strict` produces no new warnings.

---

## 16. Validation rules

Mechanically checkable. Use these for automated or manual review.

| Rule | Check |
|------|-------|
| Filename | Matches `ADR-NNN-kebab-name.md` (three-digit, lowercase kebab) |
| Title line | `# ADR-NNN: <title>` on line 1 |
| Line 2 | Blank line |
| Line 3 | `**Status:** <value>` where value is one of: Proposed, Accepted, Superseded by ADR-NNN, Deprecated, Rejected |
| Line 4 | `**Date:** YYYY-MM-DD` |
| Line 5 | `**Authors:** MCP Hangar Team` |
| Line 6 | Blank line |
| Required sections | `## Context`, `## Decision`, `## Consequences` present, in that order |
| Consequences sub-sections | `### Positive` and `### Negative` both present under `## Consequences` |
| Status value | One of the five defined values (section 8) |
| Date format | ISO 8601: `YYYY-MM-DD` |
| No forbidden sections | No `## TL;DR`, `## Problem`, `## Open questions`, `## Validation steps`, `## Phasing`, `## Next sections` |
| No extra header fields | No `**Scope:**`, `**Supersedes:**`, `**Owner:**` lines in header block |
| Internal links resolve | All `[ADR-NNN](ADR-NNN-*.md)` links point to existing files |
| README.md in sync | Every `ADR-*.md` file (excluding review/supplementary files) has a corresponding entry in `docs/adr/README.md` |
