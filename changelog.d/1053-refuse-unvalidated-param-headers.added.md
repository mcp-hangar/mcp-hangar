**core:** `headers.param_validation.required: true` refuses a `tools/call`
whose `Mcp-Param-*` headers could not be validated against its body, instead of
serving it. The SDK's pre-dispatch check is fail-open -- a failed schema listing
means no check and the call is dispatched anyway -- and an operator for whom an
unvalidated header is not servable can now say so. Off by default: it converts
an upstream listing failure into a client-visible refusal (`HEADER_MISMATCH`,
-32020) for every call carrying header parameters, which is a trade only some
deployments want. Refusing to *match* a header selector on such a request is
the separate, unconditional default (ADR-025)
