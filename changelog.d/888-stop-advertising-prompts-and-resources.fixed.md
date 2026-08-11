**core:** the handshake advertised `prompts` and `resources` and served
neither. A client that sees `prompts` advertised and gets `[]` back concludes
the upstream *has no prompts* -- which is a different statement from *this
gateway does not carry prompts*, and the client had no way to tell them apart.
Both capabilities are now withdrawn while nothing is registered under them, on
`initialize` and on the SEP-2575 `server/discover` result alike.

`prompts/*` and `resources/*` consequently answer `-32601` (method not found)
instead of an empty list. That is the honest reply from a server that does not
claim the capability, and a conformant client that reads capabilities first
will not call them at all. If you have a client that calls `prompts/list` or
`resources/list` unconditionally and treats an error as fatal, it needs to
check the advertised capabilities -- which it should have been doing.

Derived rather than hard-coded: the capability follows what is actually
registered, so proxying an upstream's prompts and resources (#889) turns both
back on without touching this code.
