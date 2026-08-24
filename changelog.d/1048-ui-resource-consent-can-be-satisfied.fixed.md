**core:** a `ui://` resource can now be allowlisted and consented to rather than
only denied. SEP-1865 mandates a human decision before such a resource reaches a
client webview, and the guard stated that mandate while both halves that satisfy
it were missing: nothing built a `UiResourcePolicy`, so no tenant had an
allowlist, and no consent gate was ever attached, so an allowlisted resource was
refused for want of anyone to ask. The policies come from a new `ui_resources`
config block, the gate is an adapter over the approval service wired at
bootstrap, and both halves still fail closed on their own -- an unconfigured
deployment denies every `ui://` resource exactly as before. Consent stays
mandatory: the file cannot turn it off (ADR-024)
