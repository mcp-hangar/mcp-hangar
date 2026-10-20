### Declare a peer identity over REST only once every replica runs this release

A key-pinned server's configuration row is stored with mode `remote-peer-key`, which the previous release declines rather than reads. While a replica of the previous release still runs, it does not serve that server. It can still write a plain row under the server's name through its old create, and this release then refuses that row at recovery and in the projection. So do not declare a peer identity over REST, or from the operator, until no replica of the previous release runs. For key-pinned rows, and for this one release, this relaxes the high-availability rule that a row written by a new release stays readable by the old one.

A configuration file that sets `tls.peer_identity` has no such guard: an older release serves the server as a plain one, without checking its key.

With authentication disabled, any client of the API can set or change a peer identity. `GET /system` now lists `capabilities`, which the operator checks before it registers a key-pinned server.
