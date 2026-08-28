# Fuzzing the policy evaluator

The gateway's job is to answer, for every tool call, "allowed, denied, or held".
Fuzzing here looks for an input where it does not answer at all -- or answers
one way through one path and the other way through another. Not "the verdict
should be X"; the verdict is the policy's business.

## Layout

| File | What it is |
|---|---|
| `invariants.py` | the properties themselves. Imports no atheris. |
| `fuzz_policy_evaluate.py` | `evaluate()` returns a `Decision`, in bounded time |
| `fuzz_policy_parse.py` | a policy parser rejects or accepts, never crashes |
| `fuzz_access_precedence.py` | deny wins, and keeps winning after a merge |
| `corpus/parse/` | seed inputs for the parse target |

The split is not decoration. `atheris` publishes manylinux x86_64 wheels for
CPython **3.12-3.14 only**, so it installs on neither this project's 3.11
baseline (`.python-version`) nor any macOS machine. If the invariants lived
inside the harnesses they would be checkable in exactly one environment.
Instead they live in `invariants.py`, and
`tests/unit/test_the_fuzz_invariants_hold.py` replays the corpus and every
known finding through them on every PR, on every platform, in well under a
second. The fuzzer's job is to search for new inputs; the invariants' job is to
say what "broken" means, and that job is not platform-specific.

## Running it

Needs Linux x86_64 and CPython 3.12+:

```bash
uv sync --extra fuzz
python fuzz/fuzz_policy_evaluate.py -runs=1000000
python fuzz/fuzz_policy_parse.py -runs=1000000 fuzz/corpus/parse
python fuzz/fuzz_access_precedence.py -runs=1000000
```

`-runs=0 <file>` replays one input. libFuzzer writes a failing input to
`crash-<sha1>` in the working directory, so a finding reproduces with:

```bash
python fuzz/fuzz_policy_evaluate.py -runs=0 crash-<sha1>
```

`fuzz_policy_evaluate` and `fuzz_access_precedence` ship no seed corpus: their
inputs are raw bytes consumed by `FuzzedDataProvider`, so a hand-written seed
would be an opaque blob rather than something a reviewer can read. libFuzzer
builds and keeps that corpus itself (and ClusterFuzzLite persists it between
runs -- #1104). What a human should be able to read is the regression, and that
lives in the replay test as a named case.

## Turning a finding into a regression

A crash is not fixed by adding a corpus entry. Add the case to
`tests/unit/test_the_fuzz_invariants_hold.py` with a name that says what broke,
so it is checked on every platform forever, then fix the defect. Both bugs this
harness has already produced were handled that way:

- **#1102** -- `RecursionError` escaped `evaluate()` instead of a verdict.
  Deep nesting the JSON encoder cannot walk. In Enforce the call died
  unattributed; in Audit it was blocked outright, which is the opposite of what
  ADR-013 promises Audit does.
- **#1106** -- `merge()` dropped a `deny_list`, so a tool denied at the server
  scope came back allowed once a group scope repeated the allow list. A policy
  bypass, found by the precedence invariant before the fuzzer ever ran.

## What the time budget is for

`scan_arguments` runs agent-controlled payload through ten regex groups
(`SECRET_PATTERN_GROUPS`). That is where catastrophic backtracking would show
up, and a hang is not a crash -- libFuzzer would sit there rather than report.
So `check_evaluate` fails an input that takes longer than
`EVALUATE_BUDGET_SECONDS`. The threshold is coarse on purpose: the distinction
that matters is microseconds versus seconds.
