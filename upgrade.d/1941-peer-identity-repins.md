### Giving a server a peer identity re-records its digest pins

A server with `tls.peer_identity` gets the upstream identity `peer-key:<key_ref>` instead of `remote:<scheme>://<host>:<port>`, and digest pins are bound to that identity (#1802). Adding a peer identity to a server that already has digest pins changes its identity, so those pins no longer match. Under `block` the server's tools are refused until the pins are recorded again. Re-record them after adding the declaration (`mcp-hangar pin --write`). Changing `key_ref` later has the same effect. A key rotation within the same `key_ref` does not.

A configuration file that declares `tls.peer_identity` needs a release that knows the key. An older release ignores the declaration and serves the server as a plain one, without checking its key.
