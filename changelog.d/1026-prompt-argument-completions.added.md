**core:** `front_door` answers `completion/complete` for a `ref/prompt`,
forwarding to the upstream that owns the prompt through the same flat-name map
`prompts/get` uses. The `completions` capability is advertised for the first
time as a result: nothing registered a handler before, so the gateway answered
method-not-found while upstreams (the official reference server among them) had
argument completions to offer.

An unknown prompt, one this tenant may not see, and a `ref/resource` reference
all answer the same generic error, so the reply cannot be used to test whether
a prompt exists for somebody else. `ref/resource` completion stays unserved: a
projected `hangar://` URI is a gateway name no upstream would recognise.
