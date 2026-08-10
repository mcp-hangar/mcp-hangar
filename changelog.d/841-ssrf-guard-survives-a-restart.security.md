**core:** the connect-time SSRF re-check shipped in 2.5.0 did not survive a
restart. The flag that turns it on -- together with the provenance and the
runtime-reported addresses it judges an endpoint against -- has a place on the
stored configuration snapshot, but nothing ever wrote it there, so every server
rebuilt from its record came back unguarded: `enforce_ssrf` off, provenance
HUMAN, no runtime addresses. That covers both paths that rebuild a server from
the record -- recovery on restart, and the fleet projection on a replica that
learned of the registration from the event log. Registration-time validation
was unaffected, which is what kept this quiet: the endpoint was still checked
once, when it was registered, and only the DNS-rebinding defence on every later
connection lapsed.

Read it this way for a running deployment: on 2.5.0 the connect-time guard
protected only remote servers registered by the process currently serving them.
A gateway that has restarted since a remote server was registered has been
connecting to that endpoint with registration-time validation alone, and so has
any replica that did not perform the registration itself. A discovered server
keeps its runtime-scoped addresses across the same trip, so its legitimate
private container address is not refused once the guard is back on.

**Servers registered under 2.5.0 are covered by the upgrade.** Recording the
flag on its own would have fixed new registrations only: every row already in
the store says `enforce_ssrf: false`, because that was the field's default
before anything wrote it, and an update does not repair such a row -- it records
the aggregate that was itself rebuilt with the flag off. Those servers would
have stayed unguarded permanently, curable only by deleting and re-registering
them. So the guard is now derived when the record shows what registration
already checked: a remote mode with an endpoint. Restart the gateway and the
existing fleet is guarded; no re-registration, no edit to the store.

Guarding is also pinning, which is the part worth knowing before it surprises
someone. A guarded connection goes to one address the policy validated, with
the original hostname kept for the `Host` header and the certificate, rather
than to whichever address the client would have picked. For an upstream behind
several A or AAAA records that means the resolver's first answer instead of
httpx's own multi-address fallback, so a dead address behind a healthy name now
fails the call rather than being skipped. That behaviour shipped in 2.5.0; what
changes here is how much of the fleet it applies to.

One case is deliberately left as the upgrade found it. A row written before
this fix says nothing about provenance, so a server that discovery registered
comes back as HUMAN with no runtime addresses -- and applying the strict policy
to a container or pod address would refuse, on every call, an upstream that
works today. The endpoint is what tells the two apart: an endpoint that passed
the strict check at registration cannot be a private literal, so a stored
endpoint that *is* one can only have come from the scoped discovery path, and
it keeps 2.5.0's behaviour rather than becoming an outage. Re-registering such
a server -- or letting discovery register it again -- writes the real
provenance, and the guard comes back with the scoping that makes its address
legitimate.
