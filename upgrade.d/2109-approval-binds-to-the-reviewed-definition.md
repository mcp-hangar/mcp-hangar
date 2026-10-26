### Approve a discovered server with the fingerprint you reviewed

A source re-reports its definition every cycle, so the definition waiting for
approval under a name can change between listing it and approving it. Pass the
fingerprint you reviewed, and the approval applies only that definition:

- MCP: `hangar_approve(mcp_server="svc", fingerprint="<fingerprint>")`, with the
  fingerprint from `hangar_discovered` or `hangar_quarantine`.
- REST: `POST /api/discovery/approve/svc?fingerprint=<fingerprint>`, or the same
  with the JSON body `{"fingerprint": "<fingerprint>"}`, with the fingerprint
  from `GET /api/discovery/pending` or `GET /api/discovery/quarantined`.

When the definition waiting has another fingerprint, the approval is refused
and nothing changes: REST answers 409 with the code
`DiscoveredDefinitionChangedError` and `details.current_fingerprint`; the tool
answers that error type with the current fingerprint in its message. Review the
definition again and approve its current fingerprint. A fingerprint that is not
a non-empty string is a 400 on REST and an invalid argument on the tool.

This is the recommended form. An approval without a fingerprint still works as
before, but logs `discovery_approval_not_bound` at WARNING; it may be required
in a later release.

Two further changes an approval now meets:

- A definition the source's own policy refuses (for example a Kubernetes
  namespace the source may not take from), or whose command the input validator
  refuses, can no longer be approved. Before, approving a server quarantined for
  that reason registered it anyway. Now the approval answers
  `{"approved": false, "error": "approval refused: <reason>"}` and the definition
  stays in pending or quarantine, where a reject removes it. Approval still
  overrides the other reasons a definition is quarantined: a failed health or
  schema check, and the rate and count limits.
- A pending entry is cleared when its source reports the running definition
  again, or stops reporting it while the source is healthy; a quarantined change
  is cleared when its source reports the running definition again. Removing a
  source clears what it left in pending and quarantine. Approving any of these
  afterwards answers "not found", as for any name that is not waiting.
