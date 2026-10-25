### Discovered servers are no longer protected as static after a restart

Applies only with a durable storage backend (`persistence.backend: sqlite` or
`postgresql`) and discovery enabled.

Before, every server restored from storage at startup was treated as declared
in the configuration, including those discovery had registered. From the first
restart on, discovery could not remove or replace such a server.

Now a server that discovery registered stays discovery's after a restart. Rows
written from this version on record the source that registered the server and
the definition it reported. For those:

- On an authoritative source, a server the source no longer reports is removed
  one TTL (`discovery.lifecycle.default_ttl_s`, 90 s by default) after the
  instance takes the management lease, including one that went away while the
  gateway was down. Its row is deleted too.
- On an additive source it stays, as it did without a restart.
- A definition that changed while the gateway was down is handled as a change.

Rows written by an earlier version record no source, which is every discovered
row at the first restart after the upgrade. Such a server is taken over when a
source next reports it, and then follows that source's mode. Until then it
stays, even on an authoritative source. A definition that changed while the
gateway was down is not compared on takeover; it is handled as a change the next time the
source changes it.

Servers declared in the configuration file and servers created over the API or
the CLI are unaffected, and so are rows written before provenance was recorded:
they read as API-created and stay protected.

If you relied on a discovered server surviving after its source stopped
reporting it, do one of the following:

- set `mode: additive` on that discovery source, which never removes what it
  registered; or
- declare the server in the configuration file, which discovery never touches.
