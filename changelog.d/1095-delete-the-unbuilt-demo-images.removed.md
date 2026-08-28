**core:** the five `docker/Dockerfile.*` demo images are gone, and
`scripts/containers.sh` now runs the official images from
`modelcontextprotocol/servers` (`mcp/filesystem`, `mcp/memory`, `mcp/fetch` on
Docker Hub) instead of `localhost/mcp-*:latest`.

Nothing in this repository built those images -- not the Makefile, not CI, not
a script -- and every `podman run` in the pool script redirected stderr to
`/dev/null`, so a missing image failed silently. The script had never worked on
a fresh checkout and said so to nobody. Two of the five repackaged an official
npm package; `fetch` repackaged a third-party fork rather than the official
`mcp-server-fetch`; `sqlite` repackaged a third-party npm whose upstream is
archived, and has no maintained official replacement, so it is dropped from the
pool rather than kept unmarked.

`examples/discovery/docker-compose.yml` is fixed in the same pass: it named
`ghcr.io/modelcontextprotocol/server-fetch:latest` and `-server-memory:latest`,
which do not exist, and declared `mode: http` with a port for servers that
speak stdio. It now uses the same official images with `mode: container`.

**If you built these images by hand**, `docs/guides/CONTAINERS.md` has the
replacement commands. For anything driven as a subprocess prefer
`npx -y @modelcontextprotocol/server-*` or `uvx mcp-server-*`: those packages
are current, while the published container images were last rebuilt in 2025.
