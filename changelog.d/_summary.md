Hangar's side of SEP-2243. A tool whose `x-mcp-header` annotations are invalid
is no longer projected -- a conforming client drops it on arrival, so
advertising it handed out a tool nobody could call -- and a new
`header_exposure` block governs which parameters an upstream may oblige a client
to send as an HTTP header, the enforcement point behind a SHOULD NOT the spec
leaves unbacked. An egress policy can select on `Mcp-Param-*`, and a request on
a revision that predates mandatory header-body validation never satisfies such a
selector.

Doing that work surfaced eight metrics that were defined, incremented on the
live path, and **never registered**, so they were absent from every scrape --
including the three approval-gate counters the observability guide documents
queries against, dead since 2.10.0. They are on `/metrics` now, and a test walks
the metrics module so the next one cannot be forgotten.
