**security:** a declared stdio principal whose roles have no effect is now named at
boot. With `auth.enabled` false and `tool_access.mode` egress (the default), every
`hangar_*` management tool is allowed to the stdio caller before its declared
`roles` are consulted, so `roles: [viewer]` did nothing and nothing said so. The
gateway now logs `stdio_principal_roles_not_enforced` with the principal and its
roles, and how to have them enforced: `tool_access.mode: front_door` or
`auth.enabled: true`. Behaviour is unchanged
