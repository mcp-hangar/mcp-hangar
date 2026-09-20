**ci:** ruff sorts imports now. `I` joins `select` in `pyproject.toml`, so
`ruff check src/mcp_hangar tests` fails on an unsorted import block and
`--fix` sorts it, in CI and in the pre-commit hook alike -- both run the same
pinned ruff. The hook and the CI step had been named for an isort that was
never configured; they are named for what they do now.

Turning the rule on was a one-time mechanical reformat of 527 files, landed as
its own commit and listed in `.git-blame-ignore-revs`. Run
`git config blame.ignoreRevsFile .git-blame-ignore-revs` once in your checkout
and `git blame` will skip it. `.github/.isort.cfg` is deleted: it configured a
`registry` package against a 88-character line, neither of which is this repo,
and no workflow read it.

No runtime change -- import order is the only thing that moved.
