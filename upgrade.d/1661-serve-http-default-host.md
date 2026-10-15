### `serve --http` binds 127.0.0.1 by default

The default `--host` (and `MCP_HTTP_HOST`) changed from `0.0.0.0` to `127.0.0.1`. A gateway that relied on the default to be reachable from other hosts or containers must now say so: `--host 0.0.0.0` (or `MCP_HTTP_HOST=0.0.0.0`), which still requires authentication. The published image and the Helm chart already pass `--host` explicitly and are unaffected.
