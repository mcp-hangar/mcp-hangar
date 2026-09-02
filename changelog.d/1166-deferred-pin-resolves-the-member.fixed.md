**core:** the first call to a pinned tool through a group after a gateway boot
was refused with `ToolDigestMismatchError` ("schema could not be verified").
The pin gate defers when the selected member is still cold, and the deferred
re-check after the cold start resolved the GROUP id alone -- the projection is
keyed by the member that started -- so it found nothing and fell through to the
fail-closed branch. It now goes through the pipeline's own two-name lookup, the
one `_gate_digest_pin` has used since #1040
