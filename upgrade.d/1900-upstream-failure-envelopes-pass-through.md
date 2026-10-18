### An upstream's failure result is relayed whole

When an upstream answers a tool call with `isError: true`, a front-door `tools/call` now returns that result as sent (content blocks, `structuredContent`, `_meta`), instead of a result holding only the error text. A client that read nothing but the first text block keeps working. A `hangar_call` entry for the same call keeps `success: false`, `result: null` and `error`, and gains an `upstream_result` field with the upstream's whole answer; a client that rejects unknown fields in a batch entry should accept it.
