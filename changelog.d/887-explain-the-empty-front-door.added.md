**core:** a `front_door` gateway that serves no tools now says why. Three very
different situations used to produce the same 200, the same `{"tools": []}` and
nothing in the log: the caller carried no tenant identity (a fail-closed deny),
the replica had discovered nothing yet (a wrong answer that a restart produces
on its own), or policy removed every tool (the one case where the empty list is
true).

An operator watching a front door that had just been rolled saw healthy pods, a
successful response, and tenants reporting that everything had vanished.

Two new signals:

- a log line naming the cause -- WARNING for the two faults, INFO for the
  correct answer, throttled to once a minute per cause so a standing condition
  cannot bury its own first occurrence;
- `mcp_hangar_empty_projection_total{reason=...}`, with reasons `no_identity`,
  `nothing_discovered` and `filtered`. Not labelled by tenant, deliberately: a
  public front door has unbounded tenant cardinality, and the log line carries
  the tenant for the follow-up. The counter is not throttled, so the rate stays
  truthful.

The missing-identity deny in `front_door` mode also logs at WARNING now,
naming that branch specifically rather than leaving a policy-shaped symptom
behind a wiring problem.
