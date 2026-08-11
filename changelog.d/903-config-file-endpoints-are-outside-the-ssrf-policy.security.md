**core:** a `remote` upstream declared in `config.yaml` gets neither half of the
SSRF policy, and now says so at startup. `enforce_ssrf` is set by the command
handler behind the REST API and discovery and nowhere else, so an endpoint the
API answers `400 ssrf_blocked` for -- `http://169.254.169.254/…`,
`http://10.0.0.5:8080/mcp` -- is accepted from the file without comment, and the
connect-time re-resolution and IP pinning added in 2.5.0 never runs for it
either. That second half is the one that closes DNS rebinding, so a config-file
upstream declared by hostname is re-resolved by httpx on every connect with no
policy applied.

The exclusion is deliberate: the operator's file is trusted, a config-file
upstream on a private address is usually meant, and applying the strict policy
there would refuse endpoints an operator chose. What was missing is that the
decision was invisible outside the source -- an operator who moved an upstream
out of the API and into the file lost two controls silently. Boot now logs one
line per such upstream naming it, its endpoint, and which protections do not
apply; an endpoint the strict policy would have refused outright is called out
in those terms rather than in general ones. Nothing is refused and no upstream
changes behaviour
