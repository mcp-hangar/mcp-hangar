`mcp-hangar serve --http` binds `127.0.0.1` by default instead of `0.0.0.0`, so the example in its own help starts without auth. The gateway already refused a non-loopback bind without auth.
