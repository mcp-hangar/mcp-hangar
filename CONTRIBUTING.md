# Contributing

See [docs/development/CONTRIBUTING.md](https://github.com/mcp-hangar/docs/blob/main/development/CONTRIBUTING.md) for the full contributing guide.

## Repository Structure

The Python core lives at the repository root. Related components — the
Kubernetes operator, the Helm charts, and the documentation site — live in
separate repositories under the [mcp-hangar org](https://github.com/mcp-hangar).
(The agent and the Terraform provider are archived; the SaaS tier they served
was retired.)

| Package | Language | Location |
|---------|----------|----------|
| Core | Python | `src/mcp_hangar/` (repo root) |

## Quick Start

See [Git Flow](https://github.com/mcp-hangar/docs/blob/main/development/GIT_FLOW.md) for branching conventions and commit scopes.

```bash
git clone https://github.com/mcp-hangar/mcp-hangar.git
cd mcp-hangar

# Keep the bulk reformats out of `git blame` (see .git-blame-ignore-revs)
git config blame.ignoreRevsFile .git-blame-ignore-revs

# Python core development (from the repo root)
pip install -e ".[dev]"
pytest

# Or use the root Makefile
make setup
make test
```

## Changelog

Do not edit `CHANGELOG.md` -- it is generated. Every non-trivial PR adds one
new file instead:

```bash
printf '**core:** what changed, and what a reader has to do about it\n' \
  > changelog.d/<issue-or-pr>-<slug>.fixed.md
```

The suffix is one of `added`, `changed`, `deprecated`, `removed`, `fixed`,
`security`, and becomes the section heading; the bullet and the PR link are
added at release time. One file per PR is what keeps concurrent PRs from
conflicting on the same lines. Details in
[changelog.d/README.md](changelog.d/README.md); `make changelog-check`
validates what you wrote.

## Upgrade notes

A change a reader has to act on -- one that breaks callers, removes public API,
or changes what an existing configuration does -- also gets an upgrade note. Do
not edit `UPGRADE.md`: add one new file whose first line is the note's heading
at level three.

```bash
printf '### what is now true\n\nThe old form, the new form, and what to change.\n' \
  > upgrade.d/<issue-or-pr>-<slug>.md
```

The release folds the fragments into `## Upgrade to <version>` and deletes
them. Details in [upgrade.d/README.md](upgrade.d/README.md).

## Licensing

MCP Hangar is licensed under the [MIT License](LICENSE).

## Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.
