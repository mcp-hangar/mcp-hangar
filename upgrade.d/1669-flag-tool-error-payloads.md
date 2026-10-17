### A failing `hangar_*` tool now returns `isError: true`

The error payload every `hangar_*` tool returns on failure (`error`, `error_type`, `details`) used to arrive with `isError: false`. It now arrives with `isError: true`; the payload itself, in `structuredContent` or as the single JSON text block, is the same.

A client that treated every `hangar_*` result as a success and looked for an `error` key still sees the key. A client that raises or retries on `isError` will now do so for these failures, which is the intent. Only the exact three-key payload of a `hangar_*` tool is flagged: a proxied backend tool's answer is never changed, and the few `hangar_*` tools that answer with a single-key `{"error": ...}` of their own (discovery and continuation when not configured) are not covered by this change.
