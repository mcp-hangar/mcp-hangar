**core:** discovery source management now works end to end. Triggering a scan
awaits the discovery cycle instead of dropping the coroutine, so the endpoint no
longer reports a fabricated success while nothing runs; enabling, disabling, or
reconfiguring a source reaches the running source rather than only its registry
spec, so the listing and the toggle agree; a deleted source is no longer
re-advertised with an id whose scan/enable routes then answer `404`; and the id
is emitted from the source status itself, so the REST API and the MCP
`hangar_sources` tool both carry it. The mutating source-management surface is
labelled Preview for 2.5.0, signalled by an `X-Hangar-Preview` response header.
