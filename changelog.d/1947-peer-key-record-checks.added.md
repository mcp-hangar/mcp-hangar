**core:** every path that brings a server into the fleet without writing a configuration row now checks the peer-key name record (ADR-030 §9):
- **Configuration file:** loading or reloading it refuses an entry that declares a recorded name without its recorded `key_ref`, or a recorded `key_ref` under another name.
- **Recovery:** a row under a recorded name that does not declare the recorded peer identity is not restored.
- **`hangar_load`:** a recorded name cannot be hot-loaded.
- **Discovery:** discovery does not register a recorded name, and logs it as `discovered_peer_key_upstream`. Its repeated attempt to delete a server that moved to a key-pinned registration is logged at info.
