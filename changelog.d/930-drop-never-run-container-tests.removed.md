**core:** the `containers` extra is gone. It installed `testcontainers` for a test
tier that never ran: those tests were gated behind `--run-containers` /
`--run-slow`, and no CI job, `Makefile` target or script ever passed either flag,
so every one of them reported `skipped` on every run. `pip install
mcp-hangar[containers]` now fails -- there is nothing left for it to install, and
nothing in the shipped package ever imported it. Tests that need a real runtime
belong in the nightly `tests/live` tiers.
