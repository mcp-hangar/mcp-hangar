A browser CORS preflight is answered by the CORS layer instead of 401ing at
authentication. `OPTIONS` hit `AuthEnforcementMiddleware` before any
CORS middleware could speak -- with no `Access-Control-Allow-Origin` on the
refusal -- so a browser OAuth client could not call `/mcp` or `/api/*` at
all, allowed origin or not. Auth now skips `OPTIONS` (a preflight carries no
credentials by design, matching the authorization chokepoint), and
CORSMiddleware wraps the served combined app, which also gives `/mcp` CORS
headers for the first time. Refused requests carry the CORS header too, so a
browser can at least read the 401.
