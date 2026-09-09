**core:** a `front_door` gateway answered `tools/list` before its upstreams
were warm, so a client that connected during the boot warm-up was handed a
catalogue containing only the `hangar_*` management tools — and since the
legacy handshake advertises `tools.listChanged: false` and no notification
follows, a client that lists once at startup and caches kept that empty
catalogue until it reconnected. The quickstart lands squarely in that window:
it says "restart your MCP client", which is exactly when a client lists.

The boot is still not gated on a backend handshake — that deadlocks the
deployment, which is why the warm-up runs on its own thread. Instead the
*listing* now waits for the warm-up that is already running, and only where an
empty answer would be knowably wrong: the caller has an identity, nothing has
been discovered yet, and the warm-up has not finished. A missing identity is
still refused instantly, a policy-filtered empty list is still the truth, and
when the deadline passes the listing is served with whatever exists.

The same wait covers `tools/call`, where the symptom was a `-32601` for a tool
that was about to exist — on a multi-replica front door, a tool the client had
listed successfully against another replica.
