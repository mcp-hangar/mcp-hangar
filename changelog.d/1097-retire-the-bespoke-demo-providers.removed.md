**core:** `examples/discovery/Dockerfile.test-provider` is gone. The discovery
examples now label the official `mcp/everything` image, which speaks stdio out
of the box -- the thing that Dockerfile was hand-building.

The podman variant beside it was worse than stale: it labelled
`python -m http.server` as an MCP provider in `mode: http`, so discovery found
two providers and neither could answer anything. The labels were the point of
the example and were the only part of it that was true.

`examples/provider_math` and `examples/provider_identity` stay, and now say why
in their own docstrings: they are stubs the live suite launches as subprocesses,
not demos. A test fixture wants to be small, offline and deterministic, which
is the same reason `tests/mock_provider.py` is not an official server either.
