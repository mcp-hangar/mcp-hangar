# Copilot Commit Message Instructions

When generating a commit message for this repository, follow Conventional
Commits with this project's exact type and scope vocabulary.

## Format

`<type>(<scope>): <subject>`

- `type` (required): one of `feat`, `fix`, `perf`, `refactor`, `docs`,
  `test`, `build`, `ci`, `chore`, `revert`, `security`.
- `scope` (required): one of `core`, `enterprise`, `cli`, `operator`,
  `helm`, `ui`, `observability`, `security`, `docs`, `deps`, `release`,
  `infra`, `tests`, `repo`. Empty scope is rejected.
- `subject`: imperative mood, total header length <=72 characters.
- `!` after scope (e.g., `feat(core)!: ...`) marks a breaking change.

## Subject rules

- Lowercase start preferred; sentence-case start acceptable.
- All-uppercase subjects forbidden (e.g., `FIX BUG NOW`).
- Mid-string uppercase characters are allowed: file names like
  `CHANGELOG.md`, `README.md`; acronyms like `MCP`, `JWT`, `OAuth`, `CRLF`.
- No trailing period.
- No emoji.

## Examples (accepted)

- `feat(core): add capability validation cache`
- `fix(ci): handle CRLF in PR body validator`
- `docs(repo): update CODEOWNERS for security paths`
- `chore(deps): bump pytest from 9.0.2 to 9.0.3`
- `refactor(observability)!: rename OTLP exporter interface`

## Examples (rejected)

- `Add capability cache` -- missing type and scope
- `feat: add cache` -- missing required scope (scope-empty: never)
- `feat(unknown): add cache` -- scope not in allow-list
- `feat(core): ADD CACHE` -- all-uppercase subject
- `feat(core): add cache.` -- trailing period
- `feat(core): add cache with emoji` -- emoji forbidden
- A 73+ character header -- exceeds `header-max-length`

## Branch naming (related)

Branch pattern: `<type>/<scope>-<slug>` where type and scope come from the
lists above. Tool prefixes also valid: `dependabot/*`, `copilot/<task>-<slug>`,
`release-please--*`.

## Schema source of truth

Type and scope vocabulary, plus all rules, live in `.commitlintrc.yml`.
The CI workflow `commitlint / lint` enforces these on every PR commit. If
`.commitlintrc.yml` changes, this instructions file must be updated to match.

## References

- `.commitlintrc.yml` -- canonical config
- `docs/development/GIT_FLOW.md` -- Conventional Commits scope reference
- `AGENTS.md` -- Git Workflow for Agents, including forbidden paths
