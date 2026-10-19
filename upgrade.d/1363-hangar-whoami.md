### a front door lists `hangar_whoami` to every authenticated caller

On a `front_door` gateway with auth on, `tools/list` used to hold no `hangar_*`
name for a caller holding no management permission, such as an agent key with
only `tool:invoke` or a key with no role at all. That list now also holds
`hangar_whoami`, which reports the caller's own identity and needs no
permission. Upstream tools are unchanged, and with auth off, or for an
anonymous caller, the list is as before.

A client or test that expects such a caller's list to contain only upstream
names should allow `hangar_whoami`, or drop names that start with `hangar_`.
