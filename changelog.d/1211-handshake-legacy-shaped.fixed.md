**core:** the outbound handshake stamped the `2026-07-28` protocol era on its
own `initialize` call. A spec-current upstream's era gate reacts to that
`_meta` key alone, and Hangar could not complete what the gate then requires
(`Mcp-Protocol-Version`/`Mcp-Method` headers, a handshake method other than
`initialize`) -- so any spec-current upstream refused to connect at all.
`initialize` now goes out legacy-shaped; the negotiated response (or a
stateless upstream's method-not-found) decides whether later calls on that
connection carry the modern envelope.
