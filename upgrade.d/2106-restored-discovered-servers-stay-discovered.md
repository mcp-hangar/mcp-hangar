### Discovered servers are no longer protected as static after a restart

Applies only with a durable storage backend (`persistence.backend: sqlite` or
`postgresql`) and discovery enabled.

Before, every server restored from storage at startup was treated as declared
in the configuration, including those discovery had registered. From the first
restart on, discovery could not remove or replace such a server.

Now a server that discovery registered stays with the source that registered
it after a restart. Rows written from this version on record that source and
the definition it reported. For those:

- On an authoritative source, a server the source no longer reports is removed
  one TTL (`discovery.lifecycle.default_ttl_s`, 90 s by default) after the
  instance takes the management lease, including one that went away while the
  gateway was down. Its row is deleted too.
- On an additive source it stays, as it did without a restart.
- A definition that changed while the gateway was down is handled as a change.

Rows written by an earlier version record no source. That is every discovered
row at the first restart after the upgrade:

- If the row's description is still the one discovery gave it, `Discovered
  from <source>`, only that source can take the server over, when it next
  reports it. The source then follows its mode, and the row records the origin
  for the next restart. Until it reports the server, the server stays, even on
  an authoritative source. A definition that changed while the gateway was down
  is not compared on takeover; it is handled as a change the next time the
  source changes it.
- If the description was changed, nothing says which source registered the
  server, and it stays static as before.

Servers declared in the configuration file and servers created over the API or
the CLI are unaffected, and so are rows written before provenance was recorded:
they read as API-created and stay protected.

During a rolling upgrade, a replica still on the previous version drops the
recorded origin from any row it writes. Such a row is then treated like one
written by an earlier version, as above.

A restored server from a `filesystem` source is not covered by the rule that
keeps the servers of a definition file that exists but cannot be parsed until
the source has reported the server again after the restart. If its file is
unparseable from the restart on, an authoritative `filesystem` source removes
it one TTL after taking the lease.

If you relied on a discovered server surviving after its source stopped
reporting it, do one of the following:

- set `mode: additive` on that discovery source, which never removes what it
  registered; or
- declare the server in the configuration file, which discovery never touches.
