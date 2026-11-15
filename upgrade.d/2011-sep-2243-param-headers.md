### Annotated arguments now reach HTTP upstreams as `Mcp-Param-*` headers

Hangar, as the client of a remote HTTP upstream, now mirrors every argument a
tool annotates with `x-mcp-header` as an `Mcp-Param-<token>` request header, as
SEP-2243 requires. Until now these values travelled only in the request body.
In a header, every proxy and load balancer between Hangar and the upstream can
read and log them.

A `header_exposure` block with `on_violation: warn`, the default, keeps such a
tool: its value is now mirrored, and the warning is logged. To keep an annotated
argument out of headers, list it under `deny_annotated` with `on_violation:
withdraw`. The tool is then refused instead of called. With such a block in
force, a direct use of the HTTP client that cannot tell which server or group it
serves is refused too.

A call is also refused when a mirrored header would replace one that Hangar
already sends, for example an `api_key_header` or an `extra_headers` key that
starts with `Mcp-Param-`. Rename that header if this applies to you.
