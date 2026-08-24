**core:** four metrics were defined, incremented on the live path, and absent
from every `/metrics` scrape because nothing added them to the registration
list: the three approval-gate counters (`mcp_hangar_approval_requests_total`,
`_deliveries_total`, `_decisions_total`, dead since 2.10.0 — the three PromQL
queries in the observability guide could never return a row) and
`mcp_hangar_egress_policy_violations_observed_total`, the Audit-mode signal
ADR-013 calls the safe adoption path for an egress policy. All four are
registered, and a test now walks the metrics module so the next one cannot be
forgotten.
