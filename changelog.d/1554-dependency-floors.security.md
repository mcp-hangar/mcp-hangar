**security:** the declared dependency floors now name versions that are free of
known vulnerabilities and that can actually be installed. pip keeps an installed
package while it satisfies the constraint, so the floor is what an existing
environment keeps on upgrade, and ours sat on vulnerable releases:
`pyjwt[crypto]>=2.13.0` (was `pyjwt>=2.8.0`; CVE-2026-48522/48523/48524/48525/48526
and CVE-2026-32597, of which 48524 and 48525 are pre-authentication DoS on the
JWKS path), `cryptography>=50.0.0` (was `>=41.0.0`; fixes through
CVE-2026-69247/69248/69249), `python-multipart>=0.0.31` (was `>=0.0.22`;
CVE-2026-53537 to 53540 and earlier), `pydantic>=2.12.0` (was `>=2.0.0`, below
what `mcp==2.0.0` already required) and, for the `dev` extra, `pytest>=9.0.3`
(CVE-2025-71176). A new `deps-floor-audit` CI job runs `pip-audit` against both
the lowest and the highest resolution of every range, daily and on each PR, and
fails when a floor we declare is lower than the one `mcp` imposes. OIDC signing
keys are no longer fetched over plain HTTP: a non-https `jwks_uri`, or a
non-https issuer used for discovery, now refuses to start instead of logging
`jwks_uri_not_https`, and a discovered non-https `jwks_uri` is refused per
request. `http://` to localhost, 127.0.0.1 and ::1 stays allowed for development.
