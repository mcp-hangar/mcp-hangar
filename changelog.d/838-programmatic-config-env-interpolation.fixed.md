**core:** environment-variable interpolation works again on the programmatic
`bootstrap(config_dict=...)` / facade path. A rc.4 change removed the per-auth
interpolation and left the whole-document pass only on the file loader, so a
config passed as a dict had no interpolation at all: `auth: {bearer_token:
"${API_TOKEN}"}` was sent literally to the upstream (a 401 on every call) and a
missing variable no longer failed the boot closed. The dict path now
interpolates the document once at its entry point, matching the file path and
restoring fail-closed-on-missing-variable.
