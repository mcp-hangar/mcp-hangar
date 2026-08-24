**core:** a tool whose `x-mcp-header` annotations are invalid is no longer
projected through the front door. SEP-2243 makes dropping it a client-side
MUST, so advertising it handed out a tool nobody could call. The definition is
never edited -- stripping the annotation would move the JCS digest and read as
upstream drift -- the tool is withheld instead, with a log line naming the
reason and `mcp_hangar_projection_withdrawals_total{reason="invalid_x_mcp_header"}`
counting it once per schema version.
