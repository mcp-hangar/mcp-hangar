### List results carry their cache hints at the top level

`tools/list`, `prompts/list`, `resources/list` and `resources/templates/list` no longer put `cacheScope` and `ttlMs` in `_meta`. They are top-level fields of the result (`"cacheScope": "private"`, `"ttlMs": 5000`), as the 2026-07-28 spec defines them. The `_meta.cacheScope` value was a per-tenant token (`tenant:<hash>`) and is no longer emitted.

A client or proxy that read `_meta.cacheScope` or `_meta.ttlMs` from Hangar should read the top-level fields instead. Nothing else changes: every projected list is still per caller, which is what `"private"` says.
