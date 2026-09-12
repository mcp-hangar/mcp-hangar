**core:** the CEF, LEEF, JSON-lines and syslog audit output now prints the
caller's tenant, which the records have carried since #1342, so a SIEM can
tell tenants apart. CEF prints it as `cs6` with `cs6Label=TenantID`, LEEF as
the custom attribute `tenantID`, JSON-lines as `tenant_id`, and syslog as the
`tenant` parameter of the `mcp@49152` structured data. Each sits beside the
session field and is escaped by that format's existing rules. A record with no
tenant, or an empty one, prints no tenant field, so its line is unchanged.
