**deps:** the `dev` extra now caps pytest below 9.2. pytest 9.2 removed
`Item._nodeid`, and the latest pytest-benchmark (5.3.0) still uses it, so every
test using the `benchmark` fixture failed at setup. The v2.34.6 release job, which
installs `.[dev]` without the lockfile, failed for this reason and never published.
