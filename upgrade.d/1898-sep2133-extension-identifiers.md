### Governance extension identifiers use a slash

The governance extensions are now named `io.mcp-hangar/validator`, `io.mcp-hangar/mutator` and `io.mcp-hangar/digest-pinning` (SEP-2133 form), in `interceptors/list` and in the capabilities. Where to look depends on the protocol era: `server/discover` (2026-07-28) carries them under `capabilities.extensions`; `initialize` carries them under `capabilities.experimental`.

A client that looks the dotted names up (`io.mcp-hangar.validator`, and so on) keeps finding them in `capabilities.experimental` of `initialize` for this minor, marked `"deprecated": true` with `replacedBy`; `interceptors/invoke` also still accepts them. The modern `server/discover` surface never carried them under the old names, and the dotted aliases will be removed in the next minor. Switch the lookup to the slash form.
